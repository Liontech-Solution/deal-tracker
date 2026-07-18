"""Conexión a Postgres (psycopg 3)."""

from __future__ import annotations

import psycopg

from .config import Config


def connect(config: Config) -> psycopg.Connection:
    """Abre una conexión. autocommit=False: la ingesta controla su transacción."""
    return psycopg.connect(config.database_url, autocommit=False)
