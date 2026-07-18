"""Contrato común para los scrapers por tienda (pluggable).

Cada tienda implementa `BaseStore.discover()` y devuelve `ScrapedProduct`s
normalizados. La ingesta (`ingest.py`) no sabe nada de la web concreta:
solo consume estos dataclasses, de modo que el resto del sistema queda
desacoplado de las particularidades (y los bloqueos) de cada tienda.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ScrapedVariant:
    """Una variante concreta (talla/color) con su precio en el momento del scrape."""

    retailer_variant_id: str  # estable por tienda, independiente de temporada
    size: str | None
    color: str | None
    sku: str | None
    price: Decimal
    list_price: Decimal | None  # precio original/tachado, si la tienda lo expone
    in_stock: bool
    url: str | None = None


@dataclass(frozen=True)
class ScrapedProduct:
    """Un producto/modelo con todas sus variantes talla/color."""

    retailer_product_id: str  # identificador único y estable por tienda
    name: str
    gender: str | None  # niño | niña | unisex
    section: str | None  # ropa | zapateria
    category: str | None  # pantalones | camisetas | zapatos | ...
    url: str | None
    variants: list[ScrapedVariant]


@runtime_checkable
class BaseStore(Protocol):
    """Interfaz que implementa el scraper de cada tienda."""

    slug: str
    name: str
    base_url: str

    def discover(self) -> Iterable[ScrapedProduct]:
        """Recorre las secciones relevantes y produce los productos con sus variantes."""
        ...
