"""Tests de parsing de H&M con fixtures reales (golden-file).

Las fixtures son capturas del `GET api.hm.com/search-services/v1/es_es/listing/resultpage` del
02/08/2026, recortadas a los productos que hacen falta y elegidas para cubrir lo que la cabecera de
`stores/hm.py` documenta — y que tiene en común que **todo responde 200**, así que nada de esto se
detecta por status:

- `hm_list_nino_pantalones.json` / `..._p2.json` — hoja real `/kids/boys/clothing/trousers`,
  páginas 1 y 2. El modelo `1260878` reparte sus colores **entre las dos páginas** (en la hoja real
  son 56 modelos de 157 los que lo hacen), que es por lo que `list_catalog()` acumula antes de
  emitir en vez de ir página a página como el resto de tiendas.
- `hm_list_nina_pantalones.json` — la hoja de niña trae el **mismo** modelo `1234419` que la de
  niño: es el cruce de géneros que lo convierte en `unisex` (#98).
- `hm_list_canario.json` — la ruta inventada que se pide una vez por pasada.
- `hm_list_hoja_muerta.json` — una hoja que no resuelve: devuelve **exactamente lo mismo** que el
  canario. Es la pareja del anterior y lo único que permite distinguir muerta de viva.
- `hm_list_fin_paginacion.json` — hoja viva pasada del final: 0 productos, `numberOfHits` intacto y
  **sin `nextPageNum`**.
- `hm_list_nina_zapatos.json` — calzado: tallas de número de pie y una zapatilla que la tienda
  llama «barefoot».
- `hm_list_bebe_bodies.json` — bebé: tallas en meses, que es donde el remodelado de `_talla()` se
  juega la alineación con Hipercor y Zara.

Las categorías se seleccionan **por atributos, no por índice**, para que reordenar `CATEGORIES` no
rompa los tests.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.base import ScrapeScope
from scraper.stores.hm import (
    _PAGE_SIZE,
    _PAGINA_INICIAL,
    CATEGORIES,
    CategoryConfig,
    HMStore,
    _ambito,
    _talla,
    es_espejismo,
    ids_de_pagina,
    parse_filas,
    product_signature,
    producto,
    raiz,
)

from .conftest import load_fixture

_NINO_P1 = "hm_list_nino_pantalones.json"
_NINO_P2 = "hm_list_nino_pantalones_p2.json"
_NINA = "hm_list_nina_pantalones.json"
_CANARIO = "hm_list_canario.json"
_MUERTA = "hm_list_hoja_muerta.json"
_FIN = "hm_list_fin_paginacion.json"
_ZAPATOS = "hm_list_nina_zapatos.json"
_BEBE = "hm_list_bebe_bodies.json"
# Un solo modelo (1168042) recortado de /kids/boys/clothing/jeans, capturado el 03/08/2026: seis
# artículos y DOS nombres de color repetidos dos veces cada uno ('Azul denim claro' y 'Azul denim
# oscuro'). Es el caso de la #123, y en vaqueros es de lo más corriente.
_VAQUEROS = "hm_list_nino_vaqueros.json"

_CAT_NINO = CategoryConfig("/kids/boys/clothing/trousers", "niño", "ropa", "pantalones")
_CAT_NINA = CategoryConfig("/kids/girls/clothing/trousers", "niña", "ropa", "pantalones")
_CAT_ZAPATOS = CategoryConfig("/kids/girls/shoes", "niña", "zapateria", "zapatos")
_CAT_BEBE = CategoryConfig("/baby/newborn/clothing/bodysuits", "unisex", "ropa", "ropa-interior")

_CFG = Config(database_url="postgresql://unused", request_delay=0.0, retry_backoff=0.0)


# --------------------------------------------------------------------------------------
# La talla: H&M invierte el convenio y hay que devolvérselo antes de emitirla
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        # Las 12 formas que sirve la tienda, medidas sobre el catálogo entero el 02/08/2026.
        ("104 (3-4Y)", "3-4 años (104 cm)"),
        ("134/140 (8-10Y)", "8-10 años (134/140 cm)"),
        ("68 (4-6M)", "4-6 meses (68 cm)"),
        ("50/56 (0-2M)", "0-2 meses (50/56 cm)"),
        # El `½` se conserva: `size_canon` ya lo traduce a `.5`, y 1½ años (86 cm) NO es 2 años.
        ("92 (1½-2Y)", "1½-2 años (92 cm)"),
        ("92/104 (1½-4Y)", "1½-4 años (92/104 cm)"),
        # Extremos abiertos: el `+` y el `<` son adorno, la talla es la que queda.
        ("170 (14Y+)", "14 años (170 cm)"),
        ("44 (<0-1M)", "0-1 meses (44 cm)"),
        # Calzado: el número YA es el número de pie y `size_canon` lo lee bien tal cual.
        ("24/25", "24/25"),
        ("31", "31"),
        # Lo que no encaja se devuelve crudo, nunca inventado.
        ("ONESIZE", "ONESIZE"),
        ("NOSIZE", "NOSIZE"),
        (None, None),
    ],
)
def test_la_talla_se_remodela_a_edad_delante_y_centimetros_en_el_parentesis(
    crudo: str | None, esperado: str | None
) -> None:
    assert _talla(crudo) == esperado


def test_la_talla_remodelada_queda_en_el_MISMO_formato_que_sirve_zara() -> None:
    """La razón entera de `_talla()`: que la canónica de H&M y la de Zara sean comparables.

    `size_canon` descarta el paréntesis y lee la unidad de lo que queda delante, así que lo que
    decide si dos tiendas casan es que la EDAD vaya fuera del paréntesis. Zara ya sirve
    `'5-6 años (116 cm)'`; sin remodelar, `'122/128 (6-8Y)'` caería en la rama de número de pie y
    saldría `'122-128'` — un chip que ni se lee ni cruza con nada.

    El patrón replica la regla de años de `db/migrations/0020_size_canon_rango_colapsado.sql`. Si
    esa regla cambia, este test es el que avisa de que hay que volver a mirar aquí.
    """
    regla_de_anos = re.compile(r"[0-9]+(\.[0-9]+)?\s*[-/]\s*[0-9]+(\.[0-9]+)?\s*a[nñ]o")
    como_zara = "5-6 años (116 cm)"
    como_hm = _talla("122/128 (6-8Y)")
    assert como_hm is not None
    assert regla_de_anos.search(como_zara)
    assert regla_de_anos.search(como_hm.replace("½", ".5"))
    # Y la de bebé cae en la rama de meses, que va ANTES que la de años en la función.
    bebe = _talla("74 (6-9M)")
    assert bebe is not None and re.search(r"[0-9]+\s*[-/]\s*[0-9]+\s*mes", bebe)


def test_las_tallas_de_bebe_salen_en_meses_y_no_en_centimetros() -> None:
    """Es la alineación que importa: un body de H&M y uno de Hipercor tienen que casar."""
    filas = parse_filas(load_fixture(_BEBE))
    etiquetas = {t[1] for f in filas for t in f.tallas}
    assert any(e is not None and e.endswith("meses (74 cm)") for e in etiquetas)
    assert not any(e == "74" for e in etiquetas)  # lo que saldría sin remodelar


# --------------------------------------------------------------------------------------
# El espejismo: la hoja muerta devuelve el cubo, con 200 y una página llena
# --------------------------------------------------------------------------------------


def test_la_hoja_muerta_devuelve_exactamente_lo_mismo_que_el_canario() -> None:
    """Sin esto el resto del fichero no prueba nada: es la premisa del detector."""
    canario = ids_de_pagina(load_fixture(_CANARIO))
    muerta = ids_de_pagina(load_fixture(_MUERTA))
    assert canario and muerta
    assert es_espejismo(muerta, canario)


def test_una_hoja_viva_no_se_confunde_con_el_cubo() -> None:
    viva = ids_de_pagina(load_fixture(_NINO_P1))
    canario = ids_de_pagina(load_fixture(_CANARIO))
    assert viva and not es_espejismo(viva, canario)


def test_el_espejismo_no_se_decide_por_el_contador_sino_por_los_ids() -> None:
    """`numberOfHits` deriva entre peticiones (9713 -> 9710 en segundos).

    Si el detector comparase contadores, una hoja muerta pedida un segundo después del canario
    saldría «viva», que es el error caro: se ingiere el cubo entero bajo una categoría.
    """
    canario = ids_de_pagina(load_fixture(_CANARIO))
    muerta = load_fixture(_MUERTA)
    muerta["plpList"]["numberOfHits"] += 3  # el cubo se movió entre las dos peticiones
    assert es_espejismo(ids_de_pagina(muerta), canario)


def test_una_pagina_vacia_no_es_un_espejismo() -> None:
    """No prueba nada, y quien llama ya la trata aparte (igual que en sfera y cacles)."""
    assert not es_espejismo([], ids_de_pagina(load_fixture(_CANARIO)))
    assert not es_espejismo(ids_de_pagina(load_fixture(_NINO_P1)), [])


# --------------------------------------------------------------------------------------
# Parseo: precios, tallas, variantes
# --------------------------------------------------------------------------------------


def test_los_precios_son_decimal_y_no_float() -> None:
    filas = parse_filas(load_fixture(_NINO_P1))
    assert filas
    for f in filas:
        assert isinstance(f.price, Decimal)
        assert f.price > 0


def test_hoy_no_hay_ningun_descuento_en_infantil() -> None:
    """Lo dice la cabecera y conviene que el fixture lo sostenga: 0 de 6518 filas rebajadas.

    Si algún día esto rompe es una buena noticia — y entonces el test de abajo es el que manda.
    """
    filas = parse_filas(load_fixture(_NINO_P1)) + parse_filas(load_fixture(_ZAPATOS))
    assert all(f.list_price is None for f in filas)


def test_el_tachado_sale_de_redPrice_y_solo_si_es_estrictamente_menor() -> None:
    """La forma se midió en `/ladies/sale/view-all`: `redPrice` es lo que se paga.

    La guarda del tachado <= precio es la misma que en Cacles (donde venía igual al precio en 248
    de 428 productos): sin ella se inventaría un descuento del 0 %.
    """
    pagina = load_fixture(_NINO_P1)
    crudo = pagina["plpList"]["productList"][0]
    crudo["prices"] = [
        {"priceType": "redPrice", "price": 9.99},
        {"priceType": "whitePrice", "price": 24.99},
    ]
    (fila,) = [f for f in parse_filas(pagina) if f.article_id == crudo["id"]]
    assert fila.price == Decimal("9.99")
    assert fila.list_price == Decimal("24.99")

    crudo["prices"] = [
        {"priceType": "redPrice", "price": 24.99},
        {"priceType": "whitePrice", "price": 24.99},  # tachado igual al precio: no es descuento
    ]
    (fila,) = [f for f in parse_filas(pagina) if f.article_id == crudo["id"]]
    assert fila.list_price is None


def test_una_talla_agotada_no_desaparece_del_catalogo_pero_se_marca() -> None:
    """`stock` es un código (0 / 2), no una cantidad: manda el criterio de la tienda.

    La talla agotada tiene que seguir estando: es justo la que un padre quiere seguir para que le
    avisen cuando reaparezca.
    """
    pagina = load_fixture(_ZAPATOS)
    crudo = pagina["plpList"]["productList"][0]
    tallas_crudas = crudo["sizes"]
    tallas_crudas[0]["stock"] = 0
    tallas_crudas[1]["stock"] = 2

    (fila,) = [f for f in parse_filas(pagina) if f.article_id == crudo["id"]]
    assert len(fila.tallas) == len(tallas_crudas)  # ninguna se cae
    por_id = {t[0]: t for t in fila.tallas}
    assert por_id[str(tallas_crudas[0]["id"])][2] is False
    assert por_id[str(tallas_crudas[1]["id"])][2] is True


def test_ninguna_variante_se_queda_sin_talla() -> None:
    for fixture in (_NINO_P1, _ZAPATOS, _BEBE):
        for fila in parse_filas(load_fixture(fixture)):
            assert all(etiqueta for _, etiqueta, _ in fila.tallas), fixture


def test_el_id_de_variante_es_articulo_mas_talla_y_es_unico() -> None:
    filas = parse_filas(load_fixture(_NINO_P1))
    prod = producto(filas, ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None
    ids = [v.retailer_variant_id for v in prod.variants]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"\d{10}-\w+", i) for i in ids)


# --------------------------------------------------------------------------------------
# La agrupación por raíz: una fila es producto+color, no producto
# --------------------------------------------------------------------------------------


def test_la_raiz_recorta_el_color_del_id_de_articulo() -> None:
    assert raiz("1343222003") == "1343222"
    # Un id más corto se devuelve entero: inventar un identificador agruparía productos distintos.
    assert raiz("12345") == "12345"


def test_los_colores_de_un_modelo_se_agrupan_en_un_solo_producto() -> None:
    """4 filas del mismo modelo en la página 1 son UN producto con 4 colores, no 4 productos."""
    filas = [f for f in parse_filas(load_fixture(_NINO_P1)) if f.raiz == "1260878"]
    assert len(filas) > 1
    prod = producto(filas, ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None
    assert prod.retailer_product_id == "1260878"
    assert len({v.color for v in prod.variants}) == len(filas)


def test_los_colores_repartidos_entre_paginas_acaban_en_el_mismo_producto() -> None:
    """El motivo de que `list_catalog()` acumule: en la hoja real le pasa a 56 modelos de 157."""
    p1 = [f for f in parse_filas(load_fixture(_NINO_P1)) if f.raiz == "1260878"]
    p2 = [f for f in parse_filas(load_fixture(_NINO_P2)) if f.raiz == "1260878"]
    assert p1 and p2, "las fixtures deben repartir el mismo modelo entre las dos páginas"
    prod = producto([*p1, *p2], ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None
    assert len({v.color for v in prod.variants}) == len(p1) + len(p2)


def test_la_foto_se_atribuye_al_color_que_retrata() -> None:
    """Invariante de `base.ScrapedImage`: {color de foto} ⊆ {color de variante}."""
    filas = parse_filas(load_fixture(_NINO_P1))
    prod = producto(filas, ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None and prod.images
    assert {i.color for i in prod.images} <= {v.color for v in prod.variants}
    assert prod.image_url == prod.images[0].url


# --------------------------------------------------------------------------------------
# Dos artículos con el mismo nombre de color (#123): el color no basta para atribuir la foto
# --------------------------------------------------------------------------------------


def test_dos_articulos_del_mismo_modelo_pueden_compartir_nombre_de_color() -> None:
    """El supuesto que rompe esta tienda, fijado sobre dato real antes de probar el arreglo."""
    filas = parse_filas(load_fixture(_VAQUEROS))
    assert len({f.raiz for f in filas}) == 1, "la fixture es un solo modelo"
    repetidos = {c for c in (f.color for f in filas) if [f.color for f in filas].count(c) > 1}
    assert repetidos == {"Azul denim claro", "Azul denim oscuro"}
    # Y son artículos distintos, cada uno con su ficha: por eso no se pueden fusionar.
    for color in repetidos:
        urls = {f.url for f in filas if f.color == color}
        assert len(urls) == 2, f"{color} debería venir de dos fichas distintas"


def test_la_foto_se_atribuye_tambien_a_la_ficha_de_la_que_sale() -> None:
    """Sin `variant_url` las fotos de los dos «Azul denim oscuro» caían en el mismo saco."""
    filas = parse_filas(load_fixture(_VAQUEROS))
    prod = producto(filas, ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None and prod.images

    # Cada foto lleva la URL de la fila de la que salió, la MISMA que llevan sus variantes.
    urls_de_variante = {v.url for v in prod.variants}
    assert {i.variant_url for i in prod.images} <= urls_de_variante

    # Y el reparto es exactamente el de la tienda: ninguna ficha se queda con fotos de la otra.
    por_fila = {f.url: len(f.images) for f in filas}
    for url, esperadas in por_fila.items():
        assert len([i for i in prod.images if i.variant_url == url]) == esperadas


def test_el_color_solo_no_separa_las_dos_fichas_y_la_url_si() -> None:
    """La medida de la #123 en pequeño: agrupando por color se mezclan, por (color, url) no."""
    filas = parse_filas(load_fixture(_VAQUEROS))
    prod = producto(filas, ScrapeScope("niño", "ropa", "pantalones"))
    assert prod is not None

    oscuras = [i for i in prod.images if i.color == "Azul denim oscuro"]
    assert len({i.variant_url for i in oscuras}) == 2, "son dos prendas, no una"
    # Que es justo lo que hacía que la galería enseñara 5 fotos de dos pantalones distintos.
    assert len(oscuras) == 5
    for url in {i.variant_url for i in oscuras}:
        assert 0 < len([i for i in oscuras if i.variant_url == url]) < len(oscuras)


