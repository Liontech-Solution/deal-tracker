#!/usr/bin/env bash
# SQL contra la base de datos de QA (o de dev), en solo lectura por defecto.
#
# Esta máquina no tiene psql ni docker, y la base de CNPG no está expuesta fuera del cluster: la
# única puerta es el pod del web, que sí trae el driver `postgres` de node y la DATABASE_URL en el
# entorno. Ojo: `require('pg')` NO existe en esa imagen, el driver es `postgres`.
#
# La consulta viaja en base64 dentro del propio script de node. Es feo y es a propósito: así no hay
# que pelearse con tres niveles de comillas (fish -> kubectl -> node) y el SQL llega byte a byte.
#
# Por defecto la transacción es READ ONLY de verdad (no un grep de palabras prohibidas): el motor
# rechaza cualquier escritura. En agosto de 2026 un pytest despistado se llevó por delante el
# histórico de vigia_run; esto es la valla de aquello.
#
# Uso:
#   qa-sql.sh "SELECT * FROM retailer ORDER BY slug"
#   qa-sql.sh -f consulta.sql
#   echo "SELECT 1" | qa-sql.sh
#   qa-sql.sh --json "SELECT ..."          # JSON crudo, para encadenar con jq
#   qa-sql.sh --ns deal-tracker-dev "..."  # otro namespace
#   qa-sql.sh --escribir "DELETE FROM interest WHERE ..."   # levanta la valla, di por qué

source "$(dirname "$(readlink -f "$0")")/qa-comun.sh"

formato="tabla"
solo_lectura=1
sql=""

while [ $# -gt 0 ]; do
  case "$1" in
    --json)      formato="json"; shift ;;
    --ns)        NS="$2"; shift 2 ;;
    --escribir)  solo_lectura=0; shift ;;
    -f)          sql="$(cat "$2")"; shift 2 ;;
    -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
    *)           sql="$1"; shift ;;
  esac
done

[ -n "$sql" ] || sql="$(cat)"
[ -n "${sql//[[:space:]]/}" ] || muere "consulta vacía"

b64="$(printf '%s' "$sql" | base64 -w0)"

lector="$([ "$solo_lectura" = 1 ] && echo true || echo false)"

# --request-timeout evita que una consulta pesada deje el terminal colgado sin señal.
kc exec --request-timeout=300s deploy/deal-tracker-web -c web -- node -e "
const postgres = require('postgres');
const q = Buffer.from('$b64', 'base64').toString('utf8');
const soloLectura = $lector;
const formato = '$formato';
const sql = postgres(process.env.DATABASE_URL, { max: 1, idle_timeout: 5 });

(async () => {
  let filas;
  if (soloLectura) {
    filas = await sql.begin(async (tx) => {
      await tx.unsafe('SET TRANSACTION READ ONLY');
      return tx.unsafe(q);
    });
  } else {
    filas = await sql.unsafe(q);
  }
  const datos = Array.from(filas);
  if (formato === 'json') {
    console.log(JSON.stringify(datos, null, 1));
  } else if (datos.length === 0) {
    console.log('(0 filas)');
  } else {
    console.table(datos);
    console.log(datos.length + ' fila(s)');
  }
  await sql.end();
})().catch((e) => {
  console.error('SQL: ' + e.message);
  process.exit(1);
});
"
