"""Tests de parsing de Lefties con fixtures reales capturados de su API (golden-file).

Son herméticos: NO necesitan navegador ni red. Ejercitan las funciones puras sobre un grid de
categoría (`grids/{uuid}`) y su respuesta de detalle (`productsArray`) reales.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

from scraper.stores.base import ScrapedImage
from scraper.stores.lefties import (
    CATEGORIES,
    CategoryConfig,
    grid_ids_by_category,
    known_product_ids,
    parse_detail_product,
    parse_listing_entries,
)

from .conftest import load_fixture

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

    # 19 componentes (colores) que son 9 modelos.
    assert len(grid["components"]) == 19
    assert len(entries) == 9
    assert len({e.retailer_product_id for e in entries}) == 9
    assert "747860883" in {e.retailer_product_id for e in entries}

    for e in entries:
        assert e.gender == "niña" and e.section == "zapateria" and e.category == "zapatos"
        assert e.signature, "la huella no puede quedar vacía"


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