# --------------------------------------------------------------------------------------
# El cruce de géneros (#98): publicado en las dos ramas = unisex
# --------------------------------------------------------------------------------------


def test_un_modelo_publicado_en_nino_y_en_nina_es_unisex() -> None:
    assert _ambito([_CAT_NINO, _CAT_NINA]).gender == "unisex"


def test_un_modelo_de_una_sola_rama_conserva_su_genero() -> None:
    assert _ambito([_CAT_NINO]).gender == "niño"
    assert _ambito([_CAT_NINA, _CAT_NINA]).gender == "niña"


def test_la_seccion_y_la_categoria_las_fija_la_primera_hoja() -> None:
    """Cruzar géneros sí tiene vocabulario (`unisex`); cruzar categorías no lo tiene."""
    otra = CategoryConfig("/kids/girls/clothing/dresses", "niña", "ropa", "vestidos")
    ambito = _ambito([_CAT_NINO, otra])
    assert (ambito.section, ambito.category) == ("ropa", "pantalones")


def test_una_hoja_unisex_sola_sigue_siendo_unisex() -> None:
    assert _ambito([_CAT_BEBE]).gender == "unisex"


def test_scopes_declara_tambien_los_ambitos_unisex_que_el_parser_puede_emitir() -> None:
    """Un ámbito no declarado no se cuenta como escaneado, y sus productos no se dan de baja nunca.

    Es el mismo motivo por el que `cacles.py` declara el producto cartesiano de lo que su parser
    PUEDE emitir en vez de lo que dicen sus hojas.
    """
    store = HMStore(_CFG)
    scopes = set(store.scopes())
    declarados = {ScrapeScope(c.gender, c.section, c.category) for c in CATEGORIES}
    assert declarados <= scopes
    for s in declarados:
        assert ScrapeScope("unisex", s.section, s.category) in scopes


