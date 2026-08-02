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
categoría retirada sino un bloqueo o un cambio de API.

Todo ocurre en una única transacción por ejecución (`scrape_run`), y si algo revienta se deshace
entera — pero **la pasada fallida se registra igual** (`status = 'failed'` y el motivo en
`message`, ver `_record_failed_run`): una tienda que deja de ingerir tiene que verse en la BD, no
solo en unos logs que rotan. Las bajas se detectan por
ausencia: lo no visto conserva un `last_seen_at` anterior, suma una pasada a `missing_streak`
y solo se marca `delisted_at` tras N pasadas consecutivas sin verlo (histéresis). Si la tienda
sabe responder por un producto concreto (`SupportsAliveProbe`), antes de descatalogar se le
pregunta directamente y solo se da de baja lo confirmado como retirado.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from .stores.base import (
    BaseStore,
    DelistCandidate,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    SupportsAliveProbe,
    SupportsScanReport,
)

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
# Tope del motivo que se guarda en `scrape_run.message`: un traceback de una librería puede venir
# con kilobytes de ruido y esta columna es para leerla de un vistazo.
_MAX_FAIL_MESSAGE = 500

_ScopeKey = tuple[str | None, str | None, str | None]


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
    leaves_scanned: int  # hojas de categoría recorridas en el listado
    leaves_failed: int  # de las anteriores, las que la tienda ya no sirve (404)
    unscanned_scopes: int  # ámbitos excluidos de las bajas por tener alguna hoja caída
    probes_sent: int  # candidatos a baja sondeados (confirmación activa)
    probes_alive: int  # el sondeo los encontró vivos: rescatados, no se dan de baja
    probes_dead: int  # el sondeo confirmó la retirada
    probes_unresolved: int  # sin veredicto (fallo, respuesta ambigua o fuera del tope)
    # Reparto si/no/desconocido del calzado ACTIVO de la tienda tras la pasada. Es el informe que
    # pide #30, y va aquí en vez de en una consulta a mano porque es la cifra que dice si el foco
    # barefoot tiene contenido: una zapatería que se queda en 0 productos `si` no es un detalle
    # técnico, es la mitad del producto vacía.
    barefoot_counts: dict[str, int] = field(default_factory=dict)


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


