"""Scraper de Hipercor (moda infantil): ropa y calzado.

Hipercor es del grupo El Corte Inglés y corre el mismo **firefly** que Sfera, con Akamai Bot
Manager delante: los cuatro clientes planos (`curl`, `wget`, `httpx` y `httpx` sin ALPN) reciben
403, así que todo va por el **navegador headless** (`stores/browser.py`).

**Por qué esto NO es «Sfera con otro prefijo», que es como se planteó en #79.** Su `robots.txt`
—ilegible con `curl` (403 de Akamai) pero servido con normalidad a Chromium— dice:

    Disallow: /api

y el firefly de Hipercor cuelga literalmente de ahí (`/api/firefly/vuestore/products_list/…`).
No es interpretable: la ruta que el recon proponía usar está vetada. Sfera se salva por prefijo y
no por suerte —su firefly vive en `/es/api/sfera-es/firefly/…`, y la regla es un prefijo desde la
raíz, así que no le aplica—, pero aquí sí aplica. Rodearlo no era una opción, igual que en #81 con
la rejilla de Springfield.

**El camino permitido resultó ser mejor de lo que parecía.** Las páginas de categoría y de
producto no están vetadas, y las dos son **SSR**: llevan el dato dentro del HTML, no en la API.
Verificado abortando `/api/**` en el propio navegador — con la ruta vetada muerta, la rejilla
sigue trayendo sus 12 productos y la ficha sus tallas:

  - **Listado** — `GET /{ruta}/{página}/` trae un `dataLayer` con `page` (`total_pages`,
    `total_products`, `hierarchy`) y `products[]` con `code_a` (el id estable `A########`),
    `name`, `status` y `price` (`f_price`, y `o_price` **solo si está rebajado**). Con eso se
    construye la huella del detalle condicional sin pedir una sola ficha.
  - **Detalle** — la PDP publica `ld+json` de tipo `ProductGroup` con `hasVariant[]`: `sku`,
    `size`, `offers.price` y `offers.availability` por talla, más `color` e `image`. Su
    `dataLayer` añade el precio tachado (`o_price`), que el `ld+json` no da.

Consecuencia de coste, y hay que tenerla presente al fijar el CronJob: aquí el detalle **sí**
cuesta una petición por producto (una navegación real, ~450 KB), a diferencia de Sfera, que lo
sirve desde la caché del listado. El detalle condicional por huella (#16) no es un ahorro
cómodo en esta tienda, es lo que la hace viable.

Y tiene una contrapartida que conviene saber antes de tocar `SCRAPER_DETAIL_MAX_AGE_DAYS`: la
rejilla **no da stock por talla**, solo un estado global del producto. Así que una talla que se
agota (o vuelve) sin que cambien ni el precio ni ese estado no se entera hasta el refresco
forzado — hasta 7 días con el valor por defecto. Bajar ese knob mejora la frescura del stock a
cambio de fichas, que aquí es a cambio de minutos de pasada.

Ids estables: producto `code_a` (`A56615356`), variante el `sku` de la talla
(`001081182601955028`). En esta tienda **cada color es un producto distinto** (dos referencias
para la misma sandalia en nude y en verde), así que el color viaja a nivel de ficha.

**Una ruta de categoría que no existe NO da 404**: devuelve 200 con el catálogo del *padre*
(#54 otra vez, y aquí más agresivo — el recon de #70 inventó seis rutas plausibles y las seis
parecieron vivas). Se detecta sin peticiones extra: `products[].hierarchy` viene **en slugs** y
refleja la ruta *realmente resuelta*, así que basta comprobar que la ruta pedida es prefijo de
ella. No hace falta cotejar ids contra el padre como en `sfera.py:324`.

**No implementa `SupportsCategoryTree`** (y es la primera tienda con hojas de categoría que no
lo hace, después de #56): la faceta que publica el árbol vive en `/api`, o sea en la ruta vetada.
La lista de `CATEGORIES` se mantiene a mano, pero **cada hoja se valida sola**: una ruta que ya
no exista cae en la comprobación de espejismo y se cuenta como hoja caída en vez de ingerir el
catálogo del género entero.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    GONE_STATUS,
    DelistCandidate,
    LeafHealth,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)
from .browser import BrowserSession

_LOG = logging.getLogger(__name__)

SLUG = "hipercor"  # a nivel de módulo: las funciones puras de parseo también lo necesitan
BASE_URL = "https://www.hipercor.es/"
_ROOT = "https://www.hipercor.es"

# Tope de guarda por si `total_pages` viniera anómalo (evita un bucle desbocado). Agotarlo NO
# cuenta como hoja sana: ver `_iter_category`.
_MAX_PAGES = 60

# Fichas ilegibles (403, 5xx tras reintentos) que se toleran antes de abortar la pasada. Sueltas
# son ruido —y la confirmación activa impide que se conviertan en bajas—, pero en cantidad
# significan que la tienda ha dejado de dejarnos entrar, y eso no se guarda como catálogo bueno.
_MAX_FICHAS_FALLIDAS = 5

# Tope de fotos por color, mismo criterio que en Zara y Sfera. Hoy la PDP da una sola por
# referencia (el color es el producto), pero el tope evita sorpresas si eso cambia.
_MAX_IMAGES_PER_COLOR = 8

# El `dataLayer` que la tienda embebe en cada página SSR. Es la fuente del listado y del
# precio tachado de la ficha. No es JSON suelto: es una asignación JS, de ahí el recorte.
_DATA_LAYER_RE = re.compile(r"dataLayer\s*=\s*(\[.*?\]);", re.DOTALL)
_LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL
)
# Ruta que el `robots.txt` de la tienda veta. Se bloquea en el navegador (ver `_abrir`) para que
# el cumplimiento no dependa de que ninguna URL del scraper la escriba: la página puede pedirla
# sola al hidratarse.
_RUTA_VETADA = "**/api/**"

# Selector que marca que la ficha ya ha pintado sus **tallas**, que es lo que hay que esperar: el
# documento inicial llega sin ellas y son el dato que justifica pedir la ficha. Vale para los dos
# esquemas (con `ProductGroup` y agotada); esperar al `ProductGroup` sería peor, porque en una
# ficha agotada no llega nunca y regalaría la espera entera a cada producto sin stock.
_SELECTOR_TALLAS = '[id^="size_option_"]'

# Recursos que la ficha carga y el scraper no lee: las fotos que guardamos son las URLs que ya
# vienen en el JSON de la página. Descartarlos baja el coste por ficha de 4,10 s a 3,55 s (medido
# el 02/08/2026 sobre 5 fichas, con el mismo número de variantes parseadas).
_RECURSOS_INUTILES = ("image", "font", "media", "stylesheet")

# Enlace a ficha dentro de la rejilla: de ahí sale la URL con la que se pide el detalle. No se
# fija la sección de la ruta (`/moda-y-accesorios/`): lo estable es la forma
# `/{sección}/A########-`, y atarse a la de hoy convertiría un renombrado de la tienda en fichas
# que dejarían de pedirse.
_PDP_HREF_RE = re.compile(r'href="(/[a-z0-9-]+/(A\d+)-[^"]*?)"')

# Opción del selector de tallas de la ficha: el id lleva NUESTRO sku de variante y la etiqueta
# accesible la talla. Es el respaldo cuando la ficha se agota entera y pierde el `ProductGroup`.
_SIZE_OPTION_RE = re.compile(
    r'id="size_option_(\d+):[^"]*".{0,400}?aria-label="variante ([^"]+)"', re.DOTALL
)


class LeafGone(RuntimeError):
    """La hoja ya no existe: 404, o 200 sirviendo el catálogo de un ancestro (#54)."""


class DetailUnavailable(RuntimeError):
    """La tienda ha dejado de servir fichas. No es que los productos se hayan retirado."""


class LeafUnreadable(RuntimeError):
    """La hoja no se ha podido leer (bloqueo, plantilla desconocida, paginación truncada).

    Se trata igual que una hoja caída **a efectos de bajas** —su ámbito deja de ser seguro— pero
    por un motivo distinto: no es que la tienda la haya retirado, es que no la hemos podido ver.
    """


@dataclass(frozen=True)
class CategoryConfig:
    """Mapea una hoja de Hipercor a nuestro dominio (género/sección/categoría).

    `category_path` es la ruta completa sin barras extremas, tal y como la publica la tienda
    (p.ej. `moda-y-accesorios/moda-infantil/nina-4-16-anos/vestidos`). Es a la vez la URL de la
    rejilla y lo que se compara contra `products[].hierarchy` para detectar el espejismo.
    """

    category_path: str
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # pantalones | camisetas | sudaderas | vestidos | ropa-interior | zapatos


_INFANTIL = "moda-y-accesorios/moda-infantil"
_BEBE_NINA = f"{_INFANTIL}/bebe-nina-6-meses-a-3-anos"
_BEBE_NINO = f"{_INFANTIL}/bebe-nino-6-meses-a-3-anos"

# Hojas curadas: las cinco categorías de ropa del brief (pantalones, camisetas,
# sudaderas/jerseys, vestidos, ropa interior) más zapatería, en los DOS rangos de edad — el ADR
# lo da por supuesto y se ha cumplido dos veces (Zara #35, Sfera #33): el barefoot vive sobre
# todo en la rama de bebé, y mapear solo la mayor parece cubrir la tienda dejando fuera el grueso
# de lo que este producto busca.
#
# Se mantienen a mano porque el árbol que publica la tienda está bajo `/api` (ruta vetada, ver la
# cabecera del módulo). A cambio, **ninguna se cree a ojo**: cada ruta se verificó pidiendo su
# rejilla y comprobando que `products[].hierarchy` la confirma (una inventada devuelve el
# catálogo del padre, no un 404), y esa misma comprobación corre en cada pasada.
#
# **El árbol no es simétrico entre ramas, y ahí es donde se pierde catálogo.** Medido el
# 02/08/2026 probando ruta a ruta: donde niña dice `pantalones-y-petos`, niño dice
# `pantalones-y-mas` (41 productos que copiando el nombre de niña se habrían quedado fuera, en
# silencio y con la hoja pareciendo viva); donde niña dice `jerseis-y-chaquetas`, niño dice
# `jerseis`; y `camisetas-y-polos` de niña es `camisetas` en niño. Es el mismo agujero que costó
# 191 productos en Sfera (#56), reproducido aquí antes de ingerir nada.
#
# Quedan FUERA a propósito, por no ser ninguna de las cinco categorías del brief: `bano`,
# `accesorios`, `ropa-de-abrigo`, `chalecos`, `calcetines-y-leotardos`, `colegios`,
# `recien-nacido`, `conjuntos` (1 producto) y `ranitas-peleles-y-cubrepanales` — esta última se
# valoró para ropa interior de bebé, pero `bodies` ya cubre esa categoría en las dos ramas de
# bebé, así que no hace falta tragarse una hoja que mezcla peleles con cubrepañales.
CATEGORIES: list[CategoryConfig] = [
    # --- zapatería (lo que más falta hace: hoy la sección depende casi entera de Cacles) ---
    # `nina` y `nino` van ANTES que `bebe` a propósito: las tres hojas se solapan (114+141+140
    # frente a los 290 que declara el padre) y el dedup de `list_catalog` es "gana la primera",
    # así que lo que tenga género declarado se queda con él y `bebe` solo aporta lo que no
    # aparece en ninguna de las dos. `unisex` no es un apaño: el catálogo y el matching lo
    # tratan como "sale en niño y en niña" (ver el ADR), que es justo lo que es una hoja de bebé.
    CategoryConfig(f"{_INFANTIL}/zapatos-infantiles/nina", "niña", "zapateria", "zapatos"),
    CategoryConfig(f"{_INFANTIL}/zapatos-infantiles/nino", "niño", "zapateria", "zapatos"),
    CategoryConfig(f"{_INFANTIL}/zapatos-infantiles/bebe", "unisex", "zapateria", "zapatos"),
    # --- niña 4-16 ---
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/pantalones-y-petos", "niña", "ropa", "pantalones"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/camisetas-y-polos", "niña", "ropa", "camisetas"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/blusas-y-camisas", "niña", "ropa", "camisetas"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/sudaderas-y-chandal", "niña", "ropa", "sudaderas"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/jerseis-y-chaquetas", "niña", "ropa", "sudaderas"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/vestidos", "niña", "ropa", "vestidos"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/faldas", "niña", "ropa", "vestidos"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/ropa-interior", "niña", "ropa", "ropa-interior"),
    CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/pijamas-y-batas", "niña", "ropa", "ropa-interior"),
    # --- niño 4-16 ---
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/pantalones-y-mas", "niño", "ropa", "pantalones"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/camisetas", "niño", "ropa", "camisetas"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/camisas", "niño", "ropa", "camisetas"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/sudaderas-y-chandal", "niño", "ropa", "sudaderas"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/jerseis", "niño", "ropa", "sudaderas"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/ropa-interior", "niño", "ropa", "ropa-interior"),
    CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/pijamas-y-batas", "niño", "ropa", "ropa-interior"),
    # --- bebé niña (6 meses a 3 años) ---
    # Se mapean a la misma categoría que su equivalente de 4-16 para que el vocabulario del
    # catálogo no se parta por rango de edad. `bodies` entra como ropa interior: es la prenda
    # base del rango, y sin ella bebé no tendría ninguna de esa categoría (en 4-16 la cubren
    # `ropa-interior` y `pijamas-y-batas`, que en bebé no existen — comprobado, no supuesto).
    CategoryConfig(f"{_BEBE_NINA}/pantalones-y-petos", "niña", "ropa", "pantalones"),
    CategoryConfig(f"{_BEBE_NINA}/camisetas-y-polos", "niña", "ropa", "camisetas"),
    CategoryConfig(f"{_BEBE_NINA}/blusas-y-camisas", "niña", "ropa", "camisetas"),
    CategoryConfig(f"{_BEBE_NINA}/sudaderas-y-chandal", "niña", "ropa", "sudaderas"),
    CategoryConfig(f"{_BEBE_NINA}/jerseis-y-chaquetas", "niña", "ropa", "sudaderas"),
    CategoryConfig(f"{_BEBE_NINA}/vestidos", "niña", "ropa", "vestidos"),
    CategoryConfig(f"{_BEBE_NINA}/bodies", "niña", "ropa", "ropa-interior"),
    # --- bebé niño (6 meses a 3 años) ---
    CategoryConfig(f"{_BEBE_NINO}/pantalones-y-petos", "niño", "ropa", "pantalones"),
    CategoryConfig(f"{_BEBE_NINO}/camisetas", "niño", "ropa", "camisetas"),
    CategoryConfig(f"{_BEBE_NINO}/blusas-y-camisas", "niño", "ropa", "camisetas"),
    CategoryConfig(f"{_BEBE_NINO}/sudaderas-y-chandal", "niño", "ropa", "sudaderas"),
    CategoryConfig(f"{_BEBE_NINO}/jerseis-y-chaquetas", "niño", "ropa", "sudaderas"),
    CategoryConfig(f"{_BEBE_NINO}/bodies", "niño", "ropa", "ropa-interior"),
]


