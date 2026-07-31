"""Scraper de Cacles Barefoot (caclesbarefoot.com): la primera tienda barefoot NATIVA.

Es una Shopify, así que el catálogo sale por los endpoints JSON públicos que trae la plataforma
de serie, sin navegador ni anti-bot:

  - listado de colección: /collections/{handle}/products.json?limit=250&page=N
  - producto suelto:      /products/{handle}.json    (solo para `probe_alive`)

A diferencia de Zara, el listado YA trae variantes, precios e imágenes, así que no hay dos fases
reales: `list_catalog()` parsea productos completos y los cachea, y `fetch_details()` sirve de esa
caché sin volver a la red (mismo patrón que `sfera.py`). La tienda entera son **dos peticiones**.

Por qué esta tienda (#32): Zara, Sfera y Lefties son cadenas de moda convencional y entre las tres
dejaban la zapatería respetuosa en ~92 referencias. Aquí **todo el catálogo es barefoot**, así que
`barefoot='si'` se declara a nivel de tienda (`tienda_barefoot=True`) sin heurística de texto.

Dos cosas que hay que tener presentes al tocar este fichero:

1. **Una colección que no existe responde 200 con `products: []`, no 404.** Comprobado en vivo. Es
   la trampa de Sfera (#54) con otra forma y peor: una hoja muerta parece "este ámbito se ha
   quedado vacío", que es exactamente lo que dispara una baja masiva falsa, y ni `GONE_STATUS` ni
   el `dead_ratio` se enteran solos. De ahí `_PAGINA_INICIAL` y el trato asimétrico de la lista
   vacía: en la primera página es una hoja muerta, a partir de la segunda es el fin normal de la
   paginación.
2. **`compare_at_price` suele venir IGUAL a `price`** (248 de 428 productos el 31/07/2026). Solo es
   precio tachado si es estrictamente mayor; tratarlo a ciegas inventaría un descuento del 0 % en
   más de la mitad del catálogo y ensuciaría justo al detector de ofertas engañosas.

Las funciones `parse_*` son puras (JSON -> dataclasses) y se testean con fixtures.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    DelistCandidate,
    LeafHealth,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)

logger = logging.getLogger(__name__)

SLUG = "cacles"  # a nivel de módulo porque las funciones puras de parseo también lo necesitan
BASE_URL = "https://www.caclesbarefoot.com/"
_COLLECTION_URL = BASE_URL + "collections/{handle}/products.json?limit={limit}&page={page}"
_PRODUCT_URL = BASE_URL + "products/{handle}.json"

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Shopify topa la página en 250 aunque se pida más (comprobado con limit=1000 -> 250).
_PAGE_SIZE = 250
_PAGINA_INICIAL = 1
# Tope de guarda para que un fallo de paginación no gire para siempre. Con 428 productos hoy
# sobran dos páginas; veinte dan margen de sobra para que la tienda crezca.
_MAX_PAGES = 20

# Nombre de la opción de variante que lleva el color. 305 de 428 productos NO la tienen (su única
# opción es la talla y el color va en el título), así que se busca por nombre y no por posición.
_OPCION_COLOR = "color"

# Valor con el que Shopify rellena la variante única de un producto SIN opciones reales. No es una
# talla y no debe guardarse como tal.
_VALOR_SIN_OPCION = "default title"

# `product_type` que no son producto seguible: una tarjeta regalo no tiene precio que vigilar y el
# medidor de pie es un accesorio suelto. Se excluyen de la ingesta.
_TIPOS_EXCLUIDOS = frozenset({"tarjetas de regalo", "medición", "medicion"})

# `product_type` de Cacles -> categoría de nuestro vocabulario. Las claves van en minúsculas
# (Cacles no es consistente: escribe "Zapatillas de Casa" y "medición").
#
# `sandalias` y `botas` son slugs NUEVOS, que estrena esta tienda junto con Lefties —que ya tenía
# hojas propias para ambos y las estaba colapsando a `zapatos`—. Zara no tiene hoja de ninguno de
# los dos y Sfera está por comprobar, así que de momento sus sandalias siguen dentro de `zapatos`.
#
# `plantillas` es exclusivo de aquí: son plantillas barefoot recortables, y quien busca calzado
# respetuoso también las busca. Los calcetines, en cambio, son ropa, y van a `ropa-interior` para
# juntarse con los del resto de tiendas.
_CATEGORIA_POR_TIPO: dict[str, tuple[str, str]] = {
    # --- sandalias ---
    "sandalias": ("zapateria", "sandalias"),
    "sandalias deportivas": ("zapateria", "sandalias"),
    "escarpines": ("zapateria", "sandalias"),  # calzado de agua: acompaña a la sandalia de verano
    # --- botas ---
    "botas y botines": ("zapateria", "botas"),
    "botas de nieve": ("zapateria", "botas"),
    "botas de agua": ("zapateria", "botas"),
    "botas de lluvia": ("zapateria", "botas"),
    "botines impermeables": ("zapateria", "botas"),
    "botines senderismo": ("zapateria", "botas"),
    # --- zapatillas (deportiva y de estar por casa) ---
    "deportivas": ("zapateria", "zapatillas"),
    "deportivas casual": ("zapateria", "zapatillas"),
    "deportivas bebé": ("zapateria", "zapatillas"),
    "casual sneakers": ("zapateria", "zapatillas"),
    "lonetas": ("zapateria", "zapatillas"),
    "lonetas barefoot": ("zapateria", "zapatillas"),
    "slip-ons": ("zapateria", "zapatillas"),
    "zapatillas de fútbol": ("zapateria", "zapatillas"),
    "zapatillas de running": ("zapateria", "zapatillas"),
    "zapatillas de trail running": ("zapateria", "zapatillas"),
    "zapatillas de gimnasia y baile": ("zapateria", "zapatillas"),
    # Mete la zapatilla de casa con la deportiva. Se sabe que mezcla dos cosas que un padre
    # compra por motivos distintos; si molesta, es un slug más (issue de vocabulario).
    "zapatillas de casa": ("zapateria", "zapatillas"),
    # --- zapatos ---
    "zapatos": ("zapateria", "zapatos"),
    "zapatos colegiales": ("zapateria", "zapatos"),
    "calzado": ("zapateria", "zapatos"),
    "bailarinas": ("zapateria", "zapatos"),
    "merceditas": ("zapateria", "zapatos"),
    "patucos": ("zapateria", "zapatos"),
    # --- accesorios y ropa ---
    "plantillas": ("zapateria", "plantillas"),
    "calcetines": ("ropa", "ropa-interior"),
}

# Destino de un `product_type` que no esté en el mapa. Cacles añade tipos cada temporada, y perder
# un producto en silencio es peor que clasificarlo de más: `zapatos` es el cajón menos comprometido
# dentro de zapatería, y el warning deja rastro para mapearlo bien.
_CATEGORIA_POR_DEFECTO = ("zapateria", "zapatos")

# Pares (sección, categoría) que el parser puede llegar a emitir. Es la mitad de `scopes()`.
_SECCION_CATEGORIA: tuple[tuple[str, str], ...] = tuple(
    dict.fromkeys((*_CATEGORIA_POR_TIPO.values(), _CATEGORIA_POR_DEFECTO))
)

# Géneros que puede emitir `_genero_desde_tags()`. Van los tres aunque hoy no haya ni un producto
# solo-`boys`: un ámbito declarado de más es inocuo (la red de seguridad de bajas ignora los que no
# llegan a `delist_min_baseline`), pero uno declarado de menos deja productos imposibles de dar de
# baja, porque las bajas solo tocan ámbitos escaneados.
_GENEROS: tuple[str, ...] = ("niño", "niña", "unisex")


@dataclass(frozen=True)
class CategoryConfig:
    """Una colección de Shopify que recorremos.

    A diferencia de las otras tiendas, aquí la hoja NO fija la categoría: la colección `infantil`
    trae calzado de todo tipo y es `product_type` quien decide, producto a producto. El género
    tampoco: sale de los tags. Por eso solo se declara el rango de edad que acota la colección.
    """

    collection_handle: str
    rango: str  # descripción legible, para los mensajes de `check_leaves()`


# Una sola colección: `infantil` es el paraguas de todo el catálogo infantil (428 productos el
# 31/07/2026) y ya incluye lo que cuelga de las colecciones por tipo (`sneakers`, `botas-y-botines`,
# `sandalias-barefoot-para-ninos`...), así que recorrerlas todas serían ~25 peticiones para los
# mismos productos. El resto del catálogo de Cacles es de adulto y queda fuera a propósito.
CATEGORIES: list[CategoryConfig] = [
    CategoryConfig("infantil", "infantil (todo el catálogo de niño)"),
]


def _precio(value: Any) -> Decimal | None:
    """Shopify da los precios como cadena decimal ("52.90"), no en céntimos.

    Nunca `Decimal(float)`: `Decimal(52.90)` arrastra la basura binaria del float.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _categoria_desde_tipo(product_type: str | None) -> tuple[str, str] | None:
    """`product_type` -> (sección, categoría). `None` si el producto no debe ingerirse."""
    tipo = (product_type or "").strip().lower()
    if tipo in _TIPOS_EXCLUIDOS:
        return None
    destino = _CATEGORIA_POR_TIPO.get(tipo)
    if destino is None:
        logger.warning(
            "cacles: product_type sin mapear %r, va a %s/%s",
            product_type,
            *_CATEGORIA_POR_DEFECTO,
        )
        return _CATEGORIA_POR_DEFECTO
    return destino


