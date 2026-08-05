"""Una pasada bloqueada por un lock falla rápido y nombrando al culpable (#169).

El escenario que reproduce: una pasada anterior muere a mitad y su backend de Postgres se queda
`idle in transaction` sosteniendo las filas que ya había tocado; la siguiente se bloquea en su
primer `INSERT` sobre `retailer`. Sin `lock_timeout` eso es una espera indistinguible de una pasada
lenta, hasta agotar el `activeDeadlineSeconds` del Job.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from scraper import db
from scraper.config import Config
from scraper.ingest import ingest

from .test_ingest import FakeStore, _product, _variant

# Cuarto de segundo: suficiente para separarse del ruido de una transacción vacía y lo bastante
# corto para que el test tarde décimas. El del cluster son 30 s.
_TIMEOUT = 0.25


def _config(url: str, lock_timeout: float) -> Config:
    return Config(database_url=url, lock_timeout=lock_timeout)


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no definido; test de integración omitido")
    return url


def _tienda() -> FakeStore:
    producto = _product("A", "Botas", [_variant("A1", "10.00")])
    return FakeStore([producto], {"A": "sig-A"})


def test_el_lock_timeout_llega_a_la_sesion(db_conn: Any) -> None:
    url = _test_url()
    with db.connect(_config(url, _TIMEOUT)) as conn, conn.cursor() as cur:
        cur.execute("SHOW lock_timeout")
        row = cur.fetchone()
        assert row is not None and row[0] == "250ms"


def test_con_cero_no_se_toca_la_espera(db_conn: Any) -> None:
    """`0` devuelve el comportamiento previo: esperar indefinidamente."""
    url = _test_url()
    with db.connect(_config(url, 0)) as conn, conn.cursor() as cur:
        cur.execute("SHOW lock_timeout")
        row = cur.fetchone()
        assert row is not None and row[0] == "0"


def test_una_pasada_bloqueada_falla_rapido_en_vez_de_esperar(db_conn: Any) -> None:
    """La regresión de #169: con la fila de `retailer` retenida, la pasada NO se cuelga."""
    url = _test_url()
    tienda = _tienda()
    # La pasada choca en su `ON CONFLICT DO UPDATE` sobre `retailer`, así que la fila tiene que
    # existir antes: es el caso real (la tienda ya estaba registrada de pasadas anteriores).
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retailer (slug, name, base_url) VALUES (%s, %s, %s)",
            (tienda.slug, tienda.name, tienda.base_url),
        )
    db_conn.commit()

    bloqueador = psycopg.connect(url, autocommit=False)
    try:
        with bloqueador.cursor() as cur:  # retiene la fila y se queda `idle in transaction`
            cur.execute("UPDATE retailer SET name = name WHERE slug = %s", (tienda.slug,))

        with db.connect(_config(url, _TIMEOUT)) as conn:
            arranque = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable):
                ingest(conn, tienda)
            tardanza = time.monotonic() - arranque
        # ~2×: la pasada agota su espera y `_record_failed_run` vuelve a chocar con el mismo lock.
        assert tardanza < _TIMEOUT * 4
    finally:
        bloqueador.rollback()
        bloqueador.close()


def test_el_diagnostico_senala_a_la_sesion_que_retiene(db_conn: Any) -> None:
    """Sin esto el error sería un misterio: lo accionable es el `pid` y su tiempo en transacción."""
    url = _test_url()
    bloqueador = psycopg.connect(url, autocommit=False)
    try:
        with bloqueador.cursor() as cur:
            cur.execute("INSERT INTO retailer (slug, name, base_url) VALUES ('x', 'X', 'u')")

        abiertas = db.transacciones_abiertas(_config(url, _TIMEOUT))

        pids = [s.pid for s in abiertas]
        assert bloqueador.info.backend_pid in pids
        culpable = next(s for s in abiertas if s.pid == bloqueador.info.backend_pid)
        assert culpable.state == "idle in transaction"
        assert "INSERT INTO retailer" in culpable.query
    finally:
        bloqueador.rollback()
        bloqueador.close()


def test_el_mensaje_dice_que_no_es_lentitud(db_conn: Any) -> None:
    """El mensaje es la mitad del arreglo: 13 min de pod costó averiguar esto la primera vez."""
    from scraper.run import _mensaje_bloqueo

    url = _test_url()
    bloqueador = psycopg.connect(url, autocommit=False)
    try:
        with bloqueador.cursor() as cur:
            cur.execute("INSERT INTO retailer (slug, name, base_url) VALUES ('y', 'Y', 'u')")

        mensaje = _mensaje_bloqueo(_config(url, _TIMEOUT))

        assert "no es lentitud" in mensaje.lower()
        assert "SCRAPER_LOCK_TIMEOUT" in mensaje
        assert f"pid {bloqueador.info.backend_pid}" in mensaje
    finally:
        bloqueador.rollback()
        bloqueador.close()


def test_una_pasada_sin_bloqueo_corre_normal(db_conn: Any) -> None:
    """La otra mitad: el `lock_timeout` no puede estorbar a una pasada legítima."""
    url = _test_url()
    tienda = _tienda()
    with db.connect(_config(url, _TIMEOUT)) as conn:
        result = ingest(conn, tienda)
    assert result.products_in_catalog == 1
    assert result.variants_seen == 1
    with db_conn.cursor() as cur:
        cur.execute("SELECT price FROM price_history")
        row = cur.fetchone()
    assert row is not None and row[0] == Decimal("10.00")