# --------------------------------------------------------------------------------------
# Huella y barefoot
# --------------------------------------------------------------------------------------


def test_la_huella_es_determinista_y_cambia_con_el_precio_y_con_el_stock() -> None:
    filas = parse_filas(load_fixture(_NINO_P1))
    scope = ScrapeScope("niño", "ropa", "pantalones")
    prod = producto(filas, scope)
    assert prod is not None
    assert product_signature(prod) == product_signature(producto(filas, scope))  # type: ignore[arg-type]

    caras = [f.__class__(**{**f.__dict__, "price": f.price + Decimal("1")}) for f in filas]
    assert product_signature(producto(caras, scope)) != product_signature(prod)  # type: ignore[arg-type]

    agotadas = [
        f.__class__(**{**f.__dict__, "tallas": tuple((i, e, False) for i, e, _ in f.tallas)})
        for f in filas
    ]
    assert product_signature(producto(agotadas, scope)) != product_signature(prod)  # type: ignore[arg-type]


def test_barefoot_solo_se_pregunta_en_zapateria() -> None:
    ropa = producto(parse_filas(load_fixture(_NINO_P1)), ScrapeScope("niño", "ropa", "pantalones"))
    assert ropa is not None and ropa.barefoot is None

    filas = parse_filas(load_fixture(_ZAPATOS))
    por_raiz: dict[str, list[Any]] = {}
    for f in filas:
        por_raiz.setdefault(f.raiz, []).append(f)
    productos = [
        producto(fs, ScrapeScope("niña", "zapateria", "zapatos")) for fs in por_raiz.values()
    ]
    valores = {p.barefoot for p in productos if p is not None}
    assert valores <= {"si", "no", "desconocido"} and valores
    # La tienda nombra el concepto: «Zapatillas deportivas barefoot» está en el catálogo de hoy.
    assert any(p is not None and p.barefoot == "si" for p in productos)