def _decimal(value: Any) -> Decimal | None:
    """Convierte un precio (float/int/str) a Decimal exacto vía str; None si no hay valor."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _normalize_size(value: Any) -> str | None:
    """Normaliza la talla, que esta tienda a veces escribe dos veces en el mismo campo.

    Medido en una pasada real: `"11-12 años/11 - 12 Años"`. Son la misma talla escrita con otra
    caja y otros espacios, así que comparar en crudo no las pliega. Se parte por `/` y se queda
    la primera forma de cada talla distinta, comparando sin mayúsculas ni espacios. Importa
    porque el valor crudo es lo que ve `size_canon` y, a través de él, el chip del filtro.
    """
    texto = _texto(value)
    if texto is None:
        return None
    partes: list[str] = []
    vistas: set[str] = set()
    for parte in texto.split("/"):
        limpia = _texto(parte)
        if limpia is None:
            continue
        clave = limpia.lower().replace(" ", "")
        if clave not in vistas:
            vistas.add(clave)
            partes.append(limpia)
    return "/".join(partes) or None


def _texto(value: Any) -> str | None:
    """Cadena no vacía, o None. Evita que un `null` de la tienda se cuele como 'None'."""
    if not isinstance(value, str):
        return None
    limpio = " ".join(value.split())
    return limpio or None


def segmentos(category_path: str) -> list[str]:
    """Trocea una ruta de categoría en sus segmentos, sin barras vacías."""
    return [p for p in category_path.strip("/").split("/") if p]


def extraer_data_layer(html: str) -> dict[str, Any] | None:
    """Primer objeto del `dataLayer` embebido en la página, o None si no está.

    None significa «esta página no es la que espero» (un error, un interstitial, un cambio de
    plantilla), y quien llama lo trata como hoja sin veredicto — nunca como catálogo vacío, que
    es como se disparan las bajas masivas.
    """
    m = _DATA_LAYER_RE.search(html)
    if m is None:
        return None
    try:
        cargado = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(cargado, list) or not cargado:
        return None
    primero = cargado[0]
    return primero if isinstance(primero, dict) else None


def pagina_de(dl: dict[str, Any]) -> dict[str, Any]:
    """Bloque `page` del dataLayer (dict vacío si falta)."""
    page = dl.get("page")
    return page if isinstance(page, dict) else {}


def productos_de(dl: dict[str, Any]) -> list[dict[str, Any]]:
    """Productos CRUDOS de una rejilla, tal y como los da la tienda.

    En crudo a propósito: el final de la paginación y la salud de una hoja se deciden con lo que
    la tienda devuelve, no con lo que sobrevive al parseo (ADR). Una página entera de productos
    descartables parsearía a cero y simularía una hoja muerta.
    """
    productos = dl.get("products")
    if not isinstance(productos, list):
        return []
    return [p for p in productos if isinstance(p, dict)]


def total_paginas(dl: dict[str, Any]) -> int:
    """`page.total_pages` saneado (mínimo 1)."""
    total = pagina_de(dl).get("total_pages")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        return total
    return 1


def ruta_resuelta(dl: dict[str, Any]) -> list[str] | None:
    """Ruta que la tienda ha resuelto **de verdad**, en slugs, o None si no lo dice.

    Sale de `products[].hierarchy`, que es el mismo vocabulario que `CategoryConfig.category_path`
    y no necesita traducción: `page.hierarchy` trae las etiquetas de presentación
    (`['Moda y Accesorios', 'Moda Infantil', 'Niña  4-16 años']`), que están localizadas y llevan
    dobles espacios, así que compararlas exigiría inventar un slugificador.
    """
    for producto in productos_de(dl):
        jerarquia = producto.get("hierarchy")
        if isinstance(jerarquia, list) and all(isinstance(s, str) and s for s in jerarquia):
            return [str(s) for s in jerarquia]
    return None


def es_espejismo(dl: dict[str, Any], category_path: str) -> bool:
    """¿La rejilla está devolviendo el catálogo de un ancestro en vez de la hoja pedida? (#54)

    La tienda responde 200 a una ruta que no existe y sirve el catálogo del padre: seis rutas
    inventadas en el recon de #70 parecieron vivas. Aquí se resuelve pidiendo que la ruta pedida
    sea **prefijo** de la resuelta — prefijo y no igualdad porque una rejilla puede legítimamente
    listar productos de sus subcategorías.

    Sin productos no se puede concluir: se cae al largo de `page.hierarchy`, que la tienda sigue
    rellenando aunque la página venga vacía. Y si tampoco está, se devuelve `False`: una página
    ilegible no es prueba de que la hoja haya muerto, y de eso ya se ocupa quien llama.
    """
    pedida = segmentos(category_path)
    resuelta = ruta_resuelta(dl)
    if resuelta is not None:
        return resuelta[: len(pedida)] != pedida
    jerarquia = pagina_de(dl).get("hierarchy")
    if isinstance(jerarquia, list):
        return len(jerarquia) < len(pedida)
    return False


def extraer_enlaces(html: str) -> dict[str, str]:
    """`code_a` -> URL **canónica** de la ficha, leídas de los enlaces de la rejilla.

    La URL no viaja en el `dataLayer`, y hace falta para pedir el detalle y para enseñar la ficha
    en el catálogo. Gana la primera aparición: el mismo producto sale varias veces en la página
    (foto y título son dos enlaces) y todas apuntan al mismo sitio.

    Se les quita la query, y no es cosmética: la tienda cuelga del enlace un
    `?parentCategoryId=…&color=…` **que depende de la hoja por la que has llegado**, así que la
    misma ficha tendría una URL distinta según la categoría y `product.url` cambiaría sola entre
    pasadas. Sin query la ficha responde igual (enruta por id: hasta con el slug cambiado da 200).
    Y hay que desescapar entidades: en el HTML el separador viene como `&amp;`.
    """
    enlaces: dict[str, str] = {}
    for ruta, code_a in _PDP_HREF_RE.findall(html):
        limpia = unescape(str(ruta)).split("?", 1)[0].split("#", 1)[0]
        enlaces.setdefault(str(code_a), _ROOT + limpia)
    return enlaces


def product_signature(producto: dict[str, Any]) -> str:
    """Huella barata del producto en la rejilla: precio efectivo, tachado y disponibilidad.

    Todo sale del listado, que es lo que hace viable esta tienda: sin huella habría que pedir la
    ficha de los ~1.500 productos en cada pasada. Se incluye `status` porque un producto que pasa
    de agotado a disponible no cambia de precio y sí de stock por talla, que es dato que el
    usuario sigue.
    """
    precio = producto.get("price") if isinstance(producto.get("price"), dict) else {}
    assert isinstance(precio, dict)
    f_price = _decimal(precio.get("f_price"))
    o_price = _decimal(precio.get("o_price"))
    estado = str(producto.get("status") or "").upper()
    return f"{f_price}|{o_price}|{estado}"


def parse_listing(
    dl: dict[str, Any], cat: CategoryConfig
) -> list[tuple[ListingEntry, dict[str, Any]]]:
    """Convierte una rejilla en entradas de listado, con su producto crudo al lado.

    Devuelve el crudo junto a la entrada porque quien llama necesita el `code_a` para casar la
    URL de la ficha, y así no se recorre dos veces.
    """
    salida: list[tuple[ListingEntry, dict[str, Any]]] = []
    for producto in productos_de(dl):
        code_a = _texto(producto.get("code_a"))
        if code_a is None:
            continue  # sin id estable no hay nada que seguir
        salida.append(
            (
                ListingEntry(
                    retailer_product_id=code_a,
                    signature=product_signature(producto),
                    gender=cat.gender,
                    section=cat.section,
                    category=cat.category,
                ),
                producto,
            )
        )
    return salida


def extraer_ld_json(html: str) -> list[Any]:
    """Bloques `application/ld+json` de una página, ya deserializados (los ilegibles se omiten)."""
    bloques: list[Any] = []
    for crudo in _LD_JSON_RE.findall(html):
        try:
            bloques.append(json.loads(crudo))
        except json.JSONDecodeError:
            continue
    return bloques


def bloque_schema(bloques: Iterable[Any], tipo: str) -> dict[str, Any] | None:
    """Primer bloque ld+json del `@type` pedido, o None.

    La tienda mete varios bloques por ficha (`BreadcrumbList` y el producto) y a veces envuelve
    alguno en una lista, así que se aplana antes de mirar el `@type`.
    """
    for bloque in bloques:
        candidatos = bloque if isinstance(bloque, list) else [bloque]
        for candidato in candidatos:
            if isinstance(candidato, dict) and candidato.get("@type") == tipo:
                return candidato
    return None


def product_group(bloques: Iterable[Any]) -> dict[str, Any] | None:
    """El `ProductGroup` (ficha con tallas comprables) entre los bloques ld+json, o None."""
    return bloque_schema(bloques, "ProductGroup")


def _tallas_del_selector(html: str) -> list[tuple[str, str | None]]:
    """`(sku, talla)` de cada opción del selector de tallas de la ficha.

    Es la única fuente de tallas cuando la ficha está **agotada del todo**: en ese caso la tienda
    deja de publicar el `ProductGroup` con `hasVariant` y solo emite un `Product` suelto cuyo
    `sku` es el **gtin**, que no es nuestro id de variante. El selector sigue ahí, y sus ids
    (`size_option_<sku>:eci`) sí son los nuestros, con la talla en el `aria-label` de al lado.

    Sin esto, un producto que se agota entero perdería sus tallas: o se dejaría de actualizar
    (enseñando stock que ya no existe) o entrarían variantes con talla nula.
    """
    return [(sku, _normalize_size(talla)) for sku, talla in _SIZE_OPTION_RE.findall(html)]


def _disponible(offers: Any) -> bool:
    """¿La talla está a la venta? `schema.org/InStock` frente a `SoldOut`."""
    if not isinstance(offers, dict):
        return False
    return str(offers.get("availability") or "").rstrip("/").endswith("InStock")


def _fotos_de_galeria(producto: dict[str, Any]) -> list[str]:
    """URLs de la galería `subjectOf.ImageGallery` de una ficha, con respaldo en `image`."""
    urls: list[str] = []
    for bloque in producto.get("subjectOf") or []:
        if not isinstance(bloque, dict) or bloque.get("@type") != "ImageGallery":
            continue
        for imagen in bloque.get("image") or []:
            url = _texto(imagen.get("url")) if isinstance(imagen, dict) else _texto(imagen)
            if url is not None and url not in urls and len(urls) < _MAX_IMAGES_PER_COLOR:
                urls.append(url)
    if not urls:
        directa = _texto(producto.get("image"))
        if directa is not None:
            urls.append(directa)
    return urls


def _tachado_de_offers(offers: Any) -> Decimal | None:
    """Precio tachado de un `Offer` (`priceSpecification` con `StrikethroughPrice`)."""
    if not isinstance(offers, dict):
        return None
    especificaciones = offers.get("priceSpecification")
    if isinstance(especificaciones, dict):
        especificaciones = [especificaciones]
    for spec in especificaciones or []:
        if isinstance(spec, dict) and "StrikethroughPrice" in str(spec.get("priceType") or ""):
            return _decimal(spec.get("price"))
    return None


def parse_pdp(html: str, cat: CategoryConfig, url: str | None = None) -> ScrapedProduct | None:
    """Convierte una ficha (SSR) en `ScrapedProduct`, o None si no es una ficha utilizable.

    Dos fuentes en la misma página y cada una aporta lo que la otra no: el `ld+json` da las
    tallas con su precio y su stock, y el `dataLayer` da el **precio tachado** (`o_price`), que
    schema.org no expresa en la forma normal. Sin el segundo, un producto rebajado entraría como
    si su precio de siempre fuera el de la rebaja, y el detector de descuentos inflados perdería
    justo la referencia que la tienda sí publica.

    **La tienda publica DOS esquemas distintos y hay que entender los dos**, porque el segundo
    aparece solo cuando el producto se agota y por tanto no se ve en un recon de un rato:

    - `ProductGroup` con `hasVariant[]` — mientras quede alguna talla comprable. Es la buena:
      trae sku, talla, precio y disponibilidad **por talla**.
    - `Product` suelto — cuando el producto se agota entero. Pierde las tallas y su `sku` pasa a
      ser el **gtin**, que no es nuestro id de variante. Las tallas se recuperan del selector
      (`_tallas_del_selector`) y entran todas sin stock, que es lo que son. Tratar esta forma
      como "ficha ilegible" habría dejado el producto con el stock de la última pasada, o sea
      enseñando tallas disponibles de algo que ya no se puede comprar.
    """
    bloques = extraer_ld_json(html)
    grupo = product_group(bloques)
    suelto = bloque_schema(bloques, "Product") if grupo is None else None
    producto_ld = grupo or suelto
    if producto_ld is None:
        return None

    dl = extraer_data_layer(html) or {}
    producto_dl = dl.get("product") if isinstance(dl.get("product"), dict) else {}
    assert isinstance(producto_dl, dict)
    precios_dl = producto_dl.get("price") if isinstance(producto_dl.get("price"), dict) else {}
    assert isinstance(precios_dl, dict)

    # Manda `code_a`, que es el id con el que la entrada llegó del listado; `productGroupID` es
    # solo el respaldo (la forma agotada del ld+json no lo trae). El orden importa: los dos campos
    # los genera la plantilla por su cuenta, y si divergieran, preferir el del ld+json partiría el
    # producto en dos filas —una nueva sin huella, que pediría ficha cada día, y la vieja dejando
    # de verse hasta que la histéresis la descatalogara— con el histórico de precio roto en dos.
    pid = _texto(producto_dl.get("code_a")) or _texto(producto_ld.get("productGroupID"))
    nombre = _texto(producto_ld.get("name")) or _texto(producto_dl.get("name"))
    if pid is None or nombre is None:
        return None

    # `o_price` solo aparece cuando hay rebaja; su ausencia significa "no hay tachado", no cero.
    list_price = _decimal(precios_dl.get("o_price"))
    ficha = _texto(producto_ld.get("url")) or url
    color = _texto(producto_ld.get("color"))

    def _variante(sku: str, talla: str | None, precio: Decimal, en_stock: bool) -> ScrapedVariant:
        return ScrapedVariant(
            retailer_variant_id=sku,
            size=talla,
            color=color,
            sku=sku,
            price=precio,
            # El tachado es del producto, no de la talla: la tienda no lo desglosa. Solo se
            # registra si es MAYOR que el precio; si viniera igual (la mentira que Cacles tenía
            # en 248 de 428) sería un descuento del 0 % inventado por nosotros.
            list_price=list_price if list_price is not None and list_price > precio else None,
            in_stock=en_stock,
            url=ficha,
        )

    variantes: list[ScrapedVariant] = []
    urls_foto: list[str] = []
    if grupo is not None:
        for variante in grupo.get("hasVariant") or []:
            if not isinstance(variante, dict):
                continue
            sku = _texto(variante.get("sku"))
            offers = variante.get("offers")
            precio = _decimal(offers.get("price")) if isinstance(offers, dict) else None
            if sku is None or precio is None:
                continue  # sin id de variante o sin precio no se puede seguir
            variantes.append(
                _variante(sku, _normalize_size(variante.get("size")), precio, _disponible(offers))
            )
            foto = _texto(variante.get("image"))
            if (
                foto is not None
                and foto not in urls_foto
                and len(urls_foto) < _MAX_IMAGES_PER_COLOR
            ):
                urls_foto.append(foto)
    else:
        assert suelto is not None
        offers = suelto.get("offers")
        precio = _decimal(offers.get("price")) if isinstance(offers, dict) else None
        if precio is None:
            precio = _decimal(precios_dl.get("f_price"))
        if precio is None:
            return None
        if list_price is None:
            list_price = _tachado_de_offers(offers)
        en_stock = _disponible(offers)
        for sku, talla in _tallas_del_selector(html):
            variantes.append(_variante(sku, talla, precio, en_stock))
        if not variantes:
            # Tercer caso, y el que menos se ve venir: producto de **talla única** (`group_by`
            # vale `"None"` y no hay selector). No es una ficha ilegible —tiene precio, stock y
            # foto—, simplemente no hay nada que elegir. Su sku de variante viaja en el
            # `dataLayer`; el `sku` del ld+json aquí es el gtin y no sirve. Sin esto se perdían
            # 12 de los 289 zapatos, todos patucos de recién nacido: justo el público del brief.
            sku_unico = _texto(producto_dl.get("variant"))
            if sku_unico is not None:
                variantes.append(_variante(sku_unico, None, precio, en_stock))
        urls_foto = _fotos_de_galeria(suelto)
    if not variantes:
        return None

    # Las fotos se atribuyen al MISMO nombre de color que llevan las variantes (`grupo["color"]`),
    # que es la obligación que fijó #26: si los dos nombres salen de sitios distintos, la ficha
    # deja de emparejar foto y precio y falla en silencio.
    imagenes = [ScrapedImage(color=color, url=u) for u in urls_foto] if variantes else []
    return ScrapedProduct(
        retailer_product_id=pid,
        name=nombre,
        gender=cat.gender,
        section=cat.section,
        category=cat.category,
        url=ficha,
        variants=variantes,
        # Hipercor es generalista y no etiqueta el calzado respetuoso: ni tiene hoja `barefoot`
        # como Lefties ni faceta que lo diga. Queda la heurística de texto sobre el nombre, que es
        # el plan B para el que existe (mismo caso que Sfera, #33). Lo que no se pueda afirmar se
        # queda en `desconocido`, que es un estado, no una carencia.
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=pid,
            section=cat.section,
            category=cat.category,
            texts=nombre,
        ),
        image_url=urls_foto[0] if urls_foto else None,
        images=imagenes,
    )


class HipercorStore:
    """Scraper de Hipercor por sus páginas SSR (vía navegador). Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "Hipercor"
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
        self._urls: dict[str, str] = {}  # code_a -> URL de ficha, rellenado por list_catalog()
        self._scan = ScanReport()

    # --- URLs ---------------------------------------------------------------------------------

    @staticmethod
    def grid_url(category_path: str, page: int) -> str:
        """URL de la rejilla. La página 1 no lleva número: es la ruta de categoría a secas."""
        base = f"{_ROOT}/{category_path.strip('/')}/"
        return base if page <= 1 else f"{base}{page}/"

    # --- recorrido ----------------------------------------------------------------------------

    def scopes(self) -> Iterable[ScrapeScope]:
        vistos: list[ScrapeScope] = []
        for cat in self._categories:
            scope = ScrapeScope(cat.gender, cat.section, cat.category)
            if scope not in vistos:
                vistos.append(scope)
        return vistos

    def _iter_category(
        self, session: BrowserSession, cat: CategoryConfig
    ) -> Iterable[tuple[ListingEntry, dict[str, Any]]]:
        """Recorre las páginas de una hoja. Eleva `LeafGone` si la hoja no es utilizable."""
        page = 1
        total = 1
        while page <= total and page <= _MAX_PAGES:
            status, html = session.get_html(self.grid_url(cat.category_path, page))
            if status in GONE_STATUS:
                raise LeafGone(f"{cat.category_path} -> HTTP {status}")
            if status != 200:
                raise LeafUnreadable(f"{cat.category_path} -> HTTP {status}")
            dl = extraer_data_layer(html)
            if dl is None:
                # Ni catálogo vacío ni hoja retirada: una página que no reconozco. Se eleva para
                # que la hoja quede fuera de las bajas en vez de parecer un ámbito vaciado.
                raise LeafUnreadable(f"{cat.category_path} p{page}: sin dataLayer")
            if page == 1:
                # ANTES de emitir nada: una ruta que ya no existe responde 200 con el catálogo del
                # padre, e ingerirlo etiquetaría cientos de productos con el ámbito equivocado.
                if es_espejismo(dl, cat.category_path):
                    raise LeafGone(f"{cat.category_path}: la tienda resuelve otra ruta")
                total = total_paginas(dl)
            crudos = productos_de(dl)
            if not crudos:
                break  # la tienda dice que aquí ya no hay nada
            enlaces = extraer_enlaces(html)
            if not enlaces:
                # La rejilla trae productos pero ninguna URL de ficha: ha cambiado la forma de sus
                # enlaces. Sin URL no se puede pedir el detalle **de ninguno**, y esos productos
                # dejarían de refrescarse en silencio hasta caer por histéresis. Es un fallo
                # nuestro de parseo, así que la hoja se marca como no leída, no como vaciada.
                raise LeafUnreadable(
                    f"{cat.category_path} p{page}: {len(crudos)} productos y ningún enlace a ficha"
                )
            for entrada, producto in parse_listing(dl, cat):
                url = enlaces.get(entrada.retailer_product_id)
                if url is not None:
                    self._urls.setdefault(entrada.retailer_product_id, url)
                yield entrada, producto
            page += 1
        if page > _MAX_PAGES and total > _MAX_PAGES:
            # Se ha visto solo una parte del catálogo de la hoja. Contarla como sana dejaría sus
            # ámbitos elegibles para bajas y descatalogaría producto vivo por no haber cabido.
            raise LeafUnreadable(f"{cat.category_path}: {total} páginas supera el tope")

    def list_catalog(self) -> Iterable[ListingEntry]:
        self._urls = {}
        self._scan = ScanReport()
        emitidos: set[str] = set()
        with self._session_factory() as session:
            session.bloquear(_RUTA_VETADA)
            session.descartar_recursos(_RECURSOS_INUTILES)
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    # El `try` envuelve el bucle entero porque `_iter_category` es un generador: el
                    # fallo de una página se ve al tirar de él, no al crearlo.
                    for entrada, _ in self._iter_category(session, cat):
                        if entrada.retailer_product_id in emitidos:
                            continue  # gana la primera, que es la que fija su ámbito
                        emitidos.add(entrada.retailer_product_id)
                        yield entrada
                except (LeafGone, LeafUnreadable):
                    self._scan.leaf_gone(scope)
                    continue
                self._scan.leaf_ok()

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    # --- detalle ------------------------------------------------------------------------------

    def _categoria_de(self, entry: ListingEntry) -> CategoryConfig:
        """`CategoryConfig` equivalente al ámbito de la entrada.

        El detalle se pide por ficha, no por hoja, así que lo único que hace falta del mapeo es el
        ámbito que ya trae la entrada. Se reconstruye en vez de buscarlo en `CATEGORIES` para que
        una entrada de una hoja retirada entre pasadas siga pudiendo parsearse.
        """
        return CategoryConfig(
            category_path="",
            gender=entry.gender or "",
            section=entry.section or "",
            category=entry.category or "",
        )

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Pide la ficha de cada entrada. Una navegación por producto: aquí está el coste.

        Distingue con cuidado **«ya no está»** de **«no he podido verlo»**, que es la confusión
        que provoca bajas falsas: un producto que sale en el listado y cuya ficha no llega no
        recibe `last_seen_at`, así que a las `SCRAPER_DELIST_MIN_MISSES` pasadas lo descatalogan
        —y las redes de `ingest.py` no lo ven, porque su ámbito sigue lleno en el listado—. Solo
        `GONE_STATUS` significa retirado; un 403 de Akamai o un 5xx que agota reintentos es
        problema nuestro, y por encima de `_MAX_FICHAS_FALLIDAS` la pasada se aborta entera en
        vez de guardar un catálogo mutilado que parece sano (mismo criterio que `zara.py`).
        """
        pendientes = list(entries)
        if not pendientes:
            return
        fallos = 0
        with self._session_factory() as session:
            session.bloquear(_RUTA_VETADA)
            session.descartar_recursos(_RECURSOS_INUTILES)
            for entry in pendientes:
                url = self._urls.get(entry.retailer_product_id)
                if url is None:
                    continue  # sin URL no hay ficha que pedir (no salió en esta pasada)
                status, html = session.get_html(url, _SELECTOR_TALLAS)
                if status in GONE_STATUS:
                    continue  # retirado entre el listado y ahora: lo resuelven las bajas
                if status != 200:
                    fallos += 1
                    _LOG.warning(
                        "hipercor: ficha %s -> HTTP %s (%d fallo/s en esta pasada)",
                        entry.retailer_product_id,
                        status,
                        fallos,
                    )
                    if fallos > _MAX_FICHAS_FALLIDAS:
                        raise DetailUnavailable(
                            f"{fallos} fichas seguidas sin poder leerse (última: HTTP {status}). "
                            "No es que los productos se hayan retirado: es que la tienda no nos "
                            "deja verlos, así que la pasada se aborta sin escribir."
                        )
                    continue
                producto = parse_pdp(html, self._categoria_de(entry), url)
                if producto is not None:
                    yield producto

    # --- vigilancia y bajas -------------------------------------------------------------------

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas (ver `stores.base.SupportsLeafHealth`).

        Pide solo la primera página de cada hoja. Contar productos NO basta como prueba de vida en
        esta tienda: una ruta retirada responde 200 con el catálogo del padre, así que sin la
        comprobación de espejismo este sondeo informaría «12 productos» de una categoría inventada.
        """
        with self._session_factory() as session:
            session.bloquear(_RUTA_VETADA)
            session.descartar_recursos(_RECURSOS_INUTILES)
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    status, html = session.get_html(self.grid_url(cat.category_path, 1))
                except Exception as exc:  # navegador caído, timeout, navegación fallida
                    yield LeafHealth(scope, cat.category_path, None, type(exc).__name__)
                    continue
                if status in GONE_STATUS:
                    yield LeafHealth(scope, cat.category_path, False, f"HTTP {status}")
                    continue
                if status != 200:
                    # Un 403 de Akamai es problema nuestro, no de la hoja: aviso sin veredicto.
                    yield LeafHealth(scope, cat.category_path, None, f"HTTP {status}")
                    continue
                dl = extraer_data_layer(html)
                if dl is None:
                    yield LeafHealth(scope, cat.category_path, None, "sin dataLayer en la página")
                    continue
                if es_espejismo(dl, cat.category_path):
                    yield LeafHealth(
                        scope,
                        cat.category_path,
                        False,
                        "espejismo: la tienda resuelve "
                        + "/".join(ruta_resuelta(dl) or pagina_de(dl).get("hierarchy") or []),
                    )
                    continue
                total = len(productos_de(dl))
                declarados = pagina_de(dl).get("total_products")
                yield LeafHealth(
                    scope,
                    cat.category_path,
                    True if total else None,
                    f"{total} productos en la 1ª página (la tienda declara {declarados})",
                )

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`).

        La ficha enruta por id y da **404 honesto** para uno retirado (verificado: el slug da
        igual, un id inventado responde 404 y un slug cambiado con id vivo responde 200). Es la
        única señal disponible: el endpoint de stock que usa Sfera vive bajo `/api`, o sea en la
        ruta que el `robots.txt` veta.
        """
        pendientes = list(candidates)
        if not pendientes:
            return {}
        veredictos: dict[str, bool] = {}
        with self._session_factory() as session:
            session.bloquear(_RUTA_VETADA)
            session.descartar_recursos(_RECURSOS_INUTILES)
            for candidato in pendientes:
                url = candidato.url or self._urls.get(candidato.retailer_product_id)
                if url is None:
                    continue  # sin URL no hay sondeo posible: sin veredicto
                try:
                    status, _ = session.get_html(url)
                except Exception:
                    continue  # timeout o error de navegación: no prueba nada
                if status in GONE_STATUS:
                    veredictos[candidato.retailer_product_id] = False
                elif status == 200:
                    veredictos[candidato.retailer_product_id] = True
                # Otros códigos (403 de Akamai, 5xx) son problema nuestro: sin veredicto.
        return veredictos
