"""Contrato común para los scrapers por tienda (pluggable).

El scrapeo se hace en **dos fases** para poder ahorrar peticiones (detalle condicional):

  1. `list_catalog()` — barre las secciones y devuelve una `ListingEntry` por producto
     con una *huella* (`signature`) barata construida con lo que se ve en el listado
     (típicamente precio por color). Son pocas peticiones.
  2. `fetch_details(entries)` — pide el detalle completo (tallas, stock, sku) SOLO de los
     productos que la ingesta decide (nuevos o con la huella cambiada).

Así la ingesta (`ingest.py`) compara la huella contra la última conocida en BD y evita
la petición de detalle cuando nada ha cambiado. Los scrapers no tocan la BD: solo la red.

Opcionalmente una tienda puede implementar `SupportsAliveProbe` (confirmación activa antes
de dar de baja); las que no puedan lo omiten y la ingesta se queda con la histéresis.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ScrapedImage:
    """Una foto del producto, atribuida al color que retrata.

    `color` debe salir del MISMO campo que alimenta `ScrapedVariant.color`: es la clave por la
    que la ficha empareja foto y precio, y si los dos nombres se desalinean el emparejamiento
    falla en silencio. Un color sin variantes utilizables no debe aportar fotos.

    **El orden de la lista es el orden de la galería**; la posición dentro de cada color la
    asigna la ingesta. No es un detalle cosmético: una tienda puede exponer dos colores distintos
    con el MISMO nombre (visto en Lefties, dos "MARRON" con ids distintos), y si cada scraper
    numerase por su cuenta las dos series arrancarían en 0 y chocarían. Numerando en un único
    sitio, por nombre de color, ese caso se resuelve solo — que es además lo que la ficha quiere,
    porque agrupa por nombre y para el usuario esos dos marrones son el mismo color.
    """

    color: str | None  # None = foto que no se puede atribuir a un color concreto
    url: str


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
    # Foto primaria (la del primer color). Opcional: hay tiendas que no la exponen en lo que ya
    # pedimos, y una ficha sin foto vale más que una petición extra por producto.
    image_url: str | None = None
    # Galería completa, agrupada por color. Opcional igual que `image_url`: una tienda que no sepa
    # darla sigue siendo válida. Vacía NO significa "este producto no tiene fotos", significa "esta
    # pasada no trae información de fotos" — por eso la ingesta no borra la galería previa con ella.
    images: list[ScrapedImage] = field(default_factory=list)


@dataclass(frozen=True)
class ScrapeScope:
    """Un ámbito del catálogo que el scraper recorre (p.ej. niña/zapateria/zapatos).

    Es la unidad sobre la que se acota la detección de bajas: solo se dan de baja
    productos de ámbitos realmente escaneados en la pasada (ver `ingest.py`).
    """

    gender: str | None
    section: str | None
    category: str | None


@dataclass(frozen=True)
class ListingEntry:
    """Producto tal y como aparece en el listado, con una huella para detectar cambios."""

    retailer_product_id: str
    signature: str  # huella barata (p.ej. precio por color); si no cambia, no pedimos detalle
    gender: str | None
    section: str | None
    category: str | None

    @property
    def scope(self) -> ScrapeScope:
        return ScrapeScope(self.gender, self.section, self.category)


@dataclass(frozen=True)
class DelistCandidate:
    """Producto que la ingesta está a punto de dar de baja y quiere confirmar antes."""

    retailer_product_id: str
    url: str | None  # `product.url` en BD: hay tiendas que solo pueden sondear por URL


@runtime_checkable
class BaseStore(Protocol):
    """Interfaz que implementa el scraper de cada tienda."""

    slug: str
    name: str
    base_url: str

    def scopes(self) -> Iterable[ScrapeScope]:
        """Ámbitos del catálogo que este scraper recorre (base para acotar las bajas)."""
        ...

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Barre las secciones relevantes (pocas peticiones) y devuelve una entrada por producto."""
        ...

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Pide el detalle completo (tallas/precio/stock) de los productos indicados."""
        ...


@runtime_checkable
class SupportsAliveProbe(Protocol):
    """Capacidad OPCIONAL: confirmar de forma activa si un producto sigue a la venta.

    Las bajas se detectan por ausencia en el listado, que es una señal indirecta (un bloqueo
    o una reestructura de categorías la falsean). Si la tienda permite preguntar por un
    producto concreto, la ingesta la usa como veredicto final antes de descatalogar.
    """

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Sondea los candidatos: retailer_product_id -> sigue a la venta.

        Tres estados con dos valores: `True` (vivo), `False` (retirado) y **ausente del
        mapa** = no concluyente (fallo de red, bloqueo, respuesta ambigua). La ingesta es
        conservadora: solo da de baja lo confirmado como retirado.
        """
        ...
