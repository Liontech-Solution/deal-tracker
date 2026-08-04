#!/usr/bin/env bash
# Los slugs de tienda registrados, tal como los ve el código.
#
# Existe porque la ruta obvia —`services/scraper/.venv/bin/python`— NO funciona desde un worktree
# de .claude/worktrees/: el venv no está versionado, así que ahí no hay ninguno. Y las sesiones de
# este repo trabajan en worktrees. Este resuelve el venv del checkout principal y el PYTHONPATH.
#
# La lista sale siempre de registry.available_slugs(), nunca escrita a mano: es lo que hace que
# registrar una tienda la meta automáticamente en la validación.

source "$(dirname "$(readlink -f "$0")")/qa-comun.sh"

principal="$(raiz_principal)"
py="$principal/services/scraper/.venv/bin/python"

[ -x "$py" ] || muere "no hay venv del scraper en $py (lánzalo con 'just setup' en el checkout principal)"

PYTHONPATH="$principal/services/scraper/src" exec "$py" -c \
  'from scraper.stores.registry import available_slugs; print("\n".join(available_slugs()))'