def _genero_desde_tags(tags: Iterable[str]) -> str:
    """Género a partir de los tags `boys`/`girls` de Shopify.

    El calzado barefoot infantil se diseña unisex y Cacles lo refleja: de 428 productos, 342
    llevan los dos tags, 80 solo `girls` y **ninguno** solo `boys` (31/07/2026). Por eso `unisex`
    no es un caso raro aquí sino el mayoritario, y por eso el catálogo del web tuvo que aprender a
    devolverlo tanto al filtrar por niño como por niña — si no, filtrar por "Niño" daría cero
    productos de esta tienda.

    Sin ninguno de los dos tags también se devuelve `unisex`: es lo que deja el producto visible
    en ambos filtros, y esconderlo sería peor que enseñarlo de más.
    """
    marcas = {t.strip().lower() for t in tags}
    nino, nina = "boys" in marcas, "girls" in marcas
    if nino and not nina:
        return "niño"
    if nina and not nino:
        return "niña"
    return "unisex"


def _claves_opcion(product: dict[str, Any]) -> tuple[str | None, str | None]:
    """Devuelve `(clave_talla, clave_color)`, es decir qué `optionN` lleva cada cosa.

    El color se busca POR NOMBRE, no por posición: 305 de 428 productos solo tienen la talla, así
    que dar por hecho que `option2` es el color metería la talla en el color de todo el catálogo.

    La talla, en cambio, es "la opción que no es el color", sin exigirle un nombre concreto. Buscar
    literalmente `Talla` parecía lo natural y perdía tallas en silencio: Cacles publica productos
    con la opción llamada `Size EUR` (unas botas Reima, 19 variantes) y otros con el `Title` que
    Shopify pone por defecto cuando el comerciante no la renombra (unos colegiales Poco Nido). En
    los dos casos el valor era la talla de toda la vida.
    """
    talla = color = None
    for opcion in product.get("options") or []:
        posicion = opcion.get("position")
        if not isinstance(posicion, int) or not 1 <= posicion <= 3:
            continue
        clave = f"option{posicion}"
        if str(opcion.get("name", "")).strip().lower() == _OPCION_COLOR:
            color = clave
        elif talla is None:
            talla = clave
    return talla, color


