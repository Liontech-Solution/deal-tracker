"""Tests de parsing de Springfield con fixtures reales capturadas de la web (golden-file)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from scraper.config import Config
from scraper.stores.base import ScrapedProduct
from scraper.stores.springfield import (
    _GENERO_POR_SEGMENTO,
    _SITEMAP_INDICE,
    CATEGORIA_POR_SEGMENTO,
    HOJAS,
    ColorInfo,
    FichaBase,
    SpringfieldStore,
    TallaInfo,
    Ubicacion,
    clasificar,
    color_de_pid,
    parse_colores,
    parse_ld_json,
    parse_sitemap_index,
    parse_sitemap_products,
    parse_tallas,
    producto,
)

from .conftest import FIXTURES


def load_html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def tienda() -> SpringfieldStore:
    """La tienda no toca la BD en nada de lo que se testea aquí; el `database_url` es de pega."""
    return SpringfieldStore(Config(database_url="postgresql:///no-usada"))


def url_de(fragmento: str, entradas: list[str]) -> str:
    """Selecciona una URL del sitemap por lo que la hace especial, nunca por índice."""
    return next(u for u in entradas if fragmento in u)


# --- sitemap -----------------------------------------------------------------


def test_el_indice_solo_devuelve_los_sitemaps_de_producto() -> None:
    """Filtra por nombre, no por posición: el índice mezcla producto, imágenes y categorías."""
    nombres = parse_sitemap_index(load_html("springfield_sitemap_index.xml"))

    assert nombres == [
        "sitemap_1-Products.xml",
        "sitemap_4-Products.xml",
        "sitemap_6-Products.xml",
    ]
    assert not any("Images" in n or "category" in n for n in nombres)


def test_toda_entrada_del_sitemap_trae_lastmod_que_es_la_huella() -> None:
    entradas = parse_sitemap_products(load_html("springfield_sitemap_products.xml"))

    assert len(entradas) == 15
    assert all(e.lastmod for e in entradas)
    # Formato ISO con zona, tal y como lo publica la tienda.
    assert all(e.lastmod.endswith("Z") for e in entradas)


def test_un_sitemap_ilegible_se_cuenta_como_una_hoja_y_dice_cual() -> None:
    """El informe nombra el FICHERO, que es lo único que sirve para ir a mirarlo (#155).

    Springfield es la excepción del proyecto en esto: su `check_leaves()` habla de ramas
    (`ninos/pantalones`) y aquí se habla de ficheros, porque un sitemap es un corte arbitrario del
    catálogo y no se corresponde con ninguna rama. Por eso mismo caen los 24 ámbitos con un solo
    fichero, y por eso mismo cuenta como UNA hoja: sumar un ámbito por hoja dispararía
    `SCRAPER_SCAN_MAX_DEAD_RATIO` con el primer fallo y abortaría una pasada a la que le quedan
    dos tercios del catálogo perfectamente legibles.
    """
    indice = load_html("springfield_sitemap_index.xml")
    productos = load_html("springfield_sitemap_products.xml")
    roto = "sitemap_4-Products.xml"

    def handler(request: httpx.Request) -> httpx.Response:
        nombre = request.url.params.get("name", "")
        if nombre == roto:
            return httpx.Response(200, text="<urlset><url><loc>sin cerrar")  # ET.ParseError
        return httpx.Response(200, text=indice if nombre == _SITEMAP_INDICE else productos)

    store = SpringfieldStore(Config(database_url="postgresql:///no-usada", request_delay=0.0))
    store._client = lambda **_: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    entradas = list(store.list_catalog())

    informe = store.scan_report()
    assert (informe.leaves_total, informe.leaves_failed) == (3, 1)
    assert informe.failed_leaves == [roto], "sin el nombre solo se sabe que se cayó uno de tres"
    assert informe.failed_scopes == set(store.scopes())
    assert entradas, "los otros dos sitemaps se leen igual: una hoja caída no tumba la pasada"


# --- clasificación por URL ---------------------------------------------------


def test_clasificar_saca_genero_seccion_y_categoria_de_la_ruta() -> None:
    urls = [e.url for e in parse_sitemap_products(load_html("springfield_sitemap_products.xml"))]

    vestido = clasificar(url_de("/nina/vestidos/", urls))
    assert vestido == Ubicacion("9123461", "niña", "ropa", "vestidos")

    # Profundidad 6 (`calzado/zapatillas/...`): la categoría es el TERCER segmento, no el último.
    zapato = clasificar(url_de("/nino/calzado/zapatillas/", urls))
    assert zapato is not None
    assert (zapato.gender, zapato.section, zapato.category) == ("niño", "zapateria", "zapatos")


def test_las_categorias_plegadas_caen_en_el_slug_del_brief() -> None:
    """`faldas`->vestidos, `jerseis`->sudaderas y `jeans`->pantalones (ver el mapa del módulo)."""
    urls = [e.url for e in parse_sitemap_products(load_html("springfield_sitemap_products.xml"))]

    falda = clasificar(url_de("/nina/faldas/", urls))
    jersey = clasificar(url_de("/nina/jerseis/", urls))
    vaquero = clasificar(url_de("/nino/jeans/", urls))

    assert falda is not None and falda.category == "vestidos"
    assert jersey is not None and jersey.category == "sudaderas"
    assert vaquero is not None and vaquero.category == "pantalones"


def test_lo_que_no_se_ingiere_se_salta_sin_reventar() -> None:
    """Los cuatro casos que devuelven None, y ninguno es un error."""
    urls = [e.url for e in parse_sitemap_products(load_html("springfield_sitemap_products.xml"))]

    # Otro mundo.
    assert clasificar(url_de("/mujer/", urls)) is None
    assert clasificar(url_de("/hombre/", urls)) is None
    assert clasificar(url_de("/teen/", urls)) is None
    # Categoría infantil fuera del brief.
    assert clasificar(url_de("/nina/complementos/", urls)) is None
    assert clasificar(url_de("/nino/pijamas/", urls)) is None
    # Ruta corta sin taxonomía: 3 segmentos, ni género ni categoría.
    assert clasificar(url_de("/conjunto-de-pantalon-zapatillas-y-polo/", urls)) is None


def test_scopes_declara_todo_lo_que_clasificar_puede_emitir() -> None:
    """Un ámbito no declarado deja sus productos IMPOSIBLES de dar de baja (solo se descataloga
    dentro de ámbitos escaneados), así que este invariante es lo que impide una fuga silenciosa."""
    declarados = set(tienda().scopes())

    emitibles = {
        Ubicacion("0", genero, section, category).scope
        for genero in ("niño", "niña")
        for section, category in CATEGORIA_POR_SEGMENTO.values()
    }
    assert emitibles <= declarados


def test_solo_se_vigilan_las_ramas_que_la_tienda_publica() -> None:
    """`HOJAS` es más corta que el cartesiano a propósito: `nino/vestidos` y `nina/polos` no
    existen, y sondearlas sería un aviso falso del vigía todas las semanas (#67, #129)."""
    assert len(HOJAS) == 19
    assert ("nino", "vestidos") not in HOJAS
    assert ("nina", "polos") not in HOJAS
    # Pero toda hoja vigilada tiene que ser ingerible, o estaríamos vigilando lo que no miramos.
    assert all(segmento in CATEGORIA_POR_SEGMENTO for _, segmento in HOJAS)
    assert all(genero in _GENERO_POR_SEGMENTO for genero, _ in HOJAS)


# --- ficha: ld+json, tallas y colores ----------------------------------------


def test_el_ld_json_da_identidad_nombre_galeria_y_textos() -> None:
    base = parse_ld_json(load_html("springfield_ficha_camiseta.html"))

    assert base is not None
    assert base.retailer_product_id == "5653304"
    assert base.nombre == 'Camiseta manga corta "Not today monday" niño'
    assert all(u.startswith("https://") for u in base.imagenes)
    # nombre + descripción + composición: es lo que come `barefoot.classify()`.
    assert len(base.textos) == 3


def test_cada_talla_trae_su_precio_su_tachado_y_su_stock() -> None:
    tallas = parse_tallas(load_html("springfield_ficha_camiseta.html"))

    assert [t.talla for t in tallas] == ["5-6", "7-8", "9-10", "11-12", "13-14"]
    assert all(t.precio == Decimal("3.99") for t in tallas)
    assert all(t.tachado == Decimal("13.99") for t in tallas)
    # El stock es por talla: aquí solo la última está agotada.
    agotadas = [t.talla for t in tallas if t.agotado]
    assert agotadas == ["13-14"]


def test_el_calzado_sin_descuento_no_inventa_tachado() -> None:
    tallas = parse_tallas(load_html("springfield_ficha_zapato.html"))

    assert [t.talla for t in tallas] == ["27", "28", "29", "30", "31", "32", "33", "34"]
    assert all(t.tachado is None for t in tallas)
    assert all(t.precio == Decimal("50") for t in tallas)


def test_los_colores_traen_el_minimo_de_30_dias_de_omnibus() -> None:
    """Segunda tienda después de C&A que lo publica. Viene por color y como texto (`"24,99 €"`)."""
    colores = parse_colores(load_html("springfield_ficha_multicolor.html"))

    assert [c.nombre for c in colores] == ["blanco", "negro", "verde"]
    blanco = next(c for c in colores if c.nombre == "blanco")
    negro = next(c for c in colores if c.nombre == "negro")

    assert blanco.minimo_30d == Decimal("24.99")
    assert blanco.agotado is False
    # El caso que hace útil el dato: la tienda tacha 24,99 pero declara haberlo vendido a 17,49
    # dentro de esos mismos 30 días.
    assert negro.tachado == Decimal("24.99")
    assert negro.minimo_30d == Decimal("17.49")
    assert negro.agotado is True


def test_un_color_cuyo_id_no_cabe_en_el_pid_sigue_emparejando() -> None:
    """El caso que costó 45 de 1112 productos, y en silencio: el swatch declara `id: "100"` pero su
    `representedProductId` codifica `76`, porque el hueco de color del pid tiene dos caracteres.
    Emparejando por `id`, las tallas caen en un cubo que ningún color reclama y el producto
    desaparece del catálogo sin error ni aviso."""
    pagina = load_html("springfield_ficha_color_descuadrado.html")
    base = parse_ld_json(pagina)
    assert base is not None

    colores = parse_colores(pagina)
    assert len(colores) == 1
    color = colores[0]
    assert color.color_id == "100"  # lo que hay que poner en `?dwvar_…_color=`
    assert color.clave == "76"  # con lo que se emparejan las tallas
    assert color.nombre == "multicolor"

    tallas_por_color: dict[str, list[TallaInfo]] = {}
    imagenes_por_color: dict[str, list[str]] = {}
    SpringfieldStore._repartir(
        base.retailer_product_id,
        parse_tallas(pagina),
        base.imagenes,
        tallas_por_color,
        imagenes_por_color,
    )
    prod = producto(
        base,
        Ubicacion("0143394", "niño", "ropa", "camisetas"),
        colores,
        tallas_por_color,
        imagenes_por_color,
        "https://myspringfield.com/x/0143394.html",
    )

    assert prod is not None
    assert len(prod.variants) == 5
    assert {v.color for v in prod.variants} == {"multicolor"}


def test_el_color_de_una_talla_sale_de_su_pid() -> None:
    """`pid = maestro + colorID + talla`, que es lo único fiable en las páginas `?dwvar`."""
    assert color_de_pid("73011006127", "7301100") == "61"
    assert color_de_pid("22303980111", "2230398") == "01"
    # Un pid que no cuelga de este maestro no se atribuye a ciegas.
    assert color_de_pid("99999990111", "2230398") is None


def test_la_ficha_agotada_marca_todas_sus_tallas_sin_stock() -> None:
    """El mismo producto pedido por otro color (`?dwvar_…_color=01`), entero agotado."""
    tallas = parse_tallas(load_html("springfield_ficha_agotada.html"))

    assert len(tallas) == 4
    assert all(t.agotado for t in tallas)
    # Sin stock, pero con precio: la variante se ingiere igual (agotada, no inexistente).
    assert all(t.precio == Decimal("17.49") for t in tallas)


# --- montaje del producto ----------------------------------------------------


@dataclass(frozen=True)
class Montaje:
    """Lo que `_ficha()` habría reunido tras pedir la ficha y la página del segundo color."""

    base: FichaBase
    colores: list[ColorInfo]
    tallas_por_color: dict[str, list[TallaInfo]]
    imagenes_por_color: dict[str, list[str]]

    def producto(self) -> ScrapedProduct | None:
        return producto(
            self.base,
            Ubicacion("2230398", "niño", "ropa", "sudaderas"),
            self.colores,
            self.tallas_por_color,
            self.imagenes_por_color,
            "https://myspringfield.com/x/2230398.html",
        )


def _monta_multicolor() -> Montaje:
    """Reproduce lo que hace `_ficha()`: página por defecto (blanco) + página del color negro."""
    por_defecto = load_html("springfield_ficha_multicolor.html")
    otro_color = load_html("springfield_ficha_agotada.html")
    base = parse_ld_json(por_defecto)
    otra_base = parse_ld_json(otro_color)
    assert base is not None and otra_base is not None

    tallas_por_color: dict[str, list[TallaInfo]] = {}
    imagenes_por_color: dict[str, list[str]] = {}
    for html, imgs in ((por_defecto, base.imagenes), (otro_color, otra_base.imagenes)):
        SpringfieldStore._repartir(
            base.retailer_product_id,
            parse_tallas(html),
            imgs,
            tallas_por_color,
            imagenes_por_color,
        )
    return Montaje(base, parse_colores(por_defecto), tallas_por_color, imagenes_por_color)


def test_el_producto_junta_las_tallas_de_cada_color_pedido() -> None:
    prod = _monta_multicolor().producto()

    assert prod is not None
    assert prod.retailer_product_id == "2230398"
    # 4 tallas de blanco + 4 de negro. El verde NO aporta: no se pidió su página, y un color sin
    # tallas no se inventa.
    assert len(prod.variants) == 8
    assert {v.color for v in prod.variants} == {"blanco", "negro"}
    # El id de variante es el pid de la tienda, el mismo que usa en el carrito.
    assert {v.retailer_variant_id for v in prod.variants} >= {"22303989911", "22303980111"}


def test_el_minimo_de_30_dias_viaja_del_color_a_todas_sus_variantes() -> None:
    """La tienda lo declara por color, así que todas las tallas de ese color lo comparten."""
    prod = _monta_multicolor().producto()

    assert prod is not None
    blancas = [v for v in prod.variants if v.color == "blanco"]
    negras = [v for v in prod.variants if v.color == "negro"]
    assert all(v.retailer_min_30d == Decimal("24.99") for v in blancas)
    assert all(v.retailer_min_30d == Decimal("17.49") for v in negras)
    # Y el stock sigue siendo por talla, no por color.
    assert [v.in_stock for v in blancas] == [True, True, True, False]
    assert not any(v.in_stock for v in negras)


def test_una_pagina_que_repite_el_color_por_defecto_no_duplica_variantes() -> None:
    """Pedir `?dwvar_…_color=Y` y recibir las tallas del color por defecto es un caso REAL: la
    primera pasada del 03/08/2026 emitió 8329 variantes para 8219 filas, 110 repetidas. Sin
    deduplicar por `pid`, cada variante del color por defecto se emite dos veces y su galería
    también."""
    pagina = load_html("springfield_ficha_multicolor.html")
    base = parse_ld_json(pagina)
    assert base is not None

    tallas_por_color: dict[str, list[TallaInfo]] = {}
    imagenes_por_color: dict[str, list[str]] = {}
    for _ in range(2):  # la misma página dos veces, que es lo que hace la tienda al ignorar dwvar
        SpringfieldStore._repartir(
            base.retailer_product_id,
            parse_tallas(pagina),
            base.imagenes,
            tallas_por_color,
            imagenes_por_color,
        )

    assert [len(v) for v in tallas_por_color.values()] == [4]
    pids = [fila.pid for filas in tallas_por_color.values() for fila in filas]
    assert len(pids) == len(set(pids))
    # La galería tampoco se apila dos veces.
    assert len(imagenes_por_color["99"]) == len(set(imagenes_por_color["99"]))


def test_cada_foto_va_con_el_color_que_retrata() -> None:
    """Si foto y color se desalinean, la ficha enseña la foto de uno con el precio de otro."""
    prod = _monta_multicolor().producto()

    assert prod is not None
    assert {i.color for i in prod.images} == {"blanco", "negro"}
    # La primaria es la del primer color declarado.
    assert prod.image_url == prod.images[0].url
    assert prod.images[0].color == "blanco"


def test_el_calzado_se_clasifica_como_barefoot_y_la_ropa_no() -> None:
    """`barefoot` es None en ropa (no aplica) y un veredicto en zapatería."""
    zapato_html = load_html("springfield_ficha_zapato.html")
    base = parse_ld_json(zapato_html)
    assert base is not None
    tallas_por_color: dict[str, list[TallaInfo]] = {}
    imagenes_por_color: dict[str, list[str]] = {}
    SpringfieldStore._repartir(
        base.retailer_product_id,
        parse_tallas(zapato_html),
        base.imagenes,
        tallas_por_color,
        imagenes_por_color,
    )

    zapato = producto(
        base,
        Ubicacion("7301100", "niño", "zapateria", "zapatos"),
        parse_colores(zapato_html),
        tallas_por_color,
        imagenes_por_color,
        "https://myspringfield.com/x/7301100.html",
    )

    assert zapato is not None
    # Springfield no es tienda barefoot ni etiqueta el calzado: queda en `desconocido`, que es la
    # respuesta honesta, no `no`.
    assert zapato.barefoot == "desconocido"


def test_un_producto_sin_variantes_utilizables_no_se_emite() -> None:
    """No se puede seguir ni avisar de algo sin precio, y emitirlo ensuciaría el catálogo."""
    base = parse_ld_json(load_html("springfield_ficha_camiseta.html"))
    assert base is not None

    assert (
        producto(base, Ubicacion("5653304", "niño", "ropa", "camisetas"), [], {}, {}, "u") is None
    )
