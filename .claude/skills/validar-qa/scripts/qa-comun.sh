# Trozos compartidos por los scripts de validar-qa. Se hace `source`, no se ejecuta.
#
# Dos cosas que aquí no son obvias y explican por qué existe este fichero:
#
#   1. El kubeconfig del cluster es ~/.kube/k3slocal.yaml y no siempre es el que KUBECONFIG apunta.
#   2. La sesión puede correr desde un worktree de .claude/worktrees/, donde los ficheros *.local
#      (credenciales del usuario de prueba) NO están, porque están gitignorados. Todo lo que
#      necesite un secreto tiene que resolverse contra el checkout principal.

set -euo pipefail

: "${KUBECONFIG:=$HOME/.kube/k3slocal.yaml}"
export KUBECONFIG

NS="${QA_NS:-deal-tracker-qa}"
QA_URL="${QA_URL:-https://dealtracker-qa.liontechsolution.com}"

# Raíz del checkout principal, aunque estemos dentro de un worktree: --git-common-dir siempre
# devuelve el .git de verdad, no el enlace del worktree.
raiz_principal() {
  dirname "$(git rev-parse --path-format=absolute --git-common-dir)"
}

kc() { kubectl -n "$NS" "$@"; }

muere() {
  echo "✖ $*" >&2
  exit 1
}

# El pod del web es la única puerta a la base de datos de QA: no hay psql en esta máquina y el
# servicio de CNPG no está expuesto fuera del cluster.
pod_web() {
  local p
  p="$(kc get pod -l app=deal-tracker-web -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  [ -n "$p" ] || muere "no hay pod del web en $NS (¿namespace equivocado o despliegue caído?)"
  printf '%s' "$p"
}
