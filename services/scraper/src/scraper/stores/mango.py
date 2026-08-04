"""Scraper de Mango (shop.mango.com): la tienda que publica su propio árbol de categorías.

Tres endpoints públicos, todos en hosts distintos del escaparate y ninguno autenticado:

  - menú:     GET https://api.shop.mango.com/ecs/menu-service/v5/menu
  - listado:  GET https://api.shop.mango.com/cs/product-lists-drive-thru/v3/.../catalogs/{id}/filters
  - ficha:    GET https://shop.mango.com/es/es/p/_{productId}   (308 -> URL canónica)

Akamai está presente (pone `_abck` y `bm_sz`) pero **solo filtra por User-Agent**: `httpx` con UA de
Chrome entra a la primera y sin UA da 403. No hace falta `BrowserSession` — la nota de la épica #4
sobre Mango «tras Akamai» era literalmente cierta y engañosa en la práctica.

── LO IMPORTANTE DE ESTE FICHERO ──────────────────────────────────────────────────────────────────

**1. La hoja muerta da 404 honesto, y es la única de las siete tiendas que lo hace en los tres
sitios.** Ruta web, API de listado (`catalogs/inventada/filters` -> 404) y ficha
(`/p/_99999999` -> 404). Después de Sfera (catálogo del padre, #54), Cacles (lista vacía
indistinguible del fin de paginación), Hipercor (espejismo) y H&M (el cubo entero con 200), aquí
`check_leaves()` y `probe_alive()` se pueden fiar del status y no necesitan canario ni comparación
con el padre. Verificado el 03/08/2026 con rutas inventadas en los tres.

**Pero el 404 honesto no es el 404 fiable, y hay una medida que lo dice.** En la primera pasada de
Mango en `dev` (#80) `rebajas_newborn.sudaderas_newborn` dio 404 y se marcó como hoja caída. No lo
estaba: la hoja seguía publicada en el menú, el listado le respondía 200 con 2 items, y 20 sondeos
seguidos dieron 20 × 200. La segunda pasada, 3 minutos después, la vio viva. O sea que aquí un 404
puede ser **transitorio**, y eso significa que un `check_leaves()` que se fía del status confunde
«no está» con «no me ha contestado bien esta vez».

Lo que salva el caso es que la consecuencia es conservadora y ya está construida: el ámbito se marca
comprometido, sus bajas se omiten y la pasada cierra sin descatalogar nada de más (`bajas: 0 / 0`).
El coste de un 404 transitorio es *dejar de detectar bajas* en ese ámbito durante esa pasada, que es
el lado correcto por el que equivocarse. **No añadas un reintento sin medir cuánto se repite**: con
dos observaciones no se sabe si esto es ruido de Akamai o una hoja concreta que renquea, y la
lección de #67 es que lo que separa un blip de un bug es la repetición, no el veredicto. La serie la
dan el vigía semanal y `vigia_run` (#111).

**2. El endpoint del listado se llama `/filters` pero ES el listado, y no pagina.** Devuelve
`{gridSize, filters, items}` con la hoja entera en una sola respuesta: medido `rebajas_she` con
**1938 items** de una tacada, muy por encima de cualquier tamaño de página redondo. Por eso aquí no
hay `_MAX_PAGES` ni la lógica de «hoja truncada» que tienen H&M y C&A: no hay paginación que agotar.
El parámetro que costó el recon de #80 es **`languageIso`** — con `language` o `lang` responde 400
«Language not present at request params», que es lo que dejó la ficha de #70 a medias.

**3. Una fila del listado es un producto+color, no un producto.** `id` = `27035145:02` es
`{productId}:{colorId}`, y la misma raíz aparece en varias filas (medido: 3490 filas -> 1502
productos). Así que NO se deduplica con «gana la primera» como en Cacles o C&A: se **agrupa** por
`productId`, y por eso `list_catalog()` acumula la pasada antes de emitir — los colores de un modelo
caen en hojas distintas. Es el mismo patrón que H&M.

**4. El 4,6 % del catálogo sale publicado a la vez en niña y en niño** (69 productos de 1502,
medido el 03/08/2026), y eso lo decide el género: se resuelve con `ambito_cruzado()`, que lo emite
como `unisex`. Sin eso sería el fallo que #98 destapó en Hipercor. Queda entre Lefties (2,0 %) y
H&M (9,3 %).

**5. Las rebajas NO son un subconjunto de la colección permanente, así que hay que recorrer las
dos.** Medido en niña: de 562 productos en `rebajas_nina`, **62 no están en `prendas_nina`**. Si se
ingiriera solo la colección permanente se perderían justo los productos rebajados, que es lo que
este proyecto existe para detectar. De ahí que `CATEGORIES` lleve cada categoría dos veces, una por
colección; el agrupado por `productId` deduplica lo que sale en las dos y los precios coinciden.

**6. `ninos` tiene CINCO ramas de género, no dos.** `nina`, `nino`, `bebe-nina`, `bebe-nino` y
`newborn`. Quedarse con las dos primeras dejaría fuera todo el bebé, que es el mismo agujero que en
H&M tenía `/baby` y en Sfera la #72. `newborn` va como `unisex` porque su rama no separa niño de
niña — el mismo criterio que `recien nacido` en H&M y la hoja de bebé de Hipercor.

**7. El mundo `teen` queda fuera a propósito.** Es un hermano de `ninos` en el menú, no un hijo, y
el brief habla de ropa infantil. Si algún día entra, son otras dos ramas (`teena`, `teeno`) con la
misma forma: se añaden a `CATEGORIES` y ya.

**8. Las tallas NO hay que remodelarlas.** Al contrario que H&M, cuyo `_talla()` existe para darle
la vuelta a `122/128 (6-8Y)`, aquí `size_canon` (`0017`, `0020`) digiere el vocabulario de Mango tal
cual, comprobado contra sus 58 formas reales: los rangos (`5-6 años`, `12-18 meses`) pasan intactos,
el número suelto de ropa sale como edad (`10` -> `10 años`, y son 3100 apariciones) y el de
zapatería se queda como número (21-39). La única canónica que aparece en las dos secciones es
`1-6 meses`, un patuco, y en las dos significa lo mismo. **La señal barata para re-medirlo** es que
la zapatería de Mango empieza en el 21: el día que sirva un número por debajo de 15 caería en el
espacio de las edades y habría que volver sobre esto (es la ambigüedad de #103).

**9. Las fotos no cuestan una sola petición.** La URL se construye con lo que ya trae el listado:
`media.mango.com/is/image/punto/{productId}-{colorId}-{portraitId}`. La ficha trae la galería
completa por color, que es la que se usa cuando se pide el detalle.

**10. La zapatería de Mango es calzado convencional, y por eso no la ve nadie en el catálogo.** No
es un fallo del clasificador (#150). Medido el 04/08/2026 sobre las **10 hojas de `zapateria`**, 137
productos con descripción los 137: **0 con señal fuerte** (ninguna ficha nombra «barefoot»,
«respetuoso», «descalzo», «minimalista» ni el drop), 3 con señal negativa —dos «Plataforma» y un
«Punta fina»—, y **134 `desconocido`**. El menú tampoco publica ninguna hoja de calzado respetuoso:
`--tree ninos` da 272 categorías y cero coincidencias. O sea que `desconocido` es **correcto**, y la
consecuencia —el catálogo filtra `barefoot=si` por defecto, así que Mango no aporta calzado a la
zapatería— es la deseada: la página enseña solo calzado respetuoso.

**Cuidado con el atajo de ampliar el vocabulario, que aquí es una trampa medida.** Las viñetas de
Mango describen estética, no construcción: **«Punta redonda» sale en 92 de los 137** y es puro
adjetivo de estilo (convive con «Plataforma» y con tacón), así que meterla en `_DEBILES` le pondría
media señal a dos tercios de la zapatería. Lo mismo con la única «horma» del catálogo, que es una
bailarina con lazo y velcro («Nuevo ajuste de horma con un diseño más ancho»). La regla de las **dos
señales débiles** ya está haciendo su trabajo: hay 5 productos con «Puntera redondeada» —que sí está
en `_DEBILES`— y los 5 se quedan en `desconocido` porque no traen una segunda, que es exactamente el
sesgo que `barefoot.py` documenta. `mango_ficha_zapato.html` fija uno de esos 5.

**La señal barata para re-medirlo**, el día que Mango se meta en calzado respetuoso, es la misma
consulta de la que salió #150 (caso D8 de `/validar-qa`):

    SELECT p.barefoot, count(*) FROM product p JOIN retailer r ON r.id = p.retailer_id
    WHERE r.slug = 'mango' AND p.section = 'zapateria' AND p.delisted_at IS NULL
    GROUP BY 1;

Cumplimiento (comprobado el 03/08/2026): el `robots.txt` de `shop.mango.com` **sí se puede leer**
(al contrario que el de H&M, Hipercor y Sfera) y declara `Crawl-delay: 0.2`. Veta `/*/c/f/*` —la
rejilla FILTRADA, que no usamos—, `/*.html` —y las fichas de Mango no acaban en `.html`—,
`/*/search*` y un montón de facetas por query. Las rutas `/c/…` y `/p/…` que sí usamos **no están
vetadas**, y declara un sitemap que el propio Akamai devuelve 403. Los dos hosts de API no sirven
`robots.txt` (404), y las reglas son por host, así que ahí no hay nada que obedecer.

Las funciones `parse_*`, `producto()` y `firma_listado()` son puras y se testean con fixtures.
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
import re
import time
from collections.abc import Iterable, Mapping, Sequence
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
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    ambito_cruzado,
    con_unisex,
)

logger = logging.getLogger(__name__)

SLUG = "mango"  # va en `--retailer`, en `retailer.slug` y en el nombre del CronJob
BASE_URL = "https://shop.mango.com"
_API_HOST = "https://api.shop.mango.com"
_LIST_URL = (
    f"{_API_HOST}/cs/product-lists-drive-thru/v3"
    "/channels/shop/countries/es/catalogs/{catalog}/filters"
)
_MENU_URL = f"{_API_HOST}/ecs/menu-service/v5/menu"
# La ficha por id: 308 a la URL canónica, que es de donde sale `ScrapedProduct.url`. El guion bajo
# es parte de la ruta, no un separador nuestro.
_FICHA_URL = f"{BASE_URL}/es/es/p/_{{producto}}"
_MEDIA_URL = "https://media.mango.com/is/image/punto/{producto}-{color}-{foto}"

# Sin UA de Chrome, Akamai responde 403 a todo. El de la config es el que se manda de verdad; este
# solo es el respaldo cuando no hay ninguno configurado.
_UA_RESPALDO = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Fichas SEGUIDAS que pueden fallar antes de abortar la pasada. Mismo criterio que Hipercor, y
# aquí importa el doble: `parse_ficha()` depende de la plantilla RSC de Next.js, así que un cambio
# de la tienda las rompe TODAS a la vez. Sin este tope, `fetch_details()` no emitiría esos
# productos, `ingest.py` no les tocaría `last_seen_at` y a las `SCRAPER_DELIST_MIN_MISSES` pasadas
# los descatalogaría — con el listado diciendo que siguen ahí. Ver `fetch_details()`.
_MAX_FICHAS_FALLIDAS = 5

# El mundo del menú que recorremos. `teen` es su hermano y queda fuera (punto 7 de la cabecera).
_MUNDO = "kids"
_RUTA_MUNDO = "/c/ninos/"


@dataclass(frozen=True)
class CategoryConfig:
    """Una hoja del catálogo que recorremos.

    `catalog_id` es el identificador que la propia tienda le da en su menú (`catalogId`), no una
    ruta inventada por nosotros: sale de `category_tree()`, que lee el mismo endpoint que usa la
    web. La convención es `{coleccion}_{rama}.{categoria}_{rama}` y **no es uniforme en el
    mayúsculas/minúsculas de la rama** — `prendas_babyNina` convive con `rebajas_babynina`, y ese
    detalle no se puede deducir, hay que leerlo.
    """

    catalog_id: str
    gender: str  # niño | niña | unisex
    section: str  # ropa | zapateria
    category: str  # nuestro vocabulario, no el suyo


# Las 111 hojas, **leídas del menú que publica la tienda** (03/08/2026) y verificadas una a una
# contra la API de listado: 111 vivas, 0 fallos. No están adivinadas — `category_tree()` las vuelve
# a sacar cuando haga falta, y `mapped_leaves()` permite cruzar lo publicado con lo que ingerimos.
#
# Cada categoría aparece DOS veces, una por colección (`prendas_*` y `rebajas_*`): ver el punto 5 de
# la cabecera. La zapatería cuelga de `accesorios_*`, que es donde Mango la pone.
#
# Fuera a propósito, por no ser del brief: baño, abrigos y chaquetas, accesorios, bolsos, bisutería,
# bufandas, colonias y complementos de pelo. Y fuera también las colecciones transversales
# (`dest_*`, `basicos_*`, `best_sellers_*`, `nuevo_*`, `licencias_*`, `denim`, `lino`, `pre-teen`,
# `total-look`, `nuevos-artículos-añadidos`), que solapan con las hojas de género y duplicarían el
# trabajo para los mismos productos — el mismo criterio que en H&M.
#
# De esas transversales conviene nombrar una, porque su etiqueta invita a mapearla y en #175 se
# comprobó que no: `dest_chandal_*` («kids.colecciones.ropadeportiva»). Es `dest_`, o sea la familia
# de destacados que rota con la campaña, y la tienda solo publica **tres** ramas —`nino`,
# `babynina`, `babynino`, sin `nina`—, que es la asimetría típica de una promoción y no de una
# taxonomía. En Sfera la hoja del mismo nombre resultó ser sudaderas y en C&A una vista transversal;
# aquí es promoción.
CATEGORIES: list[CategoryConfig] = [
    # --- nina (niña) ---
    CategoryConfig("prendas_nina.camisas_nina", "niña", "ropa", "camisetas"),
    CategoryConfig("prendas_nina.camisetas_nina", "niña", "ropa", "camisetas"),
    CategoryConfig("rebajas_nina.camisas_nina", "niña", "ropa", "camisetas"),
    CategoryConfig("rebajas_nina.camisetas_nina", "niña", "ropa", "camisetas"),
    CategoryConfig("prendas_nina.leggings_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_nina.pantalones_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_nina.shorts_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_nina.vaqueros_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_nina.leggings_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_nina.pantalones_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_nina.shorts_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_nina.vaqueros_nina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_nina.interior_nina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("prendas_nina.pijamas_nina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_nina.interior_nina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_nina.pijamas_nina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("prendas_nina.jerseys_nina", "niña", "ropa", "sudaderas"),
    CategoryConfig("prendas_nina.punto_nina", "niña", "ropa", "sudaderas"),
    CategoryConfig("prendas_nina.sudaderas_nina", "niña", "ropa", "sudaderas"),
    CategoryConfig("rebajas_nina.jerseys_nina", "niña", "ropa", "sudaderas"),
    CategoryConfig("rebajas_nina.punto_nina", "niña", "ropa", "sudaderas"),
    CategoryConfig("rebajas_nina.sudaderas_nina", "niña", "ropa", "sudaderas"),
    # La falda va a `vestidos` por el mismo criterio que en C&A, H&M e Hipercor: el brief no tiene
    # slug propio y estrenar uno crearía una categoría que ninguna otra tienda alimenta.
    CategoryConfig("prendas_nina.faldasyshorts_nina", "niña", "ropa", "vestidos"),
    CategoryConfig("prendas_nina.vestidos_nina", "niña", "ropa", "vestidos"),
    CategoryConfig("rebajas_nina.faldasyshorts_nina", "niña", "ropa", "vestidos"),
    CategoryConfig("rebajas_nina.vestidos_nina", "niña", "ropa", "vestidos"),
    CategoryConfig("accesorios_nina.zapatos_nina", "niña", "zapateria", "zapatos"),
    CategoryConfig("rebajas_nina.zapatos_nina", "niña", "zapateria", "zapatos"),
    # --- nino (niño) ---
    CategoryConfig("prendas_nino.camisas_nino", "niño", "ropa", "camisetas"),
    CategoryConfig("prendas_nino.camisetas_nino", "niño", "ropa", "camisetas"),
    CategoryConfig("rebajas_nino.camisas_nino", "niño", "ropa", "camisetas"),
    CategoryConfig("rebajas_nino.camisetas_nino", "niño", "ropa", "camisetas"),
    CategoryConfig("prendas_nino.bermudas_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_nino.jogging_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_nino.pantalones_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_nino.tejanos_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_nino.bermudas_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_nino.jogging_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_nino.pantalones_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_nino.tejanos_nino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_nino.interior_nino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("prendas_nino.pijamas_nino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_nino.pijamas_nino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("prendas_nino.jerseys_nino", "niño", "ropa", "sudaderas"),
    CategoryConfig("prendas_nino.sudaderas_nino", "niño", "ropa", "sudaderas"),
    CategoryConfig("rebajas_nino.jerseys_nino", "niño", "ropa", "sudaderas"),
    CategoryConfig("rebajas_nino.sudaderas_nino", "niño", "ropa", "sudaderas"),
    CategoryConfig("accesorios_nino.calzado_nino", "niño", "zapateria", "zapatos"),
    CategoryConfig("rebajas_nino.calzado_nino", "niño", "zapateria", "zapatos"),
    # --- bebe-nina (niña) ---
    CategoryConfig("prendas_babyNina.camisas_babyNina", "niña", "ropa", "camisetas"),
    CategoryConfig("prendas_babyNina.camisetas_babyNina", "niña", "ropa", "camisetas"),
    CategoryConfig("rebajas_babynina.camisas_babyNina", "niña", "ropa", "camisetas"),
    CategoryConfig("rebajas_babynina.camisetas_babyNina", "niña", "ropa", "camisetas"),
    CategoryConfig("prendas_babyNina.pantalones_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNina.shorts_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNina.vaqueros_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynina.pantalones_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynina.shorts_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynina.vaqueros_babyNina", "niña", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNina.interior_babyNina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("prendas_babyNina.pijamas_babyNina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_babynina.interior_babyNina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_babynina.pijamas_babyNina", "niña", "ropa", "ropa-interior"),
    CategoryConfig("prendas_babyNina.cardigansyjerseis_babyNina", "niña", "ropa", "sudaderas"),
    CategoryConfig("prendas_babyNina.sudaderas_babyNina", "niña", "ropa", "sudaderas"),
    CategoryConfig("rebajas_babynina.cardigansyjerseis_babyNina", "niña", "ropa", "sudaderas"),
    CategoryConfig("rebajas_babynina.sudaderas_babyNina", "niña", "ropa", "sudaderas"),
    CategoryConfig("prendas_babyNina.vestidos_babyNina", "niña", "ropa", "vestidos"),
    CategoryConfig("rebajas_babynina.vestidos_babyNina", "niña", "ropa", "vestidos"),
    CategoryConfig("accesorios_babyNina.calzado_babyNina", "niña", "zapateria", "zapatos"),
    CategoryConfig("rebajas_babynina.calzado_babyNina", "niña", "zapateria", "zapatos"),
    # --- bebe-nino (niño) ---
    CategoryConfig("prendas_babyNino.camisas_babyNino", "niño", "ropa", "camisetas"),
    CategoryConfig("prendas_babyNino.camisetas_babyNino", "niño", "ropa", "camisetas"),
    CategoryConfig("rebajas_babynino.camisas_babyNino", "niño", "ropa", "camisetas"),
    CategoryConfig("rebajas_babynino.camisetas_babyNino", "niño", "ropa", "camisetas"),
    CategoryConfig("prendas_babyNino.bermudas_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNino.pantalones_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNino.vaqueros_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynino.bermudas_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynino.pantalones_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("rebajas_babynino.vaqueros_babyNino", "niño", "ropa", "pantalones"),
    CategoryConfig("prendas_babyNino.interior_babyNino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("prendas_babyNino.interiorypijamas_babyNino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_babynino.interior_babyNino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_babynino.interiorypijamas_babyNino", "niño", "ropa", "ropa-interior"),
    CategoryConfig("prendas_babyNino.cardigansyjerseis_babyNino", "niño", "ropa", "sudaderas"),
    CategoryConfig("prendas_babyNino.sudaderas_babyNino", "niño", "ropa", "sudaderas"),
    CategoryConfig("rebajas_babynino.cardigansyjerseis_babyNino", "niño", "ropa", "sudaderas"),
    CategoryConfig("rebajas_babynino.sudaderas_babyNino", "niño", "ropa", "sudaderas"),
    CategoryConfig("accesorios_babyNino.calzado_babyNino", "niño", "zapateria", "zapatos"),
    CategoryConfig("rebajas_babynino.calzado_babyNino", "niño", "zapateria", "zapatos"),
    # --- newborn (unisex: su rama no separa niño de niña) ---
    CategoryConfig("prendas_newborn.camisas_newborn", "unisex", "ropa", "camisetas"),
    CategoryConfig("prendas_newborn.camisetas_newborn", "unisex", "ropa", "camisetas"),
    CategoryConfig("rebajas_newborn.camisas_newborn", "unisex", "ropa", "camisetas"),
    CategoryConfig("rebajas_newborn.camisetas_newborn", "unisex", "ropa", "camisetas"),
    CategoryConfig("prendas_newborn.bermudas_newborn", "unisex", "ropa", "pantalones"),
    CategoryConfig("prendas_newborn.pantalones_newborn", "unisex", "ropa", "pantalones"),
    CategoryConfig("rebajas_newborn.bermudas_newborn", "unisex", "ropa", "pantalones"),
    CategoryConfig("rebajas_newborn.pantalones_newborn", "unisex", "ropa", "pantalones"),
    CategoryConfig("prendas_newborn.calcetines_newborn", "unisex", "ropa", "ropa-interior"),
    CategoryConfig("prendas_newborn.pijamas_newborn", "unisex", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_newborn.calcetines_newborn", "unisex", "ropa", "ropa-interior"),
    CategoryConfig("rebajas_newborn.pijamas_newborn", "unisex", "ropa", "ropa-interior"),
    CategoryConfig("prendas_newborn.cardigansyjerseis_newborn", "unisex", "ropa", "sudaderas"),
    CategoryConfig("prendas_newborn.sudaderas_newborn", "unisex", "ropa", "sudaderas"),
    CategoryConfig("rebajas_newborn.cardigansyjerseis_newborn", "unisex", "ropa", "sudaderas"),
    CategoryConfig("rebajas_newborn.sudaderas_newborn", "unisex", "ropa", "sudaderas"),
    # El pelele es la prenda base de recién nacido y hace de vestido, como el body en H&M.
    CategoryConfig("prendas_newborn.peleles_newborn", "unisex", "ropa", "vestidos"),
    CategoryConfig("rebajas_newborn.peleles_newborn", "unisex", "ropa", "vestidos"),
    CategoryConfig("accesorios_newborn.calzado_newborn", "unisex", "zapateria", "zapatos"),
    CategoryConfig("rebajas_newborn.calzado_newborn", "unisex", "zapateria", "zapatos"),
]


class FichaIlegible(Exception):
    """La ficha respondió 200 pero no se pudo extraer su payload de producto."""


class DetailUnavailable(RuntimeError):
    """La tienda ha dejado de servir fichas legibles. No es que los productos se hayan retirado."""


def _decimal(value: Any) -> Decimal | None:
    """Precio a `Decimal` sin pasar por binario.

    Mango sirve los precios como **float JSON** (`7.99`), así que se convierte desde su
    representación en texto: `Decimal(7.99)` daría 7.9900000000000002131628…
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = repr(value) if isinstance(value, float) else str(value)
    if not isinstance(value, str):
        return None
    with contextlib.suppress(InvalidOperation):
        return Decimal(value.strip().replace(",", "."))
    return None


