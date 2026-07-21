"""CLI del job de scraping.

Uso:
    python -m scraper.run --retailer zara [--migrate] [--dry-run]

En dev local se ejecuta a mano; en el cluster lo invocará un CronJob de k8s
(definido en el repo de manifiestos, no aquí).
"""

from __future__ import annotations

import argparse
import sys

from . import db
from .config import Config, load_dotenv
from .ingest import ingest
from .migrate import apply_migrations
from .stores.registry import available_slugs, get_store


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scraper.run", description="Job de scraping de precios")
    parser.add_argument(
        "--retailer", required=True, help=f"slug de tienda ({', '.join(available_slugs())})"
    )
    parser.add_argument(
        "--migrate", action="store_true", help="aplica migraciones pendientes antes de ingerir"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="recorre el scraper y resume, sin escribir en base de datos",
    )
    return parser.parse_args(argv)


def _dry_run(config: Config, slug: str) -> int:
    store = get_store(slug, config)
    entries = list(store.list_catalog())
    products = variants = 0
    for product in store.fetch_details(entries):
        products += 1
        variants += len(product.variants)
        print(
            f"  [{product.retailer_product_id}] {product.name} — {len(product.variants)} variantes"
        )
    print(
        f"dry-run: {len(entries)} en catálogo, {products} con detalle, "
        f"{variants} variantes (sin escribir)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    config = Config.from_env()

    if args.dry_run:
        return _dry_run(config, args.retailer)

    store = get_store(args.retailer, config)
    with db.connect(config) as conn:
        if args.migrate:
            applied = apply_migrations(conn)
            if applied:
                print(f"migraciones aplicadas: {', '.join(applied)}")
        result = ingest(
            conn,
            store,
            delist_min_baseline=config.delist_min_baseline,
            delist_drop_ratio=config.delist_drop_ratio,
            delist_min_misses=config.delist_min_misses,
        )

    print(
        f"run #{result.scrape_run_id} OK — "
        f"{result.products_in_catalog} en catálogo "
        f"({result.details_fetched} con detalle, {result.products_unchanged} sin cambios), "
        f"{result.variants_seen} variantes, {result.prices_recorded} precios; "
        f"bajas: {result.products_delisted} productos / {result.variants_delisted} variantes"
    )
    if result.products_missing or result.variants_missing:
        print(
            f"ausentes pendientes de confirmar (histéresis, umbral {config.delist_min_misses}): "
            f"{result.products_missing} productos / {result.variants_missing} variantes"
        )
    if result.skipped_scopes:
        print(
            f"⚠ {result.skipped_scopes}/{result.scanned_scopes} ámbitos con caída sospechosa: "
            f"bajas omitidas (posible fallo de scraping). Revisa el listado de la tienda."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
