"""Tests de parsing de Cacles Barefoot con fixtures reales (golden-file).

El fixture `cacles_collection_infantil.json` es una captura recortada de
`/collections/infantil/products.json`, elegida para cubrir los seis destinos del mapeo de
`product_type`, las dos formas de variante (solo talla / talla+color), rebajas reales,
`compare_at_price` igual al precio, y los dos tipos que se excluyen.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from typing import Any

import httpx

from scraper.config import Config
from scraper.stores.cacles import (
    _PAGE_SIZE,
    CaclesStore,
    CategoryConfig,
    _categoria_desde_tipo,
    _genero_desde_tags,
    parse_products,
    product_signature,
)

from .conftest import load_fixture

_FIXTURE = "cacles_collection_infantil.json"
_COLECCION = CategoryConfig("infantil", "infantil (test)")


def _productos() -> list[Any]:
    return parse_products(load_fixture(_FIXTURE))


# --------------------------------------------------------------------------------------
# Mapeo de product_type -> sección/categoría
# --------------------------------------------------------------------------------------


def test_el_mapeo_cubre_los_seis_destinos() -> None:
    """El fixture se eligió para tocar los seis, así que un mapeo roto se nota aquí."""
    destinos = {(p.section, p.category) for p in _productos()}
    assert destinos == {
        ("zapateria", "sandalias"),
        ("zapateria", "botas"),
        ("zapateria", "zapatillas"),
        ("zapateria", "zapatos"),
        ("zapateria", "plantillas"),
        ("ropa", "ropa-interior"),
    }


def test_los_tipos_no_seguibles_se_excluyen() -> None:
    """Una tarjeta regalo no tiene precio que vigilar y el medidor de pie es un accesorio."""
    assert _categoria_desde_tipo("Tarjetas de regalo") is None
    assert _categoria_desde_tipo("medición") is None

    crudos = load_fixture(_FIXTURE)["products"]
    tipos = {p["product_type"] for p in crudos}
    assert {"Tarjetas de regalo", "medición"} <= tipos, "el fixture debe traerlos para probar esto"
    assert len(_productos()) == len(crudos) - 2


def test_un_tipo_desconocido_cae_en_zapatos_y_no_se_pierde() -> None:
    """Cacles añade tipos cada temporada: clasificar de más es mejor que perder el producto."""
    assert _categoria_desde_tipo("Katiuskas espaciales") == ("zapateria", "zapatos")


def test_las_sandalias_y_las_botas_no_se_colapsan_en_zapatos() -> None:
    """Los dos slugs que estrena esta tienda: colapsarlos tiraba información que Cacles da."""
    por_categoria = Counter(p.category for p in _productos())
    assert por_categoria["sandalias"] > 0
    assert por_categoria["botas"] > 0


# --------------------------------------------------------------------------------------
# Género
# --------------------------------------------------------------------------------------


def test_el_genero_sale_de_los_tags_boys_girls() -> None:
    assert _genero_desde_tags(["boys", "infantil"]) == "niño"
    assert _genero_desde_tags(["girls", "infantil"]) == "niña"
    assert _genero_desde_tags(["boys", "girls"]) == "unisex"


def test_sin_tags_de_genero_se_asume_unisex() -> None:
    """Esconderlo sería peor que enseñarlo de más: unisex lo deja visible en ambos filtros."""
    assert _genero_desde_tags([]) == "unisex"
    assert _genero_desde_tags(["infantil", "novedad"]) == "unisex"


def test_el_catalogo_es_mayoritariamente_unisex() -> None:
    """No es un caso raro sino el mayoritario, y es la razón de que el web tuviera que aprender
    a devolver `unisex` tanto al filtrar por niño como por niña."""
    generos = Counter(p.gender for p in _productos())
    assert set(generos) <= {"niño", "niña", "unisex"}
    assert generos["unisex"] > generos["niña"] + generos["niño"]


# --------------------------------------------------------------------------------------
# Precios
# --------------------------------------------------------------------------------------


def test_los_precios_son_decimal_desde_la_cadena() -> None:
    """Shopify da "52.90", no céntimos. Y nunca `Decimal(float)`, que arrastra basura binaria."""
    variantes = [v for p in _productos() for v in p.variants]
    assert variantes
    for v in variantes:
        assert isinstance(v.price, Decimal)
        assert v.price == v.price.quantize(Decimal("0.01"))


def test_compare_at_price_igual_al_precio_no_es_precio_tachado() -> None:
    """El fallo que más ensuciaría el detector de ofertas engañosas.

    Cacles manda `compare_at_price` igual a `price` en más de la mitad del catálogo (248 de 428
    el 31/07/2026). Darlo por bueno inventaría un descuento del 0 % en todos ellos.
    """
    crudos = load_fixture(_FIXTURE)["products"]
    iguales = [
        p
        for p in crudos
        for v in p["variants"]
        if v.get("compare_at_price") and float(v["compare_at_price"]) == float(v["price"])
    ]
    assert iguales, "el fixture debe traer algún compare_at_price == price para probar esto"

    for p in _productos():
        for v in p.variants:
            assert v.list_price is None or v.list_price > v.price


def test_hay_rebajas_reales_con_precio_tachado() -> None:
    """La otra mitad: cuando el tachado es mayor, sí se conserva (es la señal del producto)."""
    tachados = [v for p in _productos() for v in p.variants if v.list_price is not None]
    assert tachados, "el fixture debe traer rebajas reales"
    assert all(v.list_price > v.price for v in tachados)


# --------------------------------------------------------------------------------------
# Variantes: talla y color
# --------------------------------------------------------------------------------------


def test_todas_las_variantes_traen_talla() -> None:
    """Seguir por talla es requisito del brief, así que una variante sin talla es un fallo.

    Es además el caso que se coló al escribir el scraper: buscar literalmente la opción `Talla`
    perdía la talla de los productos donde Cacles la llama `Size EUR` o deja el `Title` que pone
    Shopify por defecto. El fixture trae los dos.
    """
    variantes = [v for p in _productos() for v in p.variants]
    sin_talla = [v for v in variantes if not v.size]
    assert not sin_talla, f"{len(sin_talla)} variantes sin talla"


def test_la_opcion_de_talla_se_reconoce_aunque_no_se_llame_talla() -> None:
    nombres = {
        o["name"]
        for p in load_fixture(_FIXTURE)["products"]
        for o in p["options"]
        if o["name"] != "Color"
    }
    assert len(nombres) > 1, "el fixture debe traer más de un nombre de opción de talla"


def test_el_color_solo_se_lee_de_la_opcion_llamada_color() -> None:
    """La mayoría de productos solo tienen talla (Cacles publica cada color por separado).

    Si el color se leyera por posición, `option1` metería la talla en el color de todo el
    catálogo sin opción de color.
    """
    productos = _productos()
    con_color = [p for p in productos if any(v.color for v in p.variants)]
    sin_color = [p for p in productos if not any(v.color for v in p.variants)]
    assert con_color and sin_color, "el fixture debe traer las dos formas"

    tallas = {v.size for p in productos for v in p.variants if v.size}
    colores = {v.color for p in productos for v in p.variants if v.color}
    assert not (tallas & colores), "ninguna talla debe haber acabado en el campo color"


def test_los_ids_de_variante_son_unicos_y_estables() -> None:
    ids = [v.retailer_variant_id for p in _productos() for v in p.variants]
    assert len(ids) == len(set(ids))
    assert all(i.isdigit() for i in ids), "el id de variante de Shopify es numérico"


def test_los_ids_de_producto_son_unicos() -> None:
    ids = [p.retailer_product_id for p in _productos()]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------------------
# Barefoot
# --------------------------------------------------------------------------------------


def test_todo_el_calzado_sale_barefoot_sin_heuristica() -> None:
    """La tienda es barefoot nativa: nada debe quedar en `desconocido`, que es invisible.

    Incluye productos como "Zapatos colegiales", que no nombran el concepto en ningún sitio y
    con la heurística de texto habrían salido `desconocido`.
    """
    calzado = [p for p in _productos() if p.section == "zapateria"]
    assert calzado
    assert {p.barefoot for p in calzado} == {"si"}

    colegiales = [p for p in calzado if "colegial" in p.name.lower()]
    assert colegiales, "el fixture debe traer colegiales (el caso que la heurística fallaría)"
    assert all(p.barefoot == "si" for p in colegiales)


def test_la_ropa_no_lleva_marca_de_barefoot() -> None:
    """En ropa la pregunta no aplica: la columna se queda en NULL."""
    ropa = [p for p in _productos() if p.section == "ropa"]
    assert ropa, "el fixture debe traer los calcetines"
    assert all(p.barefoot is None for p in ropa)


# --------------------------------------------------------------------------------------
# Imágenes
# --------------------------------------------------------------------------------------


def test_la_foto_primaria_sale_de_la_galeria() -> None:
    """Una sola fuente de verdad: `image_url` es la primera de `images`."""
    for p in _productos():
        assert p.image_url == (p.images[0].url if p.images else None)


def test_el_color_de_la_foto_coincide_con_el_de_alguna_variante() -> None:
    """Es la clave con la que la ficha empareja foto y precio (ver `base.ScrapedImage`).

    `None` está permitido: es la foto que no se puede atribuir a un color concreto.
    """
    for p in _productos():
        colores_variante = {v.color for v in p.variants} | {None}
        assert {i.color for i in p.images} <= colores_variante, p.name


def test_una_foto_de_varios_colores_no_se_atribuye_a_uno_al_azar() -> None:
    payload = {
        "products": [
            {
                "id": 1,
                "title": "Zapato de prueba",
                "handle": "zapato-prueba",
                "product_type": "Zapatos",
                "tags": ["boys", "girls"],
                "options": [
                    {"name": "Talla", "position": 1},
                    {"name": "Color", "position": 2},
                ],
                "variants": [
                    {"id": 10, "option1": "24", "option2": "Azul", "price": "10.00"},
                    {"id": 11, "option1": "24", "option2": "Rojo", "price": "10.00"},
                ],
                "images": [
                    {"src": "https://cdn/compartida.jpg", "variant_ids": [10, 11]},
                    {"src": "https://cdn/azul.jpg", "variant_ids": [10]},
                ],
            }
        ]
    }
    (producto,) = parse_products(payload)
    por_url = {i.url: i.color for i in producto.images}
    assert por_url["https://cdn/compartida.jpg"] is None  # cubre dos colores: genérica
    assert por_url["https://cdn/azul.jpg"] == "Azul"


# --------------------------------------------------------------------------------------
# Huella
# --------------------------------------------------------------------------------------


def test_la_huella_es_determinista() -> None:
    uno, otro = _productos(), _productos()
    assert [product_signature(p) for p in uno] == [product_signature(p) for p in otro]
    assert all(product_signature(p) for p in uno), "ninguna huella debería salir vacía"


def test_la_huella_cambia_con_el_precio_y_con_el_stock() -> None:
    """El stock entra en la huella porque aquí el listado YA es el detalle: no hay una segunda
    petición que fuese a recogerlo después."""
    crudo = load_fixture(_FIXTURE)
    base = product_signature(parse_products(crudo)[0])

    otro_precio = deepcopy(crudo)
    otro_precio["products"][0]["variants"][0]["price"] = "999.99"
    assert product_signature(parse_products(otro_precio)[0]) != base

    otro_stock = deepcopy(crudo)
    v = otro_stock["products"][0]["variants"][0]
    v["available"] = not v.get("available")
    assert product_signature(parse_products(otro_stock)[0]) != base


# --------------------------------------------------------------------------------------
# list_catalog: la colección vacía y la paginación
# --------------------------------------------------------------------------------------


def _store_sirviendo(paginas: dict[int, dict[str, Any]]) -> CaclesStore:
    """CaclesStore cuyo cliente HTTP devuelve una respuesta sintética por número de página."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=paginas.get(page, {"products": []}))

    store = CaclesStore(Config(database_url="x", request_delay=0.0), [_COLECCION])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def _pagina_con(*ids: int) -> dict[str, Any]:
    return {
        "products": [
            {
                "id": pid,
                "title": f"Zapato {pid}",
                "handle": f"zapato-{pid}",
                "product_type": "Zapatos",
                "tags": ["boys", "girls"],
                "options": [{"name": "Talla", "position": 1}],
                "variants": [{"id": pid * 10, "option1": "24", "price": "10.00"}],
                "images": [],
            }
            for pid in ids
        ]
    }


