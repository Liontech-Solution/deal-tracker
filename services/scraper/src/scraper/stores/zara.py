"""Scraper de Zara (niños): calzado y ropa infantil.

Zara expone endpoints AJAX públicos que devuelven JSON, así que evitamos
navegador (imagen ligera). Tres endpoints:

  - árbol de categorías:  /categories?ajax=true
  - listado de categoría: /category/{id}/products?ajax=true   (ids + precio por color)
  - detalle (con tallas):  /products-details?productIds={id}&ajax=true

El id estable del producto es `seo.discernProductId` (independiente de temporada);
la variante estable es `{productId}-{colorId}-{sizeId}`.

El scrapeo es en dos fases (ver `stores/base.py`): `list_catalog()` (barato) construye una
huella por producto con el precio por color del listado; `fetch_details()` solo pide el
detalle de los productos que la ingesta marca como nuevos o cambiados.

El mismo endpoint de detalle sirve de **confirmación activa** antes de dar de baja
(`probe_alive`): con un id que Zara ya no conoce responde 200 con una lista vacía.

Las funciones `parse_*` son puras (JSON -> dataclasses) y se testean con fixtures.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from ..config import Config
from .base import (
    DelistCandidate,
    ListingEntry,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)

BASE_URL = "https://www.zara.com/es/es/"
_CATEGORY_URL = BASE_URL + "category/{cat_id}/products?ajax=true"
# El endpoint de detalle solo devuelve datos para UN productId por llamada
# (con varios ids separados por coma responde una lista vacía).
_DETAILS_URL = BASE_URL + "products-details?productIds={product_id}&ajax=true"

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Tope de fotos que guardamos por color: Zara da hasta once y la cola son detalles de tejido que
# no aportan a una galería de catálogo.
_MAX_IMAGES_PER_COLOR = 8


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una categoría hoja de Zara a nuestro dominio (género/sección/categoría)."""

    category_id: int
    gender: str  # niño | niña | unisex
    section: str  # ropa | zapateria
    category: str  # zapatos | zapatillas | pantalones | ...


