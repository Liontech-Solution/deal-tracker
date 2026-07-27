"""Scraper de Lefties (niños): ropa y calzado infantil.

Lefties es Inditex, igual que Zara, y su API es la familia `itxrest` — pero a diferencia de Zara
**está tras Akamai**: un cliente HTTP plano recibe 403 aunque mande cabeceras de navegador
completas. Por eso todo va por el **navegador headless** (`stores/browser.py`), como Sfera.

Cuatro endpoints, y el orden importa:

  1. menú:     /api/storefront/1/stores/{store}/menu?catalogId={catalog}&typeCatalog=1&...
     Da el árbol de categorías. La clave está en que cada hoja trae `content.id`, un **uuid**:
     es lo que pide el listado. Los `/{category}/product` que uno supone por analogía con Zara
     dan todos 404 — el id numérico de categoría NO sirve para listar.
  2. listado:  /api/storefront/1/stores/{store}/grids/{uuid}?...
     Devuelve la categoría **entera y sin paginar** (medido: 227 productos en camisetas de niña),
     así que es UNA petición por hoja. `components` es un dict, y cada entrada es un **color**,
     no un modelo: los agrupa `identifier.productParentId`, que es el id estable del modelo.
     Trae precio por color, pero `sizes` viene vacío -> hace falta detalle.
  3. detalle:  /itxrest/3/catalog/store/{store}/{catalog}/productsArray?productIds={ids}&...
     El catalogId va **en la ruta** (omitirlo da 404) y acepta **varios ids por llamada**, cosa
     que Zara no permite. La forma es casi la de Zara: `detail.colors[].sizes[]`.
  4. bajas:    el mismo `productsArray` con un id que ya no existe responde 200 con
     `_ERR_PRODUCT_NOT_FOUND` — veredicto limpio para la confirmación activa.

Dos trampas que costaron el recon y conviene no volver a pisar:

- **El stock NO es `isBuyable`**, que viene `true` siempre. Es `visibilityValue`: `SHOW` =
  disponible, `HIDDEN` = agotada. Y con `allowWithoutStock=false` el endpoint **omite** las tallas
  agotadas, así que se pide `true`: si no, un producto agotado del todo parecería una baja.
- **`price` llega como string** de céntimos ("1799"), no como int, al revés que Zara.

Id estable de producto: `identifier.productParentId` (= `id` del detalle). Id estable de variante:
`{productId}-{colorId}-{sku}`. Las funciones `parse_*` son puras y se testean con fixtures reales.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    DelistCandidate,
    ListingEntry,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)
from .browser import BrowserSession

SLUG = "lefties"  # a nivel de módulo porque las funciones puras de parseo también lo necesitan
BASE_URL = "https://www.lefties.com/es/"
_ROOT = "https://www.lefties.com"

# Constantes de la tienda española (salen del propio tráfico de la web).
STORE_ID = "94009000"
CATALOG_ID = "90009053"
_LANG = "languageId=-5&appId=1"

_MENU_URL = (
    f"{_ROOT}/api/storefront/1/stores/{STORE_ID}/menu"
    f"?catalogId={CATALOG_ID}&typeCatalog=1&language=es-ES&appId=1"
)
_GRID_URL = f"{_ROOT}/api/storefront/1/stores/{STORE_ID}/grids/{{grid_id}}?{_LANG}"
# `allowWithoutStock=true` es deliberado: sin él la tienda oculta las tallas agotadas y no se
# podría registrar el stock por talla (ni distinguir "agotado" de "retirado").
_DETAILS_URL = (
    f"{_ROOT}/itxrest/3/catalog/store/{STORE_ID}/{CATALOG_ID}/productsArray"
    f"?productIds={{ids}}&languageId=-5&allowWithoutStock=true&appId=1"
)

# Ids por llamada al detalle. La tienda acepta varios; se trocea para no montar URLs enormes.
_DETAIL_BATCH = 20

# Tope de fotos por color (mismo criterio que Zara y Sfera).
_MAX_IMAGES_PER_COLOR = 8

# Sufijo de referencia que `productUrl` arrastra ("bailarina-t-l13210890") y que la URL canónica
# no lleva. La web enruta por el `-c{cat}p{id}.html`, así que el slug es puramente cosmético.
_SLUG_SUFFIX = re.compile(r"-l\d+$")


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una categoría-hoja de Lefties a nuestro dominio (género/sección/categoría).

    `category_id` es el id numérico del menú: estable y legible. El uuid del grid que hace falta
    para listar se resuelve en ejecución desde el menú, porque es un identificador de contenido
    y no hay garantía de que sobreviva a un rediseño de la home de categoría.
    """

    category_id: int
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # pantalones | camisetas | sudaderas | vestidos | ropa-interior | zapatos | ...