def test_una_coleccion_vacia_es_una_hoja_retirada_y_no_un_catalogo_vacio() -> None:
    """EL test de este fichero.

    Shopify responde 200 con `products: []` a una colección que ya no existe —comprobado en
    vivo—, no un 404. Sin este trato, la ingesta leería "este ámbito se ha quedado vacío" y daría
    de baja el catálogo entero de la tienda. Marcada como hoja caída, `dead_ratio` sube a 1.0 y
    la ingesta se salta todas las bajas.
    """
    store = _store_sirviendo({})
    assert list(store.list_catalog()) == []

    informe = store.scan_report()
    assert informe.leaves_failed == 1
    assert informe.dead_ratio == 1.0
    # Todos los ámbitos quedan fuera de las bajas, no solo uno: esta tienda los cubre todos con
    # una única colección.
    assert informe.failed_scopes == set(store.scopes())


def test_la_pagina_vacia_despues_de_la_primera_es_el_fin_normal_de_la_paginacion() -> None:
    """La misma respuesta significa dos cosas distintas según dónde aparezca.

    El caso se da cuando la colección tiene EXACTAMENTE `_PAGE_SIZE` productos: la primera página
    viene llena, así que hay que preguntar por la siguiente, y esa sí llega vacía. Es el único
    escenario en el que sigue haciendo falta la petición de más.
    """
    store = _store_sirviendo({1: _pagina_con(*range(1, _PAGE_SIZE + 1))})
    entries = list(store.list_catalog())

    assert len(entries) == _PAGE_SIZE
    informe = store.scan_report()
    assert informe.leaves_failed == 0, "la página vacía de la 2ª no es una hoja muerta"
    assert informe.leaves_total == 1
    assert informe.dead_ratio == 0.0


