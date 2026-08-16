#!/usr/bin/env bash
# Etiqueta el verde de los tests cuando falta la base que gatea los de integración.
#
# Por qué existe. Aquí un `pytest` o un `pnpm test` en verde puede no significar nada, y el
# mecanismo es distinto en cada servicio:
#
#   • scraper: sin TEST_DATABASE_URL, conftest.py hace `pytest.skip` — se ve en la salida, aunque
#     se lea por encima.
#   • web: sin TEST_DATABASE_URL_CTYPE_C es PEOR, porque no se salta nada. `BASES_CANON`
#     (test/helpers.ts) FILTRA la base ausente de la lista, así que el `describe.each` declara un
#     caso en vez de dos y el del cluster desaparece de la salida sin dejar rastro. El aviso de
#     `saltarSiNoHayBase` sólo sale cuando faltan LAS DOS. Con una puesta y la otra no —que es la
#     configuración más probable— el verde tapa justo lo que #105 destapó: bajo ctype `C`,
#     `lower()` no baja las acentuadas, y eso partió 748 variantes y dos chips de faceta.
#
# No bloquea: sólo pone el asterisco. Y emite JSON en vez de escribir en stderr porque en exit 0
# stderr va únicamente al log de depuración — no lo ve ni el usuario ni Claude.
set -uo pipefail

entrada="$(cat)"
comando="$(printf '%s' "$entrada" | jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -n "$comando" ] || exit 0

es_web=0
es_scraper=0
printf '%s' "$comando" | grep -qE '\bvitest\b|\bpnpm\b[^;&|]*\btest\b' && es_web=1
printf '%s' "$comando" | grep -qE '\bpytest\b|\bjust[[:space:]]+check\b' && es_scraper=1
[ "$es_web" = 1 ] || [ "$es_scraper" = 1 ] || exit 0

# Definida en el entorno del hook o puesta en línea en el propio comando.
definida() {
  [ -n "${!1:-}" ] && return 0
  printf '%s' "$comando" | grep -qE "\b$1="
}

avisos=""
anota() { avisos="${avisos}${avisos:+$'\n\n'}$1"; }

if ! definida TEST_DATABASE_URL; then
  anota "• Falta TEST_DATABASE_URL: los tests de integración NO se ejecutan, se saltan.
  Lo que quede en verde no ha tocado Postgres. Levanta una desechable en Docker."
fi

if [ "$es_web" = 1 ] && ! definida TEST_DATABASE_URL_CTYPE_C; then
  anota "• Falta TEST_DATABASE_URL_CTYPE_C, y esto NO sale como un test saltado.
  BASES_CANON filtra la base ausente, así que los specs de canonicalización (size_canon,
  color_canon y el plegado de la búsqueda) corren con una sola base y el caso del cluster
  desaparece de la salida sin dejar rastro. El cluster es UTF8 | C | C, donde lower() no baja
  las acentuadas: es el agujero por el que se coló #105. El verde de abajo no lo cubre.
    CREATE DATABASE deal_tracker_ctype_c
      TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';"
fi

[ -n "$avisos" ] || exit 0

jq -n --arg motivo "$avisos" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "allow",
    permissionDecisionReason: $motivo
  }
}'
