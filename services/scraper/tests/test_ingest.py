"""Test de integración de la ingesta contra Postgres.

Cubre: upsert de catálogo, apilado de historial, detección de altas/bajas y el
**detalle condicional** (si la huella del listado no cambia, no se pide el detalle).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scraper.ingest import ingest
from scraper.stores.base import ListingEntry, ScrapedProduct, ScrapedVariant

T1 = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)
T3 = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)


class FakeStore:
    """Scraper falso de dos fases. Registra a qué productos se les pidió detalle."""

    slug = "fake"
    name = "Fake Store"
    base_url = "https://fake.example/"

    def __init__(self, products: list[ScrapedProduct], signatures: dict[str, str]) -> None:
        self._by_id = {p.retailer_product_id: p for p in products}
        self._sigs = signatures
        self.detail_calls: list[str] = []

    def list_catalog(self) -> Iterable[ListingEntry]:
        for pid, p in self._by_id.items():
            yield ListingEntry(pid, self._sigs[pid], p.gender, p.section, p.category)

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        for e in entries:
            self.detail_calls.append(e.retailer_product_id)
            product = self._by_id.get(e.retailer_product_id)
            if product is not None:
                yield product


def _variant(vid: str, price: str, list_price: str | None = None) -> ScrapedVariant:
    return ScrapedVariant(
        retailer_variant_id=vid,
        size="30",
        color="Negro",
        sku=f"sku-{vid}",
        price=Decimal(price),
        list_price=Decimal(list_price) if list_price else None,
        in_stock=True,
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
        ],
        signatures={"A": "a1", "B": "b1"},
    )

    result = ingest(db_conn, store, run_ts=T1)

    assert result.products_in_catalog == 2
    assert result.details_fetched == 2  # todo es nuevo -> se pide detalle de todo
    assert result.products_unchanged == 0
    assert result.variants_seen == 3
    assert sorted(store.detail_calls) == ["A", "B"]
    assert _scalar(db_conn, "SELECT count(*) FROM product") == 2
    assert _scalar(db_conn, "SELECT count(*) FROM variant") == 3
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 3
    assert (
        _scalar(db_conn, "SELECT status FROM scrape_run WHERE id = %s", (result.scrape_run_id,))
        == "success"
    )


def test_detalle_condicional_altas_y_bajas(db_conn: Any) -> None:
    store1 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    # Segunda pasada: A con la MISMA huella (sin cambios), B desaparece (baja), C nuevo (alta).
    store2 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("C", "Sandalia", [_variant("C-1", "25.00")]),
        ],
        signatures={"A": "a1", "C": "c1"},
    )
    result = ingest(db_conn, store2, run_ts=T2)

    # Detalle condicional: a A NO se le pide detalle (huella intacta); a C sí.
    assert store2.detail_calls == ["C"]
    assert result.details_fetched == 1
    assert result.products_unchanged == 1

    # Altas: C existe con first_seen_at de la segunda pasada.
    assert _scalar(db_conn, "SELECT first_seen_at FROM product WHERE retailer_product_id='C'") == T2

    # Bajas: B descatalogado (producto y su variante).
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") == T2
    assert result.products_delisted == 1
    assert result.variants_delisted == 1

    # A sigue vivo: "tocado" aunque no se pidió su detalle (producto y variantes).
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A'") is None
    assert _scalar(db_conn, "SELECT last_seen_at FROM product WHERE retailer_product_id='A'") == T2
    assert (
        _scalar(
            db_conn,
            "SELECT count(*) FROM variant v JOIN product p ON p.id=v.product_id "
            "WHERE p.retailer_product_id='A' AND v.delisted_at IS NULL AND v.last_seen_at=%s",
            (T2,),
        )
        == 2
    )

    # Historial: 3 (run1) + 1 (C). A no genera precios nuevos al no pedirse su detalle.
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 4


def test_cambio_de_huella_fuerza_detalle_y_apila_precio(db_conn: Any) -> None:
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")])],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    # A rebaja: cambia la huella -> se pide detalle y se apila el nuevo precio con descuento.
    store2 = FakeStore(
        [
            _product(
                "A",
                "Bailarina",
                [_variant("A-1", "29.95", list_price="39.95"), _variant("A-2", "39.95")],
            )
        ],
        signatures={"A": "a2"},
    )
    result = ingest(db_conn, store2, run_ts=T2)

    assert store2.detail_calls == ["A"]
    assert result.details_fetched == 1
    assert result.products_unchanged == 0
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 4  # 2 + 2

    disc = _scalar(
        db_conn,
        "SELECT ph.discount_pct FROM price_history ph JOIN variant v ON v.id=ph.variant_id "
        "WHERE v.retailer_variant_id='A-1' AND ph.scraped_at=%s",
        (T2,),
    )
    assert disc == Decimal("25.03")  # (39.95-29.95)/39.95
