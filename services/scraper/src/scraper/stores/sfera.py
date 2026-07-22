"""Scraper de Sfera (niños): ropa y calzado infantil.

Sfera (grupo El Corte Inglés) corre sobre Salesforce Commerce Cloud (PWA "Moonshine") y
protege sus APIs con **Akamai Bot Manager**: el HTML de documento pasa con buenas cabeceras,
pero el listado/paginación exige cookies que solo se obtienen ejecutando el sensor JS. Por
eso este scraper usa un **navegador headless** (ver `stores/browser.py`): navega una vez a la
página de categoría (siembra cookies) y luego pide la API de listado `firefly/products_list`:

  GET /es/api/sfera-es/firefly/products_list/{category_path}/{page}/?showDimensions=none
  -> {"success": true, "data": {"products": [...], "pagination": {"_current","_total",...}}}

El listado ya trae el detalle completo (colores + tallas + precios + **foto**), así que **no
hay 2ª petición por producto**: `list_catalog()` recorre y cachea los productos, y
`fetch_details()` los devuelve desde caché (respetando el "detalle condicional" de la
ingesta vía la huella).

Para la **confirmación activa** antes de dar de baja (`probe_alive`) hay dos señales, de más
barata a más concluyente: el endpoint de stock por id (`firefly/stock`, JSON) prueba que el
producto sigue comprable si lo lista en `data.ADD`; y si no, la PDP resuelve la duda, porque
Sfera enruta por id y devuelve **404** para un id que ya no existe (el slug de la URL da igual:
redirige al canónico). Un producto agotado pero vivo sale de `ADD` y su PDP responde 200.

Id estable de producto: `id` (p.ej. "A200974138"). Id estable de variante: el `sku` de la
talla (p.ej. "001015811718640004"). Las funciones `parse_*` son puras (JSON -> dataclasses) y
se testean con fixtures capturados de la API real.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..config import Config
from .base import DelistCandidate, ListingEntry, ScrapedProduct, ScrapedVariant, ScrapeScope
from .browser import BrowserSession

BASE_ROOT = "https://www.sfera.com/es"
BASE_URL = BASE_ROOT + "/"
_SEED_URL = BASE_ROOT + "/{category_path}/"
_FIREFLY_URL = (
    BASE_ROOT + "/api/sfera-es/firefly/products_list/{category_path}/{page}/?showDimensions=none"
)
# Stock por id de producto: sondeo barato (JSON, sin renderizar) para la confirmación activa.
_STOCK_URL = BASE_ROOT + "/api/sfera-es/firefly/stock/2/?products={product_id}"

# Tope de guarda por si `_total` viniera anómalo (evita un bucle desbocado).
_MAX_PAGES = 200


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una categoría-hoja de Sfera a nuestro dominio (género/sección/categoría).

    `category_path` es el segmento de URL tras `/es/` (p.ej. "ninos/nina/pantalones"), que
    alimenta tanto la navegación de siembra como el endpoint firefly de listado.
    """

    category_path: str
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # pantalones | camisetas | sudaderas | vestidos | ropa-interior | zapatos


# Subconjunto curado (Fase 2): niño/niña, ropa vs zapatería y las categorías del brief
# (pantalones, camisetas, sudaderas/jerseys, vestidos, ropa interior) + calzado. Ampliable
# añadiendo entradas (el resto del código no cambia). Slugs de Sfera verificados en la API.
CATEGORIES: list[CategoryConfig] = [
    # --- niña ---
    CategoryConfig("ninos/nina/pantalones", "niña", "ropa", "pantalones"),
    CategoryConfig("ninos/nina/camisetas", "niña", "ropa", "camisetas"),
    CategoryConfig("ninos/nina/camisas-y-blusas", "niña", "ropa", "camisetas"),
    CategoryConfig("ninos/nina/sudaderas", "niña", "ropa", "sudaderas"),
    CategoryConfig("ninos/nina/punto-y-jerseis", "niña", "ropa", "sudaderas"),
    CategoryConfig("ninos/nina/vestidos-y-monos", "niña", "ropa", "vestidos"),
    CategoryConfig("ninos/nina/faldas", "niña", "ropa", "vestidos"),
    CategoryConfig("ninos/nina/pijamas-y-calcetines", "niña", "ropa", "ropa-interior"),
    CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos"),
    # --- niño ---
    CategoryConfig("ninos/nino/pantalones", "niño", "ropa", "pantalones"),
    CategoryConfig("ninos/nino/vaqueros", "niño", "ropa", "pantalones"),
    CategoryConfig("ninos/nino/camisetas-y-polos", "niño", "ropa", "camisetas"),
    CategoryConfig("ninos/nino/camisas", "niño", "ropa", "camisetas"),
    CategoryConfig("ninos/nino/sudaderas", "niño", "ropa", "sudaderas"),
    CategoryConfig("ninos/nino/punto-y-jerseis", "niño", "ropa", "sudaderas"),
    CategoryConfig("ninos/nino/pijamas-y-calcetines", "niño", "ropa", "ropa-interior"),
    CategoryConfig("ninos/nino/zapatos", "niño", "zapateria", "zapatos"),
]


