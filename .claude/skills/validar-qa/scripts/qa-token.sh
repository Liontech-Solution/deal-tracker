#!/usr/bin/env bash
# Access token de Keycloak del usuario de prueba `test-qa`, listo para un Bearer.
#
# Envuelve a .claude/qa-login.py por una razón concreta: las credenciales viven en
# .claude/qa-test-user.local, que está gitignorado y por tanto NO existe dentro de un worktree.
# Como las sesiones de este repo trabajan en worktrees, llamar al script "el de aquí al lado" falla
# de una forma que parece un problema de Keycloak y no lo es. Este resuelve siempre el checkout
# principal.
#
# El token dura 300 segundos. No lo caches entre fases: ante un 401 vuelve a pedirlo, que cuesta
# menos de dos segundos, en vez de dar por roto el endpoint.
#
# Uso:
#   TOKEN=$(qa-token.sh)
#   curl -s -H "Authorization: Bearer $TOKEN" "$QA_URL/api/interests"

source "$(dirname "$(readlink -f "$0")")/qa-comun.sh"

principal="$(raiz_principal)"
login="$principal/.claude/qa-login.py"
creds="$principal/.claude/qa-test-user.local"

[ -f "$login" ] || muere "no encuentro $login"
[ -f "$creds" ] || muere "falta $creds (gitignored): recréalo con QA_KC_USERNAME= y QA_KC_PASSWORD="

cd "$principal"
exec python3 "$login" --token-only