# Subconjunto curado: las 5 categorías de ropa del brief + calzado, por niño/niña. Varias hojas
# mapean al mismo slug de dominio (jeans/leggings/bermudas -> `pantalones`) para alinear el
# vocabulario con Zara y Sfera, porque las facetas del web son dinámicas y el slug del scraper
# *es* el filtro que ve el usuario. `vestidos` solo existe en niña, como en las otras tiendas.
#
# `barefoot` es un slug NUEVO, que hoy solo llena esta tienda: Lefties es la única de las
# candidatas que etiqueta el calzado respetuoso, y es justo el nicho del producto.
#
# EL ORDEN IMPORTA: un modelo puede colgar de dos hojas y `list_catalog()` deduplica por id,
# quedándose con la PRIMERA que lo ve. Las hojas barefoot van delante justo por eso — casi todo
# lo barefoot cuelga también de `zapatos`, y dejándolas al final el catálogo se quedaba con 4
# productos en `barefoot` en vez de ~30 (medido). Barefoot es la señal que interesa conservar,
# así que gana ella.
CATEGORIES: list[CategoryConfig] = [
    # --- barefoot: primero a propósito (ver nota de orden arriba) ---
    CategoryConfig(1030680692, "niña", "zapateria", "barefoot"),  # barefoot (rama propia)
    CategoryConfig(1030680609, "niña", "zapateria", "barefoot"),  # barefoot (dentro de zapatos)
    CategoryConfig(1030680206, "niño", "zapateria", "barefoot"),  # barefoot (rama propia)
    CategoryConfig(1030680610, "niño", "zapateria", "barefoot"),  # barefoot (dentro de zapatos)
    # --- niña / ropa ---
    CategoryConfig(1030267678, "niña", "ropa", "camisetas"),  # camisetas
    CategoryConfig(1030267686, "niña", "ropa", "camisetas"),  # tops | camisas
    CategoryConfig(1030267695, "niña", "ropa", "sudaderas"),  # sudaderas
    CategoryConfig(1030267697, "niña", "ropa", "sudaderas"),  # punto
    CategoryConfig(1030267701, "niña", "ropa", "pantalones"),  # pantalones
    CategoryConfig(1030267775, "niña", "ropa", "pantalones"),  # leggings
    CategoryConfig(1030580828, "niña", "ropa", "pantalones"),  # jeans
    CategoryConfig(1030267687, "niña", "ropa", "vestidos"),  # vestidos | monos
    CategoryConfig(1030267703, "niña", "ropa", "vestidos"),  # faldas | shorts
    CategoryConfig(1030293529, "niña", "ropa", "ropa-interior"),  # pijamas
    CategoryConfig(1030267711, "niña", "ropa", "ropa-interior"),  # ropa interior
    CategoryConfig(1030352572, "niña", "ropa", "ropa-interior"),  # calcetines
    # --- niña / zapatería ---
    CategoryConfig(1030272335, "niña", "zapateria", "zapatos"),  # zapatos
    CategoryConfig(1030272301, "niña", "zapateria", "zapatos"),  # botas y botines
    CategoryConfig(1030276114, "niña", "zapateria", "zapatos"),  # sandalias
    CategoryConfig(1030272304, "niña", "zapateria", "zapatillas"),  # zapatillas
    CategoryConfig(1030476904, "niña", "zapateria", "zapatillas"),  # deportivos
    # --- niño / ropa ---
    CategoryConfig(1030267807, "niño", "ropa", "camisetas"),  # camisetas
    CategoryConfig(1030267815, "niño", "ropa", "camisetas"),  # camisas
    CategoryConfig(1030269101, "niño", "ropa", "camisetas"),  # polos
    CategoryConfig(1030267820, "niño", "ropa", "sudaderas"),  # sudaderas
    CategoryConfig(1030267822, "niño", "ropa", "sudaderas"),  # jerséis
    CategoryConfig(1030702240, "niño", "ropa", "pantalones"),  # pantalones
    CategoryConfig(1030267826, "niño", "ropa", "pantalones"),  # pantalones de chándal
    CategoryConfig(1030566694, "niño", "ropa", "pantalones"),  # jeans
    CategoryConfig(1030321544, "niño", "ropa", "pantalones"),  # bermudas
    CategoryConfig(1030293530, "niño", "ropa", "ropa-interior"),  # pijamas
    CategoryConfig(1030267835, "niño", "ropa", "ropa-interior"),  # ropa interior
    CategoryConfig(1030352081, "niño", "ropa", "ropa-interior"),  # calcetines
    # --- niño / zapatería ---
    CategoryConfig(1030272391, "niño", "zapateria", "zapatos"),  # zapatos
    CategoryConfig(1030272326, "niño", "zapateria", "zapatos"),  # botas y botines
    CategoryConfig(1030276115, "niño", "zapateria", "zapatos"),  # sandalias
    CategoryConfig(1030272329, "niño", "zapateria", "zapatillas"),  # zapatillas
    CategoryConfig(1030272327, "niño", "zapateria", "zapatillas"),  # deportivos
]


