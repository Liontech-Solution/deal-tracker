"""Scraper de Springfield (myspringfield.com): la primera tienda que se lista por SITEMAP.

Las otras siete recorren hojas de categoría. Aquí no se puede, y no por una limitación técnica:
el `robots.txt` **veta la rejilla de producto de SFCC y su paginación** (`/*/search?cgid=`,
`/*start=`, `/*sz=`, `/*srule=`, `/*prefn*`, `/on/demandware*`), que es el camino natural para
listar un catálogo en esa plataforma. Rodearlo no es una opción, así que se entra por donde la
tienda invita a entrar:

  - listado:  `SiteMapCustom-Google?name=sitemap_0-index.xml` -> 3 ficheros de producto
  - producto: `…/{slug}/{id}.html` (y `?dwvar_{id}_color={colorID}` para los demás colores)

Cumplimiento (comprobado el 03/08/2026). El mismo `robots.txt` que veta la rejilla trae un
`Allow:` **explícito** para `/on/demandware.store/Sites-SPF-Site/*/SiteMapCustom-Google*`, que gana
al `Disallow: /on/demandware*` por ser la regla más específica, y otro para
`/on/demandware.static*` (el CDN de las fotos). No hay ningún `Disallow` sobre `.html`: las fichas
están permitidas, y `?dwvar_…` no encaja en ninguna de las reglas de query vetadas. Leídos también
el aviso legal y las Condiciones Generales (los dos en PDF): **no hay cláusula alguna** sobre
scraping, robots, crawlers, acceso automatizado ni minería de datos; la §17 va de virus, acceso no
autorizado y denegación de servicio, nada de esto. Y `myspringfield.com` **no declara
`Crawl-delay`** — el `Crawl-Delay: 20` que se citó al abrir #81 es del dominio corporativo hermano
(`springfieldfashion.com`), que es otro sitio.

Cinco cosas que hay que tener presentes al tocar este fichero, todas medidas contra el sitio real:

1. **`lastmod` es la huella, y es la única que hay.** Precio y tallas viven solo en la ficha, así
   que no se puede construir una huella de precio sin pedirla — que es justo lo que la huella
   existe para evitar. Las 12 842 URLs del sitemap traen `lastmod`, así que sirve. **Riesgo abierto
   (#81): si `lastmod` no se moviera al cambiar solo el precio, el detalle condicional congelaría
   los precios.** No se puede comprobar en una sesión (exige ver el mismo producto en dos
   momentos); mientras tanto la red que lo cubre es el refresco periódico forzado
   (`product.last_detail_at` + `SCRAPER_DETAIL_MAX_AGE_DAYS`), que vuelve a pedir la ficha aunque
   la huella no cambie.
2. **La ficha no hay que parsearla, hay que leer sus atributos.** Cada talla es un `<input>` con
   un JSON entero en `size-data` (pid, talla, stock, precio y tachado) y cada color un `<div>` con
   otro en `data-color-info` (nombre, stock, precio, tachado y el mínimo de 30 días). O sea que el
   parseo es `json.loads`, no navegación del DOM: mucho menos frágil de lo que #81 temía.
3. **Springfield publica el mínimo de 30 días de la directiva Ómnibus** (`lowestPriceData.lowest`),
   y es la **segunda** tienda que lo hace después de C&A (#78). Va a `retailer_min_30d`, que ya
   existe en `price_history` desde la migración `0018`. Ojo: lo declara **por color**, no por
   talla, así que todas las tallas de un color comparten el valor.
4. **La ficha solo pinta las tallas del color ACTIVO.** Los demás colores hay que pedirlos con
   `?dwvar_{id}_color=`, una petición más cada uno. Sale barato porque el catálogo es casi todo
   monocolor (9 de cada 10 en la muestra del recon).
5. **La clase CSS `active` miente en las páginas `?dwvar`**: ahí no la lleva ningún swatch, así que
   no sirve para saber qué color se está pintando. Lo que sí es fiable es el `selected: true` del
   `data-color-info`. Aun así el color de cada fila de talla se saca de su `pid`, que lleva el
   colorID dentro (`pid = maestro + colorID + código de talla`: `7301100` + `61` + `27` =
   `73011006127`): es dato por fila en vez de una bandera de página, y además es el valor que ya
   hace falta como `retailer_variant_id`.

**Enumera su árbol sin pedir nada** (`SupportsCategoryTree`, #179), y es la única de las nueve que
lo hace así: la taxonomía viaja en la ruta de cada URL, o sea que el árbol son las rutas distintas
del sitemap que ya se descarga. Por eso su `count` es de productos servidos y no declarados. Se
vigila cada semana (`SupportsCoverageWatch`) porque el árbol es una taxonomía de verdad, no un menú
con promociones rotando: medido el 04/08/2026, 65 rutas bajo los dos géneros y ni un hueco.

Alcance: solo el mundo `ninos-de-5-a-14` (1378 productos el 03/08/2026). `teen` (84) se queda fuera
a propósito —es otro mundo con su propia taxonomía y el brief habla de ropa infantil—, y las ~8
URLs infantiles sin taxonomía (`/es/es/ninos-de-5-a-14/pijama-flores/1310137.html`) se saltan en
vez de reventar con ellas.

Las funciones `parse_*` son puras (XML/HTML -> dataclasses) y se testean con fixtures.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import json
import logging
import random
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..barefoot import classify
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
)

logger = logging.getLogger(__name__)

SLUG = "springfield"
BASE_URL = "https://myspringfield.com"
_SITEMAP_URL = (
    BASE_URL + "/on/demandware.store/Sites-SPF-Site/es_ES/SiteMapCustom-Google?name={name}"
)
_SITEMAP_INDICE = "sitemap_0-index.xml"

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# El mundo infantil del que se ingiere. `teen` existe y queda fuera (ver cabecera).
_MUNDO_INFANTIL = "ninos-de-5-a-14"

_GENERO_POR_SEGMENTO: Mapping[str, str] = {"nino": "niño", "nina": "niña"}

# Segmento de categoría de la URL -> (sección, categoría) nuestras. Sacado de contar las 1378 URLs
# infantiles reales el 03/08/2026, no de adivinar rutas.
#
# `faldas` se pliega a `vestidos` por el mismo motivo y con el mismo precedente que C&A (`3-1-3`):
# el brief no tiene slug para falda, y dejarlas fuera perdería 37 prendas. Igual `jerseis` con
# `sudaderas` (la categoría del brief es "sudaderas/jerseys") y `jeans` con `pantalones`.
#
# `pijamas` va a `ropa-interior` por el criterio que #187 y #192 fijaron para toda prenda que no es
# ninguna de las cinco del brief: **¿tiene una de las cinco como casa natural?** El pijama sí, y no
# es una opinión nuestra —lo dicen las otras CUATRO tiendas que publican hoja propia de pijama y lo
# mandan ahí: `zara.py` (2427367 y 2422216), `hm.py` (`clothing/nightwear`), `hipercor.py`
# (`pijamas-y-batas`) y C&A por sus hojas ya mapeadas. Springfield era la única que lo dejaba fuera,
# o sea que la misma prenda entraba o no en el catálogo según la tienda. Son 64 prendas.
#
# `total-looks` NO entra, y el motivo está medido (#192). Parecía el mismo caso que el `TOTAL LOOK`
# de Zara —que desde #200 sí se ingiere, filtrado, como `conjuntos`— pero no lo es, y el filtro de
# #200 tampoco lo arreglaría porque aquí no hay ninguna ficha que filtrar: sus dos URLs son páginas
# **«Shop the look»**, no fichas. Comprobado el 05/08/2026 sobre `02092108`: 200 con 273 KB y
# **cero** `ld+json`, `size-data` ni `data-color-info`. No hay prenda, ni talla, ni precio: es una
# página que enlaza a las prendas sueltas, que ya entran por su propia categoría. Mapearla añade dos
# al listado que mueren en el detalle con un warning por pasada y no ingieren nada.
#
# Fuera del mapa por no ser del brief, y por tanto sin ingerir: `complementos` (89), `bano` (40),
# `chaquetas` (35), `abrigos` (13), `chalecos` (2), `promociones` (14) y `total-looks` (2). Añadir
# uno aquí es todo lo que hace falta para empezar a ingerirlo.
CATEGORIA_POR_SEGMENTO: Mapping[str, tuple[str, str]] = {
    "calzado": ("zapateria", "zapatos"),
    "camisetas": ("ropa", "camisetas"),
    "polos": ("ropa", "camisetas"),
    "camisas": ("ropa", "camisetas"),
    "camisas-y-blusas": ("ropa", "camisetas"),
    "pantalones": ("ropa", "pantalones"),
    "jeans": ("ropa", "pantalones"),
    "sudaderas": ("ropa", "sudaderas"),
    "jerseis": ("ropa", "sudaderas"),
    "vestidos": ("ropa", "vestidos"),
    "faldas": ("ropa", "vestidos"),
    "intimo": ("ropa", "ropa-interior"),
    "pijamas": ("ropa", "ropa-interior"),
}

# Las ramas (género, categoría) que la tienda publica DE VERDAD, contadas sobre el sitemap el
# 03/08/2026. No es el producto cartesiano de los dos mapas de arriba, y la diferencia importa:
# `nino/vestidos`, `nino/faldas`, `nina/polos`, `nina/camisas` y `nino/camisas-y-blusas` no existen,
# y sondearlas daría **cinco avisos falsos todas las semanas**. Eso no es un detalle cosmético — es
# exactamente lo que degradó el vigía de Sfera a ruido de fondo durante semanas (#129, y la lectura
# que #67 dejó escrita: lo que separa un blip de un bug es la repetición).
#
# Ojo a lo que esta lista NO hace: no decide qué se ingiere. Eso lo decide `clasificar()` a partir
# de los dos mapas, así que el día que la tienda estrene `nino/vestidos` se ingiere solo. Esta lista
# solo dice qué ramas tiene sentido VIGILAR, que es otra pregunta.
HOJAS: tuple[tuple[str, str], ...] = (
    ("nina", "camisetas"),
    ("nina", "camisas-y-blusas"),
    ("nina", "calzado"),
    ("nina", "pantalones"),
    ("nina", "jeans"),
    ("nina", "sudaderas"),
    ("nina", "jerseis"),
    ("nina", "vestidos"),
    ("nina", "faldas"),
    ("nina", "intimo"),
    ("nina", "pijamas"),
    ("nino", "camisetas"),
    ("nino", "camisas"),
    ("nino", "polos"),
    ("nino", "calzado"),
    ("nino", "pantalones"),
    ("nino", "jeans"),
    ("nino", "sudaderas"),
    ("nino", "jerseis"),
    ("nino", "intimo"),
    ("nino", "pijamas"),
)

_RE_SIZE_DATA = re.compile(r'size-data="([^"]*)"')
_RE_COLOR_INFO = re.compile(r'data-color-info="([^"]*)"')
_RE_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)
# "24,99 €" / "3,99 €" -> Decimal. La tienda escribe el precio con coma decimal y sufijo.
_RE_PRECIO_TEXTO = re.compile(r"(\d+(?:[.,]\d+)?)")
_NS_SITEMAP = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass(frozen=True)
class EntradaSitemap:
    """Una URL del sitemap de producto, con su `lastmod` (que es la huella)."""

    url: str
    lastmod: str


@dataclass(frozen=True)
class RutaProducto:
    """La taxonomía que la URL de un producto declara, **en el vocabulario de la tienda**.

    Distinta de `Ubicacion` a propósito: aquí no hay traducción a nuestro dominio. Es lo que la
    tienda dice, y es lo que necesita la capa de cobertura para poder señalar precisamente lo que
    NO sabemos traducir.
    """

    retailer_product_id: str
    # (género, categoría) o (género, categoría, subcategoría). Nunca menos de dos.
    taxonomia: tuple[str, ...]

    @property
    def genero(self) -> str:
        return self.taxonomia[0]

    @property
    def categoria(self) -> str:
        return self.taxonomia[1]

    def ancestros(self) -> list[str]:
        """Las rutas del árbol a las que este producto pertenece, de arriba abajo.

        `nina/pantalones/shorts` cuenta también para `nina/pantalones` y para `nina`: un producto
        que cuelga de una subcategoría está dentro de su categoría, y si no se contase así el
        `count` de las ramas saldría corto justo donde la tienda anida.
        """
        return ["/".join(self.taxonomia[: i + 1]) for i in range(len(self.taxonomia))]


@dataclass(frozen=True)
class Ubicacion:
    """Dónde cae un producto según su URL: género, sección y categoría nuestras."""

    retailer_product_id: str
    gender: str
    section: str
    category: str

    @property
    def scope(self) -> ScrapeScope:
        return ScrapeScope(self.gender, self.section, self.category)


@dataclass(frozen=True)
class ColorInfo:
    """Un color del producto, tal y como lo declara su swatch (`data-color-info`).

    Lleva **dos** identificadores y no son intercambiables, que es lo que costó 45 productos en la
    primera pasada:

    - `color_id` es el código de la tienda (`id`), y sirve para UNA cosa: construir la URL
      `?dwvar_{maestro}_color={color_id}`.
    - `clave` es el trozo de color del `pid`, y es con lo que se emparejan las filas de talla.

    Casi siempre coinciden, pero **no siempre**: en `0143394` («multicolor») el `id` es `100` —tres
    dígitos— mientras su `representedProductId` es `01433947605`, o sea maestro + `76` + talla. El
    hueco de color del `pid` tiene dos caracteres y un código de tres no cabe, así que la tienda
    usa otro. Emparejando por `id` esas filas caen en un cubo que ningún color reclama, el producto
    se queda sin variantes y desaparece del catálogo **en silencio**: no hay error, no hay aviso,
    simplemente no está. Por eso `clave` se deriva con la MISMA función que las filas
    (`color_de_pid`), y así coinciden por construcción en vez de por suerte.
    """

    color_id: str
    clave: str
    nombre: str
    precio: Decimal | None
    tachado: Decimal | None
    minimo_30d: Decimal | None
    agotado: bool


@dataclass(frozen=True)
class TallaInfo:
    """Una talla de un color concreto, tal y como la declara su `<input>` (`size-data`)."""

    pid: str
    talla: str
    agotado: bool
    precio: Decimal | None
    tachado: Decimal | None


@dataclass(frozen=True)
class FichaBase:
    """Lo que aporta el `ld+json`: identidad, nombre, textos y galería del color activo."""

    retailer_product_id: str
    nombre: str
    imagenes: tuple[str, ...]
    textos: tuple[str, ...]


def _precio_num(value: Any) -> Decimal | None:
    """Los precios del JSON vienen como número (`3.99`), no en céntimos.

    Se pasa por `str()` antes del `Decimal` a propósito: `Decimal(3.99)` arrastraría la basura
    binaria del float. Mismo criterio que `c_and_a._precio()`.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _precio_texto(value: Any) -> Decimal | None:
    """`"24,99 €"` -> `Decimal("24.99")`. Devuelve None si no hay número que sacar."""
    if not isinstance(value, str):
        return None
    m = _RE_PRECIO_TEXTO.search(value)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _json_de_atributo(valor: str) -> Any | None:
    """El JSON que viaja dentro de un atributo HTML, con sus entidades ya desescapadas.

    Devuelve None en vez de propagar: un atributo que la tienda cambie de formato debe costar
    ese producto, no la pasada entera.
    """
    try:
        return json.loads(html_lib.unescape(valor))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_sitemap_index(xml: str) -> list[str]:
    """Los nombres de los ficheros de sitemap **de producto** que publica el índice.

    Se filtran por nombre (`-Products`) y NO por posición: el índice mezcla producto, imágenes y
    categorías, y el día que la tienda añada un `sitemap_9` coger «los tres primeros» ingeriría
    imágenes. Devuelve el valor del parámetro `name`, que es lo que hay que volver a pedir.
    """
    raiz = ET.fromstring(xml)
    nombres = []
    for loc in raiz.iterfind(".//sm:sitemap/sm:loc", _NS_SITEMAP):
        texto = (loc.text or "").strip()
        m = re.search(r"name=([^&]+)", texto)
        if not m:
            continue
        nombre = html_lib.unescape(m.group(1)).replace("%2e", ".").replace("%2E", ".")
        if "-Products" in nombre:
            nombres.append(nombre)
    return nombres


