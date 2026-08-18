"""Scraper de H&M (hm.com): la tienda cuya hoja muerta responde 200 con una página llena.

Entra por la API de listado, que vive en **otro host que el escaparate**:

  - listado:  GET https://api.hm.com/search-services/v1/es_es/listing/resultpage
  - escaparate: https://www2.hm.com  (Akamai; 403 a `curl`, `wget` y `httpx`)

El 403 de Akamai es solo del escaparate. `api.hm.com` responde **200 a `httpx` pelado, sin una sola
cabecera** — ni User-Agent, ni `origin`, ni cookies. Así que esta tienda **no necesita
`BrowserSession`** para la pasada, al contrario de lo que la épica supuso durante meses. El
navegador solo se usó una vez, en el reconocimiento, para leer cosas que Akamai no deja leer a un
cliente plano (ver «Cumplimiento» y `CATEGORIES`).

Como en Cacles, C&A e Hipercor, **el listado ya es el detalle**: trae colores, tallas, stock,
precios e imágenes, así que `fetch_details()` sirve de caché sin una sola petición extra. El
catálogo infantil entero son ~158 peticiones y ~100 s.

── LO IMPORTANTE DE ESTE FICHERO ──────────────────────────────────────────────────────────────────

**1. `categoryId` no selecciona nada; el selector es `pageId`. Y un `pageId` que no resuelve no da
404, ni lista vacía, ni el catálogo del padre: devuelve el cubo entero de `categoryId`.**

Es la cuarta forma de mentir de una hoja muerta que se encuentra en este proyecto —404 honesto en
Zara, catálogo del padre en Sfera (#54), lista vacía indistinguible del fin de paginación en Cacles—
y la única que **no se detecta ni por status ni por vacío**. Una hoja renombrada seguiría
«funcionando» e ingiriendo productos de otra categoría en silencio, y la red de seguridad por umbral
de `ingest.py` no vería caer nada porque no cae nada.

Que la caída sea al cubo de `categoryId` es justo lo que la hace detectable, y se comprobó cambiando
solo ese parámetro (02/08/2026):

    pageId=/canario/inexistente  categoryId=kids_all       -> 9713 productos
    pageId=/canario/inexistente  categoryId=kids_shoes     ->  244
    pageId=/canario/inexistente  categoryId=kids_clothing  -> 4113

De ahí `es_espejismo()`: se pide **una** ruta deliberadamente inventada por pasada (el canario) y se
compara con ella la primera página de cada hoja. Es el mismo patrón que `is_mirage` en `sfera.py`,
cambiando el padre por el canario — que además es mejor, porque no hace falta saber cuál es el
padre.

**Se comparan los IDS, no `numberOfHits`.** El contador del cubo deriva entre peticiones
consecutivas (9713 -> 9710 en segundos, según entra y sale stock), así que una igualdad exacta de
contador declara viva una hoja muerta, que es el error caro. Medido sobre las hojas reales: solape
con el canario del **100 %** en las muertas y del **0-8 %** en las vivas (el 8 % es una hoja ancha
que legítimamente comparte producto con el cubo), así que el umbral de 0,5 tiene holgura de sobra
por los dos lados.

**Y un nodo del menú tampoco es siempre una página seleccionable**, así que el canario hace falta
también para MEDIR, no solo para ingerir. Medido el 05/08/2026 (#189): `/kids/girls/school` y
`/kids/boys/school` son espejismo, igual que `/kids/girls/clothing` y la propia `/kids/girls` —
son contenedores, y solo resuelven sus hijas—, mientras que `/kids/girls/sportswear` (109 modelos)
y `/kids/girls/outerwear` (114) sí resuelven. O sea que no se puede deducir del sitio que ocupa en
el árbol: hay que preguntarlo. Quien mida «cuánto hay» en una rama pidiéndole al padre sin comparar
con el canario se llevará ~9700 productos y sacará la conclusión contraria.

**Y hay una QUINTA forma de mentir, que el canario NO caza** (#468). `/kids/local-occasion/2-8`
(«HASTA -40%») resuelve, responde 1859 filas y **no es catálogo infantil**: entre lo que no
teníamos hay alfombras de baño, anillas para cortina de ducha, cestas de almacenaje, bandejas de
mármol, americanas y cubrepezones. No devuelve el cubo de `kids_all` —por eso el canario la da por
viva— sino **otra cosa**: el `pageId` pisa al `categoryId`. Contra esto no hay señal barata; lo que
hay es mirar los nombres de lo que trae antes de mapear una rama. Sus seis hijas por género son
espejismo o vacías. La rama que sí sirve es `last-chance`, y está mapeada al final de `CATEGORIES`.

**2. Una fila del listado es un producto+color, no un producto.** `id` = `1343222003` son 7 dígitos
de modelo y 3 de color, y **la misma raíz aparece en varias filas de la misma hoja** (medido: 36
filas -> 27 raíces, con raíces repetidas hasta 3 veces). Así que aquí NO se deduplica con «gana la
primera» como en Cacles o C&A: eso tiraría colores reales. Se **agrupa** por raíz, y por eso
`list_catalog()` acumula antes de emitir — las filas hermanas de un modelo pueden caer en páginas
distintas, e incluso en hojas distintas.

**3. El 9,3 % del catálogo sale publicado a la vez en niño y en niña**, y eso lo decide el género.
Medido el 02/08/2026 sobre las 59 hojas: **317 raíces de 3401**. Un dedup «gana la primera» las
habría dejado todas en el género de la hoja declarada antes, sacándolas de la otra sección — que es
exactamente el fallo que la #98 destapó en Hipercor. Aquí se resuelve al agrupar: una raíz que sale
en hojas de géneros distintos es `unisex`, que el catálogo y el matching ya tratan como «sale en
niño y en niña» (ver `services/web/src/catalog/gender.sql.ts`). La sección y la categoría, en
cambio, sí las fija la primera hoja que lo trajo.

**4. Las tallas invierten el convenio del resto de tiendas, y por eso se remodelan aquí.** Zara e
Hipercor sirven la edad delante y los centímetros en el paréntesis (`5-6 años (116 cm)`); H&M sirve
`122/128 (6-8Y)` y `74 (6-9M)`. Y `size_canon` (`db/migrations/0020_…`) descarta los paréntesis, que
es justo donde H&M pone la edad: `'122/128 (6-8Y)'` saldría **`122-128`** y `'74 (6-9M)'` saldría
**`74`** — sin edad, y colisionando con el espacio de los números de pie, que es la ambigüedad que
costó la #64. `_talla()` le da la vuelta a la etiqueta ANTES de emitirla, así que la función de la
base la reconoce por su regla de años/meses y devuelve `6-8 años` y `6-9 meses`, el mismo
vocabulario que ya producen las otras tiendas. Se remodela en la tienda y no en una migración nueva
a propósito: hay precedente (`sfera.py:_normalize_size`, `hipercor.py`), no exige reconstruir el
índice de `size_canon` y no toca lo que las otras cinco tiendas ya producen.

**5. Esta tienda no tiene `probe_alive`.** No hay endpoint de producto suelto: el buscador
(`pageSource=SEARCH&q=<id>`) devuelve el cubo del canario para CUALQUIER `q`, incluso para un id
vivo, y la única ficha está en `www2` (403). Así que las bajas se apoyan solo en la histéresis
(`SCRAPER_DELIST_MIN_MISSES`) y en el acotado por ámbito. Se declara aquí porque no tenerlo es una
decisión medida, no un olvido.

**6. El descuento en infantil YA no es hipotético, y lo trajo la rama de saldo** (#468). Hasta el
17/08/2026 esto decía que no había ni uno: barrido completo del 02/08/2026, **0 de 6518 filas** con
más de un precio, todas `whitePrice`. La forma del tachado se había medido en otro departamento de
la misma API (`/ladies/sale/view-all`) —un rebajado trae **dos** entradas en `prices[]`, `redPrice`
(el que se paga) y `whitePrice` (el tachado)— y `_precios()` se escribió contra esa forma sin poder
ejercerla con dato de niño.

Ya está ejercida. Pasada real del 18/08/2026 con `last-chance` mapeada: **7173 de 58275 filas** con
tachado, repartidas en **727 productos, de los que 724 son de la rama de saldo** (los otros 3, del
catálogo normal). Descuento medio **38,8 %**, precios desde 2,99 € y tachados hasta 49,99 €. No hubo
que tocar `_precios()`: la forma era la prevista. Dos consecuencias: el camino del precio rebajado
de esta tienda **deja de ser el que nunca se ha probado** (#106), y el detector de ofertas engañosas
empieza a tener materia prima de H&M — que hasta ahora salía de C&A y Springfield.

Paginación, sin ambigüedad ninguna (a diferencia de C&A, donde arrancar en 1 se saltaba un tercio de
cada hoja): **arranca en 1 y la tienda lo dice** — `page=0` responde `422` con
`{"page":[{"code":"positive","message":"page number must be greater than 0"}]}`. El fin es limpio:
pedir más allá de `totalPages` devuelve 200 con 0 productos, `numberOfHits` y `totalPages` intactos
y **sin `nextPageNum`**. El tamaño de página sube a **72** (con 80 o más responde 422).

Cumplimiento (comprobado el 02/08/2026): el `robots.txt` de `www2.hm.com` devuelve 403 a `curl` y a
`wget` incluso con UA de Chrome, pero **Chromium lo descarga con normalidad** — el mismo hallazgo
que resolvió Hipercor (#92). Su bloque `User-Agent: *` veta carrito, checkout, login, `/reviews/`,
`/assets/`, `*/search-results.html*`, `/*.product-article-` y `*/index.*.html`: **nada que toque ni
las rutas de categoría ni la ficha de producto**, y no declara `Crawl-delay`. `api.hm.com` **no
sirve `robots.txt`**: devuelve `503 DNS failure` del edge de Akamai, así que en ese host no hay nada
que obedecer. Las reglas son prefijos desde la raíz de CADA host, y la API vive en otro host que el
escaparate — que es justo el detalle que en Hipercor tumbó el diseño entero.

Las funciones `parse_*`, `es_espejismo()` y `_talla()` son puras y se testean con fixtures.
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
from ..progreso import Latido
from ..tags import TAG_DEPORTIVA
from .base import (
    CategoryNode,
    FiltroDeHoja,
    LeafHealth,
    ListingEntry,
    ProductTags,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    ambito_cruzado,
    con_unisex,
)
from .browser import BrowserSession

logger = logging.getLogger(__name__)

SLUG = "hm"  # va en `--retailer`, en `retailer.slug` y en el nombre del CronJob
BASE_URL = "https://www2.hm.com"
_API_URL = "https://api.hm.com/search-services/v1/es_es/listing/resultpage"

# El cubo al que cae un `pageId` que no resuelve. Se manda el mismo en todas las peticiones para que
# un solo canario por pasada valga para todas las hojas.
_CATEGORY_ID = "kids_all"
# Ruta deliberadamente inventada: la respuesta a esto ES el cubo, y con ella se reconocen las hojas
# muertas. Lleva el nombre del proyecto para que, si alguien la ve en los logs de H&M, se entienda.
_CANARIO_PAGE_ID = "/deal-tracker/esta-hoja-no-existe"

# Página del escaparate de la que se lee el ÁRBOL (#179). Da igual cuál sea mientras exista: el
# menú es el mismo en todas, así que se usa una hoja que ya está en `CATEGORIES` — si algún día
# dejara de resolver, `check_leaves()` lo dice antes y con mejor diagnóstico que esta capa.
_MENU_URL = f"{BASE_URL}/es_es/ninos/nina/ropa/vestidos.html"
# Una entrada del menú: rótulo y ruta navegable, en ese orden y pegados. Ver `parse_category_tree`
# para por qué se leen `title`/`path` y no `targetPath` ni las rutas sueltas del documento.
_RE_MENU = re.compile(
    r'"title":"(?P<title>(?:[^"\\]|\\.)*)","path":"/es_es(?P<path>/(?:kids|baby)[a-z0-9\-/]*)\.html"'
)
# Fracción de la primera página que ha de coincidir con el canario para llamarlo espejismo. Medido:
# 100 % en las hojas muertas, 0-8 % en las vivas.
_SOLAPE_ESPEJISMO = 0.5

# Los 7 primeros dígitos de `id` son el modelo; los 3 últimos, el color.
_LONGITUD_RAIZ = 7

# 72 es el máximo que acepta la API (con 80 responde 422).
_PAGE_SIZE = 72
_PAGINA_INICIAL = 1
# Tope de guarda. La hoja más grande hoy son 470 productos = 7 páginas; treinta dan margen de sobra.
_MAX_PAGES = 30

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# `sizes[].stock` es un código, no una cantidad: en 659 tallas medidas solo aparecen 0 y 2. Se trata
# como lo hace C&A con `isAvailable` — manda el criterio de la tienda, no una cuenta nuestra.
_SIN_STOCK = 0


@dataclass(frozen=True)
class CategoryConfig:
    """Una hoja del catálogo que recorremos.

    `page_id` es la ruta que la propia tienda usa para seleccionar la categoría. **No se puede
    adivinar**: se probaron 18 rutas plausibles de bebé (`/kids/baby`, `/kids/baby-boy/clothing`,
    `/kids/newborn-0-9m/clothing`…) y las 18 devolvieron el cubo, porque bebé no cuelga de `/kids`
    sino que es un departamento aparte, `/baby`. Ver `CATEGORIES`.
    """

    page_id: str
    gender: str  # niño | niña | unisex
    section: str  # ropa | zapateria
    category: str  # nuestro vocabulario, no el suyo
    # Solo en las hojas que mezclan vocabulario (#200). Ver `FiltroDeHoja` y `_SOLO_CONJUNTOS`.
    #
    # **Aquí el filtro solo sabe DESCARTAR**, no repartir en dos categorías como en Sfera: en esta
    # tienda la categoría no la lleva la fila sino la hoja (`_ambito()` la resuelve a partir de las
    # hojas en las que sale el modelo), así que un `resto` no tendría dónde vivir. Se rechaza al
    # importar en vez de dejar que mienta en silencio.
    filtro: FiltroDeHoja | None = None
    # Las dos marcas de una **hoja de campaña** (#468), con la misma semántica que estrenaron
    # `lefties.py` (#195) y `zara.py` (#356) y una diferencia que importa: allí la categoría del
    # residuo la da la taxonomía de la tienda (`familyName`), y aquí **no hay taxonomía ninguna**,
    # así que la da el NOMBRE. Ver `_NOMBRE_A_DOMINIO` y `categoria_desde_nombre()`.
    #
    # Con `por_familia`, `section` y `category` van vacías porque la hoja no las decide; `gender`
    # **sí sigue siendo de la hoja**, que por eso se mapean las de género y no las de edad.
    por_familia: bool = False
    estacional: bool = False  # apagarse es fin de campaña, no retirada (ver `LeafHealth`)

    def __post_init__(self) -> None:
        if self.filtro is not None and self.filtro.resto is not None:
            raise ValueError(
                f"{SLUG}: {self.page_id!r} declara un filtro con `resto`, y aquí la categoría la "
                "fija la hoja y no la fila: usa dos hojas o cambia `_ambito()`"
            )
        # Las dos mitades del mismo invariante, y se comprueban al importar porque las dos mienten
        # en silencio: una hoja `por_familia` con categoría se la impondría a todo lo que trae, y
        # una normal sin categoría emitiría productos con la sección vacía.
        if self.por_familia and (self.section or self.category):
            raise ValueError(
                f"{SLUG}: {self.page_id!r} es `por_familia`, así que su sección y su categoría las "
                f"decide cada prenda: déjalas vacías (tiene {self.section!r}/{self.category!r})"
            )
        if not self.por_familia and not (self.section and self.category):
            raise ValueError(
                f"{SLUG}: {self.page_id!r} no declara sección/categoría y no es `por_familia`"
            )


# Aquí la señal es el NOMBRE, porque el listado de esta tienda no publica ninguna taxonomía de tipo
# de prenda (Zara sí, con `familyName`, y Sfera tiene una faceta): las 30 facetas de la respuesta
# vienen con `values: []`. A cambio el rótulo es sistemático — «Conjunto de 2 piezas», «Conjunto de
# sudadera y joggers», «Conjunto de 3 bodies y 3 pantalones»— y viene en `Fila.name`, o sea en el
# listado, así que decidir no cuesta ninguna petición de ficha.
#
# La segunda alternativa no es un adorno: **parte del catálogo viene sin traducir** y esos productos
# se rotulan «2-piece cotton set», «3-piece denim set», «2-piece T-shirt and joggers set». Medido el
# 06/08/2026 sobre las siete hojas: 20 filas así, y son conjuntos igual que los otros. Dejarlas
# fuera haría que el criterio fuese «los que la tienda haya traducido», que es un accidente y no una
# decisión.
#
# Lo que se queda fuera, y sí está decidido: `Disfraz` (38 filas) y `Traje` (12), porque
# `fancy-dress-costumes` y `blazers-suits` ya están declaradas fuera del brief en esta misma lista y
# entrarían por la puerta de atrás — que es exactamente lo que #192 destapó.
#
# **Y por eso hace falta `excepto`.** El disfraz no se cuela solo con ese nombre: la tienda publica
# también «Conjunto de disfraz» y «Conjunto de disfraz de 3 piezas», que el patrón de arriba acepta
# encantado. Medido en la pasada real del 06/08/2026 contra Postgres: de los 7 conjuntos que
# llegaron a ingerirse, **3 eran disfraces**. Se vio leyendo los nombres uno a uno —`SELECT name
# FROM product WHERE category = 'conjuntos'`—, la comprobación que #192 dejó como obligatoria.
#
# Anclado al principio a propósito: «Vestido con conjunto de …» no es un conjunto, es un vestido.
_SOLO_CONJUNTOS = FiltroDeHoja(
    re.compile(r"\A(Conjunto\b|\d+-piece\b.*\bset\b)", re.IGNORECASE),
    excepto=re.compile(r"disfraz|fancy.dress|costume", re.IGNORECASE),
)


# La sección y la categoría de una hoja `por_familia`, a partir del NOMBRE de la prenda (#468).
#
# **Aquí no hay taxonomía que valga**: Zara reparte su residuo con `familyName` y Sfera tiene una
# faceta de tipo de producto, pero las 30 facetas de esta tienda vienen con `values: []` (ver
# `_SOLO_CONJUNTOS`, que ya se apoyaba en el nombre por lo mismo). Lo que hay es el rótulo, y es
# utilizable: medido el 18/08/2026 sobre los **456 modelos** que solo viven en la rama de saldo,
# esta tabla clasifica **305**, descarta **151** a propósito y **no deja ninguno sin
# reconocer**. El reparto: conjuntos 92, vestidos 79, pantalones 44, camisetas 40, zapatos 25,
# ropa-interior 17 y sudaderas 8.
#
# **Es bilingüe porque el catálogo lo es**, y no por elegancia: 78 de esos 456 (17 %) llegan sin
# traducir («Flounced swimsuit», «Lined flannel overshirt», «2-pack cotton Henley tops»). Con solo
# el español el criterio sería «los que la tienda haya traducido», que es un accidente — el mismo
# razonamiento que ya hizo #200 con los conjuntos ingleses.
#
# **El orden ES la regla**: gana la primera que casa, así que lo específico va antes que lo
# genérico y los descartes van los primeros. Dos trampas medidas, las dos de esta tabla:
#
#   - `chaqueta de punto` es una sudadera y `chaqueta` a secas es un abrigo, que está fuera del
#     brief. Si el abrigo va delante, se lleva las dos.
#   - `conjuntos` es **categoría propia** en este repo desde #192 y esta tienda ya la usa en sus
#     hojas `sets-outfits`. Mandarlos a `vestidos` —que fue el primer intento— metía **171 de los
#     456** en la categoría equivocada, y ninguna prueba lo habría cantado.
#
# `Disfraz` y `Traje` se descartan por lo mismo que en `_SOLO_CONJUNTOS`: `fancy-dress-costumes` y
# `blazers-suits` están declaradas fuera del brief, y entrarían por la puerta de atrás.
_PATRONES_DOMINIO: tuple[tuple[str, tuple[str, str] | None], ...] = (
    # --- fuera del brief: primero, o el genérico se los traga ---
    (
        r"\b(bañador|bikini|tankini|bragas? de baño|swimsuit|swim shorts|trajes? de baño"
        r"|capa de baño|albornoz|chanclas de piscina)",
        None,
    ),
    (r"\b(disfraz|fancy.dress|costume|traje\b|americana|blazer)", None),
    (r"\b(gorro|gorra|sombrero|hat\b|cap\b|beanie|visera)", None),
    (r"\b(bufanda|guante|manopla|scarf|glove|mitten)", None),
    (r"\b(zapatillas de casa|slipper)", None),
    (r"\b(mochila|bolso|bag\b|neceser|riñonera|fiambrera|lunch)", None),
    (r"\b(cinturón|cinturon|tirantes|belt\b|corbata|pajarita|tie\b)", None),
    (r"\b(gafas|sunglasses|pinza|coletero|diadema|pasador|hair)", None),
    # `chaqueta de punto` es sudadera y va ANTES que el abrigo genérico (ver arriba).
    (r"\b(chaqueta de punto|cárdigan|cardigan)", ("ropa", "sudaderas")),
    (
        r"\b(abrigo|anorak|parka|plumífero|plumifero|plumón|plumon|chubasquero|chaqueta"
        r"|jacket|coat\b|chaleco|trench)",
        None,
    ),
    (r"\b(babero|muselina|toalla|manta|saco|arrullo|cambiador|capota)", None),
    (r"\b(paraguas|umbrella|calcetín|calcetin|sock)", None),
    # --- el brief, de lo más específico a lo más genérico ---
    (
        r"\b(braguitas?|bragas?|calzoncillo|bóxer|boxer|brief\b|hipster)",
        ("ropa", "ropa-interior"),
    ),
    (r"\b(pijama|camisón|camison|pyjama|nightwear)", ("ropa", "ropa-interior")),
    (r"\b(body|bodies|bodysuit|ranita|cubrepañal|cubrepanal)", ("ropa", "ropa-interior")),
    (r"\b(conjunto|sets?\b|piezas|-piece|multipack)", ("ropa", "conjuntos")),
    (r"\b(vestido|dress\b)", ("ropa", "vestidos")),
    (r"\b(falda|skirt)", ("ropa", "vestidos")),
    (r"\b(monos?\b|peto\b|pelele|jumpsuit|dungaree|romper)", ("ropa", "vestidos")),
    (r"\b(sudadera|hoodie|sweatshirt)", ("ropa", "sudaderas")),
    (
        r"\b(pantalón|pantalon|vaquero|jeans|jegging|legging|jogger|malla|chino"
        r"|trousers|short|bermuda)",
        ("ropa", "pantalones"),
    ),
    (
        r"\b(camiseta|camisa|blusa|polo|tops?\b|t-shirt|shirt|henley|overshirt)",
        ("ropa", "camisetas"),
    ),
    # `punto` va DETRÁS de la prenda, y es la tercera trampa de orden de esta tabla: es un TEJIDO,
    # no una prenda, así que «Pantalón corto en punto de algodón» y «Camiseta en punto de canalé»
    # son pantalón y camiseta. Con esta regla delante se los llevaba a `sudaderas` — 6 de los 456
    # modelos medidos el 18/08/2026, en silencio y sin test rojo. Lo que sí es prenda (`jersey`,
    # `jumper`) no necesita ir delante de nada: no casa con ninguna regla anterior.
    (r"\b(jersey|jerséi|jersei|jumper|punto\b)", ("ropa", "sudaderas")),
    (
        r"\b(zapatilla|zapato|bailarina|sandalia|merceditas|alpargata|bota|deportiva|chancla"
        r"|mocasín|mocasin|loafer|shoe|sneaker|sandal|pump|trainer)",
        ("zapateria", "zapatos"),
    ),
)

_NOMBRE_A_DOMINIO: list[tuple[re.Pattern[str], tuple[str, str] | None]] = [
    (re.compile(patron, re.IGNORECASE), destino) for patron, destino in _PATRONES_DOMINIO
]

# Lo que `_NOMBRE_A_DOMINIO` PUEDE emitir. Lo necesita `scopes()`, y no es cosmético: un ámbito que
# no se declare no cuenta como escaneado en `ingest.py`, así que sus productos **no se
# descatalogarían nunca**. Es el mismo motivo por el que `cacles.py` declara el cartesiano de lo
# que su parser puede emitir en vez de lo que dicen sus hojas.
_DOMINIOS_POR_FAMILIA: list[tuple[str, str]] = sorted(
    {d for _, d in _NOMBRE_A_DOMINIO if d is not None}
)


def categoria_desde_nombre(nombre: str) -> tuple[str, str] | None:
    """`(sección, categoría)` de una prenda por su nombre, o `None` si no se debe ingerir.

    `None` cubre los dos casos en los que la respuesta correcta es no meterla: la prenda está
    **declarada fuera** del brief (bañador, abrigo, accesorio) o **no se reconoce**. Se juntan a
    propósito — con una hoja que mezcla vocabulario, ingerir lo que no sabemos nombrar significa
    colgarle una categoría inventada, y eso ensucia el catálogo en silencio. Pura: sin red.
    """
    for patron, destino in _NOMBRE_A_DOMINIO:
        if patron.search(nombre):
            return destino
    return None


@dataclass(frozen=True)
class TagLeaf:
    """Hoja que solo aporta un eje transversal (`scraper.tags`), sin ingerir nada.

    Ver el porqué completo en `stores/lefties.py::TagLeaf`, que es donde vive el razonamiento: no
    emite entradas, no aparece en `scopes()` —y por tanto no puede provocar bajas— y sus productos
    exclusivos se quedan fuera porque su categoría real no la dice nadie.
    """

    page_id: str
    gender: str  # niño | niña — solo para describirla en `check_leaves()`; no crea ámbito
    tag: str


# «Sportswear», una hoja por género y tramo de edad (#180, #208). Es el mismo hallazgo que en C&A y
# Lefties: casi todo lo que publican **ya entra** por su categoría, o sea que como categoría no
# aportarían casi nada; como eje sí, porque es la tienda quien dice que esa prenda es deportiva.
#
# Medido el 16/08/2026 contra el catálogo real (3768 productos): **224 modelos** entre las cuatro y
# **156 marcados (69,6 %)** — pantalones 79, camisetas 43, vestidos 16, ropa-interior 7,
# sudaderas 8, conjuntos 1, zapatos 2. Los 68 exclusivos se quedan fuera, y mirando qué son se ve
# que es la respuesta correcta y no una pérdida: calcetines de deporte, mallas de danza,
# calentadores, gorras,
# cintas del pelo, medias de fútbol y toda la colección de esquí — accesorio y abrigo, las dos
# ya declaradas fuera del brief en `CATEGORIES`.
#
# **Basta con el padre, y eso hubo que medirlo** porque en Lefties no bastaba (el padre daba 77/69 y
# la unión de sus seis hijas 93/99: 46 prendas perdidas). Aquí no: contra las 11 hijas de
# `/kids/boys/sportswear` el padre da **81 y la unión 81**, cero diferencia, y en las cuatro
# ramas el padre coincide con su propio `view-all`. Son 4 peticiones por pasada en vez de las ~50
# que temía #208.
#
# Y las otras cuatro rutas que #208 enumeraba —`/kids/{boys,girls}[-9-14y]/clothing/sport`— **no son
# hojas**: las cuatro devuelven el cubo de `kids_all` (espejismo). Están en el menú y no resuelven,
# así que siguen declaradas en `vigia.COBERTURA_DECLARADA`, pero con ese motivo y no con el viejo.
HOJAS_ETIQUETA: list[TagLeaf] = [
    TagLeaf("/kids/girls/sportswear", "niña", TAG_DEPORTIVA),  # 114 modelos
    TagLeaf("/kids/boys/sportswear", "niño", TAG_DEPORTIVA),  # 81
    TagLeaf("/kids/girls-9-14y/sportswear", "niña", TAG_DEPORTIVA),  # 127
    TagLeaf("/kids/boys-9-14y/sportswear", "niño", TAG_DEPORTIVA),  # 103
]


# Las 59 hojas, **preguntadas a la tienda y verificadas una a una con el canario** (02/08/2026), no
# adivinadas. La API no publica el árbol —no hay endpoint de navegación y las 30 facetas de la
# respuesta vienen con `values: []`—, pero la propia página de categoría de `www2` trae embebido el
# menú entero. De ahí salen éstas, y desde #179 eso ya no es un apaño de reconocimiento: lo hace
# `category_tree()`, así que para repetirlo basta
#
#   python -m scraper.run --retailer hm --tree /kids
#
# Dos cosas que ese árbol destapó y que ninguna ruta adivinada habría dado:
#
#   - **El rango 9-14 años es una rama aparte** (`/kids/boys-9-14y/…`), así que quedarse con
#     `/kids/boys/…` habría dejado fuera la mitad del catálogo por edad. Es el mismo agujero que la
#     #72 en Sfera.
#   - **Bebé y recién nacido cuelgan de `/baby`, no de `/kids`.**
#
# Las hojas de bebé y de 9-14 se mapean a LAS MISMAS categorías que las de 4-8, igual que hizo
# Hipercor: el vocabulario del catálogo no se parte por rango de edad —no hay eje de edad en el
# esquema ni en las facetas—, el rango se ve por la talla. `recien nacido` va como `unisex` porque
# su rama no separa niño de niña, que es lo mismo que se decidió para `zapatos-infantiles/bebe`.
#
# Fuera a propósito, por no ser del brief: accesorios, baño, disfraces, ropa de deporte, abrigos,
# calcetines, packs, básicos y las ramas transversales de promoción (`last-chance`, `new-arrivals`,
# `seasonal-trending`, `shop-by-product`), que solapan con las de género y duplicarían el trabajo
# para los mismos productos.
#
# **`sets-outfits` entra FILTRADA desde #200, y antes de tocarla hay que leer por qué (#192).**
# Mapear la hoja entera se probó en #192 y una pasada real lo desmintió AQUÍ: de los 20 productos
# que ingirieron las siete hojas el 05/08/2026, **11 eran disfraces y 1 un bikini** —o sea
# `fancy-dress-costumes` y `swimwear`, dos ramas que esta misma lista declara fuera del brief,
# entrando por la puerta de atrás—. Solo ~8 eran conjuntos de verdad. Así que se revirtió.
#
# Lo que cambia en #200 es que ya no hay que elegir entre la hoja entera y nada: `FiltroDeHoja` se
# queda con lo que la tienda rotula «Conjunto de …» (ver `_SOLO_CONJUNTOS`) y el disfraz se cae.
#
# Y el «~8» de arriba era el número de EXCLUSIVOS, no de conjuntos: medido el 06/08/2026, las siete
# hojas traen **495 modelos** que son conjuntos de verdad, y todos menos un puñado ya están en el
# catálogo bajo su prenda dominante. Con las hojas detrás acaban etiquetados `conjuntos` los **7**
# que no entran por ninguna otra, que es lo que esta issue venía a rescatar aquí. De ahí que vayan
# **las últimas** y no las primeras como en Zara y Sfera: las cifras están junto a ellas, al final
# de `CATEGORIES`.
#
# La trampa está en cómo se mide, y es la misma que cayó en Zara (ver su cabecera). Contar los
# productos EXCLUSIVOS de la hoja parece decir «estos no tienen casa natural», y no dice eso:
# también son exclusivos los que tienen una casa que hemos decidido **no ingerir**. En una hoja que
# reagrupa —`sets-outfits` aquí, `TOTAL LOOK` allí— las dos poblaciones se confunden, y el residuo
# no son conjuntos: es todo lo que la tienda archiva ahí y nosotros excluimos por otra vía.
#
def _hojas_de_rama(rama: str, gender: str, *, bebe: bool) -> list[CategoryConfig]:
    """Las hojas del brief dentro de una rama de género, con los nombres que usa cada rango.

    Los slugs de H&M no son uniformes entre ramas: la camiseta de niño es `t-shirts-shirts` y la de
    niña `tops-t-shirts`; la sudadera es `jumpers-sweatshirts` en niños y `jumpers-cardigans` en
    bebé. Se escribe una vez aquí en vez de repetir 59 líneas a mano.
    """
    camisetas = "t-shirts-shirts" if gender == "niño" else "tops-t-shirts"
    sudaderas = "jumpers-cardigans" if bebe else "jumpers-sweatshirts"
    pantalones = "trousers-leggings" if rama.endswith("/newborn") else "trousers"
    hojas = [
        CategoryConfig(f"{rama}/clothing/{pantalones}", gender, "ropa", "pantalones"),
        CategoryConfig(f"{rama}/clothing/shorts", gender, "ropa", "pantalones"),
        CategoryConfig(f"{rama}/clothing/{camisetas}", gender, "ropa", "camisetas"),
        CategoryConfig(f"{rama}/clothing/{sudaderas}", gender, "ropa", "sudaderas"),
        # En bebé la prenda base es el body y no hay hoja de ropa interior, igual que en Hipercor.
        CategoryConfig(
            f"{rama}/clothing/{'bodysuits' if bebe else 'underwear'}",
            gender,
            "ropa",
            "ropa-interior",
        ),
        # El pijama entra en `ropa-interior` como en Hipercor (`pijamas-y-batas`): el brief no tiene
        # slug para él y dejarlo fuera perdería ~700 prendas.
        CategoryConfig(f"{rama}/clothing/nightwear", gender, "ropa", "ropa-interior"),
        CategoryConfig(f"{rama}/shoes", gender, "zapateria", "zapatos"),
    ]
    if gender != "niño":
        # La falda va a `vestidos` por el mismo motivo que en C&A e Hipercor: no hay slug propio en
        # el brief y estrenar uno crearía una categoría que ninguna otra tienda alimenta.
        hojas.append(CategoryConfig(f"{rama}/clothing/dresses", gender, "ropa", "vestidos"))
        if not bebe:
            hojas.append(CategoryConfig(f"{rama}/clothing/skirts", gender, "ropa", "vestidos"))
    if not bebe:
        hojas.insert(1, CategoryConfig(f"{rama}/clothing/jeans", gender, "ropa", "pantalones"))
    return hojas


# Las siete ramas del catálogo infantil, con su género y si son de bebé (que cambia varios slugs,
# ver `_hojas_de_rama`). Se escriben una vez porque desde #200 hay DOS listas que las recorren, y
# dos listas de ramas que hay que mantener a la vez es la forma habitual de que una se quede corta.
_RAMAS: list[tuple[str, str, bool]] = [
    ("/kids/boys", "niño", False),
    ("/kids/boys-9-14y", "niño", False),
    ("/kids/girls", "niña", False),
    ("/kids/girls-9-14y", "niña", False),
    ("/baby/boys", "niño", True),
    ("/baby/girls", "niña", True),
    ("/baby/newborn", "unisex", True),
]

CATEGORIES: list[CategoryConfig] = [
    *(hoja for rama, gender, bebe in _RAMAS for hoja in _hojas_de_rama(rama, gender, bebe=bebe)),
    # --- conjuntos (#200): LAS ÚLTIMAS, y el porqué está MEDIDO ---
    # `_ambito()` resuelve sección y categoría con `hojas[0]` (ver `base.ambito_cruzado`), o sea que
    # ir detrás significa que un conjunto que la tienda publica además bajo una de las cinco del
    # conserva ESA categoría; solo se etiqueta `conjuntos` el que no sale en ninguna otra hoja.
    #
    # Es el mismo criterio que C&A e Hipercor, y lo contrario de lo que hacen Zara y Sfera. La
    # diferencia no es de gusto, es de tamaño, y se midió el 06/08/2026 antes de elegir:
    #
    #   | tienda | hojas delante → conjuntos | cambian de categoría |
    #   | zara   |            84             |          72          |
    #   | sfera  |            28             |           9          |
    #   | hm     |           560             |         555          |
    #
    # En Zara y Sfera la hoja de conjunto es un residuo y adelantarla mueve decenas de prendas. Aquí
    # NO es un residuo: `sets-outfits` es un catálogo paralelo de 495 modelos que la tienda publica
    # además bajo su prenda, así que adelantarla se llevaba **483 modelos de `pantalones`** —de 1418
    # a 936, un tercio de la categoría— y 50 de `camisetas`. Eso ya no es etiquetar mejor un
    # residuo, es vaciar una categoría del brief: quien busque «pantalones de niño» en H&M perdería
    # un tercio de lo que hay. Detrás, la misma pasada deja 7 conjuntos y UN cambio de categoría.
    #
    # El género NO depende del orden en ninguno de los dos casos: sale del conjunto de hojas y no de
    # la primera, así que esto no toca nada de lo que decidió #98.
    *(
        CategoryConfig(
            f"{rama}/clothing/sets-outfits", gender, "ropa", "conjuntos", _SOLO_CONJUNTOS
        )
        for rama, gender, _ in _RAMAS
    ),
    # --- saldo (#468): LAS ÚLTIMAS, mezcladas y estacionales ---
    #
    # `last-chance` («Última oportunidad») es la rama que la tienda dedica a lo rebajado, y era la
    # única que no mirábamos en un producto que existe para avisar de bajadas de precio. Medido el
    # 18/08/2026 contra las 70 hojas de aquí arriba (7835 filas / 3901 modelos):
    # **1181 filas / 885 modelos, de los que 429 (48,5 %) ya entraban y 456 no.** De esos 456, la
    # tabla de `_NOMBRE_A_DOMINIO` clasifica 305 y descarta 151 por no ser del brief.
    #
    # **El solape hay que mirarlo por MODELO, y esto es lo que casi cuela una cifra falsa**: por id
    # de artículo el solape daba **0 sobre 1181 filas** —parecía una rama entera de estreno— y no
    # era cierto. El mismo modelo entra por otro color, y `product` es el modelo, no el artículo.
    #
    # **Van las hijas de GÉNERO y no las de edad**, al revés que en Zara. Aquí el padre no miente:
    # padre contra la unión de sus hijas da 622/623, 251/255 y 349/346, o sea que las de edad
    # servirían igual de bien. Lo que las de género añaden es el `gender`, que en una hoja
    # `por_familia` es lo único que sigue decidiendo la hoja. Se pagan 7 filas que solo viven en
    # los padres, y a cambio 114 filas salen `unisex` por el cruce que esta tienda ya sabe hacer
    # (niño∩niña: 19 + 9 + 86).
    #
    # `newborn` va `unisex` porque la tienda no separa género ahí, igual que las hojas de bebé.
    #
    # **Y la rama HERMANA se queda fuera a propósito, que es el hallazgo caro de esta medición.**
    # `/kids/local-occasion/*` («HASTA -40%») responde 1859 filas y **no es catálogo infantil**:
    # entre lo que no teníamos hay alfombras de baño, anillas para cortina de ducha, cestas de
    # almacenaje, bandejas de mármol, americanas y cubrepezones. Su `pageId` resuelve y **pisa el
    # `categoryId=kids_all`**, así que **el canario no la caza** — no devuelve el cubo, devuelve
    # otra cosa. Es una quinta forma de mentir de una hoja, distinta de las cuatro del punto (1) de
    # la cabecera, y la única defensa contra ella es mirar lo que trae. Sus seis hijas por género
    # son espejismo o vacías las seis.
    *(
        CategoryConfig(page_id, gender, "", "", por_familia=True, estacional=True)
        for page_id, gender in (
            ("/kids/last-chance/girls-2-8y", "niña"),  # 414 filas
            ("/kids/last-chance/boys-2-8y", "niño"),  # 229
            ("/kids/last-chance/girls-9-14y", "niña"),  # 165
            ("/kids/last-chance/boys-9-14y", "niño"),  # 100
            ("/baby/last-chance/baby/girls", "niña"),  # 247
            ("/baby/last-chance/baby/boys", "niño"),  # 181
            ("/baby/last-chance/newborn", "unisex"),  # 102
        )
    ),
]


class HojaEspejismo(Exception):
    """La hoja no resuelve: la tienda ha devuelto el cubo de `categoryId` en su lugar."""


def _decimal(value: Any) -> Decimal | None:
    """H&M da los precios como número JSON (`14.99`), no en céntimos.

    Se pasa por `str()` antes del `Decimal` a propósito: `Decimal(14.99)` arrastraría la basura
    binaria del float.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def raiz(article_id: str) -> str:
    """El modelo dentro del id de artículo: `'1343222003'` -> `'1343222'`.

    Recorta y no interpreta: si algún día un id viniera más corto, devolverlo entero es mejor que
    inventarse un identificador que agruparía productos distintos.
    """
    return article_id[:_LONGITUD_RAIZ]