# --------------------------------------------------------------------------------------
# list_catalog: paginación desde 1, agrupación entre hojas y hojas comprometidas
# --------------------------------------------------------------------------------------


def _handler(
    paginas: dict[tuple[str, int], str], vistas: list[tuple[str, int]] | None = None
) -> Any:
    """Sirve fixtures por (pageId, page). Lo que no esté mapeado responde el canario."""

    def handler(request: httpx.Request) -> httpx.Response:
        page_id = request.url.params["pageId"]
        page = int(request.url.params["page"])
        if vistas is not None:
            vistas.append((page_id, page))
        fixture = paginas.get((page_id, page), _CANARIO)
        return httpx.Response(200, json=load_fixture(fixture))

    return handler


def _store(paginas: dict[tuple[str, int], str], cats: list[CategoryConfig], **kw: Any) -> HMStore:
    store = HMStore(_CFG, cats)
    store._client = lambda: httpx.Client(  # type: ignore[method-assign]
        transport=httpx.MockTransport(_handler(paginas, **kw))
    )
    return store


def test_la_paginacion_arranca_en_1_porque_la_pagina_0_es_un_422() -> None:
    """A diferencia de C&A, donde empezar en 1 se saltaba un tercio de cada hoja en silencio."""
    vistas: list[tuple[str, int]] = []
    store = _store({(_CAT_NINO.page_id, 1): _NINO_P1}, [_CAT_NINO], vistas=vistas)
    list(store.list_catalog())
    paginas_pedidas = [p for pid, p in vistas if pid == _CAT_NINO.page_id]
    assert min(paginas_pedidas) == _PAGINA_INICIAL == 1