def parse_sitemap_products(xml: str) -> list[EntradaSitemap]:
    """Las URLs de un fichero de sitemap de producto, con su `lastmod`.

    Una URL sin `lastmod` se descarta: sin huella no hay detalle condicional, y emitirla con una
    huella inventada (la cadena vacía, la fecha de hoy) haría que pareciese que nunca cambia o que
    cambia siempre. Hoy las traen las 12 842.
    """
    raiz = ET.fromstring(xml)
    entradas = []
    for url in raiz.iterfind(".//sm:url", _NS_SITEMAP):
        loc = url.findtext("sm:loc", namespaces=_NS_SITEMAP)
        lastmod = url.findtext("sm:lastmod", namespaces=_NS_SITEMAP)
        if not loc or not lastmod:
            continue
        entradas.append(EntradaSitemap(loc.strip(), lastmod.strip()))
    return entradas


def trocear_ruta(url: str) -> RutaProducto | None:
    """Id y taxonomía de una URL de producto infantil. `None` = esta URL no cuenta para nada.

    La ruta es `/es/es/{mundo}/{género}/{categoría}[/{subcategoría}]/{slug}/{id}.html`. Es lo único
    que hay que leer para saber dónde cae un producto, y por eso lo comparten los **tres** que
    miran el sitemap: `clasificar()` (qué se ingiere), `check_leaves()` (sigue viva la rama) y
    `category_tree()` (qué publica la tienda). Estaba escrito tres veces y separarse habría sido
    cuestión de tiempo: la capa de cobertura solo sirve si habla exactamente el mismo idioma que
    el listado.

    Se devuelve `None` —sin ruido— en los casos que no son un error: otro mundo (`mujer`, `hombre`,
    `teen`), un género que no es `nino`/`nina`, las ~8 URLs infantiles sin taxonomía
    (`/es/es/ninos-de-5-a-14/pijama-flores/1310137.html`), que tienen solo 3 segmentos, y lo que no
    acabe en un id numérico. Ojo a lo que **no** filtra: la categoría, aunque no esté en
    `CATEGORIA_POR_SEGMENTO`. Eso es cosa de `clasificar()`, porque la cobertura necesita ver justo
    lo que no ingerimos.
    """
    resto = url.split(f"{BASE_URL}/es/es/", 1)[-1]
    if resto == url:  # la URL no era de esta tienda
        return None
    segmentos = resto.split("/")
    if len(segmentos) < 5 or segmentos[0] != _MUNDO_INFANTIL:
        return None
    if segmentos[1] not in _GENERO_POR_SEGMENTO:
        return None

    fichero = segmentos[-1]
    if not fichero.endswith(".html"):
        return None
    product_id = fichero.removesuffix(".html")
    if not product_id.isdigit():
        return None

    # Los dos últimos segmentos son el slug y el fichero; la taxonomía es lo que queda por delante,
    # o sea género, categoría y —cuando la hay— subcategoría.
    return RutaProducto(product_id, tuple(segmentos[1:-2]))


