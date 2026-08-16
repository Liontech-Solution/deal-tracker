"""Scraper de Deditos Barefoot (deditosbarefoot.com): la décima tienda y la segunda barefoot NATIVA.

Es un WooCommerce, plataforma nueva para este repo (hasta ahora había Shopify, el AJAX propio de
Zara, GraphQL en C&A, sitemap en Springfield y Chromium para Sfera e Hipercor). Su **Store API**
es pública y `robots.txt` no la bloquea:

  - listado:     /wp-json/wc/store/v1/products?category=ninos&per_page=100&page=N
  - producto:    /wp-json/wc/store/v1/products/{id}      (solo para `probe_alive`)
  - categorías:  /wp-json/wc/store/v1/products/categories?per_page=100   (árbol y `--tree`)

Delante hay un LiteSpeed **sin Cloudflare**, así que `httpx` normal entra y no hace falta el
contexto sin ALPN de `scraper/tls.py`. Si algún día aparece un 429, el orden de diagnóstico es el
de la cabecera de `cacles.py`: reproducirlo con `curl` **antes** de culpar al ritmo.

Por qué esta tienda (#65): es la segunda barefoot nativa después de Cacles, así que `barefoot='si'`
se declara a nivel de tienda (`tienda_barefoot=True`) sin heurística de texto. Esa premisa venía
del recon del 25/07/2026 y **se remidió el 16/08/2026 antes de escribir una línea**, porque el
catálogo tiene 92 productos de marcas convencionales —Mustang 27, Gioseppo 15, Joma 13, Conguitos,
Hi-Tec—, que son justo las que #66 usa para decir que Zapasaurios NO es puro. Lo que las separa:
**88 de esos 92 llevan «Barefoot» o «Respetuosas» en el nombre que pone la tienda** («Calzado
Barefoot Infantil JOMA ACADEMY JR», «Deportivas Respetuosas Mustang Free»), y 395 de 431 (91,6 %)
lo llevan en todo el catálogo infantil. Son las líneas barefoot de marcas convencionales, no
calzado convencional, y por eso la declaración de tienda se sostiene. `pa_horma` **no** sirve para
decidirlo: es un eje de horma (81 productos «Estrecha», 44 de ellos de Zapy, que es barefoot).

Cinco cosas que hay que tener presentes al tocar este fichero:

1. **El listado NO trae precio ni stock por talla, y su `price_range` miente.** `variations` es
   solo `[{id, attributes}]`, y `prices` describe el producto entero. Medido el 16/08/2026 en el
   producto 9303 (Zapy Salpa): el listado dice `price: "2560"` y `price_range: null` mientras sus
   12 tallas están a 25,60 **y 27,48**. O sea que `price_range` nulo NO significa «precio único»,
   y una huella construida con el listado sería **ciega a un cambio de precio de una talla** — el
   fallo que ensucia `price_history` en silencio. Por eso aquí no hay dos fases reales: como en
   `cacles.py` y `sfera.py`, `list_catalog()` trae también la ficha de cada producto y
   `fetch_details()` sirve de la caché. Cuesta ~431 peticiones por pasada; la alternativa era
   ingerir precios falsos.

2. **Los precios vienen en dos formatos y ninguno es amigable.** La Store API los da en unidades
   menores **como cadena entera** (`"4296"` con `currency_minor_unit: 2`), y la ficha los da como
   float de PHP serializado a JSON, con toda su basura binaria:
   `"display_price": 27.480000000000000426325641456060111522674560546875`. De ahí que `_precio()`
   cuantice siempre a dos decimales y que nunca se construya un `Decimal` desde un `float`.

3. **Una ficha trae entre 5 y 9 `variations_form`**, no uno: además del producto están los de las
   prendas relacionadas que la página pinta debajo. Por eso el formulario se elige **por
   `data-product_id`**, que es el id que hemos pedido, y no por posición. Y el `<form ...>` no se
   puede acotar con `[^>]*`: el JSON del atributo lleva `>` dentro.

   **No es una precaución teórica: la primera pasada real ya se topó con el caso.** El producto
   11711 («Cangrejeras Respetuosas Mayoral 41787») publica en el listado un `permalink` viejo que
   responde **301 hacia la ficha de OTRO producto**, y en esa página no aparece por ningún lado el
   formulario del 11711 — los que hay son 59523, 34025, 59220 y 33776. Con «el primero» le
   habríamos colgado al 11711 los precios y las tallas del 59523, en silencio y sin que ningún
   recuento lo delatara: la Store API dice que el 11711 tiene 4 variaciones y el 59523 tiene
   justamente 4. Lo que hace el parser es no encontrar el suyo y devolver `None`, que la pasada
   traduce a «este producto no se ha visto» y la histéresis de bajas absorbe.

4. **Una categoría que no existe responde 200 con `[]` y `x-wp-total: 0`**, no 404. Comprobado en
   vivo con una ruta inventada. Es la misma mentira que Shopify (#120) y que Sfera con otra forma
   (#54): una hoja muerta parece «este ámbito se ha quedado vacío», que es lo que dispara una baja
   masiva falsa. De ahí el trato asimétrico de la lista vacía en `list_catalog()` y la **hoja
   canaria** de `check_leaves()`, que comprueba que el sondeo sepa distinguir antes de creerse un
   cero.

5. **El color no está donde parece.** `pa_color` existe en los 431 productos pero solo es eje de
   variación en 4; el eje real es `pa_modelo` (306 productos), cuyos términos son cadenas de
   modelo+color («IGOR cangrejeras NEMO SOLID MALVA S10324-362»). La ficha da el *slug* del
   término y el listado el diccionario slug→nombre, así que el color legible sale de cruzar los
   dos. Es feo, pero es lo que la tienda le enseña al usuario en su propio selector, y
   `color_canon` ya existe para normalizarlo.

Las funciones `parse_*` son puras (JSON/HTML -> dataclasses) y se testean con fixtures.
"""

from __future__ import annotations

import contextlib
import html as html_mod
import json
import logging
import random
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..barefoot import classify as classify_barefoot
from ..config import Config
from .base import (
    GONE_STATUS,
    CategoryNode,
    DelistCandidate,
    LeafHealth,
    ListingEntry,
    ProbeVerdict,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)

