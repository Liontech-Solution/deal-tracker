"""Configuración del servicio, cargada desde variables de entorno (y un .env opcional)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@dataclass(frozen=True)
class Config:
    """Parámetros de ejecución. Sin secretos hardcodeados: todo viene del entorno."""

    database_url: str
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 20.0
    request_delay: float = 0.5  # pausa base entre peticiones (se le aplica jitter)
    request_retries: int = 3  # reintentos ante 429/5xx/errores de red
    retry_backoff: float = 1.0  # segundos base del backoff exponencial
    # Red de seguridad de bajas: si un ámbito con al menos `delist_min_baseline`
    # productos activos ve caer lo observado por debajo de `delist_drop_ratio`,
    # se omiten sus bajas (posible fallo de scraping, no retirada real).
    delist_min_baseline: int = 5
    delist_drop_ratio: float = 0.5
    # Histéresis: pasadas consecutivas sin ver un producto/variante antes de darlo de
    # baja. Con 1 se descataloga a la primera ausencia (comportamiento sin histéresis).
    delist_min_misses: int = 2
    # Confirmación activa: antes de descatalogar se pregunta a la tienda por el producto
    # (solo tiendas que lo soportan). `delist_probe=False` vuelve a la baja por histéresis;
    # `delist_probe_max` acota el gasto de peticiones extra por pasada.
    delist_probe: bool = True
    delist_probe_max: int = 50
    # Ventana de silencio del sondeo (#412): a un candidato que ya contestó hace menos de estos
    # días no se le vuelve a preguntar, porque el veredicto se tiraba y el presupuesto se gastaba
    # en reconfirmar lo ya sabido (200 sondeos / 200 vivos / 0 bajas / 504 sin sondear, medido en
    # QA el 16/08/2026). Queda BLOQUEADO frente a las bajas igual que los que no caben en el tope:
    # ahorrarse la pregunta no es darlo por retirado. Con 0 se desactiva y se sondea como antes.
    #
    # El valor tiene un techo que no es de gusto: debe ser bastante MENOR que el tiempo en que una
    # prenda retirada de verdad dejaría de responder, o se retrasan bajas legítimas. Con
    # `delist_min_misses=2` y una pasada diaria, 7 días son ~5 pasadas de margen.
    delist_probe_cooldown_days: int = 7
    # Refresco periódico forzado del detalle: una prenda de precio estable nunca cambia de huella
    # de listado, así que sin esto no se volvería a observar jamás (y sin re-observaciones no hay
    # histórico con el que corroborar un descuento, ni stock por talla al día). Se pide el detalle
    # de lo más rancio, acotado por pasada. `detail_max_age_days=0` desactiva el refresco.
    detail_max_age_days: int = 7
    detail_refresh_max: int = 100
    # Reobservación bajo demanda (#143): ignora la EDAD y pide el detalle de todo lo sin cambios.
    # Existe porque un arreglo que cambia la forma del dato (el género de #98, y valdría igual para
    # la categoría o `barefoot`) solo se propaga al pedir la ficha, y con el umbral en días un
    # catálogo ingerido hace unas horas no se podía volver a mirar: había que esperar al reloj.
    # NO ignora `detail_refresh_max`, que sigue siendo el presupuesto de la pasada — sin ese tope,
    # esto contra Hipercor son 1224 fichas a ~10 s, o sea tres horas y media.
    detail_refresh_all: bool = False
    # Hojas de categoría caídas (404): una suelta se salta y su ámbito queda fuera de las bajas,
    # pero por encima de esta proporción la pasada aborta sin escribir — tantas hojas muertas no
    # son categorías retiradas, son un bloqueo o un cambio de API.
    scan_max_dead_ratio: float = 0.34
    # Navegador headless (solo tiendas que lo requieren, p.ej. Sfera tras Akamai).
    # `browser_headless=False` abre ventana real (dev con display); en el cluster/CI
    # se ejecuta headless. `browser_channel` fuerza un canal instalado (p.ej. "chrome").
    browser_headless: bool = True
    browser_nav_timeout: float = 45.0  # segundos para goto() y peticiones del navegador
    browser_channel: str | None = None
    # Espera máxima a que el JS de la página escriba lo que el servidor no manda en el documento
    # inicial (en Hipercor, el `ProductGroup` con las tallas). Solo la usan las tiendas que leen
    # la página en vez de una API; agotarla no es un error, es un detalle que no llega.
    browser_hydrate_timeout: float = 10.0
    # Vigía: a partir de qué factor sobre su propia línea base se avisa de que una tienda nos está
    # dejando entrar más despacio (#111). Nunca es accionable ni abre issue por sí solo, así que
    # quedarse corto cuesta poco; ×3 deja pasar el ruido de un cluster compartido y captura de
    # sobra el caso medido (×11,8 en Hipercor). `vigia_base_muestras` es cuántas ejecuciones
    # previas entran en la mediana que hace de línea base; con menos de 2 no se compara.
    vigia_factor_aviso: float = 3.0
    vigia_base_muestras: int = 4
    # Plazo que el vigía se impone a sí mismo, en segundos. Existe porque el 07/08/2026 el barrido
    # se comió los 45 min del `activeDeadlineSeconds` y el controlador mató el job: en
    # `DeadlineExceeded` Kubernetes borra el pod, así que ni `kubectl logs` recupera después en qué
    # tienda se atascó (#258). Un vigilante que puede morir sin decir por qué es exactamente el
    # problema que el vigía existe para evitar.
    #
    # Con esto deja de barrer ANTES de que lo maten, nombra lo que se quedó sin mirar y sale por el
    # camino normal —informe, resumen e issue—. Se pone POR DEBAJO del `activeDeadlineSeconds` del
    # CronJob, con margen para el cierre y el aviso. `0` lo desactiva, que es lo que quieres en
    # local: ahí nadie te va a matar el proceso.
    vigia_plazo_segundos: float = 0.0
    # Cada cuántos segundos la pasada dice por dónde va (#146). Por TIEMPO y no por número de fichas
    # para que el volumen del log no dependa del tamaño del catálogo — y de paso sale gratis que una
    # pasada caliente no se ensucie: Zara en 1m35s no llega al primer aviso. `0` lo desactiva.
    # `log_level` existe porque encender el handler enciende también los `logger.info()` que ya
    # tenían escritos mango, hm y springfield: si alguna vez molestan, WARNING los calla sin tocar
    # código.
    progress_every_seconds: float = 300.0
    log_level: str = "INFO"
    # Segundos que la pasada espera un lock antes de rendirse (#169). Sin esto, una transacción
    # huérfana —el backend de una pasada anterior muerta, que Postgres tarda en enterarse de que
    # ya no tiene cliente— bloquea a la siguiente en su primer INSERT y la deja esperando hasta
    # agotar su `activeDeadlineSeconds`: cinco horas de pod ocupado sin hacer nada y sin decirlo,
    # con el mismo aspecto en el log que una pasada lenta. 30 s es holgado a propósito: la
    # contención legítima entre pasadas es casi nula (cada tienda toca las filas de su propio
    # `retailer_id`), así que un lock que no se consigue en 30 s no es tráfico, es una huérfana.
    # `0` lo desactiva y devuelve la espera infinita.
    lock_timeout: float = 30.0
    # Segundos que se espera el advisory lock que serializa las migraciones (#298). Es OTRA espera
    # y por eso es otra variable: la de arriba acota la contención por filas, y 30 s bastan porque
    # ahí una espera larga ya es una huérfana. Aquí quien retiene el lock está aplicando
    # migraciones legítimamente, y algunas obligan a un `REINDEX` que no cabe en 30 s. `0` lo
    # desactiva y espera lo que haga falta.
    migration_lock_wait: float = 300.0
    # Segundos que una transacción nuestra puede estar OCIOSA antes de que Postgres mate la sesión
    # (#210). Es la otra mitad de #169: el `lock_timeout` de arriba acota a la VÍCTIMA —la pasada
    # que choca con una huérfana muere en 30 s nombrando al culpable— pero la huérfana sigue ahí
    # para la siguiente. Esto acota a la CULPABLE, y lo hace el servidor: la sesión sigue viva en
    # Postgres cuando el pod ya está muerto, así que es el único de los dos que la limpia.
    #
    # Ojo a la asimetría, que es lo que hace peligroso este número: `lock_timeout` acota cada
    # ESPERA por un lock y jamás mata una pasada legítima; éste acota el tiempo ocioso DENTRO de la
    # transacción, y la fase 1 no ejecuta ni una sentencia mientras lista el catálogo. O sea que
    # puesto por debajo del listado más largo mata pasadas buenas por construcción.
    #
    # El suelo, medido el 14/08/2026 sobre la vuelta completa de QA del 10/08 (las nueve tiendas,
    # dos o tres muestras cada una): el peor listado es el de Hipercor con 3m —`duracion()` redondea
    # al minuto, o sea 2m30s-3m29s—, y le siguen hm, sfera, lefties y mango entre 1 y 2 min.
    # Springfield lista en 3 s. Los otros dos huecos candidatos quedaron descartados con dato: la
    # fase 2 escribe por ficha y va a 1,0-2,5 s/ficha, y el lote entero de `probe_alive()` —que sí
    # corre sin SQL entre sondeo y sondeo— cabe en los 2m13s que Sfera tarda en TODO lo que sigue
    # al listado, sus 50 sondeos incluidos.
    #
    # Una hora es deliberadamente generosa sobre esos 3m, y el motivo es que el error no es
    # simétrico: quedarse corto cuesta una pasada buena —en QA, una semana sin datos de esa tienda—
    # y pasarse cuesta solo que la huérfana sobreviva un rato más, con el `lock_timeout` ya
    # protegiendo a quien se la encuentre. Cubre además los ~30 min que el ADR guarda del listado de
    # Hipercor antes de #160, por si algo los reintrodujera. **Este valor caduca solo**: la fase 1
    # de una tienda de navegador crece con su catálogo. `0` lo desactiva.
    idle_tx_timeout: float = 3600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        database_url = env.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError(
                "Falta DATABASE_URL (p.ej. postgresql://user:pass@host:5432/deal_tracker)"
            )
        return cls(
            database_url=database_url,
            user_agent=env.get("SCRAPER_USER_AGENT", DEFAULT_USER_AGENT),
            request_timeout=float(env.get("SCRAPER_REQUEST_TIMEOUT", "20")),
            request_delay=float(env.get("SCRAPER_REQUEST_DELAY", "0.5")),
            request_retries=int(env.get("SCRAPER_REQUEST_RETRIES", "3")),
            retry_backoff=float(env.get("SCRAPER_RETRY_BACKOFF", "1.0")),
            delist_min_baseline=int(env.get("SCRAPER_DELIST_MIN_BASELINE", "5")),
            delist_drop_ratio=float(env.get("SCRAPER_DELIST_DROP_RATIO", "0.5")),
            delist_min_misses=int(env.get("SCRAPER_DELIST_MIN_MISSES", "2")),
            delist_probe=env.get("SCRAPER_DELIST_PROBE", "1") not in ("0", "false", "False"),
            delist_probe_max=int(env.get("SCRAPER_DELIST_PROBE_MAX", "50")),
            delist_probe_cooldown_days=int(env.get("SCRAPER_DELIST_PROBE_COOLDOWN_DAYS", "7")),
            detail_max_age_days=int(env.get("SCRAPER_DETAIL_MAX_AGE_DAYS", "7")),
            detail_refresh_max=int(env.get("SCRAPER_DETAIL_REFRESH_MAX", "100")),
            detail_refresh_all=env.get("SCRAPER_DETAIL_REFRESH_ALL", "0")
            not in ("0", "false", "False"),
            scan_max_dead_ratio=float(env.get("SCRAPER_SCAN_MAX_DEAD_RATIO", "0.34")),
            browser_headless=env.get("SCRAPER_BROWSER_HEADLESS", "1")
            not in ("0", "false", "False"),
            browser_nav_timeout=float(env.get("SCRAPER_BROWSER_NAV_TIMEOUT", "45")),
            browser_hydrate_timeout=float(env.get("SCRAPER_BROWSER_HYDRATE_TIMEOUT", "10")),
            browser_channel=env.get("SCRAPER_BROWSER_CHANNEL") or None,
            vigia_factor_aviso=float(env.get("SCRAPER_VIGIA_FACTOR_AVISO", "3.0")),
            vigia_base_muestras=int(env.get("SCRAPER_VIGIA_BASE_MUESTRAS", "4")),
            vigia_plazo_segundos=float(env.get("SCRAPER_VIGIA_PLAZO_SEGUNDOS", "0")),
            progress_every_seconds=float(env.get("SCRAPER_PROGRESS_EVERY_SECONDS", "300")),
            log_level=env.get("SCRAPER_LOG_LEVEL", "INFO"),
            lock_timeout=float(env.get("SCRAPER_LOCK_TIMEOUT", "30")),
            migration_lock_wait=float(env.get("SCRAPER_MIGRATION_LOCK_WAIT", "300")),
            idle_tx_timeout=float(env.get("SCRAPER_IDLE_TX_TIMEOUT", "3600")),
        )


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Carga un .env sencillo en os.environ (sin sobrescribir lo ya definido).

    Evita añadir una dependencia externa solo para dev local. No soporta
    comillas ni multilínea: KEY=VALUE por línea, '#' para comentarios.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
