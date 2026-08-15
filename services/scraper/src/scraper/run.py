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
import logging
import sys
from collections import Counter
from collections.abc import Mapping

import psycopg

from . import db
from .config import Config, load_dotenv
from .ingest import CatalogScanAborted, ingest
from .migrate import LOCK_MIGRACIONES, apply_migrations
from .stores.base import (
    BaseStore,
    CategoryNode,
    SupportsCategoryTree,
    SupportsScanReport,
)
from .stores.registry import available_slugs, get_store
from .vigia import (
    COBERTURA_DECLARADA,
    COBERTURA_SIN_VIGILAR,
    Informe,
    cubierta,
    revisar_hojas,
)


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
        "--refresh-all",
        action="store_true",
        help="pide el detalle de todo el catálogo aunque nada haya cambiado y por reciente que "
        "sea (repara lo ya ingerido); sigue acotado por SCRAPER_DETAIL_REFRESH_MAX",
    )
    parser.add_argument(
        "--tree",
        metavar="RUTA",
        help="lista las categorías que la tienda publica bajo RUTA y marca cuáles ingerimos",
    )
    return parser.parse_args(argv)


def _reparto(counts: dict[str, int]) -> str:
    """`{'niña': 370, 'niño': 316}` -> `niña: 370, niño: 316`, en orden estable."""
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


def _cuales(nombres: list[str]) -> str:
    """` (ninos/bebe-nino/punto-y-jerseis)`, con lo que se ha caído nombrado.

    Existe porque el aviso decía CUÁNTAS hojas se habían caído pero no cuáles, y el número solo
    sirve para asustarse (#151). Desde #155 lo pasan las nueve tiendas —`leaf` es obligatorio en
    `leaf_gone()`—, cada una en su vocabulario: la ruta en Sfera e Hipercor, el `catalogId` en
    Mango, el fichero de sitemap en Springfield. La guarda del vacío se queda porque este informe
    se construye desde datos, no desde el tipo.

    Sirve igual para las dos redes de seguridad de las bajas, que sufrían el mismo mal: desde #170
    también nombra los ámbitos con caída sospechosa (`niña/ropa/camisetas`). Aquí no hay tope de
    nombres —lo hay en `scrape_run.message`, ver `ingest._MAX_NAMED_LEAVES`— porque el que acota
    aquel es el ancho de la columna, y stdout no lo tiene.
    """
    return f" ({', '.join(sorted(nombres))})" if nombres else ""


def _report_residuo(
    total: int, secas: list[str], por_hoja: Mapping[str, int] | None = None
) -> None:
    """Cuánto ha aportado el rescate del residuo (#358), con el desglose si se tiene.

    Se imprime **siempre que la tienda use el mecanismo**, aporte o no, y esa es la gracia: la
    cifra sana es la línea base contra la que se nota que algo se ha roto. Publicar solo la
    anomalía dejaría al operador sin saber nunca cuánto era lo normal.

    Al `scrape_run.message` va lo contrario —solo la anomalía, nunca la cifra—, y el reparto es
    deliberado: ver la nota de `_success_message()` en `ingest.py`.

    `por_hoja` solo lo tiene `--dry-run`, que trabaja con el `ScanReport` en la mano; la pasada
    normal lee el `IngestResult`, que lleva el total y las hojas secas ya resumidos.
    """
    if por_hoja is not None:
        if not por_hoja:
            return
        detalle = ", ".join(f"{hoja} {n}" for hoja, n in sorted(por_hoja.items()))
        print(f"  rescate del residuo: {total} entradas de {len(por_hoja)} hojas [{detalle}]")
    elif total or secas:
        print(f"  rescate del residuo: {total} entradas")
    if secas:
        print(
            f"⚠ {len(secas)} hoja{'' if len(secas) == 1 else 's'} con filtro no "
            f"{'aportó' if len(secas) == 1 else 'aportaron'} nada de residuo"
            f"{_cuales(secas)}: comprueba si la tienda ha cambiado cómo rotula esas familias "
            f"(`_FAMILIA_RESIDUAL`), porque el rescate de #289 dejaría de funcionar en silencio"
        )


