"""Pipeline de ingesta en dos fases con detalle condicional.

1. `store.list_catalog()` da una huella barata por producto (precio por color del listado).
2. Se compara con la huella del scrape anterior (columna `product.listing_signature`):
   - nuevo, huella cambiada o producto descatalogado que reaparece -> se pide el detalle
     completo y se apila precio;
   - sin cambios -> NO se pide detalle; solo se refresca `last_seen_at` (producto y variantes)
     para que no se marque como baja.

Todo ocurre en una única transacción por ejecución (`scrape_run`). Las bajas se detectan
igual que antes: lo no visto conserva un `last_seen_at` anterior y se marca `delisted_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import psycopg

from .stores.base import BaseStore, ListingEntry, ScrapedProduct, ScrapedVariant


@dataclass
class IngestResult:
    scrape_run_id: int
    products_in_catalog: int  # productos vistos en el listado
    details_fetched: int  # productos a los que se pidió detalle (nuevos/cambiados)
    products_unchanged: int  # productos sin cambios (ahorro de peticiones de detalle)
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


def _load_existing(
    cur: psycopg.Cursor, retailer_id: int
) -> dict[str, tuple[int, str | None, bool]]:
    """Estado actual por producto: retailer_product_id -> (id, huella, está_descatalogado)."""
    cur.execute(
        """
        SELECT retailer_product_id, id, listing_signature, (delisted_at IS NOT NULL)
        FROM product WHERE retailer_id = %s
        """,
        (retailer_id,),
    )
    return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}


def _touch_seen(cur: psycopg.Cursor, product_id: int, run_ts: datetime) -> None:
    """Marca producto y variantes activas como vistos en este run, sin tocar precios."""
    cur.execute("UPDATE product SET last_seen_at = %s WHERE id = %s", (run_ts, product_id))
    cur.execute(
        "UPDATE variant SET last_seen_at = %s WHERE product_id = %s AND delisted_at IS NULL",
        (run_ts, product_id),
    )


def _upsert_product(
    cur: psycopg.Cursor,
    retailer_id: int,
    run_ts: datetime,
    product: ScrapedProduct,
    signature: str,
) -> int:
    cur.execute(
        """
        INSERT INTO product (retailer_id, retailer_product_id, name, gender, section,
                             category, url, listing_signature, first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (retailer_id, retailer_product_id) DO UPDATE SET
            name = EXCLUDED.name,
            gender = EXCLUDED.gender,
            section = EXCLUDED.section,
            category = EXCLUDED.category,
            url = EXCLUDED.url,
            listing_signature = EXCLUDED.listing_signature,
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
            signature,
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


def _needs_detail(entry: ListingEntry, existing: tuple[int, str | None, bool] | None) -> bool:
    """Pide detalle si es nuevo, si la huella del listado cambió, o si estaba descatalogado."""
    if existing is None:
        return True
    _product_id, signature, is_delisted = existing
    return is_delisted or signature != entry.signature


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
            existing = _load_existing(cur, retailer_id)

            # Fase 1: listado barato. Decidimos a quién pedir detalle y a quién solo "tocar".
            entries = list(store.list_catalog())
            to_fetch: list[ListingEntry] = []
            signature_by_id: dict[str, str] = {}
            products_unchanged = 0
            for entry in entries:
                if _needs_detail(entry, existing.get(entry.retailer_product_id)):
                    to_fetch.append(entry)
                    signature_by_id[entry.retailer_product_id] = entry.signature
                else:
                    _touch_seen(cur, existing[entry.retailer_product_id][0], run_ts)
                    products_unchanged += 1

            # Fase 2: detalle SOLO de nuevos/cambiados -> upsert + apilar precio.
            details_fetched = variants_seen = prices_recorded = 0
            for product in store.fetch_details(to_fetch):
                details_fetched += 1
                signature = signature_by_id.get(product.retailer_product_id, "")
                product_id = _upsert_product(cur, retailer_id, run_ts, product, signature)
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
                (len(entries), variants_seen, run_id),
            )
        conn.commit()
        return IngestResult(
            scrape_run_id=run_id,
            products_in_catalog=len(entries),
            details_fetched=details_fetched,
            products_unchanged=products_unchanged,
            variants_seen=variants_seen,
            prices_recorded=prices_recorded,
            products_delisted=products_delisted,
            variants_delisted=variants_delisted,
        )
    except Exception:
        conn.rollback()
        raise
