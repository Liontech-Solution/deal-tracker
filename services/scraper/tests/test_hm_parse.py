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
- `hm_list_nina_sets_outfits.json` — capturada el 06/08/2026 de `/kids/girls/clothing/sets-outfits`,
  la hoja mezclada de #200, recortada a los tres casos que decide el filtro: un conjunto rotulado en
  español, un `Disfraz` (lo que la hoja cuela y no es del brief) y un `2-piece cotton plumeti set`,
  que es un conjunto **sin traducir** — la tienda publica parte del catálogo en inglés.

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
    _DOMINIOS_POR_FAMILIA,
    _PAGE_SIZE,
    _PAGINA_INICIAL,
    CATEGORIES,
    CategoryConfig,
    HMStore,
    TagLeaf,
    _ambito,
    _talla,
    categoria_desde_nombre,
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
_CONJUNTOS = "hm_list_nina_sets_outfits.json"
# Un solo modelo (1168042) recortado de /kids/boys/clothing/jeans, capturado el 03/08/2026: seis
# artículos y DOS nombres de color repetidos dos veces cada uno ('Azul denim claro' y 'Azul denim
# oscuro'). Es el caso de la #123, y en vaqueros es de lo más corriente.
_VAQUEROS = "hm_list_nino_vaqueros.json"
# Siete productos de `/kids/last-chance/{girls,boys}-2-8y` capturados el 18/08/2026, uno por
# cosa que la tabla de #468 tiene que resolver: vestido, conjunto, camiseta, pantalón, un
# zapato, un `legging` (préstamo inglés) y un descarte de baño.
_SALDO = "hm_list_saldo_nina.json"

_CAT_NINO = CategoryConfig("/kids/boys/clothing/trousers", "niño", "ropa", "pantalones")
_CAT_NINA = CategoryConfig("/kids/girls/clothing/trousers", "niña", "ropa", "pantalones")
_CAT_ZAPATOS = CategoryConfig("/kids/girls/shoes", "niña", "zapateria", "zapatos")
_CAT_BEBE = CategoryConfig("/baby/newborn/clothing/bodysuits", "unisex", "ropa", "ropa-interior")
_CAT_SALDO_NINA = CategoryConfig(
    "/kids/last-chance/girls-2-8y", "niña", "", "", por_familia=True, estacional=True
)
_CAT_SALDO_NINO = CategoryConfig(
    "/kids/last-chance/boys-2-8y", "niño", "", "", por_familia=True, estacional=True
)

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
    assert _ambito([_CAT_NINO, _CAT_NINA], []).gender == "unisex"


def test_un_modelo_de_una_sola_rama_conserva_su_genero() -> None:
    assert _ambito([_CAT_NINO], []).gender == "niño"
    assert _ambito([_CAT_NINA, _CAT_NINA], []).gender == "niña"


def test_la_seccion_y_la_categoria_las_fija_la_primera_hoja() -> None:
    """Cruzar géneros sí tiene vocabulario (`unisex`); cruzar categorías no lo tiene."""
    otra = CategoryConfig("/kids/girls/clothing/dresses", "niña", "ropa", "vestidos")
    ambito = _ambito([_CAT_NINO, otra], [])
    assert ambito is not None
    assert (ambito.section, ambito.category) == ("ropa", "pantalones")


def test_una_hoja_unisex_sola_sigue_siendo_unisex() -> None:
    assert _ambito([_CAT_BEBE], []).gender == "unisex"


def test_scopes_declara_tambien_los_ambitos_unisex_que_el_parser_puede_emitir() -> None:
    """Un ámbito no declarado no se cuenta como escaneado, y sus productos no se dan de baja nunca.

    Es el mismo motivo por el que `cacles.py` declara el producto cartesiano de lo que su parser
    PUEDE emitir en vez de lo que dicen sus hojas.
    """
    store = HMStore(_CFG)
    scopes = set(store.scopes())
    declarados = {
        ScrapeScope(c.gender, c.section, c.category) for c in CATEGORIES if not c.por_familia
    }
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


