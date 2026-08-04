#!/usr/bin/env bash
# Foto del entorno QA en una llamada: qué versión corre, si ArgoCD la ha sincronizado, qué jobs han
# ido bien y cuáles no, y si el servicio responde. Es la Fase 0 de /validar-qa.
#
# Todo es de solo lectura. Existe porque la identidad de lo que se valida es la mitad del informe:
# un "APTO" sin decir sobre qué tag se dio no vale nada, y las imágenes del web y del scraper pueden
# ir desacompasadas si un release-qa se quedó a medias.
#
# Uso:
#   qa-estado.sh                 # namespace deal-tracker-qa
#   qa-estado.sh --ns deal-tracker-dev
#   qa-estado.sh --json          # solo el bloque de identidad, para el informe

source "$(dirname "$(readlink -f "$0")")/qa-comun.sh"

solo_json=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ns)      NS="$2"; shift 2 ;;
    --json)    solo_json=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *)         muere "opción desconocida: $1" ;;
  esac
done

titulo() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# Ojo al elegir el cronjob: `matching` corre la imagen del **web**, no la del scraper, y es el
# primero por orden alfabético. Preguntarle a él por la versión del scraper da una respuesta
# plausible y falsa.
imagen_de_cronjob() {
  local cj
  cj="$(kc get cronjob -o name 2>/dev/null | grep -m1 -- "$1" || true)"
  [ -n "$cj" ] || return 0
  kc get "$cj" -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}' 2>/dev/null || true
}

img_web="$(kc get deploy deal-tracker-web -o jsonpath='{.spec.template.spec.containers[?(@.name=="web")].image}' 2>/dev/null || true)"
img_scraper="$(imagen_de_cronjob 'scraper-')"
img_matching="$(imagen_de_cronjob 'matching')"
tag_web="${img_web##*:}"
tag_scraper="${img_scraper##*:}"
tag_matching="${img_matching##*:}"

if [ "$solo_json" = 1 ]; then
  printf '{"ns":"%s","web":"%s","scraper":"%s","matching":"%s","coinciden":%s}\n' \
    "$NS" "$tag_web" "$tag_scraper" "$tag_matching" \
    "$([ "$tag_web" = "$tag_scraper" ] && [ "$tag_web" = "$tag_matching" ] && echo true || echo false)"
  exit 0
fi

titulo "versión desplegada en $NS"
printf 'web       %s\nscraper   %s\nmatching  %s\n' \
  "${img_web:-(sin deployment)}" "${img_scraper:-(sin cronjobs)}" "${img_matching:-(sin cronjob)}"
if [ -n "$tag_web" ] && { [ "$tag_web" != "$tag_scraper" ] || [ "$tag_web" != "$tag_matching" ]; }; then
  echo "✖ P0: los tres artefactos no corren el mismo tag (web $tag_web · scraper $tag_scraper · matching $tag_matching) — el release quedó a medias"
fi

titulo "ArgoCD"
kubectl -n argocd get application -o custom-columns=\
'NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision' \
  2>/dev/null | grep -E "NAME|deal-tracker" || echo "(sin acceso al namespace argocd)"

titulo "pods"
kc get pod -o custom-columns=\
'NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp' \
  --sort-by=.metadata.creationTimestamp | tail -15

titulo "cronjobs"
kc get cronjob -o custom-columns=\
'NAME:.metadata.name,SCHEDULE:.spec.schedule,SUSPEND:.spec.suspend,LAST:.status.lastScheduleTime'

titulo "jobs (últimos 20)"
kc get job --sort-by=.metadata.creationTimestamp -o custom-columns=\
'NAME:.metadata.name,SUCCEEDED:.status.succeeded,FAILED:.status.failed,START:.status.startTime,END:.status.completionTime' \
  | tail -21

fallidos="$(kc get job -o jsonpath='{range .items[?(@.status.failed)]}{.metadata.name}{"\n"}{end}' | grep -c . || true)"
[ "${fallidos:-0}" -gt 0 ] && echo "✖ $fallidos job(s) en Failed — mira sus logs antes de dar nada por bueno"

titulo "servicio"
codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$QA_URL/api/health" || echo 000)"
echo "GET $QA_URL/api/health -> $codigo"
[ "$codigo" = "200" ] || echo "✖ P0: el servicio no está sano"
echo -n "GET $QA_URL/api/config -> "
curl -s --max-time 15 "$QA_URL/api/config" || echo "(sin respuesta)"
echo
echo "(en QA los tres campos de /api/config deben venir no nulos: sin Keycloak la mitad del validador no aplica)"
