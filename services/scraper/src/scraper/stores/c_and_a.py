"""Scraper de C&A (c-and-a.com): la tienda que trae la cifra de descuento del propio retailer.

Entra por la API GraphQL del listado, con **persisted query** (APQ) y `httpx` puro — sin navegador,
sin cookies y sin trucos de huella TLS:

  - listado:  POST /api?o=list        (una petición por página de 60 productos)
  - producto: GET  /es/es/shop/{slug}-{usim}/{colorCode}   (solo para `probe_alive`)

Como en Cacles y Sfera, **el listado ya es el detalle**: trae colores, tallas, stock, precios e
imágenes, así que `fetch_details()` sirve de caché sin una sola petición extra. El catálogo infantil
entero son ~46 peticiones.

Por qué esta tienda (#78): es la única del brief que publica **`lowestPrice30Days`**, el mínimo de
los últimos 30 días al que la obliga la directiva Ómnibus. Hasta ahora el detector de descuentos
engañosos solo tenía nuestro propio histórico; esta es la primera vez que hay **la cifra del
retailer** para contrastar. Medido el 02/08/2026 sobre las 364 variantes que traen tachado Y este
campo: **67 (18 %) anuncian descuento mientras la tienda declara haberlas vendido más baratas dentro
de esos mismos 30 días**. Se guarda en `price_history.retailer_min_30d` (migración `0018`) desde la
primera pasada aunque el detector todavía no lo use: el histórico no se reconstruye hacia atrás.

Cinco cosas que hay que tener presentes al tocar este fichero. Las cuatro primeras se midieron
contra el sitio real y **ninguna de ellas falla de forma visible**: todas devuelven 200.

1. **Sin `x-country` y `x-language`, la API sirve el catálogo ALEMÁN.** Con solo `content-type` y
   `origin` responde 200 y parece correcta — pero es `prod_products_DE_de`, con `uri: /de/de/…`,
   nombres en alemán y precios de Alemania. Las dos cabeceras hacen falta **juntas**: cada una por
   su lado devuelve HTML de error. Es el peor modo de fallo de esta tienda porque no es un fallo:
   es ingerir otro país sin que salte nada.
2. **La paginación arranca en 0.** `page: 1` no es la primera página, es la segunda, y las páginas
   son disjuntas: empezar en 1 se salta 60 productos de cada hoja en silencio. Medido en `3-7-1`
   (`productCount: 172`): 60 + 60 + 52 = 172 exacto.
3. **Un hash de persisted query caducado da 200**, no 4xx, con `PersistedQueryNotFound` en el
   cuerpo. Un parser que fuese directo a `data.list.products` vería la lista vacía en TODAS las
   hojas a la vez el día que desplieguen, y lo leería como «el catálogo se ha vaciado». Por eso
   `_extraer_lista()` mira `errors` antes que `data`. Y por eso se auto-repara: esa misma respuesta
   trae `x-release-hash`, el bundle se llama igual (`/static/main.<hash>.js`) y dentro está el hash
   nuevo. Cuesta **cero peticiones** mientras el pinneado valga.
4. **Una hoja muerta responde 200 con `productCount: 0`**, igual que Cacles. Pero aquí hay antídoto:
   una hoja viva pasada de página devuelve `productCount` **intacto** con 0 productos, así que el
   `productCount` desambigua sin heurística posicional. Aun así se conserva el trato asimétrico de
   `cacles.py`: vacío en la primera página es hoja muerta, a partir de la segunda es fin normal.
5. **Hay `usim` repetidos dentro de la misma hoja** (172 items brutos → 165 productos únicos), así
   que `list_catalog()` deduplica con «gana la primera», como Cacles.

Cumplimiento (comprobado el 02/08/2026): `robots.txt` responde 200 y veta buscador, facetas y
`/*?pagenumber`, pero **no `/api` ni las rutas de categoría**, y trae un `allow: /*` explícito; no
declara `Crawl-delay`. Leídos también el aviso legal y las condiciones generales de contrato: no hay
cláusula sobre scraping, robots, extracción automatizada, minería de datos ni reproducción
sistemática. Límite declarado: las dos páginas son SPA y el texto se leyó del payload embebido.

Las funciones `parse_*` son puras (JSON -> dataclasses) y se testean con fixtures.
"""

