"""Scraper de Sfera (niños): ropa y calzado infantil.

Sfera (grupo El Corte Inglés) corre sobre Salesforce Commerce Cloud (PWA "Moonshine") y
protege sus APIs con **Akamai Bot Manager**: el HTML de documento pasa con buenas cabeceras,
pero el listado/paginación exige cookies que solo se obtienen ejecutando el sensor JS. Por
eso este scraper usa un **navegador headless** (ver `stores/browser.py`): navega **una vez por
pasada** a la página de categoría (siembra cookies) y luego pide la API de listado
`firefly/products_list`. Esa navegación de siembra es la **única** que hace la pasada — todo lo
demás, listado y PDP incluidas, va por `page.request` sin ejecutar JS (#168):

  GET /es/api/sfera-es/firefly/products_list/{category_path}/{page}/?showDimensions=none
  -> {"success": true, "data": {"products": [...], "pagination": {"_current","_total",...}}}

`showDimensions` tiene dos usos y por eso hay dos URLs sobre el mismo endpoint:

  - **`none` para ingerir** (`_FIREFLY_URL`): el payload va sin facetas, que es todo lo que la
    pasada necesita.
  - **`all` para el reconocimiento** (`_FIREFLY_TREE_URL`): puebla `data.filters._menubar` con las
    seis facetas de la web (Talla, Color, Tipo de producto, Precios, % Descuento y el árbol de
    **Categorías**). Ninguna sirve para clasificar barefoot —eso ya se midió en #33— pero la de
    Categorías es la **única fuente fiable de qué hojas existen**, y en esta tienda eso no es un
    lujo: una ruta inventada no da 404, devuelve el catálogo del padre. Ver `parse_category_tree`.

Ojo con `data.filters._filters`, que sí viene siempre en la respuesta: está **vacío con cualquier
valor del parámetro** y no es el sitio donde mirar (#33).

El listado ya trae el detalle completo (colores + tallas + precios + **foto**), así que **no
hay 2ª petición por producto**: `list_catalog()` recorre y cachea los productos, y
`fetch_details()` los devuelve desde caché (respetando el "detalle condicional" de la
ingesta vía la huella).

Para la **confirmación activa** antes de dar de baja (`probe_alive`) hay dos señales, de más
barata a más concluyente: el endpoint de stock por id (`firefly/stock`, JSON) prueba que el
producto sigue comprable si lo lista en `data.ADD`; y si no, la PDP resuelve la duda, porque
Sfera enruta por id y devuelve **404** para un id que ya no existe (el slug de la URL da igual:
redirige al canónico). Un producto agotado pero vivo sale de `ADD` y su PDP responde 200.

Ese último caso es `ProbeVerdict.UNBUYABLE` desde #426, no `ALIVE`: las dos señales se CRUZAN en
vez de que la segunda anule a la primera. Antes, un agotado con ficha viva se rescataba y se
quedaba en el catálogo indefinidamente con su último precio rebajado. Y el matiz que hace que esto
sea correcto y no una fuente de ruido: `data.ADD` se lee en TRES estados (`stock_verdict()`), así
que «la tienda dice que no queda» y «la tienda no ha contestado» no se confunden — solo el primero
emite `UNBUYABLE`.

**Una ruta de categoría que no existe NO da 404**: Sfera devuelve 200 con el catálogo del
*padre* (`ninos/nina/loquesea` -> las 30 páginas de `ninos/nina`). Como las dos redes de
seguridad para hojas muertas se apoyan en el 404 (`GONE_STATUS`), aquí hay que detectarlo por
otra vía: `is_mirage()` compara los ids de la 1ª página de la hoja con los del padre.
Ver `parent_path()` y la issue #54.

Id estable de producto: `id` (p.ej. "A200974138"). Id estable de variante: el `sku` de la
talla (p.ej. "001015811718640004"). Las funciones `parse_*` son puras (JSON -> dataclasses) y
se testean con fixtures capturados de la API real.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from ..barefoot import classify as classify_barefoot
from ..config import Config
from ..tags import TAG_DEPORTIVA
from .base import (
    GONE_STATUS,
    CategoryNode,
    DelistCandidate,
    FiltroDeHoja,
    LeafHealth,
    ListingEntry,
    ProbeVerdict,
    ProductTags,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)
from .browser import BrowserHTTPError, BrowserSession

_LOG = logging.getLogger(__name__)

SLUG = "sfera"  # a nivel de módulo porque las funciones puras de parseo también lo necesitan
BASE_ROOT = "https://www.sfera.com/es"
BASE_URL = BASE_ROOT + "/"
_SEED_URL = BASE_ROOT + "/{category_path}/"
_FIREFLY_URL = (
    BASE_ROOT + "/api/sfera-es/firefly/products_list/{category_path}/{page}/?showDimensions=none"
)
# Stock por id de producto: sondeo barato (JSON, sin renderizar) para la confirmación activa.
_STOCK_URL = BASE_ROOT + "/api/sfera-es/firefly/stock/2/?products={product_id}"
# El MISMO endpoint de listado, pero con las facetas puestas: es la única vía fiable para saber
# qué categorías existen de verdad (ver `parse_category_tree`). No se usa para ingerir — el
# payload es más gordo y `_menubar` no aporta nada a los productos.
_FIREFLY_TREE_URL = (
    BASE_ROOT + "/api/sfera-es/firefly/products_list/{category_path}/1/?showDimensions=all"
)

# Tope de recursión al bajar por el árbol: `ninos` tiene 2 niveles y el guarda evita que un
# `has_children` mal puesto (o un ciclo) convierta un recon en una tormenta de peticiones.
_MAX_TREE_DEPTH = 4

# Tope de guarda por si `_total` viniera anómalo (evita un bucle desbocado).
_MAX_PAGES = 200

# Tope de fotos que guardamos por color (mismo criterio que en Zara: una galería de catálogo).
_MAX_IMAGES_PER_COLOR = 8


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una categoría-hoja de Sfera a nuestro dominio (género/sección/categoría).

    `category_path` es el segmento de URL tras `/es/` (p.ej. "ninos/nina/pantalones"), que
    alimenta tanto la navegación de siembra como el endpoint firefly de listado.

    `tags` son los ejes transversales que la hoja declara además de su categoría (#180). Aquí no
    cuestan ni una petición: las cuatro `ropa-deportiva` ya se listan, así que marcarlas es leer lo
    que ya pasa por delante. Una hoja puede tener categoría Y eje — de hecho es lo normal, porque
    justamente el eje no sustituye a la categoría.
    """

    category_path: str
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # pantalones | camisetas | sudaderas | vestidos | ropa-interior | zapatos
    tags: tuple[str, ...] = ()  # ejes transversales de `scraper.tags`
    # Solo en las hojas que mezclan vocabulario (#200). Ver `FiltroDeHoja` y `_CONJUNTO_O_SUDADERA`.
    #
    # Ojo a la pareja que forma con `tags`, porque las dos las estrenó `ropa-deportiva` con una
    # semana de diferencia y dicen cosas distintas: el eje (#180) vale para TODA la hoja y no
    # sustituye a la categoría; el filtro parte la hoja en dos categorías. Un conjunto de esa hoja
    # es `conjuntos` **y** `deportiva`.
    filtro: FiltroDeHoja | None = None


