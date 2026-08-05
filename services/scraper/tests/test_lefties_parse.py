"""Tests de parsing de Lefties con fixtures reales capturados de su API (golden-file).

Son herméticos: NO necesitan navegador ni red. Ejercitan las funciones puras sobre un grid de
categoría (`grids/{uuid}`) y su respuesta de detalle (`productsArray`) reales.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any

from scraper.config import Config
from scraper.stores import lefties
from scraper.stores.base import ScrapedImage, ScrapeScope
from scraper.stores.browser import BrowserHTTPError
from scraper.stores.lefties import (
    CATEGORIES,
    CategoryConfig,
    LeftiesStore,
    grid_ids_by_category,
    known_product_ids,
    parse_detail_product,
    parse_listing_entries,
)

from .conftest import load_fixture

_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0)

# Hoja real de la que salen los dos fixtures: niña / zapatería / zapatos.
_CAT = CategoryConfig(1030272335, "niña", "zapateria", "zapatos")


def _detalle() -> list[dict]:
    return load_fixture("lefties_details_zapatos_nina.json")["products"]


def test_categorias_cubren_el_brief_por_genero() -> None:
    """Las 5 categorías de ropa del brief + calzado, y `vestidos` solo en niña."""
    por_genero: dict[str, set[str]] = defaultdict(set)
    for c in CATEGORIES:
        por_genero[c.gender].add(c.category)

    ropa_brief = {"pantalones", "camisetas", "sudaderas", "ropa-interior"}
    for genero in ("niña", "niño"):
        assert ropa_brief <= por_genero[genero]
        assert {"zapatos", "zapatillas", "barefoot"} <= por_genero[genero]
    assert "vestidos" in por_genero["niña"]
    assert "vestidos" not in por_genero["niño"]  # la tienda no tiene, y es correcto

    # Ids de categoría únicos: un duplicado listaría la misma hoja dos veces.
    ids = [c.category_id for c in CATEGORIES]
    assert len(ids) == len(set(ids))


def test_barefoot_va_antes_que_el_resto_del_calzado() -> None:
    """El orden de CATEGORIES no es cosmético: decide qué categoría se queda un modelo.

    `list_catalog()` deduplica por id y gana la primera hoja que lo ve. Casi todo lo barefoot
    cuelga también de `zapatos`, así que si estas hojas van al final el catálogo se queda con 4
    productos en `barefoot` en vez de ~30 (medido en una pasada real). Barefoot es la señal que
    da sentido al producto, así que gana ella.
    """
    calzado = [i for i, c in enumerate(CATEGORIES) if c.section == "zapateria"]
    barefoot = [i for i, c in enumerate(CATEGORIES) if c.category == "barefoot"]
    assert barefoot, "debe haber hojas barefoot"
    assert max(barefoot) < min(set(calzado) - set(barefoot))


def test_grid_ids_by_category_resuelve_las_hojas_del_menu() -> None:
    """El listado se pide por uuid de contenido, no por id de categoría."""
    menu = {
        "items": [
            {
                "id": 1,
                "children": [
                    {"id": 1030272335, "content": {"id": "uuid-zapatos", "type": "grid"}},
                    {"id": 3, "children": [{"id": 4, "content": {"id": "uuid-hondo"}}]},
                    {"id": 5},  # rama sin contenido: no es hoja de grid
                ],
            }
        ]
    }
    assert grid_ids_by_category(menu) == {1030272335: "uuid-zapatos", 4: "uuid-hondo"}
    assert grid_ids_by_category({}) == {}


def test_parse_listing_entries_agrupa_colores_en_modelos() -> None:
    """Cada componente del grid es un COLOR; el producto es su `productParentId`."""
    grid = load_fixture("lefties_grid_zapatos_nina.json")
    entries = parse_listing_entries(grid, _CAT)

    # 27 componentes (colores) que son 14 modelos.
    assert len(grid["components"]) == 27
    assert len(entries) == 14
    assert len({e.retailer_product_id for e in entries}) == 14
    assert "747860883" in {e.retailer_product_id for e in entries}

    for e in entries:
        assert e.gender == "niña" and e.section == "zapateria" and e.category == "zapatos"
        assert e.signature, "la huella no puede quedar vacía"


def test_el_componente_se_reconoce_por_su_id_de_modelo_y_no_por_kind() -> None:
    """La regresión de #179: la tienda cambia el nombre de la familia y nos quedamos a cero.

    El 05/08/2026 `kind` y `type` aparecieron **intercambiados** (`kind` pasó de `Product` a
    `Footwear`), y como el filtro exigía `kind == "Product"` las 38 hojas parsearon 0 entradas
    descartando 2207 componentes. Nada se puso rojo: el fixture de entonces decía `Product` y el
    menú seguía intacto, así que `check_leaves()` daba 38/38 vivas.

    Por eso esto no comprueba un valor concreto, sino que **ningún valor de `kind`/`type` decide**:
    un allowlist volvería a romperse con la siguiente familia que publique la tienda.
    """
    grid = load_fixture("lefties_grid_zapatos_nina.json")
    base = {e.retailer_product_id for e in parse_listing_entries(grid, _CAT)}
    assert base, "premisa del test: el fixture parsea algo"

    inventado = deepcopy(grid)
    for comp in inventado["components"].values():
        comp["kind"] = "UnaFamiliaQueAunNoExiste"
        comp["type"] = "TampocoEsta"

    assert {e.retailer_product_id for e in parse_listing_entries(inventado, _CAT)} == base


def test_un_componente_sin_id_de_modelo_no_es_un_producto() -> None:
    """La otra mitad del criterio: sin `productParentId` no hay modelo que emitir.

    Es lo que deja fuera un adorno aunque se cuele en `components`, ahora que el `kind` no decide.
    """
    grid = deepcopy(load_fixture("lefties_grid_zapatos_nina.json"))
    base = {e.retailer_product_id for e in parse_listing_entries(grid, _CAT)}

    # Un modelo de un solo color: al quitarle el identificador desaparece entero, sin tapar el
    # efecto con los otros colores del mismo modelo.
    por_modelo = Counter(
        str((c.get("identifier") or {}).get("productParentId")) for c in grid["components"].values()
    )
    solo = next(pid for pid, veces in por_modelo.items() if veces == 1)
    clave = next(
        k
        for k, c in grid["components"].items()
        if str((c.get("identifier") or {}).get("productParentId")) == solo
    )
    grid["components"][clave]["identifier"] = {}

    assert {e.retailer_product_id for e in parse_listing_entries(grid, _CAT)} == base - {solo}


def test_la_huella_cambia_con_el_precio_y_no_con_el_orden() -> None:
    grid = load_fixture("lefties_grid_zapatos_nina.json")
    base = {e.retailer_product_id: e.signature for e in parse_listing_entries(grid, _CAT)}

    # Reordenar los componentes no debe alterar ninguna huella (se ordena antes de unir).
    revuelto = deepcopy(grid)
    revuelto["components"] = dict(reversed(list(revuelto["components"].items())))
    assert {
        e.retailer_product_id: e.signature for e in parse_listing_entries(revuelto, _CAT)
    } == base

    # Cambiar un precio sí: es justo lo que dispara la petición de detalle.
    tocado = deepcopy(grid)
    comp = next(iter(tocado["components"].values()))
    comp["pricing"]["price"]["current"]["value"] = 1
    pid = comp["identifier"]["productParentId"]
    nuevo = {e.retailer_product_id: e.signature for e in parse_listing_entries(tocado, _CAT)}
    assert nuevo[pid] != base[pid]


def test_parse_detail_product_extrae_tallas_precio_y_stock() -> None:
    product = next(p for p in _detalle() if str(p.get("id")) == "747860883")
    scraped = parse_detail_product(product, _CAT)

    assert scraped is not None
    assert scraped.retailer_product_id == "747860883"
    assert scraped.name == "Bailarina T"  # el detalle viene en español
    assert scraped.gender == "niña" and scraped.section == "zapateria"
    assert scraped.url is not None and scraped.url.endswith("-c1030272335p747860883.html")

    v = scraped.variants[0]
    # Céntimos EN STRING ("1799"), al revés que Zara: si se leyera como int daría 1799 €.
    assert v.price == Decimal("17.99")
    assert v.size and v.color
    assert v.retailer_variant_id.startswith("747860883-")
    assert isinstance(v.in_stock, bool)


def test_el_stock_sale_de_visibility_value_y_no_de_is_buyable() -> None:
    """`isBuyable` viene true SIEMPRE; la señal real es `visibilityValue`."""
    product = deepcopy(next(p for p in _detalle() if str(p.get("id")) == "747860883"))
    tallas = product["detail"]["colors"][0]["sizes"]
    assert all(t["isBuyable"] for t in tallas), "premisa del test: isBuyable no discrimina"

    tallas[0]["visibilityValue"] = "HIDDEN"
    tallas[1]["visibilityValue"] = "SHOW"
    scraped = parse_detail_product(product, _CAT)
    assert scraped is not None
    por_sku = {v.sku: v for v in scraped.variants}
    assert por_sku[str(tallas[0]["sku"])].in_stock is False
    assert por_sku[str(tallas[1]["sku"])].in_stock is True


def test_old_price_alimenta_el_precio_tachado() -> None:
    product = deepcopy(next(p for p in _detalle() if str(p.get("id")) == "747860883"))
    talla = product["detail"]["colors"][0]["sizes"][0]
    talla["price"], talla["oldPrice"] = "1299", "1799"

    scraped = parse_detail_product(product, _CAT)
    assert scraped is not None
    v = next(v for v in scraped.variants if v.sku == str(talla["sku"]))
    assert v.price == Decimal("12.99")
    assert v.list_price == Decimal("17.99")


def test_galeria_por_color_desde_xmedia() -> None:
    """`detail.xmedia` viene indexado por `colorCode`: la galería sale gratis del detalle."""
    con_fotos = [p for p in _detalle() if p.get("detail", {}).get("xmedia")]
    assert con_fotos, "el fixture debería traer xmedia"

    scraped = parse_detail_product(con_fotos[0], _CAT)
    assert scraped is not None
    assert scraped.images

    por_color: dict[str | None, list[ScrapedImage]] = defaultdict(list)
    for img in scraped.images:
        por_color[img.color].append(img)
    for fotos in por_color.values():
        assert len(fotos) <= 8  # tope por color
        for i in fotos:
            assert i.url.startswith("https://static.lefties.com/")
            # `deliveryUrl` (jpg plano), no el hermano con la plantilla `&w=:width:`.
            assert ":width:" not in i.url

    assert scraped.image_url == scraped.images[0].url


def test_galeria_y_variantes_comparten_el_nombre_de_color() -> None:
    """Invariante que sostiene el emparejamiento foto<->precio de la ficha."""
    for product in _detalle():
        scraped = parse_detail_product(product, _CAT)
        if scraped is None:
            continue
        assert {i.color for i in scraped.images} <= {v.color for v in scraped.variants}


def test_color_sin_tallas_con_precio_no_aporta_fotos() -> None:
    product = deepcopy(next(p for p in _detalle() if p.get("detail", {}).get("xmedia")))
    vivo = product["detail"]["colors"][0]
    fantasma = deepcopy(vivo)
    fantasma["id"], fantasma["name"] = "999", "Fantasma"
    for t in fantasma["sizes"]:
        t["price"] = None
    product["detail"]["colors"] = [vivo, fantasma]

    scraped = parse_detail_product(product, _CAT)
    assert scraped is not None
    assert "Fantasma" not in {v.color for v in scraped.variants}
    assert "Fantasma" not in {i.color for i in scraped.images}


def test_entrada_de_error_no_es_un_producto() -> None:
    """`_ERR_PRODUCT_NOT_FOUND` no trae `id`: se descarta en vez de reventar."""
    error = {"description": "Item not found", "key": "_ERR_PRODUCT_NOT_FOUND"}
    assert parse_detail_product(error, _CAT) is None
    assert parse_detail_product({"id": 1, "detail": {"colors": []}}, _CAT) is None


def test_known_product_ids_es_la_prueba_de_baja() -> None:
    """Lo que la tienda no devuelve con `id` es lo que ya no existe."""
    payload = {
        "products": [
            {"id": 747860883, "name": "Vivo"},
            {"key": "_ERR_PRODUCT_NOT_FOUND"},
        ]
    }
    assert known_product_ids(payload) == {"747860883"}
    assert known_product_ids({}) == set()


# --- #41 Hojas que desaparecen del menú o dejan de responder --------------------------------

_CATS_SCAN = [
    CategoryConfig(1030272335, "niña", "zapateria", "zapatos"),
    CategoryConfig(1030267678, "niña", "ropa", "camisetas"),
]


class _ScanSession:
    """Sesión falsa: devuelve el menú y, por uuid de grid, su payload o una excepción."""

    def __init__(self, menu: dict[str, Any], grids: dict[str, Any]) -> None:
        self._menu = menu
        self._grids = grids

    def __enter__(self) -> _ScanSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def goto(self, url: str) -> int:
        return 200

    def get_json(self, url: str) -> Any:
        if "/menu" in url:
            return self._menu
        for grid_id, respuesta in self._grids.items():
            if grid_id in url:
                if isinstance(respuesta, Exception):
                    raise respuesta
                return respuesta
        raise AssertionError(f"grid no simulado: {url}")


def _menu(por_categoria: dict[int, str]) -> dict[str, Any]:
    """Menú mínimo: una hoja por categoría, con su uuid de grid en `content.id`."""
    return {
        "items": [{"id": cid, "content": {"id": grid_id}} for cid, grid_id in por_categoria.items()]
    }


def _grid(pid: str) -> dict[str, Any]:
    """Grid mínimo: un componente de producto (que en Lefties es un color)."""
    return {
        "components": {
            "c1": {
                "kind": "Product",
                "identifier": {"productParentId": pid},
                "color": {"id": "01"},
                "pricing": {"price": {"current": {"value": 1799}}},
            }
        }
    }


def _scan_store(
    menu: dict[str, Any], grids: dict[str, Any], cats: list[CategoryConfig] | None = None
) -> LeftiesStore:
    session = _ScanSession(menu, grids)
    return LeftiesStore(_CFG, categories=cats or _CATS_SCAN, session_factory=lambda: session)  # type: ignore[arg-type]


def _con_unisex(scope: ScrapeScope) -> set[ScrapeScope]:
    """El ámbito caído y su equivalente `unisex`, que hay que proteger junto a él (#98).

    Un producto que salía en las dos ramas de género deja de verse en las dos en cuanto cae una,
    así que se emitiría con el género de la superviviente; sin sacar también el `unisex` de las
    bajas, la hoja caída descatalogaría producto vivo.
    """
    return {scope, ScrapeScope("unisex", scope.section, scope.category)}


def test_hoja_ausente_del_menu_saca_su_ambito_de_las_bajas() -> None:
    """Saltarla no basta: su ámbito seguía contando como escaneado y sus productos caían."""
    store = _scan_store(
        _menu({1030272335: "uuid-zapatos"}),  # camisetas ya no está en el menú
        {"uuid-zapatos": _grid("Z1")},
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["Z1"]
    assert (report.leaves_total, report.leaves_failed) == (2, 1)
    assert report.failed_scopes == _con_unisex(ScrapeScope("niña", "ropa", "camisetas"))
    # Con su nombre (#155). Y una sola vez: el ámbito `unisex` extra es la misma hoja caída, no
    # otra, así que no suma ni en `leaves_failed` ni aquí.
    assert report.failed_leaves == ["1030267678"]


def test_grid_que_da_404_se_trata_como_hoja_retirada() -> None:
    store = _scan_store(
        _menu({1030272335: "uuid-zapatos", 1030267678: "uuid-camisetas"}),
        {
            "uuid-zapatos": _grid("Z1"),
            "uuid-camisetas": BrowserHTTPError(404, "https://lefties.example/grids/uuid-camisetas"),
        },
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]

    assert ids == ["Z1"]
    report = store.scan_report()
    assert report.failed_scopes == _con_unisex(ScrapeScope("niña", "ropa", "camisetas"))
    assert report.failed_leaves == ["1030267678"]


# --- #98 El cruce de géneros: publicado en las dos ramas = unisex ------------------------------

_CATS_CRUCE = [
    CategoryConfig(1030267678, "niña", "ropa", "camisetas"),
    CategoryConfig(1030267679, "niño", "ropa", "camisetas"),
]


def test_un_producto_en_las_dos_ramas_de_genero_se_emite_una_vez_y_unisex() -> None:
    """El caso de la #98: 14 productos (2,0 %) de esta tienda salen en niña Y en niño.

    Hasta ahora acababan los 14 en `niña`, porque su hoja va antes en `CATEGORIES`.
    """
    store = _scan_store(
        _menu({1030267678: "uuid-nina", 1030267679: "uuid-nino"}),
        {"uuid-nina": _grid("C1"), "uuid-nino": _grid("C1")},
        _CATS_CRUCE,
    )

    entradas = list(store.list_catalog())

    assert [e.retailer_product_id for e in entradas] == ["C1"], "una vez, no dos"
    assert entradas[0].gender == "unisex"
    assert (entradas[0].section, entradas[0].category) == ("ropa", "camisetas")


def test_un_producto_en_una_sola_rama_conserva_su_genero() -> None:
    store = _scan_store(
        _menu({1030267678: "uuid-nina", 1030267679: "uuid-nino"}),
        {"uuid-nina": _grid("C1"), "uuid-nino": _grid("C2")},
        _CATS_CRUCE,
    )

    generos = {e.retailer_product_id: e.gender for e in store.list_catalog()}

    assert generos == {"C1": "niña", "C2": "niño"}


def test_el_ambito_unisex_llega_tambien_al_detalle() -> None:
    """`fetch_details` construye el producto desde la `CategoryConfig` cacheada, no desde la
    entrada, así que si esa no se corrige el género vuelve a escribirse mal en la base."""
    store = _scan_store(
        _menu({1030267678: "uuid-nina", 1030267679: "uuid-nino"}),
        {"uuid-nina": _grid("C1"), "uuid-nino": _grid("C1")},
        _CATS_CRUCE,
    )

    list(store.list_catalog())

    assert store._cat_by_product["C1"].gender == "unisex"
    # Y lo que NO debe perder: el id de categoría, que es con lo que se arma la URL de la ficha.
    assert store._cat_by_product["C1"].category_id == 1030267678


def test_scopes_declara_tambien_los_ambitos_unisex_que_el_parser_puede_emitir() -> None:
    """Un ámbito no declarado no cuenta como escaneado, y sus productos no caen NUNCA."""
    scopes = list(LeftiesStore(_CFG).scopes())

    assert len(scopes) == len(set(scopes)), "sin duplicados"
    for cat in CATEGORIES:
        if cat.por_familia:
            continue  # no tiene un ámbito propio; los suyos se comprueban en el test de abajo
        assert ScrapeScope(cat.gender, cat.section, cat.category) in scopes
        assert ScrapeScope("unisex", cat.section, cat.category) in scopes


def test_scopes_declara_todo_lo_que_la_hoja_por_familia_puede_emitir() -> None:
    """La hoja de rebajas no tiene UN ámbito: tiene todos los de su tabla, y hay que declararlos.

    Es el mismo agujero que el de arriba visto desde la otra punta: la prenda que solo entra por la
    hoja de rebajas vive en un ámbito que ninguna hoja de categoría declara, y sin declararlo no se
    descatalogaría jamás.
    """
    scopes = set(LeftiesStore(_CFG).scopes())
    por_familia = [c for c in CATEGORIES if c.por_familia]

    assert por_familia, "esto deja de tener sentido si se quitan las hojas de rebajas"
    for cat in por_familia:
        for section, category in lefties.dominios_emitibles(cat.gender):
            assert ScrapeScope(cat.gender, section, category) in scopes
            assert ScrapeScope("unisex", section, category) in scopes


def test_la_hoja_mezclada_no_inventa_ambitos_que_la_tienda_no_tiene() -> None:
    """`vestidos` solo existe en niña, y la tabla de familias no distingue género.

    Sin acotarla, una falda colgada algún día de la hoja de rebajas de niño produciría un
    `niño/ropa/vestidos` que ningún filtro de la web sabe enseñar. Hoy no pasa —el short de niño
    llega como `BERMUDAS`—, pero eso es una observación de un día.
    """
    assert ("ropa", "vestidos") in lefties.dominios_emitibles("niña")
    assert ("ropa", "vestidos") not in lefties.dominios_emitibles("niño")

    cat_nino = CategoryConfig(
        1030303020, "niño", "", "", "1030267671/1030267673", por_familia=True, estacional=True
    )
    assert parse_listing_entries(_grid_familia("R1", "SKIRT"), cat_nino) == []
    assert len(parse_listing_entries(_grid_familia("R1", "BERMUDAS"), cat_nino)) == 1


# --- hoja de campaña: mezclada y estacional (#195) -------------------------------------------

_CAT_REBAJAS = CategoryConfig(
    1030302501, "niña", "", "", "1030267671/1030267672", por_familia=True, estacional=True
)


def _grid_rebajas() -> dict[str, Any]:
    """Grid real de `3_NA_S_REBAJAS`, la hoja de rebajas de niña."""
    return load_fixture("lefties_grid_rebajas_nina.json")


def _grid_familia(pid: str, familia: str | None) -> dict[str, Any]:
    grid = _grid(pid)
    grid["components"]["c1"]["classification"] = {"family": {"name": familia}}
    return grid


def test_la_hoja_de_rebajas_reparte_cada_prenda_en_su_categoria() -> None:
    """La hoja mezcla cuatro de las cinco categorías del brief y hasta calzado, así que una
    `CategoryConfig` con categoría fija metería faldas en `camisetas`. La familia es de la ficha."""
    entradas = parse_listing_entries(_grid_rebajas(), _CAT_REBAJAS)

    reparto = Counter((e.section, e.category) for e in entradas)
    assert reparto[("ropa", "vestidos")] > 0  # faldas, shorts y vestidos
    assert reparto[("ropa", "camisetas")] > 0
    assert reparto[("zapateria", "zapatillas")] > 0, "la hoja mezcla también secciones"
    # El género SÍ es de la hoja: eso la tienda no lo mezcla.
    assert {e.gender for e in entradas} == {"niña"}


def test_la_prenda_cuya_familia_no_sabemos_mapear_se_descarta() -> None:
    """Mejor perder una prenda que meterla en una categoría que no es la suya.

    `ENSEMBLE..SET` es el caso que más importa: es el conjunto de #192, que en las hojas mapeadas
    cae en cuatro categorías distintas. Decidirlo aquí de tapadillo sería peor que perderlo.
    """
    for familia in ("ENSEMBLE..SET", "WAISTCOAT", "WIND-BREAK", None, "LO QUE SEA"):
        assert parse_listing_entries(_grid_familia("R1", familia), _CAT_REBAJAS) == []

    assert len(parse_listing_entries(_grid_familia("R1", "T-SHIRT"), _CAT_REBAJAS)) == 1


def test_la_hoja_de_rebajas_no_le_pisa_la_categoria_a_quien_ya_la_tiene() -> None:
    """Lo que protege el ORDEN de `CATEGORIES`: la hoja mezclada va la última.

    Si se moviera hacia arriba, pasaría a decidir la categoría de prendas que hoy la reciben de
    una hoja que la sabe mejor — y en silencio, porque el producto seguiría entrando.
    """
    store = _scan_store(
        _menu({1030267678: "uuid-camisetas", 1030302501: "uuid-rebajas"}),
        # El mismo modelo en su hoja de camisetas y, rebajado, con familia de falda.
        {"uuid-camisetas": _grid("C1"), "uuid-rebajas": _grid_familia("C1", "SKIRT")},
        [CategoryConfig(1030267678, "niña", "ropa", "camisetas"), _CAT_REBAJAS],
    )

    entradas = list(store.list_catalog())

    assert [(e.retailer_product_id, e.section, e.category) for e in entradas] == [
        ("C1", "ropa", "camisetas")
    ]
    # Y el detalle tiene que ver lo mismo: construye el producto desde la config cacheada.
    assert store._cat_by_product["C1"].category == "camisetas"


def test_el_ambito_de_la_prenda_rebajada_llega_al_detalle() -> None:
    """`fetch_details` arma el `ScrapedProduct` desde la `CategoryConfig` cacheada, así que si la
    categoría derivada no viaja hasta ahí se ingiere con la de la hoja, que está vacía."""
    store = _scan_store(
        _menu({1030302501: "uuid-rebajas"}),
        {"uuid-rebajas": _grid_familia("R1", "SKIRT")},
        [_CAT_REBAJAS],
    )

    list(store.list_catalog())

    cat = store._cat_by_product["R1"]
    assert (cat.section, cat.category) == ("ropa", "vestidos")
    assert cat.category_id == 1030302501, "el id de la hoja, que es con lo que se arma la URL"


def test_la_hoja_de_campana_apagada_no_compromete_ni_cuenta_como_caida() -> None:
    """Al acabar la campaña la hoja se va del menú, y eso es su comportamiento normal.

    Contarla como caída haría dos daños cada temporada: subir `dead_ratio` hacia el tope que aborta
    la pasada, y sacar de las bajas un ámbito que sus 38 hojas de siempre han listado perfectamente.
    """
    store = _scan_store(
        _menu({1030267678: "uuid-camisetas"}),  # la de rebajas ya no está
        {"uuid-camisetas": _grid("C1")},
        [CategoryConfig(1030267678, "niña", "ropa", "camisetas"), _CAT_REBAJAS],
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["C1"]
    assert (report.leaves_total, report.leaves_failed) == (1, 0)
    assert report.failed_scopes == set()
    assert report.failed_leaves == []


def test_check_leaves_marca_la_hoja_de_campana_como_estacional() -> None:
    """Sigue retirada (no se puede listar), pero el vigía tiene que poder no gritarlo cada
    jueves."""
    store = _scan_store(
        _menu({1030267678: "uuid-camisetas"}),
        {"uuid-camisetas": _grid("C1")},
        [CategoryConfig(1030267678, "niña", "ropa", "camisetas"), _CAT_REBAJAS],
    )

    hojas = {h.leaf: h for h in store.check_leaves()}

    assert hojas["1030267678"].alive is True
    assert hojas["1030302501"].alive is False, "apagada es apagada: no se puede listar"
    assert hojas["1030302501"].estacional is True
    assert hojas["1030267678"].estacional is False
