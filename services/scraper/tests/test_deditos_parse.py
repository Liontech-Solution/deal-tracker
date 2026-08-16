"""Tests de parsing de Deditos Barefoot con fixtures reales (golden-file).

`deditos_listado_ninos.json` son 14 productos reales de
`/wp-json/wc/store/v1/products?category=ninos`, elegidos **por rasgo** para cubrir los destinos del
mapeo de categorías, las dos formas de eje de variación (`pa_modelo` y `pa_color`), rebaja y no
rebaja, `price_range` nulo, la categoría `unisex` y el producto de 116 variaciones.

Las tres fichas HTML son recortes de páginas reales: se conservan íntegros los `variations_form`
—el del producto y dos de prendas relacionadas— porque distinguirlos **es** el trabajo del parser.

Como en `test_zara_parse.py`, aquí no se selecciona nada por índice: reordenar el fixture no puede
romper un test.
"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.base import ProbeVerdict
from scraper.stores.deditos import (
    _HOJA_CANARIA,
    _PAGE_SIZE,
    CategoryConfig,
    DeditosStore,
    _destino,
    _genero,
    _precio,
    _slugs_de_categoria,
    parse_categorias,
    parse_producto,
    parse_variaciones,
    product_signature,
)

from .conftest import FIXTURES, load_fixture

_LISTADO = "deditos_listado_ninos.json"
_FICHA_MODELO = ("deditos_ficha_modelo.html", "76014")
_FICHA_COLOR = ("deditos_ficha_color.html", "9635")
_FICHA_REBAJA = ("deditos_ficha_rebaja.html", "9303")
_HOJA = CategoryConfig("ninos", "infantil (test)")


def _listado() -> list[dict[str, Any]]:
    return load_fixture(_LISTADO)


def _texto(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def _con_categoria(slug: str) -> dict[str, Any]:
    """El producto del fixture que lleva esa categoría de la tienda. Por rasgo, no por índice."""
    for raw in _listado():
        if slug in _slugs_de_categoria(raw):
            return raw
    raise AssertionError(f"el fixture no trae ningún producto de {slug!r}")


def _por_id(pid: str) -> dict[str, Any]:
    for raw in _listado():
        if str(raw["id"]) == pid:
            return raw
    raise AssertionError(f"el fixture no trae el producto {pid}")


def _producto(ficha: tuple[str, str]) -> Any:
    nombre, pid = ficha
    variaciones = parse_variaciones(_texto(nombre), pid)
    assert variaciones is not None
    return parse_producto(_por_id(pid), variaciones)


# --------------------------------------------------------------------------------------
# El fixture es lo que decimos que es
# --------------------------------------------------------------------------------------


def test_el_fixture_cubre_los_rasgos_que_dice_cubrir() -> None:
    listado = _listado()
    assert len(listado) >= 10
    assert len({r["id"] for r in listado}) == len(listado), "ids repetidos en el fixture"
    todas = {slug for raw in listado for slug in _slugs_de_categoria(raw)}
    imprescindibles = ("merceditas-zapatos-ninos", "calcetines", "plantillas", "sandalias-ninos")
    for imprescindible in imprescindibles:
        assert imprescindible in todas


# --------------------------------------------------------------------------------------
# Precios: dos formatos, y ninguno amigable
# --------------------------------------------------------------------------------------


def test_precio_en_unidades_menores_de_la_store_api() -> None:
    # "4296" con minor_unit 2 son 42,96 €, no 4296 €.
    assert _precio("4296", minor_unit=2) == Decimal("42.96")
    assert _precio("2560", minor_unit=2) == Decimal("25.60")


def test_precio_limpia_la_basura_binaria_del_float_de_php() -> None:
    # Lo que trae de verdad la ficha del producto 9303.
    assert _precio("27.480000000000000426325641456060111522674560546875") == Decimal("27.48")
    assert _precio("39.39999999999999857891452847979962825775146484375") == Decimal("39.40")


def test_precio_devuelve_none_ante_lo_que_no_es_numero() -> None:
    assert _precio(None) is None
    assert _precio("") is None
    assert _precio("gratis") is None
    assert _precio(True) is None  # un bool no es un precio, aunque Decimal(True) funcione


# --------------------------------------------------------------------------------------
# La ficha: elegir el formulario correcto
# --------------------------------------------------------------------------------------


def test_las_variaciones_salen_del_formulario_del_producto_y_no_del_primero() -> None:
    """La ficha trae también los formularios de las prendas relacionadas (punto 3)."""
    html = _texto(_FICHA_MODELO[0])
    del_producto = parse_variaciones(html, "76014")
    assert del_producto is not None
    assert len(del_producto) == 14

    # Y el de una relacionada sale distinto: si el parser cogiera «el primero», serían iguales.
    relacionada = parse_variaciones(html, "46474")
    assert relacionada is not None
    assert len(relacionada) == 7
    ids_producto = {v["variation_id"] for v in del_producto}
    assert ids_producto & {v["variation_id"] for v in relacionada} == set()


def test_un_producto_que_no_esta_en_la_ficha_no_devuelve_variaciones_de_otro() -> None:
    assert parse_variaciones(_texto(_FICHA_MODELO[0]), "999999999") is None


def test_el_permalink_que_redirige_a_otro_producto_no_le_roba_los_precios() -> None:
    """El caso real que se comió la primera pasada, y el motivo de elegir por `data-product_id`.

    El producto 11711 publica un `permalink` viejo que responde 301 a la ficha de OTRO producto;
    allí no está su formulario. Y el recuento no lo delataría: la Store API dice que el 11711
    tiene 4 variaciones, y el primer formulario de esa página —el del 59523— tiene justamente 4.
    """
    ajena = json.dumps(
        [
            {
                "variation_id": 595230 + i,
                "display_price": "49.95",
                "is_in_stock": True,
                "attributes": {"attribute_pa_talla": str(20 + i)},
            }
            for i in range(4)
        ]
    )
    html = (
        f"<form data-product_id=\"59523\" data-product_variations='{ajena}'></form>"
        f"<form data-product_id=\"34025\" data-product_variations='{ajena}'></form>"
    )
    assert parse_variaciones(html, "11711") is None


def test_el_umbral_ajax_de_woocommerce_es_none_y_no_lista_vacia() -> None:
    """`data-product_variations="false"` significa «te las sirvo por AJAX», no «no tiene tallas».

    Confundirlos daría de baja el catálogo entero el día que alguien toque
    `woocommerce_ajax_variation_threshold` en el servidor.
    """
    html = '<form data-product_id="123" class="variations_form" data-product_variations="false">'
    assert parse_variaciones(html, "123") is None


def test_una_ficha_con_json_roto_no_revienta() -> None:
    html = '<form data-product_id="123" data-product_variations="[{roto">'
    assert parse_variaciones(html, "123") is None


def test_el_form_se_reconoce_aunque_el_json_lleve_mayor_que_dentro() -> None:
    """El `<form ...>` no se puede acotar con `[^>]*`: el atributo lleva `>` en su HTML."""
    html = _texto(_FICHA_REBAJA[0])
    assert "&gt;" in html or ">" in html
    variaciones = parse_variaciones(html, "9303")
    assert variaciones is not None and len(variaciones) == 12


# --------------------------------------------------------------------------------------
# Mapeo de categorías: la específica gana a la genérica
# --------------------------------------------------------------------------------------


def test_la_categoria_especifica_gana_a_la_generica() -> None:
    """Un producto lleva varias; manda el orden de `_CATEGORIA_POR_SLUG`, no el de la API."""
    # Orden de la API al revés, para que el test no pueda pasar por casualidad.
    assert _destino(["zapatos-ninos", "merceditas-zapatos-ninos"]) == ("zapateria", "zapatos")
    assert _destino(["zapatos-ninos", "botas-de-agua"]) == ("zapateria", "botas")
    assert _destino(["zapatillas-de-deporte", "sandalias-ninos"]) == ("zapateria", "sandalias")


def test_unas_merceditas_reales_del_fixture_salen_de_nina() -> None:
    raw = _con_categoria("merceditas-zapatos-ninos")
    slugs = _slugs_de_categoria(raw)
    assert _destino(slugs) == ("zapateria", "zapatos")
    assert _genero(raw, slugs) == "niña"


@pytest.mark.parametrize(
    ("slug", "esperado"),
    [
        ("sandalias-ninos", ("zapateria", "sandalias")),
        ("escarpines", ("zapateria", "sandalias")),
        ("botas-ninos", ("zapateria", "botas")),
        ("senderismo-ninos", ("zapateria", "botas")),
        ("zapatillas-de-deporte", ("zapateria", "zapatillas")),
        ("lonetas-ninos", ("zapateria", "zapatillas")),
        ("slippers", ("zapateria", "zapatillas")),
        ("prewalkers", ("zapateria", "zapatos")),
        ("colegiales", ("zapateria", "zapatos")),
        ("plantillas", ("zapateria", "plantillas")),
        ("calcetines", ("ropa", "ropa-interior")),
    ],
)
def test_cada_categoria_de_la_tienda_va_a_su_destino(slug: str, esperado: tuple[str, str]) -> None:
    assert _destino(["ninos", slug]) == esperado


def test_todo_producto_del_fixture_tiene_destino_o_se_descarta_a_sabiendas() -> None:
    """Ninguno se pierde por accidente: o mapea, o cae por una categoría excluida."""
    destinos = {_destino(_slugs_de_categoria(raw)) for raw in _listado()}
    assert None not in destinos, "el fixture no trae excluidos; si los trae, revisa el mapeo"
    assert ("zapateria", "zapatos") in destinos


def test_las_categorias_excluidas_descartan_el_producto_entero() -> None:
    assert _destino(["ninos", "juguetes"]) is None
    assert _destino(["ninos", "zapatos-ninos", "segunda-vida"]) is None
    assert _destino(["accesorios", "mochilas-infantiles"]) is None


def test_una_categoria_nueva_no_pierde_el_producto() -> None:
    """La tienda añade categorías cada temporada: perder el producto es peor que el cajón."""
    assert _destino(["ninos", "categoria-que-la-tienda-estrena-manana"]) == ("zapateria", "zapatos")


# --------------------------------------------------------------------------------------
# Género: `unisex` por defecto, porque la tienda no lo publica
# --------------------------------------------------------------------------------------


def test_sin_senal_el_genero_es_unisex() -> None:
    assert _genero({"tags": []}, ["ninos", "zapatos-ninos"]) == "unisex"


def test_el_tag_de_la_tienda_manda_cuando_es_el_unico() -> None:
    assert _genero({"tags": [{"slug": "nino"}]}, ["ninos"]) == "niño"
    assert _genero({"tags": [{"slug": "nina"}]}, ["ninos"]) == "niña"


def test_los_dos_tags_a_la_vez_son_unisex() -> None:
    assert _genero({"tags": [{"slug": "nino"}, {"slug": "nina"}]}, ["ninos"]) == "unisex"


def test_la_categoria_de_nina_gana_al_tag() -> None:
    """La tienda titula la categoría «Merceditas barefoot niña»; el tag es más flojo que eso."""
    assert _genero({"tags": [{"slug": "nino"}]}, ["merceditas-zapatos-ninos"]) == "niña"


# --------------------------------------------------------------------------------------
# Producto completo
# --------------------------------------------------------------------------------------


def test_el_producto_sale_entero_con_su_ficha() -> None:
    p = _producto(_FICHA_MODELO)
    assert p is not None
    assert p.retailer_product_id == "76014"
    assert p.section == "zapateria"
    assert p.category == "zapatillas"
    assert p.barefoot == "si"
    assert len(p.variants) == 14
    assert p.url is not None and p.url.startswith("https://deditosbarefoot.com/")


def test_toda_la_zapateria_es_barefoot_por_declaracion_de_tienda() -> None:
    """La marca convencional no la quita: la tienda vende su línea barefoot y así la titula."""
    p = _producto(_FICHA_MODELO)
    assert p is not None
    assert "Gioseppo" in p.name  # marca convencional
    assert p.barefoot == "si"


def test_los_calcetines_son_ropa_y_no_llevan_marca_barefoot() -> None:
    raw = _con_categoria("calcetines")
    variaciones = [
        {"variation_id": 1, "display_price": "9.95", "attributes": {"attribute_pa_talla": "25-27"}}
    ]
    p = parse_producto(raw, variaciones)
    assert p is not None
    assert (p.section, p.category) == ("ropa", "ropa-interior")
    assert p.barefoot is None, "la pregunta barefoot no aplica a la ropa"


def test_los_nombres_no_llegan_con_entidades_html_sin_resolver() -> None:
    """La Store API publica `Blanditos by Crio&#8217;s`: 47 de 431 nombres el 16/08/2026.

    Sin resolverlas, la tarjeta del catálogo y el aviso de Telegram enseñan el `&#8217;`.
    """
    crudos = [raw["name"] for raw in _listado()]
    assert any("&#" in n or "&amp;" in n for n in crudos), (
        "si el fixture deja de traerlas, este test ha dejado de probar nada"
    )
    for raw in _listado():
        p = parse_producto(raw, [{"variation_id": 1, "display_price": "1.00"}])
        if p is not None:
            assert "&#" not in p.name and "&amp;" not in p.name


def test_el_color_tampoco_llega_con_entidades() -> None:
    """El color sale del término `pa_modelo`, y ahí la tienda escribe `B&amp;W CONGUITOS`."""
    variaciones = [
        {
            "variation_id": 1,
            "display_price": "1.00",
            "attributes": {"attribute_pa_modelo": "byw-conguitos"},
        }
    ]
    raw = {
        "id": 1,
        "name": "Zapato",
        "permalink": "https://deditosbarefoot.com/x",
        "categories": [{"slug": "zapatos-ninos"}],
        "attributes": [
            {
                "taxonomy": "pa_modelo",
                "terms": [{"slug": "byw-conguitos", "name": "B&amp;W CONGUITOS BEIG"}],
            }
        ],
    }
    p = parse_producto(raw, variaciones)
    assert p is not None
    assert p.variants[0].color == "B&W CONGUITOS BEIG"


def test_la_variante_lleva_el_id_de_variacion_y_no_el_sku() -> None:
    """Esta tienda repite el mismo `sku` en todas las tallas de un modelo."""
    p = _producto(_FICHA_REBAJA)
    assert p is not None
    skus = {v.sku for v in p.variants}
    ids = {v.retailer_variant_id for v in p.variants}
    assert len(skus) == 1, "si esto cambia, el fixture ha dejado de ilustrar el problema"
    assert len(ids) == len(p.variants)


def test_el_tachado_solo_cuenta_si_es_mayor_que_el_precio() -> None:
    p = _producto(_FICHA_REBAJA)
    assert p is not None
    rebajadas = [v for v in p.variants if v.list_price is not None]
    assert rebajadas, "el fixture de rebaja tiene que traer alguna"
    assert all(v.list_price > v.price for v in rebajadas)


def test_sin_rebaja_no_se_inventa_un_descuento_del_cero_por_ciento() -> None:
    p = _producto(_FICHA_MODELO)
    assert p is not None
    assert all(v.list_price is None for v in p.variants)


def test_el_precio_por_talla_no_es_el_del_listado() -> None:
    """El motivo por el que aquí se pide la ficha: el listado describe mal el producto.

    El listado del 9303 dice `price: "2560"` y `price_range: null`; sus tallas están a 25,60 y
    27,48. Una huella construida con el listado sería ciega a eso.
    """
    raw = _por_id("9303")
    assert raw["prices"]["price_range"] is None
    p = _producto(_FICHA_REBAJA)
    assert p is not None
    assert len({v.price for v in p.variants}) > 1


def test_hay_tallas_agotadas_y_se_reflejan() -> None:
    p = _producto(_FICHA_REBAJA)
    assert p is not None
    assert any(not v.in_stock for v in p.variants)
    assert any(v.in_stock for v in p.variants)


def test_el_color_sale_del_eje_modelo_traducido_a_su_nombre() -> None:
    """En 306 productos el eje es `pa_modelo`, y la ficha solo da su slug."""
    p = _producto(_FICHA_MODELO)
    assert p is not None
    colores = {v.color for v in p.variants}
    assert colores == {"GIOSEPPO zapatillas 80981 MARRON"}, colores


def test_el_color_sale_de_pa_color_cuando_es_el_eje() -> None:
    p = _producto(_FICHA_COLOR)
    assert p is not None
    colores = {v.color for v in p.variants if v.color}
    assert len(colores) > 1
    # Nombres legibles del término, no los slugs que trae la ficha.
    nombres = {
        t["name"]
        for a in _por_id("9635")["attributes"]
        if a["taxonomy"] == "pa_color"
        for t in a["terms"]
    }
    assert colores <= nombres


def test_las_tallas_son_las_del_atributo_talla() -> None:
    p = _producto(_FICHA_MODELO)
    assert p is not None
    tallas = {v.size for v in p.variants}
    assert "26" in tallas and None not in tallas


def test_la_galeria_atribuye_la_foto_al_mismo_color_que_la_variante() -> None:
    p = _producto(_FICHA_COLOR)
    assert p is not None
    colores_variante = {v.color for v in p.variants if v.color}
    colores_foto = {i.color for i in p.images if i.color}
    assert colores_foto, "la ficha trae fotos por variación"
    assert colores_foto <= colores_variante


def test_un_producto_sin_variaciones_con_precio_no_se_emite() -> None:
    assert parse_producto(_por_id("76014"), []) is None
    assert parse_producto(_por_id("76014"), [{"variation_id": 1}]) is None


# --------------------------------------------------------------------------------------
# Huella
# --------------------------------------------------------------------------------------


def test_la_huella_cambia_con_el_precio_y_con_el_stock() -> None:
    nombre, pid = _FICHA_REBAJA
    variaciones = parse_variaciones(_texto(nombre), pid)
    assert variaciones is not None
    base = product_signature(parse_producto(_por_id(pid), variaciones))  # type: ignore[arg-type]

    otro_precio = deepcopy(variaciones)
    otro_precio[0]["display_price"] = "99.99"
    assert product_signature(parse_producto(_por_id(pid), otro_precio)) != base  # type: ignore[arg-type]

    otro_stock = deepcopy(variaciones)
    otro_stock[0]["is_in_stock"] = not otro_stock[0].get("is_in_stock")
    assert product_signature(parse_producto(_por_id(pid), otro_stock)) != base  # type: ignore[arg-type]


def test_la_huella_no_depende_del_orden_de_las_variaciones() -> None:
    nombre, pid = _FICHA_REBAJA
    variaciones = parse_variaciones(_texto(nombre), pid)
    assert variaciones is not None
    directo = product_signature(parse_producto(_por_id(pid), variaciones))  # type: ignore[arg-type]
    revuelto = product_signature(parse_producto(_por_id(pid), list(reversed(variaciones))))  # type: ignore[arg-type]
    assert directo == revuelto


# --------------------------------------------------------------------------------------
# El árbol de categorías
# --------------------------------------------------------------------------------------


def test_el_arbol_reconstruye_la_ruta_completa() -> None:
    nodos = parse_categorias(load_fixture("deditos_categorias.json"))
    rutas = {n.path for n in nodos}
    assert "ninos" in rutas
    assert "ninos/zapatos-ninos" in rutas
    assert "ninos/zapatos-ninos/colegiales" in rutas
    assert "adultos/mujer/sandalias-mujer" in rutas


def test_el_arbol_marca_quien_tiene_hijas_y_a_que_profundidad() -> None:
    nodos = {n.path: n for n in parse_categorias(load_fixture("deditos_categorias.json"))}
    assert nodos["ninos"].depth == 1 and nodos["ninos"].has_children
    hoja = nodos["ninos/zapatos-ninos/colegiales"]
    assert hoja.depth == 3 and not hoja.has_children


def test_una_categoria_huerfana_se_cuelga_de_la_raiz_en_vez_de_perderse() -> None:
    nodos = parse_categorias(
        [{"id": 9, "slug": "huerfana", "name": "Huérfana", "parent": 4242, "count": 3}]
    )
    assert [n.path for n in nodos] == ["huerfana"]
    assert nodos[0].depth == 1


def test_un_ciclo_en_el_arbol_no_cuelga() -> None:
    nodos = parse_categorias(
        [
            {"id": 1, "slug": "a", "name": "A", "parent": 2, "count": 0},
            {"id": 2, "slug": "b", "name": "B", "parent": 1, "count": 0},
        ]
    )
    assert len(nodos) == 2


def test_el_arbol_ignora_lo_que_no_tiene_forma_de_categoria() -> None:
    assert parse_categorias({"no": "es una lista"}) == []
    assert parse_categorias([{"sin": "slug"}, None, 3]) == []


# --------------------------------------------------------------------------------------
# list_catalog: la hoja vacía y la paginación
# --------------------------------------------------------------------------------------


def _store_sirviendo(
    paginas: dict[int, list[dict[str, Any]]], fichas: dict[str, str] | None = None
) -> DeditosStore:
    """DeditosStore cuyo cliente devuelve páginas sintéticas y fichas por URL."""
    fichas = fichas or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/wp-json/"):
            page = int(request.url.params.get("page", "1"))
            productos = paginas.get(page, [])
            return httpx.Response(200, json=productos, headers={"X-WP-Total": str(len(productos))})
        cuerpo = fichas.get(str(request.url))
        if cuerpo is None:
            return httpx.Response(404, text="no such product")
        return httpx.Response(200, text=cuerpo)

    store = DeditosStore(Config(database_url="x", request_delay=0.0), [_HOJA])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def _crudo(pid: int) -> dict[str, Any]:
    return {
        "id": pid,
        "name": f"Zapato barefoot {pid}",
        "permalink": f"https://deditosbarefoot.com/tienda/ninos/zapatos-ninos/zapato-{pid}/",
        "categories": [{"slug": "ninos"}, {"slug": "zapatos-ninos"}],
        "tags": [],
        "attributes": [],
        "images": [],
        "variations": [{"id": pid * 10}],
    }


def _ficha(pid: int) -> str:
    variaciones = [
        {
            "variation_id": pid * 10,
            "sku": f"sku-{pid}",
            "display_price": "39.95",
            "display_regular_price": "39.95",
            "is_in_stock": True,
            "attributes": {"attribute_pa_talla": "24"},
        }
    ]
    return (
        f'<form data-product_id="{pid}" class="variations_form" '
        f"data-product_variations='{json.dumps(variaciones)}'></form>"
    )


def _fichas_de(*pids: int) -> dict[str, str]:
    return {_crudo(pid)["permalink"]: _ficha(pid) for pid in pids}


def test_la_hoja_que_responde_vacia_a_la_primera_es_una_hoja_muerta() -> None:
    """200 con `[]` no es «se ha quedado sin catálogo»: es la mentira del punto 4."""
    store = _store_sirviendo({})
    assert list(store.list_catalog()) == []
    informe = store.scan_report()
    assert informe.leaves_failed == 1
    assert informe.failed_leaves == ["ninos"]
    assert informe.failed_scopes == set(store.scopes())


def test_la_pagina_vacia_a_partir_de_la_segunda_es_el_fin_normal() -> None:
    store = _store_sirviendo(
        {1: [_crudo(i) for i in range(1, _PAGE_SIZE + 1)], 2: []},
        _fichas_de(*range(1, _PAGE_SIZE + 1)),
    )
    entradas = list(store.list_catalog())
    assert len(entradas) == _PAGE_SIZE
    informe = store.scan_report()
    assert informe.leaves_total == 1 and informe.leaves_failed == 0


def test_una_pagina_incompleta_cierra_la_paginacion_sin_preguntar_de_nuevo() -> None:
    store = _store_sirviendo({1: [_crudo(1), _crudo(2)]}, _fichas_de(1, 2))
    assert len({e.retailer_product_id for e in store.list_catalog()}) == 2
    assert store.scan_report().leaves_total == 1


def test_un_producto_repetido_entre_paginas_se_emite_una_vez() -> None:
    store = _store_sirviendo(
        {1: [_crudo(i) for i in range(1, _PAGE_SIZE + 1)], 2: [_crudo(1)]},
        _fichas_de(*range(1, _PAGE_SIZE + 1)),
    )
    ids = [e.retailer_product_id for e in store.list_catalog()]
    assert len(ids) == len(set(ids))


def test_una_ficha_caida_omite_su_producto_pero_no_tumba_la_pasada() -> None:
    """Sin variantes no se emite: decir «se ha quedado sin tallas» sería peor que no verlo."""
    store = _store_sirviendo({1: [_crudo(1), _crudo(2)]}, _fichas_de(1))  # falta la del 2
    ids = [e.retailer_product_id for e in store.list_catalog()]
    assert ids == ["1"]
    assert store.scan_report().leaves_failed == 0


def test_no_se_pide_la_ficha_de_un_producto_excluido() -> None:
    """431 fichas por pasada: gastar una en un juguete para tirarlo después es tirar la petición."""
    pedidas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/wp-json/"):
            return httpx.Response(200, json=[juguete, _crudo(2)])
        pedidas.append(str(request.url))
        return httpx.Response(200, text=_ficha(2))

    juguete = _crudo(1)
    juguete["categories"] = [{"slug": "ninos"}, {"slug": "juguetes"}]

    store = DeditosStore(Config(database_url="x", request_delay=0.0), [_HOJA])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    assert [e.retailer_product_id for e in store.list_catalog()] == ["2"]
    assert pedidas == [_crudo(2)["permalink"]]


def test_fetch_details_sirve_de_la_cache_sin_volver_a_la_red() -> None:
    store = _store_sirviendo({1: [_crudo(1)]}, _fichas_de(1))
    entradas = list(store.list_catalog())
    productos = list(store.fetch_details(entradas))
    assert [p.retailer_product_id for p in productos] == ["1"]


# --------------------------------------------------------------------------------------
# check_leaves: el cero solo vale si la canaria demuestra que discrimina
# --------------------------------------------------------------------------------------


def _store_sondeando(totales: dict[str, int | None]) -> DeditosStore:
    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.params.get("category", "")
        total = totales.get(slug, 0)
        if total is None:
            return httpx.Response(500, json={"code": "boom"})
        return httpx.Response(200, json=[], headers={"X-WP-Total": str(total)})

    store = DeditosStore(Config(database_url="x", request_delay=0.0, request_retries=0), [_HOJA])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_la_hoja_con_productos_esta_viva() -> None:
    (hoja,) = _store_sondeando({"ninos": 431, _HOJA_CANARIA: 0}).check_leaves()
    assert hoja.alive is True and "431" in hoja.detail


def test_la_hoja_a_cero_esta_muerta_si_la_canaria_tambien_da_cero() -> None:
    (hoja,) = _store_sondeando({"ninos": 0, _HOJA_CANARIA: 0}).check_leaves()
    assert hoja.alive is False


def test_si_la_canaria_trae_productos_ninguna_hoja_puede_declararse_muerta() -> None:
    """Una ruta inventada con catálogo significa que el filtro ha dejado de filtrar."""
    (hoja,) = _store_sondeando({"ninos": 0, _HOJA_CANARIA: 12}).check_leaves()
    assert hoja.alive is None
    assert "canaria" in hoja.detail


def test_si_la_canaria_falla_el_cero_tampoco_vale() -> None:
    (hoja,) = _store_sondeando({"ninos": 0, _HOJA_CANARIA: None}).check_leaves()
    assert hoja.alive is None


def test_una_hoja_viva_lo_sigue_estando_aunque_la_canaria_falle() -> None:
    """La canaria solo condiciona el veredicto de muerte, que es el que hace daño."""
    (hoja,) = _store_sondeando({"ninos": 431, _HOJA_CANARIA: None}).check_leaves()
    assert hoja.alive is True


def test_un_error_de_la_hoja_no_es_una_retirada() -> None:
    (hoja,) = _store_sondeando({"ninos": None, _HOJA_CANARIA: 0}).check_leaves()
    assert hoja.alive is None and "500" in hoja.detail


def test_sin_cabecera_de_total_no_hay_veredicto() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])  # sin X-WP-Total

    store = DeditosStore(Config(database_url="x", request_delay=0.0), [_HOJA])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    (hoja,) = store.check_leaves()
    assert hoja.alive is None and "X-WP-Total" in hoja.detail


# --------------------------------------------------------------------------------------
# probe_alive
# --------------------------------------------------------------------------------------


def _store_probando(respuestas: dict[str, int]) -> DeditosStore:
    def handler(request: httpx.Request) -> httpx.Response:
        pid = request.url.path.rsplit("/", 1)[-1]
        status = respuestas.get(pid, 200)
        if status != 200:
            return httpx.Response(status, json={"code": "woocommerce_rest_product_invalid_id"})
        return httpx.Response(200, json={"id": int(pid), "name": "sigue"})

    store = DeditosStore(Config(database_url="x", request_delay=0.0, request_retries=0), [_HOJA])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def _candidato(pid: str) -> Any:
    from scraper.stores.base import DelistCandidate

    return DelistCandidate(retailer_product_id=pid, url=f"{'https://deditosbarefoot.com/x'}")


def test_el_sondeo_distingue_vivo_de_retirado() -> None:
    store = _store_probando({"2": 404, "3": 410})
    veredictos = store.probe_alive([_candidato("1"), _candidato("2"), _candidato("3")])
    assert veredictos == {
        "1": ProbeVerdict.ALIVE,
        "2": ProbeVerdict.DEAD,
        "3": ProbeVerdict.DEAD,
    }


def test_un_fallo_del_servidor_no_es_una_baja() -> None:
    """Devolver DEAD ante un 500 es como se producen bajas falsas masivas."""
    store = _store_probando({"1": 500})
    assert store.probe_alive([_candidato("1")]) == {}
