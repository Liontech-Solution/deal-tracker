"""El histórico del vigía: la serie que convierte una sorpresa en una tendencia (#111).

Dos mitades. La primera necesita Postgres (`TEST_DATABASE_URL`, si no se omite) porque lo que se
comprueba es el contrato con la tabla `0022`. La segunda no necesita nada y es la que más importa:
que un fallo del historial **no le cueste el veredicto al vigía**, que es lo único para lo que
existe y encima corre con `suspend: false`.
"""

from __future__ import annotations

from typing import Any

import psycopg

from scraper.config import Config
from scraper.vigia_historial import Historial

_INSERTAR_CON_FECHA = """
INSERT INTO vigia_run (retailer_slug, capa, ran_at, segundos, unidades)
VALUES (%s, %s, %s, %s, %s)
"""


def _sembrar(conn: psycopg.Connection, filas: list[tuple[str, str, str, float, int]]) -> None:
    """Siembra con `ran_at` explícito: los tests de «las últimas N» dependen del orden, y con el
    `now()` por defecto todas las filas caen en el mismo instante y el ORDER BY queda al azar."""
    with conn.cursor() as cur:
        cur.executemany(_INSERTAR_CON_FECHA, filas)
    conn.commit()


def test_ida_y_vuelta_la_mediana_del_ritmo(db_conn: Any) -> None:
    """Lo que se guarda son segundos y unidades; lo que se compara es el cociente."""
    historial = Historial(db_conn)
    assert historial.guardar([("cacles", "hojas", 40.0, 10), ("cacles", "hojas", 60.0, 10)]) == 2

    base = historial.linea_base("cacles", "hojas", muestras=4)
    assert base is not None
    assert base.mediana == 5.0  # mediana de 4,0 y 6,0 s/hoja
    assert sorted(base.muestras) == [4.0, 6.0]


def test_solo_entran_las_ultimas_n_ejecuciones(db_conn: Any) -> None:
    """El tope existe para que la línea base siga los cambios lentos del catálogo en vez de
    promediar el año entero."""
    _sembrar(
        db_conn,
        [
            ("zara", "hojas", "2026-07-01", 1000.0, 10),  # la vieja, fuera del tope
            ("zara", "hojas", "2026-07-08", 40.0, 10),
            ("zara", "hojas", "2026-07-15", 40.0, 10),
            ("zara", "hojas", "2026-07-22", 60.0, 10),
            ("zara", "hojas", "2026-07-29", 60.0, 10),
        ],
    )
    base = Historial(db_conn).linea_base("zara", "hojas", muestras=4)

    assert base is not None
    assert base.muestras == (6.0, 6.0, 4.0, 4.0), "las cuatro últimas, más recientes primero"
    assert base.mediana == 5.0, "la de 100 s/hoja se quedó fuera"


def test_con_una_sola_muestra_no_hay_linea_base(db_conn: Any) -> None:
    """Casilla 3 de #111: sin línea base no se compara. Una muestra no es una línea base — un
    jueves con el nodo ocupado cantaría y a la semana siguiente se desmentiría solo."""
    _sembrar(db_conn, [("sfera", "hojas", "2026-07-29", 40.0, 10)])

    assert Historial(db_conn).linea_base("sfera", "hojas", muestras=4) is None


def test_una_capa_que_murio_pronto_se_guarda_pero_no_hace_de_base(db_conn: Any) -> None:
    """La medida parcial es un dato (lo dice la migración) y a la vez una base pésima: dos hojas
    sondeadas no describen el ritmo de treinta y dos."""
    _sembrar(
        db_conn,
        [
            ("hipercor", "hojas", "2026-07-15", 128.0, 32),
            ("hipercor", "hojas", "2026-07-22", 480.0, 2),  # reventó a la segunda hoja
            ("hipercor", "hojas", "2026-07-29", 128.0, 32),
        ],
    )
    base = Historial(db_conn).linea_base("hipercor", "hojas", muestras=4)

    assert base is not None
    assert base.muestras == (4.0, 4.0), "la parcial no entra: 240 s/hoja reventaría la mediana"
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM vigia_run WHERE retailer_slug = 'hipercor'")
        assert cur.fetchone()[0] == 3, "descartarla como base no es borrarla"


def test_una_tienda_de_una_sola_hoja_tambien_tiene_linea_base(db_conn: Any) -> None:
    """Encontrado ejecutando el vigía de verdad contra Cacles, que publica **una sola hoja**.

    Con un mínimo absoluto de unidades —el primer diseño— su capa de hojas se quedaba sin línea
    base para siempre, y es justo la tienda cuyo 429 por huella TLS motivó el vigía. Por eso lo que
    descalifica a una muestra es su cobertura **relativa a la de su propia tienda**.
    """
    _sembrar(
        db_conn,
        [
            ("cacles", "hojas", "2026-07-22", 1.0, 1),
            ("cacles", "hojas", "2026-07-29", 0.6, 1),
        ],
    )
    base = Historial(db_conn).linea_base("cacles", "hojas", muestras=4)

    assert base is not None
    assert base.mediana == 0.8


def test_se_mide_una_tienda_que_nunca_ha_ingerido(db_conn: Any) -> None:
    """La decisión de no poner FK a `retailer`, ejercida.

    La fila de `retailer` la crea la primera ingesta, así que con una FK la tienda que más interesa
    vigilar —la que aún no ha ingerido nunca, como Hipercor en `dev` cuando se abrió #93— sería
    justo la única imposible de medir.
    """
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retailer WHERE slug = 'reciennacida'")
        assert cur.fetchone()[0] == 0, "la premisa del test: esa tienda no existe en retailer"

    assert Historial(db_conn).guardar([("reciennacida", "hojas", 12.0, 4)]) == 1


# --- la degradación, que no necesita Postgres ------------------------------------------------


def test_sin_base_de_datos_el_historial_no_rompe_el_vigia() -> None:
    """Un puerto muerto: el vigía tiene que seguir sondeando y publicando igual que sin esto."""
    historial = Historial.abrir(Config(database_url="postgresql://nadie@127.0.0.1:1/x"))

    assert not historial.disponible
    assert historial.motivo and "no se pudo conectar" in historial.motivo
    assert historial.linea_base("cacles", "hojas", muestras=4) is None
    assert historial.guardar([("cacles", "hojas", 1.0, 1)]) == 0


def test_si_la_consulta_falla_se_queda_inerte_con_el_motivo(db_conn: Any) -> None:
    """El caso real: en QA el jueves puede llegar antes que la migración `0022`.

    Se simula con la conexión ya cerrada, que produce el mismo efecto —la consulta eleva— sin
    tocar el esquema de la base de test.
    """
    historial = Historial(db_conn)
    db_conn.close()

    assert historial.linea_base("cacles", "hojas", muestras=4) is None
    assert historial.motivo and "no se pudo leer" in historial.motivo
    assert not historial.disponible, "no se reintenta por cada tienda: si no está, no está"