def clasificar(url: str) -> Ubicacion | None:
    """Género, sección y categoría a partir de la ruta. `None` = esta URL no se ingiere.

    Resuelve el ámbito **sin una sola petición**, que es lo que hace viable esta tienda. Sobre lo
    que `trocear_ruta()` acepta, añade el único filtro propio de la ingesta: que la categoría esté
    en el mapa (`pijamas` y `complementos` no lo están, y por eso no se ingieren).
    """
    ruta = trocear_ruta(url)
    if ruta is None:
        return None

    gender = _GENERO_POR_SEGMENTO[ruta.genero]
    seccion_categoria = CATEGORIA_POR_SEGMENTO.get(ruta.categoria)
    if seccion_categoria is None:
        return None

    section, category = seccion_categoria
    return Ubicacion(ruta.retailer_product_id, gender, section, category)


def parse_ld_json(html: str) -> FichaBase | None:
    """Identidad, nombre, galería del color activo y textos, del bloque `ld+json` de la ficha.

    Los textos (nombre, descripción y composición) son lo que come `barefoot.classify()`, y por eso
    se recogen aquí aunque no se guarden: es la única señal que esta tienda da sobre si un zapato
    es respetuoso o no.
    """
    m = _RE_LD_JSON.search(html)
    if not m:
        return None
    try:
        datos = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None

    sku = datos.get("sku")
    nombre = datos.get("name")
    if not isinstance(sku, str) or not isinstance(nombre, str):
        return None

    crudas = datos.get("image")
    imagenes = tuple(u for u in crudas if isinstance(u, str)) if isinstance(crudas, list) else ()
    textos = tuple(
        t for t in (nombre, datos.get("description"), datos.get("material")) if isinstance(t, str)
    )
    return FichaBase(sku, nombre, imagenes, textos)


