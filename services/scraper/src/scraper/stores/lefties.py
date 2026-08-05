"""Scraper de Lefties (niños): ropa y calzado infantil.

Lefties es Inditex, igual que Zara, y su API es la familia `itxrest` — pero a diferencia de Zara
**está tras Akamai**: un cliente HTTP plano recibe 403 aunque mande cabeceras de navegador
completas. Por eso todo va por el **navegador headless** (`stores/browser.py`), como Sfera.

Cuatro endpoints, y el orden importa:

  1. menú:     /api/storefront/1/stores/{store}/menu?catalogId={catalog}&typeCatalog=1&...
     Da el árbol de categorías. La clave está en que cada hoja trae `content.id`, un **uuid**:
     es lo que pide el listado. Los `/{category}/product` que uno supone por analogía con Zara
     dan todos 404 — el id numérico de categoría NO sirve para listar.
     Esta misma petición es la que responde **las tres preguntas** de la tienda: qué hojas hay
     que listar, si siguen vivas (`check_leaves()`: la retirada es la que desaparece del menú) y
     qué publica que no ingerimos (`category_tree()`, #179). O sea que enumerarse le cuesta cero
     peticiones nuevas. Cada nodo trae además una `key` legible (`3_NA_T_ZAPATOS_ZAPATOS`) que es
     lo que hace el árbol comprensible cuando hay que decidir sobre un id opaco.
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

Tres trampas que conviene no volver a pisar:

- **El stock NO es `isBuyable`**, que viene `true` siempre. Es `visibilityValue`: `SHOW` =
  disponible, `HIDDEN` = agotada. Y con `allowWithoutStock=false` el endpoint **omite** las tallas
  agotadas, así que se pide `true`: si no, un producto agotado del todo parecería una baja.
- **`price` llega como string** de céntimos ("1799"), no como int, al revés que Zara.
- **Los componentes del grid no se reconocen por `kind`.** La tienda intercambió `kind` y `type`
  el 05/08/2026 y la pasada se quedó en 0 entradas sin que nada se pusiera rojo; el porqué y cómo
  se detectó están en `_product_components()`, que es donde vive la decisión.

Id estable de producto: `identifier.productParentId` (= `id` del detalle). Id estable de variante:
`{productId}-{colorId}-{sku}`. Las funciones `parse_*` son puras y se testean con fixtures reales.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    GONE_STATUS,
    CategoryNode,
    DelistCandidate,
    LeafHealth,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    ambito_cruzado,
    con_unisex,
)
from .browser import BrowserHTTPError, BrowserSession

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

# Departamento infantil del menú (`LEFTIES_KIDS`): la raíz del árbol de categorías y del barrido
# de cobertura del vigía. Ver `tree_roots()`.
_RAIZ_NINOS = 1030267671

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

    `parent` es la cadena de ids de la que cuelga la hoja, y **solo la usa la capa de cobertura**
    (`mapped_leaves()`): la pasada no la mira. Va escrita en vez de resolverse contra el menú
    porque resolverla obligaría a pedirlo, y `mapped_leaves()` lo llaman tests que tienen que
    seguir siendo herméticos —los de red aquí son opt-in (`SFERA_LIVE=1`) para no depender de
    Chromium ni de que Akamai deje entrar al runner de CI—. Que siga siendo cierta la vigila
    `test_el_padre_declarado_es_el_que_publica_el_menu`, contra la captura del menú.
    """

    category_id: int
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # pantalones | camisetas | sudaderas | vestidos | ropa-interior | zapatos | ...
    parent: str = ""  # cadena de ids del padre en el menú; ver `mapped_leaves()`


# Las seis ramas del menú de las que cuelga todo lo que ingerimos, medidas el 05/08/2026. Son el
# `parent` de cada `CategoryConfig` y con eso `mapped_leaves()` no necesita red.
_NINA = f"{_RAIZ_NINOS}/1030267672"
_NINA_ROPA = f"{_NINA}/1030267677"
_NINA_ZAPATOS = f"{_NINA}/1030267718"
_NINO = f"{_RAIZ_NINOS}/1030267673"
_NINO_ROPA = f"{_NINO}/1030269022"
_NINO_ZAPATOS = f"{_NINO}/1030267842"