@dataclass(frozen=True)
class Fila:
    """Una fila del listado: un producto+color, no un producto (punto 3 de la cabecera)."""

    product_id: str
    color_id: str
    sizes: tuple[str, ...]
    price: Decimal | None
    portrait_id: str | None


def parse_filas(payload: Any) -> list[Fila]:
    """Las filas de una respuesta de listado. Pura: no toca la red.

    Se ignoran las filas sin `productId` o sin `colorId` en vez de reventar: una fila rara no
    justifica perder la hoja entera, y sin esos dos campos no hay identidad que persistir.
    """
    if not isinstance(payload, Mapping):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    filas: list[Fila] = []
    for it in items:
        if not isinstance(it, Mapping):
            continue
        pid, cid = it.get("productId"), it.get("colorId")
        if not isinstance(pid, str) or not isinstance(cid, str) or not pid or not cid:
            continue
        tallas = it.get("sizes")
        filas.append(
            Fila(
                product_id=pid,
                color_id=cid,
                # El listado sirve las tallas en orden alfabético (`11-12 años` antes que
                # `7-8 años`), no por talla. Se ordenan aquí para que la huella sea estable, y NO
                # se emparejan por posición con nada: el detalle trae su propio `id` por talla.
                sizes=tuple(sorted(t for t in tallas if isinstance(t, str)))
                if isinstance(tallas, list)
                else (),
                price=_decimal(it.get("price")),
                portrait_id=it.get("portraitId") if isinstance(it.get("portraitId"), str) else None,
            )
        )
    return filas