def test_una_pagina_incompleta_es_la_ultima_y_no_se_pide_la_siguiente() -> None:
    """Shopify no da total, pero una página con menos de `_PAGE_SIZE` ya dice que se acabó.

    Ahorra una petición por pasada, y con esta tienda eso importa: el presupuesto es de
    complejidad, no de peticiones. Es además el modo de fallo que costó una pasada entera contra
    la tienda real (01/08/2026): las páginas 1 y 2 se leyeron bien y el 429 llegó en la 3ª, la que
    solo servía para preguntar "¿hay más?".
    """
    pedidas: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        pedidas.append(page)
        return httpx.Response(200, json=_pagina_con(1, 2))  # 2 productos < _PAGE_SIZE

    store = CaclesStore(Config(database_url="x", request_delay=0.0), [_COLECCION])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    assert [e.retailer_product_id for e in store.list_catalog()] == ["1", "2"]
    assert pedidas == [1], "no debería haber pedido una segunda página"
    assert store.scan_report().leaves_failed == 0


def test_agotar_el_tope_de_paginas_no_cuenta_como_hoja_sana() -> None:
    """Si no se llega al final de la paginación, se ha visto SOLO parte del catálogo.

    Contarla como sana sería el peor de los dos errores: los ámbitos seguirían siendo elegibles
    para bajas y, a las `delist_min_misses` pasadas, se descatalogaría producto vivo solo por no
    haber cabido en el tope. Lo que no se ha llegado a mirar no está retirado.
    """

    # Páginas SIEMPRE llenas: nunca llega la señal de final, ni por vacía ni por incompleta.
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        base = page * 1000
        return httpx.Response(200, json=_pagina_con(*range(base, base + _PAGE_SIZE)))

    store = CaclesStore(Config(database_url="x", request_delay=0.0), [_COLECCION])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    entries = list(store.list_catalog())
    assert entries, "debería haber emitido lo que sí pudo leer"

    informe = store.scan_report()
    assert informe.leaves_failed == 1, "la hoja truncada no puede contar como sana"
    assert informe.dead_ratio == 1.0
    assert informe.failed_scopes == set(store.scopes())