def _decimal(value: Any) -> Decimal | None:
    """Convierte un precio (float/int/str) a Decimal exacto vía str; None si no hay valor."""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _normalize_size(value: Any) -> str | None:
    """Normaliza la talla: `valueMain` viene a veces duplicada ("4-5 años/4-5 años")."""
    if not value:
        return None
    parts: list[str] = []
    for part in str(value).split("/"):
        p = part.strip()
        if p and p not in parts:
            parts.append(p)
    return "/".join(parts) or None


def _variant_prices(variant: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    """(precio_actual, precio_original). Si hay `sale_price`, `price` es el tachado."""
    price = _decimal(variant.get("price"))
    sale = _decimal(variant.get("sale_price"))
    if sale is not None:
        return sale, price  # actual = rebajado; original = precio tachado
    return price, None


def _usable_image(url: Any) -> bool:
    """¿Es una URL de foto aprovechable? Descarta el marcador `no-image.png` de la tienda."""
    return isinstance(url, str) and url.startswith("http") and "no-image" not in url


def _primary_image(product: dict[str, Any]) -> str | None:
    """URL de la foto principal: la que la propia tienda elige para su tarjeta.

    Viene en `image.sources` (`big` = 516x640, ~16 KB) sobre el CDN de El Corte Inglés. Ojo
    con dos cosas: `default_image` NO vale —es el marcador `no-image.png` de la tienda, y
    para eso preferimos nuestro placeholder—, y ese CDN **ignora** el `&w=` que sí acepta
    Zara (el tamaño va en `impolicy=Resize&width=...`), así que el ancho que se guarda aquí
    es el definitivo. Por eso se prefiere `big`, que es el que encaja con la tarjeta.
    """
    image = product.get("image")
    if isinstance(image, dict):
        sources = image.get("sources")
        if isinstance(sources, dict):
            for key in ("big", "medium", "small"):
                if _usable_image(sources.get(key)):
                    return str(sources[key])
        if _usable_image(image.get("default_source")):
            return str(image["default_source"])
    # Respaldo: la foto del primer color visible (en todo lo observado es la misma URL).
    for color in product.get("_my_colors", []):
        if color.get("hideColor"):
            continue
        if _usable_image(color.get("image")):
            return str(color["image"])
    return None


def _product_url(product: dict[str, Any]) -> str | None:
    """URL absoluta del producto a partir de `_canonical` (absoluta) o `_uri` (relativa)."""
    canonical = product.get("_canonical")
    if isinstance(canonical, str) and canonical.startswith("http"):
        return canonical
    uri = product.get("_uri")
    if isinstance(uri, str) and uri:
        return "https://www.sfera.com" + uri if uri.startswith("/") else uri
    return None


def products_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae `data.products` de una respuesta firefly (lista vacía si falta/!success)."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    products = data.get("products")
    return products if isinstance(products, list) else []


def pagination_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Extrae `data.pagination` de una respuesta firefly (dict vacío si falta)."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    pag = data.get("pagination")
    return pag if isinstance(pag, dict) else {}


def parse_products(products: list[dict[str, Any]], cat: CategoryConfig) -> list[ScrapedProduct]:
    """Convierte la lista de productos firefly en ScrapedProduct (ignora los sin variantes)."""
    out: list[ScrapedProduct] = []
    for product in products:
        pid = product.get("id")
        if not pid:
            continue
        url = _product_url(product)
        variants: list[ScrapedVariant] = []
        for color in product.get("_my_colors", []):
            if color.get("hideColor"):
                continue  # color oculto por la tienda: no lo registramos
            color_name = color.get("title")
            for variant in color.get("variants", []):
                price, list_price = _variant_prices(variant)
                if price is None:
                    continue  # sin precio utilizable: no la registramos
                vid = variant.get("sku") or variant.get("id")
                if vid is None:
                    continue
                inner = variant.get("variant") or {}
                variants.append(
                    ScrapedVariant(
                        retailer_variant_id=str(vid),
                        size=_normalize_size(inner.get("valueMain") or inner.get("value")),
                        color=color_name,
                        sku=str(variant["sku"]) if variant.get("sku") is not None else None,
                        price=price,
                        list_price=list_price,
                        in_stock=variant.get("status") == "ADD",
                        url=url,
                    )
                )
        if not variants:
            continue
        out.append(
            ScrapedProduct(
                retailer_product_id=str(pid),
                name=product.get("title", ""),
                gender=cat.gender,
                section=cat.section,
                category=cat.category,
                url=url,
                variants=variants,
                image_url=_primary_image(product),
            )
        )
    return out


def stock_lists_available(payload: dict[str, Any], product_id: str) -> bool:
    """¿El endpoint de stock declara el producto comprable ahora mismo (`data.ADD`)?

    Es una prueba POSITIVA: si está en `ADD`, sigue a la venta. Lo contrario no prueba nada,
    porque un producto agotado pero vivo también sale de `ADD` (`status: not_available`).
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    add = data.get("ADD")
    return isinstance(add, list) and product_id in add


def product_signature(product: ScrapedProduct) -> str:
    """Huella barata del producto: precio efectivo por variante (ordenada y estable)."""
    return "|".join(sorted(f"{v.retailer_variant_id}:{v.price}" for v in product.variants))


class SferaStore:
    """Scraper de Sfera (vía navegador headless). Implementa el Protocol BaseStore."""

    slug = "sfera"
    name = "Sfera"
    base_url = BASE_URL

    def __init__(
        self,
        config: Config,
        categories: list[CategoryConfig] | None = None,
        session_factory: Callable[[], BrowserSession] | None = None,
    ) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        # Costura para los tests: por defecto abre un Chromium real.
        self._session_factory = session_factory or (lambda: BrowserSession(config))
        self._cache: dict[str, ScrapedProduct] = {}  # rellenado por list_catalog()

    def scopes(self) -> Iterable[ScrapeScope]:
        seen: list[ScrapeScope] = []
        for cat in self._categories:
            scope = ScrapeScope(cat.gender, cat.section, cat.category)
            if scope not in seen:
                seen.append(scope)
        return seen

    def _iter_category(
        self, session: BrowserSession, cat: CategoryConfig
    ) -> Iterable[ScrapedProduct]:
        """Recorre todas las páginas firefly de una categoría y produce sus productos."""
        session.goto(_SEED_URL.format(category_path=cat.category_path))  # siembra cookies
        page = 1
        total = 1
        while page <= total and page <= _MAX_PAGES:
            payload = session.get_json(
                _FIREFLY_URL.format(category_path=cat.category_path, page=page)
            )
            if page == 1:
                total = int(pagination_of(payload).get("_total", 1) or 1)
            products = parse_products(products_of(payload), cat)
            if not products:
                break  # página vacía: no seguimos (la red de seguridad de bajas lo cubre)
            yield from products
            page += 1

    def list_catalog(self) -> Iterable[ListingEntry]:
        self._cache = {}
        with self._session_factory() as session:
            for cat in self._categories:
                for product in self._iter_category(session, cat):
                    pid = product.retailer_product_id
                    if pid in self._cache:
                        continue  # dedup entre categorías dentro de la misma ejecución
                    self._cache[pid] = product
                    yield ListingEntry(
                        retailer_product_id=pid,
                        signature=product_signature(product),
                        gender=product.gender,
                        section=product.section,
                        category=product.category,
                    )

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        # El listado ya trajo el detalle: se sirve desde caché (sin peticiones extra).
        for entry in entries:
            product = self._cache.get(entry.retailer_product_id)
            if product is not None:
                yield product

    def _seed_url(self) -> str:
        """Página de documento con la que sembrar cookies antes de tocar las APIs."""
        if not self._categories:
            return BASE_URL
        return _SEED_URL.format(category_path=self._categories[0].category_path)

    def _probe_one(self, session: BrowserSession, candidate: DelistCandidate) -> bool | None:
        """¿Sigue a la venta? True/False; None si no hay respuesta utilizable."""
        pid = candidate.retailer_product_id
        try:
            payload = session.get_json(_STOCK_URL.format(product_id=pid))
            if isinstance(payload, dict) and stock_lists_available(payload, pid):
                return True  # comprable: vivo seguro, y sin gastar una navegación
        except Exception:
            pass  # sin atajo: lo resuelve la PDP

        # Agotado y retirado se parecen en el stock, pero no en la PDP: el id retirado da 404.
        if not candidate.url:
            return None
        try:
            status = session.goto(candidate.url)
        except Exception:  # timeout / error de navegación: no es prueba de nada
            return None
        if status in (404, 410):
            return False
        # 200 = la ficha existe (aunque esté agotada). Otros códigos (403 de Akamai, 5xx)
        # son problema nuestro, no del producto: sin veredicto.
        return True if status == 200 else None

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`)."""
        pending = list(candidates)
        if not pending:
            return {}
        verdicts: dict[str, bool] = {}
        with self._session_factory() as session:
            session.goto(self._seed_url())  # siembra las cookies de Akamai del origen
            for candidate in pending:
                verdict = self._probe_one(session, candidate)
                if verdict is not None:  # sin veredicto -> se omite del mapa
                    verdicts[candidate.retailer_product_id] = verdict
        return verdicts