# Subconjunto curado (niño/niña): calzado + ropa infantil. Zara separa el catálogo en dos
# rangos de edad (6-14 años y "mini" 1½-6 años), cada uno con su propio id de categoría-hoja;
# se incluyen ambos para máxima cobertura. Varias hojas mapean al mismo slug de dominio (p.ej.
# jeans/leggings/pantalones -> "pantalones") para alinear el vocabulario con el de Sfera y que
# los filtros del web unifiquen tiendas. El dedup por id de `list_catalog()` evita duplicados
# cuando un producto aparece en dos hojas/rangos; la talla distingue el rango a nivel variante.
# Ampliable añadiendo entradas (el resto del código no cambia).
#
# `barefoot` es el slug que estrenó Lefties: el calzado respetuoso, que es el nicho del producto.
# Zara lo etiqueta en su propio árbol (`key` ...-ZAPATOS-BAREFOOT), así que aquí sale gratis, sin
# heurística sobre el nombre. Sus hojas cubren los DOS rangos de edad, mientras que el calzado
# genérico de abajo solo trae el de 6-14: de las 86 referencias barefoot medidas (27/07/2026),
# solo 8 aparecían en `zapatos`/`zapatillas` — las otras 78 no se estaban ingiriendo.
#
# EL ORDEN IMPORTA: un modelo puede colgar de dos hojas y `list_catalog()` deduplica por id,
# quedándose con la PRIMERA que lo ve. Las hojas barefoot van delante para que esos 8 solapados
# queden como `barefoot` y no como calzado genérico; barefoot es la señal que interesa conservar.
# (Mismo razonamiento y misma trampa que en `lefties.py`, donde el solape era casi total.)
#
# Nota: el hub NIÑOS > ACCESORIOS | ZAPATOS > CALZADO BAREFOOT (2597610) tiene sus propias hojas
# por género/edad (2630194, 2631201, 2630196, 2630195). Medidas: devuelven EXACTAMENTE las mismas
# 86 referencias que las cuatro de aquí, así que no se añaden — serían cuatro peticiones por
# pasada a cambio de nada.
CATEGORIES: list[CategoryConfig] = [
    # --- barefoot: primero a propósito (ver nota de orden arriba) ---
    CategoryConfig(2596605, "niña", "zapateria", "barefoot"),  # barefoot (6-14 años)
    CategoryConfig(2543431, "niña", "zapateria", "barefoot"),  # barefoot (mini)
    CategoryConfig(2595608, "niño", "zapateria", "barefoot"),  # barefoot (6-14 años)
    CategoryConfig(2543932, "niño", "zapateria", "barefoot"),  # barefoot (mini)
    # --- calzado (conservado desde la Fase 1) ---
    CategoryConfig(2427610, "niña", "zapateria", "zapatos"),
    CategoryConfig(2427608, "niña", "zapateria", "zapatillas"),
    CategoryConfig(2428560, "niño", "zapateria", "zapatos"),
    CategoryConfig(2428558, "niño", "zapateria", "zapatillas"),
    # --- ropa niña (6-14 años + mini 1½-6) ---
    CategoryConfig(2427327, "niña", "ropa", "pantalones"),  # pantalones
    CategoryConfig(2422199, "niña", "ropa", "pantalones"),  # pantalones (mini)
    CategoryConfig(2426261, "niña", "ropa", "pantalones"),  # jeans
    CategoryConfig(2422112, "niña", "ropa", "pantalones"),  # jeans (mini)
    CategoryConfig(2617641, "niña", "ropa", "pantalones"),  # leggings | chándal
    CategoryConfig(2422135, "niña", "ropa", "pantalones"),  # leggings | chándal (mini)
    CategoryConfig(2426140, "niña", "ropa", "camisetas"),  # camisetas
    CategoryConfig(2422028, "niña", "ropa", "camisetas"),  # camisetas (mini)
    CategoryConfig(2426126, "niña", "ropa", "camisetas"),  # blusas | camisas
    CategoryConfig(2421993, "niña", "ropa", "camisetas"),  # blusas | camisas (mini)
    CategoryConfig(2427482, "niña", "ropa", "sudaderas"),  # sudaderas
    CategoryConfig(2426321, "niña", "ropa", "sudaderas"),  # sudaderas (mini)
    CategoryConfig(2427411, "niña", "ropa", "sudaderas"),  # punto | crochet
    CategoryConfig(2422257, "niña", "ropa", "sudaderas"),  # punto | crochet (mini)
    CategoryConfig(2427560, "niña", "ropa", "vestidos"),  # vestidos | monos
    CategoryConfig(2426391, "niña", "ropa", "vestidos"),  # vestidos | monos (mini)
    CategoryConfig(2426231, "niña", "ropa", "vestidos"),  # faldas | bermudas
    CategoryConfig(2422071, "niña", "ropa", "vestidos"),  # faldas | bermudas (mini)
    CategoryConfig(2427367, "niña", "ropa", "ropa-interior"),  # pijamas
    CategoryConfig(2422216, "niña", "ropa", "ropa-interior"),  # pijamas (mini)
    CategoryConfig(2427530, "niña", "ropa", "ropa-interior"),  # ropa interior | calcetines
    CategoryConfig(2426369, "niña", "ropa", "ropa-interior"),  # ropa interior | calcetines (mini)
    # --- ropa niño (6-14 años + mini 1½-6) ---
    CategoryConfig(2428332, "niño", "ropa", "pantalones"),  # pantalones
    CategoryConfig(2427796, "niño", "ropa", "pantalones"),  # pantalones (mini)
    CategoryConfig(2426737, "niño", "ropa", "pantalones"),  # jeans
    CategoryConfig(2422737, "niño", "ropa", "pantalones"),  # jeans (mini)
    CategoryConfig(2426543, "niño", "ropa", "pantalones"),  # bermudas
    CategoryConfig(2422547, "niño", "ropa", "pantalones"),  # bermudas (mini)
    CategoryConfig(2426650, "niño", "ropa", "camisetas"),  # camisetas
    CategoryConfig(2422662, "niño", "ropa", "camisetas"),  # camisetas (mini)
    CategoryConfig(2426636, "niño", "ropa", "camisetas"),  # camisas | sobrecamisas
    CategoryConfig(2422647, "niño", "ropa", "camisetas"),  # camisas | sobrecamisas (mini)
    CategoryConfig(2427929, "niño", "ropa", "sudaderas"),  # sudaderas
    CategoryConfig(2428452, "niño", "ropa", "sudaderas"),  # sudaderas | punto (mini)
    CategoryConfig(2427882, "niño", "ropa", "sudaderas"),  # punto | crochet
    CategoryConfig(2428327, "niño", "ropa", "ropa-interior"),  # pijamas
    CategoryConfig(2427842, "niño", "ropa", "ropa-interior"),  # pijamas (mini)
    CategoryConfig(2428509, "niño", "ropa", "ropa-interior"),  # ropa interior | calcetines
    CategoryConfig(2427980, "niño", "ropa", "ropa-interior"),  # ropa interior | calcetines (mini)
]