def test_una_pagina_incompleta_es_la_ultima_y_ahorra_la_peticion_siguiente() -> None:
    vistas: list[tuple[str, int]] = []
    store = _store({(_CAT_NINO.page_id, 1): _NINO_P1}, [_CAT_NINO], vistas=vistas)
    list(store.list_catalog())
    # La fixture trae menos de `_PAGE_SIZE` productos, así que no se pide la página 2.
    assert len(load_fixture(_NINO_P1)["plpList"]["productList"]) < _PAGE_SIZE
    assert (_CAT_NINO.page_id, 2) not in vistas


def test_el_mismo_modelo_en_dos_hojas_de_genero_distinto_se_emite_una_vez_y_unisex() -> None:
    """El caso de la #98, de punta a punta: dos hojas, un producto, género `unisex`."""
    store = _store(
        {
            (_CAT_NINO.page_id, 1): _NINO_P1,
            (_CAT_NINA.page_id, 1): _NINA,
        },
        [_CAT_NINO, _CAT_NINA],
    )
    entradas = {e.retailer_product_id: e for e in store.list_catalog()}
    assert entradas["1234419"].gender == "unisex"
    assert entradas["1234419"].section == "ropa"
    assert entradas["1260878"].gender == "niño"  # solo sale en la hoja de niño