def parse_colores(html: str) -> list[ColorInfo]:
    """Los colores del producto, de los `data-color-info` de sus swatches.

    La ficha repite cada swatch (móvil y escritorio), así que se deduplica por `id` —que es el
    colorID— con «gana el primero». El mínimo de 30 días de Ómnibus se declara aquí, **por color**,
    y viene como texto formateado (`"24,99 €"`), no como número.

    Ojo con dos campos que parecen lo que no son: `images` trae **solo el swatch** de 10×10, no la
    galería (la galería del color que la página pinta es la del `ld+json`), y `colorSizeUrl` apunta
    a `Product-GetProductVariantData`, que es justamente la ruta que el `robots.txt` veta.
    """
    colores: dict[str, ColorInfo] = {}
    for bruto in _RE_COLOR_INFO.findall(html):
        datos = _json_de_atributo(bruto)
        if not isinstance(datos, dict):
            continue
        color_id = datos.get("id")
        nombre = datos.get("description")
        if not isinstance(color_id, str) or not isinstance(nombre, str):
            continue
        if color_id in colores:
            continue
        # La clave de emparejamiento sale del pid de referencia del propio color, no del `id`
        # (ver el docstring de `ColorInfo`). Sin pid de referencia se cae al `id`, que es lo
        # correcto en la mayoría de fichas y no empeora nada en las demás.
        maestro = datos.get("productReferenceID")
        referencia = datos.get("representedProductId")
        clave = None
        if isinstance(maestro, str) and isinstance(referencia, str):
            clave = color_de_pid(referencia, maestro)
        minimo = datos.get("lowestPriceData")
        colores[color_id] = ColorInfo(
            color_id=color_id,
            clave=clave or color_id,
            nombre=nombre,
            precio=_precio_num(datos.get("price")),
            tachado=_precio_num(datos.get("absoluteListPrice")),
            minimo_30d=_precio_texto(minimo.get("lowest")) if isinstance(minimo, dict) else None,
            agotado=bool(datos.get("outOfStock")),
        )
    return list(colores.values())


