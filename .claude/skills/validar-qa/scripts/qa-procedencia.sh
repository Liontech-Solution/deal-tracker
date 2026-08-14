#!/usr/bin/env bash
# Qué imagen escribió de verdad el dato contra el que se va a validar. Es parte de la Fase 0.
#
# Existe porque `qa-estado.sh` contesta a otra pregunta y es fácil confundirlas: él lee la imagen del
# **CronJob**, que ArgoCD ya sincronizó, así que enseña la versión nueva desde el minuto uno. Pero las
# filas de `scrape_run` las escribió un **Job**, y un Job es un snapshot inmutable del momento en que
# se disparó: conserva la imagen que hubiera entonces. Medido el 14/08/2026 en QA: las nueve pasadas
# del lunes corrieron v0.1.9 mientras QA servía v0.4.0, y nada lo decía. El informe de v0.3.0 dio por
# hecho que su bloque `## Cifras` describía su propia ingesta, y describía la de v0.1.9 (#378).
#
# El emparejamiento va por los **args del contenedor**, no por el nombre del Job. Los nombres no
# siguen ningún patrón en cuanto alguien dispara uno a mano (`validacion-v040-zara`, `hipercor-frio-1`,
# `springfield-qa-1`), pero todos llevan `--retailer <slug>`, y ese slug es el mismo string que
# `retailer.slug` en la base (ver `SLUG = "c-and-a"` en stores/c_and_a.py, con guiones a propósito).
#
# Todo es de solo lectura: `kubectl get` y una consulta por `qa-sql.sh`, que abre transacción READ ONLY.
#
# Códigos de salida, pensados para que el que llama pueda decidir:
#   0  toda pasada la escribió la imagen desplegada
#   1  hay dato heredado, o procedencia que no se puede establecer
#   2  no se pudo determinar (sin cluster, sin base): NO es un aprobado
#
# Uso:
#   qa-procedencia.sh                          # namespace deal-tracker-qa
#   qa-procedencia.sh --ns deal-tracker-dev
#   qa-procedencia.sh --rango v0.4.0..v0.5.0   # además decide la severidad (P0/P1), ver abajo
#   qa-procedencia.sh --json                   # para encadenar con jq
#   qa-procedencia.sh --ventana 7200           # margen Job↔fila, en segundos (por defecto 3600)

source "$(dirname "$(readlink -f "$0")")/qa-comun.sh"

aqui="$(dirname "$(readlink -f "$0")")"

solo_json=0
rango=""
# Un Job escribe su fila de `scrape_run` a los pocos segundos de arrancar (medido: 2 s en la pasada
# de Zara del 14/08, 5 s en la de Hipercor del 10/08) porque el INSERT vive dentro de la transacción
# de la propia pasada y `now()` es la hora de inicio de esa transacción. Una hora es holgadísimo y a
# la vez evita casar una fila con un Job viejo del mismo slug cuando el suyo ya fue recolectado.
ventana=3600

while [ $# -gt 0 ]; do
  case "$1" in
    --ns)      NS="$2"; shift 2 ;;
    --rango)   rango="$2"; shift 2 ;;
    --json)    solo_json=1; shift ;;
    --ventana) ventana="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *)         echo "✖ opción desconocida: $1" >&2; exit 2 ;;
  esac
done

no_se_puede() { echo "✖ no se puede establecer la procedencia: $*" >&2; exit 2; }

# La imagen desplegada la sabe ya qa-estado.sh; no se repite aquí para que no puedan divergir.
identidad="$("$aqui/qa-estado.sh" --ns "$NS" --json 2>/dev/null)" \
  || no_se_puede "qa-estado.sh no ha podido leer $NS"
tag_scraper="$(printf '%s' "$identidad" | jq -r '.scraper')"
tag_web="$(printf '%s' "$identidad" | jq -r '.matching')"

jobs="$(kc get job -o json 2>/dev/null)" || no_se_puede "no hay acceso a los Job de $NS"

# La lista sale de `retailer`, no de una lista escrita a mano: el LEFT JOIN LATERAL es el de D2 y es
# deliberado, porque un JOIN normal escondería la tienda que nunca ha corrido, que es justo la que
# hay que ver.
sql="
SELECT r.slug, s.id AS run_id, s.status, s.started_at
FROM retailer r
LEFT JOIN LATERAL (
  SELECT * FROM scrape_run WHERE retailer_id = r.id ORDER BY started_at DESC LIMIT 1
) s ON true
ORDER BY r.slug;
"
pasadas="$("$aqui/qa-sql.sh" --ns "$NS" --json "$sql" 2>/dev/null)" \
  || no_se_puede "la base de $NS no responde (¿pod del web caído?)"

# `job_state` es del servicio web, no del scraper: el matching corre la imagen del **web**, y por eso
# se compara contra otro tag. Preguntarle al CronJob del scraper por esto da una respuesta plausible
# y falsa, que es el mismo error que ya avisa qa-estado.sh.
matching="$("$aqui/qa-sql.sh" --ns "$NS" --json \
  "SELECT updated_at FROM job_state WHERE job = 'matching';" 2>/dev/null)" || matching='[]'