def test_una_pagina_solo_de_tipos_excluidos_no_es_el_final_del_catalogo() -> None:
    """El final se decide con los productos CRUDOS, no con los que sobreviven al parseo.

    Una página entera de tarjetas regalo parsea a cero. Si eso contase como página vacía, en la
    primera daría una hoja muerta falsa —y con ella la baja de todo el catálogo— y en las
    siguientes cortaría la paginación dejándose productos sin ver.
    """
    regalo = {
        "id": 77,
        "title": "Tarjeta de regalo",
        "handle": "tarjeta",
        "product_type": "Tarjetas de regalo",
        "tags": [],
        "options": [{"name": "Valor", "position": 1}],
        "variants": [{"id": 770, "option1": "10", "price": "10.00"}],
        "images": [],
    }
    paginas: dict[int, dict[str, Any]] = {
        1: {"products": [regalo] * _PAGE_SIZE},  # llena, pero no deja ni un producto seguible
        2: _pagina_con(5),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=paginas.get(page, {"products": []}))

    store = CaclesStore(Config(database_url="x", request_delay=0.0), [_COLECCION])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    assert [e.retailer_product_id for e in store.list_catalog()] == ["5"]
    informe = store.scan_report()
    assert informe.leaves_failed == 0, "una página de excluidos no es una hoja muerta"