def _cents(value: Any) -> Decimal | None:
    """Lefties da los precios en céntimos, pero como STRING ("1799" -> 17.99 €)."""
    if value is None or value == "":
        return None
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def grid_ids_by_category(menu: dict[str, Any]) -> dict[int, str]:
    """Recorre el menú y devuelve `id de categoría -> uuid del grid` de todas las hojas."""
    out: dict[int, str] = {}

    def walk(node: dict[str, Any]) -> None:
        content = node.get("content")
        cid = node.get("id")
        if isinstance(content, dict) and content.get("id") and isinstance(cid, int):
            out[cid] = str(content["id"])
        for child in node.get("children") or []:
            walk(child)

    for item in menu.get("items") or []:
        walk(item)
    return out


def _product_components(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """Componentes de tipo producto del grid. `components` es un DICT (no una lista)."""
    components = grid.get("components")
    if not isinstance(components, dict):
        return []
    return [c for c in components.values() if isinstance(c, dict) and c.get("kind") == "Product"]


def parse_listing_entries(grid: dict[str, Any], cat: CategoryConfig) -> list[ListingEntry]:
    """Agrupa los componentes (que son colores) por modelo y construye una entrada por modelo.

    La huella es el precio por color, como en Zara: barata de obtener en el listado y suficiente
    para saber si merece la pena pedir el detalle.
    """
    por_modelo: dict[str, list[str]] = {}
    for comp in _product_components(grid):
        ident = comp.get("identifier") or {}
        parent = ident.get("productParentId")
        if not parent:
            continue
        color = (comp.get("color") or {}).get("id")
        precio = (((comp.get("pricing") or {}).get("price") or {}).get("current") or {}).get(
            "value"
        )
        por_modelo.setdefault(str(parent), []).append(f"{color}:{precio}")

    return [
        ListingEntry(
            retailer_product_id=pid,
            signature="|".join(sorted(partes)),
            gender=cat.gender,
            section=cat.section,
            category=cat.category,
        )
        for pid, partes in por_modelo.items()
    ]


def _product_url(product: dict[str, Any], category_id: int) -> str | None:
    """URL canónica: la web enruta por `-c{categoría}p{producto}.html` e ignora el slug."""
    pid = product.get("id")
    if not pid:
        return None
    slug = _SLUG_SUFFIX.sub("", str(product.get("productUrl") or ""))
    return f"{BASE_URL}{slug}-c{category_id}p{pid}.html"


def product_name(product: dict[str, Any]) -> str:
    """Nombre de la ficha, con respaldos porque la tienda a veces no lo rellena.

    Visto en vivo: productos **visibles y comprables** con `name` y `nameEn` a `null` (p.ej.
    747871652, unas bambas de niño). Tirarlos sería perder catálogo real por un hueco de datos de
    la tienda, así que se cae a `familyName` ("BAMBAS  " -> "Bambas"), que siempre viene. Solo si
    no hay ninguno de los tres se descarta el producto: una ficha sin nombre no sirve de nada.
    """
    for clave in ("name", "nameEn", "familyName"):
        valor = product.get(clave)
        if isinstance(valor, str) and valor.strip():
            texto = " ".join(valor.split())
            return texto if clave != "familyName" else texto.capitalize()
    return ""


def _images_by_color(product: dict[str, Any]) -> dict[str, list[str]]:
    """`detail.xmedia` indexado por `colorCode` -> URLs de foto de ese color.

    Igual que en Zara se prefiere `extraInfo.deliveryUrl` (jpg plano) al `url` hermano, que lleva
    la plantilla `&w=:width:`; el ancho lo decide quien la pinta.
    """
    out: dict[str, list[str]] = {}
    for bloque in product.get("detail", {}).get("xmedia") or []:
        code = bloque.get("colorCode")
        if code is None:
            continue
        urls: list[str] = []
        for item in bloque.get("xmediaItems") or []:
            for media in item.get("medias") or []:
                url = (media.get("extraInfo") or {}).get("deliveryUrl")
                if url:
                    urls.append(str(url))
                if len(urls) == _MAX_IMAGES_PER_COLOR:
                    break
            if len(urls) == _MAX_IMAGES_PER_COLOR:
                break
        if urls:
            out[str(code)] = urls
    return out


def parse_detail_product(product: dict[str, Any], cat: CategoryConfig) -> ScrapedProduct | None:
    """Convierte una entrada de `productsArray` en ScrapedProduct (None si no hay variantes).

    Las entradas de error (`_ERR_PRODUCT_NOT_FOUND`) no traen `id` y se descartan aquí.
    """
    pid = product.get("id")
    if not pid:
        return None
    pid = str(pid)
    nombre = product_name(product)
    if not nombre:
        return None  # sin nombre por ninguna vía: una ficha así no sirve de nada
    url = _product_url(product, cat.category_id)
    galeria = _images_by_color(product)

    # Variantes e imágenes en la MISMA pasada por `colors`, leyendo el nombre del color de un
    # único sitio: es la clave con la que la ficha empareja foto y precio.
    variants: list[ScrapedVariant] = []
    images: list[ScrapedImage] = []
    for color in product.get("detail", {}).get("colors") or []:
        color_id = color.get("id")
        color_name = color.get("name")
        antes = len(variants)
        for size in color.get("sizes") or []:
            price = _cents(size.get("price"))
            if price is None:
                continue  # sin precio utilizable: no la registramos
            sku = size.get("sku")
            variants.append(
                ScrapedVariant(
                    retailer_variant_id=f"{pid}-{color_id}-{sku}",
                    size=size.get("name"),
                    color=color_name,
                    sku=str(sku) if sku is not None else None,
                    price=price,
                    list_price=_cents(size.get("oldPrice")),
                    # OJO: `isBuyable` viene true siempre y no sirve. La señal es esta.
                    in_stock=size.get("visibilityValue") == "SHOW",
                    url=url,
                )
            )
        if len(variants) == antes:
            continue  # color sin tallas con precio: sus fotos quedarían huérfanas
        images.extend(ScrapedImage(color=color_name, url=u) for u in galeria.get(str(color_id), []))

    if not variants:
        return None
    return ScrapedProduct(
        retailer_product_id=pid,
        name=nombre,
        gender=cat.gender,
        section=cat.section,
        category=cat.category,
        url=url,
        variants=variants,
        # Lefties tiene ramas `Barefoot` propias, así que casi siempre decide `cat.category`; el
        # nombre va como respaldo para el calzado respetuoso que no cuelgue de ellas.
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=pid,
            section=cat.section,
            category=cat.category,
            texts=nombre,
        ),
        image_url=images[0].url if images else None,
        images=images,
    )


