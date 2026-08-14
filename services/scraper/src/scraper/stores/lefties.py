"""Scraper de Lefties (niños): ropa y calzado infantil.

Lefties es Inditex, igual que Zara, y su API es la familia `itxrest` — pero a diferencia de Zara
**está tras Akamai**: un cliente HTTP plano recibe 403 aunque mande cabeceras de navegador
completas. Por eso todo va por el **navegador headless** (`stores/browser.py`), como Sfera.

Cuatro endpoints, y el orden importa:

  1. menú:     /api/storefront/1/stores/{store}/menu?catalogId={catalog}&typeCatalog=1&...
     Da el árbol de categorías. La clave está en que cada hoja trae `content.id`, un **uuid**:
     es lo que pide el listado. Los `/{category}/product` que uno supone por analogía con Zara
     dan todos 404 — el id numérico de categoría NO sirve para listar.
     Con una excepción que hay que conocer: en los **nodos alias** ese `content.id` no es un uuid
     sino el id numérico de otra categoría, y quien lo resuelve es `grid_ids_by_category()` (#393).
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

Cuatro trampas que conviene no volver a pisar:

- **El stock NO es `isBuyable`**, que viene `true` siempre. Es `visibilityValue`: `SHOW` =
  disponible, `HIDDEN` = agotada. Y con `allowWithoutStock=false` el endpoint **omite** las tallas
  agotadas, así que se pide `true`: si no, un producto agotado del todo parecería una baja.
- **`price` llega como string** de céntimos ("1799"), no como int, al revés que Zara.
- **El `name` de una talla puede no ser una talla.** 173 prendas de `ropa` traían `'42'`, un
  número suelto en una sección cuyo vocabulario es de edad entero, y con `'5-6 años (116 cm)'` al
  lado en `description`. Lo corrige `talla_de()`, que es donde está la medición y por qué el
  arreglo tiene que ser estrecho (#365).
- **Los componentes del grid no se reconocen por `kind`.** La tienda intercambió `kind` y `type`
  el 05/08/2026 y la pasada se quedó en 0 entradas sin que nada se pusiera rojo; el porqué y cómo
  se detectó están en `_product_components()`, que es donde vive la decisión.

**Hojas de campaña (#195, y el criterio que cerró #176).** Las dos `REBAJAS HASTA -70%` son la
primera hoja que ingerimos cuya vida depende de una campaña, y la regla que salió de medirlas vale
para cualquier tienda:

    Una hoja de campaña se mapea si su id es estable y publica producto propio. Si mezcla
    categorías, la categoría se deriva POR PRODUCTO, no por hoja. Y su apagado no es una
    retirada: es estacional, y no puede sonar como accionable.

Las tres partes están medidas aquí: el id (`1030302501`) es un hueco fijo del menú —misma forma de
`key` que las hojas permanentes, y un id **más antiguo** que el de varias de ellas—, publica 26
prendas que no están en ninguna otra hoja, y mezcla desde faldas hasta zapatillas. Lo que resuelve
cada parte: `_FAMILIA_A_DOMINIO` la categoría, `CategoryConfig.estacional` el apagado, y el ORDEN
de `CATEGORIES` que la hoja mezclada no le pise la categoría a quien ya la tiene.

⚠️ **Lo que esto deja abierto, y no está medido:** estas prendas son las únicas del catálogo que no
cuelgan de ninguna hoja permanente, así que cuando la campaña acabe dejarán de verse **del todo**.
Ahí decide `probe_alive()`, que aquí da por vivo cualquier id que `productsArray` siga reconociendo
aunque esté agotado —Sfera usa dos señales, esta solo una—, y a un producto confirmado vivo
`ingest.py` le pone la racha a cero (`_rescue`). O sea que un saldo agotado que la tienda siga
sirviendo en el detalle se quedaría en el catálogo indefinidamente. No se ha visto pasar: hay que
mirarlo al acabar esta campaña, y si pasa la respuesta es de `probe_alive()` (tratar «existe pero
todas las tallas HIDDEN» como no concluyente), no de esta hoja.

Id estable de producto: `identifier.productParentId` (= `id` del detalle). Id estable de variante:
`{productId}-{colorId}-{sku}`. Las funciones `parse_*` son puras y se testean con fixtures reales.
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
from .browser import BrowserHTTPError, BrowserSession

_LOG = logging.getLogger(__name__)

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

    `por_familia` y `estacional` son las dos marcas que necesita una **hoja de campaña** (#195), y
    son independientes a propósito: una rebaja de una sola categoría sería estacional sin ser
    mezclada. Con `por_familia`, `section` y `category` van vacías porque la hoja no las decide —
    las decide cada producto (ver `_FAMILIA_A_DOMINIO`)—, pero `gender` **sí sigue siendo de la
    hoja**: la de niña es de niña, eso la tienda no lo mezcla.
    """

    category_id: int
    gender: str  # niño | niña
    section: str  # ropa | zapateria ("" si `por_familia`)
    category: str  # pantalones | camisetas | sudaderas | ... ("" si `por_familia`)
    parent: str = ""  # cadena de ids del padre en el menú; ver `mapped_leaves()`
    por_familia: bool = False  # sección y categoría salen del producto, no de la hoja
    estacional: bool = False  # apagarse es fin de campaña, no retirada (ver `LeafHealth`)


# Las seis ramas del menú de las que cuelga todo lo que ingerimos, medidas el 05/08/2026. Son el
# `parent` de cada `CategoryConfig` y con eso `mapped_leaves()` no necesita red.
_NINA = f"{_RAIZ_NINOS}/1030267672"
_NINA_ROPA = f"{_NINA}/1030267677"
_NINA_ZAPATOS = f"{_NINA}/1030267718"
_NINO = f"{_RAIZ_NINOS}/1030267673"
_NINO_ROPA = f"{_NINO}/1030269022"
_NINO_ZAPATOS = f"{_NINO}/1030267842"

# Y las de las tres ramas de bebé (#194), medidas el 06/08/2026. `Recién Nacido` cuelga sus hojas
# directamente de la rama, sin secciones intermedias.
_BEBE_NINA = f"{_RAIZ_NINOS}/1030267674"
_BEBE_NINA_ROPA = f"{_BEBE_NINA}/1030269024"
_BEBE_NINA_ZAPATOS = f"{_BEBE_NINA}/1030267949"
_BEBE_NINO = f"{_RAIZ_NINOS}/1030267675"
_BEBE_NINO_ROPA = f"{_BEBE_NINO}/1030269100"
_BEBE_NINO_ZAPATOS = f"{_BEBE_NINO}/1030269050"
_NEWBORN = f"{_RAIZ_NINOS}/1030513546"


