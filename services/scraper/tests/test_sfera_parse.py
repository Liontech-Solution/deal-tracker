"""Tests de parsing de Sfera con fixtures reales capturados de la API firefly (golden-file).

Son herméticos: NO necesitan navegador ni red. Ejercitan las funciones puras
(`parse_products`, helpers y huella) sobre respuestas `firefly/products_list` reales.
"""

from __future__ import annotations

from decimal import Decimal

from scraper.ingest import _discount_pct
from scraper.stores.sfera import (
    CategoryConfig,
    pagination_of,
    parse_products,
    product_signature,
    products_of,
)

from .conftest import load_fixture

_NINO = CategoryConfig("ninos/nino", "niño", "ropa", "camisetas")
_REBAJAS = CategoryConfig("rebajas/ninos", "niña", "ropa", "camisetas")


def test_products_of_y_pagination_of() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    productos = products_of(payload)
    assert productos, "el fixture debería traer productos"
    pag = pagination_of(payload)
    assert pag["_total"] >= 1 and pag["count"] >= 1
    # Robustez ante payloads malformados (modo de fallo => vacío, lo cubre la red de bajas).
    assert products_of({}) == []
    assert pagination_of({"data": None}) == {}


def test_parse_products_extrae_id_estable_tallas_y_stock() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    assert products, "debería parsear productos"

    ids = [p.retailer_product_id for p in products]
    assert "A200974138" in ids  # id estable de producto (también en la URL)
    assert all(pid.startswith("A") for pid in ids)

    product = next(p for p in products if p.retailer_product_id == "A200974138")
    assert product.name
    assert product.gender == "niño"
    assert product.section == "ropa"
    assert product.category == "camisetas"
    assert product.url and product.url.startswith("https://www.sfera.com")
    assert product.variants, "debería tener variantes talla/color"

    v = product.variants[0]
    assert v.retailer_variant_id == "001015811718640004"  # sku estable de la talla
    assert v.sku == v.retailer_variant_id
    assert v.color == "Azul marino"
    assert v.size == "4-5 años"  # normalizada (venía "4-5 años/4-5 años")
    assert v.price == Decimal("5.99")
    assert v.list_price is None  # sin rebaja en esta categoría
    assert isinstance(v.in_stock, bool)

    # Ids de variante únicos dentro del producto.
    vids = [v.retailer_variant_id for v in product.variants]
    assert len(vids) == len(set(vids))


def test_parse_products_rebajas_mapea_precio_y_descuento() -> None:
    payload = load_fixture("sfera_firefly_rebajas_ninos.json")
    products = parse_products(products_of(payload), _REBAJAS)
    assert products

    # Alguna variante rebajada: price=sale_price (actual), list_price=price (tachado).
    rebajada = next(v for p in products for v in p.variants if v.list_price is not None)
    assert rebajada.price < rebajada.list_price
    assert rebajada.price == Decimal("3.99")
    assert rebajada.list_price == Decimal("7.99")
    # El veredicto de descuento honesto (server-side) recibe un descuento real (~50%).
    assert _discount_pct(rebajada.price, rebajada.list_price) == Decimal("50.06")


def test_product_signature_determinista_y_sensible_al_precio() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    p = products[0]
    # Reparsear el mismo fixture da la misma huella (estable, sin depender del orden).
    again = parse_products(products_of(load_fixture("sfera_firefly_ninos_nino.json")), _NINO)[0]
    assert product_signature(p) == product_signature(again)
    # La huella incluye el precio efectivo: un cambio de precio la cambia.
    assert product_signature(p) != f"{p.variants[0].retailer_variant_id}:0.00"