# `122/128 (6-8Y)`, `74 (6-9M)`, `92 (1½-2Y)`, `170 (14Y+)`, `44 (<0-1M)`: centímetros delante y la
# edad en el paréntesis, con `<` y `+` como adornos de los extremos abiertos.
_RE_TALLA = re.compile(
    r"^\s*(?P<cm>\d+(?:/\d+)?)\s*\(\s*<?\s*(?P<edad>\d+(?:[½.]\d*)?(?:\s*-\s*\d+(?:[½.]\d*)?)?)"
    r"\s*(?P<unidad>[YM])\s*\+?\s*\)\s*$"
)
_UNIDADES = {"Y": "años", "M": "meses"}


def _talla(label: str | None) -> str | None:
    """Le da la vuelta a la etiqueta de H&M para que `size_canon` la entienda.

        '122/128 (6-8Y)' -> '6-8 años (122/128 cm)'   -> canónica '6-8 años'
        '74 (6-9M)'      -> '6-9 meses (74 cm)'       -> canónica '6-9 meses'
        '92 (1½-2Y)'     -> '1½-2 años (92 cm)'       -> canónica '1.5-2 años'

    El `½` se conserva tal cual: `size_canon` ya lo traduce a `.5` (lo usa el rango mini de Zara) y
    respetarlo distingue 1½ años (86 cm) de 2 años (92 cm), que son tallas distintas.

    Lo que no encaja se devuelve **crudo**, que es lo correcto para las tallas de calzado
    (`'24/25'`, `'31'`), donde el número ya es el número de pie y `size_canon` lo lee bien.

    LÍMITE DECLARADO, medido: la regla de `size_canon` que separa número de pie de edad exige que
    los dos extremos del rango sean >= 15 (ver `0017_size_canon_rango_de_pie.sql`), así que las
    tallas de calzado de bebé `'12/13'` y `'14/15'` salen como `'12-13 años'` y `'14-15 años'`. Son
    **12 variantes de las 2712 de zapatería**, y arreglarlo no es cosa de esta tienda: por debajo de
    15 las dos lecturas son plausibles desde el texto y la que decide es la sección, que la función
    de la base no conoce.

    Se asume a propósito (#103), y lo que lo sostiene es que H&M sea la ÚNICA tienda con este
    patrón: medido sobre las siete ingeridas, ninguna otra aporta un solo caso — los patucos de
    Hipercor son de talla única y los de Cacles y Lefties se tallan con número suelto, no con rango.
    El día que eso deje de ser cierto, la opción de pasarle la sección a `size_canon` vuelve a estar
    sobre la mesa. Esta consulta es la señal barata que lo detecta; si devuelve algo que no sea
    `hm`, reabrir la decisión:

        select r.slug, p.section, v.size, size_canon(v.size), count(*)
          from variant v
          join product p on p.id = v.product_id
          join retailer r on r.id = p.retailer_id
         where p.section = 'zapateria' and v.delisted_at is null
           and v.size ~ '^[0-9]+\\s*[-/]\\s*[0-9]+$'
           and (regexp_match(v.size, '^([0-9]+)'))[1]::int < 15
         group by 1,2,3,4 order by 5 desc;
    """
    if label is None:
        return None
    m = _RE_TALLA.match(label)
    if not m:
        return label.strip() or None
    edad = re.sub(r"\s*-\s*", "-", m.group("edad"))
    return f"{edad} {_UNIDADES[m.group('unidad')]} ({m.group('cm')} cm)"