# Qué sección y qué categoría le tocan a un producto cuando **la hoja no lo dice** (`por_familia`).
# La familia (`classification.family.name`) es un dato de la ficha, no de la hoja, así que sirve
# justo donde el mapeo por hoja no llega: la hoja de rebajas mezcla camisetas, faldas, pijamas y
# hasta calzado en un solo listado.
#
# **La tabla no se ha inventado: es lo que las 38 hojas mapeadas ya hacen.** Medido el 05/08/2026
# sobre sus 2207 componentes, contando en qué categoría cae cada familia. Por eso `SHORT` va a
# `vestidos` aunque suene a pantalón: es la hoja `faldas | shorts` de niña la que trae los 64, y el
# short de niño llega como `BERMUDAS`, que sí va a `pantalones`. Alinearse con la tienda importa
# más que el nombre, porque el slug ES el filtro que ve el usuario.
#
# Se deja fuera **a propósito** lo que no se puede defender, y perder una prenda es mejor que
# meterla en la categoría equivocada:
#
#   - `ENSEMBLE..SET` (conjunto) — es exactamente la pregunta abierta de #192, y decidirla aquí de
#     tapadillo sería peor que perderla: en las hojas mapeadas cae en cuatro categorías distintas
#     (pantalones 42, camisetas 37, vestidos 12, sudaderas 8).
#   - `WAISTCOAT` (chaleco), `WIND-BREAK` (cortavientos), `BIB OVERALL` (peto), `ACCESSORIES` —
#     ninguna es de las 5 del brief. Que `WIND-BREAK` aparezca 6 veces dentro de `sudaderas` no lo
#     convierte en una sudadera; ahí lo decidió una hoja mapeada y aquí no hay hoja que lo decida.
#
# **`subfamily` NO sirve para esto, y está medido**: en la hoja de rebajas del 05/08/2026 mentía en
# 4 de 26 productos (una falda con `Girls’ Chunky Knit Top`, un pantalón con `…Waistcoat`, una
# camiseta con `Sporty Jacket`, un pijama con `…Long Sleeve Polo`), mientras que `family` acertaba
# en los 26 contra el nombre de la prenda.
#
# Y `barefoot` no puede salir de aquí: en esta tienda es una RAMA del menú, no una familia. Un
# barefoot rebajado entraría por su familia de calzado; lo que lo rescata es el respaldo por nombre
# de `classify_barefoot()` en `parse_product_detail`.
_FAMILIA_A_DOMINIO: dict[str, tuple[str, str]] = {
    # --- ropa ---
    "T-SHIRT": ("ropa", "camisetas"),
    "SHIRT": ("ropa", "camisetas"),
    "POLO/SHIRT": ("ropa", "camisetas"),
    "TOPS AND OTHERS": ("ropa", "camisetas"),
    "SWEATSHIRT": ("ropa", "sudaderas"),
    "SWEATER": ("ropa", "sudaderas"),
    "CARDIGAN": ("ropa", "sudaderas"),
    "TROUSERS": ("ropa", "pantalones"),
    "BERMUDAS": ("ropa", "pantalones"),
    "LEGGINGS": ("ropa", "pantalones"),
    "DRESS": ("ropa", "vestidos"),
    "SKIRT": ("ropa", "vestidos"),
    "SHORT": ("ropa", "vestidos"),  # ver la nota de arriba: es lo que hace `faldas | shorts`
    "UNDERWEAR": ("ropa", "ropa-interior"),
    "NIGHTIE/PYJAMAS": ("ropa", "ropa-interior"),
    "SOCKS": ("ropa", "ropa-interior"),
    "STOCKINGS-TIGHTS": ("ropa", "ropa-interior"),
    # --- zapatería ---
    "TRAINERS": ("zapateria", "zapatillas"),
    "SNEAKERS": ("zapateria", "zapatillas"),
    "HIGHTOPS": ("zapateria", "zapatillas"),
    "BOOTS": ("zapateria", "botas"),
    "ANKLEBOOTS": ("zapateria", "botas"),
    "SANDALS": ("zapateria", "sandalias"),
    "BEACHSANDALS": ("zapateria", "sandalias"),
    "SHOES": ("zapateria", "zapatos"),
    "BALLETPUMPS": ("zapateria", "zapatos"),
}


def dominio_de_familia(componente: Mapping[str, Any]) -> tuple[str, str] | None:
    """`(sección, categoría)` de un producto por su familia, o `None` si no la sabemos mapear.

    `None` significa **descartar la prenda**, no colarla en un cajón cualquiera: ver la lista de
    exclusiones de `_FAMILIA_A_DOMINIO`.
    """
    familia = ((componente.get("classification") or {}).get("family") or {}).get("name")
    return _FAMILIA_A_DOMINIO.get(str(familia or "").strip().upper())


