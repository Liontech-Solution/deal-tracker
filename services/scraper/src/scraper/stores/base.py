"""Contrato común para los scrapers por tienda (pluggable).

El scrapeo se hace en **dos fases** para poder ahorrar peticiones (detalle condicional):

  1. `list_catalog()` — barre las secciones y devuelve una `ListingEntry` por producto
     con una *huella* (`signature`) barata construida con lo que se ve en el listado
     (típicamente precio por color). Son pocas peticiones.
  2. `fetch_details(entries)` — pide el detalle completo (tallas, stock, sku) SOLO de los
     productos que la ingesta decide (nuevos o con la huella cambiada).

Así la ingesta (`ingest.py`) compara la huella contra la última conocida en BD y evita
la petición de detalle cuando nada ha cambiado. Los scrapers no tocan la BD: solo la red.

Opcionalmente una tienda puede implementar `SupportsAliveProbe` (confirmación activa antes
de dar de baja); las que no puedan lo omiten y la ingesta se queda con la histéresis. Y
`SupportsScanReport` para contar las hojas que se cayeron durante el listado sin abortar la
pasada (ver `ScanReport`), o `SupportsProductTags` para declarar los ejes transversales que la
tienda publica en su árbol (ver `ProductTags` y `scraper.tags`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ScrapedImage:
    """Una foto del producto, atribuida al color que retrata.

    `color` debe salir del MISMO campo que alimenta `ScrapedVariant.color`: es la clave por la
    que la ficha empareja foto y precio, y si los dos nombres se desalinean el emparejamiento
    falla en silencio. Un color sin variantes utilizables no debe aportar fotos.

    **El orden de la lista es el orden de la galería**; la posición dentro de cada color la
    asigna la ingesta. No es un detalle cosmético: una tienda puede exponer dos colores distintos
    con el MISMO nombre (visto en Lefties, dos "MARRON" con ids distintos), y si cada scraper
    numerase por su cuenta las dos series arrancarían en 0 y chocarían. Numerando en un único
    sitio, por nombre de color, ese caso se resuelve solo — que es además lo que la ficha quiere,
    porque agrupa por nombre y para el usuario esos dos marrones son el mismo color.

    `variant_url` es la excepción a ese «para el usuario son el mismo color», y solo la necesita
    quien tenga el problema de H&M (#123): allí un producto nuestro junta varios ARTÍCULOS de la
    tienda —agrupamos por la raíz del `articleId`— y dos de ellos pueden traer el mismo
    `colorName` siendo prendas distintas, cada una con su ficha y sus fotos. Con el color solo, la
    galería las mezcla y enseña la foto de una junto al precio de la otra, que es justo lo que la
    galería por color (#26) existía para evitar. Quien la rellene tiene que sacarla del MISMO
    campo que alimenta `ScrapedVariant.url`, por el mismo motivo que el color: la ficha empareja
    por ese valor. `None` —el default, y lo que dejan las otras seis tiendas— significa «esta
    tienda no distingue dos artículos bajo un mismo nombre de color», no «foto sin ficha».
    """

    color: str | None  # None = foto que no se puede atribuir a un color concreto
    url: str
    variant_url: str | None = None


@dataclass(frozen=True)
class ScrapedVariant:
    """Una variante concreta (talla/color) con su precio en el momento del scrape."""

    retailer_variant_id: str  # estable por tienda, independiente de temporada
    size: str | None
    color: str | None
    sku: str | None
    price: Decimal
    list_price: Decimal | None  # precio original/tachado, si la tienda lo expone
    in_stock: bool
    url: str | None = None
    # Mínimo de los últimos 30 días **declarado por la tienda** (directiva Ómnibus). No es una
    # observación nuestra: es lo que el retailer publica, y ahí está su valor — permite contrastar
    # nuestro histórico contra el suyo en vez de tener que creernos el precio tachado. `None` = esta
    # tienda no lo declara (hoy solo lo hace C&A), que NO es lo mismo que "no hubo mínimo".
    retailer_min_30d: Decimal | None = None


@dataclass(frozen=True)
class ScrapedProduct:
    """Un producto/modelo con todas sus variantes talla/color."""

    retailer_product_id: str  # identificador único y estable por tienda
    name: str
    gender: str | None  # niño | niña | unisex
    section: str | None  # ropa | zapateria
    category: str | None  # pantalones | camisetas | zapatos | ...
    url: str | None
    variants: list[ScrapedVariant]
    # Calzado respetuoso: 'si' | 'no' | 'desconocido', y None cuando no aplica (ropa). Lo decide
    # `scraper.barefoot.classify()` con lo que cada tienda sepa dar — su propia categoría barefoot
    # si la tiene, y si no la heurística de texto. Ver `0012_add_barefoot.sql`.
    barefoot: str | None = None
    # Foto primaria (la del primer color). Opcional: hay tiendas que no la exponen en lo que ya
    # pedimos, y una ficha sin foto vale más que una petición extra por producto.
    image_url: str | None = None
    # Galería completa, agrupada por color. Opcional igual que `image_url`: una tienda que no sepa
    # darla sigue siendo válida. Vacía NO significa "este producto no tiene fotos", significa "esta
    # pasada no trae información de fotos" — por eso la ingesta no borra la galería previa con ella.
    images: list[ScrapedImage] = field(default_factory=list)


@dataclass(frozen=True)
class ScrapeScope:
    """Un ámbito del catálogo que el scraper recorre (p.ej. niña/zapateria/zapatos).

    Es la unidad sobre la que se acota la detección de bajas: solo se dan de baja
    productos de ámbitos realmente escaneados en la pasada (ver `ingest.py`).
    """

    gender: str | None
    section: str | None
    category: str | None


def ambito_cruzado(hojas: Sequence[ScrapeScope]) -> ScrapeScope:
    """El ámbito de un producto a partir de las hojas en las que ha aparecido.

    **El género es `unisex` cuando el producto sale en hojas de géneros distintos**, que es lo que
    la tienda está diciendo al publicarlo en las dos ramas. El catálogo y el matching ya tratan
    `unisex` como «sale en niño y en niña» (`catalog/gender.sql.ts`, `matching.service.ts`), así que
    no hace falta vocabulario nuevo — solo detectar el cruce, que es lo que la #98 echó en falta.

    Una hoja ya declarada `unisex` (las de bebé, que no separan niño de niña) **no cuenta como
    género propio**: se descarta antes de mirar si hay cruce. Así un producto que sale en la hoja de
    niña y en la de bebé se queda en `niña`, que es la intención que Hipercor cuida a mano al poner
    sus hojas con género por delante de la de bebé.

    La sección y la categoría, en cambio, las fija **la primera hoja** que trajo el producto, como
    en el resto de tiendas. Uno que salga en `pantalones` de una rama y en `ropa-interior` de otra
    se queda con la primera: cruzar categorías es raro y no hay forma de decir «las dos».
    """
    generos = {h.gender for h in hojas}
    reales = generos - {"unisex"}
    primera = hojas[0]
    gender = "unisex" if len(reales) != 1 else reales.pop()
    return ScrapeScope(gender, primera.section, primera.category)


def genero_contrario(gender: str | None) -> str | None:
    """La otra rama del cruce de géneros: `niño`↔`niña`. `None` para todo lo demás.

    Ese "todo lo demás" incluye `unisex`, que no es una rama sino el resultado de cruzarlas, y el
    `None` de una tienda que no separe por género. Existe aquí, junto a `ambito_cruzado()`, porque
    es el mismo vocabulario: quién puede cruzarse con quién. `ingest.py` no tiene por qué saberlo.
    """
    contrarios = {"niño": "niña", "niña": "niño"}
    return contrarios.get(gender or "")


def con_unisex(scopes: Iterable[ScrapeScope]) -> list[ScrapeScope]:
    """Los ámbitos dados **más su equivalente `unisex`**, sin duplicar y en orden.

    Lo segundo no es cosmético en una tienda que aplica `ambito_cruzado()`: los productos que
    cruzan géneros se emiten como `unisex`, y un ámbito que no se declare aquí no cuenta como
    escaneado en `ingest.py`, así que **sus productos no se descatalogan nunca**. Es el mismo
    motivo por el que `cacles.py` declara el producto cartesiano de lo que su parser PUEDE emitir
    en vez de lo que dicen sus hojas.
    """
    declarados = list(scopes)
    unisex = [ScrapeScope("unisex", s.section, s.category) for s in declarados]
    return list(dict.fromkeys([*declarados, *unisex]))


@dataclass(frozen=True)
class ListingEntry:
    """Producto tal y como aparece en el listado, con una huella para detectar cambios."""

    retailer_product_id: str
    signature: str  # huella barata (p.ej. precio por color); si no cambia, no pedimos detalle
    gender: str | None
    section: str | None
    category: str | None

    @property
    def scope(self) -> ScrapeScope:
        return ScrapeScope(self.gender, self.section, self.category)


# Los únicos códigos que valen como "esta hoja ya no existe" y por tanto se toleran. Un 403 es un
# bloqueo y un 5xx un fallo del servidor (que además ya se reintenta con backoff): tragárselos
# convertiría un problema transitorio en un catálogo mutilado que se da por bueno.
GONE_STATUS = frozenset({404, 410})


@dataclass
class ScanReport:
    """Qué hojas del catálogo se pudieron listar en esta pasada y cuáles no.

    Una hoja retirada (404) no debe tumbar la pasada entera —lo hacía: `2428332` de Zara empezó
    a dar 404 cuatro días después de verificarla viva y las otras 46 hojas se quedaron sin
    ingerir, de forma determinista y silenciosa—, pero tampoco puede pasar inadvertida:

    - **Su ámbito deja de ser seguro para dar bajas.** Lo que no se ve en un ámbito cuya hoja no
      se ha podido mirar no está retirado: es que no se ha mirado. Y la red de seguridad por
      umbral no lo cubre, porque un ámbito alimentado por seis hojas solo pierde un 17 % de lo
      observado al caerse una — muy por debajo del 50 % que dispara la sospecha.
    - **La proporción importa.** Una hoja de 47 es una categoría que la tienda ha retirado; la
      mitad de ellas es un bloqueo o un cambio de API, y ahí la pasada sí debe abortar en vez de
      guardar un catálogo mutilado.
    """

    leaves_total: int = 0
    leaves_failed: int = 0
    failed_scopes: set[ScrapeScope] = field(default_factory=set)
    # Los IDENTIFICADORES de las hojas caídas, para poder nombrarlas después (#151). El ámbito no
    # las identifica: `niño/ropa/sudaderas` lo alimentan tres hojas en Sfera, así que saber que un
    # ámbito se cayó no dice cuál hay que ir a buscar al árbol de la tienda.
    #
    # Cada tienda escribe aquí el identificador en SU vocabulario, el mismo que pone en
    # `LeafHealth.leaf`: la ruta en Sfera e Hipercor, el `catalogId` en Mango, el `pageId` en H&M…
    # Que sea el mismo en los dos sitios es lo que hace que el vigía y la pasada nombren la misma
    # hoja de la misma manera cuando hay que ir a buscarla.
    failed_leaves: list[str] = field(default_factory=list)
    # Ámbitos cuyo GÉNERO no es de fiar en esta pasada (#172). No es lo mismo que `failed_scopes`:
    # estos se han listado perfectamente, y justo por eso son peligrosos. Al caerse la rama
    # complementaria, el producto que la tienda publica en las dos deja de cruzarse y se emite con
    # el género de la superviviente, o sea con el de ESTE ámbito, en vez de `unisex`. Medido en
    # Hipercor: al caerse `zapatos-infantiles/nino`, 32 productos pasaron de `unisex` a `niña`.
    #
    # Solo lo pueblan las tiendas que pasan `tambien_unisex=True`, que son exactamente las que
    # colapsan géneros con `ambito_cruzado()`. Una hoja ya `unisex` que se cae no lo toca: ahí no
    # hay ninguna rama superviviente que pueda mentir.
    cross_gender_suspect: set[ScrapeScope] = field(default_factory=set)

    def leaf_ok(self) -> None:
        """Registra una hoja listada con éxito."""
        self.leaves_total += 1

    def leaf_gone(self, scope: ScrapeScope, leaf: str, *, tambien_unisex: bool = False) -> None:
        """Registra una hoja que la tienda ya no sirve; su ámbito queda fuera de las bajas.

        `leaf` es el identificador de la hoja en el vocabulario de la tienda (ver
        `failed_leaves`): la pasada lo escribe en `scrape_run.message` para que la siguiente sesión
        no dependa del log del pod, que se recicla. **Es obligatorio, y esa es la gracia** (#155):
        nació opcional y ocho de las nueve tiendas se olvidaron de pasarlo, así que durante meses el
        mensaje decía cuántas hojas se habían caído pero no cuáles — que es justo el dato que hace
        falta. Un parámetro que el tipo exige no se puede olvidar; uno que se puede omitir, sí.

        `tambien_unisex` lo pasan las tiendas que resuelven el cruce de géneros con
        `ambito_cruzado()`: un producto que salía en las dos ramas deja de verse en las dos en
        cuanto cae una, y entonces se emitiría con el género de la rama superviviente en vez de
        `unisex`. Sin sacar también ese ámbito de las bajas, una hoja caída descatalogaría
        productos `unisex` que siguen perfectamente vivos.

        Ese ámbito extra **no cuenta como una hoja más**: sumarlo a `leaves_failed` inflaría
        `dead_ratio` y dispararía `SCRAPER_SCAN_MAX_DEAD_RATIO` antes de tiempo. Es una hoja
        caída, no dos. Por lo mismo tampoco añade un nombre a `failed_leaves`.

        Y anota **la rama contraria** en `cross_gender_suspect`, que es la otra consecuencia de la
        misma hoja caída y la que no bastaba con sacar de las bajas: esa sí se lista, y lo hace
        emitiendo con su género productos que son `unisex` (#172).
        """
        self.leaves_total += 1
        self.leaves_failed += 1
        self.failed_scopes.add(scope)
        self.failed_leaves.append(leaf)
        if tambien_unisex and scope.gender != "unisex":
            self.failed_scopes.add(ScrapeScope("unisex", scope.section, scope.category))
            contraria = genero_contrario(scope.gender)
            if contraria is not None:
                self.cross_gender_suspect.add(ScrapeScope(contraria, scope.section, scope.category))

    @property
    def dead_ratio(self) -> float:
        """Proporción de hojas caídas (0.0 si no se recorrió ninguna)."""
        return self.leaves_failed / self.leaves_total if self.leaves_total else 0.0


@dataclass(frozen=True)
class DelistCandidate:
    """Producto que la ingesta está a punto de dar de baja y quiere confirmar antes."""

    retailer_product_id: str
    url: str | None  # `product.url` en BD: hay tiendas que solo pueden sondear por URL


@runtime_checkable
class BaseStore(Protocol):
    """Interfaz que implementa el scraper de cada tienda."""

    slug: str
    name: str
    base_url: str

    def scopes(self) -> Iterable[ScrapeScope]:
        """Ámbitos del catálogo que este scraper recorre (base para acotar las bajas)."""
        ...

    def list_catalog(self) -> Iterable[ListingEntry]:
        """Barre las secciones relevantes (pocas peticiones) y devuelve una entrada por producto."""
        ...

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        """Pide el detalle completo (tallas/precio/stock) de los productos indicados."""
        ...


@runtime_checkable
class SupportsAliveProbe(Protocol):
    """Capacidad OPCIONAL: confirmar de forma activa si un producto sigue a la venta.

    Las bajas se detectan por ausencia en el listado, que es una señal indirecta (un bloqueo
    o una reestructura de categorías la falsean). Si la tienda permite preguntar por un
    producto concreto, la ingesta la usa como veredicto final antes de descatalogar.
    """

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        """Sondea los candidatos: retailer_product_id -> sigue a la venta.

        Tres estados con dos valores: `True` (vivo), `False` (retirado) y **ausente del
        mapa** = no concluyente (fallo de red, bloqueo, respuesta ambigua). La ingesta es
        conservadora: solo da de baja lo confirmado como retirado.
        """
        ...


@dataclass(frozen=True)
class LeafHealth:
    """Veredicto sobre UNA hoja de categoría, para el chequeo preventivo de `--check-categories`."""

    scope: ScrapeScope
    leaf: str  # el identificador de la hoja tal y como lo escribe la tienda (id, uuid, ruta)
    alive: bool | None  # True viva, False retirada, None sin veredicto (fallo nuestro)
    detail: str = ""  # qué respondió, para poder actuar sin repetir la petición a mano
    # La hoja **depende de una campaña**, así que apagarse es su comportamiento normal y no una
    # retirada que haya que ir a arreglar. No es un cuarto valor de `alive`: una hoja de campaña
    # apagada está retirada de verdad (`alive=False`) —no se puede listar, y lo que solo vivía
    # dentro deja de ingerirse—, lo que cambia es que era **esperable**, y por eso el vigía la
    # cuenta como aviso y no como accionable (ver `vigia.revisar_hojas`).
    #
    # Medido en las dos tiendas que tienen hojas así (#176, #195): en Mango la hoja da 404 y vuelve
    # con el MISMO `catalogId` un día después; en Lefties desaparece del menú al acabar la campaña.
    # Sin esta marca, las dos abrirían una issue del vigía cada semana pidiendo un id nuevo que ya
    # existe, que es la forma más rápida de que nadie se crea al vigía.
    estacional: bool = False


@runtime_checkable
class SupportsLeafHealth(Protocol):
    """Capacidad OPCIONAL: sondear las hojas de categoría SIN ingerir nada.

    Existe para enterarse de que un id ha caducado **antes** de que lo note el usuario: la pasada
    ya tolera la hoja muerta, pero mientras tanto esa categoría no se ingiere, y sin esto solo se
    descubre leyendo el resumen de un job que nadie mira. Pensado para ejecutarse a mano o desde
    un CronJob de vigilancia, no en cada pasada.
    """

    def check_leaves(self) -> Iterable[LeafHealth]:
        """Un veredicto por hoja configurada, en el orden en que están declaradas."""
        ...


@dataclass(frozen=True)
class CategoryNode:
    """Una categoría del árbol **tal y como lo publica la tienda**, no como lo mapeamos nosotros.

    Es la respuesta a «qué hojas existen de verdad», que es distinta de «qué hojas ingerimos»
    (`CATEGORIES`) y de «siguen vivas las que ingerimos» (`LeafHealth`). Existe porque adivinar
    rutas copiándolas de otra rama es exactamente como se llega a una hoja que no existe, y hay
    tiendas donde eso no da 404 (Sfera devuelve el catálogo del padre, #54).

    ⚠️ Enumera lo que la tienda PUBLICA, y eso puede quedarse corto: `ninos/nino/vaqueros` no sale
    en el árbol de Sfera del 02/08/2026 y sin embargo sirve 7 productos y el sondeo de hojas la da
    viva. O sea que **la ausencia de una hoja aquí no prueba que esté muerta** — para eso está
    `check_leaves()`, que la pide. Lo que sí es fiable es lo contrario: lo que sale, existe.
    """

    # Identificador en el vocabulario de la tienda, el mismo que `LeafHealth.leaf`.
    path: str
    title: str  # cómo la llama la tienda de cara al usuario
    # Productos que la tienda DECLARA. `None` = no lo dice, que NO es lo mismo que 0.
    #
    # Y «declara» no es «sirve»: medido en Sfera el 02/08/2026 al rescatar las hojas de #72, la
    # faceta decía 8 en `ninos/nina/leggings` y el listado dio 18, y 4 en
    # `ninos/nino/shorts-y-bermudas` contra 15. Sirve para orientarse sobre qué hojas existen y
    # cuáles pesan; NO sirve para dimensionar una pasada ni para decidir si merece la pena una
    # hoja. Para eso hay que pedir el listado.
    count: int | None
    depth: int  # niveles por debajo de la raíz pedida (1 = hija directa)
    has_children: bool


@runtime_checkable
class SupportsCategoryTree(Protocol):
    """Capacidad OPCIONAL: enumerar el árbol de categorías que la tienda publica.

    Es la herramienta de reconocimiento para decidir **cobertura**: qué hay ahí fuera que no
    estemos ingiriendo. No participa en la pasada ni en las bajas.

    Desde #156 la ejecuta además el vigía cada semana (`vigia.revisar_cobertura`), que es lo que
    convierte la pregunta en periódica: `check_leaves()` solo sabe si sigue viva una hoja que ya
    mapeamos, así que una categoría **nueva** —o una de temporada que vuelve— era invisible.
    """

    def category_tree(self, root: str) -> Iterable[CategoryNode]:
        """Categorías publicadas por debajo de `root`, de arriba abajo.

        Solo descendientes de `root`: una hoja sin descendencia devuelve **lista vacía**, que es
        la respuesta honesta a «qué cuelga de aquí».
        """
        ...

    def mapped_leaves(self) -> Iterable[str]:
        """Hojas configuradas hoy, en el mismo vocabulario que `CategoryNode.path`.

        Sirve para cruzar el árbol publicado con lo que ingerimos y que el informe pueda decir
        qué falta, que es la única pregunta que justifica pedir el árbol.
        """
        ...

    def tree_separator(self) -> str:
        """El carácter con el que la tienda anida sus rutas (`/` en Sfera, `-` en C&A).

        Es lo que deja saber si una categoría **cuelga de otra**, y sin eso el árbol no se puede
        leer: medido en C&A, de 122 rutas publicadas 53 son subcategorías de hojas que YA
        ingerimos (`3-7-1-2` Camisetas bajo `3-7-1`, que está en `CATEGORIES`). Sus productos ya
        entran por el padre, así que señalarlas sería ruido — y ese ruido era la mitad del informe.

        La comparación es `ruta == ancestro or ruta.startswith(ancestro + sep)`, **con el separador
        y no a prefijo pelado**: `3-1-11` (Calcetines) empieza por `3-1-1` (Camisetas) y las dos
        están mapeadas, así que a prefijo pelado una taparía a la otra.

        Que el id jerárquico predice la contención está **medido, no supuesto**: `3-7-2-3` (Shorts)
        aporta 0 productos nuevos porque está contenido entero en `3-7-2` (Pantalones), y eso ya
        estaba anotado en `c_and_a.CATEGORIES` antes de esta capa.

        Vive aquí y no en `SupportsCoverageWatch` desde #179, y la diferencia es visible: `--tree`
        lo necesita igual que el vigía, así que mientras estuvo en el otro protocolo las tiendas
        que enumeran sin vigilarse (Mango, y ahora Zara) imprimían como huecos los nodos que cuelgan
        de una hoja ingerida — 139 de 153 en la rama de niña de Zara. Lo que decide si una rama
        cuelga de otra es el **vocabulario**, que es cosa de quien enumera; lo que es una decisión
        de coste del barrido semanal es `tree_roots()`, y esa sí se queda allí.
        """
        ...


@runtime_checkable
class SupportsCoverageWatch(Protocol):
    """Capacidad OPCIONAL: que el **vigía** barra el árbol cada semana buscando huecos (#156).

    Aparte de `SupportsCategoryTree` a propósito, y no es una sutileza: enumerar el árbol a mano
    (`run.py --tree`) es reconocimiento y vale para cualquier tienda que sepa hacerlo, mientras que
    vigilarlo sin supervisión exige además que el ruido sea acotable. Medido el 04/08/2026, Mango
    sabe lo primero y no lo segundo: su «árbol» es el menú de navegación, con promociones que rotan
    (`dest_toystory`, `dest_ramadam`, `nuevosarticulosanadidos`) y un espejo `rebajas_*` de cada
    rama de prendas, así que hacen falta **72 declaraciones** y caducan solas. Una tienda así se
    declara en `vigia.COBERTURA_SIN_VIGILAR` con el motivo, y conserva su `--tree`.
    """

    def tree_roots(self) -> Iterable[str]:
        """Raíces que el vigía barre cada semana, en el vocabulario de `CategoryNode.path`.

        Existe porque `category_tree()` pide una raíz y hasta #156 la ponía la persona en la línea
        de comandos: sin esto el vigía no sabría por dónde empezar. La declara la tienda y no el
        vigía porque el vocabulario es suyo, y **no se deriva de `mapped_leaves()`**: en Sfera la
        ruta padre es un prefijo de texto, pero el `ipim_id` de C&A es opaco y no tiene padre que
        derivar.

        Elegirlas es una decisión de **coste**, no de cobertura máxima: el barrido se repite todas
        las semanas contra la tienda, y hay árboles que se piden con una petición por nodo. Quien
        las declare mide cuántos nodos cuelgan de cada una y lo escribe.
        """
        ...


@runtime_checkable
class SupportsScanReport(Protocol):
    """Capacidad OPCIONAL: contar las hojas que se cayeron durante `list_catalog()`.

    La implementan las tiendas que recorren hojas de categoría independientes y saben seguir
    cuando una desaparece. Una tienda que no la implemente se comporta como siempre: cualquier
    error de listado propaga y aborta la pasada.
    """

    def scan_report(self) -> ScanReport:
        """Informe del último recorrido.

        **Solo es válido con `list_catalog()` consumido entero**: es un generador, así que las
        hojas se recorren a medida que la ingesta tira de él. Quien lo consuma a medias verá un
        informe a medias.
        """
        ...


@dataclass
class ProductTags:
    """Ejes transversales observados durante el listado, y cuáles se pudieron observar de verdad.

    Las dos mitades hacen falta y la segunda es la que evita el fallo silencioso. La reconciliación
    de la ingesta BORRA las etiquetas que la tienda ya no declara, así que sin saber qué fuentes se
    pudieron leer, una hoja caída se leería como «esta tienda ya no tiene nada deportivo» y se
    llevaría por delante las marcas de toda la tienda. Es el mismo fallo que `failed_scopes` evita
    en las bajas, y aquí no hay histéresis ni sondeo que lo amortigüen: una pasada mala basta.
    """

    # retailer_product_id -> etiquetas observadas. Solo del vocabulario de `scraper.tags`.
    por_producto: dict[str, set[str]] = field(default_factory=dict)
    # Etiquetas cuya fuente se listó ENTERA y sin fallos: las únicas que se pueden reconciliar.
    fiables: set[str] = field(default_factory=set)

    def anota(self, retailer_product_id: str, tag: str) -> None:
        """Marca un producto, sin importar cuántas veces lo vea el listado.

        Se llama por cada aparición y no solo por la primera a propósito: en las tiendas que
        emiten según recorren (Sfera), el producto que ya salió por otra hoja se descarta por el
        dedup, y si la marca se anotara solo con la entrada emitida se perdería justo en el caso
        más común — el 89 % de la rama de deporte de Lefties ya entra por su categoría.
        """
        self.por_producto.setdefault(retailer_product_id, set()).add(tag)


@runtime_checkable
class SupportsProductTags(Protocol):
    """Capacidad OPCIONAL: declarar ejes transversales (`scraper.tags`) vistos en el listado.

    La implementan las tiendas que publican un cajón identificable —hoy el de deporte, #180—. Una
    tienda que no la implemente simplemente no marca nada, y sus productos no aparecen al filtrar
    por ese eje: es lo que pasa con Zara, Hipercor, Springfield y Cacles, que no publican ninguno.

    **Se rellena desde el LISTADO, no desde `fetch_details()`.** El detalle solo se pide para lo
    nuevo, lo cambiado y lo rancio, así que en régimen estacionario la mayoría de los productos no
    pasa por ahí y se quedarían sin marca; el listado se recorre entero en cada pasada.
    """

    def product_tags(self) -> ProductTags:
        """Etiquetas del último recorrido.

        Mismo contrato que `scan_report()`: **solo es válido con `list_catalog()` consumido
        entero**, porque las hojas que las alimentan se recorren a medida que la ingesta tira del
        generador.
        """
        ...
