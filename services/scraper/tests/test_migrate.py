"""Tests del migrador: el lock que lo serializa con el del web (#298).

Los de concurrencia necesitan `TEST_DATABASE_URL` y se saltan sin ella, como el resto de los de
integración. El de paridad del identificador NO: es justo la comprobación que no puede depender del
gate que más se salta, porque un número distinto en cada migrador no falla — deja la carrera abierta
sin que nada lo diga.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from scraper.migrate import LOCK_MIGRACIONES, apply_migrations

MIGRATE_TS = (
    Path(__file__).resolve().parents[3] / "services" / "web" / "src" / "database" / "migrate.ts"
)


def test_el_identificador_del_lock_es_el_mismo_en_los_dos_migradores() -> None:
    """Sin esto los dos migradores se serializan cada uno consigo mismo y no entre ellos.

    Es el modo de fallo más tonto de #298 y el único que no da la cara: cada servicio funciona,
    los tests de concurrencia de cada lado pasan, y la carrera que la issue existe para cerrar
    sigue exactamente donde estaba.
    """
    texto = MIGRATE_TS.read_text(encoding="utf-8")
    m = re.search(r"export const LOCK_MIGRACIONES\s*=\s*(\d+)", texto)
    assert m is not None, f"no se encuentra LOCK_MIGRACIONES en {MIGRATE_TS}"
    assert int(m.group(1)) == LOCK_MIGRACIONES


@pytest.fixture
def url() -> str:
    valor = os.environ.get("TEST_DATABASE_URL")
    if not valor:
        pytest.skip("TEST_DATABASE_URL no definido; test de integración omitido")
    return valor


def _lock_tomado(conn: psycopg.Connection) -> bool:
    """¿Sostiene ESTA conexión el lock de migraciones?"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND objid = %s AND pid = pg_backend_pid()",
            (LOCK_MIGRACIONES,),
        )
        fila = cur.fetchone()
    return bool(fila and fila[0])


def test_dos_migradores_concurrentes_aplican_la_migracion_una_sola_vez(
    url: str, tmp_path: Path
) -> None:
    """La carrera del cuerpo de #298: los dos leen las pendientes antes del bucle.

    Sin el lock, los dos ven `9999_...` pendiente y los dos la aplican: el perdedor muere con un
    `CREATE TABLE` duplicado o con la clave primaria de `schema_migrations`. Con él, uno espera y
    al leer ya la ve aplicada.
    """
    nombre = "9999_test_concurrencia_298.sql"
    tabla = "t_298_concurrencia"
    (tmp_path / nombre).write_text(
        # El `pg_sleep` ensancha la ventana: sin él la carrera existe igual, pero pasar el test
        # dependería del azar del planificador de hilos.
        f"CREATE TABLE {tabla} (id int); SELECT pg_sleep(0.5);",
        encoding="utf-8",
    )
    barrera = threading.Barrier(2)

    def migrar() -> list[str]:
        with psycopg.connect(url, autocommit=False) as conn:
            barrera.wait(timeout=10)
            return apply_migrations(conn, tmp_path, lock_wait=30)

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            futuros = [ex.submit(migrar), ex.submit(migrar)]
            a, b = (f.result(timeout=60) for f in futuros)

        # Ninguno falla, y la aplicó exactamente uno: las listas son disjuntas y su unión es la
        # pendiente. Es la aserción que distingue "no ha petado" de "no ha corrido dos veces".
        assert sorted(a + b) == [nombre]
        assert not (set(a) & set(b))

        with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = %s", (nombre,))
            assert cur.fetchone()[0] == 1  # type: ignore[index]
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (tabla,))
            assert cur.fetchone()[0] is True  # type: ignore[index]
    finally:
        with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {tabla}")
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (nombre,))


def test_suelta_el_lock_al_terminar(url: str, tmp_path: Path) -> None:
    """`run.py` sigue usando esta conexión para la pasada entera (30 min en frío).

    Un lock de sesión sin soltar dejaría al initContainer del web esperando todo ese rato, que es
    peor que el problema que #298 arregla.
    """
    with psycopg.connect(url, autocommit=False) as conn:
        apply_migrations(conn, tmp_path, lock_wait=30)
        assert not _lock_tomado(conn)


def test_suelta_el_lock_aunque_la_migracion_falle(url: str, tmp_path: Path) -> None:
    """El caso feo: la transacción queda abortada y `pg_advisory_unlock` se ejecuta sobre ella.

    Sin el `rollback` previo daría `InFailedSqlTransaction` desde el `finally`, que además taparía
    el error de verdad — el de la migración— con uno que no dice nada.
    """
    (tmp_path / "9999_rota_298.sql").write_text("ESTO NO ES SQL;", encoding="utf-8")
    with psycopg.connect(url, autocommit=False) as conn:
        with pytest.raises(psycopg.errors.SyntaxError):
            apply_migrations(conn, tmp_path, lock_wait=30)
        assert not _lock_tomado(conn)


def test_no_pisa_el_lock_timeout_de_la_conexion(url: str, tmp_path: Path) -> None:
    """La garantía de #169: `db.connect()` pone `lock_timeout` para TODA la pasada.

    La espera del lock de migraciones es otra y mucho más larga, así que se pone con `SET LOCAL`
    dentro de una transacción que se cierra. Si alguien lo cambiara por un `SET` a secas, la pasada
    entera correría con 300 s de espera por fila y el fallo mudo de #169 volvería sin ruido.
    """
    with psycopg.connect(url, autocommit=False, options="-c lock_timeout=7000") as conn:
        apply_migrations(conn, tmp_path, lock_wait=300)
        with conn.cursor() as cur:
            cur.execute("SHOW lock_timeout")
            assert cur.fetchone()[0] == "7s"  # type: ignore[index]