logger = logging.getLogger(__name__)

SLUG = "deditos"  # a nivel de módulo porque las funciones puras de parseo también lo necesitan
BASE_URL = "https://deditosbarefoot.com/"
_API = BASE_URL + "wp-json/wc/store/v1/"
_LISTADO_URL = _API + "products?category={category}&per_page={per_page}&page={page}"
_PRODUCTO_URL = _API + "products/{product_id}"
_CATEGORIAS_URL = _API + "products/categories?per_page=100&page={page}"

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# La WP REST API topa `per_page` en 100 aunque se pida más. Con 431 productos son 5 páginas.
_PAGE_SIZE = 100
_PAGINA_INICIAL = 1
# Tope de guarda para que un fallo de paginación no gire para siempre. Hoy sobran 5 páginas;
# veinte dan margen para que el catálogo se multiplique por cuatro.
_MAX_PAGES = 20

# Qué proporción de fichas puede fallar en una hoja antes de considerar que no la hemos leído.
#
# Aquí la ficha es la única fuente del precio y del stock (punto 1 de la cabecera), así que un
# producto cuya ficha falla NO se emite. Uno suelto es rutina y lo absorbe la histéresis de bajas:
# la primera pasada real tuvo exactamente uno de 430, el del `permalink` que redirige (punto 3).
# Muchos a la vez son otra cosa —un WAF que empieza a mitigar a la petición N, o una plantilla que
# cambia y rompe el parseo de una parte del catálogo— y ahí callarse sería el fallo: la hoja
# respondería 200, la pasada se cerraría limpia y una porción del catálogo dejaría de refrescar
# precio y stock semana tras semana, con la única huella en el log de un pod que se recicla.
#
# El 10 % es deliberadamente estrecho comparado con el 34 % de `SCRAPER_SCAN_MAX_DEAD_RATIO`,
# porque no mide lo mismo: allí se cuentan hojas que la tienda declara muertas, y aquí fichas que
# no hemos sabido leer. Al cruzarlo, la hoja se marca comprometida, lo que además **saca sus
# ámbitos de las bajas** — que es la respuesta correcta cuando no has podido mirar: lo que no se ha
# leído no está retirado.
_MAX_RATIO_FICHAS_FALLIDAS = 0.10

# Hoja inventada con la que se comprueba, en cada barrido, que un cero significa algo. Ver el
# punto 4 de la cabecera: aquí una categoría retirada responde 200 con lista vacía, así que sin
# esta comprobación el sondeo no puede distinguir «hoja muerta» de «el filtro ha dejado de
# funcionar y todo sale vacío». Lleva el slug feo a propósito: si algún día existiera, se nota.
_HOJA_CANARIA = "categoria-inventada-para-el-vigia-de-deal-tracker"

# Categorías de la tienda que NO son producto seguible. Se comprueban antes de mapear: un producto
# que lleve cualquiera de ellas se descarta entero.
_CATEGORIAS_EXCLUIDAS = frozenset(
    {
        "juguetes",
        "bloques-construccion",
        "juegos-cartas",
        "juegos-exterior",
        "juguetes-sensoriales",
        "tarjeta-regalo",
        "cuidado-del-calzado",  # cremas y cepillos: no es una prenda con talla
        "botellas-infantiles",
        "mochilas-infantiles",
        "chubasqueros",
        "complementos-pelo",
        "chalecos-flotacion",
        # Segunda mano: el mismo modelo se republica con id nuevo cada vez que entra un par usado,
        # así que su identificador no es estable y el histórico de precios no significaría nada.
        "segunda-vida",
    }
)

# Categoría de la tienda -> (sección, categoría) del vocabulario nuestro. **El orden manda**: un
# producto lleva varias categorías a la vez (`ninos`, `zapatos-ninos`, `colegiales`,
# `otono-invierno`...) y se queda con el primero de esta lista que tenga, no con el primero que
# devuelva la API. De más específico a menos.
_CATEGORIA_POR_SLUG: dict[str, tuple[str, str]] = {
    # --- zapatos, con las hojas específicas por delante de la genérica ---
    "ceremonia-merceditas-zapatos-ninos": ("zapateria", "zapatos"),
    "merceditas-zapatos-ninos": ("zapateria", "zapatos"),
    # --- botas ---
    "botas-de-agua": ("zapateria", "botas"),
    "botas-ninos": ("zapateria", "botas"),
    "botas-unisex": ("zapateria", "botas"),
    "senderismo-ninos": ("zapateria", "botas"),
    # --- sandalias ---
    "sandalias-ninos": ("zapateria", "sandalias"),
    # Calzado de agua: acompaña a la sandalia de verano, igual que se resolvió en Cacles.
    "escarpines": ("zapateria", "sandalias"),
    # --- zapatillas (deportiva, de lona y de estar por casa) ---
    "futbol-sala": ("zapateria", "zapatillas"),
    "zapatillas-futbol-barefoot": ("zapateria", "zapatillas"),
    "zapatillas-barefoot-trail-running-infantiles": ("zapateria", "zapatillas"),
    "zapatillas-de-deporte": ("zapateria", "zapatillas"),
    "lonetas-ninos": ("zapateria", "zapatillas"),
    # Mete la zapatilla de casa con la deportiva, con la misma reserva que Cacles: son dos cosas
    # que un padre compra por motivos distintos, y si molesta es un slug más.
    "slippers": ("zapateria", "zapatillas"),
    # --- zapatos (resto) ---
    "colegiales": ("zapateria", "zapatos"),
    "prewalkers": ("zapateria", "zapatos"),
    "ninos-ceremonia": ("zapateria", "zapatos"),
    "zapatos-ninos": ("zapateria", "zapatos"),
    # --- accesorios que sí son producto seguible, con los slugs que ya estrenó Cacles ---
    "plantillas": ("zapateria", "plantillas"),
    "calcetines": ("ropa", "ropa-interior"),
}