from __future__ import annotations

import contextlib
import logging
import random
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import Config
from .base import (
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

SLUG = "c-and-a"  # con guiones: va en `--retailer`, en `retailer.slug` y en el nombre del CronJob
BASE_URL = "https://www.c-and-a.com"
_API_URL = f"{BASE_URL}/api?o=list"
_BUNDLE_URL = BASE_URL + "/static/main.{release}.js"
# Cloudinary, pero las transformaciones están vetadas para `productimages/` (`w_400/…` -> 400; solo
# funcionan en `/marketing/`). Se hotlinkea la imagen cruda, ~170 KB. Tercera tienda y tercera forma
# de tratar el ancho, después de la de Zara y la de El Corte Inglés.
_IMAGE_URL = BASE_URL + "/image/upload{folder}/{file}"
_PRODUCT_URL = BASE_URL + "{uri}"

# Hash de la persisted query del listado, pinneado. Cambia cuando C&A despliega; cuando eso pase,
# `_resolver_hash()` lo saca del bundle solo. Actualizarlo aquí es opcional y solo ahorra la
# petición de 1,6 MB del primer día tras un despliegue.
_HASH_PINNEADO = "1aa00d7ef4e80d7dfa3843b8fa289599174b5391e5236a9521c767640f217ab2"
# Ancla para encontrar el hash dentro del bundle minificado: es un trozo del TEXTO de la query del
# listado, y el `sha256:"..."` que le sigue es el suyo. Se ancla en el texto de la query y no en el
# nombre de la operación porque lo segundo está minificado y lo primero no puede estarlo.
_ANCLA_QUERY = "hasSaleProducts}}"
_RE_SHA256 = re.compile(r'sha256:"([0-9a-f]{64})"')
_CODIGO_HASH_CADUCADO = "PERSISTED_QUERY_NOT_FOUND"

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# La API devuelve 60 por página y no admite pedir otro tamaño (no hay parámetro). Una página con
# MENOS de 60 productos crudos es la última.
_PAGE_SIZE = 60
_PAGINA_INICIAL = 0
# Tope de guarda para que un fallo de paginación no gire para siempre. La hoja más grande hoy son
# 4 páginas; treinta dan margen de sobra.
_MAX_PAGES = 30
# Profundidad máxima al enumerar el árbol con `--tree`, por si `navigation` apunta hacia atrás.
_MAX_TREE_DEPTH = 4


@dataclass(frozen=True)
class CategoryConfig:
    """Una hoja del catálogo que recorremos.

    `ipim_id` es el identificador jerárquico de C&A (`3-7-1`). Se usa él y **nunca la etiqueta**:
    las etiquetas del árbol vienen en el idioma de la tienda, pero los `ipimId` no cambian con el
    idioma — que es justo lo que los hace buenos identificadores de hoja.
    """

    ipim_id: str
    gender: str  # niño | niña
    section: str  # ropa | zapateria
    category: str  # nuestro vocabulario, no el suyo


# Hojas del árbol infantil que mapean al vocabulario del brief, sacadas de `category_tree()` y no
# adivinadas (ver `just tree c-and-a 3`). El árbol publica `3` = Niños con `3-1` Ropa de niña y
# `3-7` Ropa de niño; `3-2` «Destacados» es una rama transversal de promoción que solapa con las
# dos y NO se ingiere, o duplicaría el trabajo para los mismos productos.
#
# **C&A no tiene zapatería infantil**: su árbol no publica ninguna hoja de calzado. Esta tienda
# aporta solo `ropa`, y está bien — la zapatería la cubren Hipercor (#79) y H&M (#77).
#
# Fuera del mapa a propósito, medido el 02/08/2026: `3-1-2-5` y `3-7-2-3` (Shorts) aportan **0
# productos nuevos** porque están contenidos enteros en las hojas de Pantalones. Y lo que existe
# pero no es del brief se queda fuera igual que en Sfera: Baño, Pijamas, Chaquetas, Accesorios,
# Conjuntos, Básicos, Premium, Packs, Ropa de deporte, Novedades y Vuelta al cole.
CATEGORIES: list[CategoryConfig] = [
    # --- niña (rama 3-1) ---
    CategoryConfig("3-1-1", "niña", "ropa", "camisetas"),  # Camisetas y tops
    CategoryConfig("3-1-2", "niña", "ropa", "pantalones"),  # Pantalones
    CategoryConfig("3-1-17", "niña", "ropa", "pantalones"),  # Vaqueros
    # La hoja es "Vestidos y faldas" y se lleva las dos cosas a `vestidos`: sus hijas son 84
    # vestidos y 36 faldas, y el brief no tiene slug para falda. Meterlas aparte estrenaría una
    # categoría que ninguna otra tienda alimenta, y dejarlas fuera perdería 36 prendas.
    CategoryConfig("3-1-3", "niña", "ropa", "vestidos"),
    CategoryConfig("3-1-4", "niña", "ropa", "ropa-interior"),  # Ropa interior
    CategoryConfig("3-1-11", "niña", "ropa", "ropa-interior"),  # Calcetines
    CategoryConfig("3-1-7", "niña", "ropa", "sudaderas"),  # Jerséis y sudaderas
    # --- niño (rama 3-7) ---
    CategoryConfig("3-7-1", "niño", "ropa", "camisetas"),  # Camisetas y camisas
    CategoryConfig("3-7-2", "niño", "ropa", "pantalones"),  # Pantalones
    CategoryConfig("3-7-16", "niño", "ropa", "pantalones"),  # Vaqueros
    CategoryConfig("3-7-3", "niño", "ropa", "ropa-interior"),  # Ropa interior
    CategoryConfig("3-7-10", "niño", "ropa", "ropa-interior"),  # Calcetines
    CategoryConfig("3-7-6", "niño", "ropa", "sudaderas"),  # Jerséis y sudaderas
]


def _precio(value: Any) -> Decimal | None:
    """C&A da los precios como número JSON (`6.99`), no en céntimos.

    Se pasa por `str()` antes del `Decimal` a propósito: `Decimal(6.99)` arrastraría la basura
    binaria del float.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _extraer_lista(payload: Any) -> dict[str, Any]:
    """Saca `data.list` de la respuesta, o eleva con el motivo real.

    **Mira `errors` antes que `data`**, que es lo que evita el modo de fallo de la cabecera (3): un
    hash caducado responde 200 y sin esto se leería como una hoja vacía en todas las hojas a la vez.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{SLUG}: respuesta que no es un objeto JSON")
    errores = payload.get("errors")
    if _es_hash_caducado(errores):
        raise HashCaducado(f"{SLUG}: persisted query desconocida (¿han desplegado?)")
    if errores:
        raise ValueError(f"{SLUG}: la API devolvió errores: {errores}")
    lista = (payload.get("data") or {}).get("list")
    if not isinstance(lista, dict):
        raise ValueError(f"{SLUG}: la respuesta no trae data.list")
    return lista


def _es_hash_caducado(errores: Any) -> bool:
    """`True` si el error es el de persisted query desconocida, que se arregla solo."""
    if not isinstance(errores, list):
        return False
    return any(
        isinstance(e, dict) and (e.get("extensions") or {}).get("code") == _CODIGO_HASH_CADUCADO
        for e in errores
    )


class HashCaducado(Exception):
    """C&A ha desplegado y el hash pinneado ya no existe. Se resuelve releyendo el bundle."""


def _imagenes(variantes_crudas: Iterable[Mapping[str, Any]]) -> list[ScrapedImage]:
    """Galería, atribuyendo cada foto al color que retrata.

    El color sale del MISMO campo que alimenta `ScrapedVariant.color` (`color.label`), que es lo que
    pide `base.ScrapedImage`: la ficha empareja foto y precio por ese texto, y si los dos nombres se
    desalinean el emparejamiento falla en silencio.
    """
    imagenes: list[ScrapedImage] = []
    for v in variantes_crudas:
        color = ((v.get("color") or {}).get("label") or "").strip() or None
        for clave in ("variantImage", "modelImage"):
            img = v.get(clave) or {}
            folder, file = img.get("folder"), img.get("file")
            if folder and file:
                imagenes.append(
                    ScrapedImage(color=color, url=_IMAGE_URL.format(folder=folder, file=file))
                )
                break  # una foto por color: `variantImage` si la hay, si no la de modelo
    return imagenes


def _variantes(raw: Mapping[str, Any]) -> list[ScrapedVariant]:
    """Una `ScrapedVariant` por (color × talla).

    En C&A el precio cuelga del COLOR, no de la talla: todas las tallas de un color comparten
    precio, tachado y mínimo de 30 días. Se replica en cada variante porque nuestro modelo sigue el
    precio por talla — el brief exige poder seguir una talla concreta, y una tienda que hoy no
    diferencie por talla puede hacerlo mañana.
    """
    variantes: list[ScrapedVariant] = []
    for v in raw.get("variants") or []:
        precios = v.get("price") or {}
        precio = _precio(precios.get("grossPrice"))
        if precio is None:
            continue  # sin precio no hay nada que vigilar

        # Solo es precio tachado si es estrictamente MAYOR. Aquí las 630 medidas el 02/08/2026 lo
        # eran —es el reverso exacto de Cacles, donde 248 de 428 venían iguales al precio—, pero la
        # guarda cuesta una línea y cubre el día en que dejen de serlo.
        tachado = _precio(precios.get("strikePrice"))
        if tachado is not None and tachado <= precio:
            tachado = None

        color = ((v.get("color") or {}).get("label") or "").strip() or None
        url = _PRODUCT_URL.format(uri=v["uri"]) if v.get("uri") else None
        min_30d = _precio(precios.get("lowestPrice30Days"))

        for talla in v.get("sizes") or []:
            sku = talla.get("skuId")
            if not sku:
                continue  # sin identificador estable no se puede seguir ni descatalogar
            variantes.append(
                ScrapedVariant(
                    retailer_variant_id=str(sku),  # `2258533.1.092` = producto.color.talla
                    size=str(talla.get("label")) if talla.get("label") else None,
                    color=color,
                    sku=str(sku),
                    price=precio,
                    list_price=tachado,
                    # `isAvailable` y no `quantity > 0`: la tienda ya decide con `available`
                    # (AVAILABLE / LIMITED / …) y replicar ese criterio a mano lo desincronizaría.
                    in_stock=bool(talla.get("isAvailable")),
                    url=url,
                    retailer_min_30d=min_30d,
                )
            )
    return variantes


def product_signature(product: ScrapedProduct) -> str:
    """Huella del producto: precio y stock por variante, ordenados y estables.

    Entra el stock además del precio porque aquí el listado ya es el detalle: no hay una segunda
    petición que fuese a recogerlo, así que sin el stock en la huella un cambio de disponibilidad no
    se llegaría a ingerir nunca.

    **No entra `retailer_min_30d`**, y es deliberado: la tienda lo recalcula a diario, así que
    meterlo forzaría a reingerir el catálogo entero todos los días por un dato que no cambia lo que
    el usuario ve. Se guarda cuando la observación se guarda por otro motivo.
    """
    partes = [f"{v.retailer_variant_id}:{v.price}:{int(v.in_stock)}" for v in product.variants]
    return "|".join(sorted(partes))


def parse_products(payload: Any, cat: CategoryConfig) -> list[ScrapedProduct]:
    """Convierte una página del listado en productos. Puro: sin red.

    La hoja fija género, sección y categoría (a diferencia de Cacles, donde los decide el producto):
    C&A los publica en el `ipimId` de la propia hoja. **No se usa `product.categoryIpimId`**, que es
    la categoría "de casa" del producto y no coincide con la hoja por la que se ha llegado a él —un
    producto de `3-7-1` puede declarar `3-2-1-11`—, así que tomarlo por la categoría mezclaría el
    vocabulario.
    """
    lista = _extraer_lista(payload)
    productos: list[ScrapedProduct] = []
    for raw in lista.get("products") or []:
        usim = raw.get("usim")
        if usim is None:
            continue
        variantes = _variantes(raw)
        if not variantes:
            continue  # sin variantes con precio no hay producto que seguir

        usim = str(usim)
        imagenes = _imagenes(raw.get("variants") or [])
        productos.append(
            ScrapedProduct(
                retailer_product_id=usim,
                name=str(raw.get("name") or ""),
                gender=cat.gender,
                section=cat.section,
                category=cat.category,
                url=_PRODUCT_URL.format(uri=raw["uri"]) if raw.get("uri") else None,
                variants=variantes,
                # Toda la aportación de C&A es ropa, así que `barefoot` no aplica y se queda en
                # NULL. No se llama a `classify()` a propósito: devolvería None igualmente, y la
                # llamada sugeriría que aquí hay una pregunta que responder.
                barefoot=None,
                image_url=imagenes[0].url if imagenes else None,
                images=imagenes,
            )
        )
    return productos


def parse_category_tree(payload: Any, root: str) -> list[CategoryNode]:
    """Hijos directos de `root` según `navigation`. Puro: sin red.

    C&A publica el árbol con `fetchNavigation: true`, así que las hojas **se le preguntan a la
    tienda** en vez de mantenerse a mano. Importa aquí igual que en Sfera (#56, #72): un `ipimId`
    inventado no da error, devuelve `productCount: 0`, que es indistinguible de una hoja real que se
    ha quedado sin stock.

    `count` va a `None` porque `navigation` no lo publica por hijo (los declara todos a `null`);
    saberlo cuesta una petición por nodo y la hace `category_tree()`.
    """
    lista = _extraer_lista(payload)
    nav = lista.get("navigation") or {}
    nodos: list[CategoryNode] = []
    for hijo in nav.get("children") or []:
        ipim = hijo.get("ipimId")
        if not ipim or ipim == root:
            continue
        nodos.append(
            CategoryNode(
                path=str(ipim),
                title=str(hijo.get("name") or ""),
                count=None,
                depth=1,
                # `navigation` no dice si un hijo tiene descendencia; se asume que sí y lo resuelve
                # la bajada, que ante una hoja sin hijos devuelve lista vacía sin coste extra.
                has_children=True,
            )
        )
    return nodos


class CAndAStore:
    """Scraper de C&A. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "C&A"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._cache: dict[str, ScrapedProduct] = {}
        self._hash = _HASH_PINNEADO

    # --- red ---------------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "content-type": "application/json",
                # Sin `origin` responde 403 "Not allowed".
                "origin": BASE_URL,
                # SIN ESTAS DOS, LA API SIRVE EL CATÁLOGO ALEMÁN. Ver la cabecera del módulo (1).
                # Hacen falta juntas: cada una por su lado devuelve HTML en vez de JSON.
                "x-country": "ES",
                "x-language": "es",
                "User-Agent": self._config.user_agent,
            },
            timeout=self._config.request_timeout,
            follow_redirects=True,
        )

    def _polite_pause(self) -> None:
        """Pausa base entre peticiones con jitter (una cadencia fija es más detectable)."""
        base = self._config.request_delay
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _cuerpo(self, ipim: str, page: int, nav: bool) -> dict[str, Any]:
        return {
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self._hash}},
            "variables": {
                "ipimId": ipim,
                "filter": [],
                "fetchNavigation": nav,
                "refetchOnZeroResults": False,
                "page": page,
            },
        }

    def _post(self, client: httpx.Client, ipim: str, page: int, nav: bool = False) -> Any:
        """POST con reintentos, backoff y **resolución del hash si ha caducado**.

        El hash caducado se detecta AQUÍ y no al parsear, que es la diferencia entre que la pasada
        se auto-repare y que se caiga: `PersistedQueryNotFound` llega con 200 y cuerpo válido, así
        que si la comprobación viviera en `parse_*` sucedería fuera de este bucle y el reintento no
        llegaría a existir. Se reintenta **una** vez: si tras releer el bundle sigue fallando, el
        problema no es el despliegue de C&A y elevar dice más que insistir.
        """
        for intento_hash in range(2):
            payload, headers = self._post_una_vez(client, ipim, page, nav)
            if not (isinstance(payload, dict) and _es_hash_caducado(payload.get("errors"))):
                return payload
            if intento_hash:
                raise HashCaducado(f"{SLUG}: el hash releído tampoco sirve")
            # El `x-release-hash` viaja en ESTA respuesta, así que resolver no cuesta una petición
            # extra para averiguar la versión: solo la del bundle.
            self._resolver_hash(client, headers.get("x-release-hash"))
        raise AssertionError("inalcanzable")  # pragma: no cover

    def _post_una_vez(
        self, client: httpx.Client, ipim: str, page: int, nav: bool
    ) -> tuple[Any, httpx.Headers]:
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.post(_API_URL, json=self._cuerpo(ipim, page, nav))
                resp.raise_for_status()
                return resp.json(), resp.headers
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                self._backoff(attempt, exc.response.headers.get("Retry-After"))
            except httpx.TransportError:
                if attempt == retries:
                    raise
                self._backoff(attempt)
        raise AssertionError("inalcanzable")  # pragma: no cover

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Espera exponencial (respeta `Retry-After` si viene) con jitter."""
        wait = self._config.retry_backoff * (2**attempt)
        if retry_after:
            # La cabecera admite también una fecha HTTP: no la interpretamos, y en ese caso manda
            # el backoff exponencial en vez de reventar.
            with contextlib.suppress(ValueError):
                wait = max(wait, float(retry_after))
        time.sleep(wait * random.uniform(0.8, 1.2))

    def _resolver_hash(self, client: httpx.Client, release: str | None) -> None:
        """Relee el hash de la persisted query del bundle desplegado.

        Solo se llama cuando la API ha dicho `PersistedQueryNotFound`, es decir el día que C&A
        despliega. Cuesta **una** petición (el bundle, ~1,6 MB) y cero el resto de los días:
        resolverlo siempre habría sido 2,3 MB por pasada para no enterarse de nada nuevo.

        El `release` es el `x-release-hash` de la respuesta que acaba de fallar, y el bundle se
        llama exactamente igual (`/static/main.<release>.js`) — comprobado el 02/08/2026.
        """
        if not release:
            raise ValueError(f"{SLUG}: el hash caducó y la respuesta no trae x-release-hash")
        bundle = client.get(_BUNDLE_URL.format(release=release)).text
        pos = bundle.find(_ANCLA_QUERY)
        if pos < 0:
            raise ValueError(f"{SLUG}: no se encontró la query del listado en main.{release}.js")
        m = _RE_SHA256.search(bundle, pos)
        if not m:
            raise ValueError(f"{SLUG}: no hay sha256 tras la query en main.{release}.js")
        logger.warning(
            "%s: el hash pinneado caducó (release %s); se usa %s. "
            "Actualiza _HASH_PINNEADO para ahorrar esta petición",
            SLUG,
            release,
            m.group(1),
        )
        self._hash = m.group(1)

    # --- contrato ----------------------------------------------------------------------------

    def scopes(self) -> Iterable[ScrapeScope]:
        """Se deriva de `CATEGORIES`: aquí la hoja SÍ fija género, sección y categoría."""
        return list(
            dict.fromkeys(ScrapeScope(c.gender, c.section, c.category) for c in self._categories)
        )

    def _pagina(
        self, client: httpx.Client, cat: CategoryConfig, page: int
    ) -> tuple[list[ScrapedProduct], int, int]:
        """Devuelve `(productos parseados, productos CRUDOS de la página, productCount declarado)`.

        Los tres números hacen falta y no son el mismo: el parseo descarta lo que no tiene variantes
        con precio, así que decidir el fin de la paginación con el número parseado haría que una
        página entera de descartes pareciese «aquí se acabó el catálogo».
        """
        payload = self._post(client, cat.ipim_id, page)
        lista = _extraer_lista(payload)
        crudos = lista.get("products")
        declarado = lista.get("productCount")
        return (
            parse_products(payload, cat),
            len(crudos) if isinstance(crudos, list) else 0,
            declarado if isinstance(declarado, int) else 0,
        )

    def list_catalog(self) -> Iterable[ListingEntry]:
        self._scan = ScanReport()
        self._cache = {}
        emitted: set[str] = set()
        with self._client() as client:
            for cat in self._categories:
                yield from self._listar_hoja(client, cat, emitted)

    def _listar_hoja(
        self, client: httpx.Client, cat: CategoryConfig, emitted: set[str]
    ) -> Iterable[ListingEntry]:
        scope = ScrapeScope(cat.gender, cat.section, cat.category)
        truncada = True  # solo deja de serlo al ver el final real de la paginación
        for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
            productos, crudos, declarado = self._pagina(client, cat, page)
            if not crudos:
                # LA PARTE IMPORTANTE DE ESTE FICHERO. Una hoja retirada NO da 404: devuelve 200
                # con la lista vacía, igual que la página siguiente a la última. Aquí sí hay con
                # qué distinguirlas —`productCount` sigue intacto en la hoja viva—, pero en la
                # PRIMERA página se decide igual que en Cacles: si además el contador dice 0, la
                # hoja está muerta y hay que decirlo, porque si no la ingesta lee «este ámbito se
                # ha quedado vacío» y da de baja todo lo que colgaba de él.
                if page == _PAGINA_INICIAL and declarado == 0:
                    self._hoja_comprometida(
                        scope,
                        cat.ipim_id,
                        f"la hoja {cat.ipim_id!r} devolvió productCount 0, "
                        "así que se trata como retirada",
                    )
                    return
                truncada = False
                break
            for producto in productos:
                pid = producto.retailer_product_id
                if pid in emitted:
                    continue  # hay usim repetidos dentro de la misma hoja: gana la primera
                emitted.add(pid)
                self._cache[pid] = producto
                yield ListingEntry(
                    retailer_product_id=pid,
                    signature=product_signature(producto),
                    gender=cat.gender,
                    section=cat.section,
                    category=cat.category,
                )
            if crudos < _PAGE_SIZE:
                # Una página incompleta ES la última, y saberlo aquí ahorra la petición que solo
                # servía para que la tienda respondiera vacío. Con exactamente `_PAGE_SIZE`
                # productos sigue haciendo falta preguntar, y entonces cierra el caso de arriba.
                truncada = False
                break
        if truncada:
            # Se agotó el tope de páginas sin llegar al final: se ha visto SOLO una parte de la
            # hoja. Contarla como sana sería el peor de los dos errores — lo que no se ha llegado a
            # mirar no está retirado, y a las `delist_min_misses` pasadas se descatalogaría solo por
            # no haber cabido en el tope.
            self._hoja_comprometida(
                scope,
                cat.ipim_id,
                f"{cat.ipim_id!r} agotó el tope de {_MAX_PAGES} páginas sin llegar al final, "
                "así que el catálogo leído está incompleto",
            )
        else:
            self._scan.leaf_ok()

    def _hoja_comprometida(self, scope: ScrapeScope, leaf: str, motivo: str) -> None:
        """Cuenta la hoja como caída y saca su ámbito de las bajas de esta pasada."""
        self._scan.leaf_gone(scope, leaf)
        logger.warning("%s: %s; se omiten las bajas de ese ámbito", SLUG, motivo)

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        # El listado ya trajo el detalle completo (colores, tallas, precios y fotos vienen en el
        # mismo JSON), así que se sirve desde caché sin una sola petición extra.
        for entry in entries:
            producto = self._cache.get(entry.retailer_product_id)
            if producto is not None:
                yield producto

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    # --- capacidades opcionales --------------------------------------------------------------

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Sondea la ficha de producto. Ver `stores.base.SupportsAliveProbe`.

        La PDP da **404 honesto** cuando el producto no existe (verificado con `usim` inventado).
        Ojo con el otro caso medido: un `colorCode` que no existe devuelve **301**, no 404, así que
        solo el 404 cuenta como retirado — cualquier otra cosa se deja fuera del mapa, que es como
        se dice «no concluyente».
        """
        veredictos: dict[str, bool] = {}
        with self._client() as client:
            for cand in candidates:
                if not cand.url:
                    continue  # sin URL no hay a qué preguntar: no concluyente
                self._polite_pause()
                try:
                    resp = client.get(cand.url, headers={"Accept": "text/html"})
                except httpx.TransportError:
                    continue  # fallo nuestro, no veredicto de la tienda
                if resp.status_code == 404:
                    veredictos[cand.retailer_product_id] = False
                elif resp.is_success:
                    veredictos[cand.retailer_product_id] = True
        return veredictos

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas. Ver `stores.base.SupportsLeafHealth`.

        Una petición por hoja, la primera página. El veredicto sale de `productCount`, que es lo que
        distingue la hoja retirada (0) de la viva: aquí una hoja muerta no da 404, da 200.
        """
        with self._client() as client:
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    lista = _extraer_lista(self._post(client, cat.ipim_id, _PAGINA_INICIAL))
                except HashCaducado:
                    yield LeafHealth(
                        scope,
                        cat.ipim_id,
                        None,
                        "el hash de la persisted query caducó y no se pudo resolver: "
                        "C&A ha desplegado (ver _resolver_hash)",
                    )
                except httpx.HTTPStatusError as exc:
                    yield LeafHealth(scope, cat.ipim_id, None, f"HTTP {exc.response.status_code}")
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, cat.ipim_id, None, f"{type(exc).__name__}: {exc}")
                else:
                    declarado = lista.get("productCount")
                    total = declarado if isinstance(declarado, int) else 0
                    yield LeafHealth(
                        scope,
                        cat.ipim_id,
                        bool(total),
                        f"productCount {total}",
                    )

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Las hojas que esta tienda tiene configuradas."""
        return [cat.ipim_id for cat in self._categories]

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. Las dos ramas de ropa infantil.

        No se barre desde `3` (Niños) sino desde sus dos ramas de ropa: `3-2` «Destacados» es
        promoción transversal que solapa con las dos y ya se deja fuera de `CATEGORIES` por eso
        mismo, así que meterla aquí sería declarar como excepción algo que basta con no pedir.

        **Coste, que aquí no es despreciable**: `category_tree()` gasta una petición por nodo, y
        medido el 04/08/2026 son **122 rutas en ~80 s** entre las dos ramas. Es el barrido más caro
        del vigía y se paga una vez por semana; si algún día molesta, lo que hay que recortar es la
        profundidad, no las ramas — el hueco que esta capa busca aparece siempre al primer nivel.
        """
        return ["3-1", "3-7"]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCoverageWatch`. El `ipimId` es jerárquico y anida con `-`."""
        return "-"

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. Una petición por nodo.

        Baja recursivamente porque `navigation` solo publica **un nivel** por respuesta. Se
        aprovecha la misma petición para leer el `productCount` real del nodo, que `navigation` no
        publica por hijo (los declara todos a `null`).

        Un fallo propaga, no se traga: aquí no hay forma de decir «esta rama no la pude leer» sin
        inventarse un nodo. `run._tree()` se queda con lo ya emitido y lo dice.
        """
        with self._client() as client:
            yield from self._tree_from(client, root, 0, {root}, set())

    def _tree_from(
        self,
        client: httpx.Client,
        root: str,
        base_depth: int,
        pedidas: set[str],
        emitidas: set[str],
        payload: Any = None,
    ) -> Iterable[CategoryNode]:
        """Recorre `root` y sus descendientes: **exactamente una petición por nodo**.

        `payload` es la respuesta de `root` cuando quien llama ya la tiene. No es una optimización
        cosmética: sin ella cada nodo se pedía dos veces —una para leer su conteo y sus hijos, y
        otra al recursar sobre él—, lo que en el árbol infantil (150 categorías) son 300 peticiones
        contra la tienda en vez de 150.
        """
        if base_depth >= _MAX_TREE_DEPTH:
            return
        if payload is None:
            payload = self._post(client, root, _PAGINA_INICIAL, nav=True)
        for nodo in parse_category_tree(payload, root):
            if nodo.path in emitidas:
                continue
            emitidas.add(nodo.path)
            propio = self._post(client, nodo.path, _PAGINA_INICIAL, nav=True)
            cuenta = _extraer_lista(propio).get("productCount")
            hijos = parse_category_tree(propio, nodo.path)
            yield CategoryNode(
                path=nodo.path,
                title=nodo.title,
                count=cuenta if isinstance(cuenta, int) else None,
                depth=nodo.depth + base_depth,
                has_children=bool(hijos),
            )
            if hijos and nodo.path not in pedidas:
                pedidas.add(nodo.path)
                yield from self._tree_from(
                    client, nodo.path, base_depth + nodo.depth, pedidas, emitidas, propio
                )
