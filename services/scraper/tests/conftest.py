from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
import pytest

from scraper.migrate import apply_migrations

FIXTURES = Path(__file__).parent / "fixtures"
# Ruta a db/migrations resuelta desde el repo (no desde el paquete instalado), para que
# funcione tanto con install editable como no-editable (p.ej. en CI). Layout:
# services/scraper/tests/conftest.py -> parents[3] == raíz del repo.
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"

_DATA_TABLES = "product_image, price_history, variant, product, scrape_run, retailer, vigia_run"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def db_conn() -> Any:
    """Conexión a una Postgres de test. Se salta si no hay TEST_DATABASE_URL.

    Aplica migraciones (idempotente) y deja las tablas vacías antes de cada test.
    En CI lo provee el servicio `postgres` del workflow.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no definido; test de integración omitido")
    conn = psycopg.connect(url, autocommit=False)
    apply_migrations(conn, MIGRATIONS_DIR)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
