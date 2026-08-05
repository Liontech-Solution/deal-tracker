"""Vigía en vivo: comprueba que las tiendas nos siguen dejando entrar (#67).

Uso:
    python -m scraper.vigia                      # todas las tiendas del registro
    python -m scraper.vigia --retailer cacles    # una sola, para depurar
    python -m scraper.vigia --dry-run            # informa por consola, sin issue ni histórico

**Por qué existe.** Un scraper deja de funcionar de dos maneras: porque la tienda cambió (una hoja
de categoría caduca, el JSON cambia de forma) o porque la tienda dejó de dejarnos entrar. Lo segundo
es silencioso y se descubre tarde: el arreglo de la huella TLS de Cacles (`scraper/tls.py`) se apoya
en un detalle interno de httpcore, y si un bump lo rompe volvemos a comer 429 sin que nadie se
entere. La señal existía —`--check-categories` y los tests `*_LIVE=1`— pero **solo corría a mano**,
y nadie la lanza tres semanas después, que es justo cuando hace falta.

**Qué pregunta cada capa.** «¿Siguen vivas las hojas que ingerimos?» (`revisar_hojas`), «¿la cadena
entera sigue produciendo productos usables?» (`revisar_parseo`), «¿publica la tienda algo que NO
estamos ingiriendo?» (`revisar_cobertura`, #156) y «¿nos dejan entrar al ritmo de siempre?»
(`comparar_con_base`, #111). La tercera es la simétrica de la primera y faltaba: `check_leaves()`
itera lo ya mapeado, así que una categoría **nueva** —o una de temporada que vuelve, como el
`punto-y-jerseis` de bebé que #151 retiró en julio— no la sondaba nadie. La herramienta existía
(`run.py --tree`) y solo se lanzaba a mano, que en este vigía significa no tenerla.

**Por qué en el cluster y no en CI.** La pregunta que responde no es «¿la tienda está viva?» sino
«¿nos deja entrar *a nosotros*?», y eso depende de por dónde salimos a internet. Un runner de
GitHub tiene otra IP y otra reputación que el cluster: contestaría por otro. Corre como CronJob
(`deal-tracker/base/vigia-cronjob.yaml` en el repo de manifiestos), que además es el único
`suspend: false` del despliegue — un vigía suspendido es exactamente el problema que viene a
resolver.

**Por qué no son tests.** La imagen del scraper solo copia `src` (ver `Dockerfile`): no lleva pytest
ni los tests. Lo que tenga que correr en el cluster tiene que vivir aquí.

**Por qué además cronometra (#111).** El veredicto puede ser correcto y aun así ocultar la avería:
el 02/08/2026 el sondeo de Hipercor tardó 24 min 28 s desde el pod y 2 min 04 s desde fuera, verde
las dos veces, porque la tienda seguía regulándonos el paso tras el bloqueo de #107. Así que cada
capa se cronometra, se normaliza a segundos por unidad —un absoluto envejece mal, los catálogos
crecen—, se guarda en `vigia_run` y se compara contra la mediana de las últimas ejecuciones de esa
misma tienda. Sirve para dimensionar el `activeDeadlineSeconds` con una tendencia en vez de
descubrirlo cuando una pasada muere por deadline y hace rollback.

**Cómo se entera de las tiendas nuevas.** Recorriendo `registry.available_slugs()`, no una lista
propia. Registrar una tienda es meterla en el vigía, y el CronJob tampoco nombra tiendas, así que
añadir una no obliga a tocar el repo de manifiestos. Lo que el registro no puede garantizar —que la
tienda implemente `check_leaves()`— lo vigila `test_toda_tienda_registrada_tiene_vigilancia`, que
rompe `just check`.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from .avisos import AvisoGitHub
from .config import Config, load_dotenv
from .stores.base import (
    BaseStore,
    CategoryNode,
    LeafHealth,
    ListingEntry,
    ScrapedProduct,
    SupportsCategoryTree,
    SupportsCoverageWatch,
    SupportsLeafHealth,
)
from .stores.registry import available_slugs, get_store
from .vigia_historial import Base, Historial

# El reloj, indirecto a propósito: así los tests fijan duraciones sin parchear el módulo `time`
# entero, que es global y afectaría a cualquier otra cosa que corra en el mismo proceso.
_reloj = time.monotonic

# Tiendas registradas a las que se les perdona no tener `check_leaves()`, **con el motivo escrito**.
# Vacío a propósito: las cuatro de hoy lo implementan. Existe para que la excepción sea una decisión
# explícita y revisable y no un olvido silencioso; quien añada una entrada aquí está diciendo «esta
# tienda no se puede sondear por hojas y sé por qué».
SIN_VIGILANCIA_DE_HOJAS: dict[str, str] = {}

# Tiendas que saben enumerar su árbol pero a las que NO se les vigila la cobertura, con el motivo
# escrito. Mismo papel que `SIN_VIGILANCIA_DE_HOJAS`: que la excepción sea una decisión revisable.
COBERTURA_SIN_VIGILAR: dict[str, str] = {
    "zara": (
        "su árbol es el menú de navegación, no una taxonomía, y es el caso más extremo de los "
        "medidos. El 05/08/2026, acotando ya las raíces a las cinco ramas infantiles: 766 nodos "
        "y 510 sin cubrir (eran 536 antes de mapear bebé, #186), encabezados por «VER TODO» ×81 "
        "y «COLECCIÓN» ×20, más los "
        "DIVIDER_MENU_*, EDITORIAL, SPECIAL PRICES y LICENCIAS. Declararlos serían 510 entradas "
        "que caducan con la campaña siguiente. Conserva su `--tree`, que además es lo que la "
        "cabecera de zara.py manda usar cuando un id de hoja caduca (#179)"
    ),
    "cacles": (
        "sus colecciones de Shopify son PLANAS: no anidan, así que no hay forma de decir «esto ya "
        "entra por su padre» y la única hoja que ingerimos —`infantil`— es justo el paraguas de "
        "todo el catálogo infantil. Medido el 04/08/2026: 161 colecciones, y las que no son de "
        "adulto son de marketing (`60-de-descuento`, `best-sellers`, `barefoot-padel-paddle-"
        "tennis`) o cortes por tipo cuyos productos ya entran por `infantil`. Vigilarlo sería 160 "
        "huecos semanales, todos falsos. Aquí la pregunta de cobertura no es «qué hoja falta» "
        "sino «qué `product_type` no está mapeado», y esa ya la canta `_categoria_desde_tipo()` "
        "por el log en cada pasada (#179)"
    ),
    "mango": (
        "su árbol es el menú de navegación, no una taxonomía: publica promociones que rotan "
        "(dest_toystory, dest_ramadam, nuevosarticulosanadidos) y un espejo `rebajas_*` de cada "
        "rama de prendas. Medido el 04/08/2026 acotando ya las raíces a lo que ingerimos, harían "
        "falta 72 declaraciones y caducarían con la campaña siguiente. Conserva su `--tree`"
    ),
}

# Categorías que la tienda PUBLICA y que no ingerimos **a propósito**, por tienda y con el motivo
# escrito (#156). Todo lo demás que aparezca en el árbol y no esté mapeado es accionable.
#
# Es la mitad que hace que esta capa sirva. Sin ella el vigía avisaría cada jueves de las mismas
# categorías que ya decidimos no ingerir, y un vigía con falsas alarmas rutinarias acaba
# silenciado — que es peor que no tenerlo, y la razón por la que `Informe` separa `accionable` de
# `aviso`. Con ella, lo que suena es lo NUEVO: una categoría que la tienda acaba de publicar o una
# de temporada que ha vuelto.
#
# **Declarar una rama calla a sus hijas** (ver `cubierta`), que es lo que mantiene la lista corta:
# en C&A, declarar `3-1-5` (Baño) se lleva sus tres hijas, y así 56 rutas huérfanas se declaran con
# 23 entradas. Por eso van las ramas y no las hojas sueltas.
#
# La ruta va en el vocabulario de `CategoryNode.path`, el mismo que `mapped_leaves()`. Declarar
# aquí algo que además ingerimos es una declaración caducada y rompe
# `test_cobertura_declarada_no_solapa_con_lo_mapeado`: la lista tiene que envejecer con ruido, no
# en silencio, porque una entrada de sobra tapa exactamente lo que esta capa existe para ver.
COBERTURA_DECLARADA: dict[str, dict[str, str]] = {
    # Medido el 04/08/2026 sobre las cuatro ramas: 46 rutas, 13 sin cubrir. Nueve se declaran aquí;
    # las otras cuatro eran las `ropa-deportiva` que #156 dejó sonar a propósito, y #175 las midió:
    # son sudaderas, así que ahora se **ingieren** y por eso no aparecen en esta lista.
    "sfera": {
        "ninos/nina/abrigos-y-cazadoras": "abrigo no es ninguna de las 5 categorías del brief",
        "ninos/nino/abrigos-y-cazadoras": "abrigo no es ninguna de las 5 categorías del brief",
        "ninos/bebe-nina/abrigos-y-cazadoras": "abrigo no es ninguna de las 5 categorías del brief",
        "ninos/bebe-nino/abrigos-y-cazadoras": "abrigo no es ninguna de las 5 categorías del brief",
        "ninos/nina/bano": "baño no es ninguna de las 5 categorías del brief",
        "ninos/bebe-nina/bano": "baño no es ninguna de las 5 categorías del brief",
        "ninos/nino/banadores": "baño no es ninguna de las 5 categorías del brief",
        "ninos/nina/accesorios": "complementos: ni ropa ni calzado",
        "ninos/nino/accesorios": "complementos: ni ropa ni calzado",
    },
    # Las ramas que `c_and_a.CATEGORIES` ya dejaba fuera **en prosa**; esta lista es la misma
    # decisión, pero comprobable. Declarar la rama basta: sus hijas se callan solas (`cubierta`).
    "c-and-a": {
        "3-1-5": "baño no es ninguna de las 5 categorías del brief",
        "3-7-4": "baño no es ninguna de las 5 categorías del brief",
        "3-1-25": "pijama entra por `ropa-interior` en las hojas ya mapeadas",
        "3-7-25": "pijama entra por `ropa-interior` en las hojas ya mapeadas",
        "3-1-8": "chaqueta no es ninguna de las 5 categorías del brief",
        "3-7-7": "chaqueta no es ninguna de las 5 categorías del brief",
        "3-1-10": "complementos: ni ropa ni calzado",
        "3-7-9": "complementos: ni ropa ni calzado",
        "3-1-23": "«Básicos» es una vista transversal, no una categoría",
        "3-7-22": "«Básicos» es una vista transversal, no una categoría",
        "3-1-21": "«Premium» es una vista transversal, no una categoría",
        "3-7-20": "«Premium» es una vista transversal, no una categoría",
        "3-1-22": "packs: agrupan prendas de varias categorías",
        "3-7-21": "packs: agrupan prendas de varias categorías",
        "3-1-14": "«Novedades» es una vista transversal, no una categoría",
        "3-7-13": "«Novedades» es una vista transversal, no una categoría",
        "3-2-29": "«Vuelta al cole» es campaña transversal (rama 3-2, promoción)",
        "8-449": "trajes de ceremonia: no es ninguna de las 5 categorías del brief",
        "8-77": "ropa de lluvia: no es ninguna de las 5 categorías del brief",
        # La pregunta que estas dos dejaron abierta —¿«ropa de deporte» es del brief?— se contestó
        # midiendo, en #175, y la respuesta es distinta en cada tienda porque el mismo nombre tapa
        # cosas distintas: en Sfera son sudaderas (van mapeadas) y aquí es una vista transversal.
        # Medido el 04/08/2026: de los 45 productos de las dos hojas, **42 ya entran** por
        # `camisetas` (24), `pantalones` (15) y `sudaderas` (3). Los 3 exclusivos son dos chaquetas
        # y un chubasquero impermeable, que ya están fuera por su propia categoría.
        "3-1-24": "vista transversal: 42 de 45 ya entran por camisetas/pantalones/sudaderas (#175)",
        "3-7-23": "vista transversal: 42 de 45 ya entran por camisetas/pantalones/sudaderas (#175)",
    },
    # Lo que la cabecera de `springfield.py` ya dejaba fuera **en prosa**, hecho comprobable (#179).
    # Contado sobre el sitemap del 04/08/2026: **productos distintos**, no filas — el sitemap repite
    # cada URL entre sus tres ficheros y a filas estos números salen 2,3 veces más grandes. Las
    # ramas van por género porque la taxonomía de esta tienda empieza ahí: no hay `pijamas` suelto.
    #
    # Declarar la rama basta y por eso no aparecen las subcategorías (`nina/bano/bikinis`,
    # `nino/complementos/gorras`, `nina/chaquetas/chalecos`…): se callan solas por `cubierta`.
    # H&M enumera desde #179. Son 119 declaraciones para 393 rutas bajo las siete ramas de
    # `tree_roots()`, y esa proporción es la propia tienda: publica 17 vistas por rama de género
    # («Básicos», «Ver todo», «Multipacks», «Licencias»…) que reagrupan lo que ya entra por su
    # categoría. Se declaran **hoja a hoja bajo `clothing` y de rama entera fuera**: declarar
    # `/kids/boys/clothing` de una vez serían 7 líneas en lugar de 90, pero taparía una categoría
    # nueva justo donde el brief vive, que es lo único que esta capa existe para ver.
    #
    # Las siete `sets-outfits` han pasado por aquí dos veces y ya no están: salieron en #192,
    # volvieron en el mismo PR con el motivo medido (de los 20 productos que trajeron, 11 eran
    # disfraces y 1 un bikini) y se han ido definitivamente en #200, que las ingiere **filtradas**
    # por el rótulo «Conjunto de …». Declarar algo que además se ingiere rompe
    # `test_cobertura_declarada_no_solapa_con_lo_mapeado`, que es exactamente lo que se quiere: la
    # lista tiene que envejecer con ruido. El porqué completo está en la cabecera de `hm.py`.
    #
    # Tres NO son decisión firme, y conviene que se lean así (mismo formato que #187 en
    # springfield): el uniforme escolar publica pantalón, vestido, polo, jersey y zapato, o sea
    # prendas del brief en una rama que no ingerimos; `sport`/`sportswear` es la ropa deportiva
    # cuya decisión ya tiene issue (#180); y los monos entran por `vestidos` en Lefties y aquí no.
    # El día que se resuelvan, esas entradas se van y la rama pasa a `CATEGORIES`.
    "hm": {
        # --- niño 2-8 (/kids/boys) ---
        "/kids/boys/accessories": "complementos: ni ropa ni calzado (8)",
        "/kids/boys/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/kids/boys/clothing/blazers-suits": "trajes y americanas: fuera del brief (1)",
        "/kids/boys/clothing/care-products": "cuidado de la ropa: no es ropa (1)",
        "/kids/boys/clothing/characters": "licencias: vista transversal (1)",
        "/kids/boys/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/kids/boys/clothing/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/kids/boys/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/kids/boys/clothing/linen": "vista transversal por material (1)",
        "/kids/boys/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/kids/boys/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/kids/boys/clothing/socks": "calcetines: fuera del brief (1)",
        "/kids/boys/clothing/sport": "deportiva, transversal: #180 (1)",
        "/kids/boys/clothing/swimwear": "baño: fuera del brief (1)",
        "/kids/boys/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/kids/boys/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/kids/boys/outerwear": "abrigo: fuera del brief (9)",
        "/kids/boys/school": "uniforme: lleva prendas del brief, sin decidir (12)",
        "/kids/boys/sportswear": "deportiva, transversal: #180 (12)",
        # --- niño 9-14 (/kids/boys-9-14y) ---
        "/kids/boys-9-14y/accessories": "complementos: ni ropa ni calzado (8)",
        "/kids/boys-9-14y/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/kids/boys-9-14y/clothing/blazers-suits": "trajes y americanas: fuera del brief (1)",
        "/kids/boys-9-14y/clothing/care-products": "cuidado de la ropa: no es ropa (1)",
        "/kids/boys-9-14y/clothing/characters": "licencias: vista transversal (1)",
        "/kids/boys-9-14y/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/kids/boys-9-14y/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/kids/boys-9-14y/clothing/linen": "vista transversal por material (1)",
        "/kids/boys-9-14y/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/kids/boys-9-14y/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/kids/boys-9-14y/clothing/socks": "calcetines: fuera del brief (1)",
        "/kids/boys-9-14y/clothing/sport": "deportiva, transversal: #180 (1)",
        "/kids/boys-9-14y/clothing/swimwear": "baño: fuera del brief (1)",
        "/kids/boys-9-14y/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/kids/boys-9-14y/outerwear": "abrigo: fuera del brief (9)",
        "/kids/boys-9-14y/sportswear": "deportiva, transversal: #180 (12)",
        "/kids/boys-9-14y/swimwear1": "baño: fuera del brief (1)",
        # --- niña 2-8 (/kids/girls) ---
        "/kids/girls/accessories": "complementos: ni ropa ni calzado (8)",
        "/kids/girls/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/kids/girls/clothing/care-products": "cuidado de la ropa: no es ropa (1)",
        "/kids/girls/clothing/characters": "licencias: vista transversal (1)",
        "/kids/girls/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/kids/girls/clothing/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/kids/girls/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/kids/girls/clothing/jumpsuits-playsuits": "monos: sin decidir, ver Lefties (1)",
        "/kids/girls/clothing/linen": "vista transversal por material (1)",
        "/kids/girls/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/kids/girls/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/kids/girls/clothing/socks-tights": "calcetines: fuera del brief (1)",
        "/kids/girls/clothing/sport": "deportiva, transversal: #180 (1)",
        "/kids/girls/clothing/swimwear": "baño: fuera del brief (1)",
        "/kids/girls/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/kids/girls/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/kids/girls/outerwear": "abrigo: fuera del brief (11)",
        "/kids/girls/school": "uniforme: lleva prendas del brief, sin decidir (15)",
        "/kids/girls/sportswear": "deportiva, transversal: #180 (15)",
        # --- niña 9-14 (/kids/girls-9-14y) ---
        "/kids/girls-9-14y/accessories": "complementos: ni ropa ni calzado (8)",
        "/kids/girls-9-14y/body-hair": "cuidado personal: no es ropa (1)",
        "/kids/girls-9-14y/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/kids/girls-9-14y/clothing/care-products": "cuidado de la ropa: no es ropa (1)",
        "/kids/girls-9-14y/clothing/characters": "licencias: vista transversal (1)",
        "/kids/girls-9-14y/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/kids/girls-9-14y/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/kids/girls-9-14y/clothing/jumpsuits-playsuits": "monos: sin decidir, ver Lefties (1)",
        "/kids/girls-9-14y/clothing/linen": "vista transversal por material (1)",
        "/kids/girls-9-14y/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/kids/girls-9-14y/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/kids/girls-9-14y/clothing/socks-tights": "calcetines: fuera del brief (1)",
        "/kids/girls-9-14y/clothing/sport": "deportiva, transversal: #180 (1)",
        "/kids/girls-9-14y/clothing/swimwear": "baño: fuera del brief (1)",
        "/kids/girls-9-14y/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/kids/girls-9-14y/outerwear": "abrigo: fuera del brief (10)",
        "/kids/girls-9-14y/sportswear": "deportiva, transversal: #180 (15)",
        "/kids/girls-9-14y/swimwear1": "baño: fuera del brief (1)",
        # --- bebé niño (/baby/boys) ---
        "/baby/boys/accessories": "complementos: ni ropa ni calzado (5)",
        "/baby/boys/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/baby/boys/clothing/characters": "licencias: vista transversal (1)",
        "/baby/boys/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/baby/boys/clothing/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/baby/boys/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/baby/boys/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/baby/boys/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/baby/boys/clothing/rompers": "monos: sin decidir, ver Lefties (1)",
        "/baby/boys/clothing/socks": "calcetines: fuera del brief (1)",
        "/baby/boys/clothing/swimwear": "baño: fuera del brief (1)",
        "/baby/boys/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/baby/boys/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/baby/boys/outerwear": "abrigo: fuera del brief (5)",
        "/baby/boys/swimwear1": "baño: fuera del brief (1)",
        # --- bebé niña (/baby/girls) ---
        "/baby/girls/accessories": "complementos: ni ropa ni calzado (5)",
        "/baby/girls/clothing/basics": "«Básicos»: reagrupa lo ya ingerido (1)",
        "/baby/girls/clothing/characters": "licencias: vista transversal (1)",
        "/baby/girls/clothing/fancy-dress-costumes": "disfraces: fuera del brief (1)",
        "/baby/girls/clothing/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/baby/girls/clothing/jackets-coats": "abrigo: fuera del brief (1)",
        "/baby/girls/clothing/multipacks": "packs: varias prendas en una referencia (1)",
        "/baby/girls/clothing/party-occasion": "vista transversal por ocasión (1)",
        "/baby/girls/clothing/rompers": "monos: sin decidir, ver Lefties (1)",
        "/baby/girls/clothing/socks-tights": "calcetines: fuera del brief (1)",
        "/baby/girls/clothing/swimwear": "baño: fuera del brief (1)",
        "/baby/girls/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/baby/girls/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/baby/girls/outerwear": "abrigo: fuera del brief (6)",
        "/baby/girls/swimwear1": "baño: fuera del brief (1)",
        # --- recién nacido (/baby/newborn) ---
        "/baby/newborn/accessories": "complementos: ni ropa ni calzado (4)",
        "/baby/newborn/clothing/charcater-shop": "licencias (el typo es de la tienda) (1)",
        "/baby/newborn/clothing/h-m-adorables": "campaña «H&M Adorables», no categoría (1)",
        "/baby/newborn/clothing/linen": "vista transversal por material (1)",
        "/baby/newborn/clothing/outerwear": "abrigo: fuera del brief (1)",
        "/baby/newborn/clothing/rompers": "monos: sin decidir, ver Lefties (1)",
        "/baby/newborn/clothing/socks-tights": "calcetines: fuera del brief (1)",
        "/baby/newborn/clothing/view-all": "«Ver todo»: la rama entera (1)",
        "/baby/newborn/outerwear": "abrigo: fuera del brief (4)",
    },
    "springfield": {
        "nina/complementos": "complementos: ni ropa ni calzado (52)",
        "nino/complementos": "complementos: ni ropa ni calzado (36)",
        "nina/bano": "baño no es ninguna de las 5 categorías del brief (20)",
        "nino/bano": "baño no es ninguna de las 5 categorías del brief (21)",
        "nina/chaquetas": "chaqueta no es ninguna de las 5 categorías del brief (19)",
        "nino/chaquetas": "chaqueta no es ninguna de las 5 categorías del brief (17)",
        "nina/abrigos": "abrigo no es ninguna de las 5 categorías del brief (9)",
        "nino/abrigos": "abrigo no es ninguna de las 5 categorías del brief (5)",
        "nino/chalecos": "chaleco no es ninguna de las 5 categorías del brief (2)",
        "nina/promociones": "«Promociones» es una vista transversal, no una categoría (14)",
        # Se probó a ingerirla en #192, por parecerse al `TOTAL LOOK` de Zara, y la medición dijo
        # que no: son páginas «Shop the look» sin `ld+json`, sin tallas y sin precio, no fichas de
        # producto. Ojo con el parecido, que sigue sin serlo aunque desde #200 la de Zara sí entre
        # (filtrada por `familyName`): allí hay fichas de producto y aquí no hay ninguna, así que
        # ningún filtro arregla esto. Ver la cabecera de `springfield.py`.
        "nina/total-looks": "«Shop the look»: página sin ficha, no un producto — medido (2)",
        # `nina/pijamas` y `nino/pijamas` estuvieron aquí, declaradas como la incoherencia que eran.
        # Resueltas en #187: el pijama entra por `ropa-interior`, como en las otras cuatro tiendas.
    },
    # Lefties enumera desde #179, la última de las nueve. Son 42 declaraciones para 273 rutas bajo
    # una sola raíz (el departamento `Niños`; el porqué de una y no cinco está en `tree_roots()`).
    #
    # La ruta es la **cadena de ids** desde la raíz, como en Zara: los ids de esta tienda son
    # opacos y no se anidan solos (ver `lefties.parse_category_tree`). Para que se puedan leer, el
    # motivo lleva el rótulo de la tienda y su `key`, que es lo que hace reconocible el nodo.
    #
    # Y llevan **cifras medidas, no el nombre de la hoja**: el 05/08/2026 se pidió el listado de
    # cada una de las 42 y se cruzó contra los 755 productos que ingerimos hoy. Es la trampa que el
    # ADR documenta desde #175 —el nombre de una hoja no dice qué hay dentro— y aquí volvió a
    # pagar: «Licencias» suena a camisetas de personaje y sus 137 exclusivos son gorras, mochilas y
    # collares (muestra de 20). El formato es (total / ya entran / exclusivos).
    "lefties": {
        # --- niña, ropa (3_NA_*) ---
        "1030267671/1030267672/1030267676": (
            "«NOVEDADES» (3_NA_T_NEWIN): vista transversal; 233/141/92, y sus exclusivos son "
            "complemento y abrigo (muestra de 20)"
        ),
        "1030267671/1030267672/1030267677/1030267699": (
            "«Denim» (3_NA_T_DENIM): 39/36/3, ya entra por `jeans`, que sí está mapeada"
        ),
        "1030267671/1030267672/1030267677/1030267705": (
            "«Cazadoras» (3_NA_T_ABRIGOS_8): no es del brief; 17/5/12"
        ),
        "1030267671/1030267672/1030267677/1030267708": (
            "«Licencias» (3_NA_T_LICENCIAS): vista transversal de personaje; 263/126/137, y sus "
            "exclusivos son gorras, mochilas y collares (muestra de 20)"
        ),
        "1030267671/1030267672/1030267677/1030267715": (
            "«Bañadores | Bikinis» (3_NA_T_BANO): baño no es del brief; 60/2/58"
        ),
        "1030267671/1030267672/1030267677/1030439308": (
            "«Conjuntos» (3_NA_T_CONJUNTOS): 101/99/2 — aquí el conjunto YA entra por la prenda "
            "que lo compone, que es una de las opciones que #192 tiene abiertas"
        ),
        "1030267671/1030267672/1030267677/1030555692": (
            "«Chándal» (3_NA_T_CHANDAL): 32/32/0, entra entero por `pantalones` y `sudaderas`"
        ),
        "1030267671/1030267672/1030267677/1030684130": (
            "«Jerséis | Cárdigans» (3_NA_T_JERSEY_CARDIGANS): 4/4/0, entra por `punto`"
        ),
        "1030267671/1030267672/1030267716": (
            "«ACCESORIOS» (3_NA_T_ACCESORIOS): ni ropa ni calzado; 213/1/212"
        ),
        "1030267671/1030267672/1030272176": (
            "«BÁSICOS DE TEMPORADA» (3_NA_T_BASICOS): vista transversal; 62/58/4"
        ),
        "1030267671/1030267672/1030485538": (
            "«ROPA INTERIOR | PIJAMAS» (3_NA_T_ROPAINTERIOR_PIJAMAS): cabecera de menú sin "
            "listado propio; sus hojas ya se ingieren"
        ),
        "1030267671/1030267672/1030546185": (
            "«BOLSOS | MOCHILAS» (3_NA_T_BOLSOSMOCHILAS_MENU): ni ropa ni calzado; 63/2/61"
        ),
        "1030267671/1030267672/1030729353": (
            "«K-POP DEMON HUNTERS» (3_NA_T_KPOP_8): colaboración de campaña; 19/19/0, entra "
            "entera por su categoría"
        ),
        # --- niña, zapatería (3_NA_T_ZAPATOS_*) ---
        "1030267671/1030267672/1030267718/1030272299": (
            "«Ver Todo» (3_NA_T_ZAPATOS_VIEWALL): la vista completa de la rama; 105/96/9, y 7 de "
            "los 9 exclusivos son mochilas y estuches que la tienda cuelga aquí"
        ),
        "1030267671/1030267672/1030267718/1030272300": (
            "«Novedades» (3_NA_T_ZAPATOS_NOVEDADES): vista transversal; 31/29/2"
        ),
        "1030267671/1030267672/1030267718/1030272303": (
            "«Licencias» (3_NA_T_ZAPATOS_LICENCIAS_8): vista transversal; 33/29/4"
        ),
        "1030267671/1030267672/1030267718/1030272306": (
            "«Baño» (3_NA_T_ZAPATOS_BANO_8): 10/10/0, entra entera por `sandalias`"
        ),
        "1030267671/1030267672/1030267718/1030293050": (
            "«Promoción» (3_NA_T_ZAPATOS_PROMO): vista transversal; 37/36/1"
        ),
        "1030267671/1030267672/1030267718/1030421806": (
            "«Rebajas» (3_NA_T_ZAPATOS_REBAJAS_ES): vista transversal; 1/0/1"
        ),
        # --- niño, ropa (3_NO_*) ---
        "1030267671/1030267673/1030269021": (
            "«NOVEDADES» (3_NO_T_NEWIN): vista transversal; 208/125/83, misma composición que la "
            "de niña"
        ),
        "1030267671/1030267673/1030269022/1030267824": (
            "«Denim» (3_NO_T_DENIM): 24/23/1, ya entra por `jeans`, que sí está mapeada"
        ),
        "1030267671/1030267673/1030269022/1030267829": (
            "«Cazadoras» (3_NO_T_ABRIGOS_8): no es del brief; 12/2/10"
        ),
        "1030267671/1030267673/1030269022/1030267832": (
            "«Licencias» (3_NO_T_LICENCIAS): vista transversal de personaje; 185/105/80, misma "
            "composición que la de niña"
        ),
        "1030267671/1030267673/1030269022/1030267839": (
            "«Bañadores» (3_NO_T_BANO): baño no es del brief; 38/9/29"
        ),
        "1030267671/1030267673/1030269022/1030439309": (
            "«Conjuntos» (3_NO_T_CONJUNTOS): 64/64/0, entra entero por la prenda que lo compone "
            "(#192)"
        ),
        "1030267671/1030267673/1030269022/1030556189": (
            "«Chándal» (3_NO_T_CHANDAL): 21/21/0, entra entero por `pantalones` y `sudaderas`"
        ),
        "1030267671/1030267673/1030267840": (
            "«ACCESORIOS» (3_NO_T_ACCESORIOS): ni ropa ni calzado; 120/0/120"
        ),
        "1030267671/1030267673/1030272248": (
            "«BÁSICOS DE TEMPORADA» (3_NO_T_BASICOS): vista transversal; 76/69/7"
        ),
        "1030267671/1030267673/1030487036": (
            "«ROPA INTERIOR | PIJAMAS» (3_NO_T_ROPAINTERIOR_PIJAMAS): cabecera de menú sin "
            "listado propio; sus hojas ya se ingieren"
        ),
        "1030267671/1030267673/1030546685": (
            "«MOCHILAS | ESTUCHES» (3_NO_T_MOCHILAS_MENU): ni ropa ni calzado; 33/1/32"
        ),
        # --- niño, zapatería (3_NO_T_ZAPATOS_*) ---
        "1030267671/1030267673/1030267842/1030272324": (
            "«Ver Todo» (3_NO_T_ZAPATOS_VIEWALL): la vista completa de la rama; 93/82/11, con la "
            "misma mezcla de mochilas que en niña"
        ),
        "1030267671/1030267673/1030267842/1030272325": (
            "«Novedades» (3_NO_T_ZAPATOS_NOVEDADES): vista transversal; 24/24/0"
        ),
        "1030267671/1030267673/1030267842/1030272328": (
            "«Licencias» (3_NO_T_ZAPATOS_LICENCIAS): vista transversal; 27/23/4"
        ),
        "1030267671/1030267673/1030267842/1030272331": (
            "«Baño» (3_NO_T_ZAPATOS_BANO_8): 15/15/0, entra entera por `sandalias`"
        ),
        "1030267671/1030267673/1030267842/1030293053": (
            "«Promoción» (3_NO_T_ZAPATOS_PROMO): vista transversal; 25/25/0"
        ),
        # --- Las cinco que NO son decisión firme (mismo formato que #187 en springfield) --------
        #
        # Las tres ramas de bebé son catálogo del brief entero sin ingerir: **391 productos
        # distintos, 0 de ellos ingeridos por otra rama** (Bebé Niña 220, Bebé Niño 206, Recién
        # Nacido 86, medido el 05/08/2026 sobre las 82 hojas con producto). Es la sexta vez que
        # mirar un árbol destapa catálogo del brief, y la misma forma que el departamento de bebé
        # de Zara (#186). Se declaran para que el vigía no las cante cada jueves mientras se
        # decide; el día que se resuelvan, estas entradas se van y las hojas pasan a `CATEGORIES`.
        # Tienen issue propia pendiente de abrir: el número se escribe aquí al abrirla.
        "1030267671/1030267674": (
            "SIN DECIDIR — «Bebé Niña» (LEFTIES_BABYGIRL): rama entera sin ingerir, 220 "
            "productos y 0 que entren por otra rama"
        ),
        "1030267671/1030267675": (
            "SIN DECIDIR — «Bebé Niño» (LEFTIES_BABYBOY): rama entera sin ingerir, 206 productos "
            "y 0 que entren por otra rama"
        ),
        "1030267671/1030513546": (
            "SIN DECIDIR — «Recién Nacido» (LEFTIES_BABYGROW): rama entera sin ingerir, 86 "
            "productos y 0 que entren por otra rama"
        ),
        # Las dos hojas `REBAJAS HASTA -70%` ya NO se declaran aquí: se ingieren desde #195, con la
        # categoría derivada por producto (`lefties._FAMILIA_A_DOMINIO`). Lo que las sacó de esta
        # lista es que el 0 de solape resultó ser de temporada y no de campaña: las 38 hojas
        # mapeadas van enteras en `I2026` y las de rebajas enteras en `V2026`.
        #
        # Y la ropa deportiva, que ya tiene issue: #180. Esta tienda añade su tercer dato y va en
        # la misma dirección que Sfera y C&A —el eje «deportivo» es transversal, no una
        # categoría—: de los 146 productos de las dos ramas, **130 ya entran** por `camisetas`,
        # `pantalones` y `sudaderas`, y los 16 exclusivos se reparten 8 y 8.
        "1030267671/1030267672/1030267677/1030267709": (
            "SIN DECIDIR — «Ropa Deportiva» (3_NA_T_ROPADEPORTIVA): 77/69/8, vista transversal; "
            "la decisión es la de #180"
        ),
        "1030267671/1030267673/1030269022/1030267833": (
            "SIN DECIDIR — «Ropa Deportiva» (3_NO_T_ROPADEPORTIVA): 69/61/8, misma lectura (#180)"
        ),
    },
}

# Cuántos productos se llevan hasta el final (listado -> detalle -> parseo) por tienda. Cinco basta
# para saber que la cadena entera sigue produciendo productos con variantes y precios, y acota el
# gasto: en Zara, que pide el detalle de uno en uno, son cinco peticiones y no 2219.
MUESTRA_POR_DEFECTO = 5


@dataclass(frozen=True)
class Medida:
    """Lo que tardó una capa y sobre cuántas unidades, que es lo que la hace comparable.

    Un absoluto por tienda envejece mal —los catálogos crecen— así que lo que se compara entre
    semanas es `por_unidad`: segundos por hoja sondeada, segundos por producto pedido.
    """

    segundos: float
    unidades: int
    unidad: str

    @property
    def por_unidad(self) -> float | None:
        """`None` cuando no llegó a cubrirse ni una unidad: no hay ritmo que calcular."""
        return self.segundos / self.unidades if self.unidades else None

    def render(self) -> str:
        if self.por_unidad is None:
            return f"{_duracion(self.segundos)} (sin unidades)"
        ritmo = f"{_numero(self.por_unidad)} s/{self.unidad}"
        return f"{_duracion(self.segundos)} ({ritmo} · {self.unidades})"


@dataclass
class Informe:
    """Lo que el vigía tiene que contar de UNA tienda.

    Separa `accionable` de `aviso` por la misma razón que `--check-categories`: un vigía que da
    falsas alarmas rutinarias acaba silenciado, que es peor que no tenerlo. Solo lo accionable
    —algo que alguien puede arreglar— sale != 0 y abre issue; lo demás se cuenta y se sigue.
    """

    slug: str
    lineas: list[str] = field(default_factory=list)
    accionables: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    tiempos: dict[str, Medida] = field(default_factory=dict)
    # Aparte de `lineas` solo por el orden del informe: la comparación con el histórico se lee
    # justo debajo de los tiempos, que es lo que explica.
    comparaciones: list[str] = field(default_factory=list)

    @property
    def esta_bien(self) -> bool:
        return not self.accionables

    @property
    def segundos(self) -> float:
        return sum(m.segundos for m in self.tiempos.values())

    def render(self) -> str:
        partes = [f"## {self.slug}", *self.lineas]
        if self.tiempos:
            capas = " · ".join(f"{capa} {m.render()}" for capa, m in self.tiempos.items())
            partes.append(f"tiempos: {capas} · total {_duracion(self.segundos)}")
        partes += self.comparaciones
        partes += [f"✖ {motivo}" for motivo in self.accionables]
        partes += [f"⚠ {motivo}" for motivo in self.avisos]
        return "\n".join(partes)


@contextmanager
def _cronometrar(
    informe: Informe, capa: str, unidad: str, contar: Callable[[], int]
) -> Iterator[None]:
    """Anota en el informe lo que tardó el bloque, pase lo que pase dentro.

    El `finally` no es celo: una capa que revienta a mitad **conserva lo medido hasta ahí**, y
    «murió tras 12 hojas en 8 min» es justo el dato que hace falta cuando una tienda nos empieza a
    regular el paso. `contar` se llama al final para que las unidades sean las que se llegaron a
    cubrir y no las que se esperaban.
    """
    inicio = _reloj()
    try:
        yield
    finally:
        informe.tiempos[capa] = Medida(_reloj() - inicio, contar(), unidad)


def revisar_hojas(store: BaseStore, informe: Informe) -> None:
    """Capa 1: ¿siguen existiendo las hojas de categoría configuradas?

    La política de veredicto vive aquí y `run.py --check-categories` la reutiliza, para que no haya
    dos copias de la misma regla (el repo ya paga ese precio en otro sitio, ver #39):

    - Una hoja **RETIRADA** es accionable: pide un id nuevo en `CATEGORIES`.
    - Una hoja **RETIRADA Y ESTACIONAL** solo avisa: es una hoja de campaña apagada, y su id vuelve
      cuando vuelve la campaña (`LeafHealth.estacional`). Pedir un id nuevo cada semana por algo que
      se cura solo es la forma más rápida de que nadie se crea al vigía (#176, #195).
    - Una hoja **SIN VEREDICTO** avisa pero no rompe: medido contra Sfera, un chequeo normal ya trae
      un 403 suelto de Akamai.
    - Que **ninguna** hoja se confirme viva sí rompe: eso ya no es un blip, es un bloqueo. Es la
      forma en la que se vería una regresión de la huella TLS, que devolvería 429 en todas.
    """
    if not isinstance(store, SupportsLeafHealth):
        motivo = SIN_VIGILANCIA_DE_HOJAS.get(store.slug)
        if motivo:
            informe.lineas.append(f"hojas: sin sondeo por decisión ({motivo})")
        else:
            informe.accionables.append(
                "sin vigilancia de hojas: no implementa `SupportsLeafHealth`, así que una "
                "categoría caducada dejaría de ingerirse sin que nadie se entere (ver "
                "`check_leaves()` en stores/base.py)"
            )
        return

    # Los contadores viven fuera del `with` para que el cronómetro pueda contar las hojas que se
    # llegaron a sondear aunque `check_leaves()` reviente a mitad.
    vivas = 0
    retiradas: list[LeafHealth] = []
    estacionales: list[LeafHealth] = []
    sin_veredicto: list[LeafHealth] = []

    def sondeadas() -> int:
        return vivas + len(retiradas) + len(estacionales) + len(sin_veredicto)

    with _cronometrar(informe, "hojas", "hoja", sondeadas):
        for hoja in store.check_leaves():
            if hoja.alive:
                vivas += 1
            elif hoja.alive is False:
                (estacionales if hoja.estacional else retiradas).append(hoja)
            else:
                sin_veredicto.append(hoja)

    total = sondeadas()
    informe.lineas.append(f"hojas: {vivas}/{total} vivas")
    if retiradas:
        informe.accionables.append(
            f"{len(retiradas)} hoja(s) RETIRADA(S) — busca sus ids nuevos y actualiza CATEGORIES:\n"
            + "\n".join(f"  - {_describe(h)}" for h in retiradas)
        )
    if estacionales:
        informe.avisos.append(
            f"{len(estacionales)} hoja(s) de campaña apagada(s) (no es una retirada: su id vuelve "
            "con la campaña, ver `LeafHealth.estacional`):\n"
            + "\n".join(f"  - {_describe(h)}" for h in estacionales)
        )
    if total and not vivas:
        informe.accionables.append(
            "ninguna hoja confirmada viva: esto no es un blip, es un bloqueo. Si el detalle dice "
            "429 `local_rate_limited`, mira primero la huella TLS (scraper/tls.py); esa marca "
            "también la trae el 429 por ritmo de la tienda, así que no la des por sentada (#120)."
        )
    if sin_veredicto:
        informe.avisos.append(
            f"{len(sin_veredicto)} hoja(s) sin veredicto (fallo del sondeo, no retirada):\n"
            + "\n".join(f"  - {_describe(h)}" for h in sin_veredicto)
        )


def revisar_parseo(store: BaseStore, informe: Informe, muestra: int) -> None:
    """Capa 2: la cadena entera —listado, detalle, parseo— sigue produciendo productos usables.

    Genérica a propósito: se apoya solo en `BaseStore`, así que **cubre también a las tiendas que
    todavía no existen**. Es lo que hoy comprueban a mano `test_sfera_live.py` y
    `test_cacles_live_acepta_nuestra_huella`, pero sin código por tienda que alguien tenga que
    acordarse de escribir.

    `islice` sobre el generador y no `list()`: las cuatro tiendas emiten perezosamente, así que
    cortar a los `muestra` primeros para el recorrido en la primera hoja en vez de barrer el
    catálogo entero.
    """
    # La unidad de esta capa es el producto **pedido**, no el emitido: lo que fija el coste es
    # cuántas fichas se piden, y así el ritmo sigue siendo comparable la semana que la tienda deje
    # de emitir alguno.
    entradas: list[ListingEntry] = []
    productos: list[ScrapedProduct] = []

    with _cronometrar(informe, "parseo", "producto", lambda: len(entradas)):
        entradas = list(itertools.islice(store.list_catalog(), muestra))
        if entradas:
            productos = list(store.fetch_details(entradas))

    if not entradas:
        informe.accionables.append(
            "el listado no devolvió ni una entrada: o la tienda nos ha cerrado la puerta o el "
            "endpoint de catálogo ha cambiado de forma"
        )
        return

    if not productos:
        informe.accionables.append(
            f"{len(entradas)} entradas en el listado pero ningún producto con detalle: el parseo "
            "se ha quedado sin nada que emitir"
        )
        return

    variantes = sum(len(p.variants) for p in productos)
    if not variantes:
        informe.accionables.append(
            f"{len(productos)} productos sin una sola variante: sin talla/color no hay nada que "
            "seguir ni precio que registrar"
        )
        return

    sin_precio = [v for p in productos for v in p.variants if v.price <= 0]
    if sin_precio:
        informe.accionables.append(
            f"{len(sin_precio)}/{variantes} variantes con precio <= 0: el precio ha cambiado de "
            "sitio o de unidad en el JSON de la tienda"
        )
        return

    informe.lineas.append(
        f"parseo: {len(entradas)} entradas -> {len(productos)} productos, {variantes} variantes, "
        "precios > 0"
    )


def cubierta(ruta: str, cubren: Iterable[str], sep: str) -> bool:
    """¿Está `ruta` contada ya, por debajo o por encima? (ver `SupportsCategoryTree.tree_separator`)

    Pura y con el separador explícito para poder testear la trampa sin red: a prefijo pelado,
    `3-1-11` colgaría de `3-1-1` y las dos son hojas hermanas que ingerimos por separado.

    Son **dos** relaciones, y las dos significan «esto no es un hueco»:

    - `ruta` cuelga de una cubierta — sus productos entran por el padre, que ya ingerimos. Es lo
      que midió #179 en C&A: 53 de 122 rutas eran subcategorías de hojas de `CATEGORIES`.
    - `ruta` es **antepasada** de una cubierta — su catálogo entra por sus hijas. `/kids/boys/
      clothing` no es catálogo que nos falte: es el cajón donde están `trousers`, `jeans` y
      `nightwear`, que sí ingerimos.

    La segunda apareció con H&M (#179), la primera tienda de taxonomía en tres niveles
    (rama/sección/hoja): Sfera y Springfield cuelgan las hojas directamente de la raíz, así que
    nunca emiten un nodo intermedio. Sin ella eran 7 nodos señalados cada jueves como huecos que no
    lo son, y es la misma familia de ruido que el separador vino a quitar — un informe que señala
    lo que ya ingerimos no lo lee nadie dos veces.

    Ojo a lo que **no** hace: silencia el nodo intermedio, nunca a sus hijas. Una hoja nueva bajo
    `/kids/boys/clothing` no cuelga de ninguna cubierta ni es antepasada de ninguna, así que sigue
    saliendo — que es justo para lo que existe esta capa.

    Las dos direcciones se separan en `cuelga_de` porque **no siempre valen las dos**: reducir el
    informe a las rutas maximales pregunta solo por la primera, y con la segunda se comería el
    hallazgo entero (una rama sin cubrir es antepasada de sus propias hijas sin cubrir).
    """
    return cuelga_de(ruta, cubren, sep) or any(c.startswith(ruta + sep) for c in cubren)


def cuelga_de(ruta: str, otras: Iterable[str], sep: str) -> bool:
    """¿Es `ruta` una de `otras` o desciende de alguna? Solo hacia abajo (ver `cubierta`)."""
    return any(ruta == c or ruta.startswith(c + sep) for c in otras)


def revisar_cobertura(store: BaseStore, informe: Informe) -> None:
    """Capa 3: ¿publica la tienda categorías que NO estamos ingiriendo? (#156)

    La simétrica de `revisar_hojas`, y la que faltaba. Aquella itera `CATEGORIES`, o sea lo que ya
    tenemos mapeado, así que responde «¿sigue viva la hoja que ingerimos?» y es ciega a la otra
    mitad: una categoría nueva, o una de temporada que vuelve —`ninos/bebe-nino/punto-y-jerseis` se
    retiró en julio y #151 la quitó de `CATEGORIES`; si la tienda la republica en otoño, nadie la
    sonda—. El resultado es catálogo que existe y no ingerimos sin que nadie se entere: medido en
    Sfera, 31 prendas entre las dos `ropa-deportiva` de bebé.

    **Lo no mapeado es accionable salvo que esté declarado** en `COBERTURA_DECLARADA`. Es lo
    contrario del criterio de las otras capas —allí lo dudoso avisa y solo lo cierto rompe— y es a
    propósito: `main()` únicamente publica en GitHub las tiendas con accionables, así que un aviso
    se quedaría en el log del pod, que es exactamente el punto ciego que esta capa viene a tapar.
    Lo que evita la alarma semanal no es bajar el veredicto, es que la excepción sea explícita.

    **Una tienda que no sabe enumerarse no es un hallazgo.** Hoy lo implementan 7 de 9 (#179 subió
    de 3 a 7: springfield, zara, cacles y hm), pero exigirlo —como sí se exige `check_leaves()`—
    seguiría siendo caro donde no hay por dónde: en Hipercor el árbol vive bajo la ruta que su
    `robots.txt` veta. Se anota como línea y se sigue.
    """
    if not isinstance(store, SupportsCategoryTree):
        informe.lineas.append("cobertura: no la sabe enumerar (no implementa SupportsCategoryTree)")
        return
    motivo = COBERTURA_SIN_VIGILAR.get(store.slug)
    if motivo:
        informe.lineas.append(f"cobertura: sin vigilar por decisión ({motivo})")
        return
    if not isinstance(store, SupportsCoverageWatch):
        informe.accionables.append(
            "sabe enumerar su árbol pero no se le puede vigilar la cobertura: implementa "
            "`SupportsCoverageWatch` (tree_roots) en la tienda, o declara el motivo en "
            "`COBERTURA_SIN_VIGILAR` de scraper/vigia.py"
        )
        return

    sep = store.tree_separator()
    raices = list(store.tree_roots())
    cubren = set(store.mapped_leaves()) | set(COBERTURA_DECLARADA.get(store.slug, {}))
    nodos: list[CategoryNode] = []
    rotas: list[str] = []

    with _cronometrar(informe, "cobertura", "nodo", lambda: len(nodos)):
        for raiz in raices:
            try:
                # Se acumula a medida que llega y no con `list(...)`: en estas tiendas un 403 suelto
                # de Akamai es rutina, y `list` no devuelve nada parcial si el generador revienta a
                # mitad. Perder el barrido entero —y las peticiones que costó— por el último tramo
                # sería lo contrario de para lo que sirve. Mismo criterio que `run._tree()`.
                for nodo in store.category_tree(raiz):
                    nodos.append(nodo)
            except Exception as exc:  # red o bloqueo: se anota y siguen las demás raíces
                rotas.append(f"{raiz} — {type(exc).__name__}: {exc}")

    # Una ruta puede salir por dos raíces distintas si los árboles se solapan; cuenta una vez. Y la
    # raíz nunca se señala a sí misma: es lo que hemos preguntado, no un hallazgo — aunque alguna
    # tienda se emita a sí misma al pedir su propio árbol.
    rutas = {n.path: n for n in nodos if n.path not in raices}
    sin_cubrir = {p: n for p, n in rutas.items() if not cubierta(p, cubren, sep)}

    informe.lineas.append(
        f"cobertura: {len(rutas)} ruta(s) publicadas bajo {len(raices)} raíz/raíces, "
        f"{len(rutas) - len(sin_cubrir)} ingeridas, colgando de una ingerida o declaradas"
    )
    if sin_cubrir:
        # Solo las maximales: si `Baño` no está cubierta, sus tres hijas tampoco, y nombrarlas
        # todas convertiría un hallazgo en una parrafada. Se señala la rama y quien la atienda
        # decide sobre ella entera.
        #
        # `cuelga_de` y no `cubierta`: aquí la pregunta es solo «¿desciende de otra sin cubrir?».
        # Con la regla de antepasados de `cubierta`, una rama quedaría tapada por sus propias
        # hijas —y ellas por ella— y no sobreviviría ninguna.
        maximales = [
            n
            for p, n in sorted(sin_cubrir.items())
            if not cuelga_de(p, sin_cubrir.keys() - {p}, sep)
        ]
        informe.accionables.append(
            f"{len(maximales)} categoría(s) publicadas y SIN cubrir — decide si se ingieren "
            "(añádelas a CATEGORIES) o no (declara el motivo en COBERTURA_DECLARADA de "
            "scraper/vigia.py):\n" + "\n".join(f"  - {_describe_nodo(n)}" for n in maximales)
        )
    if rotas:
        informe.avisos.append(
            f"{len(rotas)} raíz/raíces del árbol sin recorrer (fallo del barrido, no cobertura):\n"
            + "\n".join(f"  - {r}" for r in rotas)
        )


def revisar_tienda(slug: str, config: Config, muestra: int) -> Informe:
    """Las tres capas sobre una tienda. Nunca eleva: un fallo inesperado ES el hallazgo."""
    informe = Informe(slug)
    try:
        store = get_store(slug, config)
    except Exception as exc:  # config imposible, dependencia que falta al arrancar...
        informe.accionables.append(f"no se ha podido construir el scraper: {exc!r}")
        return informe

    # Una excepción aquí no es un error del vigía, es el hallazgo: se anota y se sigue con las
    # demás tiendas, que una reviente no puede dejar a las otras sin mirar.
    try:
        revisar_hojas(store, informe)
    except Exception as exc:
        informe.accionables.append(f"el sondeo de hojas reventó — {type(exc).__name__}: {exc}")
        # Y NO se sigue con el parseo. Medido con Lefties sin Chromium instalado: el fallo de la
        # primera capa deja el navegador a medio arrancar y el de la segunda sale distinto y
        # engañoso («usa la API async»), tapando la causa real con un síntoma derivado. Un vigía
        # que apunta a la pista falsa es peor que uno que dice una sola cosa cierta.
        informe.avisos.append("parseo y cobertura omitidos: el sondeo de hojas ya falló")
        return informe
    try:
        revisar_parseo(store, informe, muestra)
    except Exception as exc:
        informe.accionables.append(f"el smoke de parseo reventó — {type(exc).__name__}: {exc}")
    # La cobertura sí se intenta aunque el parseo haya fallado: son preguntas independientes —el
    # árbol se lee del menú o de la faceta, no del listado— y saber que la tienda publica una
    # categoría nueva sigue valiendo aunque hoy el detalle no se deje parsear.
    try:
        revisar_cobertura(store, informe)
    except Exception as exc:
        informe.accionables.append(f"el barrido de cobertura reventó — {type(exc).__name__}: {exc}")
    return informe


def comparar_con_base(informe: Informe, bases: dict[str, Base | None], factor: float) -> None:
    """Capa 3: ¿nos están dejando entrar al ritmo de siempre? (#111)

    Pura y sin BD a propósito — la línea base se lee fuera y entra por parámetro— porque la regla
    es lo único que hay que poder testear sin Postgres.

    **Avisa, nunca acciona**, y eso no es timidez: `main()` solo publica en GitHub las tiendas con
    accionables, así que una tienda verde pero lenta se lee en el log y no abre issue. Un número
    lento suelto no es accionable —puede ser el nodo, puede ser el jueves— mientras que la serie sí
    lo será; promoverlo es una decisión para cuando haya varias semanas de histórico.

    Sin línea base no se compara y se dice, que es lo que evita confundir «va bien» con «no lo he
    mirado» durante las primeras semanas.
    """
    comparadas: list[str] = []
    sin_base: list[str] = []
    for capa, medida in informe.tiempos.items():
        ritmo = medida.por_unidad
        base = bases.get(capa)
        if ritmo is None:
            continue
        if base is None:
            sin_base.append(capa)
            continue
        veces = ritmo / base.mediana if base.mediana else 0.0
        comparadas.append(f"{capa} {_numero(base.mediana)} s/{medida.unidad} (×{_numero(veces)})")
        if veces >= factor:
            muestras = " ".join(_numero(m) for m in base.muestras)
            informe.avisos.append(
                f"{capa}: {_numero(ritmo)} s/{medida.unidad} contra una línea base de "
                f"{_numero(base.mediana)} — ×{_numero(veces)}. La puerta sigue abierta, pero nos "
                f"están dejando entrar más despacio (mediana de {len(base.muestras)}: {muestras})"
            )
    if comparadas:
        informe.comparaciones.append("base: " + " · ".join(comparadas))
    if sin_base:
        informe.comparaciones.append(
            f"base: sin línea base ({', '.join(sin_base)}) — no se compara"
        )


def _numero(valor: float) -> str:
    """Un decimal y coma, como se escriben las medidas en las issues de este repo."""
    return f"{valor:.1f}".replace(".", ",")


def _duracion(segundos: float) -> str:
    """Legible de un vistazo: `13,2 s` para lo corto, `24m 28s` a partir del minuto.

    El corte se decide sobre el valor **ya redondeado** para que 59,96 s salga como `1m 00s` y no
    como el `60,0 s` que nadie escribe.
    """
    if round(segundos, 1) < 60:
        return f"{_numero(segundos)} s"
    minutos, resto = divmod(round(segundos), 60)
    return f"{minutos}m {resto:02d}s"


def _describe(hoja: LeafHealth) -> str:
    ambito = f"{hoja.scope.gender}/{hoja.scope.section}/{hoja.scope.category}"
    return f"{hoja.leaf} ({ambito}) {hoja.detail}"


def _describe_nodo(nodo: CategoryNode) -> str:
    """Una categoría sin mapear, con lo justo para decidir sin volver a pedir el árbol.

    El conteo va entre paréntesis pero es **lo que la tienda declara**, que no es lo que sirve:
    medido en Sfera, la faceta decía 8 en `leggings` y el listado dio 18 (ver `CategoryNode`).
    Orienta sobre el peso; no vale para decidir si la hoja merece la pena — para eso hay que pedir
    el listado. `?` es «no lo dice», distinto de 0.
    """
    cuenta = "?" if nodo.count is None else str(nodo.count)
    hijos = ", con hijas" if nodo.has_children else ""
    return f"{nodo.path} ({cuenta}) «{nodo.title}»{hijos}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scraper.vigia",
        description="Smoke en vivo de todas las tiendas: avisa antes de que falle el CronJob",
    )
    parser.add_argument(
        "--retailer",
        help=f"revisa solo esta tienda ({', '.join(available_slugs())}); por defecto, todas",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="informa por consola sin abrir issue en GitHub ni guardar la medida en `vigia_run`",
    )
    parser.add_argument(
        "--muestra",
        type=int,
        default=MUESTRA_POR_DEFECTO,
        help=f"productos llevados hasta el parseo por tienda (por defecto {MUESTRA_POR_DEFECTO})",
    )
    return parser.parse_args(argv)


def informar(informes: Iterable[Informe]) -> str:
    """Cuerpo del informe, el mismo que se imprime y que se manda a la issue."""
    return "\n\n".join(inf.render() for inf in informes)


def _medidas(informes: Iterable[Informe]) -> list[tuple[str, str, float, int]]:
    """Las medidas en la forma plana que persiste `vigia_historial`, para que ese módulo no tenga
    que saber qué es un `Informe`."""
    return [
        (inf.slug, capa, m.segundos, m.unidades)
        for inf in informes
        for capa, m in inf.tiempos.items()
    ]


def _resumen_de_tiempos(informes: Sequence[Informe]) -> str:
    total = sum(inf.segundos for inf in informes)
    lenta = max(informes, key=lambda inf: inf.segundos)
    return f"⏱ total {_duracion(total)} — la más lenta: {lenta.slug} ({_duracion(lenta.segundos)})"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    config = Config.from_env()

    slugs = [args.retailer] if args.retailer else available_slugs()
    # El historial se abre antes de sondear porque la línea base tiene que estar en el informe que
    # se publica, no en un segundo pase. Si no está, `Historial` se queda inerte y todo lo demás
    # sigue igual: el veredicto no puede depender de que haya base de datos.
    historial = Historial.abrir(config)
    try:
        informes = []
        for slug in slugs:
            informe = revisar_tienda(slug, config, args.muestra)
            bases = {
                capa: historial.linea_base(slug, capa, config.vigia_base_muestras)
                for capa in informe.tiempos
            }
            comparar_con_base(informe, bases, config.vigia_factor_aviso)
            informes.append(informe)
        # No se guarda en `--dry-run`: la serie mezcla mal, y una ejecución desde un portátil
        # contra la misma base metería tiempos de fuera en la línea base del cluster — que es
        # justo la diferencia que esto existe para medir (×11,8 en Hipercor).
        if not args.dry_run:
            historial.guardar(_medidas(informes))
    finally:
        historial.cerrar()

    print(informar(informes))
    if informes:
        print(_resumen_de_tiempos(informes))
    if historial.motivo:
        print(f"(sin historial: {historial.motivo})")

    malas = [inf for inf in informes if not inf.esta_bien]
    if not malas:
        print(f"\n✔ {len(informes)} tienda(s) revisadas, todas nos dejan entrar.")
        return 0

    culpables = ", ".join(inf.slug for inf in malas)
    print(f"\n✖ {len(malas)}/{len(informes)} tienda(s) con algo que arreglar: {culpables}")
    if args.dry_run:
        print("(--dry-run: no se abre issue)")
        return 1

    aviso = AvisoGitHub.from_env()
    if aviso is None:
        # Igual que Keycloak y Telegram: sin configurar, apagado. Es la ruta de dev local.
        print("(sin VIGIA_GITHUB_TOKEN/VIGIA_GITHUB_REPO: no se abre issue)")
        return 1
    try:
        print(aviso.publicar(informar(malas)))
    except Exception as exc:
        # Que falle el aviso no puede tapar el hallazgo: ya está impreso arriba y el job sale != 0.
        print(f"⚠ el hallazgo no se pudo publicar en GitHub: {exc!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