def _report_dead_leaves(store: BaseStore) -> None:
    """Avisa de las hojas de categoría que la tienda ya no sirve (solo en `--dry-run`)."""
    if not isinstance(store, SupportsScanReport):
        return
    report = store.scan_report()
    if report.leaves_failed:
        print(
            f"⚠ {report.leaves_failed}/{report.leaves_total} hojas de categoría no responden"
            f"{_cuales(report.failed_leaves)}: "
            f"busca sus ids nuevos en el árbol de categorías de la tienda"
        )
    if report.empty_filter_leaves:
        cuantas = len(report.empty_filter_leaves)
        print(
            f"⚠ {cuantas} hoja{'' if cuantas == 1 else 's'} "
            f"{'respondió' if cuantas == 1 else 'respondieron'} pero su filtro no casó con nada"
            f"{_cuales(report.empty_filter_leaves)}: mira cómo se llaman hoy sus productos"
        )
    _report_residuo(report.residual_entries, report.barren_residual_leaves, report.residual_by_leaf)


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

    # Las marcas son las MISMAS categorías que decide el vigía (#156), y eso importa: antes esto
    # contaba «sin mapear» a secas y decía 44 donde el vigía dice 0 huecos, porque 42 de esas 44
    # cuelgan de una hoja que sí ingerimos. Dos lecturas distintas del mismo árbol es cómo alguien
    # se convence de que el vigía está roto.
    declaradas = COBERTURA_DECLARADA.get(slug, {})
    # Desde #179 el separador lo declara quien enumera, no quien se vigila: antes las tiendas que
    # enumeran sin vigilarse (Mango, Zara) se quedaban sin él y pintaban como hueco todo lo que
    # cuelga de una hoja ingerida, que en la rama de niña de Zara eran 139 de 153.
    sep = store.tree_separator()

    huecos = 0
    lineas: list[str] = []
    for nodo in nodos:
        if nodo.path in mapeadas:
            marca = "✓"
        elif nodo.path in declaradas:
            marca = "×"
        elif sep and cubierta(nodo.path, mapeadas, sep):
            marca = "↳"  # cuelga de una que ingerimos: sus productos ya entran por el padre
        elif sep and cubierta(nodo.path, declaradas, sep):
            marca = "×"
        else:
            marca = "·"
            huecos += 1
        sangria = "  " * nodo.depth
        # `None` y 0 son cosas distintas: «no lo dice» frente a «hoja real y vacía».
        cuenta = "     ?" if nodo.count is None else f"{nodo.count:>6}"
        hijos = " ▸" if nodo.has_children else ""
        lineas.append(f"  {marca} {sangria}{nodo.path:<44}{cuenta}  {nodo.title}{hijos}")

    if nodos:
        # El vigía solo mira las tiendas sin declarar, así que decirlo aquí evita la lectura de
        # que estos huecos suenen solos el jueves: en las declaradas, este comando ES la vigilancia.
        aviso = (
            " (sin vigilancia semanal: estos huecos no los va a cantar nadie)"
            if slug in COBERTURA_SIN_VIGILAR
            else ""
        )
        print(f"{slug} · árbol de {root}: {len(nodos)} categorías, {huecos} sin cubrir{aviso}")
        for linea in lineas:
            print(linea)
        print(
            "  ✓ = ya en CATEGORIES · ↳ = cuelga de una que ingerimos · "
            "× = fuera a propósito (COBERTURA_DECLARADA) · · = hueco: el vigía avisa · "
            "▸ = tiene hijas"
        )
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
    # El reparto del listado, que es la forma barata de re-medir lo que decide `ambito_cruzado()`
    # sin base de datos: el género ya viene resuelto en cada entrada (#139).
    print(f"género en el listado: {_reparto(Counter(e.gender or 'sin-marcar' for e in entries))}")
    return 0