def _load_existing(cur: psycopg.Cursor, retailer_id: int) -> dict[str, _ExistingProduct]:
    """Estado actual por producto, indexado por `retailer_product_id`."""
    cur.execute(
        """
        SELECT retailer_product_id, id, listing_signature, (delisted_at IS NOT NULL),
               last_detail_at
        FROM product WHERE retailer_id = %s
        """,
        (retailer_id,),
    )
    return {
        row[0]: _ExistingProduct(
            id=row[1], signature=row[2], delisted=row[3], last_detail_at=row[4]
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
) -> int:
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
            product.gender,
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
    """
    if not images:
        return
    cur.execute("DELETE FROM product_image WHERE product_id = %s", (product_id,))
    siguiente: dict[str | None, int] = defaultdict(int)
    filas: list[tuple[int, str | None, int, str]] = []
    for img in images:
        filas.append((product_id, img.color, siguiente[img.color], img.url))
        siguiente[img.color] += 1
    cur.executemany(
        "INSERT INTO product_image (product_id, color, position, url) VALUES (%s, %s, %s, %s)",
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
) -> set[str]:
    """De los productos sin cambios, cuáles toca refrescar igualmente por antigüedad.

    Se eligen los **más rancios primero** y se recorta al presupuesto de la pasada: lo refrescado
    estrena `last_detail_at` y se va al final de la cola, así que pasadas sucesivas barren el
    catálogo en round-robin sin ráfagas. Con `max_age_days = 0` no se refresca nada (escape hatch).
    """
    if max_age_days <= 0 or refresh_max <= 0:
        return set()
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


def _suspicious_scopes(
    scanned: list[ScrapeScope],
    seen_by_scope: dict[_ScopeKey, int],
    prior_active: dict[_ScopeKey, int],
    min_baseline: int,
    drop_ratio: float,
) -> set[_ScopeKey]:
    """Ámbitos donde lo observado cae de forma sospechosa (posible fallo, no retirada real).

    Solo se consideran ámbitos con una población previa mínima (`min_baseline`) para no
    saltar por ruido en ámbitos pequeños. Devuelve las claves cuyas bajas hay que OMITIR.
    """
    suspicious: set[_ScopeKey] = set()
    for scope in scanned:
        key = (scope.gender, scope.section, scope.category)
        base = prior_active.get(key, 0)
        seen = seen_by_scope.get(key, 0)
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
    """Resultado de la confirmación activa de una pasada (ver `_confirm_candidates`)."""

    sent: int = 0
    alive: int = 0
    dead: int = 0
    unresolved: int = 0
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
        unresolved=len(over_cap),
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


def _record_failed_run(
    conn: psycopg.Connection, store: BaseStore, run_ts: datetime, exc: BaseException
) -> None:
    """Deja constancia en `scrape_run` de una pasada que no llegó a escribir nada.

    Va en una transacción NUEVA, después del rollback: la fila que abrió la pasada se fue con él,
    así que sin esto un fallo no deja rastro ninguno en BD y solo se ve en los logs del pod —
    exactamente cómo Zara pudo pasarse cuatro días sin poder ingerir sin que nadie lo notara. Con
    la fila, "¿desde cuándo falla esta tienda?" es una consulta.

    Que este registro falle no puede tapar el error original (que se está propagando), así que se
    traga cualquier excepción: es información útil, no parte del contrato.
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
    scan_max_dead_ratio: float = DEFAULT_SCAN_MAX_DEAD_RATIO,
) -> IngestResult:
    """Ejecuta una pasada completa del scraper y persiste el resultado. Atómico."""
    run_ts = run_ts or datetime.now(UTC)
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
            entries = list(store.list_catalog())

            # Hojas caídas durante el listado (#41). El informe se lee DESPUÉS de consumir el
            # generador entero, que es cuando existe.
            report = store.scan_report() if isinstance(store, SupportsScanReport) else ScanReport()
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
                unchanged, run_ts, detail_max_age_days, detail_refresh_max
            )
            products_unchanged = 0
            for entry, prior in unchanged:
                if entry.retailer_product_id in to_refresh:
                    to_fetch.append(entry)
                    # Con su huella ACTUAL: escribir otra cosa haría que la pasada siguiente la
                    # viera cambiada y volviese a pedir el detalle de todo, en bucle.
                    signature_by_id[entry.retailer_product_id] = entry.signature
                else:
                    _touch_seen(cur, prior.id, run_ts)
                    products_unchanged += 1

            # Fase 2: detalle de nuevos/cambiados/rancios -> upsert + apilar precio.
            details_fetched = variants_seen = prices_recorded = 0
            for product in store.fetch_details(to_fetch):
                details_fetched += 1
                signature = signature_by_id.get(product.retailer_product_id, "")
                product_id = _upsert_product(cur, retailer_id, run_ts, product, signature)
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
            suspicious = _suspicious_scopes(
                scanned, seen_by_scope, prior_active, delist_min_baseline, delist_drop_ratio
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

            cur.execute(
                """
                UPDATE scrape_run
                SET finished_at = now(), status = 'success',
                    products_seen = %s, variants_seen = %s, errors = %s
                WHERE id = %s
                """,
                (
                    len(entries),
                    variants_seen,
                    len(suspicious) + probe.unresolved + report.leaves_failed,
                    run_id,
                ),
            )
            barefoot_counts = _barefoot_counts(cur, retailer_id)
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
            leaves_scanned=report.leaves_total,
            leaves_failed=report.leaves_failed,
            unscanned_scopes=len(declared) - len(scanned),
            probes_sent=probe.sent,
            probes_alive=probe.alive,
            probes_dead=probe.dead,
            probes_unresolved=probe.unresolved,
            barefoot_counts=barefoot_counts,
        )
    except Exception as exc:
        conn.rollback()
        _record_failed_run(conn, store, run_ts, exc)
        raise