def known_product_ids(payload: dict[str, Any]) -> set[str]:
    """Ids que la tienda reconoce en una respuesta de `productsArray`.

    Lo que no sale (viene como `_ERR_PRODUCT_NOT_FOUND`, sin `id`) es lo que ya no existe: es la
    prueba negativa que necesita la confirmación activa antes de dar de baja.
    """
    return {
        str(p["id"]) for p in payload.get("products") or [] if isinstance(p, dict) and p.get("id")
    }


def _batched(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class LeftiesStore:
    """Scraper de Lefties. Implementa BaseStore y SupportsAliveProbe."""

    slug = SLUG
    name = "Lefties"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        # Detalle cacheado del ámbito de cada producto: `fetch_details` recibe `ListingEntry`,
        # pero necesita la CategoryConfig para el dominio y para construir la URL.
        self._cat_by_product: dict[str, CategoryConfig] = {}

    def scopes(self) -> Iterable[ScrapeScope]:
        out: list[ScrapeScope] = []
        for c in self._categories:
            scope = ScrapeScope(c.gender, c.section, c.category)
            if scope not in out:
                out.append(scope)
        return out

    def list_catalog(self) -> Iterable[ListingEntry]:
        vistos: set[str] = set()
        with BrowserSession(self._config) as session:
            session.goto(BASE_URL)  # siembra las cookies de Akamai
            grids = grid_ids_by_category(session.get_json(_MENU_URL))
            for cat in self._categories:
                grid_id = grids.get(cat.category_id)
                if grid_id is None:
                    continue  # hoja retirada del menú: la red de bajas por ámbito la cubre
                grid = session.get_json(_GRID_URL.format(grid_id=grid_id))
                for entry in parse_listing_entries(grid, cat):
                    # Dedup por id: un modelo puede aparecer en dos hojas (p.ej. barefoot también
                    # cuelga de zapatos). Gana la primera, que fija su ámbito.
                    if entry.retailer_product_id in vistos:
                        continue
                    vistos.add(entry.retailer_product_id)
                    self._cat_by_product[entry.retailer_product_id] = cat
                    yield entry

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        ids = [e.retailer_product_id for e in entries]
        if not ids:
            return
        with BrowserSession(self._config) as session:
            session.goto(BASE_URL)
            for lote in _batched(ids, _DETAIL_BATCH):
                payload = session.get_json(_DETAILS_URL.format(ids=",".join(lote)))
                for product in payload.get("products") or []:
                    pid = str(product.get("id") or "")
                    cat = self._cat_by_product.get(pid)
                    if cat is None:
                        continue
                    scraped = parse_detail_product(product, cat)
                    if scraped is not None:
                        yield scraped

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Pregunta por los candidatos: lo que la tienda no reconoce está retirado.

        Un fallo de red deja el lote **sin veredicto** (fuera del mapa) en vez de darlo por
        retirado: la ingesta es conservadora y prefiere esperar otra pasada.
        """
        ids = [c.retailer_product_id for c in candidates]
        if not ids:
            return {}
        veredictos: dict[str, bool] = {}
        with BrowserSession(self._config) as session:
            session.goto(BASE_URL)
            for lote in _batched(ids, _DETAIL_BATCH):
                try:
                    payload = session.get_json(_DETAILS_URL.format(ids=",".join(lote)))
                except Exception:
                    continue  # sin veredicto para este lote
                vivos = known_product_ids(payload)
                for pid in lote:
                    veredictos[pid] = pid in vivos
        return veredictos
