"""Migrador de esquema minimalista y sin dependencias externas.

Aplica en orden los ficheros `NNNN_*.sql` de `db/migrations` (SQL neutro,
contrato compartido con el servicio web) y registra los aplicados en
`schema_migrations`. Idempotente: reejecutarlo no reaplica nada.

Serializado con un advisory lock que comparte con el migrador del web (#298): los dos leen el
conjunto de aplicadas ANTES del bucle, así que sin él dos procesos que arranquen a la vez ven la
misma lista de pendientes e intentan aplicar el mismo fichero.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.pq import TransactionStatus

# Identificador del advisory lock que serializa las migraciones. Tiene que ser el MISMO número en
# `services/web/src/database/migrate.ts` o los dos migradores no se ven; lo ata el test de paridad
# de `tests/test_migrate.py`. El valor es `0x64746d67`, que en ASCII es `dtmg` (deal-tracker
# migrations): arbitrario, pero reconocible en un `pg_locks` y estable.
#
# Los advisory locks son POR BASE DE DATOS, no por cluster (verificado el 13/08/2026: la misma
# clave se toma sin esperar desde otra base de la misma instancia, y `pg_locks.database` trae el
# OID). Importa porque `deal_tracker`, `deal_tracker_qa` y `deal_tracker_prod` comparten la misma
# CNPG con cuatro proyectos ajenos: esto no los alcanza, y QA no bloquea a prod.
LOCK_MIGRACIONES = 1685351783

# Segundos que se espera el lock. Generoso a propósito y distinto de `SCRAPER_LOCK_TIMEOUT` (30 s,
# #169): quien lo retiene está aplicando migraciones, y algunas obligan a un `REINDEX` (0014, 0029)
# que sobre un catálogo entero no cabe en 30 s. Lo que esto acota no es contención, es que el otro
# migrador haya muerto dejando el lock colgado — y para eso 5 minutos ya es un tope.
DEFAULT_LOCK_WAIT = 300.0


def _default_migrations_dir() -> Path:
    """Ubicación de db/migrations. Configurable por entorno (útil en el contenedor)."""
    override = os.environ.get("SCRAPER_MIGRATIONS_DIR")
    if override:
        return Path(override)
    # services/scraper/src/scraper/migrate.py -> parents[4] == raíz del repo
    return Path(__file__).resolve().parents[4] / "db" / "migrations"


DEFAULT_MIGRATIONS_DIR = _default_migrations_dir()

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _tomar_lock(conn: psycopg.Connection, espera: float) -> None:
    """Toma el lock de migraciones, esperando como mucho `espera` segundos.

    El `lock_timeout` va con `set_config(..., is_local=true)` —o sea `SET LOCAL`— dentro de una
    transacción que se cierra aquí mismo. Así el valor revierte solo en el `commit` y es imposible
    dejar pisado el `SCRAPER_LOCK_TIMEOUT` que `db.connect()` puso para el resto de la pasada, que
    es la garantía de #169. El lock es de SESIÓN, así que sobrevive a ese `commit` (comprobado).

    Si la espera se agota, Postgres cancela con `55P03` y psycopg lo levanta como
    `LockNotAvailable`, que es justo lo que `run.py` ya captura.
    """
    with conn.cursor() as cur:
        if espera > 0:
            cur.execute("SELECT set_config('lock_timeout', %s, true)", (f"{int(espera * 1000)}ms",))
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_MIGRACIONES,))
    conn.commit()


def _soltar_lock(conn: psycopg.Connection) -> None:
    """Suelta el lock. Va en un `finally`, así que puede tocarle una conexión en mal estado.

    Dos motivos para el `rollback` previo: si una migración falló, la transacción está abortada y
    cualquier consulta sobre ella daría `InFailedSqlTransaction`, tapando el error de verdad.
    """
    if conn.info.transaction_status in (TransactionStatus.INERROR, TransactionStatus.UNKNOWN):
        conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_MIGRACIONES,))
    conn.commit()


def _pending(conn: psycopg.Connection, files: list[Path]) -> list[Path]:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_TABLE)
        cur.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
    return [f for f in files if f.name not in applied]


def apply_migrations(
    conn: psycopg.Connection,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    lock_wait: float = DEFAULT_LOCK_WAIT,
) -> list[str]:
    """Aplica las migraciones pendientes. Devuelve las versiones aplicadas ahora.

    El lock se toma ANTES de `_pending()`, no alrededor del bucle: el fallo está en leer el
    conjunto de aplicadas y decidir con él, así que envolver solo la escritura no arregla nada.

    Y se suelta aquí dentro, nunca en el que llama: `run.py` sigue usando esta misma conexión para
    la pasada entera, y un lock de sesión sin soltar dejaría al initContainer del web esperando los
    30 minutos que dura una pasada en frío.
    """
    files = sorted(migrations_dir.glob("*.sql"))
    _tomar_lock(conn, lock_wait)
    try:
        pending = _pending(conn, files)
        for f in pending:
            with conn.cursor() as cur:
                cur.execute(f.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.name,))
            conn.commit()
        return [f.name for f in pending]
    finally:
        _soltar_lock(conn)