# Destino de un producto infantil que no case con ninguna categoría del mapa. La tienda añade
# categorías cada temporada, y perder un producto en silencio es peor que clasificarlo de más:
# `zapatos` es el cajón menos comprometido dentro de zapatería, y el warning deja rastro.
_CATEGORIA_POR_DEFECTO = ("zapateria", "zapatos")

# Pares (sección, categoría) que el parser puede llegar a emitir. Es la mitad de `scopes()`.
_SECCION_CATEGORIA: tuple[tuple[str, str], ...] = tuple(
    dict.fromkeys((*_CATEGORIA_POR_SLUG.values(), _CATEGORIA_POR_DEFECTO))
)

# Géneros que puede emitir `_genero()`. Van los tres aunque la inmensa mayoría salga `unisex`: un
# ámbito declarado de más es inocuo, pero uno declarado de menos deja productos imposibles de dar
# de baja, porque las bajas solo tocan ámbitos escaneados (mismo motivo que en `cacles.py`).
_GENEROS: tuple[str, ...] = ("niño", "niña", "unisex")

# Categorías cuyo nombre la propia tienda pone en femenino. Es la única señal de género fiable que
# publica: los tags `nino`/`nina` solo cubren 29 de 431 productos (medido el 16/08/2026).
_CATEGORIAS_DE_NINA = frozenset({"merceditas-zapatos-ninos", "ceremonia-merceditas-zapatos-ninos"})

# Taxonomías que pueden llevar el color de la variante, por orden de preferencia. `pa_color` es el
# eje de verdad cuando existe (4 productos); en los otros 306 el eje es `pa_modelo`, cuyo término
# es una cadena de modelo+color. Ver el punto 5 de la cabecera.
_TAXONOMIAS_DE_COLOR: tuple[str, ...] = ("pa_color", "pa_modelo")
_TAXONOMIA_TALLA = "pa_talla"

# Los dos atributos de la ficha: el que trae las variaciones y el que dice de quién son. Van por
# separado y se emparejan **acotando por el `<form>` que los contiene**, no por distancia ni por
# orden entre ellos. Escribirlo como un solo patrón «id, luego hasta 400 caracteres, luego
# variaciones» funciona con la plantilla de hoy —los dos van pegados— y se rompe en silencio en
# cuanto el tema meta un atributo entre medias o los reordene: `parse_variaciones` devolvería
# `None` para los productos afectados y esos productos dejarían de refrescarse.
#
# El troceo por `<form` es seguro porque el valor del atributo va HTML-escapado: un `<` dentro del
# JSON aparece como `&lt;`, así que ningún `<form` literal puede caer dentro de una variación.
_RE_VARIACIONES = re.compile(r"data-product_variations=(?P<c>['\"])(?P<var>.*?)(?P=c)", re.S)
_RE_ID = re.compile(r"data-product_id=(?P<c>['\"])(?P<pid>\d+)(?P=c)")
_MARCA_FORMULARIO = "<form"


@dataclass(frozen=True)
class CategoryConfig:
    """Una categoría de la tienda que recorremos.

    Como en Cacles, la hoja NO fija la categoría: `ninos` es el paraguas de todo el catálogo
    infantil y son las categorías de cada producto las que deciden. El género tampoco. Por eso
    solo se declara el rango que acota la hoja.
    """

    category_slug: str
    rango: str  # descripción legible, para los mensajes de `check_leaves()`


# Una sola hoja: `ninos` es el paraguas del catálogo infantil (431 productos el 16/08/2026) e
# incluye lo que cuelga de sus ocho hijas y también los calcetines y plantillas infantiles, que
# la tienda publica además bajo `accesorios`. Recorrer las hijas por separado serían ~14
# peticiones para los mismos productos. El resto del catálogo es de adulto y queda fuera a
# propósito (ver `COBERTURA_DECLARADA` en `scraper/vigia.py`).
CATEGORIES: list[CategoryConfig] = [
    CategoryConfig("ninos", "infantil (todo el catálogo de niño)"),
]