def ids_de_hoja(payload: Any) -> list[str]:
    """Los `productId` distintos de una hoja, en orden de aparición."""
    return list(dict.fromkeys(f.product_id for f in parse_filas(payload)))


def es_listado(payload: Any) -> bool:
    """Si la respuesta tiene la FORMA de un listado, aunque venga sin productos.

    Distingue «esta hoja está vacía» de «esto no es un listado», que aquí no es lo mismo y confundir
    los dos sale caro en las dos direcciones:

    - Una hoja **legítimamente vacía** existe y es frecuente: `CATEGORIES` lleva 55 hojas
      `rebajas_*`, y una categoría sin nada rebajado devuelve 0 productos con toda normalidad. Si
      eso se contara como hoja caída, al acabar una campaña se caerían muchas a la vez y
      `SCRAPER_SCAN_MAX_DEAD_RATIO` (0,34) abortaría la pasada de una tienda perfectamente sana —
      y `check_leaves()` abriría una issue del vigía cada semana por lo mismo.
    - Que la hoja **ya no exista** no hace falta deducirlo del vacío: esta tienda da 404 honesto,
      que es justo lo que la hace fácil de vigilar (punto 1 de la cabecera).

    Así que lo que se comprueba es la forma: un objeto con `items` en lista. Lo que no la tenga es
    un cambio de la API, y ahí el ámbito sí deja de ser seguro para dar bajas.
    """
    return isinstance(payload, Mapping) and isinstance(payload.get("items"), list)


