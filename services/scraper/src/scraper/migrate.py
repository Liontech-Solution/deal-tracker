"""Migrador de esquema minimalista y sin dependencias externas.

Aplica en orden los ficheros `NNNN_*.sql` de `db/migrations` (SQL neutro,
contrato compartido con el servicio web) y registra los aplicados en
`schema_migrations`. Idempotente: reejecutarlo no reaplica nada.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg


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


def _pending(conn: psycopg.Connection, files: list[Path]) -> list[Path]:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_TABLE)
        cur.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
    return [f for f in files if f.name not in applied]


def apply_migrations(
    conn: psycopg.Connection, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR
) -> list[str]:
    """Aplica las migraciones pendientes. Devuelve las versiones aplicadas ahora."""
    files = sorted(migrations_dir.glob("*.sql"))
    pending = _pending(conn, files)
    for f in pending:
        with conn.cursor() as cur:
            cur.execute(f.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.name,))
        conn.commit()
    return [f.name for f in pending]