def _configura_logging(nivel: str) -> None:
    """Enciende el logging del scraper. Sin esto no se ve NADA por debajo de WARNING.

    No es cosmético y es la mitad de #146: sin handler, el *last resort* de Python solo emite
    WARNING y por encima, así que los `logger.info()` que mango, hm y springfield ya tenían escritos
    llevaban desde siempre sin salir por ningún lado — y un `logging.info()` nuevo tampoco habría
    salido. De paso los `logger.warning()` de las siete tiendas pasan a llevar hora y origen en vez
    de aparecer pelados por stderr, que en un log de cinco horas es la diferencia entre poder situar
    un aviso y no.

    A stdout a propósito, con el resto de la salida del job: `kubectl logs` los mezcla igual, y así
    el orden entre el progreso y el resumen final se conserva.

    **Las librerías se quedan en WARNING**, y eso no es tiquismiquis: `httpx` emite un INFO por
    petición, así que encender el INFO global pone una línea por ficha —2219 en Zara, 1224 en la
    fría de Hipercor— y ahoga exactamente la señal que #146 existe para dar. Medido en la pasada de
    verificación contra Cacles. Nuestro progreso es una línea cada cinco minutos a propósito; si
    algún día hace falta el detalle por petición, `SCRAPER_LOG_LEVEL=DEBUG` lo devuelve.
    """
    nivel_num = getattr(logging, nivel.upper(), logging.INFO)
    logging.basicConfig(
        level=nivel_num,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    if nivel_num > logging.DEBUG:
        for ruidosa in ("httpx", "httpcore", "hpack", "asyncio"):
            logging.getLogger(ruidosa).setLevel(logging.WARNING)


def _mensaje_bloqueo(config: Config) -> str:
    """Explica un `lock_timeout` agotado y señala a quien retiene las filas (#169).

    El objetivo es que el mensaje diga lo que costó 13 minutos de pod averiguar la primera vez: que
    esto NO es lentitud. Sin la lista de sesiones el error seguiría siendo un misterio, así que se
    pide aquí — pero si pedirla falla, el mensaje sale igual: nunca puede tapar al error original.
    """
    lineas = [
        f"✖ pasada abortada: no se pudo tomar un lock en {config.lock_timeout:g}s "
        "(SCRAPER_LOCK_TIMEOUT).",
        "  No es lentitud: alguien sostiene las filas que esta pasada necesita.",
    ]
    try:
        abiertas = db.transacciones_abiertas(config)
    except Exception as exc:  # pragma: no cover - el diagnóstico es de apoyo
        lineas.append(f"  (no se ha podido consultar pg_stat_activity: {exc})")
        return "\n".join(lineas)
    if abiertas:
        cuantas = (
            "1 transacción abierta"
            if len(abiertas) == 1
            else f"{len(abiertas)} transacciones abiertas"
        )
        lineas.append(f"  Hay {cuantas} sobre esta base:")
        lineas.extend(f"    {s}" for s in abiertas)
        lineas.append(
            "  Una `idle in transaction` con horas encima suele ser una pasada anterior muerta "
            "cuyo backend Postgres aún no ha soltado."
        )
    else:
        lineas.append(
            "  Ya no queda ninguna transacción abierta: la culpable ha soltado entre el fallo y "
            "esta consulta. Reintentar debería bastar."
        )
    return "\n".join(lineas)


def _mensaje_lock_migraciones(config: Config) -> str:
    """Explica que la espera agotada fue la del lock de migraciones, no la de las filas (#298).

    Tiene mensaje propio y no una rama del de #169 porque las dos esperas son distintas cosas con
    variables distintas: allí quien estorba es una transacción huérfana sobre las filas, aquí es el
    otro migrador —el initContainer del web o el CronJob de otra tienda— aplicando el esquema.
    Reutilizar aquel texto nombraría la variable equivocada y mandaría a mirar donde no es.
    """
    lineas = [
        f"✖ pasada abortada: no se pudo tomar el lock de migraciones en "
        f"{config.migration_lock_wait:g}s (SCRAPER_MIGRATION_LOCK_WAIT).",
        "  Otro migrador (el initContainer del web, o el CronJob de otra tienda) lo tiene tomado.",
    ]
    try:
        retenedores = db.retenedores_del_lock(config, LOCK_MIGRACIONES)
    except Exception as exc:  # pragma: no cover - el diagnóstico es de apoyo
        lineas.append(f"  (no se ha podido consultar pg_locks: {exc})")
        return "\n".join(lineas)
    if retenedores:
        lineas.append("  Lo retiene:")
        lineas.extend(f"    {r}" for r in retenedores)
        lineas.append(
            "  Si sigue ahí y no está aplicando nada, es un migrador muerto cuyo backend Postgres "
            "aún no ha soltado: comprobar que el pod ya no existe antes de terminarlo."
        )
    else:
        lineas.append(
            "  Ya no lo retiene nadie: ha soltado entre el fallo y esta consulta. "
            "Reintentar debería bastar."
        )
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    config = Config.from_env()
    _configura_logging(config.log_level)

    if args.tree:
        return _tree(config, args.retailer, args.tree)

    if args.check_categories:
        return _check_categories(config, args.retailer)

    if args.dry_run:
        return _dry_run(config, args.retailer)

    # La bandera es para el Job de un solo uso que se lanza a mano; la variable de entorno, para un
    # CronJob que quiera nacer con la reobservación puesta. Cualquiera de las dos la activa (#143).
    refresh_all = args.refresh_all or config.detail_refresh_all

    store = get_store(args.retailer, config)
    with db.connect(config) as conn:
        try:
            if args.migrate:
                # Captura propia y más cerrada que la de abajo: las dos son `LockNotAvailable`,
                # pero solo aquí se sabe que la espera agotada fue la del lock de migraciones.
                try:
                    applied = apply_migrations(conn, lock_wait=config.migration_lock_wait)
                except psycopg.errors.LockNotAvailable:
                    print(_mensaje_lock_migraciones(config), file=sys.stderr)
                    return 1
                if applied:
                    print(f"migraciones aplicadas: {', '.join(applied)}")
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
                detail_refresh_all=refresh_all,
                scan_max_dead_ratio=config.scan_max_dead_ratio,
                progress_every_seconds=config.progress_every_seconds,
            )
        except CatalogScanAborted as exc:
            # Un traceback aquí no aporta nada: el mensaje ya dice qué pasó y qué mirar.
            print(f"✖ pasada abortada: {exc}", file=sys.stderr)
            return 1
        except psycopg.errors.LockNotAvailable:
            # Tampoco aquí: el traceback señalaría el INSERT que se comió la espera, que es el
            # síntoma. Lo que hace falta saber es quién retiene las filas (#169).
            print(_mensaje_bloqueo(config), file=sys.stderr)
            return 1

    print(
        f"run #{result.scrape_run_id} OK — "
        f"{result.products_in_catalog} en catálogo "
        f"({result.details_fetched} con detalle, {result.products_unchanged} sin cambios), "
        f"{result.variants_seen} variantes, {result.prices_recorded} precios; "
        f"bajas: {result.products_delisted} productos / {result.variants_delisted} variantes"
    )
    if result.barefoot_counts:
        print(f"calzado por marca barefoot: {_reparto(result.barefoot_counts)}")
    if result.tag_counts:
        print(f"ejes transversales: {_reparto(result.tag_counts)}")
    if result.gender_counts:
        print(f"género en el listado: {_reparto(result.gender_counts)}")
    if result.gender_frozen:
        print(
            f"⚠ {result.gender_frozen} productos conservan su género `unisex` en vez del que dice "
            "el listado: se ha caído la rama de género contraria (mira «ambitos sin bajas» de "
            "arriba), así que el listado solo los ha visto en una de las dos y no puede saber que "
            "se cruzan. No es un fallo del parseo; se resuelve solo cuando la hoja vuelva."
        )
    if result.gender_stale:
        print(
            f"⚠ {result.gender_stale} productos guardan un género distinto del que dice el "
            "listado: solo se reescribe al pedir el detalle, así que a estos no se les ha pedido. "
            "Dos causas posibles, y conviene no confundirlas: una pasada anterior a algún arreglo "
            "de género, o una hoja caída en ESTA pasada, que hace que el listado emita con el "
            "género de la rama superviviente lo que en realidad es `unisex` (#172). Si hay hojas "
            "caídas arriba, sospecha de lo segundo. Los del primer caso los corrige una pasada con "
            "--refresh-all, el refresco forzado (SCRAPER_DETAIL_MAX_AGE_DAYS) o un cambio de "
            "huella."
        )
    if result.details_refreshed:
        motivo = (
            f"--refresh-all: sin mirar la edad, tope {config.detail_refresh_max}"
            if refresh_all
            else f"con el detalle rancio, > {config.detail_max_age_days} días"
        )
        print(
            f"refresco forzado: {result.details_refreshed} productos vueltos a observar ({motivo})"
        )
    if result.products_missing or result.variants_missing:
        print(
            f"ausentes pendientes de confirmar (histéresis, umbral {config.delist_min_misses}): "
            f"{result.products_missing} productos / {result.variants_missing} variantes"
        )
    if result.probes_sent or result.probes_over_cap or result.probes_unresolved:
        # `over_cap` y `unresolved` se dicen por separado (#261): el primero es la cola normal de
        # una tienda con muchos candidatos, el segundo es la tienda sin contestar. Solo el segundo
        # suma en `errors`, así que leerlos juntos era lo que hacía ilegible el número.
        # `unbuyable` va DENTRO del paréntesis y no fuera (#197) porque es un veredicto, no una
        # cola: sin él la suma no cuadra con `sondeos` —en Lefties serían 33 de 58 desaparecidos
        # del desglose— y un total que no cuadra es justo lo que manda a buscar al sitio
        # equivocado.
        print(
            f"confirmación activa: {result.probes_sent} sondeos "
            f"({result.probes_alive} siguen a la venta, {result.probes_dead} retirados, "
            f"{result.probes_unbuyable} agotados sin baja, "
            f"{result.probes_unresolved} sin veredicto) · "
            f"{result.probes_over_cap} fuera del tope de {config.delist_probe_max}: "
            f"entran los primeros en la siguiente pasada"
        )
    if result.skipped_scopes:
        print(
            f"⚠ {result.skipped_scopes}/{result.scanned_scopes} ámbitos con caída sospechosa"
            f"{_cuales(result.skipped_scope_names)}: "
            f"bajas omitidas (posible fallo de scraping). Revisa el listado de la tienda."
        )
    if result.remapped_scopes:
        # Sin ⚠ a propósito: no es un fallo, es esta pasada diciendo que ha visto un cambio de
        # clasificación y que por eso NO ha marcado como sospechoso lo que antes marcaba (#174).
        print(
            f"{result.remapped_scopes} ámbito{'' if result.remapped_scopes == 1 else 's'} "
            f"remapeado{'' if result.remapped_scopes == 1 else 's'}"
            f"{_cuales(result.remapped_scope_names)}: "
            "sus productos se han mudado de ámbito, no han desaparecido. Bajas aplicadas."
        )
    if result.leaves_failed:
        ambitos = result.unscanned_scopes
        print(
            f"⚠ {result.leaves_failed}/{result.leaves_scanned} hojas de categoría no responden"
            f"{_cuales(result.failed_leaves)}: "
            f"esas categorías han dejado de ingerirse y "
            f"{ambitos} ámbito{'' if ambitos == 1 else 's'} se "
            f"{'queda' if ambitos == 1 else 'quedan'} sin detección de bajas. "
            f"Busca sus ids nuevos en el árbol de la tienda."
        )
    if result.empty_filter_leaves:
        cuantas = len(result.empty_filter_leaves)
        print(
            f"⚠ {cuantas} hoja{'' if cuantas == 1 else 's'} "
            f"{'respondió' if cuantas == 1 else 'respondieron'} pero su filtro no casó con nada"
            f"{_cuales(result.empty_filter_leaves)}: o la tienda ha cambiado cómo rotula esos "
            f"productos o ya no le quedan. Su{'' if cuantas == 1 else 's'} ámbito"
            f"{'' if cuantas == 1 else 's'} se queda{'' if cuantas == 1 else 'n'} sin detección de "
            "bajas hasta saber cuál de las dos. Compruébalo pidiendo la hoja y mirando los nombres."
        )
    _report_residuo(result.residual_entries, result.barren_residual_leaves)
    return 0


if __name__ == "__main__":
    sys.exit(main())