def _valor_opcion(variante: dict[str, Any], clave: str | None) -> str | None:
    """Lee una opción de la variante, descartando el centinela de Shopify."""
    if not clave:
        return None
    valor = variante.get(clave)
    if not valor or str(valor).strip().lower() == _VALOR_SIN_OPCION:
        return None
    return str(valor)


def _variantes(product: dict[str, Any], url: str | None) -> list[ScrapedVariant]:
    """Variantes con precio. El id de variante de Shopify es estable y ajeno a temporada."""
    clave_talla, clave_color = _claves_opcion(product)

    variantes: list[ScrapedVariant] = []
    for v in product.get("variants") or []:
        precio = _precio(v.get("price"))
        if precio is None:
            continue  # sin precio no hay nada que vigilar
        vid = v.get("id")
        if vid is None:
            continue
        # Solo es precio tachado si es MAYOR: Cacles manda `compare_at_price` igual al precio en
        # más de la mitad del catálogo, y darlo por bueno inventaría un descuento del 0 %.
        tachado = _precio(v.get("compare_at_price"))
        if tachado is not None and tachado <= precio:
            tachado = None
        sku = v.get("sku")
        variantes.append(
            ScrapedVariant(
                retailer_variant_id=str(vid),
                size=_valor_opcion(v, clave_talla),
                color=_valor_opcion(v, clave_color),
                sku=str(sku) if sku else None,
                price=precio,
                list_price=tachado,
                in_stock=bool(v.get("available")),
                url=url,
            )
        )
    return variantes