filas="$(printf '%s' "$jobs" | jq -c \
  --argjson pasadas "$pasadas" \
  --argjson matching "$matching" \
  --arg tag_scraper "$tag_scraper" \
  --arg tag_web "$tag_web" \
  --argjson ventana "$ventana" '
  def epoch: if . == null then null else (sub("\\.[0-9]+"; "") | fromdateiso8601) end;
  def tag: split(":") | last;

  # Un Job puede no traer startTime todavía (recién creado): sin él no se puede fechar nada.
  [ .items[]
    | .spec.template.spec.containers[0] as $c
    | (($c.command // []) + ($c.args // [])) as $argv
    | { nombre: .metadata.name,
        inicio: (.status.startTime | epoch),
        tag: ($c.image | tag),
        slug: (($argv | index("--retailer")) as $i
               | if $i == null then null else $argv[$i + 1] end),
        es_matching: ($argv | join(" ") | test("matching\\.job")) }
    | select(.inicio != null) ] as $jobs

  | def elegir($cands; $t):
      [ $cands[] | select(.inicio <= $t and .inicio >= ($t - $ventana)) ]
      | sort_by(.inicio) | last;

  ( $pasadas | map(
      . as $r
      | ($r.started_at | epoch) as $t
      | if $t == null then
          { que: $r.slug, cuando: null, escrita_por: null, desplegado: $tag_scraper,
            veredicto: "nunca" }
        else
          elegir([ $jobs[] | select(.slug == $r.slug) ]; $t) as $j
          | { que: $r.slug, cuando: $r.started_at, job: $j.nombre,
              escrita_por: $j.tag, desplegado: $tag_scraper,
              veredicto: (if $j == null then "desconocida"
                          elif $j.tag == $tag_scraper then "ok"
                          else "heredado" end) }
        end ) )
  + ( $matching | map(
      . as $m
      | ($m.updated_at | epoch) as $t
      | elegir([ $jobs[] | select(.es_matching) ]; $t) as $j
      | { que: "matching", cuando: $m.updated_at, job: $j.nombre,
          escrita_por: $j.tag, desplegado: $tag_web,
          veredicto: (if $j == null then "desconocida"
                      elif $j.tag == $tag_web then "ok"
                      else "heredado" end) } ) )
')"

if [ "$solo_json" = 1 ]; then
  printf '%s\n' "$filas" | jq .
else
  printf '\n\033[1m== procedencia del dato en %s\033[0m\n' "$NS"
  # La cabecera va a pelo y no por printf: `%-14s` cuenta BYTES, y con «qué» y «última» los acentos
  # se comen una columna cada uno y dejan el encabezado desalineado con sus filas.
  echo "qué            última escritura     la escribió    desplegado"
  printf '%s' "$filas" | jq -r '.[] | [
      .que,
      (.cuando | if . == null then "(nunca ha corrido)"
                 else (sub("\\.[0-9]+"; "") | sub("T"; " ")) end),
      (.escrita_por // "?"),
      .desplegado,
      (if .veredicto == "ok" then "✔"
       elif .veredicto == "heredado" then "✖ dato de otra versión"
       elif .veredicto == "nunca" then "— sin pasada (lo cuenta D1)"
       else "✖ procedencia desconocida (el Job ya no está)" end)
    ] | @tsv' |
  while IFS=$'\t' read -r que cuando escribio desplegado marca; do
    printf "%-14s %-20s %-14s %-12s %s\n" "$que" "$cuando" "$escribio" "$desplegado" "$marca"
  done
fi

heredados="$(printf '%s' "$filas" | jq '[.[] | select(.veredicto == "heredado")] | length')"
desconocidos="$(printf '%s' "$filas" | jq '[.[] | select(.veredicto == "desconocida")] | length')"
sospechosos="$(printf '%s' "$filas" | jq -r \
  '[.[] | select(.veredicto == "heredado" or .veredicto == "desconocida") | .que] | join(", ")')"

if [ "$solo_json" = 1 ]; then
  [ "$((heredados + desconocidos))" -eq 0 ] && exit 0
  exit 1
fi

if [ "$((heredados + desconocidos))" -eq 0 ]; then
  echo "✔ todo el dato lo escribió la versión desplegada"
  exit 0
fi

# La severidad depende de si esta release toca el scraper, y esa es la lección de #378: en v0.3.0 la
# coartada era que `services/scraper/` no tenía ni un cambio, y esa coartada no se repite. Con
# --rango se decide aquí; sin él, se dice qué falta para poder decidirlo, en vez de elegir la
# severidad blanda por omisión.
severidad="P1"
motivo="dato heredado, pero no sé si esta release toca el scraper (pásame --rango vANTERIOR..vACTUAL)"
if [ -n "$rango" ]; then
  if ! tocados="$(git log --oneline "$rango" -- services/scraper/ 2>/dev/null)"; then
    motivo="dato heredado, y no he podido evaluar el rango '$rango' (¿falta un tag? prueba 'git fetch --tags'): decide la severidad a mano mirando si la release toca services/scraper/"
  elif [ -n "$tocados" ]; then
    severidad="P0"
    motivo="esta release toca services/scraper/ ($(printf '%s\n' "$tocados" | grep -c .) commits), así que validar el frente de datos contra estas filas no prueba nada del código que va a producción"
  else
    motivo="esta release no toca services/scraper/, así que el dato heredado sigue describiendo el mismo scraper"
  fi
fi

echo
echo "✖ $severidad: $sospechosos — $motivo"
if [ "$severidad" = "P0" ]; then
  echo "  Dispara una pasada con la imagen desplegada antes de validar el frente de datos:"
  echo "    kubectl -n $NS create job validacion-<version>-<slug> --from=cronjob/deal-tracker-scraper-<slug>"
fi
if [ "$desconocidos" -gt 0 ]; then
  echo "  («procedencia desconocida» no es un aprobado: el Job caducó —successfulJobsHistoryLimit: 3—"
  echo "   y ya no queda forma de saber qué imagen escribió esa fila)"
fi
exit 1
