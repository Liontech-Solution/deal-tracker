#!/usr/bin/env bash
# Avisa cuando un commit mueve un lado de un espejo del contrato y deja el otro quieto.
#
# Por qué existe. Este repo tiene dos parejas de ficheros que sólo son correctas si cambian
# JUNTAS, y cuya divergencia **compila, pasa los tests y miente**:
#
#   1. `deal-rule.ts` y `deal-rule.sql.ts`. La regla de honestidad etiqueta la tarjeta en TS, pero
#      `onlyDeals` y `sort=ofertas` tienen que decidir ANTES del LIMIT, así que la regla está
#      reimplementada en SQL. Cuando #375 movió el margen en un solo lado, los 725 tests del
#      servicio siguieron en verde.
#   2. `db/migrations/**` y el espejo Drizzle `schema.ts`. `missing_streak` llevaba desalineada
#      desde la 0008 y salió de rebote, revisando otra cosa (#364).
#
# Los subagentes `revisor-espejo-honestidad` y `revisor-contrato-esquema` cubren esto mucho mejor
# que un `grep`, pero son opt-in: hay que acordarse de invocarlos. Este hook no propone reemplazarlos
# — sólo se encarga de que nadie llegue al commit sin que le hayan preguntado. `da33aef` tocó
# `deal-rule.ts` sin el `.sql.ts` y nadie lo paró.
#
# Es `ask`, no `deny`: hay cambios legítimos de un solo lado (un comentario, un test), y un hook que
# los bloquea es un hook que se acaba desactivando.
#
# Shebang explícito a bash a propósito: la shell interactiva de este equipo es zsh y la sintaxis
# de fish revienta aquí sin avisar.
set -uo pipefail

entrada="$(cat)"
comando="$(printf '%s' "$entrada" | jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -n "$comando" ] || exit 0

# `git commit` al principio o detrás de un separador: el compuesto `git add -A && git commit -m x`
# es la forma habitual de commitear aquí, y un prefijo simple no lo vería. `git -C` no se contempla
# porque el sandbox lo veta.
printf '%s' "$comando" \
  | grep -qE '(^|[;&|(]|&&)[[:space:]]*git[[:space:]]+commit([[:space:]]|$)' || exit 0

# Sin `-C`: queremos el stage del worktree desde el que se commitea, que es donde trabajan las
# sesiones de este repo.
ficheros="$(git diff --cached --name-only 2>/dev/null)"

# `git commit -a` no pasa por el stage: hay que mirar además los tracked modificados.
if printf '%s' "$comando" | grep -qE 'git[[:space:]]+commit[^;&|]*([[:space:]]-[a-zA-Z]*a([[:space:]]|$)|[[:space:]]--all([[:space:]]|$))'; then
  ficheros="$ficheros
$(git diff --name-only HEAD 2>/dev/null)"
fi

[ -n "${ficheros//[[:space:]]/}" ] || exit 0

toca() { printf '%s\n' "$ficheros" | grep -qF -- "$1"; }

avisos=""
anota() { avisos="${avisos}${avisos:+$'\n\n'}$1"; }

if toca 'services/web/src/matching/deal-rule.ts' \
   && ! toca 'services/web/src/matching/deal-rule.sql.ts'; then
  anota "• El commit toca deal-rule.ts SIN deal-rule.sql.ts.
  La regla de honestidad está espejada en SQL porque el filtrado (onlyDeals) y el orden
  (sort=ofertas) deciden antes del LIMIT, así que no pueden usar la versión TS. Cambiar un lado
  compila, pasa los tests y miente: con el margen movido en un solo lado pasaron los 725 tests
  del servicio (#375). Lo que de verdad diverge es el PVP creíble y el descuento honesto — el
  veredicto 'real' es el menos sensible de los tres.
  → Pasa el subagente 'revisor-espejo-honestidad' antes de commitear."
fi

if toca 'db/migrations/' && ! toca 'services/web/src/database/schema.ts'; then
  anota "• El commit toca db/migrations/ SIN el espejo Drizzle schema.ts.
  db/migrations es la fuente de verdad; schema.ts y el SQL crudo de ingest.py son espejos, y los
  tres derivan en silencio (#364). Mira también si ingest.py necesita el cambio: sólo aplica a las
  tablas que posee el scraper (retailer, product, variant, price_history, scrape_run, vigia_run),
  no a las del web.
  → Pasa el subagente 'revisor-contrato-esquema' antes de commitear."
fi

[ -n "$avisos" ] || exit 0

jq -n --arg motivo "$avisos" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $motivo
  }
}'