def test_los_colores_de_las_dos_hojas_acaban_en_el_mismo_producto() -> None:
    store = _store(
        {
            (_CAT_NINO.page_id, 1): _NINO_P1,
            (_CAT_NINA.page_id, 1): _NINA,
        },
        [_CAT_NINO, _CAT_NINA],
    )
    entradas = list(store.list_catalog())
    prod = next(p for p in store.fetch_details(entradas) if p.retailer_product_id == "1234419")
    # 2 colores en la hoja de niño + 2 en la de niña, y uno repetido no cuenta dos veces.
    ids = [v.retailer_variant_id for v in prod.variants]
    assert len(ids) == len(set(ids))


def test_una_hoja_que_devuelve_el_cubo_se_trata_como_retirada() -> None:
    """Y con ella se van las bajas de su ámbito: es lo único que impide ingerir 9.700 ajenos."""
    store = _store({}, [_CAT_NINO])  # sin mapear: todo responde el canario
    assert list(store.list_catalog()) == []
    informe = store.scan_report()
    assert informe.leaves_failed == 1
    assert ScrapeScope("niño", "ropa", "pantalones") in informe.failed_scopes
    # Y nombrada por su `pageId` (#155), que es lo que hay que llevarse al árbol de la tienda.
    assert informe.failed_leaves == ["/kids/boys/clothing/trousers"]