def dominios_emitibles(gender: str) -> list[tuple[str, str]]:
    """Lo que una hoja mezclada puede emitir para ese género: la tabla, acotada a lo que ya
    ingerimos por hojas de categoría.

    La tabla no distingue género —una familia es una familia— y sin acotarla, el día que la tienda
    colgara una falda de la hoja de rebajas de niño saldría un `niño/ropa/vestidos`, que es un
    ámbito que esta tienda no tiene: `vestidos` solo existe en niña, igual que en Zara y Sfera, y
    hay un test que lo da por cierto sobre `CATEGORIES`. Hoy no pasa (medido: el short de niño
    llega como `BERMUDAS`), pero eso es una observación de un día, no una garantía — y la prenda
    perdida es más barata que un ámbito inventado que ningún filtro de la web sabe enseñar.

    Se deriva de `CATEGORIES` en vez de escribirse para que no haya dos verdades: si algún día
    `vestidos` llega a niño por su hoja propia, esto se entera solo.
    """
    del_genero = {
        (c.section, c.category) for c in CATEGORIES if c.gender == gender and not c.por_familia
    }
    return [d for d in dict.fromkeys(_FAMILIA_A_DOMINIO.values()) if d in del_genero]


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
#
# Y el mismo mecanismo, al revés, es lo que hace segura la hoja de rebajas: va **la última**, así
# que solo aporta lo que ninguna hoja de categoría publica. Si algún día la tienda deja de sacar
# la prenda rebajada de su categoría, esto no cambia nada — la categoría de verdad ya la habrá
# fijado su hoja. Mover esas dos líneas hacia arriba sí lo rompería, y en silencio: pasarían a
# decidir la categoría de prendas que hoy la reciben de una hoja que la sabe mejor.
#
# Entre medias van las tres ramas de BEBÉ (#194) y, detrás de todo lo del brief, las hojas de
# `conjuntos`: el criterio común de #187/#192 quiere que un conjunto que la tienda publica también
# bajo una de las cinco conserve esa categoría, y aquí eso pasa mucho (ver la nota del bloque).
CATEGORIES: list[CategoryConfig] = [
    # --- barefoot: primero a propósito (ver nota de orden arriba) ---
    # Va SOLO la de la rama propia, como en bebé: la de dentro de `ZAPATOS` publica EXACTAMENTE lo
    # mismo. Medido el 06/08/2026 (15/15 niña, 23/23 niño) y **remedido el 14/08/2026** con el
    # catálogo ya crecido —21/21 y 25/25, cruzando por `productParentId`—, así que no es una
    # coincidencia de un día. Las dos espejo quedan declaradas en `COBERTURA_DECLARADA` con la
    # medición; ver #203 para por qué el coste no era la petición sino el comentario que las hacía
    # parecer dos conjuntos distintos que había que unir.
    #
    # Y no son dos hojas hermanas: la que mapeamos es un **alias de menú** (`key` acabada en
    # `_MENU`) cuyo `content.id` es la otra —`1030680692` -> `1030680609`, `1030680206` ->
    # `1030680610`—, así que la identidad la garantiza el menú, no la medición. Ese salto lo
    # deshace `grid_ids_by_category()` desde #393: la pasada pide el uuid de la hoja apuntada y no
    # el id numérico, que la tienda venía resolviendo de casualidad. Eso responde
    # también a la duda de #203 sobre perder señal al declararla: la que sondea `check_leaves()`
    # no puede sobrevivir a la declarada, porque lo que pide es su grid. Si la de dentro de
    # `ZAPATOS` muriera, la petición de la rama propia moriría con ella y la pasada la trataría
    # como hoja retirada (`_hoja_comprometida`), que es exactamente lo que hay que decir.
    CategoryConfig(1030680692, "niña", "zapateria", "barefoot", _NINA),  # barefoot (rama propia)
    CategoryConfig(1030680206, "niño", "zapateria", "barefoot", _NINO),  # barefoot (rama propia)
    # Las dos ramas de bebé tienen su hoja `BAREFOOT` propia, que es justo el nicho del producto y
    # hasta ahora solo se llenaba desde niño/niña. Va SOLO la de la rama, no la de dentro de
    # `ZAPATOS`: se midió el 06/08/2026 y las dos publican EXACTAMENTE lo mismo (23 y 24 modelos,
    # solape total), así que mapear las dos sería una petición por pasada a cambio de nada. El
    # espejo queda declarado en `COBERTURA_DECLARADA` con esa medición.
    CategoryConfig(1030680693, "niña", "zapateria", "barefoot", _BEBE_NINA),  # bebé (rama propia)
    CategoryConfig(1030680207, "niño", "zapateria", "barefoot", _BEBE_NINO),  # bebé (rama propia)
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
    # --- bebé (#194): tres ramas que el departamento infantil publica y no mirábamos -------------
    #
    # `Bebé Niña`, `Bebé Niño` y `Recién Nacido`. Medido el 06/08/2026 cruzando por
    # `productParentId`: estas hojas aportan **292 modelos y NINGUNO entra por otra rama**, así que
    # el catálogo de la tienda pasa de 785 a 1077 (pasada real, 13436 variantes, 0 bajas). Es la
    # misma forma que el departamento de bebé de Zara (#186), y como allí el género sale de si la
    # rama separa niño de niña:
    #
    #   - `Bebé Niña` -> `niña` y `Bebé Niño` -> `niño`, que es lo que ya hacen `sfera.py`
    #     (`ninos/bebe-nina`) e `hipercor.py` (`bebe-nina-6-meses-a-3-anos`).
    #   - `Recién Nacido` -> `unisex`, porque su rama NO separa. Es la misma decisión que
    #     `zapatos-infantiles/bebe` en Hipercor, `/baby/newborn` en H&M, `prendas_newborn.*` en
    #     Mango y la rama de bebé de Zara. Ojo a lo que arrastra: es la primera hoja `unisex` de
    #     esta tienda, y por eso el docstring de `scopes()` ya no puede leer su `unisex` como «el
    #     número de cruces de género».
    #
    # LO QUE EL NOMBRE DE LA HOJA NO DICE, y hubo que mirar dentro (la trampa del ADR desde #175):
    # aquí la familia dominante de media docena de hojas es `BABY SHORT`, que NO es «short» — es la
    # de los conjuntos y co-ords. Por eso el mismo modelo («Hello Kitty T-shirt and leggings
    # co-ord») sale a la vez en `Camisetas`, `Leggings`, `Faldas | Shorts` y `Conjuntos`, y por eso
    # el orden de aquí abajo decide de verdad: gana la primera y `conjuntos` va al final.
    #
    # Tres hojas cuya elección no es obvia, con el recuento que la decidió:
    #   - `Peleles | Braguitas` (recién nacido) -> `vestidos`. De sus 13, cinco son peleles/rompers
    #     puros («Swiss embroidery romper», «Double gauze playsuit») y ocho co-ords. El pelele va a
    #     `vestidos` como en Zara (`VESTIDOS | PELELES`, 2637249) y como razona Mango; los co-ords
    #     conservan esa categoría a propósito, que es lo que el criterio de #187/#192 pide.
    #   - `Denim` -> `pantalones`. Es un eje de TEJIDO, no de prenda: de sus 15 (niña), diez son
    #     vaqueros, shorts o falda-pantalón, y las dos cazadoras y los dos vestidos de denim ya
    #     tienen su casa en las hojas que van por delante.
    #   - `Bermudas | Petos` (bebé niño) -> `pantalones`, como la hoja `bermudas` de niño
    #     (1030321544): de sus 26, veintitrés nombran «Bermuda shorts».
    #
    # Y el árbol NO es simétrico entre las dos ramas, que es donde se pierde catálogo en silencio
    # (el agujero de #56 en Sfera y el de Hipercor): `Bebé Niño` no publica `Zapatos` ni `Botas y
    # Botines`, y `Bebé Niña` no publica `Camisas` sino `Camisas | Tops`. Copiar la lista de una
    # rama en la otra habría dejado fuera diez modelos con las hojas pareciendo vivas.
    #
    # --- bebé niña / ropa ---
    CategoryConfig(1030267914, "niña", "ropa", "camisetas", _BEBE_NINA_ROPA),  # camisetas
    CategoryConfig(1030715207, "niña", "ropa", "camisetas", _BEBE_NINA_ROPA),  # camisas | tops
    CategoryConfig(1030267927, "niña", "ropa", "sudaderas", _BEBE_NINA_ROPA),  # sudaderas
    CategoryConfig(1030267923, "niña", "ropa", "vestidos", _BEBE_NINA_ROPA),  # vestidos | petos
    CategoryConfig(1030323503, "niña", "ropa", "vestidos", _BEBE_NINA_ROPA),  # faldas | shorts
    CategoryConfig(1030267933, "niña", "ropa", "pantalones", _BEBE_NINA_ROPA),  # pantalones
    CategoryConfig(1030267993, "niña", "ropa", "pantalones", _BEBE_NINA_ROPA),  # leggings
    CategoryConfig(1030716207, "niña", "ropa", "pantalones", _BEBE_NINA_ROPA),  # denim
    CategoryConfig(1030267944, "niña", "ropa", "ropa-interior", _BEBE_NINA_ROPA),  # pijamas
    CategoryConfig(1030267922, "niña", "ropa", "ropa-interior", _BEBE_NINA_ROPA),  # bodies
    # `Ropa Interior` tiene tres hijas (braguitas, calcetines, bodies) y es su unión exacta: 27 =
    # 9+10+8, medido. Se mapea la madre y las hijas quedan cubiertas por descendencia.
    CategoryConfig(1030526545, "niña", "ropa", "ropa-interior", _BEBE_NINA_ROPA),  # ropa interior
    CategoryConfig(1030564699, "niña", "ropa", "ropa-interior", _BEBE_NINA_ROPA),  # calcetines
    # --- bebé niña / zapatería ---
    CategoryConfig(1030272372, "niña", "zapateria", "zapatos", _BEBE_NINA_ZAPATOS),  # zapatos
    CategoryConfig(1030681608, "niña", "zapateria", "botas", _BEBE_NINA_ZAPATOS),  # botas y botines
    CategoryConfig(1030272374, "niña", "zapateria", "sandalias", _BEBE_NINA_ZAPATOS),  # sandalias
    CategoryConfig(1030272357, "niña", "zapateria", "zapatillas", _BEBE_NINA_ZAPATOS),  # zapatillas
    # --- bebé niño / ropa ---
    CategoryConfig(1030269025, "niño", "ropa", "camisetas", _BEBE_NINO_ROPA),  # camisetas
    CategoryConfig(1030570730, "niño", "ropa", "camisetas", _BEBE_NINO_ROPA),  # camisas
    CategoryConfig(1030269038, "niño", "ropa", "sudaderas", _BEBE_NINO_ROPA),  # sudaderas
    CategoryConfig(1030269042, "niño", "ropa", "pantalones", _BEBE_NINO_ROPA),  # pantalones
    CategoryConfig(1030324001, "niño", "ropa", "pantalones", _BEBE_NINO_ROPA),  # bermudas | petos
    CategoryConfig(1030716208, "niño", "ropa", "pantalones", _BEBE_NINO_ROPA),  # denim
    CategoryConfig(1030269047, "niño", "ropa", "ropa-interior", _BEBE_NINO_ROPA),  # pijamas
    CategoryConfig(1030269033, "niño", "ropa", "ropa-interior", _BEBE_NINO_ROPA),  # bodies
    CategoryConfig(1030527045, "niño", "ropa", "ropa-interior", _BEBE_NINO_ROPA),  # ropa interior
    CategoryConfig(1030564195, "niño", "ropa", "ropa-interior", _BEBE_NINO_ROPA),  # calcetines
    # --- bebé niño / zapatería (sin `zapatos` ni `botas`: no los publica en esta rama) ---
    CategoryConfig(1030276118, "niño", "zapateria", "sandalias", _BEBE_NINO_ZAPATOS),  # sandalias
    CategoryConfig(1030272371, "niño", "zapateria", "zapatillas", _BEBE_NINO_ZAPATOS),  # zapatillas
    # --- recién nacido (unisex): sus hojas cuelgan de la rama, sin secciones intermedias ---
    CategoryConfig(1030718712, "unisex", "ropa", "vestidos", _NEWBORN),  # peleles | braguitas
    CategoryConfig(1030564188, "unisex", "ropa", "pantalones", _NEWBORN),  # leggings | pantalones
    CategoryConfig(1030535546, "unisex", "ropa", "ropa-interior", _NEWBORN),  # pijamas
    CategoryConfig(1030524057, "unisex", "ropa", "ropa-interior", _NEWBORN),  # bodies
    CategoryConfig(1030719211, "unisex", "ropa", "ropa-interior", _NEWBORN),  # ropa interior
    # --- conjuntos: DETRÁS de todo lo del brief (criterio común de #187 / #192) ------------------
    #
    # `conjuntos` es la categoría de la prenda que no tiene ninguna de las cinco del brief como
    # casa natural. Yendo detrás, un conjunto que la tienda publica ADEMÁS bajo una de las cinco
    # conserva esa categoría, y solo se etiqueta `conjuntos` el que no sale en ninguna otra hoja:
    # decide la taxonomía de la tienda y no quien mapea.
    #
    # Va SOLO la de `Recién Nacido`, y el porqué salió de ingerir y leer los nombres, que es la
    # única comprobación que lo distingue (lo dice el ADR, y aquí lo volvió a demostrar):
    #
    #   - `Recién Nacido` aporta **4 conjuntos legítimos** («Conjunto Garfield gofrado camiseta y
    #     bermuda», «Set camiseta y bermuda heavy jersey»…). Tienen sentido justo ahí porque esta
    #     rama NO tiene hoja de camisetas ni de sudaderas: son conjuntos sin casa natural, que es
    #     la definición de la categoría.
    #   - Las de **bebé niña y bebé niño NO se mapean**. Sus 33 y 38 modelos entran todos por
    #     `Camisetas`, `Leggings` o `Bermudas | Petos`, que van por delante, así que lo único que
    #     llegaban a etiquetar `conjuntos` era **un bañador** — residuo de `Bañadores`, una hoja
    #     que declaramos fuera. Es la misma trampa que revirtió H&M y Zara: el exclusivo de una
    #     hoja que reagrupa no es «sin casa natural», puede ser «con una casa que no ingerimos».
    CategoryConfig(1030524056, "unisex", "ropa", "conjuntos", _NEWBORN),  # conjuntos
    # --- rebajas: LAS ÚLTIMAS, y mezcladas (ver la nota de orden y `_FAMILIA_A_DOMINIO`) ---
    #
    # «REBAJAS HASTA -70%», una por género. Publican prenda que **no está en ninguna otra hoja**:
    # medido el 05/08/2026 (25+6 modelos, 0 solapes) y otra vez el 06/08/2026 (22+4, 0 solapes),
    # cruzando por `productParentId` contra las 38 hojas de arriba.
    #
    # Y ese 0 no es que la tienda saque la prenda de su categoría al rebajarla —eso está medido y
    # es falso: 275 de los 2207 componentes de las hojas normales vienen rebajados—, es la
    # **temporada**: las 38 hojas van enteras en `I2026` (2207 de 2207) y las de rebajas enteras en
    # `V2026` (32 de 32). Son el saldo de la temporada que sale, que ya no cuelga de ninguna
    # categoría. O sea: sin estas dos hojas, la ropa infantil **rebajada de verdad** de esta tienda
    # no la ve nadie, que es justo lo que el producto existe para avisar.
    CategoryConfig(1030302501, "niña", "", "", _NINA, por_familia=True, estacional=True),
    CategoryConfig(1030303020, "niño", "", "", _NINO, por_familia=True, estacional=True),
]