def _store(
    paginas: dict[tuple[str, int], str],
    cats: list[CategoryConfig],
    tag_leaves: list[TagLeaf] | None = None,
    **kw: Any,
) -> HMStore:
    """Sin hojas de etiqueta salvo que el test las pida.

    Con las cuatro de serie, cada test pagaría cuatro peticiones que le responde el canario y que
    no está mirando; las de etiqueta tienen sus propios tests, más abajo.
    """
    store = HMStore(_CFG, cats, tag_leaves=tag_leaves if tag_leaves is not None else [])
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

    # Sin hojas de etiqueta: aquí se mira el camino de las hojas de categoría, y las de etiqueta
    # tienen su propio test. Con las cuatro de serie esto seguiría pasando —también salen `None`,
    # que es lo correcto— pero dejaría de decir qué se está comprobando.
    store = HMStore(_CFG, [_CAT_NINO], tag_leaves=[])
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


def test_el_filtro_de_conjuntos_acepta_los_dos_idiomas_y_descarta_el_disfraz() -> None:
    """El mecanismo de #200 sobre la hoja mezclada real, con sus tres casos.

    Los tres son de la misma página: la tienda publica parte del catálogo sin traducir, así que un
    conjunto puede llegar como «Conjunto de fútbol de 3 piezas» o como «2-piece cotton plumeti
    set», y el `Disfraz` es lo que #192 destapó entrando por la puerta de atrás — su rama
    (`fancy-dress-costumes`) está declarada fuera del brief y volvería por aquí.
    """
    cat = next(c for c in CATEGORIES if c.category == "conjuntos")
    assert cat.filtro is not None
    filas = parse_filas(load_fixture(_CONJUNTOS))
    aceptados = {f.name for f in filas if cat.filtro.acepta(f.name)}
    descartados = {f.name for f in filas if not cat.filtro.acepta(f.name)}

    assert aceptados == {"Conjunto de fútbol de 3 piezas", "2-piece cotton plumeti set"}
    assert descartados == {"Disfraz"}


def test_el_disfraz_no_entra_ni_llamandose_conjunto() -> None:
    """El agujero que la pasada real destapó, y que el fixture por sí solo no ve (#200).

    La tienda publica «Conjunto de disfraz», que el patrón acepta encantado porque empieza por
    «Conjunto». Medido contra Postgres el 06/08/2026: de los 7 conjuntos que llegaron a ingerirse,
    **3 eran disfraces** — o sea `fancy-dress-costumes`, una rama declarada fuera del brief en esta
    misma lista, volviendo por la puerta de atrás. Es el fallo de #192 un nivel más abajo: allí se
    colaba por la hoja y aquí por el nombre.
    """
    cat = next(c for c in CATEGORIES if c.category == "conjuntos")
    assert cat.filtro is not None

    assert not cat.filtro.acepta("Conjunto de disfraz")
    assert not cat.filtro.acepta("Conjunto de disfraz de 3 piezas")
    assert not cat.filtro.acepta("2-piece fancy dress costume set")
    # Y lo que sí es conjunto sigue entrando: la excepción no puede comerse el caso normal.
    assert cat.filtro.acepta("Conjunto de 2 piezas con bordado inglés")
    assert cat.filtro.acepta("Conjunto estampado de 2 piezas en tejido rizado")


def test_las_hojas_de_conjuntos_van_las_ultimas_y_solo_atrapan_lo_exclusivo() -> None:
    """Lo contrario que en Zara y Sfera, y por una razón medida (#200).

    `_ambito()` fija la categoría con la PRIMERA hoja que trae el modelo, así que ir detrás es lo
    que hace que un conjunto que la tienda publica además en `trousers` conserve `pantalones`.
    Medido el 06/08/2026: adelantarlas se llevaba 483 modelos de `pantalones` (de 1418 a 936),
    porque aquí `sets-outfits` no es un residuo sino un catálogo paralelo de 495 modelos.

    Si alguien las sube en la lista, eso se rompe en silencio: la pasada sigue verde y un tercio de
    `pantalones` cambia de categoría.
    """
    categorizadas = [c for c in CATEGORIES if not c.por_familia]
    rutas = [c.page_id for c in categorizadas]
    conjuntos = [c for c in categorizadas if c.category == "conjuntos"]
    assert len(conjuntos) == 7, "una por rama; si cambian las ramas, cambia esto"
    primera = min(rutas.index(c.page_id) for c in conjuntos)
    assert primera == len(categorizadas) - len(conjuntos), "tienen que ser el último bloque"
    # Las de saldo (#468) van detrás de todo y NO debilitan esto: no votan la categoría, así que
    # su sitio en la lista da igual — lo garantiza `_ambito()`, no el orden. Ver su propio test.
    assert all(c.por_familia for c in CATEGORIES[len(categorizadas) :])