def test_al_caer_una_hoja_de_genero_tambien_se_protege_el_ambito_unisex() -> None:
    """Un modelo que salía en las dos ramas se emitiría con el género de la que sobrevive.

    Sin esta protección, la pasada daría de baja productos `unisex` que están perfectamente vivos.
    """
    store = _store({}, [_CAT_NINO])
    list(store.list_catalog())
    assert ScrapeScope("unisex", "ropa", "pantalones") in store.scan_report().failed_scopes


def test_una_hoja_viva_pasada_del_final_no_se_confunde_con_una_muerta() -> None:
    """La pareja del test anterior: 0 productos, pero `numberOfHits` intacto y sin `nextPageNum`."""
    fin = load_fixture(_FIN)
    assert fin["plpList"]["productList"] == []
    assert fin["plpList"]["numberOfHits"] > 0
    assert "nextPageNum" not in fin["pagination"]


def test_sin_canario_no_se_da_ninguna_hoja_por_viva() -> None:
    """Decir «viva» sin poder reconocer el cubo sería peor que callar."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["pageId"].startswith("/deal-tracker/"):
            return httpx.Response(500)
        return httpx.Response(200, json=load_fixture(_NINO_P1))

    store = HMStore(_CFG, [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    hojas = list(store.check_leaves())
    assert [h.alive for h in hojas] == [None]
    assert "canario" in hojas[0].detail


# --------------------------------------------------------------------------------------
# check_leaves
# --------------------------------------------------------------------------------------


def test_check_leaves_distingue_viva_de_espejismo() -> None:
    store = _store({(_CAT_NINO.page_id, 1): _NINO_P1}, [_CAT_NINO, _CAT_NINA])
    hojas = {h.leaf: h for h in store.check_leaves()}
    assert hojas[_CAT_NINO.page_id].alive is True
    assert hojas[_CAT_NINA.page_id].alive is False  # sin mapear -> responde el canario
    assert "espejismo" in hojas[_CAT_NINA.page_id].detail


def test_check_leaves_no_sentencia_lo_que_no_ha_podido_mirar() -> None:
    """Un 500 es un fallo nuestro o suyo, no un veredicto de la tienda."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["pageId"].startswith("/deal-tracker/"):
            return httpx.Response(200, json=load_fixture(_CANARIO))
        return httpx.Response(500)

    store = HMStore(_CFG, [_CAT_NINO])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    hojas = list(store.check_leaves())
    assert [h.alive for h in hojas] == [None]
    assert "500" in hojas[0].detail


# --------------------------------------------------------------------------------------
# Las hojas configuradas
# --------------------------------------------------------------------------------------


def test_las_cinco_categorias_del_brief_estan_cubiertas_en_los_dos_generos() -> None:
    del_brief = {"pantalones", "camisetas", "sudaderas", "ropa-interior"}
    for genero in ("niño", "niña"):
        cubiertas = {c.category for c in CATEGORIES if c.gender == genero and c.section == "ropa"}
        assert del_brief <= cubiertas, genero
    # `vestidos` solo en niña, como pide el brief (y en unisex, que es «sale en las dos»).
    assert not any(c.category == "vestidos" and c.gender == "niño" for c in CATEGORIES)
    assert any(c.category == "vestidos" and c.gender == "niña" for c in CATEGORIES)


def test_hay_zapateria_en_los_tres_generos() -> None:
    """Es la mitad de por qué esta tienda entra: hoy la sección casi entera es de Cacles."""
    generos = {c.gender for c in CATEGORIES if c.section == "zapateria"}
    assert generos == {"niño", "niña", "unisex"}


def test_las_hojas_no_se_repiten() -> None:
    rutas = [c.page_id for c in CATEGORIES]
    assert len(rutas) == len(set(rutas))


def test_bebe_y_recien_nacido_van_a_las_mismas_categorias_que_el_resto() -> None:
    """El vocabulario del catálogo no se parte por rango de edad: no hay eje de edad.

    Es la misma decisión que tomó Hipercor con sus hojas de bebé, y lo que hace que filtrar por
    `pantalones` devuelva prendas de bebé y de 14 años de todas las tiendas.
    """
    de_bebe = {c.category for c in CATEGORIES if c.page_id.startswith("/baby/")}
    de_ninos = {c.category for c in CATEGORIES if c.page_id.startswith("/kids/")}
    assert de_bebe <= de_ninos