def firma_listado(filas: Sequence[Fila]) -> str:
    """Huella barata de un producto con lo que ya se ve en el listado.

    Lleva precio y tallas **por color**, que es lo que cambia cuando cambia algo que nos importa:
    una rebaja, una talla que se agota (desaparece del listado) o un color nuevo. No se llama a la
    ficha para construirla — que era justo la duda que #80 dejaba abierta.
    """
    partes = [
        f"{f.color_id}:{f.price if f.price is not None else '-'}:{'|'.join(f.sizes)}"
        for f in sorted(filas, key=lambda f: f.color_id)
    ]
    return ";".join(partes)


# --- ficha ------------------------------------------------------------------------------------

_RSC = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _objeto_json(texto: str, inicio: int) -> str | None:
    """El objeto JSON que empieza en `texto[inicio]`, contando llaves fuera de cadena."""
    if inicio >= len(texto) or texto[inicio] != "{":
        return None
    profundidad = 0
    en_cadena = False
    escapado = False
    for i in range(inicio, len(texto)):
        c = texto[i]
        if escapado:
            escapado = False
            continue
        if c == "\\":
            escapado = True
            continue
        if c == '"':
            en_cadena = not en_cadena
            continue
        if en_cadena:
            continue
        if c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio : i + 1]
    return None