def _imagenes(product: dict[str, Any], variantes: list[ScrapedVariant]) -> list[ScrapedImage]:
    """Galería, atribuyendo cada foto a su color vía `images[].variant_ids`.

    El color tiene que salir del MISMO sitio que el de la variante (`base.ScrapedImage`): la ficha
    empareja foto y precio por ese texto. Donde el producto no tiene opción de color —la mayoría,
    porque Cacles publica cada color como producto aparte— queda `None`, que es el valor previsto
    para "foto que no se puede atribuir a un color concreto".
    """
    color_por_variante = {v.retailer_variant_id: v.color for v in variantes}
    imagenes: list[ScrapedImage] = []
    for img in product.get("images") or []:
        src = img.get("src")
        if not src:
            continue
        colores = {
            color
            for vid in img.get("variant_ids") or []
            if (color := color_por_variante.get(str(vid))) is not None
        }
        # Solo se atribuye si la foto apunta a un único color; si cubre varios (o ninguno) es una
        # foto genérica del producto y `None` lo dice mejor que elegir uno al azar.
        color = colores.pop() if len(colores) == 1 else None
        imagenes.append(ScrapedImage(color=color, url=str(src)))
    return imagenes


def product_signature(product: ScrapedProduct) -> str:
    """Huella del producto: precio y stock por variante, ordenados y estables.

    Entra el stock además del precio porque aquí el listado ya es el detalle: no hay una segunda
    petición que fuese a recogerlo, así que si no estuviera en la huella un cambio de
    disponibilidad no se llegaría a ingerir nunca.
    """
    partes = [f"{v.retailer_variant_id}:{v.price}:{int(v.in_stock)}" for v in product.variants]
    return "|".join(sorted(partes))