@dataclass(frozen=True)
class TagLeaf:
    """Hoja que **solo aporta un eje transversal** (`scraper.tags`), sin ingerir nada.

    No es una `CategoryConfig` y la diferencia es deliberada, no cosmética:

    - **No emite `ListingEntry`.** Sus productos entran al catálogo por su categoría de verdad o no
      entran; esta hoja solo dice «además, esto es deportivo».
    - **No aparece en `scopes()`.** Ese es el motivo de peso. `scopes()` alimenta las redes de bajas
      (`safe_scopes`, `_suspicious_scopes`), así que una hoja metida en `CATEGORIES` para etiquetar
      pasaría a poder provocar bajas — y una hoja transversal es exactamente la que más se mueve.
    - **Su ámbito no existe.** La prenda deportiva no es una categoría (#175, #180): darle una para
      poder listarla sería reintroducir por la puerta de atrás lo que la issue descartó.

    El precio es que los productos **exclusivos** de la hoja se quedan fuera: 16 de los 146 aquí (8
    y 8), porque su categoría real no la dice nadie. Es la cola que la propia issue admite que puede
    no compensar, y está medida en `vigia.COBERTURA_DECLARADA`.
    """

    category_id: int
    parent: str  # cadena de ids del padre, igual que en `CategoryConfig`; ver `mapped_leaves()`
    tag: str
    gender: str  # niño | niña — solo para describirla en `check_leaves()`; no crea ámbito