# La única hoja de esta tienda que trae dos cosas: `ropa-deportiva` son 66 sudaderas y 25 conjuntos,
# y la propia tienda lo publica en su faceta `attr.fashion_level3` («Sudaderas sin capucha 56 ·
# Conjuntos 25 · Sudaderas con capucha 10», medido el 04/08/2026).
#
# **Se usa el nombre y no la faceta**, aunque la faceta sea el dato más limpio: el listado firefly
# no trae `attr` por producto (comprobado sobre el fixture, está en el ADR), así que filtrar por
# faceta costaría **una petición más por hoja** — cuatro por pasada para mover 25 prendas. Los
# títulos dicen exactamente lo mismo que la faceta: 66 empiezan por «Sudadera» y 25 por «Conjunto».
#
# Aquí el filtro sí lleva `resto`: lo que no case sigue entrando como `sudaderas`, que es lo que
# ya hacía la hoja entera desde #175. Esta hoja no rescata prendas, las re-etiqueta.
_CONJUNTO_O_SUDADERA = FiltroDeHoja(re.compile(r"\AConjunto\b", re.IGNORECASE), resto="sudaderas")


# Subconjunto curado (Fase 2): niño/niña, ropa vs zapatería y las categorías del brief
# (pantalones, camisetas, sudaderas/jerseys, vestidos, ropa interior) + calzado. Ampliable
# añadiendo entradas (el resto del código no cambia). Slugs de Sfera verificados en la API.
CATEGORIES: list[CategoryConfig] = [
    # --- «ropa deportiva»: 66 SUDADERAS y 25 CONJUNTOS, repartidos por el filtro (#175, #200) ---
    # El nombre de la hoja engaña y por eso llevaba fuera desde el principio: aquí no hay ni una
    # malla ni una camiseta técnica. Medido el 04/08/2026 sobre las cuatro hojas, 91 productos, con
    # la faceta `attr.fashion_level3` («Tipo de producto») que la propia tienda publica:
    #
    #     Sudaderas sin capucha 56 · Conjuntos 25 · Sudaderas con capucha 10
    #
    # y los títulos dicen lo mismo: 66 empiezan por «Sudadera» y 25 por «Conjunto». Hasta #200 la
    # hoja entraba entera como `sudaderas` y los 25 conjuntos entraban con ellas, porque separarlos
    # exigía pedir la hoja filtrada por la faceta —una petición más por hoja—. El nombre da lo mismo
    # gratis, así que ahora `_CONJUNTO_O_SUDADERA` reparte la hoja en dos.
    #
    # **Ya no van al final, y el porqué del cambio importa.** Estaban las últimas para que los 47
    # productos que también entran por `ninos/{nina,nino}/sudaderas` conservaran aquella hoja y
    # ningún producto vivo cambiara de ámbito. Con el filtro eso deja de ser un motivo: el `resto`
    # de esta hoja ES `sudaderas`, así que esos 47 siguen igual venga por donde venga. Delante,
    # cambian de categoría solo los conjuntos, que es lo que #200 pide, y ni uno más — medido el
    # 06/08/2026 sobre la pasada entera: **28 conjuntos, de los que solo 9 estaban en `sudaderas`**,
    # y 50 modelos que no entraban por ninguna otra hoja.
    #
    # Ojo si algún día esta hoja engorda: el criterio de delante/detrás es el TAMAÑO, y en H&M las
    # suyas van detrás justo por eso (ver el final de su `CATEGORIES`).
    #
    # Lo que aportan además son los **44 exclusivos**: 32 de bebé, donde `bebe-nino` no tiene hoja
    # de sudaderas desde #151 y `bebe-nina` solo tiene `punto-y-jerseis` (9).
    #
    # Y son también la fuente del eje `deportiva` (#180), que convive con el filtro sin estorbarlo
    # porque dicen cosas distintas: el eje es de la hoja entera —lo que la tienda publica en su
    # cajón de deporte— y el filtro reparte esa hoja en dos categorías. Un chándal de aquí sale
    # `conjuntos` **y** `deportiva`; una sudadera, `sudaderas` y `deportiva`.
    #
    # `tags` y `filtro` van con NOMBRE y no por posición a propósito: los dos son el quinto y sexto
    # campo de `CategoryConfig`, los estrenaron estas mismas cuatro hojas con una semana de
    # diferencia (#180 y #200), y pasarlos por posición es exactamente cómo se cuela un filtro
    # dentro de `tags` sin que nada proteste.
    CategoryConfig(
        "ninos/nina/ropa-deportiva",
        "niña",
        "ropa",
        "conjuntos",
        tags=(TAG_DEPORTIVA,),
        filtro=_CONJUNTO_O_SUDADERA,
    ),
    CategoryConfig(
        "ninos/nino/ropa-deportiva",
        "niño",
        "ropa",
        "conjuntos",
        tags=(TAG_DEPORTIVA,),
        filtro=_CONJUNTO_O_SUDADERA,
    ),
    CategoryConfig(
        "ninos/bebe-nina/ropa-deportiva",
        "niña",
        "ropa",
        "conjuntos",
        tags=(TAG_DEPORTIVA,),
        filtro=_CONJUNTO_O_SUDADERA,
    ),
    CategoryConfig(
        "ninos/bebe-nino/ropa-deportiva",
        "niño",
        "ropa",
        "conjuntos",
        tags=(TAG_DEPORTIVA,),
        filtro=_CONJUNTO_O_SUDADERA,
    ),
    # --- niña ---
    CategoryConfig("ninos/nina/pantalones", "niña", "ropa", "pantalones"),
    # Las tres siguientes salieron de enumerar el árbol con `--tree` (#72), no de adivinar. Son
    # `pantalones` del brief y llevaban fuera desde el principio por lo mismo que las de bebé en
    # #56: `CATEGORIES` se escribió desde las categorías del brief buscando el slug que pegara, y
    # aquí probar rutas a ojo no delata el error (una ruta inventada devuelve el catálogo del
    # padre, #54). `vaqueros` es la que más cantaba: en niño SÍ estaba mapeada, así que el catálogo
    # tenía un sesgo de género que no responde a nada de la tienda — era un olvido nuestro.
    #
    # Van DESPUÉS de `pantalones` a propósito: `list_catalog()` deduplica con «gana la primera»,
    # así que un producto que aparezca en dos hojas conserva el mapeo que ya tenía. Medido el
    # 02/08/2026 sobre las SIETE hojas de `pantalones` (las cuatro nuevas y las tres que ya
    # estaban): 125 ids, **0 en más de una hoja**. Y las cuatro aportan 9 + 18 + 13 + 15 = 55
    # productos, que es exactamente el delta del `--dry-run` (493 → 548, 2471 → 2711 variantes),
    # así que tampoco chocan con el resto del catálogo.
    CategoryConfig("ninos/nina/vaqueros", "niña", "ropa", "pantalones"),
    CategoryConfig("ninos/nina/leggings", "niña", "ropa", "pantalones"),
    CategoryConfig("ninos/nina/shorts-y-bermudas", "niña", "ropa", "pantalones"),
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
    CategoryConfig("ninos/nino/shorts-y-bermudas", "niño", "ropa", "pantalones"),
    CategoryConfig("ninos/nino/camisetas-y-polos", "niño", "ropa", "camisetas"),
    CategoryConfig("ninos/nino/camisas", "niño", "ropa", "camisetas"),
    CategoryConfig("ninos/nino/sudaderas", "niño", "ropa", "sudaderas"),
    CategoryConfig("ninos/nino/punto-y-jerseis", "niño", "ropa", "sudaderas"),
    CategoryConfig("ninos/nino/pijamas-y-calcetines", "niño", "ropa", "ropa-interior"),
    CategoryConfig("ninos/nino/zapatos", "niño", "zapateria", "zapatos"),
    # --- calzado del rango bebé (#33) ---
    # Sfera separa los rangos de edad en ramas distintas, igual que Zara (#17), y **ahí es donde
    # está su barefoot**: de los 6 productos que lo dicen en el nombre, 5 cuelgan de estas dos
    # hojas. Se mapean al mismo género que su rama de 6-14, que es el vocabulario del catálogo.
    #
    # Solo entra el calzado, que es el alcance de #33; la ropa del mismo rango va justo debajo.
    CategoryConfig("ninos/bebe-nina/zapatos", "niña", "zapateria", "zapatos"),
    CategoryConfig("ninos/bebe-nino/zapatos", "niño", "zapateria", "zapatos"),
    # --- ropa del rango bebé (#56) ---
    # Estas doce hojas NO se pueden derivar copiando las rutas de `nina`/`nino`: el árbol de Sfera
    # no es simétrico entre rangos, y el nombre cambia justo en las categorías gordas —donde 6-14
    # dice `pantalones` y `leggings` por separado, bebé dice `pantalones-y-leggings`; donde dice
    # `camisetas` + `camisas-y-blusas`, bebé dice `blusas-y-camisetas`—. Salen de enumerar la
    # faceta de categorías con `--tree` (ver `parse_category_tree`), que es la única vía fiable:
    # probar rutas a ojo devuelve 200 con el catálogo del padre, no un 404 (#54, `is_mirage`).
    #
    # Se mapean a la misma categoría que su equivalente de 6-14 para que el vocabulario del
    # catálogo no se parta por rango de edad, y ninguna estrena ámbito: todos los `ScrapeScope`
    # que producen ya existían, así que la superficie de bajas no cambia.
    CategoryConfig("ninos/bebe-nina/pantalones-y-leggings", "niña", "ropa", "pantalones"),
    CategoryConfig("ninos/bebe-nina/shorts-y-bermudas", "niña", "ropa", "pantalones"),
    CategoryConfig("ninos/bebe-nina/blusas-y-camisetas", "niña", "ropa", "camisetas"),
    CategoryConfig("ninos/bebe-nina/punto-y-jerseis", "niña", "ropa", "sudaderas"),
    CategoryConfig("ninos/bebe-nina/vestidos-y-faldas", "niña", "ropa", "vestidos"),
    CategoryConfig("ninos/bebe-nina/accesorios-y-pijamas", "niña", "ropa", "ropa-interior"),
    CategoryConfig("ninos/bebe-nino/pantalones-y-monos", "niño", "ropa", "pantalones"),
    CategoryConfig("ninos/bebe-nino/bermudas-y-petos", "niño", "ropa", "pantalones"),
    CategoryConfig("ninos/bebe-nino/camisetas", "niño", "ropa", "camisetas"),
    CategoryConfig("ninos/bebe-nino/camisas", "niño", "ropa", "camisetas"),
    # `ninos/bebe-nino/punto-y-jerseis` VA Y VIENE, y esa es la lección: no es una hoja que la
    # tienda retirase, es una categoría de temporada. #151 la quitó de aquí al medir que se había
    # ido entre el 24/07 y el 02/08/2026 —`--tree ninos/bebe-nino` publicaba ocho categorías y
    # ninguna era esa, mientras `bebe-nina` SÍ la seguía publicando— y volvió antes del 05/08.
    # Se reingiere en #212 con 4 prendas, ya creciendo (eran 3 doce horas antes).
    #
    # Quien la vuelva a ver desaparecer: NO la borre otra vez. Cuando se fue se dio por hecho
    # además que no había sustituta —«lo que la rama publica y no ingerimos es `ropa-deportiva` y
    # `abrigos-y-cazadoras`, que no son `sudaderas`»— y **eso era una suposición que #175 midió
    # falsa**: `bebe-nino/ropa-deportiva` son 18 productos que la propia tienda etiqueta
    # «Sudaderas sin capucha» (14) y «Conjuntos» (4) en su faceta `attr.fashion_level3`. Sigue
    # mapeada a la cabeza de esta lista con su filtro, y las dos conviven igual que en `bebe-nina`.
    #
    # Que su ida y vuelta se note ya no depende de `--tree` a mano: `revisar_cobertura` (#156)
    # compara lo publicado con lo mapeado cada jueves, y es quien cantó el regreso.
    CategoryConfig("ninos/bebe-nino/punto-y-jerseis", "niño", "ropa", "sudaderas"),
    CategoryConfig("ninos/bebe-nino/accesorios-y-pijamas", "niño", "ropa", "ropa-interior"),
    # `accesorios-y-pijamas` es la única del bloque que entra sucia: mezcla los pijamas (que sí son
    # ropa interior, igual que en `pijamas-y-calcetines` de 6-14) con gorros y baberos, que no lo
    # son. Entra porque son 8 productos entre los dos géneros y dejarla fuera sería no tener NINGUNA
    # ropa interior de bebé, que es una de las cinco categorías del brief. Si algún día la hoja
    # engorda, lo que toca es filtrar por `attr.fashion_level3`, no seguir tragándola entera.
    #
    # Se quedan FUERA a propósito, por no ser ninguna de las cinco del brief y porque su equivalente
    # de 6-14 tampoco se mapea: `bano` / `banadores-bebe` y `abrigos-y-cazadoras`.
    #
    # **Escribirlo aquí no basta y eso costó #212**: lo comprobable es
    # `vigia.COBERTURA_DECLARADA["sfera"]`, y esta prosa decía «`bano` / `banadores-bebe`» mientras
    # aquella lista solo tenía las TRES ramas que se llaman `bano`. La cuarta, la del slug
    # asimétrico, cantó como hueco cada jueves hasta que se declaró. Al tocar esta lista, tócala
    # también allí.
    #
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


def _source_url(image: Any) -> str | None:
    """URL aprovechable de un objeto imagen de Sfera (`{ratio, sources: {small|medium|big|zoom}}`).

    Se prefiere `big` (516x640, ~16 KB) porque el CDN de El Corte Inglés **ignora** el `&w=` que
    sí acepta Zara —el tamaño va en su propio `impolicy=Resize&width=...`—, así que el ancho que
    se guarda aquí es el definitivo, y `big` es el que encaja con la tarjeta (`zoom` son 64 KB).
    `default_source` NO vale: es el marcador `no-image.png` de la tienda, y para eso preferimos
    nuestro placeholder; lo filtra `_usable_image`.
    """
    if not isinstance(image, dict):
        return str(image) if _usable_image(image) else None
    sources = image.get("sources")
    if isinstance(sources, dict):
        for key in ("big", "medium", "small"):
            if _usable_image(sources.get(key)):
                return str(sources[key])
    if _usable_image(image.get("default_source")):
        return str(image["default_source"])
    return None


def _color_image_urls(color: dict[str, Any]) -> list[str]:
    """URLs de las fotos de UN color, en el orden que las da la tienda.

    Vienen en `all_images` del propio listado firefly: **cero peticiones nuevas**. `thumbnail_url`
    queda fuera a propósito — es la muestra de color (un png de la carta), no una foto de la
    prenda. Respaldo a `image` (URL plana) para un color que no traiga `all_images`.
    """
    urls: list[str] = []
    all_images = color.get("all_images")
    if isinstance(all_images, list):
        for entry in all_images:
            url = _source_url(entry)
            if url:
                urls.append(url)
            if len(urls) == _MAX_IMAGES_PER_COLOR:
                break
    if not urls:
        fallback = _source_url(color.get("image"))
        if fallback:
            urls.append(fallback)
    return urls


def _primary_image(product: dict[str, Any]) -> str | None:
    """Foto que la propia tienda elige para su tarjeta. Respaldo cuando no hay galería."""
    direct = _source_url(product.get("image"))
    if direct:
        return direct
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


class LeafMirage(RuntimeError):
    """La ruta pedida no existe: Sfera ha servido el catálogo del padre en su lugar.

    No es un error de red ni de parseo — la respuesta es un 200 perfectamente válido. Es el
    equivalente al 404 que esta tienda no da, y por eso se trata igual: la hoja se cuenta como
    caída (`ScanReport.leaf_gone`) en vez de ingerir el catálogo de otro ámbito.
    """


def parent_path(category_path: str) -> str | None:
    """Ruta del padre de una hoja (`ninos/nina/zapatos` -> `ninos/nina`).

    `None` cuando no hay contra qué comparar: una ruta de menos de tres segmentos ya es una raíz
    de género, y su "padre" sería la tienda entera. Comparar contra eso no distingue nada y
    costaría una petición, así que esas rutas se quedan sin esta red (todas las hojas curadas de
    `CATEGORIES` tienen tres segmentos).
    """
    partes = [p for p in category_path.strip("/").split("/") if p]
    if len(partes) < 3:
        return None
    return "/".join(partes[:-1])


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


def page_product_ids(payload: dict[str, Any]) -> list[str]:
    """Ids de producto de una página firefly, en el orden que los da la tienda."""
    return [str(p["id"]) for p in products_of(payload) if p.get("id")]


def is_mirage(leaf: dict[str, Any], parent: dict[str, Any]) -> bool:
    """¿La respuesta de la hoja es en realidad el catálogo del padre? (ver `LeafMirage`)

    Se comparan **los ids de la 1ª página y el número total de páginas**, no `data.title`: el
    título es texto de presentación y localizado (`Niña (Niños) | Sfera España`), mientras que los
    ids son el contrato. Pedir además que coincida el total de páginas aleja el falso positivo de
    una hoja real que casualmente empezara por los mismos productos que su padre.

    Queda un caso que NADIE puede distinguir ni en principio: una hoja que contuviera exactamente
    todo el catálogo de su padre. Se acepta a sabiendas, porque el error cae del lado seguro — la
    hoja se cuenta como caída, lo que suspende las bajas de su ámbito, en vez de ingerir productos
    con el género/sección/categoría equivocados.
    """
    ids = page_product_ids(leaf)
    if not ids:
        return False  # una página vacía no prueba nada; de eso ya se ocupa `_iter_category`
    if ids != page_product_ids(parent):
        return False
    return pagination_of(leaf).get("_total") == pagination_of(parent).get("_total")


def _categories_facet(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Valores de la faceta `Categorías` dentro de `data.filters._menubar` (vacío si no viene)."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    filters = data.get("filters")
    if not isinstance(filters, dict):
        return []
    menubar = filters.get("_menubar")
    if not isinstance(menubar, list):
        return []
    for faceta in menubar:
        if isinstance(faceta, dict) and faceta.get("type") == "categories":
            valores = faceta.get("values")
            if not isinstance(valores, list):
                return []
            return [v for v in valores if isinstance(v, dict)]
    return []


def parse_category_tree(payload: dict[str, Any], root: str) -> list[CategoryNode]:
    """Categorías que la tienda publica **por debajo de `root`**, leídas de una respuesta firefly.

    Exige `showDimensions=all` (`_FIREFLY_TREE_URL`): con el `none` que usa la ingesta, `_menubar`
    viene lista vacía y esto devuelve `[]` sin quejarse. No es un error — es que no se ha pedido.

    Dos comportamientos que solo se ven pidiéndolo de verdad, y que son la razón de que esto no
    sea un `for` de tres líneas:

    - **`values` no siempre son los hijos.** Para una categoría SIN descendencia (`ninos/mini`) la
      faceta responde con el rastro de **ancestros** («Sfera España», «Niños»), no con hijos. Por
      eso se filtra a descendientes estrictos de `root`: una hoja devuelve `[]`, que es la
      respuesta honesta a «qué cuelga de aquí», en vez de un árbol que apunta hacia arriba.
    - **El nodo raíz de la tienda no trae `slugs` ni `link`**, solo `label` («Sfera España»). Sin
      ruta no hay nada que pedir ni que mapear, así que se descarta.

    El `count` se respeta como lo da la tienda y `None` significa «no lo dice», que **no** es lo
    mismo que 0: una hoja real vacía es una decisión de cobertura distinta a una sin dato.
    """
    raiz = [p for p in root.strip("/").split("/") if p]
    nodos: list[CategoryNode] = []
    for valor in _categories_facet(payload):
        slugs = valor.get("slugs")
        if not isinstance(slugs, list) or not all(isinstance(s, str) and s for s in slugs):
            continue  # el nodo raíz de la tienda no tiene ruta
        if len(slugs) <= len(raiz) or slugs[: len(raiz)] != raiz:
            continue  # ancestro, hermano, o `root` mismo: no cuelga de lo que se ha pedido
        count = valor.get("count")
        nodos.append(
            CategoryNode(
                path="/".join(slugs),
                title=str(valor.get("label") or ""),
                # `bool` es subclase de `int`, así que un `True` colado pasaría por un count de 1.
                count=count if isinstance(count, int) and not isinstance(count, bool) else None,
                depth=len(slugs) - len(raiz),
                # Estricto a propósito: la comparación con `True` no se deja engañar por el
                # `"False"` en texto que devuelven otras APIs de esta misma casa.
                has_children=valor.get("has_children") is True,
            )
        )
    return nodos


def parse_products(products: list[dict[str, Any]], cat: CategoryConfig) -> list[ScrapedProduct]:
    """Convierte la lista de productos firefly en ScrapedProduct (ignora los sin variantes)."""
    out: list[ScrapedProduct] = []
    for product in products:
        pid = product.get("id")
        if not pid:
            continue
        url = _product_url(product)
        # Variantes e imágenes, en la MISMA pasada por `_my_colors` y leyendo el nombre del color
        # de un único sitio (`color["title"]`): es la clave con la que la ficha empareja foto y
        # precio, y sacarla en dos recorridos distintos es justo como se desalinean.
        variants: list[ScrapedVariant] = []
        images: list[ScrapedImage] = []
        for color in product.get("_my_colors", []):
            if color.get("hideColor"):
                continue  # color oculto por la tienda: no lo registramos
            color_name = color.get("title")
            variants_before = len(variants)
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
            if len(variants) == variants_before:
                continue  # color sin ninguna talla con precio: sus fotos quedarían huérfanas
            images.extend(ScrapedImage(color=color_name, url=u) for u in _color_image_urls(color))
        if not variants:
            continue
        nombre = product.get("title", "")
        # La hoja puede traer dos cosas y el nombre dice cuál es cada una (#200). Sin filtro,
        # `categoria` es la de la hoja y esto no cambia nada.
        categoria = (
            cat.filtro.categoria(nombre, propia=cat.category) if cat.filtro else cat.category
        )
        if categoria is None:
            continue
        out.append(
            ScrapedProduct(
                retailer_product_id=str(pid),
                name=nombre,
                gender=cat.gender,
                section=cat.section,
                category=categoria,
                url=url,
                variants=variants,
                # Sfera no etiqueta el barefoot en su árbol de categorías —a diferencia de Zara y
                # Lefties— pero **sí lo dice en el nombre del producto** ("Zapatilla runner
                # barefoot", "Merceditas basic barefoot"): 6 de sus 51 zapatos, medidos el
                # 31/07/2026. Así que aquí la clasificación va por la heurística de texto, que es
                # el plan B para el que existe, y no por la categoría.
                #
                # Lo que NO tiene es faceta: `data.filters._filters` viene vacío con cualquier
                # valor de `showDimensions`, y de las 6 facetas reales (en `_menubar`) ninguna
                # habla de calzado respetuoso. Comprobado en vivo, ver #33. El resto de su calzado
                # se queda en `desconocido`, que es justo para lo que existe ese estado y no una
                # carencia que haya que tapar inventando un `si`.
                barefoot=classify_barefoot(
                    retailer=SLUG,
                    retailer_product_id=str(pid),
                    section=cat.section,
                    category=categoria,
                    texts=nombre,
                ),
                # Se prefiere la galería para que la foto de tarjeta sea de un color conocido; si
                # esta pasada no trae galería (fixture antiguo sin media), la elección de la tienda.
                image_url=images[0].url if images else _primary_image(product),
                images=images,
            )
        )
    return out


def stock_verdict(payload: Any, product_id: str) -> bool | None:
    """¿El stock declara el producto comprable? `True` sí, `False` no, `None` no lo dice.

    Sustituye a `stock_lists_available()`, que devolvía un `bool` y por tanto **colapsaba dos cosas
    distintas en el mismo `False`**: «la tienda contesta que no queda nada» y «la petición se cayó
    o el JSON vino con otra forma». Mientras el único uso era confirmar vida daba igual —las dos
    llevaban a mirar la PDP—, pero desde #426 ese `False` decide si se emite `UNBUYABLE`, y sobre
    la señal vieja eso convertiría un fallo de red en un «agotado». Ese veredicto alimenta
    contadores y, desde #427, una alarma: inflarlo con fallos nuestros es exactamente cómo se
    construye una alarma que nadie puede creerse.

    `None` es la respuesta honesta a «no lo sé», y quien llama decide qué hacer con ella. En
    `_probe_one` lo que se hace es seguir dando el producto por vivo, que es lo conservador.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    add = data.get("ADD")
    if not isinstance(add, list):
        return None
    return product_id in add


def product_signature(product: ScrapedProduct) -> str:
    """Huella barata del producto: precio efectivo por variante (ordenada y estable)."""
    return "|".join(sorted(f"{v.retailer_variant_id}:{v.price}" for v in product.variants))


class SferaStore:
    """Scraper de Sfera (vía navegador headless). Implementa el Protocol BaseStore."""

    slug = SLUG
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
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._tags = ProductTags()  # ídem; ver `product_tags()`
        # 1ª página de cada ruta padre, cacheada durante la pasada: la comparten todas las hojas
        # que cuelgan de ella, así que la detección de espejismo cuesta UNA petición por padre
        # (dos con las categorías de hoy), no una por hoja.
        self._parent_pages: dict[str, dict[str, Any]] = {}

    def scopes(self) -> Iterable[ScrapeScope]:
        """Los ámbitos que esta tienda recorre, **incluido el `resto` de las hojas con filtro**.

        Lo segundo no es redundante aunque hoy lo parezca: `ropa-deportiva` declara `conjuntos` y
        manda su resto a `sudaderas`, que da la casualidad de que otras hojas ya declaran. Si algún
        día no fuera así, ese ámbito no contaría como escaneado y sus productos **no se
        descatalogarían nunca** — el mismo agujero que `con_unisex()` tapa en H&M y Cacles.
        """
        seen: list[ScrapeScope] = []
        for cat in self._categories:
            candidatos = [ScrapeScope(cat.gender, cat.section, cat.category)]
            if cat.filtro is not None and cat.filtro.resto is not None:
                candidatos.append(ScrapeScope(cat.gender, cat.section, cat.filtro.resto))
            for scope in candidatos:
                if scope not in seen:
                    seen.append(scope)
        return seen

    def _parent_page(self, session: BrowserSession, category_path: str) -> dict[str, Any] | None:
        """1ª página del padre de una hoja, cacheada por pasada. `None` si no hay padre útil.

        Si la petición del PADRE fallara, el error sube y lo trata quien llama como si fuera de la
        hoja. Es lo correcto aunque suene tosco: los padres son las raíces de género, y que una
        deje de responder afecta a todas sus hojas — con lo que `SCRAPER_SCAN_MAX_DEAD_RATIO`
        aborta la pasada en vez de guardar un catálogo a medias, que es justo lo que se quiere.
        """
        padre = parent_path(category_path)
        if padre is None:
            return None
        if padre not in self._parent_pages:
            self._parent_pages[padre] = session.get_json(
                _FIREFLY_URL.format(category_path=padre, page=1)
            )
        return self._parent_pages[padre]

    def _check_not_mirage(
        self, session: BrowserSession, category_path: str, payload: dict[str, Any]
    ) -> None:
        """Lanza `LeafMirage` si la hoja está devolviendo el catálogo de su padre (#54)."""
        parent = self._parent_page(session, category_path)
        if parent is not None and is_mirage(payload, parent):
            raise LeafMirage(
                f"{category_path} devuelve el catálogo de {parent_path(category_path)}"
            )

    def _iter_category(
        self, session: BrowserSession, cat: CategoryConfig
    ) -> Iterable[ScrapedProduct]:
        """Recorre todas las páginas firefly de una categoría y produce sus productos.

        No siembra: las cookies vienen de la única navegación que hace `list_catalog` antes del
        bucle. Sembrar aquí era una navegación por hoja —38 por pasada, cada una con el render
        completo de una página de escaparate— para conseguir lo que ya estaba conseguido (#168).
        """
        page = 1
        total = 1
        while page <= total and page <= _MAX_PAGES:
            payload = session.get_json(
                _FIREFLY_URL.format(category_path=cat.category_path, page=page)
            )
            if page == 1:
                # ANTES de emitir nada: una ruta que ya no existe devuelve 200 con el catálogo
                # del padre, y ingerirlo etiquetaría cientos de productos con este ámbito.
                self._check_not_mirage(session, cat.category_path, payload)
                total = int(pagination_of(payload).get("_total", 1) or 1)
            products = parse_products(products_of(payload), cat)
            if not products:
                break  # página vacía: no seguimos (la red de seguridad de bajas lo cubre)
            yield from products
            page += 1

    def list_catalog(self) -> Iterable[ListingEntry]:
        self._cache = {}
        self._scan = ScanReport()
        self._parent_pages = {}
        self._tags = ProductTags()
        # Optimista y se retira lo que falle, al revés que `_scan`: aquí lo que hay que detectar es
        # la hoja que NO se pudo leer, y si una etiqueta se declarara fiable solo al terminar bien
        # todas sus hojas haría falta llevar la cuenta de cuántas le tocaban. Con este apaño, una
        # sola hoja caída basta para que su eje no se reconcilie, que es la respuesta conservadora.
        self._tags.fiables = {t for cat in self._categories for t in cat.tags}
        with self._session_factory() as session:
            # Una sola siembra para toda la pasada, igual que en `check_leaves` y en `probe_alive`.
            # Akamai contesta 403 a la PRIMERA petición de la sesión y suelta las cookies con esa
            # misma respuesta (#129), así que la navegación sigue siendo obligatoria —sin ella se
            # pierde la primera hoja—, pero basta una: medido el 05/08/2026 contra la tienda, 38/38
            # hojas con payload y productos partiendo de esta única siembra (#168).
            session.goto(self._seed_url())  # siembra las cookies de Akamai del origen
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                filtrados = 0  # productos que ha casado el filtro de esta hoja, si lo lleva
                try:
                    # El `try` envuelve el bucle entero porque `_iter_category` es un generador:
                    # el fallo de una página se ve al tirar de él, no al crearlo. Lo que ya haya
                    # emitido se queda (el dedup por id lo cubre), pero su ámbito deja de ser
                    # seguro para dar bajas, que es lo que importa.
                    for product in self._iter_category(session, cat):
                        if product.category == cat.category:
                            # Se cuenta ANTES del dedup: que un conjunto ya lo hubiera traído otra
                            # hoja no significa que esta haya dejado de rotularlos, que es lo único
                            # que `filtro_vacio` quiere saber.
                            filtrados += 1
                        pid = product.retailer_product_id
                        # ANTES del dedup, y sigue siendo obligatorio aunque #200 haya cambiado
                        # QUIÉN se descarta. Cerca de la mitad de los productos de las hojas de
                        # deporte salen también por `sudaderas` (47 de 91 la última vez que se contó
                        # el reparto; la pasada real del 05/08/2026 marcó 97 en total): antes esas
                        # hojas iban al final y los duplicados se descartaban aquí, y ahora van
                        # delante y los descartados son los de `sudaderas`. En los dos casos la
                        # marca se anota por CADA hoja que ve el producto, así que anotarla antes
                        # del `continue` es lo que la hace del producto y no de la hoja que ganó el
                        # dedup — que es el error del que #180 avisa, y que el orden no arregla.
                        for tag in cat.tags:
                            self._tags.anota(pid, tag)
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
                except BrowserHTTPError as exc:
                    if exc.status not in GONE_STATUS:
                        raise  # bloqueo de Akamai o fallo del servidor: no es una hoja retirada
                    self._hoja_caida(cat, scope)
                    continue
                except LeafMirage:
                    # El 404 que esta tienda no da: la ruta ya no existe (#54). Mismo trato.
                    self._hoja_caida(cat, scope)
                    continue
                except Exception as exc:
                    # Red ancha a propósito (#107): un timeout de navegación —el fallo transitorio
                    # más probable en una tienda que va por navegador— se colaba por aquí y tumbaba
                    # la pasada entera, incluidas las hojas ya leídas. Cualquier fallo que llegue
                    # hasta aquí es «no he podido ver esta hoja», que es justo lo que `leaf_gone`
                    # trata: su ámbito queda fuera de las bajas. Barrer tan ancho es seguro porque
                    # `SCRAPER_SCAN_MAX_DEAD_RATIO` sigue abortando si caen demasiadas — se pierde
                    # una hoja, no el criterio.
                    _LOG.warning(
                        "sfera: hoja %s ilegible (%s: %s)",
                        cat.category_path,
                        type(exc).__name__,
                        exc,
                    )
                    self._hoja_caida(cat, scope)
                    continue
                self._scan.leaf_ok()
                # La hoja se ha listado entera y su `resto` ha entrado con normalidad, pero no ha
                # salido ni un conjunto: o la tienda ha cambiado la rotulación o ya no le quedan.
                # Ver `ScanReport.filtro_vacio()`; solo se afirma sobre el ámbito filtrado.
                if cat.filtro is not None and not filtrados:
                    self._scan.filtro_vacio(scope, cat.category_path)

    def _hoja_caida(self, cat: CategoryConfig, scope: ScrapeScope) -> None:
        """Una hoja que no se ha podido leer: fuera de las bajas, y fuera de la reconciliación.

        Lo segundo es lo nuevo (#180) y es asimétrico con lo primero a propósito. Una hoja caída
        deja su ÁMBITO sin bajas; aquí deja sin reconciliar el EJE entero de la tienda, aunque las
        otras tres hojas de deporte se hayan leído bien. Es más bruto porque el error es peor: la
        marca no tiene histéresis ni sondeo detrás, así que reconciliar con lo que sí se vio
        borraría las de la hoja caída en la misma pasada, sin nada que lo frene.
        """
        self._scan.leaf_gone(scope, leaf=cat.category_path)
        self._tags.fiables -= set(cat.tags)

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def product_tags(self) -> ProductTags:
        """Ver `stores.base.SupportsProductTags` (válido con `list_catalog()` ya consumido)."""
        return self._tags

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas (ver `stores.base.SupportsLeafHealth`).

        Pide solo la **primera página** de cada categoría: basta para saber si la ruta sigue
        existiendo y si devuelve género. Una categoría viva pero vacía cuenta como aviso (`None`,
        sin veredicto): no está retirada, pero tampoco está aportando nada.

        Contar productos NO basta como prueba de vida en esta tienda: una ruta que ya no existe
        responde 200 con el catálogo del padre, y sin la comprobación de espejismo (#54) este
        sondeo informaba «12 productos en la 1ª página» de una categoría inventada.

        La siembra de cookies no es opcional aquí (#129): Akamai contesta 403 a la **primera**
        petición de la sesión y suelta las cookies con esa misma respuesta, así que sin navegar
        antes la primera hoja que se pida —sea cual sea, se comprobó invirtiendo `CATEGORIES`—
        sale sin veredicto. Basta una para todo el sondeo. Con el vigía semanal (#67) eso era un
        falso positivo cada jueves, que es la manera de que un aviso deje de leerse.
        """
        self._parent_pages = {}
        with self._session_factory() as session:
            session.goto(self._seed_url())  # siembra las cookies de Akamai del origen
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                url = _FIREFLY_URL.format(category_path=cat.category_path, page=1)
                try:
                    payload = session.get_json(url)
                    self._check_not_mirage(session, cat.category_path, payload)
                except BrowserHTTPError as exc:
                    alive = False if exc.status in GONE_STATUS else None
                    yield LeafHealth(scope, cat.category_path, alive, f"HTTP {exc.status}")
                    continue
                except LeafMirage:
                    yield LeafHealth(
                        scope,
                        cat.category_path,
                        False,
                        f"espejismo: devuelve el catálogo de {parent_path(cat.category_path)}",
                    )
                    continue
                except Exception as exc:  # navegador caído, timeout, respuesta no-JSON
                    yield LeafHealth(scope, cat.category_path, None, type(exc).__name__)
                    continue
                total = len(products_of(payload))
                yield LeafHealth(
                    scope,
                    cat.category_path,
                    True if total else None,
                    f"{total} productos en la 1ª página",
                )

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las rutas que esta tienda tiene configuradas."""
        return [cat.category_path for cat in self._categories]

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. Las cuatro ramas del mundo infantil.

        No se barre desde `ninos` sino desde sus cuatro hijas porque es donde cuelgan las hojas:
        una petición de más por el nivel intermedio, y a cambio el informe no arrastra los cuatro
        nodos de rama. Medido el 04/08/2026: **46 rutas** entre las cuatro.
        """
        return ["ninos/nina", "ninos/nino", "ninos/bebe-nina", "ninos/bebe-nino"]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCoverageWatch`. Sfera anida sus rutas con `/`."""
        return "/"

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. Una petición por nodo con hijos.

        Baja recursivamente porque la faceta solo publica **un nivel** por respuesta: para saber
        qué hay bajo `ninos` hacen falta también las de `ninos/nina`, `ninos/bebe-nino`… Son 5
        peticiones para el árbol entero de niños, así que no compensa complicarlo.

        Un fallo de red o un 403 de Akamai **propaga**, no se traga: aquí no hay forma de decir
        «esta rama no la pude leer» sin inventarse un nodo. Quien llama es el que decide, y
        `run._tree()` se queda con lo ya emitido y lo dice. El que además tiene que sobrevivir a
        los blips sin ayuda de nadie es `check_leaves()`, que es el vigía.
        """
        with self._session_factory() as session:
            session.goto(_SEED_URL.format(category_path=root))  # siembra cookies de Akamai
            yield from self._tree_from(session, root, 0, {root}, set())

    def _tree_from(
        self,
        session: BrowserSession,
        root: str,
        base_depth: int,
        pedidas: set[str],
        emitidas: set[str],
    ) -> Iterable[CategoryNode]:
        """Recorre `root` y sus descendientes, con la profundidad ya referida a la raíz original.

        Dos conjuntos, porque son dos preguntas distintas: `pedidas` evita repetir una PETICIÓN
        (un `has_children` que apunte hacia atrás sería un bucle infinito) y `emitidas` evita
        repetir un NODO en el informe. Se separan porque una ruta se emite justo antes de que se
        pida, así que compartir conjunto cortaría la bajada en el primer hijo.
        """
        if base_depth >= _MAX_TREE_DEPTH:
            return
        payload = session.get_json(_FIREFLY_TREE_URL.format(category_path=root))
        for nodo in parse_category_tree(payload, root):
            if nodo.path not in emitidas:
                emitidas.add(nodo.path)
                # `parse_category_tree` mide la profundidad contra el `root` que se le pasa, que
                # en la recursión ya no es el que pidió quien llama: se le suma el desplazamiento.
                yield replace(nodo, depth=nodo.depth + base_depth)
            if nodo.has_children and nodo.path not in pedidas:
                pedidas.add(nodo.path)
                yield from self._tree_from(
                    session, nodo.path, base_depth + nodo.depth, pedidas, emitidas
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

    def _probe_one(
        self, session: BrowserSession, candidate: DelistCandidate
    ) -> ProbeVerdict | None:
        """El veredicto del sondeo, o `None` si no hay respuesta utilizable.

        Cruza las DOS señales, que es lo que #426 vino a arreglar: hasta él, un producto agotado
        con la ficha viva salía `ALIVE` y `_rescue()` le ponía la racha a cero, así que se quedaba
        en el catálogo indefinidamente con su último precio rebajado y sin una talla que comprar.
        El CLAUDE.md presentaba a Sfera como la referencia de «dos señales», y era verdad que usaba
        dos — pero ninguna de las dos era stock cuando la primera no confirmaba.

        Ojo al orden de las guardas: `UNBUYABLE` solo se emite cuando el endpoint de stock ha
        CONTESTADO que no queda nada (`stock_verdict()` devuelve `False`). Si no contestó, el
        producto se sigue dando por vivo — un fallo de red no puede disfrazarse de agotado.
        """
        pid = candidate.retailer_product_id
        stock: bool | None = None
        try:
            stock = stock_verdict(session.get_json(_STOCK_URL.format(product_id=pid)), pid)
        except Exception:
            stock = None  # sin atajo: lo resuelve la PDP
        if stock is True:
            return ProbeVerdict.ALIVE  # comprable: vivo seguro, y sin gastar una navegación

        # Agotado y retirado se parecen en el stock, pero no en la PDP: el id retirado da 404.
        if not candidate.url:
            return None
        try:
            # Se PIDE la ficha en vez de navegarla: de ella solo se lee el status, y `pedir_html`
            # lo da igual sin ejecutar JS ni pedir subrecursos (#160). Medido el 05/08/2026 sobre
            # 7 URLs —5 vivas y 2 canarios con el id mutado— y con la sesión sembrada y sin
            # sembrar: 14/14 veredictos idénticos a los de `goto`, con los 404 de los canarios
            # llegando por los dos caminos (#168). Las cookies las siembra `probe_alive`, que es
            # la precondición que este camino no puede darse a sí mismo.
            status, _ = session.pedir_html(candidate.url)
        except Exception:  # timeout / error de red: no es prueba de nada
            return None
        if status in (404, 410):
            return ProbeVerdict.DEAD
        # Otros códigos (403 de Akamai, 5xx) son problema nuestro, no del producto: sin veredicto.
        if status != 200:
            return None
        # 200 = la ficha existe. Que se pueda COMPRAR ya lo contestó (o no) el endpoint de stock:
        # con un `False` explícito es `UNBUYABLE`; sin respuesta suya, `ALIVE` como hasta ahora.
        # `UNBUYABLE` no rescata ni da de baja — que no quede stock hoy no prueba una retirada.
        return ProbeVerdict.UNBUYABLE if stock is False else ProbeVerdict.ALIVE

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, ProbeVerdict]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`)."""
        pending = list(candidates)
        if not pending:
            return {}
        verdicts: dict[str, ProbeVerdict] = {}
        with self._session_factory() as session:
            session.goto(self._seed_url())  # siembra las cookies de Akamai del origen
            for candidate in pending:
                verdict = self._probe_one(session, candidate)
                if verdict is not None:  # sin veredicto -> se omite del mapa
                    verdicts[candidate.retailer_product_id] = verdict
        return verdicts
