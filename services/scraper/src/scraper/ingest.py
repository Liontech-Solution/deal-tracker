"""Pipeline de ingesta: scraper -> upsert catálogo -> append historial -> altas/bajas.

Todo ocurre dentro de una única transacción por ejecución (`scrape_run`).
La detección de bajas se apoya en `last_seen_at`: los productos/variantes vistos
en esta ejecución quedan marcados con el timestamp del run; los que no se ven
conservan un `last_seen_at` anterior y se marcan como descatalogados (`delisted_at`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import psycopg

from .stores.base import BaseStore, ScrapedProduct, ScrapedVariant


@dataclass
class IngestResult:
    scrape_run_id: int
    products_seen: int
    variants_seen: int
    prices_recorded: int
    products_delisted: int
    variants_delisted: int


def _returned_id(cur: psycopg.Cursor) -> int:
    """Devuelve el id de una cláusula RETURNING como int (tipado estricto)."""
    row = cur.fetchone()
    assert row is not None, "se esperaba una fila RETURNING"
    return int(row[0])


def _discount_pct(price: Decimal, list_price: Decimal | None) -> Decimal | None:
    """% de rebaja respecto al precio original (None si no hay o no es un descuento real)."""
    if list_price is None or list_price <= 0 or price >= list_price:
        return None
    return ((list_price - price) / list_price * 100).quantize(Decimal("0.01"))


def _upsert_retailer(cur: psycopg.Cursor, store: BaseStore) -> int:
    cur.execute(
        """
        INSERT INTO retailer (slug, name, base_url)
        VALUES (%s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, base_url = EXCLUDED.base_url
        RETURNING id
        """,
        (store.slug, store.name, store.base_url),
    )
    return _returned_id(cur)


def _upsert_product(
    cur: psycopg.Cursor, retailer_id: int, run_ts: datetime, product: ScrapedProduct
) -> int:
    cur.execute(
        """
        INSERT INTO product (retailer_id, retailer_product_id, name, gender, section,
                             category, url, first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_product_id) DO UPDATE SET
            name = EXCLUDED.name,
            gender = EXCLUDED.gender,
            section = EXCLUDED.section,
            category = EXCLUDED.category,
            url = EXCLUDED.url,
            last_seen_at = EXCLUDED.last_seen_at,
            delisted_at = NULL
        RETURNING id
        """,
        (
            retailer_id,
            product.retailer_product_id,
            product.name,
            product.gender,
            product.section,
            product.category,
            product.url,
            run_ts,
            run_ts,
        ),
    )
    return _returned_id(cur)


def _upsert_variant(
    cur: psycopg.Cursor, product_id: int, run_ts: datetime, variant: ScrapedVariant
) -> int:
    cur.execute(
        """
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url,
                             first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (product_id, retailer_variant_id) DO UPDATE SET
            size = EXCLUDED.size,
            color = EXCLUDED.color,
            sku = EXCLUDED.sku,
            url = EXCLUDED.url,
            last_seen_at = EXCLUDED.last_seen_at,
            delisted_at = NULL
        RETURNING id
        """,
        (
            product_id,
            variant.retailer_variant_id,
            variant.size,
            variant.color,
            variant.sku,
            variant.url,
            run_ts,
            run_ts,
        ),
    )
    return _returned_id(cur)


def _record_price(
    cur: psycopg.Cursor,
    variant_id: int,
    run_id: int,
    run_ts: datetime,
    variant: ScrapedVariant,
) -> None:
    cur.execute(
        """
        INSERT INTO price_history (variant_id, price, currency, list_price, discount_pct,
                                   in_stock, scraped_at, scrape_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            variant_id,
            variant.price,
            "EUR",
            variant.list_price,
            _discount_pct(variant.price, variant.list_price),
            variant.in_stock,
            run_ts,
            run_id,
        ),
    )


def ingest(
    conn: psycopg.Connection, store: BaseStore, run_ts: datetime | None = None
) -> IngestResult:
    """Ejecuta una pasada completa del scraper y persiste el resultado. Atómico."""
    run_ts = run_ts or datetime.now(UTC)
    try:
        with conn.cursor() as cur:
            retailer_id = _upsert_retailer(cur, store)
            cur.execute(
                "INSERT INTO scrape_run (retailer_id, started_at) VALUES (%s, %s) RETURNING id",
                (retailer_id, run_ts),
            )
            run_id = _returned_id(cur)

            products_seen = variants_seen = prices_recorded = 0
            for product in store.discover():
                product_id = _upsert_product(cur, retailer_id, run_ts, product)
                products_seen += 1
                for variant in product.variants:
                    variant_id = _upsert_variant(cur, product_id, run_ts, variant)
                    _record_price(cur, variant_id, run_id, run_ts, variant)
                    variants_seen += 1
                    prices_recorded += 1

            # Bajas: lo no visto en esta ejecución conserva un last_seen_at anterior.
            cur.execute(
                """
                UPDATE product SET delisted_at = %s
                WHERE retailer_id = %s AND delisted_at IS NULL AND last_seen_at < %s
                """,
                (run_ts, retailer_id, run_ts),
            )
            products_delisted = cur.rowcount
            cur.execute(
                """
                UPDATE variant v SET delisted_at = %s
                FROM product p
                WHERE v.product_id = p.id AND p.retailer_id = %s
                  AND v.delisted_at IS NULL AND v.last_seen_at < %s
                """,
                (run_ts, retailer_id, run_ts),
            )
            variants_delisted = cur.rowcount

            cur.execute(
                """
                UPDATE scrape_run
                SET finished_at = now(), status = 'success',
                    products_seen = %s, variants_seen = %s
                WHERE id = %s
                """,
                (products_seen, variants_seen, run_id),
            )
        conn.commit()
        return IngestResult(
            scrape_run_id=run_id,
            products_seen=products_seen,
            variants_seen=variants_seen,
            prices_recorded=prices_recorded,
            products_delisted=products_delisted,
            variants_delisted=variants_delisted,
        )
    except Exception:
        conn.rollback()
        raise
