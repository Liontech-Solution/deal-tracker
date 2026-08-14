#!/usr/bin/env python3
"""Barrido completo del espejo Drizzle contra el esquema real (#364).

Compara una base con las migraciones de `db/migrations` ya aplicadas contra lo que declara
`services/web/src/database/schema.ts`: tablas, columnas, nulabilidad y tipo. Existe porque
mirar el espejo a ojo solo encuentra lo que uno va buscando — `missing_streak` llevaba
desalineada desde la `0008` y salió de rebote, revisando otra cosa (#364).

No sustituye al `revisor-contrato-esquema`: no mira `ingest.py`, ni los `ON CONFLICT`, ni el
reparto de propiedad. Cubre la parte mecánica, que es justo la que se escapa leyendo.

Uso (necesita una base con TODAS las migraciones aplicadas; una desechable vale):

    python3 .claude/agents/barrido-espejo.py --dsn "$DATABASE_URL"
    python3 .claude/agents/barrido-espejo.py --psql-cmd "docker exec dt-pg psql -U dealtracker -d dt_364"

Sale con 1 si hay discrepancias, 0 si el espejo está al día.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ESPEJO = RAIZ / "services/web/src/database/schema.ts"

# `schema_migrations` no está en db/migrations: la crean los dos aplicadores para llevar su
# propia cuenta, así que no es parte del contrato que el espejo refleja.
TABLAS_FUERA_DE_CONTRATO = {"schema_migrations"}

SQL = """
SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) FROM (
  SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, c.is_generated, t.table_type
  FROM information_schema.columns c
  JOIN information_schema.tables t
    ON t.table_schema = c.table_schema AND t.table_name = c.table_name
  WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
  ORDER BY c.table_name, c.ordinal_position
) t;
"""

# `export const x = pgTable(<lo que sea>'nombre',`
RE_TABLA = re.compile(r"export const (\w+) = pgTable\(\s*'([a-z_0-9]+)'", re.M)
# `nombreTs: tipo('nombre_sql'` — una columna del objeto de definición.
RE_COL = re.compile(r"^\s{2,4}(\w+): (\w+)\('([a-z_0-9]+)'", re.M)
# PK compuesta declarada aparte: `primaryKey({ columns: [t.productId, t.tag] })`
RE_PK_COMP = re.compile(r"primaryKey\(\{[^}]*columns:\s*\[([^\]]*)\]")

# Solo para cazar derivas gordas; no pretende ser exhaustivo.
EQUIVALENCIAS = {
    "text": {"text", "character varying"},
    "integer": {"integer"},
    "bigint": {"bigint"},
    "numeric": {"numeric"},
    "boolean": {"boolean"},
    "timestamp": {"timestamp with time zone", "timestamp without time zone"},
    "date": {"date"},
    "jsonb": {"jsonb"},
    "smallint": {"smallint"},
    "real": {"real", "double precision"},
}


def leer_base(psql_cmd: list[str]) -> dict[str, dict[str, dict]]:
    salida = subprocess.run(
        [*psql_cmd, "-At", "-c", " ".join(SQL.split())],
        capture_output=True, text=True, check=True,
    ).stdout
    tablas: dict[str, dict[str, dict]] = {}
    for fila in json.loads(salida):
        tablas.setdefault(fila["table_name"], {})[fila["column_name"]] = fila
    return tablas


def leer_espejo(ruta: Path) -> dict[str, dict[str, dict]]:
    txt = ruta.read_text()
    marcas = [(m.start(), m.group(2)) for m in RE_TABLA.finditer(txt)]
    tablas: dict[str, dict[str, dict]] = {}
    for i, (pos, nombre) in enumerate(marcas):
        bloque = txt[pos : marcas[i + 1][0] if i + 1 < len(marcas) else len(txt)]
        pk_compuesta = {
            x.strip().removeprefix("t.")
            for m in RE_PK_COMP.finditer(bloque)
            for x in m.group(1).split(",")
            if x.strip()
        }
        cols: dict[str, dict] = {}
        for m in RE_COL.finditer(bloque):
            resto = bloque[m.start() :]
            sig = RE_COL.search(resto[len(m.group(0)) :])
            trozo = resto[: len(m.group(0)) + sig.start()] if sig else resto
            cols[m.group(3)] = {
                "tipo": m.group(2),
                # `.primaryKey()` implica NOT NULL en Postgres, y no se escribe con `.notNull()`.
                "notNull": ".notNull()" in trozo
                or ".primaryKey()" in trozo
                or m.group(1) in pk_compuesta,
            }
        tablas[nombre] = cols
    return tablas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                   help="URL de una base con todas las migraciones aplicadas")
    p.add_argument("--psql-cmd", help="orden psql completa, si la base no es alcanzable por DSN")
    p.add_argument("--espejo", type=Path, default=ESPEJO)
    args = p.parse_args()

    if args.psql_cmd:
        psql_cmd = shlex.split(args.psql_cmd)
    elif args.dsn:
        psql_cmd = ["psql", args.dsn]
    else:
        p.error("hace falta --dsn (o DATABASE_URL) o --psql-cmd")

    base = leer_base(psql_cmd)
    espejo = leer_espejo(args.espejo)
    hallazgos: list[str] = []

    for t in sorted(set(base) - set(espejo) - TABLAS_FUERA_DE_CONTRATO):
        hallazgos.append(f"TABLA AUSENTE  {t}  ({len(base[t])} columnas en la base)")
    for t in sorted(set(espejo) - set(base)):
        hallazgos.append(f"TABLA SOBRA    {t}  (declarada, no existe en la base)")

    for t in sorted(set(base) & set(espejo)):
        cols_base, cols_esp = base[t], espejo[t]
        for c, f in cols_base.items():
            if c not in cols_esp:
                gen = "  [generada]" if f["is_generated"] == "ALWAYS" else ""
                nn = "NOT NULL" if f["is_nullable"] == "NO" else "NULL"
                hallazgos.append(f"COLUMNA AUSENTE  {t}.{c}  {f['data_type']}  {nn}{gen}")
                continue
            e = cols_esp[c]
            if (f["is_nullable"] == "NO") != e["notNull"]:
                hallazgos.append(
                    f"NULABILIDAD  {t}.{c}  base={'NOT NULL' if f['is_nullable'] == 'NO' else 'NULL'}"
                    f"  espejo={'notNull()' if e['notNull'] else 'nulable'}"
                )
            aceptados = EQUIVALENCIAS.get(e["tipo"])
            if aceptados and f["data_type"] not in aceptados:
                hallazgos.append(
                    f"TIPO  {t}.{c}  base={f['data_type']}  espejo={e['tipo']}()"
                )
        for c in cols_esp:
            if c not in cols_base:
                hallazgos.append(f"COLUMNA SOBRA  {t}.{c}  (declarada, no existe en la base)")

    if not hallazgos:
        print(f"Espejo al día: {len(espejo)} tablas, sin discrepancias.")
        return 0
    print("\n".join(hallazgos))
    print(f"\n{len(hallazgos)} discrepancia(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
