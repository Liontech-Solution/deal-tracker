"""Test de integración de la ingesta contra Postgres: upsert, historial y altas/bajas."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scraper.ingest import ingest
from scraper.stores.base import ScrapedProduct, ScrapedVariant

T1 = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)


class FakeStore:
    """Scraper falso: devuelve productos predefinidos, sin tocar la red."""

    slug = "fake"
    name = "Fake Store"
    base_url = "https://fake.example/"

    def __init__(self, products: list[ScrapedProduct]) -> None:
        self._products = products

    def discover(self) -> Iterable[ScrapedProduct]:
        return list(self._products)


def _variant(
    vid: str, price: str, list_price: str | None = None, in_stock: bool = True
) -> ScrapedVariant:
    return ScrapedVariant(
        retailer_variant_id=vid,
        size="30",
        color="Negro",
        sku=f"sku-{vid}",
        price=Decimal(price),
        list_price=Decimal(list_price) if list_price else None,
        in_stock=in_stock,
    )


def _product(pid: str, name: str, variants: list[ScrapedVariant]) -> ScrapedProduct:
    return ScrapedProduct(
        retailer_product_id=pid,
        name=name,
        gender="niña",
        section="zapateria",
        category="zapatos",
        url=f"https://fake.example/p{pid}.html",
        variants=variants,
    )


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def test_primera_pasada_persiste_catalogo_e_historial(db_conn: Any) -> None:
    store = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ]
    )

    result = ingest(db_conn, store, run_ts=T1)

    assert result.products_seen == 2
    assert result.variants_seen == 3
    assert result.prices_recorded == 3
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT count(*) FROM retailer") == 1
    assert _scalar(db_conn, "SELECT count(*) FROM product") == 2
    assert _scalar(db_conn, "SELECT count(*) FROM variant") == 3
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 3
    assert (
        _scalar(db_conn, "SELECT status FROM scrape_run WHERE id = %s", (result.scrape_run_id,))
        == "success"
    )


def test_segunda_pasada_detecta_altas_bajas_y_apila_historial(db_conn: Any) -> None:
    store1 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ]
    )
    ingest(db_conn, store1, run_ts=T1)

    # Segunda pasada: A sigue (con rebaja en A-1), B desaparece (baja), C es nuevo (alta).
    store2 = FakeStore(
        [
            _product(
                "A",
                "Bailarina",
                [_variant("A-1", "29.95", list_price="39.95"), _variant("A-2", "39.95")],
            ),
            _product("C", "Sandalia", [_variant("C-1", "25.00")]),
        ]
    )
    result = ingest(db_conn, store2, run_ts=T2)

    # Altas: C existe y su first_seen_at es de la segunda pasada.
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE retailer_product_id = 'C'") == 1
    assert (
        _scalar(db_conn, "SELECT first_seen_at FROM product WHERE retailer_product_id = 'C'") == T2
    )

    # Bajas: B queda descatalogado (producto y variante).
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id = 'B'") == T2
    assert result.products_delisted == 1
    assert result.variants_delisted == 1

    # A sigue vivo y actualizado.
    assert (
        _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id = 'A'") is None
    )
    assert (
        _scalar(db_conn, "SELECT last_seen_at FROM product WHERE retailer_product_id = 'A'") == T2
    )

    # Historial apilado: 3 (run1) + 3 (run2: A-1, A-2, C-1). B no genera nuevo precio.
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 6

    # La rebaja de A-1 se registró con su discount_pct.
    disc = _scalar(
        db_conn,
        """
        SELECT ph.discount_pct FROM price_history ph
        JOIN variant v ON v.id = ph.variant_id
        WHERE v.retailer_variant_id = 'A-1' AND ph.scraped_at = %s
        """,
        (T2,),
    )
    assert disc == Decimal("25.03")  # (39.95-29.95)/39.95