def parse_ficha(html: str) -> Mapping[str, Any]:
    """El objeto `product` que la ficha trae embebido en su payload RSC. Pura.

    La ficha es Next.js App Router: el estado viaja en `self.__next_f.push([1,"…"])` como una
    cadena JSON escapada. Se desescapa y se recorta el objeto `product` contando llaves, en vez de
    intentar un regex sobre 200 KB de payload anidado.

    Lanza `FichaIlegible` si no está: un 200 sin producto es un cambio de la tienda, no un producto
    retirado, y confundir las dos cosas es lo que provoca bajas falsas.
    """
    trozos = _RSC.findall(html)
    if not trozos:
        raise FichaIlegible("la ficha no trae payload __next_f")
    try:
        texto = json.loads('"' + "".join(trozos) + '"')
    except ValueError as exc:
        raise FichaIlegible(f"payload __next_f no desescapable: {exc}") from exc
    marca = '"product":'
    i = texto.find(marca)
    if i < 0:
        raise FichaIlegible("el payload no contiene un objeto `product`")
    crudo = _objeto_json(texto, i + len(marca))
    if crudo is None:
        raise FichaIlegible("el objeto `product` está truncado")
    try:
        obj = json.loads(crudo)
    except ValueError as exc:
        raise FichaIlegible(f"objeto `product` ilegible: {exc}") from exc
    if not isinstance(obj, Mapping) or not obj.get("colors"):
        raise FichaIlegible("el objeto `product` no trae colores")
    return obj


