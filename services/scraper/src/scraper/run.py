"""CLI del job de scraping.

Uso:
    python -m scraper.run --retailer zara [--migrate] [--dry-run]
    python -m scraper.run --retailer zara --check-categories   # vigilancia, no ingiere

En dev local se ejecuta a mano; en el cluster lo invocará un CronJob de k8s
(definido en el repo de manifiestos, no aquí).
"""

from __future__ import annotations

import argparse
import sys

from . import db
from .config import Config, load_dotenv
from .ingest import CatalogScanAborted, ingest
from .migrate import apply_migrations
from .stores.base import BaseStore, SupportsLeafHealth, SupportsScanReport
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
    parser.add_argument(
        "--check-categories",
        action="store_true",
        help="sondea las hojas de categoría y sale != 0 si alguna ha caducado (no ingiere)",
    )
    return parser.parse_args(argv)


def _report_dead_leaves(store: BaseStore) -> None:
    """Avisa de las hojas de categoría que la tienda ya no sirve (solo en `--dry-run`)."""
    if not isinstance(store, SupportsScanReport):
        return
    report = store.scan_report()
    if report.leaves_failed:
        print(
            f"⚠ {report.leaves_failed}/{report.leaves_total} hojas de categoría no responden: "
            f"busca sus ids nuevos en el árbol de categorías de la tienda"
        )


def _check_categories(config: Config, slug: str) -> int:
    """Sondeo preventivo de las hojas de categoría. Devuelve el código de salida.

    Es la vigilancia que faltaba: la pasada ya no muere por una hoja caducada, pero mientras nadie
    la arregle esa categoría deja de ingerirse, y hoy eso solo se ve en el resumen de un job que
    nadie mira.

    **Solo falla por lo accionable.** Una hoja RETIRADA pide un id nuevo, así que sale != 0. Una
    hoja SIN VEREDICTO se avisa pero no rompe: medido contra Sfera, un chequeo normal ya trae un
    403 suelto de Akamai, y un vigía que da falsas alarmas rutinarias acaba silenciado — que es
    peor que no tenerlo. La excepción es que **ninguna** hoja se confirme viva: eso ya no es un
    blip, es un bloqueo, y sí debe cantar.
    """
    store = get_store(slug, config)
    if not isinstance(store, SupportsLeafHealth):
        print(f"{slug} no sabe sondear sus categorías (no implementa SupportsLeafHealth)")
        return 0

    vivas = 0
    retiradas: list[str] = []
    sin_veredicto: list[str] = []
    for leaf in store.check_leaves():
        ambito = f"{leaf.scope.gender}/{leaf.scope.section}/{leaf.scope.category}"
        linea = f"  {leaf.leaf}  ({ambito})  {leaf.detail}"
        if leaf.alive:
            vivas += 1
        elif leaf.alive is False:
            retiradas.append(linea)
        else:
            sin_veredicto.append(linea)

    total = vivas + len(retiradas) + len(sin_veredicto)
    print(f"{slug}: {vivas}/{total} hojas de categoría vivas.")
    if retiradas:
        print(f"✖ {len(retiradas)} RETIRADAS — busca sus ids nuevos y actualiza CATEGORIES:")
        for linea in retiradas:
            print(linea)
    if sin_veredicto:
        print(f"⚠ {len(sin_veredicto)} sin veredicto (fallo del sondeo, no retirada confirmada):")
        for linea in sin_veredicto:
            print(linea)

    if retiradas:
        return 1
    if total and not vivas:
        print("✖ ninguna hoja confirmada viva: esto no es un blip, es un bloqueo.")
        return 1
    return 0


def _dry_run(config: Config, slug: str) -> int:
    store = get_store(slug, config)
    entries = list(store.list_catalog())
    _report_dead_leaves(store)
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

    if args.check_categories:
        return _check_categories(config, args.retailer)

    if args.dry_run:
        return _dry_run(config, args.retailer)

    store = get_store(args.retailer, config)
    with db.connect(config) as conn:
        if args.migrate:
            applied = apply_migrations(conn)
            if applied:
                print(f"migraciones aplicadas: {', '.join(applied)}")
        try:
            result = ingest(
                conn,
                store,
                delist_min_baseline=config.delist_min_baseline,
                delist_drop_ratio=config.delist_drop_ratio,
                delist_min_misses=config.delist_min_misses,
                delist_probe=config.delist_probe,
                delist_probe_max=config.delist_probe_max,
                detail_max_age_days=config.detail_max_age_days,
                detail_refresh_max=config.detail_refresh_max,
                scan_max_dead_ratio=config.scan_max_dead_ratio,
            )
        except CatalogScanAborted as exc:
            # Un traceback aquí no aporta nada: el mensaje ya dice qué pasó y qué mirar.
            print(f"✖ pasada abortada: {exc}", file=sys.stderr)
            return 1

    print(
        f"run #{result.scrape_run_id} OK — "
        f"{result.products_in_catalog} en catálogo "
        f"({result.details_fetched} con detalle, {result.products_unchanged} sin cambios), "
        f"{result.variants_seen} variantes, {result.prices_recorded} precios; "
        f"bajas: {result.products_delisted} productos / {result.variants_delisted} variantes"
    )
    if result.barefoot_counts:
        reparto = ", ".join(f"{k}: {v}" for k, v in sorted(result.barefoot_counts.items()))
        print(f"calzado por marca barefoot: {reparto}")
    if result.details_refreshed:
        print(
            f"refresco forzado: {result.details_refreshed} productos con el detalle rancio "
            f"(> {config.detail_max_age_days} días) vueltos a observar"
        )
    if result.products_missing or result.variants_missing:
        print(
            f"ausentes pendientes de confirmar (histéresis, umbral {config.delist_min_misses}): "
            f"{result.products_missing} productos / {result.variants_missing} variantes"
        )
    if result.probes_sent or result.probes_unresolved:
        print(
            f"confirmación activa: {result.probes_sent} sondeos "
            f"({result.probes_alive} siguen a la venta, {result.probes_dead} retirados, "
            f"{result.probes_unresolved} sin confirmar: se reintentan)"
        )
    if result.skipped_scopes:
        print(
            f"⚠ {result.skipped_scopes}/{result.scanned_scopes} ámbitos con caída sospechosa: "
            f"bajas omitidas (posible fallo de scraping). Revisa el listado de la tienda."
        )
    if result.leaves_failed:
        ambitos = result.unscanned_scopes
        print(
            f"⚠ {result.leaves_failed}/{result.leaves_scanned} hojas de categoría no responden: "
            f"esas categorías han dejado de ingerirse y "
            f"{ambitos} ámbito{'' if ambitos == 1 else 's'} se "
            f"{'queda' if ambitos == 1 else 'quedan'} sin detección de bajas. "
            f"Busca sus ids nuevos en el árbol de la tienda."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
