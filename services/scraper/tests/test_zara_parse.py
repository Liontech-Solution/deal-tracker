"""Tests de parsing de Zara con fixtures reales capturados de la web (golden-file)."""

from __future__ import annotations

from decimal import Decimal

from scraper.ingest import _discount_pct
from scraper.stores.zara import CATEGORIES, parse_detail_product, parse_listing_ids

from .conftest import load_fixture

_CAT = CATEGORIES[0]  # niña / zapateria / zapatos


def test_parse_listing_ids_extrae_ids_estables() -> None:
    listing = load_fixture("zara_category_2427610.json")
    ids = parse_listing_ids(listing)
    assert ids, "el listado debería contener productos"
    assert "545453620" in ids  # bailarina barefoot vista en el probe
    assert len(ids) == len(set(ids)), "no debe haber ids duplicados"
    assert all(isinstance(i, str) for i in ids)


def test_parse_detail_product_construye_producto_con_variantes() -> None:
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], _CAT)

    assert product is not None
    assert product.retailer_product_id == "545453620"
    assert "BAREFOOT" in product.name.upper()
    assert product.gender == "niña"
    assert product.section == "zapateria"
    assert product.url and product.url.endswith(".html")
    assert product.variants, "debería tener variantes talla/color"

    v = product.variants[0]
    assert v.retailer_variant_id.startswith("545453620-")  # {pid}-{color}-{size}
    assert v.price == Decimal("39.95")  # 3995 céntimos
    assert v.color == "Negro"
    assert v.size is not None
    assert v.sku is not None
    assert isinstance(v.in_stock, bool)


def test_precios_en_euros_y_variant_id_unico() -> None:
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], _CAT)
    assert product is not None
    ids = [v.retailer_variant_id for v in product.variants]
    assert len(ids) == len(set(ids)), "cada talla/color debe tener id único"
    assert all(v.price > 0 for v in product.variants)


def test_discount_pct() -> None:
    assert _discount_pct(Decimal("39.95"), None) is None
    assert _discount_pct(Decimal("40"), Decimal("40")) is None  # sin rebaja real
    assert _discount_pct(Decimal("30"), Decimal("60")) == Decimal("50.00")
    assert _discount_pct(Decimal("50"), Decimal("40")) is None  # precio > original: no es descuento
