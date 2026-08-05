"""Conexión a Postgres (psycopg 3)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from .config import Config

_MAX_QUERY = 120  # caracteres de la consulta ajena que se muestran en el diagnóstico


def connect(config: Config) -> psycopg.Connection:
    """Abre una conexión. autocommit=False: la ingesta controla su transacción.

    El `lock_timeout` va como parámetro de la CONEXIÓN, no como un `SET` posterior, para que valga
    también para lo primero que se ejecute (#169). Y se aplica a toda la sesión a propósito, o sea
    incluidas las migraciones: `run.py` las aplica sobre esta misma conexión, y un `ALTER TABLE`
    esperando su `ACCESS EXCLUSIVE` detrás de una pasada viva es el mismo fallo mudo con peor cara.

    Ojo: acota cada ESPERA por un lock, no la duración de la transacción. Una pasada legítima de
    cinco horas no se ve afectada mientras nadie le retenga las filas.
    """
    if config.lock_timeout > 0:
        ms = int(config.lock_timeout * 1000)
        return psycopg.connect(
            config.database_url, autocommit=False, options=f"-c lock_timeout={ms}"
        )
    return psycopg.connect(config.database_url, autocommit=False)


@dataclass(frozen=True)
class SesionAbierta:
    """Una sesión ajena con transacción abierta sobre nuestra base."""

    pid: int
    state: str
    en_transaccion: str  # ya formateado por Postgres (p.ej. "02:13:41")
    query: str

    def __str__(self) -> str:
        return (
            f"pid {self.pid} · {self.state} · {self.en_transaccion} en transacción · {self.query}"
        )


def transacciones_abiertas(config: Config) -> list[SesionAbierta]:
    """Sesiones ajenas con una transacción abierta sobre esta base, la más vieja primero.

    Es el diagnóstico del `lock_timeout`: cuando la pasada no consigue un lock, esto dice QUIÉN lo
    retiene, que es la diferencia entre un error y un error accionable. Una `idle in transaction`
    con horas encima es casi siempre el backend de una pasada anterior muerta.

    Va por una conexión NUEVA y en autocommit: la que ha fallado se queda con la transacción
    abortada y no puede consultar nada. No necesita superusuario —las huérfanas son del mismo rol de
    la aplicación, así que su `query` es visible—, y si aun así fallara, el que llama se lo traga:
    esto es información de apoyo, nunca parte del contrato.
    """
    with psycopg.connect(config.database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT pid,
                   state,
                   to_char(now() - xact_start, 'HH24:MI:SS') AS en_transaccion,
                   left(coalesce(query, ''), %s)             AS query
              FROM pg_stat_activity
             WHERE datname = current_database()
               AND pid <> pg_backend_pid()
               AND xact_start IS NOT NULL
             ORDER BY xact_start
            """,
            (_MAX_QUERY,),
        )
        return [SesionAbierta(pid, state, edad, query) for pid, state, edad, query in cur]