def _precios(raw: Mapping[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    """`(precio, tachado)` a partir de `prices[]`.

    Un producto rebajado trae DOS entradas: `redPrice` es lo que se paga y `whitePrice` el tachado.
    Uno sin rebaja trae solo `whitePrice`. Se decide por el tipo y no por «el menor de los dos», que
    sería adivinar.

    En infantil no hay ni un rebajado, y ya no es cuestión de muestra: **0 de 118 197 filas en 30
    días** (#106, cuarta medida, dos entornos). O sea que este camino no lo ha ejercido nunca un
    dato real de esta sección. Lo que sí está comprobado contra la sección de adulto, que rebaja, es
    que la forma es exactamente esta: los únicos `priceType` que sirve la API son `whitePrice` y
    `redPrice`, sin un tercero. Cuidado con `minPrice`/`maxPrice`, que NO son ningún mínimo
    histórico: son el rango dentro de la propia entrada y vienen iguales al `price`.

    El tachado solo cuenta si es estrictamente MAYOR, la misma guarda que en Cacles (donde venía
    igual al precio en 248 de 428 productos) y en C&A.
    """
    por_tipo: dict[str, Decimal] = {}
    for p in raw.get("prices") or []:
        if not isinstance(p, dict):
            continue
        valor = _decimal(p.get("price"))
        tipo = p.get("priceType")
        if valor is not None and isinstance(tipo, str):
            por_tipo.setdefault(tipo, valor)
    blanco = por_tipo.get("whitePrice")
    rojo = por_tipo.get("redPrice")
    if rojo is None:
        return blanco, None
    tachado = blanco if blanco is not None and blanco > rojo else None
    return rojo, tachado


@dataclass(frozen=True)
class Fila:
    """Una fila del listado: un producto **en un color concreto**, con sus tallas.

    No es un producto nuestro: `ScrapedProduct` agrupa todas las filas que comparten `raiz`.
    """

    article_id: str
    raiz: str
    name: str
    color: str | None
    url: str | None
    price: Decimal
    list_price: Decimal | None
    in_stock_global: bool
    images: tuple[str, ...]
    tallas: tuple[tuple[str, str | None, bool], ...]  # (id de talla, etiqueta, con stock)


def parse_filas(payload: Any) -> list[Fila]:
    """Convierte una página del listado en filas. Puro: sin red.

    Descarta lo que no se puede seguir —sin id, sin precio o sin tallas—, y lo hace aquí para que
    quien decide el fin de la paginación cuente los productos **crudos** y no éstos.
    """
    lista = _plp(payload)
    filas: list[Fila] = []
    for raw in lista.get("productList") or []:
        if not isinstance(raw, dict):
            continue
        article = raw.get("id")
        if not isinstance(article, str) or not article:
            continue
        precio, tachado = _precios(raw)
        if precio is None:
            continue  # sin precio no hay nada que vigilar
        tallas = tuple(
            (str(t.get("id")), _talla(t.get("label")), t.get("stock") != _SIN_STOCK)
            for t in raw.get("sizes") or []
            if isinstance(t, dict) and t.get("id") is not None
        )
        if not tallas:
            continue
        url = raw.get("url")
        imagenes = tuple(
            img["url"]
            for img in raw.get("images") or []
            if isinstance(img, dict) and isinstance(img.get("url"), str)
        )
        estado = (raw.get("availability") or {}).get("stockState")
        filas.append(
            Fila(
                article_id=article,
                raiz=raiz(article),
                name=str(raw.get("productName") or ""),
                color=(str(raw.get("colorName")).strip() or None) if raw.get("colorName") else None,
                url=f"{BASE_URL}{url}" if isinstance(url, str) and url else None,
                price=precio,
                list_price=tachado,
                in_stock_global=estado != "OutOfStock",
                images=imagenes or ((raw["productImage"],) if raw.get("productImage") else ()),
                tallas=tallas,
            )
        )
    return filas


def _plp(payload: Any) -> Mapping[str, Any]:
    """Saca `plpList` de la respuesta, o eleva diciendo qué ha llegado."""
    if not isinstance(payload, dict):
        raise ValueError(f"{SLUG}: respuesta que no es un objeto JSON")
    lista = payload.get("plpList")
    if not isinstance(lista, dict):
        raise ValueError(f"{SLUG}: la respuesta no trae plpList")
    return lista


def ids_de_pagina(payload: Any) -> list[str]:
    """Los ids de artículo de una página, en orden. Puro: es lo que compara `es_espejismo()`."""
    return [
        raw["id"]
        for raw in _plp(payload).get("productList") or []
        if isinstance(raw, dict) and isinstance(raw.get("id"), str)
    ]


def es_espejismo(ids_hoja: Sequence[str], ids_canario: Sequence[str]) -> bool:
    """`True` si la hoja ha devuelto el cubo en vez de su catálogo. Puro: sin red.

    Compara los ids de la primera página contra los del canario. Ver el punto (1) de la cabecera:
    NO se compara `numberOfHits`, que deriva entre peticiones y declararía viva una hoja muerta.

    Una página vacía no prueba nada aquí y devuelve `False`: quien llama ya la trata aparte, igual
    que hacen `sfera.py` y `cacles.py`.
    """
    if not ids_hoja or not ids_canario:
        return False
    comunes = len(set(ids_hoja) & set(ids_canario))
    return comunes / len(set(ids_hoja)) >= _SOLAPE_ESPEJISMO


def parse_category_tree(html: str, root: str) -> list[CategoryNode]:
    """El árbol que publica el menú del escaparate, por debajo de `root`. Pura (HTML -> nodos).

    **El menú no es un endpoint: viaja embebido en cualquier página de categoría de `www2`.** Por
    eso el árbol entero cuesta UNA petición, y por eso hay que leerlo con navegador (Akamai), que
    es la única cosa de esta tienda que no entra por `api.hm.com`.

    Se recogen los pares `"title"`+`"path"` del propio menú y **no todas las rutas que aparezcan en
    el documento**, que es lo que proponía la receta anotada en `CATEGORIES` desde #77
    (`re.findall(r"/(?:kids|baby)/[a-z0-9\\-/]+", html)`). Medido el 05/08/2026, la diferencia no
    es cosmética: el regex suelto saca **690 rutas y el menú publica 651**, porque también recoge
    el `<meta name="contentPath">` de la propia página y los `praData`, que nombran las mismas
    categorías con OTRO vocabulario (`1/kids/kids_girls/kids_girls_clothing`). Treinta y nueve
    rutas que no existen como categoría se habrían señalado como hueco cada jueves.

    La ruta es `path` sin el prefijo de idioma ni el `.html`, o sea **el mismo vocabulario que
    `CategoryConfig.page_id`** — por eso `mapped_leaves()` no tiene que resolver nada, al revés que
    en Zara. Y se lee de `path` y no de `targetPath`, que es a dónde apunta la entrada y no lo que
    la entrada es: `/kids/boys/clothing/jackets-coats` tiene `targetPath` a
    `/kids/boys/outerwear/view-all`, así que por ahí una hoja se leería con el nombre de otra.

    `count=None` en todos: el menú publica navegación, no cuántos productos hay detrás. `None` es
    «no lo dice», que no es 0 (ver `CategoryNode`).
    """
    vistas: dict[str, str] = {}
    for m in _RE_MENU.finditer(html):
        ruta = m.group("path")
        # Se queda la PRIMERA: la misma categoría sale varias veces en el menú (740 entradas para
        # 651 rutas) porque cuelga de más de una vista, y todas traen el mismo rótulo.
        vistas.setdefault(ruta, _texto_json(m.group("title")))

    prefijo = root + "/"
    con_hijas = {r.rsplit("/", 1)[0] for r in vistas}
    return [
        CategoryNode(
            path=ruta,
            title=vistas[ruta],
            count=None,
            depth=ruta.count("/") - root.count("/"),
            has_children=ruta in con_hijas,
        )
        for ruta in sorted(vistas)
        if ruta.startswith(prefijo)  # solo descendientes: la raíz no se emite a sí misma
    ]


def _texto_json(crudo: str) -> str:
    """Deshace los escapes de la cadena JSON del menú (`H\\u0026M Adorables` -> `H&M Adorables`)."""
    try:
        return str(json.loads(f'"{crudo}"'))
    except ValueError:
        return crudo  # un rótulo ilegible no justifica perder el nodo


def _variantes(filas: Iterable[Fila]) -> list[ScrapedVariant]:
    """Una `ScrapedVariant` por (color × talla) de todas las filas del mismo modelo.

    En H&M el precio cuelga del COLOR —cada fila trae el suyo—, así que se replica en cada talla de
    esa fila. Es lo mismo que hace C&A y por el mismo motivo: nuestro modelo sigue el precio por
    talla, y una tienda que hoy no diferencie puede diferenciar mañana.
    """
    variantes: list[ScrapedVariant] = []
    for fila in filas:
        for talla_id, etiqueta, con_stock in fila.tallas:
            variantes.append(
                ScrapedVariant(
                    # `{articleId}-{sizeId}`: el articleId ya lleva el color, así que el par
                    # identifica la variante sin depender del nombre del color, que es texto.
                    retailer_variant_id=f"{fila.article_id}-{talla_id}",
                    size=etiqueta,
                    color=fila.color,
                    sku=f"{fila.article_id}-{talla_id}",
                    price=fila.price,
                    list_price=fila.list_price,
                    in_stock=con_stock and fila.in_stock_global,
                    url=fila.url,
                    # H&M no publica el mínimo de 30 días de la directiva Ómnibus (hoy solo C&A).
                    retailer_min_30d=None,
                )
            )
    return variantes


def _imagenes(filas: Iterable[Fila], colores_con_variantes: set[str | None]) -> list[ScrapedImage]:
    """Galería, atribuyendo cada foto al color que retrata Y al artículo del que sale.

    El color sale del MISMO campo que alimenta `ScrapedVariant.color` (`colorName`), que es lo que
    pide `base.ScrapedImage`. Un color sin variantes utilizables no aporta fotos.

    Y aquí el color NO basta para atribuir la foto, que es lo que descubrió la #123: esta tienda
    publica a veces dos artículos del mismo modelo con el mismo `colorName` —prendas distintas,
    cada una con su ficha— y un producto nuestro los junta porque agrupa por `raiz`. Medido en
    `dev` el 03/08/2026: **803 grupos así, en 105 productos**; el producto 6323 («Pantalón en
    mezcla de lino») tenía 7 fotos bajo «Azul marino», las de los artículos 1315153003 y
    1315153005 revueltas. Por eso va también `variant_url`, la misma URL de fila que `_variantes`
    pone en `ScrapedVariant.url`: es lo que deja a la ficha separar las dos galerías.
    """
    imagenes: list[ScrapedImage] = []
    for fila in filas:
        if fila.color not in colores_con_variantes:
            continue
        for url in fila.images:
            imagenes.append(ScrapedImage(color=fila.color, url=url, variant_url=fila.url))
    return imagenes


def producto(filas: Sequence[Fila], scope: ScrapeScope) -> ScrapedProduct | None:
    """Junta las filas de un mismo modelo en un producto. Puro: sin red.

    `filas` va en el orden en que las trajo el listado, así que el nombre, la URL y la foto primaria
    son las del primer color visto — que es el que la tienda enseña primero.
    """
    variantes = _variantes(filas)
    if not variantes:
        return None
    colores = {v.color for v in variantes}
    imagenes = _imagenes(filas, colores)
    primera = filas[0]
    return ScrapedProduct(
        retailer_product_id=primera.raiz,
        name=primera.name,
        gender=scope.gender,
        section=scope.section,
        category=scope.category,
        url=primera.url,
        variants=variantes,
        barefoot=classify_barefoot(
            retailer=SLUG,
            retailer_product_id=primera.raiz,
            section=scope.section,
            category=scope.category,
            # Solo hay el nombre: el listado no trae descripción ni composición. Basta para lo que
            # H&M sí nombra — «Zapatillas de casa barefoot» está en el catálogo de hoy.
            texts=[f.name for f in filas],
        ),
        image_url=imagenes[0].url if imagenes else None,
        images=imagenes,
    )


def product_signature(product: ScrapedProduct) -> str:
    """Huella del producto: precio y stock por variante, ordenados y estables.

    Entra el stock además del precio porque aquí el listado ya es el detalle: no hay una segunda
    petición que fuese a recogerlo, así que sin el stock en la huella un cambio de disponibilidad no
    se llegaría a ingerir nunca. Es el mismo razonamiento que en Cacles y C&A.
    """
    partes = [f"{v.retailer_variant_id}:{v.price}:{int(v.in_stock)}" for v in product.variants]
    return "|".join(sorted(partes))


class HMStore:
    """Scraper de H&M. Implementa el Protocol BaseStore."""

    slug = SLUG
    name = "H&M"
    base_url = BASE_URL

    def __init__(
        self,
        config: Config,
        categories: list[CategoryConfig] | None = None,
        tag_leaves: list[TagLeaf] | None = None,
    ) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        self._tag_leaves = tag_leaves if tag_leaves is not None else HOJAS_ETIQUETA
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._tags = ProductTags()  # ídem; ver `product_tags()`
        self._cache: dict[str, ScrapedProduct] = {}
        self._canario: list[str] | None = None
        self._menu: str | None = None  # el escaparate del que sale el árbol; ver `_menu_html()`

    # --- red ---------------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        """`api.hm.com` entra sin una sola cabecera; se manda UA igualmente, por educación."""
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

    def _params(self, page_id: str, page: int) -> dict[str, Any]:
        return {
            "pageSource": "PLP",
            "page": page,
            "sort": "RELEVANCE",
            "pageId": page_id,
            "page-size": _PAGE_SIZE,
            "categoryId": _CATEGORY_ID,
            "touchPoint": "DESKTOP",
            "skipStockCheck": "false",
        }

    def _get_json(self, client: httpx.Client, page_id: str, page: int) -> Any:
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = client.get(_API_URL, params=self._params(page_id, page))
                resp.raise_for_status()
                return resp.json()
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

    def _ids_canario(self, client: httpx.Client) -> list[str]:
        """Los ids del cubo, pedidos **una vez por pasada** y cacheados.

        Si esto falla, falla la pasada: sin canario no hay forma de distinguir una hoja muerta de
        una viva, y seguir sería ingerir el cubo entero repartido por 59 categorías.
        """
        if self._canario is None:
            self._canario = ids_de_pagina(self._get_json(client, _CANARIO_PAGE_ID, _PAGINA_INICIAL))
            logger.info("%s: canario con %d ids de referencia", SLUG, len(self._canario))
        return self._canario

    # --- contrato ----------------------------------------------------------------------------

    def scopes(self) -> Iterable[ScrapeScope]:
        """Los ámbitos declarados **más su equivalente `unisex`**.

        Lo segundo no es cosmético: el 9,3 % de los modelos sale en hojas de los dos géneros y se
        emite como `unisex` (ver el punto 3 de la cabecera), así que un ámbito `unisex` que no se
        declarase aquí no se contaría como escaneado y sus productos no se podrían descatalogar
        nunca. Es el mismo motivo por el que `cacles.py` declara el producto cartesiano de lo que su
        parser PUEDE emitir en vez de lo que dicen sus hojas.

        **Y las hojas de saldo no tienen ámbito propio, sino todos los que su tabla puede emitir**
        (#468): su `section` y su `category` van vacías, así que declararlas tal cual metería un
        ámbito `('niña', '', '')` que no existe y —peor— dejaría sin declarar los que sí emiten.
        Es el mismo cartesiano que hace `lefties.py` con sus hojas `por_familia`.
        """
        declarados = [
            ScrapeScope(c.gender, c.section, c.category)
            for c in self._categories
            if not c.por_familia
        ]
        declarados += [
            ScrapeScope(c.gender, section, category)
            for c in self._categories
            if c.por_familia
            for section, category in _DOMINIOS_POR_FAMILIA
        ]
        return con_unisex(dict.fromkeys(declarados))

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Recorre las hojas y emite un producto por modelo.

        **Acumula la pasada entera antes de emitir**, a diferencia de las demás tiendas. Hacen falta
        las dos cosas y ninguna se puede decidir con una página delante: agrupar los colores de un
        modelo, que se reparten entre páginas y hojas, y ver si un modelo sale en hojas de géneros
        distintos, que es lo que lo hace `unisex`. Son 6518 filas: la memoria da igual, y el ámbito
        de una entrada ya emitida no se puede corregir.

        Ese acumular deja **ciega** a la instrumentación de `ingest.py` (#146): late al recibir
        entradas y aquí no recibe ninguna hasta el final, así que la fase 1 salía muda. Por eso el
        latido por hoja va aquí dentro — es el único sitio donde se sabe por qué hoja va. Esta
        tienda es la más barata de las dos que acumulan (httpx contra `api.hm.com`, no un
        navegador), pero es también la del catálogo más grande del proyecto.
        """
        self._scan = ScanReport()
        self._tags = ProductTags()
        self._cache = {}
        self._canario = None
        filas_por_raiz: dict[str, list[Fila]] = {}
        hojas_por_raiz: dict[str, list[CategoryConfig]] = {}
        latido = Latido(self._config.progress_every_seconds, SLUG, logger)
        total_hojas = len(self._categories)

        with self._client() as client:
            self._lee_hojas_etiqueta(client)
            for n_hoja, cat in enumerate(self._categories, start=1):
                # Al ENTRAR en la hoja y no al salir: si la pasada se atasca, lo que hace falta
                # saber es en cuál se ha quedado, no cuál fue la última que terminó bien.
                latido.late(
                    f"listando · hoja {n_hoja}/{total_hojas} {cat.page_id} · "
                    f"{sum(len(f) for f in filas_por_raiz.values())} filas"
                )
                filas = self._leer_hoja(client, cat)
                if filas is None:
                    continue  # hoja caída: ya está contada y su ámbito fuera de las bajas
                for fila in filas:
                    filas_por_raiz.setdefault(fila.raiz, [])
                    hojas_por_raiz.setdefault(fila.raiz, [])
                    if fila.article_id not in {f.article_id for f in filas_por_raiz[fila.raiz]}:
                        filas_por_raiz[fila.raiz].append(fila)
                    if cat not in hojas_por_raiz[fila.raiz]:
                        hojas_por_raiz[fila.raiz].append(cat)

        for raiz_modelo, filas in filas_por_raiz.items():
            scope = _ambito(hojas_por_raiz[raiz_modelo], filas)
            if scope is None:
                continue  # solo vive en la rama de saldo y su nombre no dice qué es (#468)
            prod = producto(filas, scope)
            if prod is None:
                continue
            self._cache[raiz_modelo] = prod
            yield ListingEntry(
                retailer_product_id=raiz_modelo,
                signature=product_signature(prod),
                gender=scope.gender,
                section=scope.section,
                category=scope.category,
                image_url=prod.image_url,  # ver #443 en `ListingEntry.image_url`
            )

    def _leer_hoja(self, client: httpx.Client, cat: CategoryConfig) -> list[Fila] | None:
        """Todas las páginas de una hoja, o `None` si la hoja está caída.

        Devuelve la lista completa en vez de un generador porque quien llama necesita la pasada
        entera antes de emitir nada (ver `list_catalog`).
        """
        scope = ScrapeScope(cat.gender, cat.section, cat.category)
        acumuladas: list[Fila] = []
        truncada = True  # solo deja de serlo al ver el final real de la paginación
        for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
            payload = self._get_json(client, cat.page_id, page)
            ids = ids_de_pagina(payload)
            if page == _PAGINA_INICIAL:
                if es_espejismo(ids, self._ids_canario(client)):
                    # LA PARTE IMPORTANTE DE ESTE FICHERO. La hoja no resuelve y la tienda ha
                    # devuelto el cubo: ingerirlo metería miles de productos de otras categorías
                    # bajo este ámbito, y con 200 en todas las peticiones.
                    self._hoja_comprometida(
                        scope,
                        cat.page_id,
                        f"la hoja {cat.page_id!r} devolvió el cubo de {_CATEGORY_ID!r} "
                        "(espejismo), así que se trata como retirada",
                        estacional=cat.estacional,
                    )
                    return None
                if not ids:
                    self._hoja_comprometida(
                        scope,
                        cat.page_id,
                        f"la hoja {cat.page_id!r} devolvió 0 productos en su primera página, "
                        "así que se trata como retirada",
                        estacional=cat.estacional,
                    )
                    return None
            elif not ids:
                truncada = False
                break
            acumuladas.extend(parse_filas(payload))
            if len(ids) < _PAGE_SIZE or not (payload.get("pagination") or {}).get("nextPageNum"):
                # Una página incompleta ES la última, y `nextPageNum` desaparece al llegar al
                # final: con cualquiera de las dos señales sobra la petición que solo servía para
                # que la tienda respondiera vacío.
                truncada = False
                break
        if truncada:
            # Se agotó el tope de páginas sin llegar al final: se ha visto SOLO una parte de la
            # hoja. Contarla como sana sería el peor de los dos errores — lo que no se ha llegado a
            # mirar no está retirado, y a las `delist_min_misses` pasadas se descatalogaría solo por
            # no haber cabido en el tope.
            #
            # **Y aquí NO se pasa `estacional`, al revés que en las dos ramas de arriba.** No es un
            # olvido: truncarse no es apagarse. Una hoja de campaña que se apaga no compromete nada
            # porque lo suyo ya se listó por otras hojas o dejó de existir; una que se trunca ha
            # dejado productos sin mirar, y **exentarla sería dejar su ámbito sin la protección de
            # `leaf_gone()`** — o sea, cambiar una subida de `dead_ratio` por bajas falsas, que es
            # el peor de los dos cambios. El coste asumido es que un `last-chance` que un día pase
            # de `_MAX_PAGES` cuente como hoja caída; hoy la mayor tiene 470 productos sobre un tope
            # de 2160, así que hay margen de sobra antes de que eso importe.
            self._hoja_comprometida(
                scope,
                cat.page_id,
                f"{cat.page_id!r} agotó el tope de {_MAX_PAGES} páginas sin llegar al final, "
                "así que el catálogo leído está incompleto",
            )
            return None
        self._scan.leaf_ok()
        if cat.filtro is not None:
            # Se filtra AQUÍ y no antes a propósito: el final de la paginación y el canario se
            # deciden con los productos crudos de cada página (`ids_de_pagina`), y colarles un
            # conteo ya filtrado convertiría «esta hoja trae pocos conjuntos» en «esta hoja se ha
            # acabado». El filtro solo decide qué se ingiere, nunca cuánto se pide.
            acumuladas = [f for f in acumuladas if cat.filtro.acepta(f.name)]
            if not acumuladas:
                # La hoja respondió y no ha casado ni una: o la tienda ha cambiado la rotulación o
                # ya no le quedan, y no se distingue. Ver `ScanReport.filtro_vacio()`.
                self._scan.filtro_vacio(scope, cat.page_id)
        return acumuladas

    def _lee_hojas_etiqueta(self, client: httpx.Client) -> None:
        """Recorre las hojas que solo aportan eje transversal (ver `TagLeaf`).

        No usa `_leer_hoja()` a propósito, aunque la paginación sea la misma: aquella cuenta la hoja
        en `_scan` y saca su ámbito de las bajas, y estas **no tienen ámbito**, así que no pueden
        dejar a nadie sin bajas ni contar para el `dead_ratio`.

        Lo que sí comparten es el canario, que aquí es la señal de hoja muerta: en esta tienda una
        ruta que no resuelve devuelve **200 con el cubo entero** (~9700 productos). Sin cotejarlo,
        la rama de deporte parecería enorme y marcaría `deportiva` en media tienda.

        Y una hoja que falla retira su eje de `fiables`, que es lo que impide el fallo silencioso:
        la reconciliación de la ingesta BORRA las etiquetas que la tienda ya no declara, así que una
        rama caída se leería como «H&M ya no publica nada deportivo» y se llevaría por delante las
        marcas de los 156 productos. Ver `ProductTags`.
        """
        self._tags.fiables = {hoja.tag for hoja in self._tag_leaves}
        for hoja in self._tag_leaves:
            try:
                marcados = 0
                for page in range(_PAGINA_INICIAL, _PAGINA_INICIAL + _MAX_PAGES):
                    payload = self._get_json(client, hoja.page_id, page)
                    ids = ids_de_pagina(payload)
                    if page == _PAGINA_INICIAL and es_espejismo(ids, self._ids_canario(client)):
                        logger.warning(
                            "%s: la hoja de etiqueta %s devolvió el cubo de %r (espejismo); "
                            "`%s` no se reconcilia",
                            SLUG,
                            hoja.page_id,
                            _CATEGORY_ID,
                            hoja.tag,
                        )
                        self._tags.fiables.discard(hoja.tag)
                        break
                    if not ids:
                        break
                    for fila in parse_filas(payload):
                        self._tags.anota(fila.raiz, hoja.tag)
                        marcados += 1
                    if len(ids) < _PAGE_SIZE or not (payload.get("pagination") or {}).get(
                        "nextPageNum"
                    ):
                        break
                else:
                    # Se agotó el tope sin ver el final: se ha leído solo una parte de la rama, así
                    # que reconciliar borraría las marcas de lo que no ha dado tiempo a mirar.
                    logger.warning(
                        "%s: la hoja de etiqueta %s agotó el tope de %d páginas; `%s` no se "
                        "reconcilia",
                        SLUG,
                        hoja.page_id,
                        _MAX_PAGES,
                        hoja.tag,
                    )
                    self._tags.fiables.discard(hoja.tag)
            except Exception as exc:  # noqa: BLE001 — misma red ancha que en el listado
                logger.warning(
                    "%s: hoja de etiqueta %s ilegible (%s: %s); `%s` no se reconcilia",
                    SLUG,
                    hoja.page_id,
                    type(exc).__name__,
                    exc,
                    hoja.tag,
                )
                self._tags.fiables.discard(hoja.tag)

    def product_tags(self) -> ProductTags:
        """Ver `stores.base.SupportsProductTags` (válido con `list_catalog()` ya consumido)."""
        return self._tags

    def _hoja_comprometida(
        self, scope: ScrapeScope, leaf: str, motivo: str, *, estacional: bool = False
    ) -> None:
        """Cuenta la hoja como caída y saca su ámbito —y el `unisex` equivalente— de las bajas.

        El porqué de lo segundo está en `ScanReport.leaf_gone()`.

        **Una hoja estacional apagada no compromete nada** (#195, y aquí #468). Contarla como
        caída haría dos daños, los dos al acabar cada campaña: subiría `dead_ratio` hacia el tope
        que aborta la pasada —10 hojas de saldo sobre 70 es el 14 %— y sacaría de las bajas unos
        ámbitos que se han listado perfectamente por sus hojas de siempre. Lo que solo vivía en la
        rama de saldo tampoco se descataloga de golpe: esta tienda **no tiene `probe_alive`** (es
        una decisión medida, punto 5 de la cabecera), así que se va por `delist_min_misses` como
        cualquier otro producto que deja de listarse.
        """
        if estacional:
            logger.info("%s: %s; es hoja de campaña, así que no cuenta como caída", SLUG, motivo)
            return
        self._scan.leaf_gone(scope, leaf, tambien_unisex=True)
        logger.warning("%s: %s; se omiten las bajas de ese ámbito", SLUG, motivo)

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        # El listado ya trajo el detalle completo (colores, tallas, precios y fotos vienen en el
        # mismo JSON), así que se sirve desde caché sin una sola petición extra.
        for entry in entries:
            prod = self._cache.get(entry.retailer_product_id)
            if prod is not None:
                yield prod

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    # --- capacidades opcionales --------------------------------------------------------------

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Ver `stores.base.SupportsCategoryTree`. Una petición para el árbol entero (#179).

        Es lo único de esta tienda que necesita navegador: el menú vive en el escaparate, que es
        Akamai, y no en `api.hm.com`. **No participa en la pasada**, así que el CronJob de H&M
        sigue siendo httpx pelado; quien paga el Chromium es el vigía del jueves, que ya lo lleva
        en la imagen por Sfera, Lefties e Hipercor.

        Se navega (`get_html`) en vez de pedir el documento servido (`pedir_html`), y es una
        decisión medida el 05/08/2026, no el descuido que parece después de #160: `pedir_html()` en
        frío da **403** —Akamai quiere las cookies que siembra una navegación de verdad— y con la
        sesión ya sembrada devuelve el mismo menú, las mismas 651 rutas, en 0,96 s frente a 2,10 s.
        O sea que serviría, pero **exige una navegación previa igualmente**: para UNA página al mes
        salen dos peticiones donde `get_html()` hace una. El día que haya que leer varias páginas
        del escaparate, el cálculo cambia y esto pasa a ser sembrar + `pedir_html()`.
        """
        return parse_category_tree(self._menu_html(), root)

    def _menu_html(self) -> str:
        """El escaparate del que sale el árbol, cacheado por instancia.

        `--tree` sobre `/kids` y `/baby` y el barrido del vigía sobre las siete ramas piden el
        árbol varias veces, y el menú es el mismo en todas: sin caché serían siete navegaciones
        para el mismo dato.
        """
        if self._menu is None:
            with BrowserSession(self._config) as session:
                status, html = session.get_html(_MENU_URL)
            if status != 200:
                raise ValueError(f"{SLUG}: el escaparate respondió HTTP {status} al pedir el menú")
            self._menu = html
        return self._menu

    def mapped_leaves(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCategoryTree`. Los `page_id` de `CATEGORIES`, tal cual.

        Sin resolver nada contra el árbol, al revés que en Zara: allí `CATEGORIES` guarda el id
        suelto y el vocabulario de esta capa es la cadena de ids, mientras que aquí `page_id` **ya
        es** la ruta que publica el menú. Comprobado el 05/08/2026: las 59 salen en el menú.

        Una hoja que el menú dejara de publicar se sigue emitiendo tal cual, y es a propósito: lo
        que hay que decir entonces es que la hoja ha muerto, y eso lo dice `check_leaves()` con su
        canario y con el veredicto que corresponde. Omitirla aquí solo conseguiría que además
        apareciese como hueco de cobertura, que es el mismo hecho contado dos veces y peor.

        Las hojas de etiqueta cuentan como mapeadas aunque no ingieran nada (#208): la pregunta de
        esta capa es «¿qué publica la tienda que no estemos mirando?», y una rama de la que sí
        sacamos el eje `deportiva` sí la estamos mirando. Es lo mismo que ya hacen Lefties y C&A, y
        es además lo que retira las cuatro `sportswear` de `vigia.COBERTURA_DECLARADA`.
        """
        return [cat.page_id for cat in self._categories] + [
            hoja.page_id for hoja in self._tag_leaves
        ]

    def tree_separator(self) -> str:
        """Ver `stores.base.SupportsCategoryTree`. H&M anida sus rutas con `/`, y son suyas."""
        return "/"

    def tree_roots(self) -> Iterable[str]:
        """Ver `stores.base.SupportsCoverageWatch`. Las siete ramas donde vive el catálogo.

        No se barre desde `/kids` y `/baby` por el mismo motivo que Sfera no barre desde `ninos` y
        Springfield no barre desde su mundo: la taxonomía del brief empieza en la rama de género, y
        colgando de los dos departamentos hay mucho que no lo es. Medido el 05/08/2026: el árbol
        entero son **651 rutas**, de las que **393 caen bajo estas siete**; las 258 restantes son
        casa y juguetes (`bedding`, `furniture-lighting`, `toys`, `rugs`…), vistas transversales
        que reagrupan lo mismo (`shop-by-product` ×133, `9-14y` ×74) y campaña que caduca sola
        (`last-chance`, `new-arrivals`, `seasonal-trending`, `global-node-*`). Declararlas serían
        ~60 entradas que envejecen sin que nadie las mire, que es justo lo que #179 midió en Zara.

        Lo que queda fuera del barrido **no queda fuera del alcance**: `--tree /kids` y
        `--tree /baby` siguen enumerando el árbol entero, y es ahí donde se mira si un día aparece
        un departamento nuevo — que es exactamente como se encontró el de bebé de Zara (#186).
        """
        return [
            "/kids/boys",
            "/kids/boys-9-14y",
            "/kids/girls",
            "/kids/girls-9-14y",
            "/baby/boys",
            "/baby/girls",
            "/baby/newborn",
        ]

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas. Ver `stores.base.SupportsLeafHealth`.

        Una petición por hoja más una del canario. El veredicto sale de compararlas: aquí una hoja
        muerta no da 404 ni lista vacía, da el cubo entero con 200.

        No hay `SupportsAliveProbe` que complemente esto (ver el punto 5 de la cabecera), así que
        este sondeo es lo único que avisa de que una hoja ha cambiado de nombre.
        """
        with self._client() as client:
            try:
                canario = self._ids_canario(client)
            except (httpx.HTTPError, ValueError) as exc:
                # Sin canario no hay veredicto posible para NINGUNA hoja, y decir «viva» sería
                # peor que callar: el vigía trata `None` como aviso, que es lo que esto es.
                for cat in self._categories:
                    yield LeafHealth(
                        ScrapeScope(cat.gender, cat.section, cat.category),
                        cat.page_id,
                        None,
                        f"no se pudo pedir el canario ({type(exc).__name__}), "
                        "así que no hay con qué reconocer un espejismo",
                        estacional=cat.estacional,
                    )
                return
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                try:
                    payload = self._get_json(client, cat.page_id, _PAGINA_INICIAL)
                    ids = ids_de_pagina(payload)
                except httpx.HTTPStatusError as exc:
                    yield LeafHealth(
                        scope,
                        cat.page_id,
                        None,
                        f"HTTP {exc.response.status_code}",
                        estacional=cat.estacional,
                    )
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(
                        scope,
                        cat.page_id,
                        None,
                        f"{type(exc).__name__}: {exc}",
                        estacional=cat.estacional,
                    )
                else:
                    if es_espejismo(ids, canario):
                        yield LeafHealth(
                            scope,
                            cat.page_id,
                            False,
                            f"espejismo: devuelve el cubo de {_CATEGORY_ID!r}",
                            estacional=cat.estacional,
                        )
                    else:
                        total = _plp(payload).get("numberOfHits")
                        yield LeafHealth(
                            scope,
                            cat.page_id,
                            bool(ids),
                            f"{len(ids)} productos en la 1ª página (la tienda declara {total})",
                            estacional=cat.estacional,
                        )
            # Las hojas de etiqueta se sondean igual (#208), y su ámbito va sin sección ni categoría
            # porque no la tienen: eso es lo que las hace transversales. No ingieren nada, así que
            # su muerte no vacía ninguna categoría — pero dejaría de marcarse un eje entero **en
            # silencio**, que es exactamente el fallo que el vigía existe para no tener.
            for hoja in self._tag_leaves:
                scope = ScrapeScope(hoja.gender, None, None)
                try:
                    payload = self._get_json(client, hoja.page_id, _PAGINA_INICIAL)
                    ids = ids_de_pagina(payload)
                except httpx.HTTPStatusError as exc:
                    yield LeafHealth(scope, hoja.page_id, None, f"HTTP {exc.response.status_code}")
                except (httpx.TransportError, ValueError) as exc:
                    yield LeafHealth(scope, hoja.page_id, None, f"{type(exc).__name__}: {exc}")
                else:
                    espejismo = es_espejismo(ids, canario)
                    yield LeafHealth(
                        scope,
                        hoja.page_id,
                        not espejismo and bool(ids),
                        f"espejismo: devuelve el cubo de {_CATEGORY_ID!r}"
                        if espejismo
                        else f"eje `{hoja.tag}`: {len(ids)} productos en la 1ª página",
                    )


def _ambito(hojas: Sequence[CategoryConfig], filas: Sequence[Fila]) -> ScrapeScope | None:
    """El ámbito de un modelo a partir de las hojas en las que ha aparecido.

    La regla vive en `base.ambito_cruzado()`, que es donde está escrito el porqué: aquí solo se
    traduce la `CategoryConfig` de esta tienda al `ScrapeScope` que aquella espera. El cruce vale
    317 modelos de 3401 (9,3 %) el 02/08/2026.

    **Las hojas de saldo (#468) no votan la categoría, y ése es todo el mecanismo.** Si el modelo
    sale además en cualquier hoja normal, decide ésa y aquí no cambia nada: es la misma garantía
    que en Zara dan el ORDEN de `CATEGORIES` y el residuo del final —la prenda rebajada que
    además cuelga de su hoja de prenda conserva su categoría de siempre— solo que aquí no puede
    depender del orden, porque esta tienda **acumula la pasada entera antes de emitir** y para
    entonces ya sabe todas las hojas de cada modelo.

    Solo cuando el modelo vive **exclusivamente** en la rama de saldo se recurre al nombre. Y
    entonces puede no haber respuesta: `None` significa «no ingerir», nunca «categoría vacía».

    **Pero el GÉNERO lo votan TODAS las hojas, también las de saldo, y esa asimetría es el meollo
    de esta función.** Lo que la hoja de saldo no sabe es qué prenda es; de qué rama cuelga sí lo
    sabe, y publicar un modelo en `last-chance/girls-2-8y` es la tienda diciendo que es de niña
    igual que lo dice su hoja de vestidos. La primera versión de #468 descartaba la hoja de saldo
    entera en cuanto había una categorizada, y con ella se iba el cruce: un modelo en
    `boys/.../trousers` **y** en `last-chance/girls-2-8y` salía `niño` en vez de `unisex`. Es
    exactamente el fallo que #98 destapó en Hipercor, reintroducido en la intersección
    normal↔saldo. **Hoy no hay ni un producto así** (medido el 18/08/2026: 0 de los 429
    compartidos), o sea que no había nada que reparar — pero el mecanismo estaba mal y no lo
    cazaba nada, que es la mitad peligrosa.
    """
    categorizadas = [h for h in hojas if not h.por_familia]
    if categorizadas:
        # La sección y la categoría las fija la PRIMERA hoja categorizada, como en el resto de
        # tiendas; las de saldo no entran en esa votación pero sí en la del género, abajo.
        section, category = categorizadas[0].section, categorizadas[0].category
    else:
        destino = categoria_desde_nombre(filas[0].name) if filas else None
        if destino is None:
            return None
        section, category = destino
    return ambito_cruzado([ScrapeScope(h.gender, section, category) for h in hojas])
