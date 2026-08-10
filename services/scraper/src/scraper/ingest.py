"""Pipeline de ingesta en dos fases con detalle condicional.

1. `store.list_catalog()` da una huella barata por producto (precio por color del listado).
2. Se compara con la huella del scrape anterior (columna `product.listing_signature`):
   - nuevo, huella cambiada o producto descatalogado que reaparece -> se pide el detalle
     completo y se apila precio;
   - sin cambios -> NO se pide detalle; solo se refresca `last_seen_at` (producto y variantes)
     para que no se marque como baja.
3. A eso se suma un **refresco periódico forzado**: una prenda de precio estable nunca cambia de
   huella, así que sin esto no se volvería a observar jamás. Sin re-observaciones no hay serie
   temporal con la que corroborar un descuento (el veredicto de honestidad y el aviso exigen
   histórico previo) y el stock por talla se queda congelado. Lo más rancio según
   `product.last_detail_at` vuelve a pedirse, acotado por un presupuesto por pasada.

Una hoja de categoría que la tienda ya no sirve (404) no tumba la pasada: el scraper la salta y
la apunta en su `ScanReport`, la ingesta saca su ámbito de las bajas —lo que no se ha podido
mirar no está retirado— y solo aborta si cae una proporción alta de las hojas, que ya no es una
categoría retirada sino un bloqueo o un cambio de API. Esa hoja caída tiene además una segunda
consecuencia, en un ámbito **distinto** del suyo: en las tiendas que colapsan géneros, la rama
que sobrevive lista como suyos productos que son `unisex`, así que su género tampoco se escribe
(ver `_gender_a_escribir`, #172).

Todo ocurre en una única transacción por ejecución (`scrape_run`), y si algo revienta se deshace
entera — pero **la pasada fallida se registra igual** (`status = 'failed'` y el motivo en
`message`, ver `_record_failed_run`): una tienda que deja de ingerir tiene que verse en la BD, no
solo en unos logs que rotan.

Por lo mismo, `message` **no es solo del camino de fallo**: una pasada con éxito lo rellena cuando
no está del todo limpia —hojas caídas, ámbitos con caída sospechosa— y lo deja a `NULL` cuando lo
está (ver `_success_message`, #151). O sea que la columna significa «por qué esta pasada no es
limpia», no «por qué falló»; el `status` es el que distingue los dos casos.

Las bajas se detectan por ausencia: lo no visto conserva un `last_seen_at` anterior, suma una
pasada a `missing_streak`
y solo se marca `delisted_at` tras N pasadas consecutivas sin verlo (histéresis). Si la tienda
sabe responder por un producto concreto (`SupportsAliveProbe`), antes de descatalogar se le
pregunta directamente y solo se da de baja lo confirmado como retirado.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from .progreso import Latido, duracion
from .stores.base import (
    BaseStore,
    DelistCandidate,
    ListingEntry,
    ProductTags,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    SupportsAliveProbe,
    SupportsProductTags,
    SupportsScanReport,
)
from .tags import SECCION_APLICABLE, TAGS_CONOCIDOS

# Umbrales por defecto de la red de seguridad de bajas (ver `_suspicious_scopes`).
DEFAULT_DELIST_MIN_BASELINE = 5
DEFAULT_DELIST_DROP_RATIO = 0.5
# Histéresis: pasadas consecutivas sin ver antes de dar de baja (ver `_advance_missing`).
DEFAULT_DELIST_MIN_MISSES = 2
# Confirmación activa: tope de sondeos por pasada (ver `_confirm_candidates`).
DEFAULT_DELIST_PROBE_MAX = 50
# Refresco forzado: edad a partir de la cual un detalle se considera rancio, y tope de
# refrescos por pasada (ver `_stale_refreshes`).
DEFAULT_DETAIL_MAX_AGE_DAYS = 7
DEFAULT_DETAIL_REFRESH_MAX = 100
# Hojas de categoría caídas: proporción a partir de la cual la pasada aborta (ver `ingest`).
DEFAULT_SCAN_MAX_DEAD_RATIO = 0.34
# Cada cuánto informa la pasada de por dónde va (ver `progreso.Latido`). Va por TIEMPO y no por
# número de fichas para que el volumen del log no dependa del tamaño del catálogo, y de paso sale
# gratis que una pasada caliente no se ensucie: Zara en 1m35s no llega al primer aviso. 0 lo apaga.
DEFAULT_PROGRESS_EVERY_SECONDS = 300.0
# Tope del motivo que se guarda en `scrape_run.message`: un traceback de una librería puede venir
# con kilobytes de ruido y esta columna es para leerla de un vistazo.
_MAX_FAIL_MESSAGE = 500
# Tope de hojas caídas que se nombran en `message` (ver `_success_message`). Con más, el número ya
# lo dice todo: eso no es una categoría retirada, es un cambio de API — y por encima de
# `SCRAPER_SCAN_MAX_DEAD_RATIO` la pasada ni siquiera llega aquí.
_MAX_NAMED_LEAVES = 5

_ScopeKey = tuple[str | None, str | None, str | None]

_LOG = logging.getLogger(__name__)


class CatalogScanAborted(RuntimeError):
    """Se cayeron demasiadas hojas del catálogo: no es una categoría retirada, es un problema.

    Una hoja suelta se tolera (ver `stores.base.ScanReport`), pero a partir de cierta proporción
    lo que hay delante es un bloqueo o un cambio de API, y guardar el catálogo mutilado sería
    peor que no guardar nada: daría por buenas unas ausencias que acabarían en bajas.
    """


# Orden de "nunca se le pidió detalle" al elegir a quién refrescar: lo más rancio que hay.
_NEVER = datetime.min.replace(tzinfo=UTC)


@dataclass
class IngestResult:
    scrape_run_id: int
    products_in_catalog: int  # productos vistos en el listado
    details_fetched: int  # productos a los que se pidió detalle (nuevos/cambiados/rancios)
    details_refreshed: int  # de los anteriores, los pedidos solo por antigüedad del detalle
    products_unchanged: int  # productos sin cambios (ahorro de peticiones de detalle)
    variants_seen: int
    prices_recorded: int
    products_delisted: int
    variants_delisted: int
    products_missing: int  # ausentes que aún no llegan al umbral de histéresis
    variants_missing: int
    scanned_scopes: int  # ámbitos recorridos en esta pasada
    skipped_scopes: int  # ámbitos con caída sospechosa: se omitieron sus bajas
    # Cómo se llaman esos ámbitos (`niña/ropa/camisetas`, ver `_render_scope`), por el mismo motivo
    # que `failed_leaves` de abajo (#170): mientras dure la sospecha ese ámbito no aplica bajas en
    # NINGUNA pasada, así que sus productos retirados siguen visibles — y «1/17 ámbitos» no dice por
    # dónde empezar a mirar. `scrape_run.message` ya los nombraba (ver `_success_message`); lo que
    # faltaba era decirlo también en el resumen, que es lo que alguien lee.
    skipped_scope_names: list[str]
    # Ámbitos que HABRÍAN salido como caída sospechosa y no lo hacen porque sus productos se han
    # mudado a otro ámbito, no desaparecido (#174). No es un error —por eso no suma en `errors`—
    # pero se publica: es la única señal de que esta pasada ha visto un cambio de clasificación, y
    # sin ella el arreglo y una regresión del arreglo se leen igual (silencio en las dos).
    remapped_scopes: int
    remapped_scope_names: list[str]
    leaves_scanned: int  # hojas de categoría recorridas en el listado
    leaves_failed: int  # de las anteriores, las que la tienda ya no sirve (404)
    # Cómo se llaman esas hojas, en el vocabulario de cada tienda (ver `ScanReport.failed_leaves`,
    # #151 y #155). El resumen las nombra: «1/35 hojas no responden» no dice cuál hay que ir a
    # buscar al árbol.
    failed_leaves: list[str]
    # Hojas que respondieron pero cuyo filtro no casó con nada (#200). No suman en `leaves_failed`
    # —la hoja se listó— pero sacan su ámbito de las bajas igual que una caída, así que cuentan en
    # `unscanned_scopes` de abajo y hay que poder nombrarlas por el mismo motivo que a las caídas.
    empty_filter_leaves: list[str]
    unscanned_scopes: int  # ámbitos excluidos de las bajas por una hoja caída o un filtro vacío
    probes_sent: int  # candidatos a baja sondeados (confirmación activa)
    probes_alive: int  # el sondeo los encontró vivos: rescatados, no se dan de baja
    probes_dead: int  # el sondeo confirmó la retirada
    probes_over_cap: int  # no cabían en el tope: rutina, entran los primeros en la siguiente
    probes_unresolved: int  # sondeados y sin veredicto (fallo de red, bloqueo o respuesta ambigua)
    # Reparto si/no/desconocido del calzado ACTIVO de la tienda tras la pasada. Es el informe que
    # pide #30, y va aquí en vez de en una consulta a mano porque es la cifra que dice si el foco
    # barefoot tiene contenido: una zapatería que se queda en 0 productos `si` no es un detalle
    # técnico, es la mitad del producto vacía.
    barefoot_counts: dict[str, int] = field(default_factory=dict)
    # Productos ACTIVOS marcados por cada eje transversal tras la pasada (#180). Se publica por el
    # mismo motivo que el reparto barefoot: es la cifra que dice si la tienda está aportando algo al
    # eje. Y es donde se ve la cola que no ingerimos — las prendas exclusivas de una hoja mixta no
    # están en el catálogo, así que no se pueden marcar y el número sale por debajo de lo que la
    # hoja publica. Una tienda sin cajón de deporte no aparece aquí en absoluto.
    tag_counts: dict[str, int] = field(default_factory=dict)
    # Reparto de género de lo que ESTA pasada ha listado, por el mismo motivo que el de arriba:
    # niño/niña es el otro eje del brief y hasta #139 no se publicaba en ningún sitio. Ojo con lo
    # que significa el `unisex`, que no es lo mismo en todas las tiendas: donde hay hojas `unisex`
    # declaradas (bebé en Hipercor, newborn en H&M y Mango) suma esas y los cruces de género; donde
    # no las hay (Lefties), es exactamente el número de productos publicados en las dos ramas.
    gender_counts: dict[str, int] = field(default_factory=dict)
    # Productos cuyo género almacenado NO es el que dice el listado y que esta pasada no ha
    # reescrito. No es un fallo del scraper: `gender` solo lo escribe el detalle, así que una
    # tienda ingerida antes de un arreglo de género conserva el viejo hasta que el refresco forzado
    # llegue a ella. Se publica porque sin esta cifra la única forma de verlo era una consulta a
    # mano contra la base, que es lo que costó #139.
    gender_stale: int = 0
    # Productos a los que esta pasada NO les ha escrito el género que decía el listado, porque se
    # había caído la rama complementaria y el listado no podía saber que eran `unisex` (#172). Es la
    # cifra que separa "aquí no pasó nada" de "aquí una hoja caída estuvo a punto de reetiquetar
    # medio ámbito": el reparto de `gender_counts` refleja igualmente el desplazamiento, porque es
    # lo que el listado dijo, y sin esta cifra parecería que se ha guardado.
    gender_frozen: int = 0


def _scalar_int(cur: psycopg.Cursor) -> int:
    """Devuelve como int la única columna de la fila leída (RETURNING id, count(*), ...)."""
    row = cur.fetchone()
    assert row is not None, "se esperaba una fila"
    return int(row[0])


def _discount_pct(price: Decimal, list_price: Decimal | None) -> Decimal | None:
    """% de rebaja respecto al precio original (None si no hay o no es un descuento real)."""
    if list_price is None or list_price <= 0 or price >= list_price:
        return None
    return ((list_price - price) / list_price * 100).quantize(Decimal("0.01"))


def _upsert_retailer(cur: psycopg.Cursor, store: BaseStore) -> int:
    cur.execute(
        """
        INSERT INTO retailer (slug, name, base_url)
        VALUES (%s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, base_url = EXCLUDED.base_url
        RETURNING id
        """,
        (store.slug, store.name, store.base_url),
    )
    return _scalar_int(cur)


@dataclass(frozen=True)
class _ExistingProduct:
    """Lo que ya sabemos de un producto antes de la pasada (decide si se le pide detalle)."""

    id: int
    signature: str | None
    delisted: bool
    last_detail_at: datetime | None  # None = nunca se le pidió detalle (o pre-migración 0009)
    # ÁMBITO ALMACENADO: el que escribió una pasada anterior, que no tiene por qué ser el que el
    # listado le asigna hoy. Sirve para dos cosas distintas:
    #   - `gender` solo, para detectar que la fila conserva el género viejo (`gender_stale` en
    #     IngestResult). Lo escribe `_upsert_product`, o sea solo el detalle.
    #   - los tres juntos, para saber qué productos se han MUDADO de ámbito (#174, ver
    #     `_moved_out_counts`). Ahí importa justamente que sea el viejo: es el lado del que hay
    #     que descontar la caída.
    gender: str | None = None
    section: str | None = None
    category: str | None = None

    @property
    def scope(self) -> _ScopeKey:
        return (self.gender, self.section, self.category)


def _load_existing(cur: psycopg.Cursor, retailer_id: int) -> dict[str, _ExistingProduct]:
    """Estado actual por producto, indexado por `retailer_product_id`."""
    cur.execute(
        """
        SELECT retailer_product_id, id, listing_signature, (delisted_at IS NOT NULL),
               last_detail_at, gender, section, category
        FROM product WHERE retailer_id = %s
        """,
        (retailer_id,),
    )
    return {
        row[0]: _ExistingProduct(
            id=row[1],
            signature=row[2],
            delisted=row[3],
            last_detail_at=row[4],
            gender=row[5],
            section=row[6],
            category=row[7],
        )
        for row in cur.fetchall()
    }


def _touch_seen(cur: psycopg.Cursor, product_id: int, run_ts: datetime) -> None:
    """Marca producto y variantes activas como vistos en este run, sin tocar precios."""
    cur.execute("UPDATE product SET last_seen_at = %s WHERE id = %s", (run_ts, product_id))
    cur.execute(
        "UPDATE variant SET last_seen_at = %s WHERE product_id = %s AND delisted_at IS NULL",
        (run_ts, product_id),
    )


def _upsert_product(
    cur: psycopg.Cursor,
    retailer_id: int,
    run_ts: datetime,
    product: ScrapedProduct,
    signature: str,
    gender: str | None,
) -> int:
    """Escribe el producto. `gender` va aparte del resto de `product` a propósito (#172).

    Es el único campo de la fila que la pasada puede decidir NO recalcular: cuando se ha caído la
    rama de género complementaria, el listado emite como `niño`/`niña` un producto que es `unisex`,
    y eso hay que conservarlo en vez de escribirlo. Lo decide quien tiene delante el `ScanReport`
    y la fila previa, o sea `ingest()`; aquí solo se obedece.
    """
    cur.execute(
        """
        INSERT INTO product (retailer_id, retailer_product_id, name, gender, section,
                             category, barefoot, url, image_url, listing_signature, first_seen_at,
                             last_seen_at, last_detail_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_product_id) DO UPDATE SET
            name = EXCLUDED.name,
            gender = EXCLUDED.gender,
            section = EXCLUDED.section,
            category = EXCLUDED.category,
            -- Sin COALESCE, al revés que `image_url`: aquí el valor nuevo SIEMPRE manda, incluido
            -- un `desconocido` que degrade a un `si` anterior. La clasificación se recalcula entera
            -- en cada pasada (categoría de la tienda, heurística y correcciones manuales), así que
            -- conservar lo viejo dejaría clavado un veredicto que ya hemos decidido cambiar.
            barefoot = EXCLUDED.barefoot,
            url = EXCLUDED.url,
            -- COALESCE y no EXCLUDED a secas: una tienda que aún no sepa dar foto (o un fallo
            -- puntual de parseo) no debe dejar sin imagen una ficha que ya la tenía.
            image_url = COALESCE(EXCLUDED.image_url, product.image_url),
            listing_signature = EXCLUDED.listing_signature,
            last_seen_at = EXCLUDED.last_seen_at,
            last_detail_at = EXCLUDED.last_detail_at,
            delisted_at = NULL
        RETURNING id
        """,
        (
            retailer_id,
            product.retailer_product_id,
            product.name,
            gender,
            product.section,
            product.category,
            product.barefoot,
            product.url,
            product.image_url,
            signature,
            run_ts,
            run_ts,
            run_ts,  # por aquí pasa todo producto al que se le pidió el detalle
        ),
    )
    return _scalar_int(cur)


def _gender_a_escribir(
    product: ScrapedProduct,
    prior: _ExistingProduct | None,
    cross_gender_suspect: set[ScrapeScope],
) -> str | None:
    """El género que se guarda: el del listado, salvo que esta pasada no pueda saberlo (#172).

    Una tienda que colapsa géneros (`ambito_cruzado()`) marca `unisex` al producto que publica en
    las dos ramas. Si una rama se cae, ese producto solo se ve en la otra y el listado lo emite con
    el género de la superviviente — no porque haya cambiado, sino porque falta media observación.
    Persistirlo lo saca del alcance del interés que su usuario pidió (`interest.gender`).

    La protección es deliberadamente estrecha: **solo** se conserva un `unisex` ya guardado, y solo
    en los ámbitos que `ScanReport.cross_gender_suspect` señala. Todo lo demás se escribe normal,
    incluida una corrección legítima de `niño`↔`niña`, que no tiene nada que ver con el cruce.

    Dos límites que se aceptan a sabiendas:

    - Un producto **nuevo** en un ámbito sospechoso se guarda con el género de la superviviente:
      no hay nada previo que conservar y es la única información que existe.
    - Un `unisex`→`niña` de verdad se retrasa hasta una pasada con las dos ramas vivas.

    Los dos se curan solos en cuanto la hoja vuelve y al producto le toca detalle, que es la misma
    forma en que se cura el `gender_stale` de siempre.
    """
    if prior is None or prior.gender != "unisex" or product.gender == "unisex":
        return product.gender
    scope = ScrapeScope(product.gender, product.section, product.category)
    return "unisex" if scope in cross_gender_suspect else product.gender


def _replace_product_images(
    cur: psycopg.Cursor, product_id: int, images: Sequence[ScrapedImage]
) -> None:
    """Deja la galería del producto igual a `images`. Con `images` vacío NO toca nada.

    Ese "no toca nada" es deliberado y es la misma filosofía que el
    `COALESCE(EXCLUDED.image_url, product.image_url)` del upsert: una lista vacía significa "esta
    pasada no trae información de fotos" (tienda que aún no sabe darlas, parseo que falla, campo
    que la tienda deja de servir), no "este producto se quedó sin fotos". Borrar ahí dejaría la
    ficha pelada por un fallo transitorio.

    Cuando sí viene con contenido se reemplaza entera en vez de fusionar: es lo que hace que
    desaparezcan las fotos de un color que la tienda ha retirado. Va dentro de la transacción
    atómica de la pasada, así que o entra todo o no entra nada.

    La **posición se asigna aquí**, por nombre de color y en el orden en que llegan, en vez de
    fiarse de la que traiga el scraper. Una tienda puede exponer dos colores distintos con el
    mismo nombre (visto en Lefties: dos "MARRON" con ids distintos), y numerando por su cuenta
    ambas series arrancarían en 0 y violarían el UNIQUE. Numerar en un único sitio lo resuelve
    para todas las tiendas, presentes y futuras, y además fusiona esas dos series en la galería
    del color — que es lo que la ficha quiere, porque agrupa por nombre.

    `variant_url` (0023, #123) es el matiz de ese «lo que la ficha quiere»: en H&M las dos series
    NO son el mismo color visto dos veces, son dos artículos distintos de la tienda, cada uno con
    su ficha y sus fotos, y fusionarlos enseña la foto de uno junto al precio del otro. La columna
    las separa **sin tocar la numeración**: `position` se sigue contando por color y el
    `UNIQUE (product_id, color, position)` se queda como estaba, porque la tarjeta del catálogo
    (`applyReprImages`) hace join por `position = 0` y espera una sola fila por (producto, color).
    Quien necesita separar las dos referencias es la ficha, y le basta con filtrar por la columna.
    """
    if not images:
        return
    cur.execute("DELETE FROM product_image WHERE product_id = %s", (product_id,))
    siguiente: dict[str | None, int] = defaultdict(int)
    filas: list[tuple[int, str | None, int, str, str | None]] = []
    for img in images:
        filas.append((product_id, img.color, siguiente[img.color], img.url, img.variant_url))
        siguiente[img.color] += 1
    cur.executemany(
        "INSERT INTO product_image (product_id, color, position, url, variant_url)"
        " VALUES (%s, %s, %s, %s, %s)",
        filas,
    )


def _upsert_variant(
    cur: psycopg.Cursor, product_id: int, run_ts: datetime, variant: ScrapedVariant
) -> int:
    cur.execute(
        """
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url,
                             first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (product_id, retailer_variant_id) DO UPDATE SET
            size = EXCLUDED.size,
            color = EXCLUDED.color,
            sku = EXCLUDED.sku,
            url = EXCLUDED.url,
            last_seen_at = EXCLUDED.last_seen_at,
            delisted_at = NULL
        RETURNING id
        """,
        (
            product_id,
            variant.retailer_variant_id,
            variant.size,
            variant.color,
            variant.sku,
            variant.url,
            run_ts,
            run_ts,
        ),
    )
    return _scalar_int(cur)


def _record_price(
    cur: psycopg.Cursor,
    variant_id: int,
    run_id: int,
    run_ts: datetime,
    variant: ScrapedVariant,
) -> None:
    cur.execute(
        """
        INSERT INTO price_history (variant_id, price, currency, list_price, discount_pct,
                                   retailer_min_30d, in_stock, scraped_at, scrape_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            variant_id,
            variant.price,
            "EUR",
            variant.list_price,
            _discount_pct(variant.price, variant.list_price),
            variant.retailer_min_30d,
            variant.in_stock,
            run_ts,
            run_id,
        ),
    )


def _needs_detail(entry: ListingEntry, existing: _ExistingProduct | None) -> bool:
    """Pide detalle si es nuevo, si la huella del listado cambió, o si estaba descatalogado."""
    if existing is None:
        return True
    return existing.delisted or existing.signature != entry.signature


def _stale_refreshes(
    candidates: list[tuple[ListingEntry, _ExistingProduct]],
    run_ts: datetime,
    max_age_days: int,
    refresh_max: int,
    refresh_all: bool = False,
) -> set[str]:
    """De los productos sin cambios, cuáles toca refrescar igualmente: por antigüedad o a petición.

    Se eligen los **más rancios primero** y se recorta al presupuesto de la pasada: lo refrescado
    estrena `last_detail_at` y se va al final de la cola, así que pasadas sucesivas barren el
    catálogo en round-robin sin ráfagas. Con `max_age_days = 0` no se refresca nada (escape hatch).

    `refresh_all` es la reobservación bajo demanda de #143: entran **todos** los sin cambios, sin
    mirar la edad — pero el presupuesto sigue mandando, así que el orden por antigüedad no sobra.
    """
    if refresh_max <= 0:
        return set()
    if refresh_all:
        stale = [
            (existing.last_detail_at, entry.retailer_product_id) for entry, existing in candidates
        ]
    elif max_age_days <= 0:
        return set()
    else:
        cutoff = run_ts - timedelta(days=max_age_days)
        stale = [
            (existing.last_detail_at, entry.retailer_product_id)
            for entry, existing in candidates
            if existing.last_detail_at is None or existing.last_detail_at < cutoff
        ]
    # `None` es lo más rancio posible: nunca se le pidió el detalle (o es de antes de la 0009).
    stale.sort(key=lambda item: item[0] or _NEVER)
    return {product_id for _, product_id in stale[:refresh_max]}


def _barefoot_counts(cur: psycopg.Cursor, retailer_id: int) -> dict[str, int]:
    """Reparto si/no/desconocido del calzado activo de la tienda (informe de #30).

    Solo `zapateria`: en la ropa la columna es NULL porque la pregunta no aplica, y meterla aquí
    solo serviría para inflar el recuento con un estado que no significa nada.
    """
    cur.execute(
        """
        SELECT COALESCE(barefoot, 'sin-marcar'), count(*)
        FROM product
        WHERE retailer_id = %s AND section = 'zapateria' AND delisted_at IS NULL
        GROUP BY 1
        """,
        (retailer_id,),
    )
    return {str(row[0]): int(row[1]) for row in cur.fetchall()}


def _reconcile_tags(cur: psycopg.Cursor, retailer_id: int, tags: ProductTags) -> dict[str, int]:
    """Deja `product_tag` igual a lo que esta pasada ha observado, y devuelve el recuento por eje.

    Reconciliación y no acumulación: una prenda que la tienda saca de su cajón de deporte deja de
    estar marcada. Sin el borrado, la marca solo podría crecer y el filtro acabaría enseñando lo que
    la tienda dejó de decir hace meses.

    **Solo se tocan las etiquetas de `tags.fiables`**, o sea aquellas cuya fuente se pudo listar
    entera. Una hoja caída no significa «esta tienda ya no tiene nada deportivo»: sin este acote, la
    pasada siguiente a un 404 borraría las marcas de toda la tienda en silencio. Es la misma regla
    que `safe_scopes` aplica a las bajas, y aquí importa más porque no hay histéresis ni sondeo
    detrás que lo amortigüen.

    Y solo las de `TAGS_CONOCIDOS`: si algún día otra herramienta escribe en esta tabla, una pasada
    del scraper no se lleva su trabajo por delante.
    """
    reconciliables = sorted(tags.fiables & TAGS_CONOCIDOS)
    if not reconciliables:
        return {}

    # Tres arrays en paralelo, que es lo que permite resolver `retailer_product_id -> product.id` y
    # la sección aplicable de cada eje en una sola sentencia. La sección va aquí y no en un `WHERE`
    # fijo porque es propiedad del eje: `deportiva` es solo ropa, y el que venga detrás decidirá.
    rpids: list[str] = []
    etiquetas: list[str] = []
    secciones: list[str | None] = []
    for rpid, observadas in tags.por_producto.items():
        for tag in sorted(observadas & set(reconciliables)):
            rpids.append(rpid)
            etiquetas.append(tag)
            secciones.append(SECCION_APLICABLE.get(tag))

    # El borrado va PRIMERO y mira el deseado sin filtrar por sección: si un producto cambió de
    # `ropa` a `zapateria`, su marca tiene que irse, y con el filtro puesto aquí se quedaría
    # huérfana para siempre —no estaría en el deseado válido, pero tampoco se borraría—.
    cur.execute(
        """
        DELETE FROM product_tag t
        USING product p
        WHERE t.product_id = p.id
          AND p.retailer_id = %s
          AND t.tag = ANY(%s)
          AND NOT EXISTS (
              SELECT 1 FROM unnest(%s::text[], %s::text[]) AS d(rpid, tag)
              WHERE d.rpid = p.retailer_product_id AND d.tag = t.tag
          )
        """,
        (retailer_id, reconciliables, rpids, etiquetas),
    )

    if rpids:
        cur.execute(
            """
            INSERT INTO product_tag (product_id, tag)
            SELECT p.id, d.tag
            FROM unnest(%s::text[], %s::text[], %s::text[]) AS d(rpid, tag, seccion)
            JOIN product p
              ON p.retailer_id = %s
             AND p.retailer_product_id = d.rpid
             AND (d.seccion IS NULL OR p.section = d.seccion)
            ON CONFLICT DO NOTHING
            """,
            (rpids, etiquetas, secciones, retailer_id),
        )

    # Se cuenta contra la BASE y no sobre `rpids`: la diferencia entre las dos cifras es justo lo
    # que se ha descartado por sección o por no estar el producto en el catálogo (los exclusivos de
    # una hoja mixta, que no ingerimos), y es el número que dice si una tienda está aportando algo.
    cur.execute(
        """
        SELECT t.tag, count(*)
        FROM product_tag t
        JOIN product p ON p.id = t.product_id
        WHERE p.retailer_id = %s AND t.tag = ANY(%s) AND p.delisted_at IS NULL
        GROUP BY t.tag
        """,
        (retailer_id, reconciliables),
    )
    return {str(row[0]): int(row[1]) for row in cur.fetchall()}


def _load_active_counts(cur: psycopg.Cursor, retailer_id: int) -> dict[_ScopeKey, int]:
    """Población activa por ámbito ANTES de esta pasada (base para la red de seguridad)."""
    cur.execute(
        """
        SELECT gender, section, category, count(*)
        FROM product
        WHERE retailer_id = %s AND delisted_at IS NULL
        GROUP BY gender, section, category
        """,
        (retailer_id,),
    )
    return {(row[0], row[1], row[2]): row[3] for row in cur.fetchall()}


def _moved_out_counts(
    entries: list[ListingEntry], existing: dict[str, _ExistingProduct]
) -> dict[_ScopeKey, int]:
    """Productos que han cambiado de ámbito, contados en su ámbito de ORIGEN (#174).

    `prior_active` habla el vocabulario que escribió la pasada anterior y `seen_by_scope` el del
    código de ahora, así que un cambio de clasificación —el arreglo unisex de #98, el de #139— hace
    que los productos «se muden»: el ámbito que pierde población parece desplomarse aunque no falte
    ni uno. Medido en Hipercor `niña/zapateria/zapatos`: 21 productos vivos, 0 perdidos, y las runs
    #45 y #46 avisando de caída sospechosa y omitiendo las bajas de ese ámbito.

    No hace falta reclasificar nada para verlo: la entrada del listado ya trae la clasificación de
    ahora y `existing` la de antes, así que cruzarlas por `retailer_product_id` dice exactamente
    quién se ha mudado y desde dónde.

    Solo cuentan los que estaban ACTIVOS: `prior_active` no incluye a los dados de baja, así que
    sumar uno de esos descontaría de una caída que sí es real. Y se cuenta en el ámbito de origen
    porque es el que hay que rescatar de la sospecha, no el de destino, que ya los ve llegar.
    """
    moved: Counter[_ScopeKey] = Counter()
    for entry in entries:
        prior = existing.get(entry.retailer_product_id)
        if prior is None or prior.delisted:
            continue
        if prior.scope != (entry.gender, entry.section, entry.category):
            moved[prior.scope] += 1
    return dict(moved)


def _suspicious_scopes(
    scanned: list[ScrapeScope],
    seen_by_scope: dict[_ScopeKey, int],
    prior_active: dict[_ScopeKey, int],
    moved_out: dict[_ScopeKey, int],
    min_baseline: int,
    drop_ratio: float,
) -> set[_ScopeKey]:
    """Ámbitos donde lo observado cae de forma sospechosa (posible fallo, no retirada real).

    Solo se consideran ámbitos con una población previa mínima (`min_baseline`) para no
    saltar por ruido en ámbitos pequeños. Devuelve las claves cuyas bajas hay que OMITIR.

    Lo mudado cuenta como visto (#174): un producto que ahora se lista en otro ámbito **no ha
    desaparecido**, así que restarlo de la caída es lo que distingue una reclasificación de una
    tienda rota. No afloja la red donde importa: si un ámbito se vacía de verdad, sus productos no
    aparecen en ningún otro y `moved_out` es cero. Y si solo se muda parte, los que faltan de verdad
    siguen su camino normal por histéresis y sondeo de confirmación.
    """
    suspicious: set[_ScopeKey] = set()
    for scope in scanned:
        key = (scope.gender, scope.section, scope.category)
        base = prior_active.get(key, 0)
        seen = seen_by_scope.get(key, 0) + moved_out.get(key, 0)
        if base >= min_baseline and seen < base * drop_ratio:
            suspicious.add(key)
    return suspicious


def _scope_conditions(scopes: list[ScrapeScope], prefix: str = "") -> tuple[str, list[str | None]]:
    """Construye el `WHERE (... OR ...)` que acota una acción a un conjunto de ámbitos."""
    template = (
        f"({prefix}gender IS NOT DISTINCT FROM %s"
        f" AND {prefix}section IS NOT DISTINCT FROM %s"
        f" AND {prefix}category IS NOT DISTINCT FROM %s)"
    )
    clause = " OR ".join(template for _ in scopes)
    params: list[str | None] = []
    for scope in scopes:
        params.extend((scope.gender, scope.section, scope.category))
    return clause, params


def _reset_missing(cur: psycopg.Cursor, retailer_id: int, run_ts: datetime) -> None:
    """Pone a cero la racha de lo visto en esta pasada, sea cual sea su ámbito.

    Haber sido visto es evidencia positiva: no se acota por ámbito ni por sospecha.
    """
    cur.execute(
        """
        UPDATE product SET missing_streak = 0
        WHERE retailer_id = %s AND last_seen_at = %s AND missing_streak <> 0
        """,
        (retailer_id, run_ts),
    )
    cur.execute(
        """
        UPDATE variant v SET missing_streak = 0
        FROM product p
        WHERE v.product_id = p.id AND p.retailer_id = %s
          AND v.last_seen_at = %s AND v.missing_streak <> 0
        """,
        (retailer_id, run_ts),
    )


def _advance_missing(
    cur: psycopg.Cursor,
    retailer_id: int,
    run_ts: datetime,
    safe_scopes: list[ScrapeScope],
    min_misses: int,
) -> tuple[int, int]:
    """Suma una pasada sin ver a lo ausente dentro de los ámbitos seguros.

    Un ámbito no escaneado o sospechoso NO avanza el contador: una pasada fallida no
    debe gastar intentos. Devuelve cuántos productos/variantes quedan ausentes pero aún
    por debajo del umbral (los que sí lo alcanzan los da de baja `_delist`).
    """
    if not safe_scopes:
        return 0, 0
    prod_clause, prod_params = _scope_conditions(safe_scopes)
    cur.execute(
        f"""
        WITH bumped AS (
            UPDATE product SET missing_streak = missing_streak + 1
            WHERE retailer_id = %s AND delisted_at IS NULL AND last_seen_at < %s
              AND ({prod_clause})
            RETURNING missing_streak
        )
        SELECT count(*) FROM bumped WHERE missing_streak < %s
        """,
        [retailer_id, run_ts, *prod_params, min_misses],
    )
    products_missing = _scalar_int(cur)

    var_clause, var_params = _scope_conditions(safe_scopes, prefix="p.")
    cur.execute(
        f"""
        WITH bumped AS (
            UPDATE variant v SET missing_streak = v.missing_streak + 1
            FROM product p
            WHERE v.product_id = p.id AND p.retailer_id = %s
              AND v.delisted_at IS NULL AND v.last_seen_at < %s
              AND ({var_clause})
            RETURNING v.missing_streak
        )
        SELECT count(*) FROM bumped WHERE missing_streak < %s
        """,
        [retailer_id, run_ts, *var_params, min_misses],
    )
    return products_missing, _scalar_int(cur)


@dataclass
class ProbeOutcome:
    """Resultado de la confirmación activa de una pasada (ver `_confirm_candidates`).

    Los cinco contadores se persisten en `scrape_run.probes_*` (migración 0028, #261): sin ellos
    la única huella del mecanismo era un sumando dentro de `errors`, y la pregunta "¿el pool de
    candidatos crece o se drena?" obligaba a leer el log de un pod que se recicla.
    `sent + over_cap` es el pool de la pasada; `dead` es el drenaje real.

    `over_cap` y `unresolved` estaban juntos y se separan aquí porque **no son lo mismo**, y esa es
    toda la diferencia entre una alarma útil y una que nadie mira: quedarse fuera del tope es la
    rutina de una tienda con muchos candidatos, mientras que quedarse sin veredicto es la tienda
    negándose a contestar. Solo el segundo suma en `errors`.
    """

    sent: int = 0
    alive: int = 0
    dead: int = 0
    over_cap: int = 0  # no cabían en el tope de sondeos: rutina, se sondean en la siguiente
    unresolved: int = 0  # sondeados y sin veredicto: fallo de red, bloqueo o respuesta ambigua
    blocked_ids: list[int] = field(default_factory=list)  # `product.id` que NO se dan de baja


def _load_delist_candidates(
    cur: psycopg.Cursor,
    retailer_id: int,
    run_ts: datetime,
    safe_scopes: list[ScrapeScope],
    min_misses: int,
) -> list[tuple[int, str, str | None]]:
    """Los que `_delist` descatalogaría en esta pasada, con el más ausente primero."""
    clause, params = _scope_conditions(safe_scopes)
    cur.execute(
        f"""
        SELECT id, retailer_product_id, url
        FROM product
        WHERE retailer_id = %s AND delisted_at IS NULL AND last_seen_at < %s
          AND missing_streak >= %s AND ({clause})
        ORDER BY missing_streak DESC, id
        """,
        [retailer_id, run_ts, min_misses, *params],
    )
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _rescue(cur: psycopg.Cursor, product_ids: list[int]) -> None:
    """Pone a cero la racha de los confirmados vivos (producto y sus variantes).

    Al desaparecer del listado el producto entero, no hay evidencia de que sus tallas se
    retiraran: se rescatan también las variantes. Con la racha a cero quedan por debajo del
    umbral y `_delist` ya no los toca.
    """
    if not product_ids:
        return
    cur.execute("UPDATE product SET missing_streak = 0 WHERE id = ANY(%s::int[])", (product_ids,))
    cur.execute(
        "UPDATE variant SET missing_streak = 0 WHERE product_id = ANY(%s::int[])", (product_ids,)
    )


def _confirm_candidates(
    cur: psycopg.Cursor,
    store: BaseStore,
    retailer_id: int,
    run_ts: datetime,
    safe_scopes: list[ScrapeScope],
    min_misses: int,
    max_probes: int,
) -> ProbeOutcome:
    """(#4) Pregunta a la tienda por los candidatos a baja antes de descatalogarlos.

    Solo se da de baja lo confirmado como retirado: sin veredicto (fallo de red, bloqueo,
    o candidatos que se salen del tope de sondeos) el producto queda bloqueado para esta
    pasada, conserva su racha y se reintenta en la siguiente.
    """
    if not safe_scopes or max_probes <= 0 or not isinstance(store, SupportsAliveProbe):
        return ProbeOutcome()

    candidates = _load_delist_candidates(cur, retailer_id, run_ts, safe_scopes, min_misses)
    if not candidates:
        return ProbeOutcome()

    # Lo que no cabe en el tope se queda sin sondear: tampoco se da de baja (se reintenta).
    over_cap = candidates[max_probes:]
    candidates = candidates[:max_probes]
    outcome = ProbeOutcome(
        sent=len(candidates),
        over_cap=len(over_cap),
        blocked_ids=[product_id for product_id, _pid, _url in over_cap],
    )

    try:
        verdicts = store.probe_alive(
            DelistCandidate(retailer_product_id=pid, url=url) for _id, pid, url in candidates
        )
    except Exception:  # un sondeo roto no debe tumbar la ingesta: todo queda sin veredicto
        verdicts = {}

    alive_ids: list[int] = []
    for product_id, retailer_product_id, _url in candidates:
        verdict = verdicts.get(retailer_product_id)
        if verdict is True:
            alive_ids.append(product_id)
            outcome.alive += 1
        elif verdict is False:
            outcome.dead += 1
        else:  # no concluyente: ni se rescata ni se da de baja
            outcome.blocked_ids.append(product_id)
            outcome.unresolved += 1
    _rescue(cur, alive_ids)
    return outcome


def _delist(
    cur: psycopg.Cursor,
    retailer_id: int,
    run_ts: datetime,
    safe_scopes: list[ScrapeScope],
    min_misses: int,
    blocked_ids: list[int],
) -> tuple[int, int]:
    """Marca bajas (producto y variante) SOLO dentro de los ámbitos seguros y no vistos.

    Con histéresis: hace falta llevar `min_misses` pasadas consecutivas sin aparecer. Los
    `blocked_ids` son productos cuyo sondeo no fue concluyente: se dejan para otra pasada.
    """
    if not safe_scopes:
        return 0, 0
    prod_clause, prod_params = _scope_conditions(safe_scopes)
    cur.execute(
        f"""
        UPDATE product SET delisted_at = %s
        WHERE retailer_id = %s AND delisted_at IS NULL AND last_seen_at < %s
          AND missing_streak >= %s AND NOT (id = ANY(%s::int[])) AND ({prod_clause})
        """,
        [run_ts, retailer_id, run_ts, min_misses, blocked_ids, *prod_params],
    )
    products_delisted = cur.rowcount

    var_clause, var_params = _scope_conditions(safe_scopes, prefix="p.")
    cur.execute(
        f"""
        UPDATE variant v SET delisted_at = %s
        FROM product p
        WHERE v.product_id = p.id AND p.retailer_id = %s
          AND v.delisted_at IS NULL AND v.last_seen_at < %s
          AND v.missing_streak >= %s AND NOT (p.id = ANY(%s::int[])) AND ({var_clause})
        """,
        [run_ts, retailer_id, run_ts, min_misses, blocked_ids, *var_params],
    )
    return products_delisted, cur.rowcount


def _render_scope(scope: ScrapeScope | _ScopeKey) -> str:
    """`niña/ropa/sudaderas`. El `-` es un ámbito que la tienda deja sin declarar, no un error."""
    partes = (
        (scope.gender, scope.section, scope.category) if isinstance(scope, ScrapeScope) else scope
    )
    return "/".join(p or "-" for p in partes)


def _success_message(
    report: ScanReport,
    suspicious: set[_ScopeKey],
    gender_frozen: int = 0,
    rescued: set[_ScopeKey] | None = None,
) -> str | None:
    """Por qué esta pasada, aun con éxito, no está del todo limpia. `None` si lo está (#151).

    Existe porque `errors` es un entero que suma tres cosas distintas —ámbitos sospechosos, hojas
    caídas y sondeos sin veredicto— y el detalle solo se decía por stdout. Una pasada de QA cerró
    en `success` con `errors = 15` llevando dentro una hoja de Sfera retirada, o sea un ámbito
    entero sin detección de bajas durante semanas; cuando alguien fue a mirar, el log del pod ya se
    había reciclado y no había forma de saber **qué hoja**.

    Había un cuarto sumando y desde #261 ya no: los candidatos que no caben en el tope de sondeos.
    Se midió que no son un error —de 40 candidatos de Zara ausentes 14+ días, 39 tenían stock— sino
    prendas vivas que el listado ha dejado de ver, así que viven en `scrape_run.probes_over_cap`
    (migración 0028). Los sondeos SIN VEREDICTO sí siguen sumando: ésos son la tienda sin contestar.

    Devolver `None` cuando no hay nada que contar no es cosmética: es lo que hace que la consulta
    sea `WHERE message IS NOT NULL` en vez de un `LIKE` sobre texto libre.

    `gender_frozen` entra aquí aunque NO sea un error y no sume en `errors`: es una decisión que
    esta pasada ha tomado sobre el dato guardado (#172), y si solo se dijera por stdout habría que
    creerse el log de un pod que se recicla — que es exactamente lo que hizo falta y no había
    cuando se midió el caso de Hipercor.

    `rescued` entra por el mismo motivo y con la misma condición de no sumar en `errors` (#174):
    son ámbitos que HABRÍAN salido como caída sospechosa y no lo hacen porque sus productos se han
    mudado. Dicho aquí, un cambio de clasificación deja rastro en la fila de la pasada en vez de
    parecer que no pasó nada.
    """
    partes: list[str] = []
    if report.leaves_failed:
        detalle = f"hojas caidas {report.leaves_failed}/{report.leaves_total}"
        nombradas = sorted(report.failed_leaves)[:_MAX_NAMED_LEAVES]
        if nombradas:
            de_mas = len(report.failed_leaves) - len(nombradas)
            cola = f" +{de_mas}" if de_mas > 0 else ""
            detalle += f" [{', '.join(nombradas)}{cola}]"
        partes.append(detalle)
    # Fuera del `if` de arriba a propósito (#200): desde que existe `filtro_vacio()` un ámbito puede
    # quedarse sin bajas SIN que se haya caído ninguna hoja, y ése es justo el caso que hay que
    # poder leer — la pasada se ve perfecta y hay una categoría entera sin detección de bajas.
    if report.empty_filter_leaves:
        nombradas = sorted(report.empty_filter_leaves)[:_MAX_NAMED_LEAVES]
        de_mas = len(report.empty_filter_leaves) - len(nombradas)
        cola = f" +{de_mas}" if de_mas > 0 else ""
        partes.append(f"hojas sin nada que casara el filtro [{', '.join(nombradas)}{cola}]")
    if report.failed_scopes:
        ambitos = sorted(_render_scope(s) for s in report.failed_scopes)
        partes.append(f"ambitos sin bajas: {', '.join(ambitos)}")
    if suspicious:
        ambitos = sorted(_render_scope(s) for s in suspicious)
        partes.append(f"ambitos con caida sospechosa: {', '.join(ambitos)}")
    if rescued:
        ambitos = sorted(_render_scope(s) for s in rescued)
        partes.append(f"ambitos remapeados: {', '.join(ambitos)}")
    if gender_frozen:
        partes.append(f"generos conservados: {gender_frozen}")
    return " · ".join(partes)[:_MAX_FAIL_MESSAGE] if partes else None


def _record_failed_run(
    conn: psycopg.Connection, store: BaseStore, run_ts: datetime, exc: BaseException
) -> None:
    """Deja constancia en `scrape_run` de una pasada que no llegó a escribir nada.

    Va en una transacción NUEVA, después del rollback: la fila que abrió la pasada se fue con él,
    así que sin esto un fallo no deja rastro ninguno en BD y solo se ve en los logs del pod —
    exactamente cómo Zara pudo pasarse cuatro días sin poder ingerir sin que nadie lo notara. Con
    la fila, "¿desde cuándo falla esta tienda?" es una consulta.

    Que este registro falle no puede tapar el error original (que se está propagando), así que se
    traga cualquier excepción: es información útil, no parte del contrato. Eso incluye el caso de
    #169 — si la pasada murió por un `lock_timeout`, este `_upsert_retailer` choca con el MISMO
    lock y agota otro timeout antes de rendirse. O sea que una pasada bloqueada tarda ~2× el
    `lock_timeout` en morir; sigue siendo segundos frente a las cinco horas de antes.
    """
    try:
        with conn.cursor() as cur:
            retailer_id = _upsert_retailer(cur, store)
            cur.execute(
                """
                INSERT INTO scrape_run (retailer_id, started_at, finished_at, status, errors,
                                        message)
                VALUES (%s, %s, now(), 'failed', 1, %s)
                """,
                (retailer_id, run_ts, f"{type(exc).__name__}: {exc}"[:_MAX_FAIL_MESSAGE]),
            )
        conn.commit()
    except Exception:
        conn.rollback()


def ingest(
    conn: psycopg.Connection,
    store: BaseStore,
    run_ts: datetime | None = None,
    *,
    delist_min_baseline: int = DEFAULT_DELIST_MIN_BASELINE,
    delist_drop_ratio: float = DEFAULT_DELIST_DROP_RATIO,
    delist_min_misses: int = DEFAULT_DELIST_MIN_MISSES,
    delist_probe: bool = True,
    delist_probe_max: int = DEFAULT_DELIST_PROBE_MAX,
    detail_max_age_days: int = DEFAULT_DETAIL_MAX_AGE_DAYS,
    detail_refresh_max: int = DEFAULT_DETAIL_REFRESH_MAX,
    detail_refresh_all: bool = False,
    scan_max_dead_ratio: float = DEFAULT_SCAN_MAX_DEAD_RATIO,
    progress_every_seconds: float = DEFAULT_PROGRESS_EVERY_SECONDS,
) -> IngestResult:
    """Ejecuta una pasada completa del scraper y persiste el resultado. Atómico."""
    run_ts = run_ts or datetime.now(UTC)
    latido = Latido(progress_every_seconds, store.slug, _LOG)
    # Sin mirar el reloj: es lo que distingue «listando» de «colgada antes de empezar», que hoy se
    # parecen mucho porque las dos escriben exactamente lo mismo, o sea nada.
    latido.anuncia("pasada arrancada")
    try:
        with conn.cursor() as cur:
            retailer_id = _upsert_retailer(cur, store)
            cur.execute(
                "INSERT INTO scrape_run (retailer_id, started_at) VALUES (%s, %s) RETURNING id",
                (retailer_id, run_ts),
            )
            run_id = _scalar_int(cur)
            existing = _load_existing(cur, retailer_id)
            prior_active = _load_active_counts(cur, retailer_id)  # baseline por ámbito

            # Fase 1: listado barato. Decidimos a quién pedir detalle y a quién solo "tocar".
            # Se acumula a mano en vez de con `list(...)` para poder latir mientras baja: con la
            # llamada envuelta, una tienda que tarda media hora en listar no se distingue de una
            # colgada. **Pero no en todas**: `hm` e `hipercor` acumulan la pasada entera antes de
            # emitir nada —la primera porque su fila de listado es producto+color, la segunda porque
            # el 13 % de su catálogo sale en las dos ramas de género y el ámbito de una entrada ya
            # emitida no se puede corregir (#98)—, así que en esas dos el listado sigue siendo mudo
            # y solo se ve la línea de frontera al acabarlo. Medido en dev el 04/08/2026: 6 min de
            # silencio en la fase 1 de Hipercor. Cubrirlo exigiría latir DENTRO del bucle de hojas
            # de cada tienda, que es otro alcance; lo que sí se ve ya es la fase 2, que en Hipercor
            # es ~204 min de 208.
            entries: list[ListingEntry] = []
            for entry in store.list_catalog():
                entries.append(entry)
                latido.late(f"listando · {len(entries)} entradas")
            listado_seg = latido.transcurrido

            # Hojas caídas durante el listado (#41). El informe se lee DESPUÉS de consumir el
            # generador entero, que es cuando existe.
            report = store.scan_report() if isinstance(store, SupportsScanReport) else ScanReport()
            # Los ejes transversales (#180) se leen en el mismo punto y por la misma razón: los
            # alimentan hojas que se recorren durante el listado. Una tienda que no publique
            # ninguno devuelve el vacío y no se toca `product_tag`.
            tags = store.product_tags() if isinstance(store, SupportsProductTags) else ProductTags()
            if report.dead_ratio > scan_max_dead_ratio:
                raise CatalogScanAborted(
                    f"{report.leaves_failed} de {report.leaves_total} hojas de categoría no "
                    f"responden (> {scan_max_dead_ratio:.0%}): la pasada se aborta sin escribir. "
                    "Revisa si la tienda ha reestructurado el catálogo o nos está bloqueando."
                )
            # Un ámbito con CUALQUIER hoja caída queda fuera de las bajas: lo que no se ha podido
            # mirar no está retirado. Si no, sus productos contarían como ausentes y acabarían
            # descatalogados — y la red por umbral no lo salva, porque un ámbito alimentado por
            # seis hojas solo pierde un 17 % al caerse una, lejos del 50 % que dispara la sospecha.
            declared = list(dict.fromkeys(store.scopes()))  # ámbitos de la tienda, sin duplicar
            scanned = [s for s in declared if s not in report.failed_scopes]

            to_fetch: list[ListingEntry] = []
            signature_by_id: dict[str, str] = {}
            unchanged: list[tuple[ListingEntry, _ExistingProduct]] = []
            for entry in entries:
                prior = existing.get(entry.retailer_product_id)
                if _needs_detail(entry, prior):
                    to_fetch.append(entry)
                    signature_by_id[entry.retailer_product_id] = entry.signature
                else:
                    assert prior is not None  # _needs_detail ya devolvió True si no existía
                    unchanged.append((entry, prior))

            # Refresco forzado: los sin cambios con el detalle más rancio se piden igualmente,
            # porque su huella no va a cambiar nunca sola y sin re-observaciones no hay serie.
            to_refresh = _stale_refreshes(
                unchanged, run_ts, detail_max_age_days, detail_refresh_max, detail_refresh_all
            )
            products_unchanged = gender_stale = 0
            for entry, prior in unchanged:
                if entry.retailer_product_id in to_refresh:
                    to_fetch.append(entry)
                    # Con su huella ACTUAL: escribir otra cosa haría que la pasada siguiente la
                    # viera cambiada y volviese a pedir el detalle de todo, en bucle.
                    signature_by_id[entry.retailer_product_id] = entry.signature
                else:
                    _touch_seen(cur, prior.id, run_ts)
                    products_unchanged += 1
                    # El género de la fila se queda como estaba: `_touch_seen` no lo toca y el
                    # listado ya no vuelve a mirarse. Se cuenta aquí, que es el único punto donde
                    # se sabe a la vez lo que dice el listado y lo que hay guardado.
                    if prior.gender is not None and prior.gender != entry.gender:
                        gender_stale += 1

            # La frontera entre fases, sin mirar el reloj: es la línea que convierte un «más de 300
            # minutos» en «el listado tardó X y el resto se fue en fichas», que era justo lo que no
            # se pudo decir de ninguno de los cuatro intentos de #93.
            total_fetch = len(to_fetch)
            latido.anuncia(
                f"listado: {len(entries)} entradas en {duracion(listado_seg)} · "
                f"se piden {total_fetch} fichas, {products_unchanged} sin cambios"
            )

            # Fase 2: detalle de nuevos/cambiados/rancios -> upsert + apilar precio.
            details_fetched = variants_seen = prices_recorded = gender_frozen = 0
            fase2_inicio = latido.transcurrido
            for product in store.fetch_details(to_fetch):
                details_fetched += 1
                # El ritmo se mide sobre la fase 2 sola: mezclarle el listado da un s/ficha que
                # empieza altísimo y baja solo, y es el número con el que se compara una tienda
                # consigo misma entre pasadas (el cap de CPU de Hipercor, §4 de #93).
                gastado = latido.transcurrido - fase2_inicio
                por_ficha = gastado / details_fetched
                quedan = total_fetch - details_fetched
                latido.late(
                    f"fichas {details_fetched}/{total_fetch} "
                    f"({details_fetched * 100 // max(total_fetch, 1)}%) · "
                    f"{por_ficha:.1f} s/ficha · faltan ~{duracion(quedan * por_ficha)}"
                )
                signature = signature_by_id.get(product.retailer_product_id, "")
                gender = _gender_a_escribir(
                    product, existing.get(product.retailer_product_id), report.cross_gender_suspect
                )
                if gender != product.gender:
                    gender_frozen += 1
                product_id = _upsert_product(cur, retailer_id, run_ts, product, signature, gender)
                _replace_product_images(cur, product_id, product.images)
                for variant in product.variants:
                    variant_id = _upsert_variant(cur, product_id, run_ts, variant)
                    _record_price(cur, variant_id, run_id, run_ts, variant)
                    variants_seen += 1
                    prices_recorded += 1

            # Bajas acotadas: (#1) solo en ámbitos realmente escaneados y (#2) descartando
            # los que sufren una caída sospechosa (posible fallo de scraping, no retirada real).
            seen_by_scope: dict[_ScopeKey, int] = Counter(
                (e.gender, e.section, e.category) for e in entries
            )
            # …y (#174) lo que no ha desaparecido, solo se ha mudado de ámbito: sin esto, cualquier
            # cambio de clasificación deja al ámbito de origen sin bajas durante una o dos pasadas.
            moved_out = _moved_out_counts(entries, existing)
            suspicious = _suspicious_scopes(
                scanned,
                seen_by_scope,
                prior_active,
                moved_out,
                delist_min_baseline,
                delist_drop_ratio,
            )
            # Los RESCATADOS por la mudanza: los que habrían saltado sin contarla. Se publican
            # aunque NO sean un error porque si el rescate fuera mudo, una regresión que dejara de
            # detectar la mudanza se vería exactamente igual que el arreglo funcionando. Mismo
            # criterio que `generos conservados` (#172). Se calcula volviendo a llamar con las
            # mudanzas a cero en vez de repitiendo el umbral aquí, que es lo que se desincronizaría.
            rescued = (
                _suspicious_scopes(
                    scanned, seen_by_scope, prior_active, {}, delist_min_baseline, delist_drop_ratio
                )
                - suspicious
                if moved_out
                else set()
            )
            safe_scopes = [
                s for s in scanned if (s.gender, s.section, s.category) not in suspicious
            ]
            # (#3) Histéresis: la ausencia suma una pasada y solo da de baja al llegar al umbral.
            _reset_missing(cur, retailer_id, run_ts)
            products_missing, variants_missing = _advance_missing(
                cur, retailer_id, run_ts, safe_scopes, delist_min_misses
            )
            # (#4) Confirmación activa: se pregunta a la tienda por los que iban a caer.
            probe = _confirm_candidates(
                cur,
                store,
                retailer_id,
                run_ts,
                safe_scopes,
                delist_min_misses,
                delist_probe_max if delist_probe else 0,
            )
            products_delisted, variants_delisted = _delist(
                cur, retailer_id, run_ts, safe_scopes, delist_min_misses, probe.blocked_ids
            )

            # `clock_timestamp()` y no `now()`: toda la pasada va en UNA transacción (ver la
            # cabecera del módulo) y `now()` devuelve la hora de INICIO de la transacción, así que
            # con él `finished_at` salía igual que `started_at` y toda pasada con éxito registraba
            # duración cero. El camino de fallo no lo sufría —`_record_failed_run` abre transacción
            # nueva—, o sea que hasta ahora lo único que sabíamos cronometrar era lo que reventaba.
            # Importa al fijar el `activeDeadlineSeconds` del CronJob de cada tienda: la ingesta es
            # atómica, y pasarse del deadline no es perder la pasada, es no poblar nunca.
            cur.execute(
                """
                UPDATE scrape_run
                SET finished_at = clock_timestamp(), status = 'success',
                    products_seen = %s, variants_seen = %s, errors = %s, message = %s,
                    probes_sent = %s, probes_alive = %s, probes_dead = %s,
                    probes_over_cap = %s, probes_unresolved = %s
                WHERE id = %s
                """,
                (
                    len(entries),
                    variants_seen,
                    # Los candidatos que no cupieron en el tope ya NO suman aquí (#261): se midió
                    # que no son un error ni prendas retiradas atrapadas —de 40 candidatos de Zara
                    # ausentes 14+ días, el sondeo llamaba vivos a los 40 y 39 tenían stock de
                    # verdad— sino prendas vivas que el listado ha dejado de ver. Contarlas hacía
                    # que tres validaciones seguidas leyeran una cobertura incompleta como una
                    # ingesta rota. Los sondeos SIN VEREDICTO sí siguen contando: ésos son la
                    # tienda negándose a contestar, que es justo lo que hay que cazar.
                    len(suspicious) + report.leaves_failed + probe.unresolved,
                    # `errors` cuenta; `message` dice QUÉ (#151). El desglose del sondeo no entra en
                    # `message`: tiene columnas propias (migración 0028), y meterlo aquí dejaría
                    # casi ninguna pasada con `message` a NULL —lo único que hace útil la consulta.
                    _success_message(report, suspicious, gender_frozen, rescued),
                    probe.sent,
                    probe.alive,
                    probe.dead,
                    probe.over_cap,
                    probe.unresolved,
                    run_id,
                ),
            )
            barefoot_counts = _barefoot_counts(cur, retailer_id)
            # Después de las bajas: así el recuento que se publica ya excluye lo retirado en esta
            # misma pasada y es el que se puede comparar con lo que enseña el catálogo.
            tag_counts = _reconcile_tags(cur, retailer_id, tags)
        conn.commit()
        return IngestResult(
            scrape_run_id=run_id,
            products_in_catalog=len(entries),
            details_fetched=details_fetched,
            details_refreshed=len(to_refresh),
            products_unchanged=products_unchanged,
            variants_seen=variants_seen,
            prices_recorded=prices_recorded,
            products_delisted=products_delisted,
            variants_delisted=variants_delisted,
            products_missing=products_missing,
            variants_missing=variants_missing,
            scanned_scopes=len(scanned),
            skipped_scopes=len(suspicious),
            skipped_scope_names=sorted(_render_scope(s) for s in suspicious),
            remapped_scopes=len(rescued),
            remapped_scope_names=sorted(_render_scope(s) for s in rescued),
            leaves_scanned=report.leaves_total,
            leaves_failed=report.leaves_failed,
            failed_leaves=sorted(report.failed_leaves),
            empty_filter_leaves=sorted(report.empty_filter_leaves),
            unscanned_scopes=len(declared) - len(scanned),
            probes_sent=probe.sent,
            probes_alive=probe.alive,
            probes_dead=probe.dead,
            probes_over_cap=probe.over_cap,
            probes_unresolved=probe.unresolved,
            barefoot_counts=barefoot_counts,
            tag_counts=tag_counts,
            # `sin-marcar` para el género ausente, igual que `_barefoot_counts`: una tienda que no
            # lo declare tiene que verse en el reparto, no desaparecer de él.
            gender_counts=dict(sorted(Counter(e.gender or "sin-marcar" for e in entries).items())),
            gender_stale=gender_stale,
            gender_frozen=gender_frozen,
        )
    except Exception as exc:
        conn.rollback()
        _record_failed_run(conn, store, run_ts, exc)
        raise
