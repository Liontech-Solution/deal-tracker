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
            progress_every_seconds=float(env.get("SCRAPER_PROGRESS_EVERY_SECONDS", "300")),
            log_level=env.get("SCRAPER_LOG_LEVEL", "INFO"),
            lock_timeout=float(env.get("SCRAPER_LOCK_TIMEOUT", "30")),
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