def parse_products(payload: dict[str, Any]) -> list[ScrapedProduct]:
    """Convierte una página de `products.json` en productos. Puro: sin red."""
    productos: list[ScrapedProduct] = []
    for raw in payload.get("products") or []:
        pid = raw.get("id")
        if pid is None:
            continue
        destino = _categoria_desde_tipo(raw.get("product_type"))
        if destino is None:
            continue  # tarjeta regalo, medidor de pie...
        section, category = destino

        handle = raw.get("handle")
        url = _PRODUCT_URL.format(handle=handle).removesuffix(".json") if handle else None
        variantes = _variantes(raw, url)
        if not variantes:
            continue  # sin variantes con precio no hay producto que seguir

        pid = str(pid)
        imagenes = _imagenes(raw, variantes)
        productos.append(
            ScrapedProduct(
                retailer_product_id=pid,
                name=str(raw.get("title") or ""),
                gender=_genero_desde_tags(raw.get("tags") or []),
                section=section,
                category=category,
                url=url,
                variants=variantes,
                # Toda la tienda es barefoot: se declara y no se pregunta al texto. Sin esto, un
                # "Zapatos colegiales" —que no nombra el concepto por ningún lado— saldría
                # `desconocido` y quedaría invisible con el filtro por defecto del catálogo.
                # `classify` sigue devolviendo None para los calcetines, que son ropa.
                barefoot=classify_barefoot(
                    retailer=SLUG,
                    retailer_product_id=pid,
                    section=section,
                    category=category,
                    tienda_barefoot=True,
                ),
                image_url=imagenes[0].url if imagenes else None,
                images=imagenes,
            )
        )
    return productos