def _cents(value: Any) -> Decimal | None:
    """Zara da los precios en céntimos enteros (3995 -> 39.95 €)."""
    if value is None:
        return None
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def _iter_product_nodes(listing: Any) -> Iterable[dict[str, Any]]:
    """Recorre el JSON del listado y produce cada nodo de producto (con `seo.discernProductId`)."""

    def walk(node: Any) -> Iterable[dict[str, Any]]:
        if isinstance(node, dict):
            seo = node.get("seo")
            if isinstance(seo, dict) and seo.get("discernProductId"):
                yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    yield from walk(listing)


def _listing_signature(node: dict[str, Any]) -> str:
    """Huella barata del producto en el listado: precio por color (ordenado y estable)."""
    parts = [
        f"{color.get('id')}:{color.get('price')}"
        for color in node.get("detail", {}).get("colors", [])
    ]
    return "|".join(sorted(parts))


def _color_image_urls(color: dict[str, Any]) -> list[str]:
    """URLs de las fotos de UN color del detalle, en el orden que las da la tienda.

    Solo están en el detalle: el `xmedia` que trae el listado viene vacío. Se prefiere
    `extraInfo.deliveryUrl` (jpg plano) al `url` hermano, que lleva la plantilla `&w={width}`;
    el ancho lo decide quien la pinta. Se recorta a `_MAX_IMAGES_PER_COLOR`: Zara llega a dar
    once por color (detalles de tejido, la prenda en percha...) y la cola no aporta.
    """
    urls: list[str] = []
    for media in color.get("xmedia") or []:
        if media.get("type") != "image":
            continue
        url = (media.get("extraInfo") or {}).get("deliveryUrl")
        if url:
            urls.append(str(url))
        if len(urls) == _MAX_IMAGES_PER_COLOR:
            break
    return urls


def parse_listing_entries(listing: dict[str, Any], cat: CategoryConfig) -> list[ListingEntry]:
    """Extrae una `ListingEntry` (id estable + huella) por producto, en orden y sin duplicar."""
    entries: list[ListingEntry] = []
    seen: set[str] = set()
    for node in _iter_product_nodes(listing):
        pid = str(node["seo"]["discernProductId"])
        if pid in seen:
            continue
        seen.add(pid)
        entries.append(
            ListingEntry(
                retailer_product_id=pid,
                signature=_listing_signature(node),
                gender=cat.gender,
                section=cat.section,
                category=cat.category,
            )
        )
    return entries