# Subconjunto curado: las 5 categorías de ropa del brief + calzado, por niño/niña. Varias hojas
# mapean al mismo slug de dominio (jeans/leggings/bermudas -> `pantalones`) para alinear el
# vocabulario con Zara y Sfera, porque las facetas del web son dinámicas y el slug del scraper
# *es* el filtro que ve el usuario. `vestidos` solo existe en niña, como en las otras tiendas.
#
# `barefoot` es un slug NUEVO, que hoy solo llena esta tienda: Lefties es la única de las
# candidatas que etiqueta el calzado respetuoso, y es justo el nicho del producto.
#
# `botas` y `sandalias` también son slugs nuevos, que esta tienda estrena junto con Cacles. Antes
# estas cuatro hojas se colapsaban a `zapatos` para alinearse con Zara y Sfera, que no tienen hoja
# propia para ninguno de los dos; el resultado era tirar una distinción que Lefties YA nos daba
# gratis y que un padre buscando botas de agua sí quiere. Zara y Sfera quedan pendientes de una
# issue de vocabulario: hasta entonces sus sandalias siguen dentro de `zapatos`.
#
# Al remapear, la primera pasada verá caer mucho la población del ámbito (género, zapateria,
# zapatos) y la red de seguridad de `ingest.py` OMITIRÁ sus bajas esa vez. Es lo correcto y se
# recupera sola en la siguiente: los productos no se duplican ni se descatalogan, solo cambian de
# categoría (`ingest.py` hace `category = EXCLUDED.category`).
#
# EL ORDEN IMPORTA: un modelo puede colgar de dos hojas y `list_catalog()` deduplica por id,
# quedándose con la PRIMERA que lo ve. Las hojas barefoot van delante justo por eso — casi todo
# lo barefoot cuelga también de `zapatos`, y dejándolas al final el catálogo se quedaba con 4
# productos en `barefoot` en vez de ~30 (medido). Barefoot es la señal que interesa conservar,
# así que gana ella.
CATEGORIES: list[CategoryConfig] = [
    # --- barefoot: primero a propósito (ver nota de orden arriba) ---
    CategoryConfig(1030680692, "niña", "zapateria", "barefoot", _NINA),  # barefoot (rama propia)
    CategoryConfig(1030680609, "niña", "zapateria", "barefoot", _NINA_ZAPATOS),  # dentro de zapatos
    CategoryConfig(1030680206, "niño", "zapateria", "barefoot", _NINO),  # barefoot (rama propia)
    CategoryConfig(1030680610, "niño", "zapateria", "barefoot", _NINO_ZAPATOS),  # dentro de zapatos
    # --- niña / ropa ---
    CategoryConfig(1030267678, "niña", "ropa", "camisetas", _NINA_ROPA),  # camisetas
    CategoryConfig(1030267686, "niña", "ropa", "camisetas", _NINA_ROPA),  # tops | camisas
    CategoryConfig(1030267695, "niña", "ropa", "sudaderas", _NINA_ROPA),  # sudaderas
    CategoryConfig(1030267697, "niña", "ropa", "sudaderas", _NINA_ROPA),  # punto
    CategoryConfig(1030267701, "niña", "ropa", "pantalones", _NINA_ROPA),  # pantalones
    CategoryConfig(1030267775, "niña", "ropa", "pantalones", _NINA_ROPA),  # leggings
    CategoryConfig(1030580828, "niña", "ropa", "pantalones", _NINA_ROPA),  # jeans
    CategoryConfig(1030267687, "niña", "ropa", "vestidos", _NINA_ROPA),  # vestidos | monos
    CategoryConfig(1030267703, "niña", "ropa", "vestidos", _NINA_ROPA),  # faldas | shorts
    CategoryConfig(1030293529, "niña", "ropa", "ropa-interior", _NINA_ROPA),  # pijamas
    CategoryConfig(1030267711, "niña", "ropa", "ropa-interior", _NINA_ROPA),  # ropa interior
    CategoryConfig(1030352572, "niña", "ropa", "ropa-interior", _NINA_ROPA),  # calcetines
    # --- niña / zapatería ---
    CategoryConfig(1030272335, "niña", "zapateria", "zapatos", _NINA_ZAPATOS),  # zapatos
    CategoryConfig(1030272301, "niña", "zapateria", "botas", _NINA_ZAPATOS),  # botas y botines
    CategoryConfig(1030276114, "niña", "zapateria", "sandalias", _NINA_ZAPATOS),  # sandalias
    CategoryConfig(1030272304, "niña", "zapateria", "zapatillas", _NINA_ZAPATOS),  # zapatillas
    CategoryConfig(1030476904, "niña", "zapateria", "zapatillas", _NINA_ZAPATOS),  # deportivos
    # --- niño / ropa ---
    CategoryConfig(1030267807, "niño", "ropa", "camisetas", _NINO_ROPA),  # camisetas
    CategoryConfig(1030267815, "niño", "ropa", "camisetas", _NINO_ROPA),  # camisas
    CategoryConfig(1030269101, "niño", "ropa", "camisetas", _NINO_ROPA),  # polos
    CategoryConfig(1030267820, "niño", "ropa", "sudaderas", _NINO_ROPA),  # sudaderas
    CategoryConfig(1030267822, "niño", "ropa", "sudaderas", _NINO_ROPA),  # jerséis
    CategoryConfig(1030702240, "niño", "ropa", "pantalones", _NINO_ROPA),  # pantalones
    CategoryConfig(1030267826, "niño", "ropa", "pantalones", _NINO_ROPA),  # pantalones de chándal
    CategoryConfig(1030566694, "niño", "ropa", "pantalones", _NINO_ROPA),  # jeans
    CategoryConfig(1030321544, "niño", "ropa", "pantalones", _NINO_ROPA),  # bermudas
    CategoryConfig(1030293530, "niño", "ropa", "ropa-interior", _NINO_ROPA),  # pijamas
    CategoryConfig(1030267835, "niño", "ropa", "ropa-interior", _NINO_ROPA),  # ropa interior
    CategoryConfig(1030352081, "niño", "ropa", "ropa-interior", _NINO_ROPA),  # calcetines
    # --- niño / zapatería ---
    CategoryConfig(1030272391, "niño", "zapateria", "zapatos", _NINO_ZAPATOS),  # zapatos
    CategoryConfig(1030272326, "niño", "zapateria", "botas", _NINO_ZAPATOS),  # botas y botines
    CategoryConfig(1030276115, "niño", "zapateria", "sandalias", _NINO_ZAPATOS),  # sandalias
    CategoryConfig(1030272329, "niño", "zapateria", "zapatillas", _NINO_ZAPATOS),  # zapatillas
    CategoryConfig(1030272327, "niño", "zapateria", "zapatillas", _NINO_ZAPATOS),  # deportivos
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


def _es_divisor(nodo: Mapping[str, Any]) -> bool:
    """¿Es una raya de separación del menú y no una categoría?

    La tienda intercala separadores entre bloques del menú: se llaman `-`, son de tipo
    `marketing` y su `key` lo dice (`3_NA_T_SEPARACIÓN_ROPA`). Medido el 05/08/2026 bajo la rama
    infantil: **28 de 301 nodos**, ninguno con hijas y ninguno mapeado en `CATEGORIES`.

    No se emiten porque no son categorías: emitirlos sería inventarse 28 huecos de cobertura que
    nadie va a ingerir jamás, o exigir 28 declaraciones que envejecen con el menú. Se mira el
    nombre y no la `key` a propósito — si algún día una raya cambia de nombre, el vigía la cantará
    una vez como categoría nueva, que es un aviso barato y que se corrige solo; filtrar por un
    trozo de `key` podría, al revés, tapar una categoría de verdad.
    """
    return nodo.get("name") == "-"


def parse_category_tree(menu: dict[str, Any], root: str) -> list[CategoryNode]:
    """El árbol que publica el menú por debajo de `root`. Pura (JSON -> nodos).

    **La ruta es la cadena de ids desde la raíz pedida**, como en Zara y por el mismo motivo: los
    ids de Lefties son opacos (`1030267678`) y no se anidan solos, así que sin cadena no hay forma
    de saber que un nodo cuelga de una hoja que ya ingerimos. Medido el 05/08/2026: de los 273
    nodos de la rama infantil, **187 cuelgan de una hoja de `CATEGORIES`**, y a id suelto los 187
    se señalarían como huecos.

    El menú trae además una `key` legible (`3_NA_T_ZAPATOS_ZAPATOS`, `LEFTIES_BABYGIRL`) que es lo
    que hace el árbol comprensible al leerlo. No se usa como ruta porque el vocabulario de esta
    capa tiene que ser el mismo que el de `LeafHealth.leaf` y el de `CATEGORIES`, que es el id
    numérico; la `key` va en el motivo de cada declaración, que es donde se lee.

    El `count` es `None` en todos: el menú publica navegación, no inventario (ver `CategoryNode`).
    """
    raiz = _buscar_nodo(menu.get("items") or [], root.rsplit("/", 1)[-1])
    if raiz is None:
        return []

    nodos: list[CategoryNode] = []

    def walk(nodo: Mapping[str, Any], cadena: str, depth: int) -> None:
        for hija in nodo.get("children") or []:
            if not isinstance(hija, dict) or not isinstance(hija.get("id"), int):
                continue
            ruta = f"{cadena}/{hija['id']}"
            # Los divisores no se emiten, pero se recorren igual: si alguno llegara a tener hijas,
            # saltárselas escondería catálogo, que es justo lo contrario de para lo que existe esto.
            if not _es_divisor(hija):
                nodos.append(
                    CategoryNode(
                        path=ruta,
                        title=str(hija.get("name") or ""),
                        count=None,
                        depth=depth,
                        has_children=any(
                            isinstance(n, dict) and not _es_divisor(n)
                            for n in hija.get("children") or []
                        ),
                    )
                )
            walk(hija, ruta, depth + 1)

    walk(raiz, root, 1)
    return nodos


def _buscar_nodo(nodos: Iterable[Any], category_id: str) -> Mapping[str, Any] | None:
    """El nodo con ese id en cualquier profundidad, o `None` si el menú ya no lo publica."""
    for nodo in nodos:
        if not isinstance(nodo, dict):
            continue
        if str(nodo.get("id")) == category_id:
            return nodo
        encontrado = _buscar_nodo(nodo.get("children") or [], category_id)
        if encontrado is not None:
            return encontrado
    return None


def _product_components(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """Componentes de producto del grid. `components` es un DICT (no una lista).

    **No se filtra por `kind`, y es una cicatriz.** Hasta el 05/08/2026 esto exigía
    `kind == "Product"`; ese día se midió que la tienda había pasado a nombrar el `kind` por
    familia —`Clothing` y `Footwear`—, así que **las 38 hojas parseaban 0 entradas** y se
    descartaban 2207 componentes que traían su `identifier.productParentId` intacto.

    Lo que lo hace peligroso es lo callado que es: los tests con fixtures seguían verdes (el
    fixture decía `Product`) y `check_leaves()` daba 38/38 vivas, porque el menú no había
    cambiado. Lo cazó el vigía, y por el listado vacío, no por el `kind` (#179).

    Y no fue un renombrado, fue un **intercambio**: el componente sigue trayendo los dos campos,
    con los valores cambiados de sitio. Medido sobre la misma hoja (zapatos de niña):

        antes:  kind="Product"   type="Footwear"
        hoy:    kind="Footwear"  type="Product"

    O sea que fiarse de `type` sería repetir la apuesta que acaba de salir mal. El criterio es el
    que de verdad distingue un producto de un adorno: que traiga el identificador del modelo, que
    es además lo único que este parser necesita de él. Los banners de campaña viajan aparte, en
    `promotionalBanners`.
    """
    components = grid.get("components")
    if not isinstance(components, dict):
        return []
    return [
        c
        for c in components.values()
        if isinstance(c, dict) and (c.get("identifier") or {}).get("productParentId")
    ]


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

    def __init__(
        self,
        config: Config,
        categories: list[CategoryConfig] | None = None,
        session_factory: Callable[[], BrowserSession] | None = None,
    ) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        # Costura para los tests: por defecto abre un Chromium real (igual que Sfera).
        self._session_factory = session_factory or (lambda: BrowserSession(config))
        # Detalle cacheado del ámbito de cada producto: `fetch_details` recibe `ListingEntry`,
        # pero necesita la CategoryConfig para el dominio y para construir la URL.
        self._cat_by_product: dict[str, CategoryConfig] = {}
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._menu_cache: dict[str, Any] | None = None  # árbol cacheado; ver `_menu()`

    def scopes(self) -> Iterable[ScrapeScope]:
        """Los ámbitos de las hojas **más su equivalente `unisex`** (ver `base.con_unisex`).

        Los productos que salen en las dos ramas de género se emiten `unisex` (#98), así que sin
        declarar esos ámbitos no se descatalogarían nunca. **14 de 700 (2,0 %) el 03/08/2026**, casi
        todos camisetas.

        Esta tienda no declara ninguna hoja `unisex` en `CATEGORIES` —al revés que Hipercor, H&M y
        Mango, que tienen rama de bebé o newborn—, así que ese número **es** el de cruces y se
        re-mide sin base de datos ni detalle con `python -m scraper.run --retailer lefties
        --dry-run`, que publica el reparto de género del listado. Se dice aquí porque #139 nació de
        comparar ese 0 contra el `unisex` de las otras tres, que no mide lo mismo.
        """
        return con_unisex(ScrapeScope(c.gender, c.section, c.category) for c in self._categories)

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Recorre las hojas y emite un producto por `productParentId`.

        **Acumula la pasada entera antes de emitir**, como H&M e Hipercor: que un producto salga en
        la rama de niña Y en la de niño —lo que lo hace `unisex`, #98— solo se sabe con todas las
        hojas vistas, y el ámbito de una entrada ya emitida no se puede corregir.
        """
        self._scan = ScanReport()
        primera_entrada: dict[str, ListingEntry] = {}
        hojas_por_producto: dict[str, list[ScrapeScope]] = {}
        with self._session_factory() as session:
            session.goto(BASE_URL)  # siembra las cookies de Akamai
            grids = grid_ids_by_category(session.get_json(_MENU_URL))
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                grid_id = grids.get(cat.category_id)
                if grid_id is None:
                    # Hoja que ya no está en el menú: es el mismo caso que el 404 de una hoja de
                    # Zara. Se salta, pero su ámbito sale de las bajas — el comentario que había
                    # aquí daba por hecho que la red por ámbito lo cubría, y no es así: `scopes()`
                    # se deriva de CATEGORIES, así que el ámbito seguía contando como escaneado.
                    self._hoja_comprometida(scope, str(cat.category_id))
                    continue
                try:
                    grid = session.get_json(_GRID_URL.format(grid_id=grid_id))
                except BrowserHTTPError as exc:
                    if exc.status not in GONE_STATUS:
                        raise
                    self._hoja_comprometida(scope, str(cat.category_id))
                    continue
                self._scan.leaf_ok()
                for entry in parse_listing_entries(grid, cat):
                    pid = entry.retailer_product_id
                    # Un modelo puede aparecer en varias hojas (p.ej. barefoot también cuelga de
                    # zapatos). La primera fija sección, categoría y huella; el género sale del
                    # conjunto de hojas, no de ella sola.
                    if pid not in primera_entrada:
                        primera_entrada[pid] = entry
                        self._cat_by_product[pid] = cat
                    hojas = hojas_por_producto.setdefault(pid, [])
                    if scope not in hojas:
                        hojas.append(scope)

        for pid, entry in primera_entrada.items():
            ambito = ambito_cruzado(hojas_por_producto[pid])
            cat_primera = self._cat_by_product[pid]
            # `fetch_details` construye la URL y el dominio desde esta `CategoryConfig`, así que se
            # conserva la de la primera hoja (su `category_id`) con el género ya resuelto.
            self._cat_by_product[pid] = replace(
                cat_primera, gender=ambito.gender or cat_primera.gender
            )
            yield replace(
                entry,
                gender=ambito.gender,
                section=ambito.section,
                category=ambito.category,
            )

    def _hoja_comprometida(self, scope: ScrapeScope, leaf: str) -> None:
        """Cuenta la hoja como caída y saca su ámbito —y el `unisex` equivalente— de las bajas.

        El porqué de lo segundo está en `ScanReport.leaf_gone()`.
        """
        self._scan.leaf_gone(scope, leaf, tambien_unisex=True)

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas (ver `stores.base.SupportsLeafHealth`).

        Aquí sale casi gratis y sin tocar los grids: **el menú entero es UNA petición** y una hoja
        retirada es, precisamente, la que ya no aparece en él. Que el grid siga respondiendo se
        comprueba en la pasada, que es cuando hace falta.
        """
        with self._session_factory() as session:
            session.goto(BASE_URL)  # siembra las cookies de Akamai
            grids = grid_ids_by_category(session.get_json(_MENU_URL))
        for cat in self._categories:
            scope = ScrapeScope(cat.gender, cat.section, cat.category)
            grid_id = grids.get(cat.category_id)
            yield LeafHealth(
                scope,
                str(cat.category_id),
                grid_id is not None,
                f"grid {grid_id}" if grid_id else "ya no está en el menú",
            )

    # --- capacidades opcionales --------------------------------------------------------------

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. Sale del menú que la pasada ya se baja.

        **Cero peticiones nuevas sobre lo que esta tienda ya pide**: `list_catalog()` y
        `check_leaves()` bajan este mismo menú en cada ejecución para resolver los uuid de los
        grids (ver `grid_ids_by_category`), así que enumerarse le cuesta lo mismo que sondearse.

        `root` es una cadena de ids (`1030267671` para Niños, `1030267671/1030267672` para su rama
        de niña) y basta el último para localizar el nodo: los ids son únicos en todo el menú.
        """
        return parse_category_tree(self._menu(), root)

    def _menu(self) -> dict[str, Any]:
        """El menú del que sale el árbol, cacheado por instancia.

        `--tree` y el barrido del vigía piden el árbol varias veces y el menú es el mismo en
        todas; sin caché serían varios Chromium y varias siembras de Akamai para el mismo JSON.
        """
        if self._menu_cache is None:
            with self._session_factory() as session:
                session.goto(BASE_URL)  # siembra las cookies de Akamai
                self._menu_cache = session.get_json(_MENU_URL)
        return self._menu_cache

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las hojas de `CATEGORIES`, en cadena de ids.

        **Sin red**, al revés que en Zara: allí la cadena se resuelve pidiendo el árbol, y aquí se
        arma con el `parent` que cada `CategoryConfig` ya declara. La diferencia importa porque a
        esto lo llama `test_cobertura_declarada_no_solapa_con_lo_mapeado`, que corre en `just
        check`: resolver contra el menú metería Chromium y a Akamai en el camino por defecto de CI,
        que es justo lo que los smokes en vivo evitan siendo opt-in.

        Una hoja que el menú dejara de publicar se sigue emitiendo tal cual, como en H&M y a
        propósito: lo que hay que decir entonces es que la hoja ha muerto, y eso lo canta
        `check_leaves()` —aquí una hoja retirada es, precisamente, la que desaparece del menú— con
        el veredicto que corresponde. Omitirla aquí solo conseguiría que además apareciese como
        hueco de cobertura, que es el mismo hecho contado dos veces y peor.
        """
        return [f"{cat.parent}/{cat.category_id}" for cat in self._categories]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCategoryTree`. La cadena de ids se anida con `/`.

        Es nuestro, no de la tienda: sus ids son opacos y no se anidan solos (ver
        `parse_category_tree`). Da igual cuál sea mientras no aparezca dentro de un id, y un id de
        Lefties es siempre un número.
        """
        return "/"

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. El departamento infantil entero, una raíz.

        No se barre por rama de género —como sí hacen Sfera, Springfield y H&M— porque aquí el
        departamento **no tapa nada**: medido el 05/08/2026, de `Niños` cuelgan exactamente las
        cinco ramas de género (Niña, Niño, Bebé Niña, Bebé Niño, Recién Nacido) y un separador, y
        sus 273 nodos son todos infantiles. En H&M el departamento arrastraba 258 rutas de casa y
        juguetes, y por eso allí las raíces son las siete ramas.

        Barrer desde el departamento tiene además una propiedad que las raíces por rama no dan:
        **una rama de género nueva se ve sola**. Con las cinco declaradas a mano, el día que la
        tienda partiera «Bebé» en dos nadie se enteraría — que es la forma exacta del hueco que
        esta capa existe para tapar.
        """
        return [str(_RAIZ_NINOS)]

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
