"""Contrato común para los scrapers por tienda (pluggable).

El scrapeo se hace en **dos fases** para poder ahorrar peticiones (detalle condicional):

  1. `list_catalog()` — barre las secciones y devuelve una `ListingEntry` por producto
     con una *huella* (`signature`) barata construida con lo que se ve en el listado
     (típicamente precio por color). Son pocas peticiones.
  2. `fetch_details(entries)` — pide el detalle completo (tallas, stock, sku) SOLO de los
     productos que la ingesta decide (nuevos o con la huella cambiada).

Así la ingesta (`ingest.py`) compara la huella contra la última conocida en BD y evita
la petición de detalle cuando nada ha cambiado. Los scrapers no tocan la BD: solo la red.
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


@dataclass(frozen=True)
class ListingEntry:
    """Producto tal y como aparece en el listado, con una huella para detectar cambios."""

    retailer_product_id: str
    signature: str  # huella barata (p.ej. precio por color); si no cambia, no pedimos detalle
    gender: str | None
    section: str | None
    category: str | None


@runtime_checkable
class BaseStore(Protocol):
    """Interfaz que implementa el scraper de cada tienda."""

    slug: str
    name: str
    base_url: str

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Barre las secciones relevantes (pocas peticiones) y devuelve una entrada por producto."""
        ...

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Pide el detalle completo (tallas/precio/stock) de los productos indicados."""
        ...
