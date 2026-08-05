"""Tests de parsing de C&A con fixtures reales (golden-file).

Las fixtures son capturas del `POST /api?o=list` del 02/08/2026, elegidas para cubrir las cuatro
trampas que documenta la cabecera de `stores/c_and_a.py` — y que tienen en común que **todas
responden 200**, así que ninguna se detecta por status:

- `c_and_a_list_nino_camisetas.json`  — hoja real (`3-7-1`, página 0) con `navigation`.
- `c_and_a_list_nina_sudaderas_rebajas.json` — `3-1-7` página 1: 26 tachados, 26 con Ómnibus y
  **13 del caso «anuncia descuento pero su propio mínimo de 30 d es más barato»**, que es la señal
  entera por la que esta tienda entró.
- `c_and_a_list_hoja_muerta.json`     — `ipimId` inventado: `productCount: 0`.
- `c_and_a_list_fin_paginacion.json`  — hoja viva pasada del final: `productCount` INTACTO, 0
  productos. Es la pareja del anterior y lo que permite distinguirlos.
- `c_and_a_persisted_query_not_found.json` — hash caducado: 200 con el error en el cuerpo.

Las categorías se seleccionan **por atributos, no por índice**, para que reordenar `CATEGORIES` no
rompa los tests.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.base import ScrapeScope
from scraper.stores.c_and_a import (
    _ANCLA_QUERY as _ANCLA,
)
from scraper.stores.c_and_a import (
    _PAGE_SIZE,
    _PAGINA_INICIAL,
    CATEGORIES,
    CAndAStore,
    CategoryConfig,
    HashCaducado,
    parse_category_tree,
    parse_products,
    product_signature,
)

from .conftest import load_fixture

_NINO = "c_and_a_list_nino_camisetas.json"
_REBAJAS = "c_and_a_list_nina_sudaderas_rebajas.json"
_MUERTA = "c_and_a_list_hoja_muerta.json"
_FIN = "c_and_a_list_fin_paginacion.json"
_HASH_MALO = "c_and_a_persisted_query_not_found.json"

_CAT_NINO = CategoryConfig("3-7-1", "niño", "ropa", "camisetas")
_CAT_NINA = CategoryConfig("3-1-7", "niña", "ropa", "sudaderas")


def _productos(fixture: str = _NINO, cat: CategoryConfig = _CAT_NINO) -> list[Any]:
    return parse_products(load_fixture(fixture), cat)


# --------------------------------------------------------------------------------------
# La hoja fija el ámbito (no el producto)
# --------------------------------------------------------------------------------------


def test_el_ambito_sale_de_la_hoja_y_no_del_producto() -> None:
    """`categoryIpimId` es la categoría "de casa" del producto y NO la hoja por la que se llega.

    Un producto de `3-7-1` puede declarar `3-2-1-11`; tomarlo por la categoría mezclaría nuestro
    vocabulario con el suyo. Se comprueba que el fixture tiene de verdad ese desajuste, o el test
    pasaría por casualidad.
    """
    crudo = load_fixture(_NINO)["data"]["list"]["products"]
    propias = {p.get("categoryIpimId") for p in crudo}
    assert propias - {"3-7-1"}, "el fixture ya no tiene productos con categoría propia distinta"

    assert {(p.gender, p.section, p.category) for p in _productos()} == {
        ("niño", "ropa", "camisetas")
    }
    assert {(p.gender, p.section, p.category) for p in _productos(_REBAJAS, _CAT_NINA)} == {
        ("niña", "ropa", "sudaderas")
    }


def test_la_ropa_no_lleva_marca_barefoot() -> None:
    """C&A no tiene zapatería: `barefoot` es NULL ("no aplica"), nunca `desconocido`."""
    assert {p.barefoot for p in _productos()} == {None}


# --------------------------------------------------------------------------------------
# Precios
# --------------------------------------------------------------------------------------


def test_los_precios_son_decimal_y_no_float() -> None:
    variantes = [v for p in _productos() for v in p.variants]
    assert variantes
    for v in variantes:
        assert isinstance(v.price, Decimal)
        assert v.price > 0
        assert v.price == v.price.quantize(Decimal("0.01"))


def test_el_tachado_solo_cuenta_si_es_estrictamente_mayor() -> None:
    """La guarda de Cacles, aquí como seguro: hoy los 630 medidos eran honestos."""
    for p in _productos(_REBAJAS, _CAT_NINA):
        for v in p.variants:
            if v.list_price is not None:
                assert v.list_price > v.price


def test_un_tachado_igual_al_precio_se_descarta() -> None:
    crudo = deepcopy(load_fixture(_REBAJAS))
    variante = crudo["data"]["list"]["products"][0]["variants"][0]
    variante["price"]["strikePrice"] = variante["price"]["grossPrice"]
    assert parse_products(crudo, _CAT_NINA)[0].variants[0].list_price is None


def test_se_captura_el_minimo_de_30_dias_que_declara_la_tienda() -> None:
    """Es el motivo entero de elegir esta tienda: sin esto, el PR no compra nada."""
    con_min = [v for p in _productos(_REBAJAS, _CAT_NINA) for v in p.variants if v.retailer_min_30d]
    assert con_min, "el fixture debería traer variantes con lowestPrice30Days"
    for v in con_min:
        assert isinstance(v.retailer_min_30d, Decimal)


def test_el_fixture_trae_el_caso_que_delata_el_descuento_inflado() -> None:
    """Prendas que anuncian rebaja y cuyo propio mínimo de 30 d está POR DEBAJO del precio de hoy.

    Medido el 02/08/2026: 67 de 364 en el catálogo entero. Si este test deja de encontrar ninguna,
    o el fixture ha cambiado o el campo ha dejado de parsearse, y las dos cosas importan.
    """
    sospechosas = [
        v
        for p in _productos(_REBAJAS, _CAT_NINA)
        for v in p.variants
        if v.list_price is not None
        and v.retailer_min_30d is not None
        and v.retailer_min_30d < v.price
    ]
    assert sospechosas, "el fixture ya no contiene el caso «anuncia descuento pero no es mínimo»"


def test_el_precio_del_color_se_replica_en_todas_sus_tallas() -> None:
    """En C&A el precio cuelga del color; nuestro modelo lo sigue por talla."""
    for p in _productos():
        por_color: dict[str | None, set[Decimal]] = {}
        for v in p.variants:
            por_color.setdefault(v.color, set()).add(v.price)
        for precios in por_color.values():
            assert len(precios) == 1


# --------------------------------------------------------------------------------------
# Identificadores, tallas e imágenes
# --------------------------------------------------------------------------------------


def test_la_tienda_repite_usims_dentro_de_UNA_MISMA_pagina() -> None:
    """Documenta el dato que obliga a deduplicar: no es teoría, y no es solo entre páginas.

    Medido el 02/08/2026: la página 0 de `3-7-1` trae 60 items con 56 `usim` distintos. `parse_*`
    es pura y devuelve lo que hay; deduplicar es trabajo de `list_catalog()` (ver el test de más
    abajo), que es donde se sabe qué se ha emitido ya.
    """
    ids = [p.retailer_product_id for p in _productos()]
    assert len(ids) > len(set(ids)), "el fixture ya no trae usims repetidos en la misma página"


def test_dentro_de_un_producto_los_ids_de_variante_son_unicos() -> None:
    for p in _productos():
        ids = [v.retailer_variant_id for v in p.variants]
        assert len(ids) == len(set(ids))


def test_el_id_de_variante_es_el_skuId_producto_color_talla() -> None:
    for p in _productos():
        for v in p.variants:
            assert v.retailer_variant_id.startswith(f"{p.retailer_product_id}.")
            assert v.retailer_variant_id.count(".") == 2


def test_una_talla_sin_skuId_se_descarta() -> None:
    """Sin identificador estable no se puede seguir ni descatalogar: mejor no ingerirla."""
    crudo = deepcopy(load_fixture(_NINO))
    primera = crudo["data"]["list"]["products"][0]
    antes = len(parse_products(crudo, _CAT_NINO)[0].variants)
    del primera["variants"][0]["sizes"][0]["skuId"]
    assert len(parse_products(crudo, _CAT_NINO)[0].variants) == antes - 1


def test_todas_las_variantes_traen_talla() -> None:
    assert all(v.size for p in _productos() for v in p.variants)


def test_las_fotos_se_atribuyen_al_mismo_color_que_las_variantes() -> None:
    """Si los dos nombres se desalinean, la ficha empareja foto y precio mal, en silencio."""
    for p in _productos():
        colores_variante = {v.color for v in p.variants}
        for img in p.images:
            assert img.color in colores_variante
        if p.images:
            assert p.image_url == p.images[0].url
            assert p.image_url.startswith("https://www.c-and-a.com/image/upload/")


def test_un_producto_sin_variantes_con_precio_no_se_ingiere() -> None:
    crudo = deepcopy(load_fixture(_NINO))
    for v in crudo["data"]["list"]["products"][0]["variants"]:
        v["price"]["grossPrice"] = None
    ids = {p.retailer_product_id for p in parse_products(crudo, _CAT_NINO)}
    assert str(crudo["data"]["list"]["products"][0]["usim"]) not in ids


# --------------------------------------------------------------------------------------
# Huella
# --------------------------------------------------------------------------------------


def test_la_huella_cambia_con_el_precio_y_con_el_stock() -> None:
    """El stock entra porque aquí el listado ES el detalle: no hay otra petición que lo traiga."""
    crudo = load_fixture(_NINO)
    base = product_signature(parse_products(crudo, _CAT_NINO)[0])

    otro_precio = deepcopy(crudo)
    otro_precio["data"]["list"]["products"][0]["variants"][0]["price"]["grossPrice"] = 999.99
    assert product_signature(parse_products(otro_precio, _CAT_NINO)[0]) != base

    otro_stock = deepcopy(crudo)
    talla = otro_stock["data"]["list"]["products"][0]["variants"][0]["sizes"][0]
    talla["isAvailable"] = not talla["isAvailable"]
    assert product_signature(parse_products(otro_stock, _CAT_NINO)[0]) != base


def test_la_huella_NO_cambia_con_el_minimo_de_30_dias() -> None:
    """La tienda lo recalcula a diario: meterlo forzaría a reingerir el catálogo entero cada día."""
    crudo = load_fixture(_REBAJAS)
    base = product_signature(parse_products(crudo, _CAT_NINA)[0])
    otro = deepcopy(crudo)
    otro["data"]["list"]["products"][0]["variants"][0]["price"]["lowestPrice30Days"] = 0.01
    assert product_signature(parse_products(otro, _CAT_NINA)[0]) == base


# --------------------------------------------------------------------------------------
# Las cuatro respuestas 200 que no significan lo mismo
# --------------------------------------------------------------------------------------


def test_un_hash_caducado_no_se_confunde_con_una_hoja_vacia() -> None:
    """Es 200 con el error en el cuerpo. Sin mirar `errors`, TODAS las hojas parecerían vacías."""
    with pytest.raises(HashCaducado):
        parse_products(load_fixture(_HASH_MALO), _CAT_NINO)


def test_el_hash_caducado_se_resuelve_del_bundle_y_la_pasada_continua() -> None:
    """El día que C&A despliega, la pasada no debe caerse: relee el hash y sigue.

    Se comprueba además que el hash nuevo se usa en la petición siguiente — si se resolviera pero
    no se guardara, cada página pagaría el bundle de 1,6 MB.
    """
    import json

    nuevo = "b" * 64
    bundle = f'…refetchOnZeroResults:$refetchOnZeroResults){{…{_ANCLA}}}${{x}}`,sha256:"{nuevo}"}}'
    usados: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/static/"):
            assert request.url.path == "/static/main.deadbeef.js"
            return httpx.Response(200, text=bundle)
        usado = json.loads(request.content)["extensions"]["persistedQuery"]["sha256Hash"]
        usados.append(usado)
        if usado != nuevo:
            return httpx.Response(
                200, json=load_fixture(_HASH_MALO), headers={"x-release-hash": "deadbeef"}
            )
        return httpx.Response(200, json=_pagina([7], 1))

    store = CAndAStore(Config(database_url="x", request_delay=0.0), [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    assert [e.retailer_product_id for e in store.list_catalog()] == ["7"]
    assert usados[0] != nuevo and usados[1] == nuevo


def test_si_el_hash_sigue_fallando_tras_releer_el_bundle_no_se_insiste() -> None:
    """Reintentar en bucle no arregla un problema que no es el despliegue de C&A."""
    import json

    intentos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/static/"):
            return httpx.Response(200, text=f'{_ANCLA}`,sha256:"{"c" * 64}"}}')
        intentos.append(json.loads(request.content)["extensions"]["persistedQuery"]["sha256Hash"])
        return httpx.Response(
            200, json=load_fixture(_HASH_MALO), headers={"x-release-hash": "deadbeef"}
        )

    store = CAndAStore(Config(database_url="x", request_delay=0.0), [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    with pytest.raises(HashCaducado):
        list(store.list_catalog())
    assert len(intentos) == 2  # el pinneado y el releído, y ahí se para


def test_check_leaves_nombra_el_hash_caducado_en_vez_de_decir_solo_que_fallo() -> None:
    """El `detail` es lo único que se lee cuando el vigía canta: «HTTP 200» no diría nada."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/static/"):
            return httpx.Response(200, text=f'{_ANCLA}`,sha256:"{"c" * 64}"}}')
        return httpx.Response(
            200, json=load_fixture(_HASH_MALO), headers={"x-release-hash": "deadbeef"}
        )

    store = CAndAStore(Config(database_url="x", request_delay=0.0), [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    hojas = list(store.check_leaves())
    assert [h.alive for h in hojas] == [None]  # sin veredicto, NO "retirada"
    assert "desplegado" in hojas[0].detail or "caducó" in hojas[0].detail


def test_la_hoja_muerta_y_el_fin_de_paginacion_se_distinguen_por_productCount() -> None:
    muerta = load_fixture(_MUERTA)["data"]["list"]
    fin = load_fixture(_FIN)["data"]["list"]
    assert muerta["products"] == [] and fin["products"] == []
    assert muerta["productCount"] == 0
    assert fin["productCount"] > 0  # la hoja viva conserva su contador


# --------------------------------------------------------------------------------------
# list_catalog: paginación desde 0, deduplicado y hojas comprometidas
# --------------------------------------------------------------------------------------


def _pagina(usims: list[int], product_count: int) -> dict[str, Any]:
    return {
        "data": {
            "list": {
                "ipimId": "3-7-1",
                "page": 0,
                "productCount": product_count,
                "products": [
                    {
                        "usim": str(u),
                        "name": f"Camiseta {u}",
                        "uri": f"/es/es/shop/camiseta-{u}/1",
                        "variants": [
                            {
                                "uri": f"/es/es/shop/camiseta-{u}/1",
                                "color": {"label": "azul"},
                                "price": {
                                    "grossPrice": 9.99,
                                    "strikePrice": None,
                                    "lowestPrice30Days": None,
                                },
                                "sizes": [
                                    {"label": "116", "isAvailable": True, "skuId": f"{u}.1.116"}
                                ],
                            }
                        ],
                    }
                    for u in usims
                ],
            }
        }
    }


def _store_sirviendo(paginas: dict[int, dict[str, Any]], product_count: int = 999) -> CAndAStore:
    """CAndAStore cuyo cliente HTTP devuelve una respuesta sintética por número de página."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        page = json.loads(request.content)["variables"]["page"]
        return httpx.Response(200, json=paginas.get(page, _pagina([], product_count)))

    store = CAndAStore(Config(database_url="x", request_delay=0.0), [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_la_paginacion_arranca_en_cero_y_no_se_salta_la_primera_pagina() -> None:
    """`page: 1` no es la primera página: es la segunda, y las páginas son disjuntas.

    Con `_PAGINA_INICIAL = 1` este test vería 1 producto en vez de 2 — que es exactamente el
    tercio de catálogo que se perdía en silencio.
    """
    assert _PAGINA_INICIAL == 0
    # La página 0 va LLENA a propósito: una página incompleta es la última, así que con menos de
    # `_PAGE_SIZE` productos no se llegaría a pedir la siguiente y el test no probaría nada.
    llena = list(range(1, _PAGE_SIZE + 1))
    store = _store_sirviendo({0: _pagina(llena, _PAGE_SIZE + 1), 1: _pagina([999], _PAGE_SIZE + 1)})
    ids = [e.retailer_product_id for e in store.list_catalog()]
    assert ids[0] == "1", "no se ha leído la página 0"
    assert ids[-1] == "999", "no se ha leído la página 1"


def test_un_usim_repetido_dentro_de_la_hoja_solo_se_emite_una_vez() -> None:
    """172 items brutos daban 165 productos únicos: los repetidos son reales, no teóricos."""
    store = _store_sirviendo(
        {0: _pagina(list(range(1, _PAGE_SIZE + 1)), 200), 1: _pagina([1, 99], 200)}
    )
    ids = [e.retailer_product_id for e in store.list_catalog()]
    assert len(ids) == len(set(ids))
    assert "99" in ids


def test_una_pagina_incompleta_es_la_ultima_y_ahorra_la_peticion_siguiente() -> None:
    pedidas: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        page = json.loads(request.content)["variables"]["page"]
        pedidas.append(page)
        return httpx.Response(200, json=_pagina([1], 1) if page == 0 else _pagina([], 1))

    store = CAndAStore(Config(database_url="x", request_delay=0.0), [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    list(store.list_catalog())
    assert pedidas == [0]  # no se preguntó "¿hay más?"
    assert store.scan_report().leaves_failed == 0


def test_una_hoja_con_productCount_cero_se_trata_como_retirada() -> None:
    """Si no, la ingesta lee «este ámbito se ha quedado vacío» y descataloga todo lo que colgaba."""
    store = _store_sirviendo({0: _pagina([], 0)}, product_count=0)
    assert list(store.list_catalog()) == []
    informe = store.scan_report()
    assert informe.leaves_failed == 1
    assert ScrapeScope("niño", "ropa", "camisetas") in informe.failed_scopes
    # Nombrada por su `ipimId` (#155), que es justo el identificador que no cambia con el idioma.
    assert informe.failed_leaves == [_CAT_NINO.ipim_id]


def test_una_hoja_viva_pero_vacia_en_la_primera_pagina_no_se_da_por_muerta() -> None:
    """`productCount` intacto = la tienda dice que hay catálogo aunque esta página venga vacía."""
    store = _store_sirviendo({0: _pagina([], 172)}, product_count=172)
    assert list(store.list_catalog()) == []
    assert store.scan_report().leaves_failed == 0


def test_agotar_el_tope_de_paginas_compromete_la_hoja() -> None:
    """Lo que no se ha llegado a mirar no está retirado: contarla como sana provocaría bajas."""
    llenas = {p: _pagina(list(range(p * 100, p * 100 + _PAGE_SIZE)), 99999) for p in range(0, 40)}
    store = _store_sirviendo(llenas, product_count=99999)
    list(store.list_catalog())
    assert store.scan_report().leaves_failed == 1
    assert store.scan_report().failed_leaves == [_CAT_NINO.ipim_id]


# --------------------------------------------------------------------------------------
# Árbol de categorías
# --------------------------------------------------------------------------------------


def test_el_arbol_se_lee_de_navigation() -> None:
    """Las hojas se le preguntan a la tienda: aquí un ipimId inventado no da error, da 0."""
    nodos = parse_category_tree(load_fixture(_NINO), "3-7-1")
    assert nodos
    assert all(n.path.startswith("3-7-1-") for n in nodos)
    assert all(n.count is None for n in nodos)  # navigation no publica el conteo por hijo
    assert all(n.title for n in nodos)


def test_las_hojas_configuradas_son_ipimId_y_no_etiquetas() -> None:
    """Las etiquetas cambian con el idioma; los ipimId no. Por eso el mapa va por id."""
    store = CAndAStore(Config(database_url="x"))
    hojas = list(store.mapped_leaves())
    assert hojas
    assert all(h[0].isdigit() and "-" in h for h in hojas)


def test_los_ambitos_declarados_son_los_de_las_hojas_configuradas() -> None:
    """Declarar de menos dejaría productos imposibles de dar de baja."""
    store = CAndAStore(Config(database_url="x"))
    scopes = set(store.scopes())
    assert ScrapeScope("niña", "ropa", "vestidos") in scopes
    assert ScrapeScope("niño", "ropa", "vestidos") not in scopes  # correcto: no hay
    assert all(s.section == "ropa" for s in scopes)  # C&A no tiene zapatería


def test_conjuntos_va_detras_de_las_hojas_del_brief_de_su_genero() -> None:
    """El invariante de orden del que depende que #192 sea correcto.

    `conjuntos` es la categoría de la prenda que no tiene ninguna de las cinco del brief como casa
    natural. Con «gana la primera» —el `emitted` de `list_catalog()`—, ir DETRÁS significa
    que un conjunto que la tienda además
    publica bajo una de las cinco conserva esa categoría, y solo se etiqueta `conjuntos` el que no
    sale en ninguna otra hoja — o sea que quien decide es la taxonomía de la tienda, no quien mapea.

    Si alguien reordenara `CATEGORIES`, las prendas que hoy entran bien como `pantalones` o
    `sudaderas` pasarían a `conjuntos` **en silencio**, partiendo su histórico de precio en dos
    categorías. Sin este test lo único que lo impedía era un comentario.
    """
    for genero in {c.gender for c in CATEGORIES}:
        conjuntos = [
            i for i, c in enumerate(CATEGORIES) if c.gender == genero and c.category == "conjuntos"
        ]
        if not conjuntos:
            continue
        del_brief = [
            i
            for i, c in enumerate(CATEGORIES)
            if c.gender == genero and c.section == "ropa" and c.category != "conjuntos"
        ]
        assert min(conjuntos) > max(del_brief), (
            f"en {genero!r} una hoja de `conjuntos` va por delante de una del brief: se quedaría "
            "con prendas que la tienda publica además como pantalones/camisetas/sudaderas/..."
        )
