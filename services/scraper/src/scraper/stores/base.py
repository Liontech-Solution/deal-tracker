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
de dar de baja); las que no puedan lo omiten y la ingesta se queda con la histéresis. Y
`SupportsScanReport` para contar las hojas que se cayeron durante el listado sin abortar la
pasada (ver `ScanReport`).
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
    # Mínimo de los últimos 30 días **declarado por la tienda** (directiva Ómnibus). No es una
    # observación nuestra: es lo que el retailer publica, y ahí está su valor — permite contrastar
    # nuestro histórico contra el suyo en vez de tener que creernos el precio tachado. `None` = esta
    # tienda no lo declara (hoy solo lo hace C&A), que NO es lo mismo que "no hubo mínimo".
    retailer_min_30d: Decimal | None = None


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
    # Calzado respetuoso: 'si' | 'no' | 'desconocido', y None cuando no aplica (ropa). Lo decide
    # `scraper.barefoot.classify()` con lo que cada tienda sepa dar — su propia categoría barefoot
    # si la tiene, y si no la heurística de texto. Ver `0012_add_barefoot.sql`.
    barefoot: str | None = None
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


# Los únicos códigos que valen como "esta hoja ya no existe" y por tanto se toleran. Un 403 es un
# bloqueo y un 5xx un fallo del servidor (que además ya se reintenta con backoff): tragárselos
# convertiría un problema transitorio en un catálogo mutilado que se da por bueno.
GONE_STATUS = frozenset({404, 410})


@dataclass
class ScanReport:
    """Qué hojas del catálogo se pudieron listar en esta pasada y cuáles no.

    Una hoja retirada (404) no debe tumbar la pasada entera —lo hacía: `2428332` de Zara empezó
    a dar 404 cuatro días después de verificarla viva y las otras 46 hojas se quedaron sin
    ingerir, de forma determinista y silenciosa—, pero tampoco puede pasar inadvertida:

    - **Su ámbito deja de ser seguro para dar bajas.** Lo que no se ve en un ámbito cuya hoja no
      se ha podido mirar no está retirado: es que no se ha mirado. Y la red de seguridad por
      umbral no lo cubre, porque un ámbito alimentado por seis hojas solo pierde un 17 % de lo
      observado al caerse una — muy por debajo del 50 % que dispara la sospecha.
    - **La proporción importa.** Una hoja de 47 es una categoría que la tienda ha retirado; la
      mitad de ellas es un bloqueo o un cambio de API, y ahí la pasada sí debe abortar en vez de
      guardar un catálogo mutilado.
    """

    leaves_total: int = 0
    leaves_failed: int = 0
    failed_scopes: set[ScrapeScope] = field(default_factory=set)

    def leaf_ok(self) -> None:
        """Registra una hoja listada con éxito."""
        self.leaves_total += 1

    def leaf_gone(self, scope: ScrapeScope) -> None:
        """Registra una hoja que la tienda ya no sirve; su ámbito queda fuera de las bajas."""
        self.leaves_total += 1
        self.leaves_failed += 1
        self.failed_scopes.add(scope)

    @property
    def dead_ratio(self) -> float:
        """Proporción de hojas caídas (0.0 si no se recorrió ninguna)."""
        return self.leaves_failed / self.leaves_total if self.leaves_total else 0.0


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


@dataclass(frozen=True)
class LeafHealth:
    """Veredicto sobre UNA hoja de categoría, para el chequeo preventivo de `--check-categories`."""

    scope: ScrapeScope
    leaf: str  # el identificador de la hoja tal y como lo escribe la tienda (id, uuid, ruta)
    alive: bool | None  # True viva, False retirada, None sin veredicto (fallo nuestro)
    detail: str = ""  # qué respondió, para poder actuar sin repetir la petición a mano


@runtime_checkable
class SupportsLeafHealth(Protocol):
    """Capacidad OPCIONAL: sondear las hojas de categoría SIN ingerir nada.

    Existe para enterarse de que un id ha caducado **antes** de que lo note el usuario: la pasada
    ya tolera la hoja muerta, pero mientras tanto esa categoría no se ingiere, y sin esto solo se
    descubre leyendo el resumen de un job que nadie mira. Pensado para ejecutarse a mano o desde
    un CronJob de vigilancia, no en cada pasada.
    """

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Un veredicto por hoja configurada, en el orden en que están declaradas."""
        ...


@dataclass(frozen=True)
class CategoryNode:
    """Una categoría del árbol **tal y como lo publica la tienda**, no como lo mapeamos nosotros.

    Es la respuesta a «qué hojas existen de verdad», que es distinta de «qué hojas ingerimos»
    (`CATEGORIES`) y de «siguen vivas las que ingerimos» (`LeafHealth`). Existe porque adivinar
    rutas copiándolas de otra rama es exactamente como se llega a una hoja que no existe, y hay
    tiendas donde eso no da 404 (Sfera devuelve el catálogo del padre, #54).
    """

    # Identificador en el vocabulario de la tienda, el mismo que `LeafHealth.leaf`.
    path: str
    title: str  # cómo la llama la tienda de cara al usuario
    # Productos que la tienda declara. `None` = no lo dice, que NO es lo mismo que 0.
    count: int | None
    depth: int  # niveles por debajo de la raíz pedida (1 = hija directa)
    has_children: bool


@runtime_checkable
class SupportsCategoryTree(Protocol):
    """Capacidad OPCIONAL: enumerar el árbol de categorías que la tienda publica.

    Es la herramienta de reconocimiento para decidir **cobertura**: qué hay ahí fuera que no
    estemos ingiriendo. No participa en la pasada ni en las bajas; se ejecuta a mano.
    """

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Categorías publicadas por debajo de `root`, de arriba abajo.

        Solo descendientes de `root`: una hoja sin descendencia devuelve **lista vacía**, que es
        la respuesta honesta a «qué cuelga de aquí».
        """
        ...

    def mapped_leaves(self) -> Iterable[str]:
        """Hojas configuradas hoy, en el mismo vocabulario que `CategoryNode.path`.

        Sirve para cruzar el árbol publicado con lo que ingerimos y que el informe pueda decir
        qué falta, que es la única pregunta que justifica pedir el árbol.
        """
        ...


@runtime_checkable
class SupportsScanReport(Protocol):
    """Capacidad OPCIONAL: contar las hojas que se cayeron durante `list_catalog()`.

    La implementan las tiendas que recorren hojas de categoría independientes y saben seguir
    cuando una desaparece. Una tienda que no la implemente se comporta como siempre: cualquier
    error de listado propaga y aborta la pasada.
    """

    def scan_report(self) -> ScanReport:
        """Informe del último recorrido.

        **Solo es válido con `list_catalog()` consumido entero**: es un generador, así que las
        hojas se recorren a medida que la ingesta tira de él. Quien lo consuma a medias verá un
        informe a medias.
        """
        ...
