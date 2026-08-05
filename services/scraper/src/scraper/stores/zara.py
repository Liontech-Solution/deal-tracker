"""Scraper de Zara (niños): calzado y ropa infantil.

Zara expone endpoints AJAX públicos que devuelven JSON, así que evitamos
navegador (imagen ligera). Tres endpoints:

  - árbol de categorías:  /categories?ajax=true
  - listado de categoría: /category/{id}/products?ajax=true   (ids + precio por color)
  - detalle (con tallas):  /products-details?productIds={id}&ajax=true

El árbol se enumera (`SupportsCategoryTree`, #179) y con él va `--tree`, que es lo que hay que
lanzar cuando una hoja da 404 (ver la nota sobre ids caducados más abajo). Lo que **no** se hace es
vigilarlo cada semana: ese árbol es el menú de navegación, con 536 nodos sin cubrir de 766 que son
«VER TODO», «COLECCIÓN», dividers y editoriales. El motivo, medido, está en
`vigia.COBERTURA_SIN_VIGILAR`.

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
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    GONE_STATUS,
    CategoryNode,
    DelistCandidate,
    FiltroDeHoja,
    LeafHealth,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)

SLUG = "zara"  # a nivel de módulo porque las funciones puras de parseo también lo necesitan
BASE_URL = "https://www.zara.com/es/es/"
_CATEGORY_URL = BASE_URL + "category/{cat_id}/products?ajax=true"
# El árbol de navegación entero en una petición (~1 MB). Solo lo usa el recon (`--tree`), no la
# pasada: para saber si una hoja sigue viva se le pide su listado, no esto (ver `check_leaves`).
_CATEGORIES_URL = BASE_URL + "categories?ajax=true"
# El nodo del que cuelga todo el catálogo infantil, que es el alcance de este scraper.
_RAIZ_NINOS = 2112261
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
    # Solo en las hojas que mezclan vocabulario (#200). Ver `FiltroDeHoja` y `_SOLO_CONJUNTOS`.
    filtro: FiltroDeHoja | None = None


# El conjunto de Zara se reconoce por DOS señales y basta con una, porque medido el 06/08/2026
# ninguna basta sola:
#
#   - `familyName` es **taxonomía de la tienda** (`CONJUNTO`, junto a `PANTALON`, `ZAPATO`,
#     `BAMBAS`…) y viene en cada nodo del listado. Coge 13 que el título pierde: los que la tienda
#     titula `PACK BODY CRUZADO Y LEGGING` o `SET PRIMERA PUESTA Y BOLSITA` —que son conjuntos, solo
#     que llamados de otra forma— y un `CONJUTO CAMISETA Y BERMUDA` con la errata de la tienda.
#   - el **título**, que coge 40 que la familia pierde: `CONJUNTO SUDADERA RAYAS Y LEGGING FLARE` y
#     compañía están archivados en la familia `CHANDAL BEBE`, que NO vale como señal por sí sola —
#     esa familia también contiene pantalones y camisetas sueltos (ver la hoja 2426354 arriba).
#
# Las dos se anclan al principio (`\A`) porque las dos son etiquetas, no frases: sin ancla, un
# `VESTIDO CON CONJUNTO DE …` entraría como conjunto sin que nadie lo mirara.
_SOLO_CONJUNTOS = FiltroDeHoja(re.compile(r"\ACONJUNTO", re.IGNORECASE))


# Subconjunto curado: calzado + ropa infantil. Zara separa el catálogo en TRES rangos de edad
# (6-14 años, "mini" 1½-6 años y bebé 0-18 meses), cada uno con su propio id de categoría-hoja;
# se incluyen los tres para máxima cobertura. Varias hojas mapean al mismo slug de dominio (p.ej.
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
# Y por el mismo motivo al revés, LAS HOJAS DE BEBÉ VAN AL FINAL (#186). Su rango solapa con el
# de las hojas mini: medido el 05/08/2026, las once hojas listan 1157 productos y 612 de ellos YA
# entraban por una hoja con género. Yendo detrás, el dedup se queda con la versión con género
# —`niña`/`niño`, que es la que la web puede filtrar— y solo entran como `unisex` los 545 que de
# verdad no estaban (el catálogo pasa de 3382 a 3927). Si fuesen delante, esos 612 perderían el
# género y además contarían como mudanza de ámbito (`ingest._moved_out_counts`, #174).
#
# El género es `unisex` porque la rama de bebé de Zara NO separa niño de niña, igual que ya se
# decidió para `recien nacido` en H&M y `zapatos-infantiles/bebe` en Hipercor.
#
# Las once hojas son las del eje de PRENDA, que es el que habla nuestro vocabulario. Zara publica
# además el mismo catálogo por EDAD (`RECIÉN NACIDO | 0-9 MESES` y los tramos 0-1/1-3/3-6/6-9/
# 9-12/12-18 meses): son la misma ropa cortada por talla, así que añadirlas serían ~40 peticiones
# por pasada para no aportar productos. Lo que queda fuera a propósito por no ser del brief:
# `CAZADORAS | BUZOS` (2428026, abrigo, como en Sfera), `ACCESORIOS` (2428034) y `BOLSOS DE
# MATERNIDAD` (2428129).
#
# **`CONJUNTOS` entra FILTRADA, y el motivo hay que leerlo antes de tocarla (#192, #200).** Mapear
# la hoja entera se probó en #192 y una pasada real lo desmintió: las hojas de conjunto de Zara son
# LOOKBOOKS, agrupan las prendas sueltas que componen un look y no un producto que sea varias
# prendas. De los 41 productos que ingirieron el 05/08/2026, solo 7 se llamaban «CONJUNTO …»; los
# otros 34 eran gorros, capotas, cazadoras, chubasqueros, blazers, jerséis y leggings — prendas
# sueltas, y casi todas de las que el brief deja fuera (abrigo, accesorios). Así que se revirtió.
#
# La trampa está en cómo se mide, y sigue en pie. Contar los productos EXCLUSIVOS de la hoja (los
# que no entran por ninguna otra) parece decir «estos no tienen casa natural», y no dice eso:
# también son exclusivos los que tienen una casa que hemos decidido no ingerir. En una hoja-lookbook
# las dos poblaciones se confunden, y salen 52 «conjuntos» que no lo son. El indicio estructural
# estaba a la vista y no se leyó: las tres cuelgan de `TOTAL LOOK | CHÁNDAL`, no del eje de prenda.
#
# Lo que cambia en #200 es que ya no hay que elegir entre la hoja entera y nada: `FiltroDeHoja` deja
# quedarse solo con lo que la tienda identifica como conjunto (ver `_SOLO_CONJUNTOS`), y el residuo
# del lookbook se descarta como antes.
#
# **Y cuáles se mapean lo dice la medida, no el nombre de la hoja.** Contadas las seis candidatas el
# 06/08/2026, «conjuntos» = familia CONJUNTO ∪ título «CONJUNTO …»:
#   ✓ 2428167 CONJUNTOS  (bebé)      245 productos → 78 conjuntos
#     2426357 CONJUNTOS  (niña 6-14)  56          →  0   ← la hoja se llama así y no trae ninguno
#     2428290 TOTAL LOOK (niño 6-14)  44          →  0
#   ✓ 2426354 CHÁNDAL    (niña)       81          → 13   ← la ÚNICA fuente de conjuntos de niña 6-14
#   ✓ 2622124 CHANDAL    (niño)      117          → 20
#   ✓ 2558947 PACKS|CONJUNTOS (niño)  26          → 18
#
# O sea que las dos hojas que llevan «CONJUNTOS» en el rótulo no publican ni uno, y tres de las que
# #192 descartó son las que los tienen. Es la misma lección que aquella issue, por el otro lado: en
# esta tienda el nombre de la hoja no dice lo que hay dentro, ni para bien ni para mal.
#
# Las dos de 0 se quedan **fuera a propósito**: mapearlas costaría dos peticiones por pasada para no
# traer nada y dejaría su ámbito permanentemente fuera de las bajas por `filtro_vacio()`, que es una
# señal que solo vale si significa algo. Si algún día vuelven a publicar conjuntos, se añaden.
#
# Ojo también al número que citaba #192: «207 productos nuevos» **ya no es cierto**. Se midió
# mientras se implementaba #186, contra el catálogo de antes de que existieran las hojas de bebé.
#
# Dos hojas de bebé mezclan vocabulario y la elección no es obvia; queda escrita porque el nombre
# de la hoja no basta y hubo que mirar el contenido:
#   - `PETOS | MONOS` (2428227) -> `vestidos`. De sus 47 productos, 22 nombran «peto» y 18 «mono».
#     Hipercor y Sfera mandan el peto a `pantalones`, pero Zara agrupa aquí el mono, que su propia
#     hoja `VESTIDOS | MONOS` (2427560) ya clasifica como `vestidos`, y Mango razona lo mismo con
#     el pelele. Se prefiere ser coherente DENTRO de la tienda a serlo entre tiendas; la
#     divergencia con Hipercor es de la misma familia que #187 y está declarada aquí, no oculta.
#   - `BERMUDAS | BRAGUITAS` (2428071) -> `pantalones`. De sus 146 productos, 96 nombran «bermuda»
#     contra 25 «braguita» y 11 «bloomer», y la braguita de bebé es el cubrepañal que se lleva
#     encima, no ropa interior. Manda la bermuda, que es lo que ya hace la hoja de niño (2426543).
# `BODIES` (2428124) -> `ropa-interior` no tiene esa duda: es la prenda base de bebé, y es
# literalmente la decisión que ya está escrita en `hipercor.py` y en `hm.py`.
#
# Bebé no tiene hoja barefoot propia (las cuatro de arriba cubren 6-14 y mini), así que su calzado
# respetuoso solo lo puede marcar el respaldo por descripción de `classify_barefoot`.
#
# Nota: el hub NIÑOS > ACCESORIOS | ZAPATOS > CALZADO BAREFOOT (2597610) tiene sus propias hojas
# por género/edad (2630194, 2631201, 2630196, 2630195). Medidas: devuelven EXACTAMENTE las mismas
# 86 referencias que las cuatro de aquí, así que no se añaden — serían cuatro peticiones por
# pasada a cambio de nada.
#
# LOS IDS CADUCAN, y más rápido de lo que parece: `2428332` (niño > pantalones, 6-14) se verificó
# vivo al añadirlo y devolvía 404 CUATRO DÍAS después, desaparecido del árbol de `/categories`;
# Zara lo había reemplazado por `2428292`, mismo `key` que su gemelo de niña. Aquello tumbó la
# pasada entera (47 hojas por una). Desde #41 ya no: un 404 se salta, se cuenta en el `ScanReport`
# y su ÁMBITO sale de las bajas —lo que no se ha podido mirar no está retirado—, y solo si cae una
# proporción alta de las hojas se aborta. Aun así, una hoja caída es una categoría que dejamos de
# ingerir: el resumen del job la canta, y toca buscar el id nuevo en `/categories?ajax=true`.
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
    # --- conjuntos (#200): DELANTE de la ropa, y con filtro ---
    # Van delante para que un conjunto que la tienda publica además bajo una de las cinco del brief
    # se quede como `conjuntos` y no como `sudaderas`.
    #
    # **El orden no es el mismo en las cinco tiendas, y el criterio es el tamaño de la hoja**,
    # medido el 06/08/2026: DELANTE donde la hoja es un residuo (aquí 84 conjuntos, 72 de ellos
    # re-etiquetados; en Sfera 28 y 9) y DETRÁS donde es un catálogo paralelo (en H&M eran 560 y
    # adelantarlas vaciaba un tercio de `pantalones`; las cifras están al final de su `CATEGORIES`).
    # C&A e Hipercor van detrás por lo mismo por lo que fueron las primeras en tener la categoría:
    # allí la hoja es limpia y solo se buscaba lo exclusivo.
    #
    # Pero NO delante del calzado: un producto de las hojas barefoot conserva su precedencia, que es
    # lo que decidió la nota de orden de arriba y no tiene nada que ver con esto.
    CategoryConfig(2426354, "niña", "ropa", "conjuntos", filtro=_SOLO_CONJUNTOS),  # CHÁNDAL, 13
    CategoryConfig(2622124, "niño", "ropa", "conjuntos", filtro=_SOLO_CONJUNTOS),  # CHANDAL, 20
    CategoryConfig(2558947, "niño", "ropa", "conjuntos", filtro=_SOLO_CONJUNTOS),  # PACKS, 18
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
    CategoryConfig(2428292, "niño", "ropa", "pantalones"),  # pantalones (2428332 → 404 28/07)
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
    # --- bebé 0-18 meses (#186): AL FINAL a propósito, ver la nota del orden más arriba ---
    # Su hoja de conjuntos va PRIMERA dentro de este bloque, no del fichero: así gana a las demás
    # de bebé (un conjunto de bebé se etiqueta `conjuntos` y no `sudaderas`) y sigue perdiendo con
    # todas las de género, que es exactamente lo que #186 midió y quiso — 612 productos de bebé ya
    # entraban con género, y adelantarla se lo quitaría para ponerles `unisex`.
    CategoryConfig(2428167, "unisex", "ropa", "conjuntos", filtro=_SOLO_CONJUNTOS),
    CategoryConfig(2637249, "unisex", "ropa", "vestidos"),  # vestidos | peleles
    CategoryConfig(2428227, "unisex", "ropa", "vestidos"),  # petos | monos
    CategoryConfig(2428149, "unisex", "ropa", "camisetas"),  # camisetas
    CategoryConfig(2428131, "unisex", "ropa", "camisetas"),  # camisas
    CategoryConfig(2428796, "unisex", "ropa", "sudaderas"),  # sudaderas
    CategoryConfig(2428244, "unisex", "ropa", "sudaderas"),  # punto
    CategoryConfig(2428210, "unisex", "ropa", "pantalones"),  # pantalones
    CategoryConfig(2428071, "unisex", "ropa", "pantalones"),  # bermudas | braguitas
    CategoryConfig(2428124, "unisex", "ropa", "ropa-interior"),  # bodies
    CategoryConfig(2428809, "unisex", "ropa", "ropa-interior"),  # ropa int. | pijamas | calcetines
    CategoryConfig(2428823, "unisex", "zapateria", "zapatos"),  # zapatos
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
    """Extrae una `ListingEntry` (id estable + huella) por producto, en orden y sin duplicar.

    Si la hoja lleva `filtro` (#200), lo que no case **no se emite**: la hoja es un lookbook y sus
    otros productos son prendas sueltas que ya entran por su propia hoja o que el brief deja fuera.
    Descartarlos aquí y no más adelante importa por el dedup de `list_catalog()`: un producto no
    emitido no ocupa su hueco en `emitted`, así que sigue pudiendo entrar por la hoja que le toca.
    """
    entries: list[ListingEntry] = []
    seen: set[str] = set()
    for node in _iter_product_nodes(listing):
        pid = str(node["seo"]["discernProductId"])
        if pid in seen:
            continue
        seen.add(pid)
        categoria = cat.category
        if cat.filtro is not None:
            # Las DOS señales, porque ninguna basta sola (ver `_SOLO_CONJUNTOS`).
            resuelta = cat.filtro.categoria(
                str(node.get("familyName") or ""),
                str(node.get("name") or ""),
                propia=cat.category,
            )
            if resuelta is None:
                continue
            categoria = resuelta
        entries.append(
            ListingEntry(
                retailer_product_id=pid,
                signature=_listing_signature(node),
                gender=cat.gender,
                section=cat.section,
                category=categoria,
            )
        )
    return entries


def _descripciones(entry: dict[str, Any]) -> list[str]:
    """Textos descriptivos del detalle. Zara los cuelga de cada COLOR, no del producto."""
    return [
        str(color["description"])
        for color in entry.get("detail", {}).get("colors", [])
        if color.get("description")
    ]


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
    name = entry.get("name", "")
    return ScrapedProduct(
        retailer_product_id=pid,
        name=name,
        gender=gender,
        section=section,
        category=category,
        url=url,
        variants=variants,
        # Zara etiqueta el barefoot en su árbol, así que lo normal es que decida `category`. La
        # descripción por color va igualmente como respaldo: cubre el zapato respetuoso que Zara
        # describe como tal pero no cuelga de la hoja BAREFOOT.
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=pid,
            section=section,
            category=category,
            texts=[name, *_descripciones(entry)],
        ),
        # La foto de tarjeta sale de la propia galería, para no tener dos fuentes de verdad.
        image_url=images[0].url if images else None,
        images=images,
    )


def parse_category_tree(payload: Any, root: str) -> list[CategoryNode]:
    """El árbol que publica `/categories`, por debajo de `root`. Pura (JSON -> nodos).

    **La ruta es la cadena de ids desde la raíz pedida** (`2425905/2427327`), no el id suelto, y es
    la decisión que hace útil a toda la capa: los ids de Zara son opacos —al revés que los de C&A,
    donde `3-7-1-2` ya dice que cuelga de `3-7-1`— así que sin cadena no hay forma de saber que un
    nodo está dentro de una hoja que ya ingerimos. Medido el 04/08/2026: de los 878 nodos del árbol
    infantil, **183 cuelgan de una hoja de `CATEGORIES`**, y a id suelto los 183 se señalarían como
    huecos. Un informe con 183 falsos positivos no lo lee nadie dos veces.

    El `count` es `None` en todos: este endpoint publica la navegación, no cuántos productos hay
    detrás. `None` es «no lo dice», que no es 0 (ver `CategoryNode`).
    """
    raiz = _buscar_nodo(payload.get("categories") or [], int(root.rsplit("/", 1)[-1]))
    if raiz is None:
        return []

    nodos: list[CategoryNode] = []

    def walk(nodo: Mapping[str, Any], cadena: str, depth: int) -> None:
        for hija in nodo.get("subcategories") or []:
            if not isinstance(hija, dict) or not isinstance(hija.get("id"), int):
                continue
            ruta = f"{cadena}/{hija['id']}"
            nodos.append(
                CategoryNode(
                    path=ruta,
                    title=str(hija.get("name") or ""),
                    count=None,
                    depth=depth,
                    has_children=bool(hija.get("subcategories")),
                )
            )
            walk(hija, ruta, depth + 1)

    walk(raiz, root, 1)
    return nodos


def _buscar_nodo(nodos: Iterable[Any], category_id: int) -> Mapping[str, Any] | None:
    """El nodo con ese id en cualquier profundidad, o `None` si el árbol ya no lo publica."""
    for nodo in nodos:
        if not isinstance(nodo, dict):
            continue
        if nodo.get("id") == category_id:
            return nodo
        encontrado = _buscar_nodo(nodo.get("subcategories") or [], category_id)
        if encontrado is not None:
            return encontrado
    return None


class ZaraStore:
    """Scraper de Zara. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "Zara"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._rutas: list[str] | None = None  # árbol cacheado; ver `_cadenas()`

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
        self._scan = ScanReport()
        emitted: set[str] = set()  # dedup entre categorías dentro de la misma ejecución
        with self._client() as client:
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    listing = self._get_json(client, _CATEGORY_URL.format(cat_id=cat.category_id))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in GONE_STATUS:
                        raise  # bloqueo o fallo del servidor: no es una hoja retirada
                    self._scan.leaf_gone(scope, str(cat.category_id))
                    continue
                self._scan.leaf_ok()
                entradas = parse_listing_entries(listing, cat)
                # La hoja respondió pero su filtro no casó con nada: puede ser que la tienda haya
                # cambiado la rotulación, y entonces callarse descatalogaría todo lo que había.
                # Ver `ScanReport.filtro_vacio()`.
                if cat.filtro is not None and not entradas:
                    self._scan.filtro_vacio(scope, str(cat.category_id))
                for entry in entradas:
                    if entry.retailer_product_id not in emitted:
                        emitted.add(entry.retailer_product_id)
                        yield entry

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. Una sola petición para el árbol entero.

        `/categories?ajax=true` es el endpoint que la cabecera de este módulo ya manda consultar a
        mano cada vez que una hoja da 404 — «toca buscar el id nuevo». Esto es exactamente eso, pero
        sin salir del proyecto y cruzado contra lo que ingerimos, que es la parte que a ojo se hace
        mal: son 878 nodos.

        `root` es una cadena de ids (`2112261` para NIÑOS, `2112261/2425905` para su rama de niña),
        y basta con el último para localizar el nodo: los ids son únicos en todo el árbol.
        """
        with self._client() as client:
            payload = self._get_json(client, _CATEGORIES_URL)
        return parse_category_tree(payload, root)

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las hojas de `CATEGORIES`, en cadena de ids.

        Hay que resolverlas contra el árbol porque `CATEGORIES` guarda el id suelto y el vocabulario
        de esta capa es la cadena (ver `parse_category_tree`). Cuesta la misma petición que el
        árbol y se cachea, así que `run --tree` sigue haciendo una sola.

        Una hoja que el árbol ya no publica **se omite**, y eso no es tragarse un fallo: significa
        que su id ha caducado, que es justo lo que `check_leaves()` sí sabe decir y con el veredicto
        que corresponde. Aquí inventarle una cadena sería peor — la marcaría como ingerida.
        """
        arbol = {ruta.rsplit("/", 1)[-1]: ruta for ruta in self._cadenas()}
        return [
            arbol[str(cat.category_id)] for cat in self._categories if str(cat.category_id) in arbol
        ]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCategoryTree`. La cadena de ids se anida con `/`.

        Es nuestro, no de la tienda: sus ids son opacos y no se anidan solos (ver
        `parse_category_tree`). Da igual cuál sea mientras no aparezca dentro de un id, y un id de
        Zara es siempre un número.
        """
        return "/"

    def _cadenas(self) -> list[str]:
        """Las rutas del árbol infantil completo, cacheadas por instancia."""
        if self._rutas is None:
            self._rutas = [n.path for n in self.category_tree(str(_RAIZ_NINOS))]
        return self._rutas

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas (ver `stores.base.SupportsLeafHealth`).

        Pide el mismo listado que la pasada: Zara no tiene un endpoint más barato para preguntar
        "¿existe esta categoría?", y el árbol de `/categories` no sirve porque el id retirado
        simplemente deja de estar en él sin decir cuál lo sustituye.
        """
        with self._client() as client:
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                leaf = str(cat.category_id)
                try:
                    self._get_json(client, _CATEGORY_URL.format(cat_id=cat.category_id))
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    alive = False if status in GONE_STATUS else None
                    yield LeafHealth(scope, leaf, alive, f"HTTP {status}")
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, leaf, None, type(exc).__name__)
                else:
                    yield LeafHealth(scope, leaf, True, "HTTP 200")

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