def test_list_catalog_deduplica_por_id_entre_paginas() -> None:
    """La página 1 va llena para que la paginación siga; el 2 repetido llega en la siguiente."""
    llena = _pagina_con(*range(1, _PAGE_SIZE + 1))
    store = _store_sirviendo({1: llena, 2: _pagina_con(2, _PAGE_SIZE + 1)})
    ids = [e.retailer_product_id for e in store.list_catalog()]

    assert len(ids) == len(set(ids)), "no debe emitir dos veces el mismo id"
    assert ids[-1] == str(_PAGE_SIZE + 1), "lo nuevo de la 2ª página sí entra"
    assert len(ids) == _PAGE_SIZE + 1


def test_fetch_details_sirve_de_cache_sin_tocar_la_red() -> None:
    """El listado ya trajo el detalle completo: una petición extra por producto sobraría."""
    store = _store_sirviendo({1: _pagina_con(1, 2)})
    entries = list(store.list_catalog())

    # Si volviera a la red, el transporte fallaría: se lo quitamos.
    store._client = None  # type: ignore[assignment,method-assign]
    productos = list(store.fetch_details(entries))
    assert [p.retailer_product_id for p in productos] == ["1", "2"]


def test_los_scopes_declaran_todo_lo_que_el_parser_puede_emitir() -> None:
    """Un ámbito no declarado deja sus productos imposibles de dar de baja.

    Aquí la hoja no acota género ni categoría —los decide el parser producto a producto—, así que
    `scopes()` no se puede deducir de las colecciones como en las otras tiendas.
    """
    store = CaclesStore(Config(database_url="x"))
    declarados = set(store.scopes())
    emitidos = {(p.gender, p.section, p.category) for p in _productos()}
    for gender, section, category in emitidos:
        assert (gender, section, category) in {
            (s.gender, s.section, s.category) for s in declarados
        }


# --------------------------------------------------------------------------------------
# probe_alive
# --------------------------------------------------------------------------------------


def _store_probando(respuesta: httpx.Response | Exception) -> CaclesStore:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    store = CaclesStore(
        Config(database_url="x", request_delay=0.0, retry_backoff=0.0, request_retries=0)
    )
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def _candidato() -> Any:
    from scraper.stores.base import DelistCandidate

    return DelistCandidate("1", "https://www.caclesbarefoot.com/products/zapato-1")


def test_probe_alive_confirma_vivo_con_200_y_producto() -> None:
    producto = load_fixture("cacles_product_handle.json")
    store = _store_probando(httpx.Response(200, json=producto))
    assert store.probe_alive([_candidato()]) == {"1": True}


def test_probe_alive_confirma_retirado_con_404() -> None:
    store = _store_probando(httpx.Response(404))
    assert store.probe_alive([_candidato()]) == {"1": False}


def test_un_error_de_red_no_vale_como_prueba_de_retirada() -> None:
    """Tres estados con dos valores: sin veredicto se omite del mapa (ver `SupportsAliveProbe`).

    Devolver `False` ante un 500 o una red caída provocaría bajas masivas falsas.
    """
    for fallo in (
        httpx.Response(500),
        httpx.Response(403),
        httpx.ConnectError("red caída"),
    ):
        assert _store_probando(fallo).probe_alive([_candidato()]) == {}


def test_un_candidato_sin_url_no_se_puede_sondear() -> None:
    from scraper.stores.base import DelistCandidate

    store = _store_probando(httpx.Response(200, json={"product": {"id": 1}}))
    assert store.probe_alive([DelistCandidate("1", None)]) == {}


def test_el_410_tambien_confirma_la_retirada() -> None:
    """410 significa lo mismo que 404 y así lo declara `GONE_STATUS` en el contrato común."""
    assert _store_probando(httpx.Response(410)).probe_alive([_candidato()]) == {"1": False}


# --------------------------------------------------------------------------------------
# Educación con el servidor
# --------------------------------------------------------------------------------------


def test_retry_after_con_decimales_se_respeta() -> None:
    """Shopify manda "2.0", y `.isdigit()` —lo que usa zara.py— lo daría por no numérico.

    Se ignoraría en silencio justo en la tienda que cobra por complejidad y tarda minutos en
    rellenar el cubo.
    """
    import scraper.stores.cacles as modulo

    esperas: list[float] = []
    store = CaclesStore(Config(database_url="x", request_delay=0.0, retry_backoff=1.0))
    original = modulo.time.sleep
    modulo.time.sleep = esperas.append  # type: ignore[assignment]
    try:
        store._backoff(0, retry_after="30.0")
        store._backoff(0, retry_after="no-es-un-numero")
    finally:
        modulo.time.sleep = original

    assert esperas[0] >= 24, "debería esperar los 30 s que pide la tienda (con jitter a la baja)"
    assert esperas[1] < 2, "un valor ilegible cae al backoff exponencial, no revienta"