# «Ropa Deportiva», una rama por género, con seis hijas cada una (#180). No se ingieren: la mayoría
# de sus productos **ya entra** por `camisetas`, `pantalones` y `sudaderas`, que es el tercer dato
# —tras Sfera y C&A— de que el eje deportivo es transversal y no una categoría. Medido el
# 05/08/2026 con la pasada real: **181 productos** en la rama, **167 ya dentro** del catálogo y 14
# exclusivos. De esos 167 se marcan **130**: los otros 37 son calzado, y el eje no lo toca porque
# la zapatilla deportiva ya se encuentra por la categoría `zapatillas` (ver `scraper/tags.py`).
#
# **Se recorre la rama ENTERA, no solo su nodo**, y esto costó una medición equivocada: el grid del
# padre NO devuelve el subárbol. Medido el 05/08/2026, pidiendo las dos cosas:
#
#     rama    padre   union de sus 6 hijas   en las hijas y no en el padre
#     niña      77            93                        16
#     niño      69            99                        30
#
# O sea que quedarse en el padre se deja fuera 46 prendas, casi un cuarto de la rama. El «146» que
# circula por #180 y por las declaraciones del vigía es la cifra del padre, no la de la rama.
#
# Las hijas se resuelven del MENÚ en ejecución (`parse_category_tree`) en vez de escribirse aquí:
# el menú ya se pide para traducir cada `category_id` a su uuid de grid, así que enumerarlas no
# cuesta ninguna petición extra, y una hija nueva entra sola. Escribir sus ids sería declarar una
# lista que caduca en silencio, que es justo lo que `check_leaves()` existe para cazar.
HOJAS_ETIQUETA: list[TagLeaf] = [
    TagLeaf(1030267709, _NINA_ROPA, TAG_DEPORTIVA, "niña"),  # 3_NA_T_ROPADEPORTIVA
    TagLeaf(1030267833, _NINO_ROPA, TAG_DEPORTIVA, "niño"),  # 3_NO_T_ROPADEPORTIVA
]


def _cents(value: Any) -> Decimal | None:
    """Lefties da los precios en céntimos, pero como STRING ("1799" -> 17.99 €)."""
    if value is None or value == "":
        return None
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


#: Saltos máximos al resolver un alias. Medido sobre los dos menús que hay en fixtures, los 14
#: alias resuelven en **uno**; el margen es para que una cadena inesperada no se quede a medias, y
#: el tope para que un ciclo del menú no cuelgue la pasada.
_MAX_SALTOS_ALIAS = 3