def _precio(value: Any, *, minor_unit: int | None = None) -> Decimal | None:
    """Precio a `Decimal`, cuantizado a dos decimales. Ver el punto 2 de la cabecera.

    Con `minor_unit` el valor viene en unidades menores como entero (la Store API: `"4296"` son
    42,96 €). Sin él viene ya en euros, y puede traer la basura binaria de un float de PHP
    (`27.480000000000000426...`), que es justo lo que quita el `quantize`.

    Nunca `Decimal(float)`: arrastraría esa basura en vez de quitarla.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if minor_unit:
        numero = numero / (Decimal(10) ** minor_unit)
    try:
        return numero.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _texto(valor: Any) -> str:
    """Texto de la tienda, con sus entidades HTML resueltas.

    La Store API **no las resuelve**: publica `Blanditos by Crio&#8217;s` y `B&amp;W CONGUITOS`.
    Medido el 16/08/2026: 47 de los 431 nombres de producto (11 %) y 8 términos de atributo. Sin
    esto la tarjeta del catálogo y el aviso de Telegram enseñarían el `&#8217;` tal cual, y el
    color —que en 306 productos sale del término `pa_modelo`— entraría así en `color_canon`.
    """
    return html_mod.unescape(str(valor))


def _slugs_de_categoria(raw: Mapping[str, Any]) -> list[str]:
    """Los slugs de categoría que trae el producto, en el orden en que los da la tienda."""
    return [
        str(cat["slug"])
        for cat in raw.get("categories") or []
        if isinstance(cat, dict) and cat.get("slug")
    ]


def esta_excluido(slugs: Iterable[str]) -> bool:
    """¿Es de los que no se ingieren (juguete, accesorio que no es prenda, segunda mano)?

    Separado de `_destino()` porque `list_catalog()` lo consulta **antes** de pedir la ficha: son
    ~431 peticiones por pasada y no tiene sentido gastar una en un juego de construcción para
    descartarlo después. `_destino()` lo vuelve a preguntar porque es puro y no puede fiarse de que
    quien lo llame haya filtrado.
    """
    return bool(set(slugs) & _CATEGORIAS_EXCLUIDAS)


def _destino(slugs: Iterable[str], *, nombre: str = "") -> tuple[str, str] | None:
    """Categorías de la tienda -> (sección, categoría). `None` si no debe ingerirse.

    El orden lo manda `_CATEGORIA_POR_SLUG` y no la tienda: un producto lleva varias categorías a
    la vez y la más específica tiene que ganar (unas merceditas son `merceditas-zapatos-ninos` y
    también `zapatos-ninos`).
    """
    presentes = set(slugs)
    if esta_excluido(presentes):
        return None
    for slug, destino in _CATEGORIA_POR_SLUG.items():
        if slug in presentes:
            return destino
    logger.warning(
        "deditos: producto sin categoría mapeada (%s) %r, va a %s/%s",
        ", ".join(sorted(presentes)) or "sin categorías",
        nombre,
        *_CATEGORIA_POR_DEFECTO,
    )
    return _CATEGORIA_POR_DEFECTO


def _genero(raw: Mapping[str, Any], slugs: Iterable[str]) -> str:
    """Género del producto. `unisex` salvo que la tienda diga otra cosa.

    Esta tienda **no publica un eje niño/niña**: de 431 productos, 27 llevan el tag `nino`, 2 el
    `nina` y ninguno los dos (medido el 16/08/2026). Lo único sistemático son las categorías de
    merceditas, que la propia tienda titula «Merceditas barefoot niña».

    `unisex` es por tanto el caso mayoritario y no el raro, igual que en Cacles: el catálogo del
    web ya lo trata como «sale en niño y en niña», así que un producto sin señal queda visible en
    los dos filtros. Inventarle un género a partir del color o del nombre sería lo contrario del
    sesgo de este repo — en la duda, lo que no esconde ni miente.
    """
    if set(slugs) & _CATEGORIAS_DE_NINA:
        return "niña"
    tags = {str(t.get("slug", "")).strip().lower() for t in raw.get("tags") or []}
    nino, nina = "nino" in tags, "nina" in tags
    if nino and not nina:
        return "niño"
    if nina and not nino:
        return "niña"
    return "unisex"


def _terminos_por_taxonomia(raw: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """`{taxonomía: {slug del término: nombre legible}}`, para traducir lo que da la ficha.

    La ficha identifica cada variación por el **slug** del término
    (`gioseppo-zapatillas-80981-marron`) y el listado es quien publica su nombre («GIOSEPPO
    zapatillas 80981 MARRON»). Sin este cruce el color de 306 productos sería un slug.
    """
    mapas: dict[str, dict[str, str]] = {}
    for attr in raw.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        taxonomia = attr.get("taxonomy")
        if not isinstance(taxonomia, str):
            continue
        mapas[taxonomia] = {
            str(t["slug"]): _texto(t.get("name") or t["slug"])
            for t in attr.get("terms") or []
            if isinstance(t, dict) and t.get("slug")
        }
    return mapas


def _valor_de_atributo(
    atributos: Mapping[str, Any], taxonomia: str, terminos: Mapping[str, Mapping[str, str]]
) -> str | None:
    """Lee `attribute_<taxonomía>` de una variación y lo traduce a su nombre legible.

    Si el término no está en el diccionario del listado se devuelve el **slug tal cual**, que es
    feo pero cierto. Medido tras la primera pasada real: 64 de 6685 colores (0,96 %) acaban así,
    y `color_family()` los canoniza igual de bien —`coqueflex-4379-samba-pino-verde` sale `verde`—
    porque busca la palabra dentro. Embellecerlo (quitar guiones, capitalizar) sería inventarle a
    la tienda un nombre que no ha publicado, que es justo lo que este repo no hace.
    """
    valor = atributos.get(f"attribute_{taxonomia}")
    if not valor:
        return None
    slug = str(valor)
    return terminos.get(taxonomia, {}).get(slug) or _texto(slug)


def parse_variaciones(html: str, product_id: str) -> list[Mapping[str, Any]] | None:
    """Las variaciones que la ficha embebe para **este** producto. Pura (HTML -> JSON).

    Devuelve `None` cuando la ficha no las trae, que no es lo mismo que una lista vacía: WooCommerce
    escribe `data-product_variations="false"` cuando el producto pasa de
    `woocommerce_ajax_variation_threshold` y las sirve por AJAX. Hoy no pasa —el producto con más
    variaciones del catálogo, 10792 con 116, las trae todas embebidas—, pero es un ajuste del
    servidor que puede cambiar sin avisarnos, y confundirlo con «este producto no tiene tallas»
    daría de baja el catálogo entero.

    Se elige el formulario **por `data-product_id`** y no por posición: la ficha trae entre 5 y 9,
    los demás son prendas relacionadas, y uno de ellos puede ser directamente de otro producto
    cuando el `permalink` redirige (punto 3 de la cabecera). El id se busca dentro del `<form>` que
    contiene a las variaciones, sin suponerle orden ni distancia a los dos atributos.
    """
    for m in _RE_VARIACIONES.finditer(html):
        inicio = html.rfind(_MARCA_FORMULARIO, 0, m.start())
        fin = html.find(_MARCA_FORMULARIO, m.end())
        trozo = html[inicio if inicio != -1 else 0 : fin if fin != -1 else len(html)]
        propietario = _RE_ID.search(trozo)
        if propietario is None or propietario.group("pid") != product_id:
            continue
        crudo = html_mod.unescape(m.group("var"))
        if crudo.strip() in ("false", ""):
            return None  # por encima del umbral: la tienda las sirve por AJAX
        try:
            variaciones = json.loads(crudo)
        except ValueError:
            return None
        if not isinstance(variaciones, list):
            return None
        return [v for v in variaciones if isinstance(v, dict)]
    return None


def _variantes(
    variaciones: Iterable[Mapping[str, Any]],
    terminos: Mapping[str, Mapping[str, str]],
    url: str | None,
) -> list[ScrapedVariant]:
    """Variantes con precio. `variation_id` es un post ID de WordPress: estable y ajeno a temporada.

    El `sku` **no** identifica la variante: esta tienda repite el mismo para todas las tallas de un
    modelo (medido en el producto 9303, doce tallas con `zapy-deportivas-salpa-jeans-638-lis-678`).
    Se guarda igual porque es lo que la tienda usa de referencia interna, pero quien identifica es
    el id.
    """
    variantes: list[ScrapedVariant] = []
    for v in variaciones:
        vid = v.get("variation_id")
        if vid is None:
            continue
        precio = _precio(v.get("display_price"))
        if precio is None:
            continue  # sin precio no hay nada que vigilar
        # Solo es precio tachado si es MAYOR: WooCommerce manda `display_regular_price` igual al
        # precio cuando el producto no está rebajado, y darlo por bueno inventaría un descuento
        # del 0 % en las dos terceras partes del catálogo que no lo están.
        tachado = _precio(v.get("display_regular_price"))
        if tachado is not None and tachado <= precio:
            tachado = None
        atributos = v.get("attributes")
        atributos = atributos if isinstance(atributos, dict) else {}
        color = next(
            (
                nombre
                for taxonomia in _TAXONOMIAS_DE_COLOR
                if (nombre := _valor_de_atributo(atributos, taxonomia, terminos))
            ),
            None,
        )
        sku = v.get("sku")
        variantes.append(
            ScrapedVariant(
                retailer_variant_id=str(vid),
                size=_valor_de_atributo(atributos, _TAXONOMIA_TALLA, terminos),
                color=color,
                sku=str(sku) if sku else None,
                price=precio,
                list_price=tachado,
                in_stock=bool(v.get("is_in_stock")),
                url=url,
            )
        )
    return variantes


def _imagenes(
    raw: Mapping[str, Any],
    variaciones: Iterable[Mapping[str, Any]],
    terminos: Mapping[str, Mapping[str, str]],
) -> list[ScrapedImage]:
    """Galería, atribuyendo a su color las fotos que la variación trae y dejando el resto sin color.

    El color sale del MISMO sitio que el de la variante (`base.ScrapedImage`): la ficha del web
    empareja foto y precio por ese texto. Las fotos del listado son del producto entero y no se
    pueden atribuir, así que van con `color=None`, que es el valor previsto para eso.
    """
    imagenes: list[ScrapedImage] = []
    vistas: set[str] = set()
    for v in variaciones:
        imagen = v.get("image")
        if not isinstance(imagen, dict):
            continue
        src = imagen.get("src")
        if not src or str(src) in vistas:
            continue
        atributos = v.get("attributes")
        atributos = atributos if isinstance(atributos, dict) else {}
        color = next(
            (
                nombre
                for taxonomia in _TAXONOMIAS_DE_COLOR
                if (nombre := _valor_de_atributo(atributos, taxonomia, terminos))
            ),
            None,
        )
        vistas.add(str(src))
        imagenes.append(ScrapedImage(color=color, url=str(src)))
    for img in raw.get("images") or []:
        if not isinstance(img, dict):
            continue
        src = img.get("src")
        if not src or str(src) in vistas:
            continue
        vistas.add(str(src))
        imagenes.append(ScrapedImage(color=None, url=str(src)))
    return imagenes


def parse_producto(
    raw: Mapping[str, Any], variaciones: Iterable[Mapping[str, Any]]
) -> ScrapedProduct | None:
    """Un producto del listado más las variaciones de su ficha. Pura: sin red."""
    pid = raw.get("id")
    if pid is None:
        return None
    slugs = _slugs_de_categoria(raw)
    nombre = _texto(raw.get("name") or "")
    destino = _destino(slugs, nombre=nombre)
    if destino is None:
        return None  # juguete, accesorio que no es prenda, segunda mano...
    section, category = destino

    permalink = raw.get("permalink")
    url = str(permalink) if permalink else None
    terminos = _terminos_por_taxonomia(raw)
    variaciones = list(variaciones)
    variantes = _variantes(variaciones, terminos, url)
    if not variantes:
        return None  # sin variantes con precio no hay producto que seguir

    pid = str(pid)
    imagenes = _imagenes(raw, variaciones, terminos)
    return ScrapedProduct(
        retailer_product_id=pid,
        name=nombre,
        gender=_genero(raw, slugs),
        section=section,
        category=category,
        url=url,
        variants=variantes,
        # Toda la tienda es barefoot y se declara, como en Cacles: sin esto, unos «Zapatos
        # colegiales» —que no nombran el concepto— saldrían `desconocido` y quedarían invisibles
        # con el filtro por defecto del catálogo. El porqué, medido, está en la cabecera.
        # `classify` sigue devolviendo None para los calcetines, que son ropa.
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=pid,
            section=section,
            category=category,
            tienda_barefoot=True,
        ),
        image_url=imagenes[0].url if imagenes else None,
        images=imagenes,
    )


def product_signature(product: ScrapedProduct) -> str:
    """Huella del producto: precio y stock por variante, ordenados y estables.

    Entra el stock además del precio porque aquí el listado no es una fase más barata que el
    detalle: la ficha ya se ha pedido para construir el producto, así que si la disponibilidad no
    estuviera en la huella un cambio de stock no se llegaría a ingerir nunca. Misma decisión y
    mismo motivo que en `cacles.product_signature`.
    """
    partes = [f"{v.retailer_variant_id}:{v.price}:{int(v.in_stock)}" for v in product.variants]
    return "|".join(sorted(partes))


def parse_categorias(payload: Any) -> list[CategoryNode]:
    """El árbol que publica `/products/categories`. Puro (JSON -> nodos).

    A diferencia de Shopify, aquí el árbol **sí anida**: cada categoría trae su `parent`, así que
    se reconstruye la ruta completa (`ninos/zapatos-ninos/colegiales`) y con ella `cubierta()`
    puede decir «esto ya entra por su padre», que es lo que mantiene corta la lista de
    `COBERTURA_DECLARADA`.

    Las categorías huérfanas —cuyo `parent` no está en la respuesta— se cuelgan de la raíz en vez
    de descartarse: perder una rama del árbol es peor que enseñarla mal colocada, porque el vigía
    dejaría de ver los huecos que hay debajo.
    """
    if not isinstance(payload, list):
        return []
    crudas: dict[int, dict[str, Any]] = {}
    for cat in payload:
        if not isinstance(cat, dict):
            continue
        cid, slug = cat.get("id"), cat.get("slug")
        if not isinstance(cid, int) or not isinstance(slug, str) or not slug:
            continue
        crudas[cid] = cat

    hijas: dict[int, int] = {}
    for cid, cat in crudas.items():
        parent = cat.get("parent")
        if isinstance(parent, int) and parent in crudas and parent != cid:
            hijas[cid] = hijas.get(cid, 0)
            hijas[parent] = hijas.get(parent, 0) + 1

    def ruta(cid: int) -> list[str]:
        partes: list[str] = []
        visto: set[int] = set()
        actual: int | None = cid
        while actual is not None and actual in crudas and actual not in visto:
            visto.add(actual)
            partes.append(str(crudas[actual]["slug"]))
            padre = crudas[actual].get("parent")
            actual = padre if isinstance(padre, int) and padre in crudas else None
        return list(reversed(partes))

    nodos: list[CategoryNode] = []
    for cid, cat in crudas.items():
        partes = ruta(cid)
        cuenta = cat.get("count")
        nodos.append(
            CategoryNode(
                path="/".join(partes),
                title=str(cat.get("name") or partes[-1]),
                count=cuenta if isinstance(cuenta, int) and not isinstance(cuenta, bool) else None,
                depth=len(partes),
                has_children=hijas.get(cid, 0) > 0,
            )
        )
    nodos.sort(key=lambda n: n.path)
    return nodos


class DeditosStore:
    """Scraper de Deditos Barefoot. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "Deditos Barefoot"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._cache: dict[str, ScrapedProduct] = {}

    def scopes(self) -> Iterable[ScrapeScope]:
        """Producto cartesiano de géneros × (sección, categoría) posibles.

        No se deduce de `self._categories`: aquí la hoja no fija ni el género ni la categoría —los
        decide el parser producto a producto—, así que el ámbito hay que declararlo desde lo que el
        parser PUEDE emitir. Declarar de menos dejaría productos fuera del alcance de las bajas y
        por tanto imposibles de descatalogar. Mismo razonamiento que `cacles.scopes()`.
        """
        return [
            ScrapeScope(genero, section, category)
            for genero in _GENEROS
            for section, category in _SECCION_CATEGORIA
        ]

    def _client(self) -> httpx.Client:
        """Cliente HTTP. **`Accept-Encoding` no se toca**, y esa es la parte que hay que respetar.

        La tentación es escribirlo a mano —la ficha son 1,5 MB en crudo y 224 KB comprimida, y es
        la petición que se repite 431 veces por pasada—, pero httpx anuncia exactamente los
        códecs que sabe deshacer, y `br` **no** está entre ellos salvo que se instale `brotli`.
        Anunciarlo igualmente hace que el LiteSpeed de esta tienda responda en brotli y que httpx
        entregue el cuerpo comprimido: la primera petición de la primera pasada murió con
        `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xf4 in position 0`, que no se parece
        en nada a su causa. Con el default, gzip ya deja la ficha en ~250 KB.
        """
        return httpx.Client(
            headers={"User-Agent": self._config.user_agent},
            timeout=self._config.request_timeout,
            follow_redirects=True,
        )

    def _polite_pause(self) -> None:
        """Pausa base entre peticiones con jitter (una cadencia fija es más detectable)."""
        base = self._config.request_delay
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _get(self, client: httpx.Client, url: str, *, accept: str) -> httpx.Response:
        """GET con reintentos y backoff exponencial + jitter ante throttling/errores de red."""
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.get(url, headers={"Accept": accept})
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                self._backoff(attempt, retry_after=exc.response.headers.get("Retry-After"))
            except httpx.TransportError:
                if attempt == retries:
                    raise
                self._backoff(attempt)
        raise AssertionError("inalcanzable: el bucle sale por return o por raise")

    def _get_json(self, client: httpx.Client, url: str) -> Any:
        return self._get(client, url, accept="application/json").json()

    def _get_html(self, client: httpx.Client, url: str) -> str:
        return self._get(client, url, accept="text/html").text

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Espera exponencial (respeta `Retry-After` si viene) con jitter.

        `float()` y no `.isdigit()`, por lo mismo que en `cacles.py`: la cabecera admite decimales
        y `"2.0".isdigit()` es False, así que se habría ignorado en silencio.
        """
        wait = self._config.retry_backoff * (2**attempt)
        if retry_after:
            # También admite una fecha HTTP: no la interpretamos, y en ese caso nos quedamos con el
            # backoff exponencial en vez de reventar.
            with contextlib.suppress(ValueError):
                wait = max(wait, float(retry_after))
        time.sleep(wait * random.uniform(0.8, 1.2))

    def _pagina(self, client: httpx.Client, category: str, page: int) -> list[Mapping[str, Any]]:
        """Los productos CRUDOS de una página del listado."""
        url = _LISTADO_URL.format(category=category, per_page=_PAGE_SIZE, page=page)
        payload = self._get_json(client, url)
        if not isinstance(payload, list):
            raise ValueError(f"deditos: respuesta inesperada en {category!r} pág. {page}")
        return [p for p in payload if isinstance(p, dict)]

    def _producto_completo(
        self, client: httpx.Client, raw: Mapping[str, Any]
    ) -> ScrapedProduct | None:
        """Cruza el producto del listado con las variaciones de su ficha.

        Un fallo de la ficha **no tumba la pasada**: se registra y el producto se omite de esta
        pasada, que la ingesta interpreta como «no visto» y absorbe con la histéresis de bajas. Lo
        que no se hace es emitirlo sin variantes, que sería decir que se ha quedado sin tallas.
        """
        pid = raw.get("id")
        url = raw.get("permalink")
        if pid is None or not url:
            return None
        if esta_excluido(_slugs_de_categoria(raw)):
            return None  # antes de gastar la petición: no se pide la ficha de un juguete
        try:
            html = self._get_html(client, str(url))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("deditos: no se pudo leer la ficha de %s: %s", pid, type(exc).__name__)
            return None
        variaciones = parse_variaciones(html, str(pid))
        if variaciones is None:
            logger.warning(
                "deditos: la ficha de %s no trae su `data-product_variations`; se omite el "
                "producto en esta pasada. Las dos causas vistas: un `permalink` viejo que "
                "redirige a la ficha de otro producto (medido en el 11711), o el umbral AJAX de "
                "WooCommerce. En ninguna de las dos vale coger el formulario de otro",
                pid,
            )
            return None
        return parse_producto(raw, variaciones)

    def _hoja_comprometida(self, leaf: str, motivo: str) -> None:
        """Cuenta la hoja como caída y saca TODOS sus ámbitos de las bajas.

        Se cuenta como UNA hoja (no una por ámbito) para que `leaves_total` siga midiendo lo que
        dice medir; los ámbitos restantes se añaden aparte porque esta tienda cubre todos con una
        sola hoja. Con una única hoja configurada, `dead_ratio` pasa a 1.0 y la ingesta aborta sin
        escribir, que es la red que queremos. Mismo trato que en `cacles.py`.
        """
        ambitos = list(self.scopes())
        self._scan.leaf_gone(ambitos[0], leaf)
        self._scan.failed_scopes.update(ambitos[1:])
        logger.warning("deditos: %s; se omiten las bajas de esta pasada", motivo)

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Lista el catálogo infantil **y pide la ficha de cada producto**.

        Lo segundo no es un descuido de la separación en dos fases: es que aquí el listado no
        publica precio ni stock por talla y su `price_range` miente (punto 1 de la cabecera), así
        que no existe ninguna huella barata que sea fiel. `fetch_details()` sirve de la caché.
        """
        self._scan = ScanReport()
        self._cache = {}
        emitted: set[str] = set()
        with self._client() as client:
            for cat in self._categories:
                slug = cat.category_slug
                viva = False
                truncada = True  # solo deja de serlo al ver el final real de la paginación
                pedidas = fallidas = 0  # fichas: ver `_MAX_RATIO_FICHAS_FALLIDAS`
                for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
                    crudos = self._pagina(client, slug, page)
                    if not crudos:
                        # Una categoría retirada NO da 404: responde 200 con la lista vacía, igual
                        # que la página siguiente a la última (punto 4 de la cabecera). En la
                        # primera página eso es una hoja muerta y hay que decirlo —si no, la
                        # ingesta lee «este ámbito se ha quedado vacío» y da de baja el catálogo
                        # entero—; a partir de la segunda es el fin normal de la paginación.
                        if not viva:
                            self._hoja_comprometida(
                                slug,
                                f"la categoría {slug!r} no devolvió ningún producto, "
                                "así que se trata como hoja retirada",
                            )
                        truncada = False
                        break
                    viva = True
                    for raw in crudos:
                        pid = raw.get("id")
                        if pid is None or str(pid) in emitted:
                            continue
                        if esta_excluido(_slugs_de_categoria(raw)):
                            continue  # ni se pide la ficha ni cuenta como fallo: no es nuestro
                        pedidas += 1
                        producto = self._producto_completo(client, raw)
                        if producto is None:
                            fallidas += 1
                            continue
                        pid = producto.retailer_product_id
                        emitted.add(pid)
                        self._cache[pid] = producto
                        yield ListingEntry(
                            retailer_product_id=pid,
                            signature=product_signature(producto),
                            gender=producto.gender,
                            section=producto.section,
                            category=producto.category,
                        )
                    if len(crudos) < _PAGE_SIZE:
                        # Una página incompleta ES la última, y saberlo aquí ahorra la petición
                        # extra que solo servía para que la tienda respondiera vacío.
                        truncada = False
                        break
                if viva and truncada:
                    # Se agotó el tope de páginas sin llegar al final: hemos visto SOLO una parte
                    # del catálogo. Contarla como hoja sana sería el peor de los dos errores — lo
                    # que no se ha llegado a mirar no está retirado, y a las `delist_min_misses`
                    # pasadas se descatalogaría solo por no haber cabido en el tope.
                    self._hoja_comprometida(
                        slug,
                        f"{slug!r} agotó el tope de {_MAX_PAGES} páginas sin llegar al final, "
                        "así que el catálogo leído está incompleto",
                    )
                elif viva and fallidas and fallidas / pedidas > _MAX_RATIO_FICHAS_FALLIDAS:
                    self._hoja_comprometida(
                        slug,
                        f"{fallidas} de {pedidas} fichas de {slug!r} no se pudieron leer "
                        f"(> {_MAX_RATIO_FICHAS_FALLIDAS:.0%}), así que el catálogo leído está "
                        "incompleto y no se sabe de qué parte",
                    )
                elif viva:
                    if fallidas:
                        # Por debajo del umbral: se sigue, pero queda dicho cuántas y sobre cuántas.
                        # Sin este recuento, «una ficha falló» y «cuarenta fichas fallaron» se leen
                        # igual de mal en un log por producto.
                        logger.warning(
                            "deditos: %d de %d fichas de %r no se pudieron leer; por debajo del "
                            "%.0f%% se sigue, y esos productos se omiten de esta pasada",
                            fallidas,
                            pedidas,
                            slug,
                            _MAX_RATIO_FICHAS_FALLIDAS * 100,
                        )
                    self._scan.leaf_ok()

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        # `list_catalog()` ya pidió la ficha de cada producto, así que se sirve desde caché sin
        # una sola petición extra. Ver su docstring y el punto 1 de la cabecera.
        for entry in entries:
            producto = self._cache.get(entry.retailer_product_id)
            if producto is not None:
                yield producto

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def _cuantos(self, client: httpx.Client, slug: str) -> int | None:
        """Cuántos productos declara la tienda en esa categoría, según `X-WP-Total`.

        `None` = no se pudo saber (fallo nuestro), que NO es lo mismo que cero. La cabecera es lo
        que hace barato este sondeo: se pide una sola unidad y se lee el total.
        """
        url = _LISTADO_URL.format(category=slug, per_page=1, page=_PAGINA_INICIAL)
        resp = self._get(client, url, accept="application/json")
        total = resp.headers.get("X-WP-Total")
        if total is None:
            return None
        try:
            return int(total)
        except ValueError:
            return None

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las categorías configuradas (ver `stores.base.SupportsLeafHealth`).

        **Con una hoja canaria por delante**, y esa es la parte que importa. Aquí una categoría
        retirada responde 200 con la lista vacía (punto 4 de la cabecera), o sea que el veredicto
        «muerta» se apoya en un cero — y un cero también es lo que devolvería un filtro `category`
        que la tienda dejara de entender, con todas las hojas cayendo a la vez. La canaria es una
        ruta inventada: si ella también trae productos, el sondeo no discrimina y ninguna hoja
        puede declararse muerta. Es el mismo remedio que usa H&M para su hoja invisible.
        """
        with self._client() as client:
            try:
                canaria = self._cuantos(client, _HOJA_CANARIA)
            except (httpx.HTTPError, ValueError) as exc:
                canaria = None
                fallo_canaria: str | None = type(exc).__name__
            else:
                fallo_canaria = None

            discrimina = canaria == 0
            if not discrimina:
                motivo = (
                    f"la hoja canaria falló ({fallo_canaria})"
                    if fallo_canaria
                    else f"la hoja canaria inventada devolvió {canaria} productos"
                )

            for cat in self._categories:
                scope = ScrapeScope(None, None, None)  # la hoja no acota ámbito en esta tienda
                leaf = cat.category_slug
                try:
                    total = self._cuantos(client, leaf)
                except httpx.HTTPStatusError as exc:
                    yield LeafHealth(scope, leaf, None, f"HTTP {exc.response.status_code}")
                    continue
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, leaf, None, type(exc).__name__)
                    continue
                if total is None:
                    yield LeafHealth(scope, leaf, None, "HTTP 200 pero sin cabecera X-WP-Total")
                elif total > 0:
                    yield LeafHealth(scope, leaf, True, f"HTTP 200 con {total} productos")
                elif not discrimina:
                    # Cero, pero el sondeo no ha probado que sepa distinguir: no se declara muerta.
                    yield LeafHealth(
                        scope,
                        leaf,
                        None,
                        f"HTTP 200 con 0 productos, pero {motivo}: el sondeo no puede "
                        "distinguir una hoja retirada de un filtro que ha dejado de funcionar",
                    )
                else:
                    yield LeafHealth(scope, leaf, False, "HTTP 200 con 0 productos")

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. El árbol de categorías, que aquí SÍ anida.

        `root` acota: se devuelve la rama que cuelga de esa ruta (incluida ella). Con la raíz vacía
        o `/` sale el árbol entero, que son 84 categorías en una o dos peticiones.
        """
        nodos: list[CategoryNode] = []
        with self._client() as client:
            for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
                payload = self._get_json(client, _CATEGORIAS_URL.format(page=page))
                if not isinstance(payload, list) or not payload:
                    break
                nodos += parse_categorias(payload)
                if len(payload) < 100:
                    break
        raiz = (root or "").strip("/")
        if not raiz:
            return nodos
        return [n for n in nodos if n.path == raiz or n.path.startswith(f"{raiz}/")]

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las categorías de `CATEGORIES` (hoy, `ninos`).

        Va el slug pelado y no la ruta completa porque `ninos` es raíz del árbol: su `path` es
        `ninos`, y `cubierta()` se lleva por delante a sus ocho hijas y sus nietas.
        """
        return [cat.category_slug for cat in self._categories]

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. Una sola raíz, y es **el árbol entero**.

        `tree_roots()` existe para acotar el coste del barrido semanal, porque hay tiendas cuyo
        árbol se pide con una petición por nodo (Sfera baja nivel a nivel). Aquí pasa lo contrario:
        `/products/categories?per_page=100` devuelve las 84 categorías de una vez, así que trocear
        por ramas **multiplicaría** las peticiones para leer lo mismo.

        Y la raíz única gana además en cobertura, que es lo que decide: el informe no señala las
        raíces —son lo que se ha preguntado, no un hallazgo—, así que declarar `ninos`, `adultos`,
        `juguetes`… dejaría **invisible una categoría de primer nivel nueva**, que es justo el
        cambio que esta capa existe para ver. Con el árbol entero, lo nuevo aparece salvo que
        cuelgue de algo ingerido o declarado.
        """
        return ["/"]

    def tree_separator(self) -> str:
        """Ver `SupportsCategoryTree`. Las rutas las construye `parse_categorias` con `/`."""
        return "/"

    def _probe_one(self, client: httpx.Client, product_id: str) -> bool | None:
        """¿Sigue a la venta? True/False; None si la tienda no da respuesta utilizable."""
        try:
            payload = self._get_json(client, _PRODUCTO_URL.format(product_id=product_id))
        except httpx.HTTPStatusError as exc:
            # 404/410 son un veredicto ("ese id ya no existe"); el resto, tras agotar los
            # reintentos, es un fallo nuestro y no vale como prueba de retirada. Se usa
            # `GONE_STATUS` y no un 404 suelto para no dejar fuera el 410.
            return False if exc.response.status_code in GONE_STATUS else None
        except (httpx.TransportError, ValueError):  # red caída o respuesta no-JSON
            return None
        if not isinstance(payload, dict):
            return None  # forma inesperada: no arriesgamos una baja con esto
        return payload.get("id") is not None

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, ProbeVerdict]:
        """Confirmación activa (ver `stores.base.SupportsAliveProbe`): un GET por candidato.

        Solo `ALIVE`/`DEAD`. El tercer veredicto, `UNBUYABLE` (#197), sería aplicable aquí —la
        Store API trae `is_in_stock`, así que se puede saber si no queda ni una talla comprable—
        pero **el criterio para usarlo lo está decidiendo #426**, que además tiene que resolver qué
        se hace con las otras tiendas con población. Emitirlo aquí por delante sería adelantar esa
        decisión desde la tienda que menos la ha medido.
        """
        verdicts: dict[str, ProbeVerdict] = {}
        with self._client() as client:
            for candidate in candidates:
                verdict = self._probe_one(client, candidate.retailer_product_id)
                if verdict is not None:  # sin veredicto -> se omite del mapa
                    verdicts[candidate.retailer_product_id] = (
                        ProbeVerdict.ALIVE if verdict else ProbeVerdict.DEAD
                    )
        return verdicts