def parse_tallas(html: str) -> list[TallaInfo]:
    """Las tallas del color ACTIVO de la ficha, de los `size-data` de sus `<input>`.

    Deduplicadas por `pid` con «gana la primera», por el mismo motivo que los colores: el selector
    de talla aparece dos veces (la lista y el desplegable pegajoso).
    """
    tallas: dict[str, TallaInfo] = {}
    for bruto in _RE_SIZE_DATA.findall(html):
        datos = _json_de_atributo(bruto)
        if not isinstance(datos, dict):
            continue
        pid = datos.get("pid")
        talla = datos.get("sizeDisplayValue")
        if not isinstance(pid, str) or not isinstance(talla, str):
            continue
        if pid in tallas:
            continue
        tallas[pid] = TallaInfo(
            pid=pid,
            talla=talla,
            agotado=bool(datos.get("outOfStock")),
            precio=_precio_num(datos.get("price")),
            tachado=_precio_num(datos.get("absoluteListPrice")),
        )
    return list(tallas.values())


def color_de_pid(pid: str, retailer_product_id: str) -> str | None:
    """El colorID que lleva dentro el `pid` (`maestro + colorID + talla`).

    Es la única forma fiable de saber a qué color pertenece una fila de talla: en una página pedida
    con `?dwvar_…_color=` ningún swatch queda marcado como `active` (ver cabecera, punto 5). Se
    quita el prefijo del maestro en vez de cortar por posición fija para no depender de que los ids
    midan siempre 7 dígitos.
    """
    resto = pid.removeprefix(retailer_product_id)
    if resto == pid or len(resto) < 3:
        return None
    return resto[:2]


def producto(
    ficha: FichaBase,
    ubicacion: Ubicacion,
    colores: Sequence[ColorInfo],
    tallas_por_color: Mapping[str, Sequence[TallaInfo]],
    imagenes_por_color: Mapping[str, Sequence[str]],
    url: str,
) -> ScrapedProduct | None:
    """Junta ficha, colores y tallas en un producto. Puro: sin red.

    El precio de la variante sale de la **talla** y no del color, porque es el más fino de los dos
    (la tienda podría rebajar solo unas tallas); el mínimo de 30 días sale del **color**, porque es
    donde la tienda lo declara. Si una talla no trae precio se cae al del color antes que
    descartarla: una variante sin precio no se puede ingerir, pero una con el precio del color sí,
    y es el mismo número en todo lo medido.

    Devuelve `None` si no queda ni una variante con precio: un producto sin variantes utilizables
    no se ingiere (no se puede seguir ni avisar de él).
    """
    variantes: list[ScrapedVariant] = []
    imagenes: list[ScrapedImage] = []

    for color in colores:
        filas = tallas_por_color.get(color.clave, ())
        if not filas:
            # Un color que no se ha llegado a pedir (o que no publica tallas) no aporta variantes.
            # Sus fotos tampoco: `ScrapedImage` exige que la foto vaya con un color que tenga
            # variantes, o la ficha empareja foto y precio de cosas distintas.
            continue
        for fila in filas:
            precio = fila.precio if fila.precio is not None else color.precio
            if precio is None:
                continue
            variantes.append(
                ScrapedVariant(
                    retailer_variant_id=fila.pid,
                    size=fila.talla,
                    color=color.nombre,
                    sku=fila.pid,
                    price=precio,
                    list_price=fila.tachado if fila.tachado is not None else color.tachado,
                    in_stock=not fila.agotado,
                    url=url,
                    retailer_min_30d=color.minimo_30d,
                )
            )
        for foto in imagenes_por_color.get(color.clave, ()):
            imagenes.append(ScrapedImage(color=color.nombre, url=foto))

    if not variantes:
        return None

    return ScrapedProduct(
        retailer_product_id=ubicacion.retailer_product_id,
        name=ficha.nombre,
        gender=ubicacion.gender,
        section=ubicacion.section,
        category=ubicacion.category,
        url=url,
        variants=variantes,
        barefoot=classify(
            retailer=SLUG,
            retailer_product_id=ubicacion.retailer_product_id,
            section=ubicacion.section,
            category=ubicacion.category,
            texts=ficha.textos,
        ),
        image_url=imagenes[0].url if imagenes else None,
        images=imagenes,
    )