def _variantes(ficha: Mapping[str, Any], product_id: str) -> list[ScrapedVariant]:
    """Una variante por talla y color, con su precio y su stock.

    El tachado sale de `prices.previousPrices.originalShop` y **solo cuenta si es estrictamente
    mayor** que el precio: es la misma guarda que en Cacles, donde venía igual al precio en 248 de
    428. Medido en Mango sobre 58 colores con tachado: 58 honestos, 0 mentiras.
    """
    variantes: list[ScrapedVariant] = []
    for color in ficha.get("colors") or []:
        if not isinstance(color, Mapping):
            continue
        color_id = color.get("id")
        if not isinstance(color_id, str):
            continue
        etiqueta = color.get("label") if isinstance(color.get("label"), str) else None
        crudo_precios = color.get("prices")
        precios: Mapping[str, Any] = crudo_precios if isinstance(crudo_precios, Mapping) else {}
        precio = _decimal(precios.get("price"))
        anteriores = precios.get("previousPrices")
        tachado = (
            _decimal(anteriores.get("originalShop")) if isinstance(anteriores, Mapping) else None
        )
        if precio is None:
            continue  # sin precio no hay nada que registrar en el histórico
        if tachado is not None and tachado <= precio:
            tachado = None
        for talla in color.get("sizes") or []:
            if not isinstance(talla, Mapping):
                continue
            talla_id = talla.get("id")
            if not isinstance(talla_id, str):
                continue
            variantes.append(
                ScrapedVariant(
                    # Estable y ajeno a la temporada: los tres ids son de la tienda.
                    retailer_variant_id=f"{product_id}-{color_id}-{talla_id}",
                    size=talla.get("label") if isinstance(talla.get("label"), str) else None,
                    color=etiqueta,
                    sku=f"{product_id}-{color_id}-{talla_id}",
                    price=precio,
                    list_price=tachado,
                    in_stock=bool(talla.get("available")),
                )
            )
    return variantes


def _imagenes(
    ficha: Mapping[str, Any], colores_con_variantes: set[str | None]
) -> list[ScrapedImage]:
    """La galería, atribuida al color que retrata.

    El color se toma del MISMO campo que alimenta `ScrapedVariant.color` (`colors[].label`), que es
    lo que la ficha del web usa para emparejar foto y precio. Un color sin variantes utilizables no
    aporta fotos, como pide `ScrapedImage`.
    """
    imagenes: list[ScrapedImage] = []
    for color in ficha.get("colors") or []:
        if not isinstance(color, Mapping):
            continue
        etiqueta = color.get("label") if isinstance(color.get("label"), str) else None
        if etiqueta not in colores_con_variantes:
            continue
        looks = color.get("looks")
        candidatos = (
            looks.values()
            if isinstance(looks, Mapping)
            else looks
            if isinstance(looks, list)
            else []
        )
        for look in candidatos:
            if not isinstance(look, Mapping):
                continue
            for medio in look.get("media") or []:
                if not isinstance(medio, Mapping) or medio.get("format") != "IMAGE":
                    continue
                src = medio.get("src")
                if isinstance(src, str) and src:
                    imagenes.append(ScrapedImage(color=etiqueta, url=src))
    return list(dict.fromkeys(imagenes))


def foto_de_listado(fila: Fila) -> str | None:
    """La foto primaria a partir del listado, sin una sola petición (punto 9 de la cabecera)."""
    if not fila.portrait_id:
        return None
    return _MEDIA_URL.format(producto=fila.product_id, color=fila.color_id, foto=fila.portrait_id)


def producto(
    ficha: Mapping[str, Any], scope: ScrapeScope, filas: Sequence[Fila] = ()
) -> ScrapedProduct | None:
    """Un `ScrapedProduct` a partir de la ficha ya parseada. Pura.

    `filas` son las del listado y solo se usan para la foto primaria cuando la ficha no trae
    galería: es información que ya tenemos y ahorra dejar el producto sin foto.
    """
    product_id = ficha.get("id")
    if not isinstance(product_id, str) or not product_id:
        return None
    variantes = _variantes(ficha, product_id)
    if not variantes:
        return None
    colores = {v.color for v in variantes}
    imagenes = _imagenes(ficha, colores)
    url = ficha.get("url")
    nombre = ficha.get("name")
    return ScrapedProduct(
        retailer_product_id=product_id,
        name=nombre if isinstance(nombre, str) and nombre else product_id,
        gender=scope.gender,
        section=scope.section,
        category=scope.category,
        url=f"{BASE_URL}{url}" if isinstance(url, str) and url.startswith("/") else None,
        variants=variantes,
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=product_id,
            section=scope.section,
            category=scope.category,
            # Mango no es barefoot nativa, así que decide la heurística de texto. Se le da el
            # nombre y la descripción, que es lo que la ficha nombra.
            texts=[t for t in (nombre, _descripcion(ficha)) if t],
        ),
        image_url=imagenes[0].url
        if imagenes
        else next((u for u in (foto_de_listado(f) for f in filas) if u), None),
        images=imagenes,
    )


def _descripcion(ficha: Mapping[str, Any]) -> str | None:
    """Las viñetas de la descripción del primer color, unidas. Alimenta la heurística barefoot."""
    for color in ficha.get("colors") or []:
        if not isinstance(color, Mapping):
            continue
        desc = color.get("description")
        if isinstance(desc, Mapping):
            bullets = [b for b in (desc.get("bullets") or []) if isinstance(b, str)]
            if bullets:
                return " ".join(bullets)
    return None


def _ambito(hojas: Sequence[CategoryConfig]) -> ScrapeScope:
    """El ámbito de un producto a partir de las hojas en las que ha aparecido.

    La regla vive en `base.ambito_cruzado()`, que es donde está escrito el porqué; aquí solo se
    traduce la `CategoryConfig` de esta tienda. El cruce vale 69 productos de 1502 (4,6 %) el
    03/08/2026.
    """
    return ambito_cruzado([ScrapeScope(h.gender, h.section, h.category) for h in hojas])