def grid_ids_by_category(menu: dict[str, Any]) -> dict[int, str]:
    """Recorre el menú y devuelve `id de categoría -> uuid del grid` de todas las hojas.

    El menú tiene **nodos alias**, y su `content.id` no es el uuid de un grid: es el id numérico de
    **otra categoría** (#393). Es una convención suya y no una rareza de barefoot — las `_VIEWALL`
    («Ver Todo») apuntan a su padre y las `_MENU` a la hoja de dentro de otra rama. Medido sobre los
    fixtures: 8 de 116 hojas en el menú de niña y 6 de 108 en el de bebé.

    Devolver ese id tal cual funcionaba de casualidad: la tienda hoy resuelve el id numérico en el
    hueco del uuid. El día que deje de hacerlo, las cuatro hojas `barefoot` que mapeamos —que son
    justamente alias— darían 404 **a la vez**, `_hoja_comprometida()` las leería como retiradas y
    con `SCRAPER_SCAN_MAX_DEAD_RATIO` de por medio eso no es inofensivo. Lo caro no es la petición
    fallida: es el diagnóstico, que manda a buscar una baja que no existe.

    Un alias cuyo destino no esté en el menú **conserva su valor**. Quitarlo convertiría el alias
    huérfano en una hoja desaparecida, que es exactamente el diagnóstico equivocado que esto viene a
    evitar.
    """
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

    # Se resuelve al final y no dentro de `walk()` a propósito: un alias puede aparecer en el árbol
    # antes que la categoría a la que apunta.
    for cid, grid in list(out.items()):
        destino = grid
        vistos = {cid}
        for _ in range(_MAX_SALTOS_ALIAS):
            if not destino.isdigit():
                break
            siguiente = int(destino)
            if siguiente in vistos or siguiente not in out:
                break
            vistos.add(siguiente)
            destino = out[siguiente]
        out[cid] = destino

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

    En una hoja `por_familia` (la de rebajas) la sección y la categoría **salen de cada producto**
    y no de la hoja, y la prenda cuya familia no sepamos mapear —o que caiga en un ámbito que esta
    tienda no publica para ese género— se descarta en vez de colarla en un cajón cualquiera (ver
    `_FAMILIA_A_DOMINIO` y `dominios_emitibles`). La familia es del modelo, no del color, así que
    basta mirar el primer componente de cada uno.
    """
    por_modelo: dict[str, list[str]] = {}
    dominio_de: dict[str, tuple[str, str]] = {}
    emitibles = set(dominios_emitibles(cat.gender)) if cat.por_familia else set()
    for comp in _product_components(grid):
        ident = comp.get("identifier") or {}
        parent = ident.get("productParentId")
        if not parent:
            continue
        pid = str(parent)
        if cat.por_familia and pid not in dominio_de:
            dominio = dominio_de_familia(comp)
            if dominio is None or dominio not in emitibles:
                continue  # familia que no mapea a nada del brief, o ámbito que la tienda no tiene
            dominio_de[pid] = dominio
        color = (comp.get("color") or {}).get("id")
        precio = (((comp.get("pricing") or {}).get("price") or {}).get("current") or {}).get(
            "value"
        )
        por_modelo.setdefault(pid, []).append(f"{color}:{precio}")

    entradas = []
    for pid, partes in por_modelo.items():
        section, category = dominio_de.get(pid, (cat.section, cat.category))
        entradas.append(
            ListingEntry(
                retailer_product_id=pid,
                signature="|".join(sorted(partes)),
                gender=cat.gender,
                section=section,
                category=category,
            )
        )
    return entradas


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


# La talla por edad tal y como la escribe esta tienda: «5-6 años», «6-12 meses», «1-6 meses».
_TALLA_POR_EDAD = re.compile(r"^\s*\d+\s*(?:-\s*\d+)?\s*(?:años|año|meses|mes)\b", re.IGNORECASE)
# Un número pelado, sin unidad ni rango: «42». En `ropa` esta tienda no tiene ninguna talla así.
_NUMERO_SUELTO = re.compile(r"^\s*\d+\s*$")


def talla_de(size: Mapping[str, Any], section: str) -> str | None:
    """La talla de una variante, corrigiendo el número suelto que la tienda mete en `ropa` (#365).

    Normalmente es `name` tal cual. La excepción está medida: **173 prendas de `ropa` traían la
    talla cruda `42`**, un número suelto entre un vocabulario que en esta sección es de edad de
    punta a punta. Pedido el detalle de las afectadas el 14/08/2026, las tres hipótesis que la
    issue dejaba abiertas se resuelven de una vez, porque la respuesta viene en el mismo objeto:

        {'name': '42', 'description': '5-6 años (116 cm)', 'visibilityValue': 'SHOW'}

    O sea que **no es ropa de adulto** (116 cm es el peldaño más bajo de la escalera, justo debajo
    de `6-7 años`) y **no es un artefacto nuestro** (leemos `name` fielmente): es un valor malo en
    el `name` de la tienda, con el bueno al lado en `description`. Muestreadas 1237 variantes de
    cinco hojas de secciones y ramas distintas, el `42` siempre venía con la misma descripción.

    **El arreglo es estrecho a propósito, porque `description` no es mejor fuente en general**, y
    eso también está medido en la misma muestra:

    - 521 variantes **no traen `description`** (el calzado, casi entero).
    - En los calcetines el bueno es `name`: `'15-17'` con `description` `'1-6 meses (8-9 cm)'`.
      Ahí la descripción da la equivalencia por edad, no la talla, y preferirla borraría el
      vocabulario por número que #325 tuvo que nombrar `Por número`.
    - En los complementos, `'M'` con `'Talla única'`.

    Por eso la regla pide las tres cosas a la vez: sección `ropa`, `name` que sea un número suelto
    —que en `ropa` no es talla de nada— y una `description` que sí empiece por una etiqueta de
    edad. Cualquier gemelo futuro del `42` entra solo; nada de lo que hoy está bien se toca.
    """
    nombre = size.get("name")
    if not isinstance(nombre, str):
        return None
    if section != "ropa" or not _NUMERO_SUELTO.match(nombre):
        return nombre
    descripcion = size.get("description")
    if not isinstance(descripcion, str) or not _TALLA_POR_EDAD.match(descripcion):
        return nombre  # número suelto sin edad al lado: no hay con qué corregirlo, se deja igual
    return descripcion.split("(")[0].strip()


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
                    size=talla_de(size, cat.section),
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
        tag_leaves: list[TagLeaf] | None = None,
    ) -> None:
        self._config = config
        self._categories = categories if categories is not None else CATEGORIES
        # Costura para los tests: por defecto abre un Chromium real (igual que Sfera).
        self._session_factory = session_factory or (lambda: BrowserSession(config))
        # Detalle cacheado del ámbito de cada producto: `fetch_details` recibe `ListingEntry`,
        # pero necesita la CategoryConfig para el dominio y para construir la URL.
        self._cat_by_product: dict[str, CategoryConfig] = {}
        self._scan = ScanReport()  # lo rellena list_catalog(); ver `scan_report()`
        self._tag_leaves = tag_leaves if tag_leaves is not None else HOJAS_ETIQUETA
        self._tags = ProductTags()  # ídem; ver `product_tags()`
        self._menu_cache: dict[str, Any] | None = None  # árbol cacheado; ver `_menu()`

    def scopes(self) -> Iterable[ScrapeScope]:
        """Los ámbitos de las hojas **más su equivalente `unisex`** (ver `base.con_unisex`).

        Los productos que salen en las dos ramas de género se emiten `unisex` (#98), así que sin
        declarar esos ámbitos no se descatalogarían nunca. **14 de 700 (2,0 %) el 03/08/2026**, casi
        todos camisetas.

        ⚠️ **Ese número dejó de ser el de cruces con #194.** Hasta entonces esta tienda no declaraba
        ninguna hoja `unisex`, así que el `unisex` del reparto **era** exactamente el cruce de
        géneros; desde que `Recién Nacido` entra como `unisex` —igual que Hipercor, H&M y Mango con
        sus ramas de bebé o newborn— el reparto suma las dos cosas y ya no se pueden leer por
        separado ahí. Sigue re-midiéndose sin base de datos ni detalle con `python -m scraper.run
        --retailer lefties --dry-run`, pero para aislar el cruce hay que descontar los modelos que
        vienen de las hojas de `Recién Nacido`. Se dice aquí porque #139 nació de comparar aquel 0
        contra el `unisex` de las otras tres, que no medía lo mismo.

        Una hoja `por_familia` no tiene un ámbito, tiene **todos los que su tabla puede emitir**, y
        hay que declararlos: un ámbito sin declarar no cuenta como escaneado y sus productos no se
        descatalogarían nunca. Es lo mismo que hace `cacles.py` —declarar lo que el parser PUEDE
        emitir en vez de lo que dicen sus hojas— y por el mismo motivo.
        """
        ambitos = []
        for c in self._categories:
            if c.por_familia:
                ambitos += [
                    ScrapeScope(c.gender, s, cat) for s, cat in dominios_emitibles(c.gender)
                ]
            else:
                ambitos.append(ScrapeScope(c.gender, c.section, c.category))
        return con_unisex(ambitos)

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Recorre las hojas y emite un producto por `productParentId`.

        **Acumula la pasada entera antes de emitir**, como H&M e Hipercor: que un producto salga en
        la rama de niña Y en la de niño —lo que lo hace `unisex`, #98— solo se sabe con todas las
        hojas vistas, y el ámbito de una entrada ya emitida no se puede corregir.

        El ámbito que se apunta por producto es **el de la entrada, no el de la hoja**. En las 38
        hojas de categoría son el mismo; en la de rebajas no, porque allí la sección y la categoría
        las decide cada producto (`por_familia`).
        """
        self._scan = ScanReport()
        self._tags = ProductTags()
        primera_entrada: dict[str, ListingEntry] = {}
        hojas_por_producto: dict[str, list[ScrapeScope]] = {}
        with self._session_factory() as session:
            session.goto(BASE_URL)  # siembra las cookies de Akamai
            menu = session.get_json(_MENU_URL)
            grids = grid_ids_by_category(menu)
            self._lee_hojas_etiqueta(session, grids, menu)
            for cat in self._categories:
                scope = ScrapeScope(cat.gender, cat.section, cat.category)
                grid_id = grids.get(cat.category_id)
                if grid_id is None:
                    # Hoja que ya no está en el menú: es el mismo caso que el 404 de una hoja de
                    # Zara. Se salta, pero su ámbito sale de las bajas — el comentario que había
                    # aquí daba por hecho que la red por ámbito lo cubría, y no es así: `scopes()`
                    # se deriva de CATEGORIES, así que el ámbito seguía contando como escaneado.
                    self._hoja_comprometida(cat, scope)
                    continue
                try:
                    grid = session.get_json(_GRID_URL.format(grid_id=grid_id))
                except BrowserHTTPError as exc:
                    if exc.status not in GONE_STATUS:
                        raise
                    self._hoja_comprometida(cat, scope)
                    continue
                self._scan.leaf_ok()
                for entry in parse_listing_entries(grid, cat):
                    pid = entry.retailer_product_id
                    # Un modelo puede aparecer en varias hojas (p.ej. barefoot también cuelga de
                    # zapatos). La primera fija sección, categoría y huella; el género sale del
                    # conjunto de hojas, no de ella sola.
                    if pid not in primera_entrada:
                        primera_entrada[pid] = entry
                        # Con `por_familia` la sección y la categoría son del producto, así que la
                        # config que viaja al detalle es la de la hoja CON el dominio ya resuelto:
                        # `fetch_details` construye desde ella el ámbito del `ScrapedProduct`.
                        self._cat_by_product[pid] = replace(
                            cat, section=entry.section or "", category=entry.category or ""
                        )
                    hojas = hojas_por_producto.setdefault(pid, [])
                    if entry.scope not in hojas:
                        hojas.append(entry.scope)

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

    def _lee_hojas_etiqueta(
        self, session: BrowserSession, grids: dict[int, str], menu: dict[str, Any]
    ) -> None:
        """Recorre las ramas que solo aportan eje transversal (ver `TagLeaf`).

        Va ANTES que las de categoría por una razón práctica: si Akamai va a cerrar la puerta, que
        lo haga aquí y no a mitad del catálogo. Lo que se apunta son ids, así que el orden no
        influye en el resultado — las marcas se aplican al final, sobre lo que sea que se ingiera.

        **Recorre la rama entera**, no solo su nodo: aquí el grid del padre no devuelve el subárbol
        (ver la cabecera de `HOJAS_ETIQUETA`, donde está medido). Las hijas salen del menú que esta
        pasada ya se ha bajado, así que enumerarlas no cuesta una petición extra — solo la de cada
        grid.

        Un fallo retira su eje de `fiables` y no aborta nada: sin catálogo que ingerir detrás, lo
        peor que puede pasar aquí es quedarse sin marcar, y eso no debe costar la pasada. Tampoco
        toca `_scan`: estas ramas no tienen ámbito, así que no pueden dejar a nadie sin bajas ni
        contar para el `dead_ratio` que aborta la pasada.
        """
        self._tags.fiables = {hoja.tag for hoja in self._tag_leaves}
        for hoja in self._tag_leaves:
            # El nodo y sus descendientes. `dict.fromkeys` en vez de `set` para que el orden sea
            # estable entre pasadas: hace los logs comparables y el test determinista.
            ids = list(
                dict.fromkeys(
                    [hoja.category_id]
                    + [
                        int(n.path.rsplit("/", 1)[-1])
                        for n in parse_category_tree(menu, str(hoja.category_id))
                    ]
                )
            )
            if grids.get(hoja.category_id) is None:
                _LOG.warning(
                    "lefties: la rama de etiqueta %s ya no está en el menú; `%s` no se reconcilia",
                    hoja.category_id,
                    hoja.tag,
                )
                self._tags.fiables.discard(hoja.tag)
                continue
            # Dos nodos de la rama pueden resolver al MISMO grid, porque los alias del menú
            # apuntan a otra categoría (#393) y el `_VIEWALL` de un eje apunta justo a su padre,
            # que es la hoja del propio eje: medido sobre `lefties_menu.json`, `1030267747` y
            # `1030267709` dan los dos `0526c2bc-…`. El dedupe de arriba es por `category_id`, que
            # no lo ve. Antes de #393 tampoco se veía —las dos URL eran distintas y la tienda las
            # resolvía al mismo sitio—, así que esto no arregla una regresión: quita una petición
            # que llevaba ahí desde el principio, contra una tienda que va tras Akamai.
            #
            # El conjunto es POR HOJA y no global: dos ejes distintos que compartieran grid
            # aplican etiquetas distintas, y saltarse el segundo dejaría prendas sin marcar.
            pedidos: set[str] = set()
            for cid in ids:
                grid_id = grids.get(cid)
                if grid_id is None:
                    continue  # nodo del menú sin grid propio (un divisor, o una sección espejo)
                if grid_id in pedidos:
                    continue
                pedidos.add(grid_id)
                try:
                    grid = session.get_json(_GRID_URL.format(grid_id=grid_id))
                except Exception as exc:  # noqa: BLE001 — misma red ancha que en el listado (#107)
                    _LOG.warning(
                        "lefties: hoja de etiqueta %s ilegible (%s: %s); `%s` no se reconcilia",
                        cid,
                        type(exc).__name__,
                        exc,
                        hoja.tag,
                    )
                    self._tags.fiables.discard(hoja.tag)
                    break
                for comp in _product_components(grid):
                    parent = (comp.get("identifier") or {}).get("productParentId")
                    if parent:
                        self._tags.anota(str(parent), hoja.tag)

    def _hoja_comprometida(self, cat: CategoryConfig, scope: ScrapeScope) -> None:
        """Cuenta la hoja como caída y saca su ámbito —y el `unisex` equivalente— de las bajas.

        El porqué de lo segundo está en `ScanReport.leaf_gone()`.

        **Una hoja estacional apagada no compromete nada**, y esa es la diferencia que #195 vino a
        introducir. Contarla como caída haría dos daños, los dos al acabar cada campaña: subiría
        `dead_ratio` hacia el tope que aborta la pasada, y sacaría de las bajas un ámbito que se ha
        listado perfectamente por sus 38 hojas de siempre. Lo que solo vivía en la hoja de rebajas
        tampoco se descataloga por sorpresa: al desaparecer del listado pasa por la confirmación
        activa (`probe_alive`), que es la que decide si el producto existe todavía.
        """
        if cat.estacional:
            return
        self._scan.leaf_gone(scope, str(cat.category_id), tambien_unisex=True)

    def scan_report(self) -> ScanReport:
        """Ver `stores.base.SupportsScanReport` (válido con `list_catalog()` ya consumido)."""
        return self._scan

    def product_tags(self) -> ProductTags:
        """Ver `stores.base.SupportsProductTags` (válido con `list_catalog()` ya consumido)."""
        return self._tags

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Sondea las hojas configuradas (ver `stores.base.SupportsLeafHealth`).

        Aquí sale casi gratis y sin tocar los grids: **el menú entero es UNA petición** y una hoja
        retirada es, precisamente, la que ya no aparece en él. Que el grid siga respondiendo se
        comprueba en la pasada, que es cuando hace falta.

        La hoja de rebajas desaparece del menú al acabar la campaña, que aquí es exactamente lo
        mismo que ve una hoja retirada de verdad. Se emite `estacional=True` para que el vigía lo
        cuente como aviso y no pida cada jueves un id nuevo que volverá solo (ver `LeafHealth`).
        """
        with self._session_factory() as session:
            session.goto(BASE_URL)  # siembra las cookies de Akamai
            grids = grid_ids_by_category(session.get_json(_MENU_URL))
        for cat in self._categories:
            scope = ScrapeScope(cat.gender, cat.section, cat.category)
            grid_id = grids.get(cat.category_id)
            if grid_id:
                detalle = f"grid {grid_id}"
            elif cat.estacional:
                detalle = "no está en el menú: campaña apagada, su id vuelve con la campaña"
            else:
                detalle = "ya no está en el menú"
            yield LeafHealth(
                scope,
                str(cat.category_id),
                grid_id is not None,
                detalle,
                estacional=cat.estacional,
            )
        # Las hojas que solo etiquetan se sondean igual (#180). No ingieren nada, así que su muerte
        # no vacía ninguna categoría — pero dejaría de marcarse un eje entero **en silencio**, que
        # es exactamente el fallo que el vigía existe para no tener. Su ámbito va sin categoría
        # porque no la tienen: eso es lo que las hace transversales.
        for hoja in self._tag_leaves:
            grid_id = grids.get(hoja.category_id)
            yield LeafHealth(
                ScrapeScope(hoja.gender, "ropa", None),
                str(hoja.category_id),
                grid_id is not None,
                f"eje `{hoja.tag}`: " + (f"grid {grid_id}" if grid_id else "ya no está en el menú"),
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

        Las hojas de etiqueta (`TagLeaf`) cuentan como mapeadas aunque no ingieran nada. La
        pregunta que el vigía hace aquí es «¿esta rama la estamos mirando o es un hueco?», y a esas
        las miramos: dejarlas fuera obligaría a mantenerlas encima en `COBERTURA_DECLARADA` diciendo
        «sin decidir» cuando ya está decidido, que es la deuda que #180 vino a quitar.
        """
        propias = [f"{cat.parent}/{cat.category_id}" for cat in self._categories]
        return propias + [f"{hoja.parent}/{hoja.category_id}" for hoja in self._tag_leaves]

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