class CaclesStore:
    """Scraper de Cacles Barefoot. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "Cacles Barefoot"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._cache: dict[str, ScrapedProduct] = {}

    def scopes(self) -> Iterable[ScrapeScope]:
        """Producto cartesiano de géneros × (sección, categoría) posibles.

        No se deduce de `self._categories` como en las otras tiendas: aquí la hoja no fija ni el
        género ni la categoría —los decide el parser producto a producto—, así que el ámbito hay
        que declararlo desde lo que el parser PUEDE emitir. Declarar de menos dejaría productos
        fuera del alcance de las bajas y por tanto imposibles de descatalogar.
        """
        return [
            ScrapeScope(genero, section, category)
            for genero in _GENEROS
            for section, category in _SECCION_CATEGORIA
        ]

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._config.user_agent, "Accept": "application/json"},
            timeout=self._config.request_timeout,
            follow_redirects=True,
        )

    def _polite_pause(self) -> None:
        """Pausa base entre peticiones con jitter (una cadencia fija es más detectable)."""
        base = self._config.request_delay
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _get_json(self, client: httpx.Client, url: str) -> Any:
        """GET con reintentos y backoff exponencial + jitter ante throttling/errores de red."""
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                self._backoff(attempt, retry_after=exc.response.headers.get("Retry-After"))
            except httpx.TransportError:
                if attempt == retries:
                    raise
                self._backoff(attempt)

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Espera exponencial (respeta Retry-After si viene) con jitter."""
        wait = self._config.retry_backoff * (2**attempt)
        if retry_after and retry_after.isdigit():
            wait = max(wait, float(retry_after))
        time.sleep(wait * random.uniform(0.8, 1.2))

    def _pagina(self, client: httpx.Client, handle: str, page: int) -> list[ScrapedProduct]:
        url = _COLLECTION_URL.format(handle=handle, limit=_PAGE_SIZE, page=page)
        payload = self._get_json(client, url)
        if not isinstance(payload, dict):
            raise ValueError(f"cacles: respuesta inesperada en {handle} pág. {page}")
        return parse_products(payload)

    def _hoja_retirada(self, handle: str) -> None:
        """Marca la colección como caída y saca TODOS sus ámbitos de las bajas.

        Se cuenta como UNA hoja (no una por ámbito) para que `leaves_total` siga midiendo lo que
        dice medir; los ámbitos restantes se añaden aparte porque esta tienda cubre todos con una
        sola colección. Con una única hoja configurada, `dead_ratio` pasa a 1.0 y la ingesta aborta
        sin escribir, que es la red que queremos.
        """
        ambitos = list(self.scopes())
        self._scan.leaf_gone(ambitos[0])
        self._scan.failed_scopes.update(ambitos[1:])
        logger.warning(
            "cacles: la colección %r no devolvió ningún producto; se trata como hoja retirada "
            "y se omiten sus bajas",
            handle,
        )

    def list_catalog(self) -> Iterable[ListingEntry]:
        self._scan = ScanReport()
        self._cache = {}
        emitted: set[str] = set()
        with self._client() as client:
            for cat in self._categories:
                viva = False
                for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
                    productos = self._pagina(client, cat.collection_handle, page)
                    if not productos:
                        # LA PARTE IMPORTANTE DE ESTE FICHERO. Una colección retirada NO da 404:
                        # devuelve 200 con la lista vacía, igual que la página siguiente a la
                        # última. En la primera página eso es una hoja muerta y hay que decirlo
                        # —si no, la ingesta lee "este ámbito se ha quedado vacío" y da de baja el
                        # catálogo entero—; a partir de la segunda es el fin normal de la
                        # paginación.
                        if not viva:
                            self._hoja_retirada(cat.collection_handle)
                        break
                    viva = True
                    for producto in productos:
                        pid = producto.retailer_product_id
                        if pid in emitted:
                            continue
                        emitted.add(pid)
                        self._cache[pid] = producto
                        yield ListingEntry(
                            retailer_product_id=pid,
                            signature=product_signature(producto),
                            gender=producto.gender,
                            section=producto.section,
                            category=producto.category,
                        )
                else:
                    logger.warning(
                        "cacles: %r alcanzó el tope de %d páginas; puede faltar catálogo",
                        cat.collection_handle,
                        _MAX_PAGES,
                    )
                if viva:
                    self._scan.leaf_ok()

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        # El listado ya trajo el detalle completo (variantes, precios y fotos vienen en el mismo
        # JSON), así que se sirve desde caché sin una sola petición extra.
        for entry in entries:
            producto = self._cache.get(entry.retailer_product_id)
            if producto is not None:
                yield producto

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las colecciones configuradas (ver `stores.base.SupportsLeafHealth`).

        Pide una sola página de una unidad: basta para distinguir la colección viva de la retirada,
        que aquí se reconoce por venir vacía y no por un 404.
        """
        with self._client() as client:
            for cat in self._categories:
                scope = ScrapeScope(None, None, None)  # la hoja no acota ámbito en esta tienda
                leaf = cat.collection_handle
                url = _COLLECTION_URL.format(handle=leaf, limit=1, page=_PAGINA_INICIAL)
                try:
                    payload = self._get_json(client, url)
                except httpx.HTTPStatusError as exc:
                    yield LeafHealth(scope, leaf, None, f"HTTP {exc.response.status_code}")
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, leaf, None, type(exc).__name__)
                else:
                    productos = payload.get("products") if isinstance(payload, dict) else None
                    if productos:
                        yield LeafHealth(scope, leaf, True, "HTTP 200 con catálogo")
                    else:
                        # 200 y vacío: en Shopify es lo que responde una colección inexistente.
                        yield LeafHealth(scope, leaf, False, "HTTP 200 pero sin productos")

    def _probe_one(self, client: httpx.Client, url: str | None) -> bool | None:
        """¿Sigue a la venta? True/False; None si la tienda no da respuesta utilizable."""
        if not url:
            return None  # sin URL no hay handle por el que preguntar
        try:
            payload = self._get_json(client, f"{url}.json")
        except httpx.HTTPStatusError as exc:
            # 404 es un veredicto ("ese handle ya no existe"); el resto, tras agotar los
            # reintentos, es un fallo nuestro y no vale como prueba de retirada.
            return False if exc.response.status_code == 404 else None
        except (httpx.TransportError, ValueError):  # red caída o respuesta no-JSON
            return None
        if not isinstance(payload, dict):
            return None  # forma inesperada: no arriesgamos una baja con esto
        return bool(payload.get("product"))

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`): un GET por candidato."""
        verdicts: dict[str, bool] = {}
        with self._client() as client:
            for candidate in candidates:
                verdict = self._probe_one(client, candidate.url)
                if verdict is not None:  # sin veredicto -> se omite del mapa
                    verdicts[candidate.retailer_product_id] = verdict
        return verdicts
