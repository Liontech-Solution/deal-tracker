"""La transacción huérfana se muere sola en vez de quedarse para siempre (#210).

Es la otra mitad de #169, y apunta al revés que `test_lock_timeout.py`: allí se comprueba que la
pasada que se TOPA con una huérfana falla rápido y nombrando al culpable; aquí, que la huérfana
—el backend de una pasada anterior muerta, que Postgres mantiene `idle in transaction` mientras
nadie le diga lo contrario— acaba muriéndose ella.

Lo que hace que esto funcione es que el timeout viaja en la CONEXIÓN y lo aplica el servidor: el
pod puede estar muerto y la cuenta sigue corriendo. Y lo que lo hace peligroso es que la fase 1 no
ejecuta SQL mientras lista el catálogo, así que un valor por debajo del listado más largo mata
pasadas legítimas — los dos últimos tests son exactamente ese par.
"""

from __future__ import annotations

import os
import time
from typing import Any

import psycopg
import pytest

from scraper import db
from scraper.config import Config
from scraper.ingest import ingest

from .test_ingest import FakeStore, _product, _variant

# Los mismos milisegundos de siempre por el mismo motivo: separarse del ruido sin que el test tarde.
_OCIOSO = 0.25
_LOCK = 0.25


def _config(url: str, *, idle: float = _OCIOSO, lock: float = 0.0) -> Config:
    return Config(database_url=url, lock_timeout=lock, idle_tx_timeout=idle)


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no definido; test de integración omitido")
    return url


class TiendaLenta(FakeStore):
    """Una tienda cuyo LISTADO tarda, que es el hueco ocioso real de una pasada."""

    def __init__(self, espera: float) -> None:
        producto = _product("A", "Botas", [_variant("A1", "10.00")])
        super().__init__([producto], {"A": "sig-A"})
        self._espera = espera

    def list_catalog(self) -> Any:
        time.sleep(self._espera)  # sin tocar la base: es justo lo que hace la fase 1 de verdad
        return super().list_catalog()


def test_el_timeout_ocioso_llega_a_la_sesion(db_conn: Any) -> None:
    url = _test_url()
    with db.connect(_config(url)) as conn, conn.cursor() as cur:
        cur.execute("SHOW idle_in_transaction_session_timeout")
        row = cur.fetchone()
        assert row is not None and row[0] == "250ms"


def test_con_cero_no_se_toca_la_transaccion(db_conn: Any) -> None:
    """`0` devuelve el comportamiento previo: la huérfana vive para siempre."""
    url = _test_url()
    with db.connect(_config(url, idle=0)) as conn, conn.cursor() as cur:
        cur.execute("SHOW idle_in_transaction_session_timeout")
        row = cur.fetchone()
        assert row is not None and row[0] == "0"


def test_los_dos_timeouts_conviven(db_conn: Any) -> None:
    """Regresión de la forma del `options`: al pasar de un `-c` suelto a una lista, es trivial
    que uno de los dos se coma al otro y nadie lo note hasta que haga falta."""
    url = _test_url()
    with db.connect(_config(url, idle=_OCIOSO, lock=_LOCK)) as conn, conn.cursor() as cur:
        cur.execute("SHOW idle_in_transaction_session_timeout")
        ocioso = cur.fetchone()
        cur.execute("SHOW lock_timeout")
        lock = cur.fetchone()
    assert ocioso is not None and ocioso[0] == "250ms"
    assert lock is not None and lock[0] == "250ms"


def test_la_huerfana_se_muere_sola(db_conn: Any) -> None:
    """El caso entero de la issue: una transacción abierta y ociosa deja de estarlo.

    Se comprueba con la excepción exacta —`IdleInTransactionSessionTimeout`, SQLSTATE 25P03— y no
    con un `conn.closed` a secas, porque «la conexión se cayó» lo produce cualquier cosa y aquí lo
    que se afirma es QUÉ la mató.
    """
    url = _test_url()
    conn = db.connect(_config(url))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1"
            )  # abre la transacción de verdad; sin esto no está `in transaction`

        time.sleep(_OCIOSO * 4)

        with pytest.raises(psycopg.errors.IdleInTransactionSessionTimeout), conn.cursor() as cur:
            cur.execute("SELECT 1")
        assert conn.closed
    finally:
        conn.close()


def test_una_pasada_cuyo_listado_pasa_del_tope_muere(db_conn: Any) -> None:
    """La mitad incómoda, y la que justifica que el valor de producción sea holgado.

    La fase 1 no ejecuta SQL mientras lista, así que un listado más largo que el tope se lleva por
    delante una pasada perfectamente sana. Esto no es un fallo del mecanismo: es el criterio con el
    que se eligió el número, y por eso está escrito como test y no como comentario.

    Y lo que se afirma aquí NO es `IdleInTransactionSessionTimeout`, aunque sea lo que mata a la
    sesión: el `except` de `ingest()` hace `conn.rollback()` sobre la conexión ya muerta, ese
    rollback eleva `OperationalError` y **sustituye al error original**. O sea que quien mire el log
    lee «the connection is lost» y no «terminating connection due to idle-in-transaction timeout»,
    y además `_record_failed_run` no llega a ejecutarse, así que la pasada no deja fila en
    `scrape_run` — justo lo que el docstring de esa función dice que existe para evitar. Es
    anterior a #210 (le pasa a cualquier conexión que muera) y vive en `ingest.py`, que en la
    v0.5.0 es de otra sesión, así que aquí se deja MEDIDO y no arreglado. El test se escribe contra
    lo que de verdad se observa; el día que se arregle, este `raises` es lo que hay que cambiar.
    """
    url = _test_url()
    with db.connect(_config(url, idle=_OCIOSO)) as conn, pytest.raises(psycopg.OperationalError):
        ingest(conn, TiendaLenta(_OCIOSO * 4))


def test_la_misma_pasada_sobrevive_con_margen(db_conn: Any) -> None:
    """Y el contraste, que es lo que convierte el test anterior en un criterio y no en un susto."""
    url = _test_url()
    tienda = TiendaLenta(_OCIOSO * 4)
    with db.connect(_config(url, idle=_OCIOSO * 40)) as conn:
        resultado = ingest(conn, tienda)
        conn.commit()
    assert resultado.products_in_catalog == 1