class SpringfieldStore:
    """Scraper de Springfield. Listado por sitemap, detalle por ficha."""

    slug = SLUG
    name = "Springfield"
    base_url = BASE_URL

    def __init__(self, config: Config) -> None:
        self._config = config
        self._report = ScanReport()
        # Ubicación por producto, rellenada en `list_catalog()` y consumida en `fetch_details()`:
        # la URL de la ficha y el ámbito ya se saben del sitemap, y volver a deducirlos costaría
        # rehacer el listado entero.
        self._ubicaciones: dict[str, tuple[Ubicacion, str]] = {}

    # --- contrato BaseStore -------------------------------------------------

    def scopes(self) -> Iterable[ScrapeScope]:
        """Producto cartesiano de géneros × (sección, categoría) del mapa.

        Se declara lo que el mapa PUEDE emitir y no lo que se haya visto en la pasada: un ámbito
        declarado de menos deja sus productos fuera del alcance de las bajas, y por tanto
        imposibles de descatalogar. No hace falta `con_unisex()` — aquí no hay cruce de géneros:
        los 1378 ids infantiles del sitemap son únicos y cada uno cuelga de una sola rama.

        Esto es a propósito el producto cartesiano y NO `HOJAS`, que es más corta. La asimetría no
        es un descuido: las dos listas contestan preguntas contrarias y el error barato está en
        lados opuestos. Aquí pasarse es inocuo (un ámbito de más no tiene productos y no hace
        nada) y quedarse corto deja productos imposibles de dar de baja; en `check_leaves()` es al
        revés — pasarse es un aviso falso cada semana.
        """
        vistos = dict.fromkeys(CATEGORIA_POR_SEGMENTO.values())
        return [
            ScrapeScope(genero, section, category)
            for genero in _GENERO_POR_SEGMENTO.values()
            for section, category in vistos
        ]

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Recorre los sitemaps de producto. **4 peticiones para el catálogo entero.**

        Un fichero de sitemap que falle no tumba la pasada, pero sí saca del alcance de las bajas
        a TODOS los ámbitos: un sitemap es un corte arbitrario del catálogo (no va por categorías),
        así que no se puede saber a quién se ha dejado de mirar. Sin eso, perder un fichero
        descatalogaría a ciegas el tercio de productos que traía.
        """
        self._report = ScanReport()
        self._ubicaciones = {}

        with self._client() as client:
            indice = self._get_texto(client, _SITEMAP_URL.format(name=_SITEMAP_INDICE))
            nombres = parse_sitemap_index(indice)
            logger.info("springfield: %d sitemaps de producto en el índice", len(nombres))

            for nombre in nombres:
                try:
                    xml = self._get_texto(client, _SITEMAP_URL.format(name=nombre))
                    entradas = parse_sitemap_products(xml)
                except (httpx.HTTPError, ET.ParseError) as exc:
                    logger.warning("springfield: sitemap %s ilegible (%s)", nombre, exc)
                    self._marcar_sitemap_caido(nombre)
                    continue

                self._report.leaf_ok()
                for entrada in entradas:
                    ubicacion = clasificar(entrada.url)
                    if ubicacion is None:
                        continue
                    self._ubicaciones[ubicacion.retailer_product_id] = (ubicacion, entrada.url)
                    yield ListingEntry(
                        retailer_product_id=ubicacion.retailer_product_id,
                        signature=entrada.lastmod,
                        gender=ubicacion.gender,
                        section=ubicacion.section,
                        category=ubicacion.category,
                    )

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Una petición por producto, más una por cada color adicional.

        Lo caro de esta tienda es esto, y por eso el detalle condicional por `lastmod` es lo que la
        hace viable: en régimen estable solo se piden las fichas que hayan cambiado.
        """
        with self._client() as client:
            for entry in entries:
                ubicacion_url = self._ubicaciones.get(entry.retailer_product_id)
                if ubicacion_url is None:
                    # Guarda defensiva, no un caso esperado: la ingesta solo pide el detalle de
                    # entradas salidas de ESTE `list_catalog()` —incluidas las del refresco
                    # periódico forzado, que reusa la entrada con su huella actual
                    # (`ingest._stale_refreshes`)—, así que todas han pasado por `_ubicaciones`.
                    logger.warning(
                        "springfield: %s sin ubicación; ¿detalle pedido sin listar?",
                        entry.retailer_product_id,
                    )
                    continue
                ubicacion, url = ubicacion_url
                try:
                    prod = self._ficha(client, ubicacion, url)
                except httpx.HTTPError as exc:
                    logger.warning(
                        "springfield: ficha %s ilegible (%s)", entry.retailer_product_id, exc
                    )
                    continue
                if prod is not None:
                    yield prod

    def scan_report(self) -> ScanReport:
        return self._report

    # --- capacidades opcionales ---------------------------------------------

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea el catálogo por sitemap y por rama, sin ingerir.

        Aquí «hoja» no es una URL de categoría —no las usamos— sino cada par
        (género, segmento de categoría) del mapa, y el sondeo sale **gratis**: con los 3 sitemaps ya
        descargados se cuenta cuántos productos cae en cada rama. Una rama que baje a **0** es
        exactamente la señal que el vigía existe para dar: la tienda ha renombrado el segmento y
        llevamos semanas sin ingerir esa categoría sin que nada lo dijese.

        Se sondea `HOJAS` y no el producto cartesiano de los dos mapas: hay pares que la tienda no
        publica (no hay `nino/vestidos` ni `nina/polos`) y avisar de ellos sería llorar al lobo cada
        semana. Ver el comentario de `HOJAS`.

        Coste total: 4 peticiones para las 21 hojas.
        """
        ramas = list(HOJAS)

        try:
            rutas = self._rutas_del_sitemap()
        except (httpx.HTTPError, ET.ParseError) as exc:
            # Sin sitemap no hay veredicto sobre ninguna rama: `None` es «no lo sé», que NO es lo
            # mismo que «retirada». Dar False aquí descatalogaría el catálogo entero.
            detalle = f"{type(exc).__name__}: {exc}"
            for genero, segmento in ramas:
                section, category = CATEGORIA_POR_SEGMENTO[segmento]
                scope = ScrapeScope(_GENERO_POR_SEGMENTO[genero], section, category)
                yield LeafHealth(scope, f"{genero}/{segmento}", None, detalle)
            return

        cuenta = Counter(f"{r.genero}/{r.categoria}" for r in rutas)
        for genero, segmento in ramas:
            section, category = CATEGORIA_POR_SEGMENTO[segmento]
            scope = ScrapeScope(_GENERO_POR_SEGMENTO[genero], section, category)
            n = cuenta.get(f"{genero}/{segmento}", 0)
            yield LeafHealth(scope, f"{genero}/{segmento}", n > 0, f"{n} productos en el sitemap")

    def _rutas_del_sitemap(self) -> list[RutaProducto]:
        """Los productos infantiles del sitemap, **uno por id**, con su taxonomía.

        Deduplicar no es cosmético: el sitemap **repite cada URL** entre sus ficheros de producto
        (medido el 04/08/2026: 3207 filas para 1382 productos, un factor de 2,3). Contando filas,
        `check_leaves()` decía «446 productos» donde hay 193 y la cobertura habría publicado esos
        mismos números inflados. No cambia ningún veredicto —lo que se mira es que la rama no baje
        a 0— pero sí lo que se lee, y este número se compara a ojo con el del catálogo.

        Un fichero ilegible **propaga**, al revés que en `list_catalog()`: allí perder un tercio del
        catálogo es una pasada incompleta que hay que acotar, y aquí es un sondeo que se repite en
        una hora. Quien llama decide, y los dos que llaman saben decir «no lo sé».
        """
        with self._client() as client:
            indice = self._get_texto(client, _SITEMAP_URL.format(name=_SITEMAP_INDICE))
            entradas: list[EntradaSitemap] = []
            for nombre in parse_sitemap_index(indice):
                entradas += parse_sitemap_products(
                    self._get_texto(client, _SITEMAP_URL.format(name=nombre))
                )

        por_id: dict[str, RutaProducto] = {}
        for entrada in entradas:
            ruta = trocear_ruta(entrada.url)
            if ruta is not None:
                por_id.setdefault(ruta.retailer_product_id, ruta)
        return list(por_id.values())

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las 21 ramas de `HOJAS`.

        El mismo vocabulario que usa `check_leaves()` para su `LeafHealth.leaf`, y a propósito: dos
        idiomas para el mismo sitio es cómo se llega a que una capa diga que falta lo que la otra
        está ingiriendo.
        """
        return [f"{genero}/{segmento}" for genero, segmento in HOJAS]

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. Los dos géneros del mundo infantil.

        No se barre desde `ninos-de-5-a-14`: el mundo no es un nodo del árbol que publicamos —la
        taxonomía empieza en el género— y los otros mundos (`mujer`, `hombre`, `teen`) quedan fuera
        del brief a propósito. Medido el 04/08/2026: **65 rutas** entre las dos (29 de niño y 36 de
        niña, subcategorías incluidas) sobre 1382 productos.
        """
        return ["nino", "nina"]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCoverageWatch`. La ruta de Springfield anida con `/`."""
        return "/"

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. El árbol sale del sitemap, sin pedir nada nuevo.

        Esta tienda no publica un endpoint de categorías —su rejilla de SFCC está vetada por el
        `robots.txt`, que es de lo que va #81— pero **no le hace falta**: la taxonomía viaja en la
        ruta de cada URL de producto, así que el árbol es el conjunto de rutas distintas de las
        1382 URLs que `check_leaves()` ya se descarga. 4 peticiones para el árbol entero.

        Eso le da una propiedad que no tienen ni Sfera ni C&A: aquí `count` es el número de
        productos que la tienda **sirve** en esa rama, no el que **declara** su faceta. En Sfera los
        dos números no coinciden (8 declarados contra 18 servidos, medido en #72), y el aviso de
        `CategoryNode.count` es justo sobre eso. Aquí no hay hueco entre los dos porque se cuenta lo
        mismo que se ingiere.

        Un nodo con hijas **también cuenta sus productos**: los 71 `shorts` de `nina/pantalones`
        están dentro de los 193 de `nina/pantalones`, porque la tienda cuelga el producto de la
        subcategoría y de su padre a la vez.
        """
        rutas = self._rutas_del_sitemap()
        prefijo = f"{root}/"

        cuenta: Counter[str] = Counter()
        for ruta in rutas:
            for ancestro in ruta.ancestros():
                if ancestro.startswith(prefijo):  # solo descendientes de la raíz pedida
                    cuenta[ancestro] += 1

        con_hijas = {p.rsplit("/", 1)[0] for p in cuenta}
        for path in sorted(cuenta):
            yield CategoryNode(
                path=path,
                # La tienda no publica un rótulo aparte: el segmento ES como la llama.
                title=path.rsplit("/", 1)[-1],
                count=cuenta[path],
                depth=path.count("/") - root.count("/"),
                has_children=path in con_hijas,
            )

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Pregunta por la ficha: 200 directo = vivo, 404 = retirado, lo demás sin veredicto.

        **Se sondea sin seguir redirecciones, y eso es el punto.** Medido el 03/08/2026 sobre la
        camiseta `6801308`: el id inventado `9999999` da un 404 honesto, pero el id vecino
        `6801309` —plausible, y con el slug de otro producto en la ruta— responde **301 a una ficha
        DISTINTA** que sirve un 200 perfectamente válido. Con `follow_redirects=True` eso llega
        aquí como 200 y se leería «sigue a la venta» mirando otra prenda. Es la misma trampa que
        #54 encontró en Sfera, y la única defensa es no dejar que la redirección se resuelva sola.

        Un 3xx queda entonces **fuera del mapa**, que es la respuesta honesta: la tienda no ha dicho
        que esté retirado, ha dicho que mires a otro sitio. Igual un fallo de red o un 5xx. La
        ingesta solo da de baja lo confirmado, así que abstenerse solo retrasa una baja real,
        mientras que un `False` de más borra del catálogo algo que se sigue vendiendo.
        """
        veredictos: dict[str, bool] = {}
        with self._client(seguir_redirecciones=False) as client:
            for candidato in candidates:
                url = candidato.url
                if not url:
                    continue
                self._polite_pause()
                try:
                    resp = client.get(url)
                except httpx.HTTPError:
                    continue
                if resp.status_code in GONE_STATUS:
                    veredictos[candidato.retailer_product_id] = False
                elif resp.status_code == 200:
                    veredictos[candidato.retailer_product_id] = True
        return veredictos

    # --- interno ------------------------------------------------------------

    def _ficha(self, client: httpx.Client, ubicacion: Ubicacion, url: str) -> ScrapedProduct | None:
        """Pide la ficha (y los colores que falten) y devuelve el producto montado."""
        html = self._get_texto(client, url)
        base = parse_ld_json(html)
        if base is None:
            logger.warning("springfield: ficha %s sin ld+json", ubicacion.retailer_product_id)
            return None

        colores = parse_colores(html)
        tallas_por_color: dict[str, list[TallaInfo]] = {}
        imagenes_por_color: dict[str, list[str]] = {}
        self._repartir(
            ubicacion.retailer_product_id,
            parse_tallas(html),
            base.imagenes,
            tallas_por_color,
            imagenes_por_color,
        )

        for color in colores:
            if color.clave in tallas_por_color:
                continue
            try:
                otra = self._get_texto(
                    client, f"{url}?dwvar_{ubicacion.retailer_product_id}_color={color.color_id}"
                )
            except httpx.HTTPError as exc:
                # Se pierde ESE color, no el producto: los que ya se tienen valen, y devolver None
                # aquí dejaría de ver una prenda entera porque su tercer color dio un 502.
                logger.warning(
                    "springfield: color %s de %s ilegible (%s)",
                    color.color_id,
                    ubicacion.retailer_product_id,
                    exc,
                )
                continue
            otra_base = parse_ld_json(otra)
            self._repartir(
                ubicacion.retailer_product_id,
                parse_tallas(otra),
                otra_base.imagenes if otra_base else (),
                tallas_por_color,
                imagenes_por_color,
            )

        return producto(base, ubicacion, colores, tallas_por_color, imagenes_por_color, url)

    @staticmethod
    def _repartir(
        retailer_product_id: str,
        tallas: Sequence[TallaInfo],
        imagenes: Sequence[str],
        tallas_por_color: dict[str, list[TallaInfo]],
        imagenes_por_color: dict[str, list[str]],
    ) -> None:
        """Reparte por colorID las tallas de una página, y le atribuye su galería.

        Las fotos del `ld+json` son las del color que la página está pintando, y ese color se sabe
        por el `pid` de sus tallas (punto 5 de la cabecera). Una página cuyas tallas no dejen
        deducir el color no aporta fotos: preferible una galería incompleta a una que enseñe el
        color equivocado.

        **Deduplica por `pid` contra lo ya repartido, no solo dentro de la página.** No es
        defensivo de más: pasa cuando se pide `?dwvar_…_color=Y` y la tienda contesta con las
        tallas del color por DEFECTO en vez de las de Y (medido en `2563674`, que declara dos
        colores y sirve el primero las dos veces). Sin esta guarda esas filas se apilan otra vez en
        el cubo del color por defecto y cada variante suya se emite dos veces.

        Y el daño no es cosmético, que fue la primera lectura. `variant` tiene
        `UNIQUE (product_id, retailer_variant_id)` y absorbe el duplicado, así que la tabla queda
        bien — pero `_record_price()` corre **una vez por variante emitida**, no por fila de
        `variant`. Medido en la primera pasada real (03/08/2026): 8329 variantes emitidas para 8219
        filas, y **110 variantes con DOS observaciones de precio en la misma pasada**, en la tabla
        de la que salen las gráficas y el detector de descuentos inflados. Un contador descuadrado
        se lee como ruido; esto era serie de precios sucia.
        """
        ya_repartidos = {fila.pid for filas in tallas_por_color.values() for fila in filas}
        colores_vistos: list[str] = []
        for fila in tallas:
            if fila.pid in ya_repartidos:
                continue
            color_id = color_de_pid(fila.pid, retailer_product_id)
            if color_id is None:
                continue
            tallas_por_color.setdefault(color_id, []).append(fila)
            if color_id not in colores_vistos:
                colores_vistos.append(color_id)
        if len(colores_vistos) == 1 and imagenes:
            imagenes_por_color.setdefault(colores_vistos[0], []).extend(imagenes)

    def _marcar_sitemap_caido(self, nombre: str) -> None:
        """Un sitemap ilegible saca a TODOS los ámbitos de las bajas (ver `list_catalog`).

        Cuenta como **una** hoja caída, que es lo que es: un fichero de tres. Por eso se llama a
        `leaf_gone()` una sola vez y los demás ámbitos se añaden directamente a `failed_scopes` —
        llamarlo 24 veces sumaría 24 hojas muertas de 24 y dispararía
        `SCRAPER_SCAN_MAX_DEAD_RATIO` con el primer fallo, abortando una pasada a la que todavía
        le quedan dos tercios del catálogo perfectamente legibles.

        La hoja que se nombra es **el fichero de sitemap**, y ahí esta tienda es la excepción del
        proyecto: su `check_leaves()` habla en `genero/segmento` y este mensaje habla en
        `sitemap_1.xml`. La divergencia es deliberada porque las dos cosas son distintas de verdad
        —el sondeo mira ramas, la pasada lee ficheros— y el fichero es lo que hay que ir a mirar
        cuando esto salta. El ámbito no serviría: un sitemap es un corte arbitrario del catálogo,
        así que los 24 ámbitos salen de las bajas por uno solo de los tres ficheros.
        """
        ambitos = list(self.scopes())
        self._report.leaf_gone(ambitos[0], nombre)
        self._report.failed_scopes.update(ambitos)

    def _client(self, *, seguir_redirecciones: bool = True) -> httpx.Client:
        """Cliente HTTP. Sin redirecciones solo en `probe_alive()` (ver allí por qué)."""
        return httpx.Client(
            headers={
                "User-Agent": self._config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9",
            },
            timeout=self._config.request_timeout,
            follow_redirects=seguir_redirecciones,
        )

    def _polite_pause(self) -> None:
        """Pausa base entre peticiones con jitter (una cadencia fija es más detectable)."""
        base = self._config.request_delay
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _get_texto(self, client: httpx.Client, url: str) -> str:
        """GET con reintentos y backoff exponencial + jitter ante throttling/errores de red."""
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                self._backoff(attempt, retry_after=exc.response.headers.get("Retry-After"))
            except httpx.TransportError:
                if attempt == retries:
                    raise
                self._backoff(attempt)
        raise AssertionError("inalcanzable")  # pragma: no cover

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Espera exponencial (respeta `Retry-After` si viene) con jitter."""
        wait = self._config.retry_backoff * (2**attempt)
        if retry_after:
            with contextlib.suppress(ValueError):
                wait = max(wait, float(retry_after))
        time.sleep(wait * random.uniform(0.8, 1.2))