def test_bebe_y_recien_nacido_van_a_las_mismas_categorias_que_el_resto() -> None:
    """El vocabulario del catálogo no se parte por rango de edad: no hay eje de edad.

    Es la misma decisión que tomó Hipercor con sus hojas de bebé, y lo que hace que filtrar por
    `pantalones` devuelva prendas de bebé y de 14 años de todas las tiendas.
    """
    de_bebe = {c.category for c in CATEGORIES if c.page_id.startswith("/baby/")}
    de_ninos = {c.category for c in CATEGORIES if c.page_id.startswith("/kids/")}
    assert de_bebe <= de_ninos


# --------------------------------------------------------------------------------------
# Las hojas de etiqueta: marcan el eje `deportiva` sin ingerir nada (#208)
# --------------------------------------------------------------------------------------

_DEPORTE = TagLeaf("/kids/boys/sportswear", "niño", "deportiva")


def test_la_hoja_de_etiqueta_marca_sin_ingerir_ni_crear_ambito() -> None:
    """Lo que la hace transversal: aporta el eje y **nada más**.

    Se reutiliza la fixture de pantalones de niño como si fuera la rama de deporte: lo que se mira
    aquí no es qué prendas trae, sino que sus modelos salgan marcados y que la hoja no aparezca por
    ningún otro lado — ni emitiendo entradas, ni en `scopes()`, ni en el `ScanReport`.
    """
    store = _store(
        {(_CAT_NINA.page_id, 1): _NINA, (_DEPORTE.page_id, 1): _NINO_P1},
        [_CAT_NINA],
        tag_leaves=[_DEPORTE],
    )
    ids = {e.retailer_product_id for e in store.list_catalog()}
    tags = store.product_tags()

    marcados = set(tags.por_producto)
    assert marcados, "la hoja de etiqueta tiene que haber marcado algo"
    assert all(t == {"deportiva"} for t in tags.por_producto.values())
    assert "deportiva" in tags.fiables, "se listó entera y sin fallos"
    # No ingiere: sus modelos exclusivos no entran al catálogo por esta puerta.
    assert marcados - ids, "la fixture trae modelos que no publica la hoja de categoría"
    # Y no tiene ámbito: no puede provocar bajas ni contar como hoja de la pasada.
    assert store.scan_report().leaves_total == 1
    assert all(s.category is not None for s in store.scopes())


def test_una_hoja_de_etiqueta_espejismo_no_borra_las_marcas_de_toda_la_tienda() -> None:
    """El fallo silencioso que `fiables` existe para evitar.

    La reconciliación de la ingesta BORRA las etiquetas que la tienda ya no declara. Si una rama
    caída se leyera como «H&M ya no publica nada deportivo», se llevaría por delante las marcas de
    los 156 productos que sí lo son. Aquí la señal de rama caída es el canario, no una lista vacía:
    la ruta que no resuelve devuelve 200 con el cubo entero.
    """
    store = _store(
        {(_CAT_NINA.page_id, 1): _NINA},  # la de deporte cae al canario -> espejismo
        [_CAT_NINA],
        tag_leaves=[_DEPORTE],
    )
    list(store.list_catalog())
    tags = store.product_tags()

    assert "deportiva" not in tags.fiables, "no se puede reconciliar lo que no se ha podido leer"
    # Y la pasada sigue adelante: la hoja de etiqueta no compromete ningún ámbito ni cuenta como
    # caída, porque no tiene ámbito que comprometer.
    assert store.scan_report().leaves_failed == 0
    assert store.scan_report().failed_scopes == set()


# --------------------------------------------------------------------------------------
# La rama de saldo: `last-chance` (#468)
# --------------------------------------------------------------------------------------


def test_la_tabla_de_nombres_clasifica_los_dos_idiomas() -> None:
    """El catálogo llega mezclado y el criterio no puede ser «lo que la tienda haya traducido».

    Medido el 18/08/2026: **78 de los 456 modelos** que solo viven en la rama de saldo (17 %)
    vienen sin traducir. Los nombres de aquí abajo son reales, de esa medición.
    """
    assert categoria_desde_nombre("Camiseta oversize estampada") == ("ropa", "camisetas")
    assert categoria_desde_nombre("2-pack cotton Henley tops") == ("ropa", "camisetas")
    assert categoria_desde_nombre("Vestido escalonado de algodón") == ("ropa", "vestidos")
    assert categoria_desde_nombre("Lined flannel overshirt") == ("ropa", "camisetas")
    assert categoria_desde_nombre("Pantalón de lino") == ("ropa", "pantalones")
    assert categoria_desde_nombre("Baggy Low Waist Jeans") == ("ropa", "pantalones")