class MangoStore:
    """Scraper de Mango. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "Mango"
    base_url = BASE_URL

    def __init__(self, config: Config, categories: list[CategoryConfig] | None = None) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._filas: dict[str, list[Fila]] = {}
        self._hojas: dict[str, list[CategoryConfig]] = {}

    # --- red ---------------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        """Sin UA de Chrome, Akamai responde 403 a todo (comprobado en los tres endpoints)."""
        ua = self._config.user_agent or _UA_RESPALDO
        return httpx.Client(
            headers={"User-Agent": ua, "Accept-Language": "es-ES,es;q=0.9"},
            timeout=self._config.request_timeout,
            follow_redirects=True,  # la ficha por id responde 308 a la URL canónica
        )

    def _polite_pause(self) -> None:
        """Pausa entre peticiones con jitter. `robots.txt` declara `Crawl-delay: 0.2`."""
        base = max(self._config.request_delay, 0.2)
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _get(
        self, client: httpx.Client, url: str, params: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp
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

    def _get_hoja(self, client: httpx.Client, catalog_id: str) -> Any:
        resp = self._get(client, _LIST_URL.format(catalog=catalog_id), {"languageIso": "es"})
        return resp.json()

    # --- contrato ----------------------------------------------------------------------------

    def scopes(self) -> Iterable[ScrapeScope]:
        """Los ámbitos declarados **más su equivalente `unisex`**.

        Lo segundo no es cosmético: el 4,6 % de los productos sale en hojas de los dos géneros y se
        emite como `unisex`, así que un ámbito `unisex` que no se declarase aquí no contaría como
        escaneado y sus productos no se podrían descatalogar nunca (ver `base.con_unisex`).
        """
        return con_unisex(ScrapeScope(c.gender, c.section, c.category) for c in self._categories)

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Recorre las hojas y emite una entrada por producto.

        **Acumula la pasada entera antes de emitir**, como H&M y por los dos mismos motivos: los
        colores de un producto se reparten entre hojas, y si sale en hojas de géneros distintos es
        `unisex` — cosa que no se puede decidir con una hoja delante. Son ~3500 filas: la memoria da
        igual, y el ámbito de una entrada ya emitida no se puede corregir.
        """
        self._scan = ScanReport()
        self._filas = {}
        self._hojas = {}

        with self._client() as client:
            for cat in self._categories:
                filas = self._leer_hoja(client, cat)
                if filas is None:
                    continue  # hoja caída: ya está contada y su ámbito fuera de las bajas
                for fila in filas:
                    vistas = self._filas.setdefault(fila.product_id, [])
                    if fila.color_id not in {f.color_id for f in vistas}:
                        vistas.append(fila)
                    hojas = self._hojas.setdefault(fila.product_id, [])
                    if cat not in hojas:
                        hojas.append(cat)

        for product_id, filas in self._filas.items():
            scope = _ambito(self._hojas[product_id])
            yield ListingEntry(
                retailer_product_id=product_id,
                signature=firma_listado(filas),
                gender=scope.gender,
                section=scope.section,
                category=scope.category,
            )

    def _leer_hoja(self, client: httpx.Client, cat: CategoryConfig) -> list[Fila] | None:
        """Las filas de una hoja, o `None` si la hoja está caída.

        Aquí no hay bucle de paginación: `/filters` devuelve la hoja entera (punto 2 de la
        cabecera), así que tampoco existe el riesgo de contar como sana una hoja truncada.
        """
        scope = ScrapeScope(cat.gender, cat.section, cat.category)
        try:
            payload = self._get_hoja(client, cat.catalog_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in GONE_STATUS:
                self._hoja_comprometida(
                    scope,
                    cat.catalog_id,
                    f"la hoja {cat.catalog_id!r} devolvió {exc.response.status_code}",
                )
                return None
            raise
        if not es_listado(payload):
            # No es que la hoja esté vacía: es que esto ya no tiene forma de listado, o sea un
            # cambio de la API. Su ámbito deja de ser seguro para dar bajas. Ver `es_listado()`.
            self._hoja_comprometida(
                scope,
                cat.catalog_id,
                f"la hoja {cat.catalog_id!r} respondió 200 sin la forma de un listado",
            )
            return None
        self._scan.leaf_ok()
        # Una hoja vacía es una hoja sana con 0 productos (típico en `rebajas_*` fuera de campaña):
        # cuenta como leída y no aporta nada, que es exactamente lo que la tienda está diciendo.
        return parse_filas(payload)

    def _hoja_comprometida(self, scope: ScrapeScope, leaf: str, motivo: str) -> None:
        """Cuenta la hoja como caída y saca su ámbito —y el `unisex` equivalente— de las bajas.

        El porqué de lo segundo está en `ScanReport.leaf_gone()`.
        """
        self._scan.leaf_gone(scope, leaf, tambien_unisex=True)
        logger.warning("%s: %s; se omiten las bajas de ese ámbito", SLUG, motivo)

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Una petición por producto: la ficha trae nombre, colores, tallas, stock y tachado.

        El listado no trae ni el nombre ni la URL, así que aquí no se puede servir de caché como en
        H&M o C&A. Lo que abarata la pasada es el detalle condicional de `ingest.py`: en régimen
        solo llegan aquí los productos cuya huella ha cambiado.

        Distingue con cuidado **«ya no está»** de **«no he podido verlo»**, que es la confusión que
        provoca bajas falsas: un producto que sale en el listado y cuya ficha no llega no recibe
        `last_seen_at`, así que a las `SCRAPER_DELIST_MIN_MISSES` pasadas lo descatalogan — y las
        redes de `ingest.py` no lo ven, porque su ámbito sigue lleno en el listado. Solo
        `GONE_STATUS` significa retirado; un 403, un status inesperado, un fallo de transporte o
        una ficha ilegible son problema nuestro, y por encima de `_MAX_FICHAS_FALLIDAS` **seguidas**
        la pasada se aborta entera en vez de guardar un catálogo mutilado que parece sano (mismo
        criterio que `hipercor.py` y `zara.py`).

        Aquí la ficha ilegible pesa más que en Hipercor: `parse_ficha()` depende de la plantilla
        RSC de Next.js, así que un cambio de la tienda no rompe una ficha suelta sino **todas a la
        vez**, y sin el tope eso sería una descatalogación masiva silenciosa de justo los productos
        que habían cambiado de precio. Una ficha leída —aunque sea un 404 honesto— reinicia la
        cuenta: prueba que la tienda nos sigue dejando entrar y sirviendo la plantilla que
        conocemos.
        """
        fallos = 0
        with self._client() as client:
            for entry in entries:
                pid = entry.retailer_product_id
                try:
                    resp = self._get(client, _FICHA_URL.format(producto=pid))
                    ficha = parse_ficha(resp.text)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in GONE_STATUS:
                        # Retirado entre el listado y el detalle. No se emite: la ausencia la
                        # resuelve la histéresis de bajas, que es conservadora a propósito.
                        logger.info("%s: %s ya no tiene ficha (404)", SLUG, pid)
                        fallos = 0
                        continue
                    fallos = self._ficha_fallida(pid, f"HTTP {exc.response.status_code}", fallos)
                    continue
                except httpx.TransportError as exc:
                    fallos = self._ficha_fallida(pid, f"{type(exc).__name__}: {exc}", fallos)
                    continue
                except FichaIlegible as exc:
                    fallos = self._ficha_fallida(pid, f"ficha ilegible ({exc})", fallos)
                    continue
                fallos = 0  # la tienda nos deja entrar y la plantilla sigue siendo la que sabemos
                prod = producto(ficha, entry.scope, self._filas.get(pid, ()))
                if prod is None:
                    continue
                if prod.retailer_product_id != pid:
                    # La ficha dice ser otro producto. Emitirlo guardaría su huella bajo el id
                    # equivocado y `ingest.py` volvería a pedir el detalle de los dos en CADA
                    # pasada, tirando el ahorro de las dos fases sin que nada falle a la vista.
                    logger.warning(
                        "%s: la ficha de %s dice ser %s; se omite",
                        SLUG,
                        pid,
                        prod.retailer_product_id,
                    )
                    continue
                yield prod

    def _ficha_fallida(self, pid: str, motivo: str, fallos: int) -> int:
        """Cuenta una ficha que no se ha podido leer y aborta si van demasiadas seguidas."""
        fallos += 1
        logger.warning("%s: ficha %s -> %s (%d fallo/s seguido/s)", SLUG, pid, motivo, fallos)
        if fallos > _MAX_FICHAS_FALLIDAS:
            raise DetailUnavailable(
                f"{fallos} fichas seguidas sin poder leerse (última: {pid}, {motivo}). No es que "
                "los productos se hayan retirado: es que la tienda no nos los deja ver o ha "
                "cambiado la plantilla, así que la pasada se aborta sin escribir."
            )
        return fallos

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    # --- capacidades opcionales --------------------------------------------------------------

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas. Ver `stores.base.SupportsLeafHealth`.

        El sondeo más simple del repo, y es mérito de la tienda: un catálogo que no existe da 404,
        así que basta el status. Sin canario (H&M), sin comparar con el padre (Sfera, Hipercor) y
        sin desambiguar una lista vacía (Cacles).
        """
        with self._client() as client:
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    payload = self._get_hoja(client, cat.catalog_id)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in GONE_STATUS:
                        yield LeafHealth(scope, cat.catalog_id, False, f"HTTP {status}")
                    else:
                        yield LeafHealth(scope, cat.catalog_id, None, f"HTTP {status}")
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, cat.catalog_id, None, f"{type(exc).__name__}: {exc}")
                else:
                    if not es_listado(payload):
                        yield LeafHealth(
                            scope, cat.catalog_id, None, "200 sin la forma de un listado"
                        )
                        continue
                    # Una hoja vacía sigue VIVA: las de `rebajas_*` se vacían al acabar una
                    # campaña, y llamar a eso «retirada» convertiría al vigía en un avisador en
                    # falso semanal. La retirada de verdad la dice el 404. Ver `es_listado()`.
                    ids = ids_de_hoja(payload)
                    yield LeafHealth(
                        scope,
                        cat.catalog_id,
                        True,
                        f"{len(ids)} productos" if ids else "viva, sin productos ahora mismo",
                    )

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Confirma bajas preguntando por la ficha. Ver `stores.base.SupportsAliveProbe`.

        `/es/es/p/_{id}` redirige (308) a la URL canónica cuando el producto existe y da **404
        honesto** cuando no, comprobado con cuatro ids inventados. Un fallo de red o un status
        inesperado se dejan **fuera del mapa**: no concluyente, que es lo que la ingesta necesita
        para no descatalogar en masa por un bloqueo.
        """
        veredicto: dict[str, bool] = {}
        with self._client() as client:
            for cand in candidates:
                try:
                    self._get(client, _FICHA_URL.format(producto=cand.retailer_product_id))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in GONE_STATUS:
                        veredicto[cand.retailer_product_id] = False
                    # cualquier otro status: sin veredicto
                except httpx.TransportError:
                    pass  # sin veredicto
                else:
                    veredicto[cand.retailer_product_id] = True
        return veredicto

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """El árbol que la tienda publica, leído de su menú. Ver `stores.base.SupportsCategoryTree`.

        Mango es la primera tienda del repo que lo sirve entero en **una** petición pública, la
        misma que usa su web. `root` es el `catalogId` (o el prefijo de colección) del que colgar;
        vacío o `ninos` devuelve el mundo infantil completo.

        La tienda no declara cuántos productos tiene cada hoja en el menú, así que `count` va a
        `None` — que es la respuesta honesta a «no lo dice», distinta de 0.
        """
        with self._client() as client:
            payload = self._get(
                client, _MENU_URL, {"channelId": "shop", "countryIso": "ES", "languageIso": "es"}
            ).json()
        mundo = next(
            (
                m
                for m in (payload.get("menus") or [])
                if isinstance(m, Mapping) and m.get("id") == _MUNDO
            ),
            None,
        )
        if mundo is None:
            return
        raiz = (root or "").strip()
        for nodo, profundidad in _recorrer(mundo, 0):
            catalog_id = nodo.get("catalogId")
            if not isinstance(catalog_id, str):
                continue
            if raiz and raiz not in ("ninos", _MUNDO) and not catalog_id.startswith(raiz):
                continue
            yield CategoryNode(
                path=catalog_id,
                title=str(nodo.get("labelId") or catalog_id),
                count=None,
                depth=profundidad,
                has_children=bool(nodo.get("menus")),
            )

    def mapped_leaves(self) -> Iterable[str]:
        """Las hojas que ingerimos hoy, en el vocabulario de `CategoryNode.path`."""
        return [c.catalog_id for c in self._categories]


def _recorrer(nodo: Mapping[str, Any], profundidad: int) -> Iterable[tuple[Mapping[str, Any], int]]:
    """Los descendientes de `nodo` con su profundidad, de arriba abajo (solo el mundo infantil)."""
    for hijo in nodo.get("menus") or []:
        if not isinstance(hijo, Mapping):
            continue
        url = hijo.get("url")
        if isinstance(url, str) and url and _RUTA_MUNDO not in url and not hijo.get("menus"):
            continue
        yield hijo, profundidad + 1
        yield from _recorrer(hijo, profundidad + 1)
