"""CLI del job de scraping.

Uso:
    python -m scraper.run --retailer zara [--migrate] [--dry-run]
    python -m scraper.run --retailer zara --check-categories   # vigilancia, no ingiere

En dev local se ejecuta a mano; en el cluster lo invocará un CronJob de k8s
(definido en el repo de manifiestos, no aquí).

`--check-categories` es la versión a mano y sobre una tienda de lo que `scraper.vigia` hace
programado y sobre todas: comparten la regla de veredicto, no una copia de ella.
"""

from __future__ import annotations

import argparse
import sys

from . import db
from .config import Config, load_dotenv
from .ingest import CatalogScanAborted, ingest
from .migrate import apply_migrations
from .stores.base import BaseStore, CategoryNode, SupportsCategoryTree, SupportsScanReport
from .stores.registry import available_slugs, get_store
from .vigia import Informe, revisar_hojas


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
    parser.add_argument(
        "--tree",
        metavar="RUTA",
        help="lista las categorías que la tienda publica bajo RUTA y marca cuáles ingerimos",
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
    """Sondeo preventivo de las hojas de categoría de UNA tienda. Devuelve el código de salida.

    Es la vigilancia que faltaba: la pasada ya no muere por una hoja caducada, pero mientras nadie
    la arregle esa categoría deja de ingerirse, y hoy eso solo se ve en el resumen de un job que
    nadie mira.

    La regla de veredicto (**solo falla por lo accionable**: RETIRADA rompe, SIN VEREDICTO avisa, y
    que ninguna esté viva rompe porque ya es un bloqueo) vive en `vigia.revisar_hojas` y aquí solo
    se invoca. Tenerla en dos sitios sería pedir que se separasen: esto y el vigía (#67) responden a
    la misma pregunta, uno a mano sobre una tienda y otro programado sobre todas.
    """
    informe = Informe(slug)
    revisar_hojas(get_store(slug, config), informe)
    print(informe.render())
    return 0 if informe.esta_bien else 1


def _tree(config: Config, slug: str, root: str) -> int:
    """Enumera el árbol de categorías que publica la tienda. Devuelve el código de salida.

    Es una herramienta de RECONOCIMIENTO, no un vigía: informa y sale 0 pase lo que pase. El que
    falla por lo accionable es `--check-categories`, y son preguntas distintas — aquel comprueba
    que lo que ingerimos sigue vivo, este enseña lo que existe y no estamos ingiriendo.

    Existe porque en algunas tiendas no se pueden adivinar las rutas: Sfera devuelve 200 con el
    catálogo del padre a una ruta que no existe (#54), así que probar nombres copiados de otra
    rama no distingue una hoja real de una inventada. La faceta de categorías sí.

    **Lo ya descubierto se imprime aunque la bajada se corte a medias.** El árbol se recorre rama
    a rama, y en estas tiendas un 403 suelto de Akamai es rutina: perder el recon entero —y las
    peticiones que costó— por el último tramo sería justo lo contrario de para lo que sirve. Se
    dice qué falló, y sigue saliendo 0: quien lo lanza está mirando la salida.
    """
    store = get_store(slug, config)
    if not isinstance(store, SupportsCategoryTree):
        print(f"{slug} no sabe enumerar sus categorías (no implementa SupportsCategoryTree)")
        return 0

    mapeadas = set(store.mapped_leaves())
    nodos: list[CategoryNode] = []
    fallo: str | None = None
    try:
        # Se acumula a medida que llega, no con `list(...)`: si el generador revienta a mitad,
        # `list` no devuelve nada parcial y se pierde todo lo que ya se había leído.
        for nodo in store.category_tree(root):
            nodos.append(nodo)
    except Exception as exc:  # red, bloqueo, respuesta ilegible: se informa y se sigue
        fallo = f"{type(exc).__name__}: {exc}"

    sin_mapear = 0
    lineas: list[str] = []
    for nodo in nodos:
        marca = "✓" if nodo.path in mapeadas else "·"
        if nodo.path not in mapeadas:
            sin_mapear += 1
        sangria = "  " * nodo.depth
        # `None` y 0 son cosas distintas: «no lo dice» frente a «hoja real y vacía».
        cuenta = "     ?" if nodo.count is None else f"{nodo.count:>6}"
        hijos = " ▸" if nodo.has_children else ""
        lineas.append(f"  {marca} {sangria}{nodo.path:<44}{cuenta}  {nodo.title}{hijos}")

    if nodos:
        print(f"{slug} · árbol de {root}: {len(nodos)} categorías, {sin_mapear} sin mapear")
        for linea in lineas:
            print(linea)
        print("  ✓ = ya en CATEGORIES · · = existe y no la ingerimos · ▸ = tiene hijas")
    elif fallo is None:
        print(f"{slug}: {root} no publica ninguna categoría por debajo (¿es ya una hoja?).")

    if fallo is not None:
        print(
            f"⚠ el recorrido se cortó antes de acabar ({fallo}): el árbol de arriba está a medias."
        )
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

    if args.tree:
        return _tree(config, args.retailer, args.tree)

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