def test_la_tabla_descarta_lo_que_no_es_del_brief() -> None:
    """`None` es «no ingerir», y cubre los dos casos en los que ésa es la respuesta correcta.

    Sin esto, una hoja que mezcla vocabulario mete bañadores y gorros en el catálogo con la
    categoría de lo que sea que casara primero.
    """
    for nombre in (
        "Traje de baño con volantes",
        "Flounced swimsuit",
        "Pack de 2 bragas de baño con volante",  # el que la primera versión de la tabla no cazó
        "Gorro con protección UPF 50",
        "Sombrero de paja",
        "Parka de bebé ligera con capucha",
        "Cinturón",
        "Pinza estampada",
    ):
        assert categoria_desde_nombre(nombre) is None, nombre


def test_las_dos_trampas_de_orden_de_la_tabla() -> None:
    """Las dos que se midieron equivocadas antes de acertarlas, y ninguna daba test rojo.

    1. `chaqueta de punto` es una sudadera y `chaqueta` a secas es un abrigo, que está fuera. Con
       el abrigo delante, las dos se van fuera.
    2. `conjuntos` es categoría propia (#192) y esta tienda ya la usa en `sets-outfits`. Con los
       conjuntos cayendo en `vestidos` —el primer intento— **171 de 456** acababan mal, y la
       categoría equivocada no la canta nadie.
    """
    assert categoria_desde_nombre("Chaqueta de punto en algodón") == ("ropa", "sudaderas")
    assert categoria_desde_nombre("Chaqueta acolchada con capucha") is None

    assert categoria_desde_nombre("Conjunto de 2 piezas en algodón") == ("ropa", "conjuntos")
    assert categoria_desde_nombre("2-piece terry set") == ("ropa", "conjuntos")
    assert categoria_desde_nombre("Vestido imperio de algodón") == ("ropa", "vestidos")


def test_las_hojas_de_saldo_van_las_ultimas_son_estacionales_y_no_traen_categoria() -> None:
    """Las tres marcas juntas, porque las tres se necesitan y ninguna se nota si falta.

    Sin `por_familia` la hoja impondría su categoría a todo lo que trae; sin `estacional`, el fin
    de campaña apaga las siete a la vez y `dead_ratio` (7 de 77, el 9 %) empieza a contarlas como
    hojas caídas cada temporada.
    """
    saldo = [c for c in CATEGORIES if c.por_familia]
    assert len(saldo) == 7, "6 con género + newborn; si cambian, cámbialo aquí a conciencia"
    assert all(c.estacional for c in saldo)
    assert all(c.section == "" and c.category == "" for c in saldo)
    assert all("last-chance" in c.page_id for c in saldo)

    indices = [i for i, c in enumerate(CATEGORIES) if c.por_familia]
    resto = [i for i, c in enumerate(CATEGORIES) if not c.por_familia]
    assert min(indices) > max(resto), "las de saldo van detrás de todas las categorizadas"


def test_una_hoja_por_familia_no_puede_declarar_categoria() -> None:
    """Las dos mitades del invariante, y las dos mienten en silencio si no se comprueban aquí."""
    with pytest.raises(ValueError, match="por_familia"):
        CategoryConfig("/kids/last-chance/x", "niña", "ropa", "vestidos", por_familia=True)
    with pytest.raises(ValueError, match="no declara sección/categoría"):
        CategoryConfig("/kids/whatever", "niña", "", "")


def test_el_saldo_no_le_pisa_la_categoria_a_quien_ya_la_tiene() -> None:
    """El mecanismo entero de #468, y lo que sustituye al truco del ORDEN que usa Zara.

    Aquí no puede depender del orden: esta tienda **acumula la pasada entera antes de emitir**, así
    que cuando decide el ámbito ya conoce todas las hojas del modelo. Lo que hace el trabajo es que
    la hoja de saldo no vote la categoría.
    """
    filas = parse_filas(load_fixture(_SALDO))
    ambito = _ambito([_CAT_SALDO_NINA, _CAT_NINO], filas)

    assert ambito is not None
    assert (ambito.section, ambito.category) == ("ropa", "pantalones")  # la de la hoja normal


