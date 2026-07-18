"""Scraper de Zara (niños): calzado y ropa infantil.

Zara expone endpoints AJAX públicos que devuelven JSON, así que evitamos
navegador (imagen ligera). Tres endpoints:

  - árbol de categorías:  /categories?ajax=true
  - listado de categoría: /category/{id}/products?ajax=true   (ids de producto)
  - detalle (con tallas):  /products-details?productIds={ids}&ajax=true

El id estable del producto es `seo.discernProductId` (independiente de temporada);
la variante estable es `{productId}-{colorId}-{sizeId}`.

Las funciones `parse_*` son puras (JSON -> dataclasses) y se testean con fixtures;
`ZaraStore.discover()` es la única parte que toca la red.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from ..config import Config
from .base import ScrapedProduct, ScrapedVariant

BASE_URL = "https://www.zara.com/es/es/"
_CATEGORY_URL = BASE_URL + "category/{cat_id}/products?ajax=true"
_DETAILS_URL = BASE_URL + "products-details?productIds={ids}&ajax=true"
# El endpoint de detalle solo devuelve datos para UN productId por llamada
# (con varios ids separados por coma responde una lista vacía).
_DETAILS_BATCH = 1


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una categoría hoja de Zara a nuestro dominio (género/sección/categoría)."""

    category_id: int
    gender: str  # niño | niña | unisex
    section: str  # ropa | zapateria
    category: str  # zapatos | zapatillas | pantalones | ...


# Subconjunto curado para la Fase 1: calzado infantil niño/niña.
# Ampliable añadiendo entradas (el resto del código no cambia).
CATEGORIES: list[CategoryConfig] = [
    CategoryConfig(2427610, "niña", "zapateria", "zapatos"),
    CategoryConfig(2427608, "niña", "zapateria", "zapatillas"),
    CategoryConfig(2428560, "niño", "zapateria", "zapatos"),
    CategoryConfig(2428558, "niño", "zapateria", "zapatillas"),
]


def _cents(value: Any) -> Decimal | None:
    """Zara da los precios en céntimos enteros (3995 -> 39.95 €)."""
    if value is None:
        return None
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def parse_listing_ids(listing: dict[str, Any]) -> list[str]:
    """Extrae los `discernProductId` estables de un listado, en orden y sin duplicar."""
    ids: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            seo = node.get("seo")
            if isinstance(seo, dict) and seo.get("discernProductId"):
                pid = str(seo["discernProductId"])
                if pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(listing)
    return ids


def parse_detail_product(entry: dict[str, Any], cat: CategoryConfig) -> ScrapedProduct | None:
    """Convierte una entrada de `products-details` en ScrapedProduct (None si no hay variantes)."""
    seo = entry.get("seo") or {}
    pid = seo.get("discernProductId")
    if not pid:
        return None
    pid = str(pid)
    keyword = seo.get("keyword", "")
    seo_pid = seo.get("seoProductId", "")
    url = f"{BASE_URL}{keyword}-p{seo_pid}.html" if keyword and seo_pid else None

    variants: list[ScrapedVariant] = []
    for color in entry.get("detail", {}).get("colors", []):
        color_id = color.get("id")
        color_name = color.get("name")
        color_old = color.get("oldPrice")
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

    if not variants:
        return None
    return ScrapedProduct(
        retailer_product_id=pid,
        name=entry.get("name", ""),
        gender=cat.gender,
        section=cat.section,
        category=cat.category,
        url=url,
        variants=variants,
    )


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ZaraStore:
    """Scraper de Zara. Implementa el Protocol BaseStore."""

    slug = "zara"
    name = "Zara"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._config.user_agent, "Accept": "application/json"},
            timeout=self._config.request_timeout,
            follow_redirects=True,
        )

    def _get_json(self, client: httpx.Client, url: str) -> Any:
        if self._config.request_delay > 0:
            time.sleep(self._config.request_delay)
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()

    def discover(self) -> Iterable[ScrapedProduct]:
        emitted: set[str] = set()  # dedup entre categorías dentro de la misma ejecución
        with self._client() as client:
            for cat in self._categories:
                listing = self._get_json(client, _CATEGORY_URL.format(cat_id=cat.category_id))
                ids = [pid for pid in parse_listing_ids(listing) if pid not in emitted]
                for batch in _chunked(ids, _DETAILS_BATCH):
                    details = self._get_json(client, _DETAILS_URL.format(ids=",".join(batch)))
                    for entry in details:
                        product = parse_detail_product(entry, cat)
                        if product and product.retailer_product_id not in emitted:
                            emitted.add(product.retailer_product_id)
                            yield product