def parse_detail_product(
    entry: dict[str, Any], *, gender: str | None, section: str | None, category: str | None
) -> ScrapedProduct | None:
    """Convierte una entrada de `products-details` en ScrapedProduct (None si no hay variantes)."""
    seo = entry.get("seo") or {}
    pid = seo.get("discernProductId")
    if not pid:
        return None
    pid = str(pid)
    keyword = seo.get("keyword", "")
    seo_pid = seo.get("seoProductId", "")
    url = f"{BASE_URL}{keyword}-p{seo_pid}.html" if keyword and seo_pid else None

    # Variantes e imágenes se construyen en la MISMA pasada por `colors`, leyendo el nombre del
    # color de un único sitio (`color["name"]`): es la clave con la que la ficha empareja la foto
    # con el precio, y sacarla en dos recorridos distintos es justo como se desalinean.
    variants: list[ScrapedVariant] = []
    images: list[ScrapedImage] = []
    for color in entry.get("detail", {}).get("colors", []):
        color_id = color.get("id")
        color_name = color.get("name")
        color_old = color.get("oldPrice")
        variants_before = len(variants)
        for size in color.get("sizes", []):
            price = _cents(size.get("price"))
            if price is None:
                continue  # priceUnavailable: no la registramos
            size_id = size.get("id")
            variants.append(
                ScrapedVariant(
                    retailer_variant_id=f"{pid}-{color_id}-{size_id}",
                    size=size.get("name"),
                    color=color_name,
                    sku=str(size["sku"]) if size.get("sku") is not None else None,
                    price=price,
                    list_price=_cents(size.get("oldPrice") or color_old),
                    in_stock=size.get("availability") == "in_stock",
                    url=url,
                )
            )
        if len(variants) == variants_before:
            continue  # color sin ninguna talla con precio: sus fotos quedarían huérfanas
        images.extend(ScrapedImage(color=color_name, url=u) for u in _color_image_urls(color))

    if not variants:
        return None
    return ScrapedProduct(
        retailer_product_id=pid,
        name=entry.get("name", ""),
        gender=gender,
        section=section,
        category=category,
        url=url,
        variants=variants,
        # La foto de tarjeta sale de la propia galería, para no tener dos fuentes de verdad.
        image_url=images[0].url if images else None,
        images=images,
    )


class ZaraStore:
    """Scraper de Zara. Implementa el Protocol BaseStore."""

    slug = "zara"
    name = "Zara"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES

    def scopes(self) -> Iterable[ScrapeScope]:
        # Ámbitos que se recorren, deducidos de las categorías configuradas (sin duplicar).
        seen: list[ScrapeScope] = []
        for cat in self._categories:
            scope = ScrapeScope(cat.gender, cat.section, cat.category)
            if scope not in seen:
                seen.append(scope)
        return seen

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

    def list_catalog(self) -> Iterable[ListingEntry]:
        emitted: set[str] = set()  # dedup entre categorías dentro de la misma ejecución
        with self._client() as client:
            for cat in self._categories:
                listing = self._get_json(client, _CATEGORY_URL.format(cat_id=cat.category_id))
                for entry in parse_listing_entries(listing, cat):
                    if entry.retailer_product_id not in emitted:
                        emitted.add(entry.retailer_product_id)
                        yield entry

    def _probe_one(self, client: httpx.Client, product_id: str) -> bool | None:
        """¿Sigue a la venta? True/False; None si la tienda no da una respuesta utilizable."""
        url = _DETAILS_URL.format(product_id=product_id)
        try:
            detail = self._get_json(client, url)
        except httpx.HTTPStatusError as exc:
            # 404 es un veredicto ("ese producto ya no existe"); el resto, tras agotar los
            # reintentos, es un fallo nuestro: no vale como prueba de retirada.
            return False if exc.response.status_code == 404 else None
        except (httpx.TransportError, ValueError):  # red caída o respuesta no-JSON
            return None
        if not isinstance(detail, list):
            return None  # forma inesperada: no arriesgamos una baja con esto
        return bool(detail)  # lista vacía = Zara ya no conoce ese id

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`): un GET por candidato."""
        verdicts: dict[str, bool] = {}
        with self._client() as client:
            for candidate in candidates:
                verdict = self._probe_one(client, candidate.retailer_product_id)
                if verdict is not None:  # sin veredicto -> se omite del mapa
                    verdicts[candidate.retailer_product_id] = verdict
        return verdicts

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        with self._client() as client:
            for entry in entries:
                url = _DETAILS_URL.format(product_id=entry.retailer_product_id)
                for detail in self._get_json(client, url):
                    product = parse_detail_product(
                        detail,
                        gender=entry.gender,
                        section=entry.section,
                        category=entry.category,
                    )
                    if product:
                        yield product