def test_un_modelo_que_solo_vive_en_el_saldo_saca_la_categoria_del_nombre() -> None:
    """Y el género lo sigue poniendo la hoja, lo único que en `por_familia` sigue siendo suyo."""
    filas = [f for f in parse_filas(load_fixture(_SALDO)) if f.name.startswith("Vestido")]
    assert filas, "el fixture tiene que traer el vestido"

    ambito = _ambito([_CAT_SALDO_NINA], filas)

    assert ambito is not None
    assert (ambito.gender, ambito.section, ambito.category) == ("niña", "ropa", "vestidos")


def test_el_saldo_tambien_cruza_generos() -> None:
    """Un modelo en la rama de saldo de niño y en la de niña es `unisex` como cualquier otro.

    No es hipotético: medido el 18/08/2026, **114 filas** salen en las dos ramas
    (niño∩niña: 19 en 2-8y, 9 en 9-14y y 86 en bebé).
    """
    filas = [f for f in parse_filas(load_fixture(_SALDO)) if f.name.startswith("Vestido")]
    ambito = _ambito([_CAT_SALDO_NINA, _CAT_SALDO_NINO], filas)

    assert ambito is not None
    assert ambito.gender == "unisex"
    assert (ambito.section, ambito.category) == ("ropa", "vestidos")


def test_lo_que_el_saldo_no_sabe_nombrar_no_se_ingiere() -> None:
    """`None` y no una categoría por defecto: inventarla ensucia el catálogo sin que se note."""
    filas = [f for f in parse_filas(load_fixture(_SALDO)) if "baño" in f.name]
    assert filas, "el fixture tiene que traer el descarte de baño"

    assert _ambito([_CAT_SALDO_NINA], filas) is None


def test_scopes_declara_todo_lo_que_una_hoja_de_saldo_puede_emitir() -> None:
    """Un ámbito no declarado no cuenta como escaneado, y sus productos no se dan de baja NUNCA.

    Una hoja `por_familia` no tiene un ámbito, tiene todos los que su tabla puede producir. Es el
    mismo cartesiano que hacen `cacles.py` y `lefties.py`.
    """
    scopes = set(HMStore(_CFG).scopes())
    saldo = [c for c in CATEGORIES if c.por_familia]

    for cat in saldo:
        for section, category in _DOMINIOS_POR_FAMILIA:
            assert ScrapeScope(cat.gender, section, category) in scopes
            assert ScrapeScope("unisex", section, category) in scopes
    # Y el ámbito crudo de la hoja, el que no existe, NO se declara.
    assert not any(s.section == "" or s.category == "" for s in scopes)


def test_una_hoja_de_saldo_apagada_no_cuenta_como_caida() -> None:
    """El fin de campaña no es una avería, y contarlo como tal hace dos daños a la vez (#195).

    Las 7 hojas de saldo se apagan juntas: sobre las 77 de `CATEGORIES` son el 9 %, así que
    `dead_ratio` empezaría a subir hacia el tope que aborta la pasada una vez por temporada. Y
    sacaría de las bajas unos ámbitos que se han listado perfectamente por sus hojas de siempre.
    """
    # Sin fixture mapeado, el handler responde el canario: para la hoja eso ES el espejismo.
    store = _store({(_CAT_NINO.page_id, 1): _NINO_P1}, [_CAT_NINO, _CAT_SALDO_NINA])
    list(store.list_catalog())
    informe = store.scan_report()

    assert informe.leaves_failed == 0, "la hoja de campaña apagada no es una hoja caída"
    assert _CAT_SALDO_NINA.page_id not in informe.failed_leaves
    assert not informe.failed_scopes, "y su ámbito sigue pudiendo dar bajas"


def test_una_hoja_normal_apagada_si_cuenta_como_caida() -> None:
    """El control de la anterior: sin él, ese test pasaría con `estacional` roto en las dos."""
    store = _store({}, [_CAT_NINO])
    list(store.list_catalog())

    assert store.scan_report().leaves_failed == 1
    assert _CAT_NINO.page_id in store.scan_report().failed_leaves


def test_check_leaves_marca_la_hoja_de_saldo_como_estacional() -> None:
    """Para que el vigía la cuente como aviso y no abra una issue cada fin de campaña."""
    store = _store({(_CAT_NINO.page_id, 1): _NINO_P1}, [_CAT_NINO, _CAT_SALDO_NINA])
    hojas = {h.leaf: h for h in store.check_leaves()}

    assert hojas[_CAT_SALDO_NINA.page_id].estacional is True
    assert hojas[_CAT_NINO.page_id].estacional is False
