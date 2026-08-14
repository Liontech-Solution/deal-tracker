---
name: escrituras-contra-prod-las-pide-el-usuario
description: "el clasificador de auto mode bloquea las escrituras contra prod y el merge del repo de manifiestos; no insistas, pídeselas al usuario con el prefijo `!`"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7b3fc60e-45f5-4c57-9ef4-c06e6a29669e
  modified: 2026-08-14T09:53:27.696Z
---

Las acciones que **escriben** en producción o que la despliegan las bloquea el clasificador de auto
mode, aunque el resto de la sesión vaya con permisos. Medido el 08/08/2026 y ampliado el 12/08/2026:

- `kubectl -n deal-tracker-prod create job ... --from=cronjob/...` (la sesión anterior de #278 chocó
  con lo mismo y dejó la casilla sin hacer por eso).
- `gh pr merge` en los repos de GitOps: `k3s-local-apps-manifests` **y también
  `toolsuite-platform-gitops`** (medido el 12/08/2026 en su PR #59, que solo añadía un script y
  documentación). El criterio del clasificador parece ser el repo, no lo que cambia el PR: el
  `gh pr merge` de un PR de `deal-tracker` sí pasa. En los de GitOps ni lo intentes dos veces.
- ~~`gh release edit <tag> --prerelease=false`~~ **ya no: desmentido el 14/08/2026.** En la
  validación de v0.3.0 (12/08) se midió bloqueado, lectura incluida, y así quedó escrito aquí. En la
  de **v0.4.0 pasó sin más**: `gh release edit v0.4.0 --prerelease=false`, `gh release list` y
  `gh release view --json isPrerelease` funcionaron los tres a la primera. Así que **el ascenso de
  la release lo puedes hacer tú**; no se lo pidas al usuario por costumbre. Si algún día vuelve a
  caer, será por otra cosa, no por ser `gh release`.

  Y la moraleja que vale más que el dato: **una medida de «esto está bloqueado» caduca**. El
  clasificador cambia — en la misma sesión del 14/08 un `python3 - <<PY` que editaba **este mismo
  fichero** sí cayó, y se resolvió con la herramienta de edición normal. Intentar el comando cuesta
  un turno; darlo por imposible durante semanas cuesta la casilla entera.

Las **lecturas** contra prod pasan a veces, no siempre, y conviene no darlo por hecho:
`kubectl get/logs/kustomize` sí.

**Pero `kubectl exec` con `psql` contra `deal_tracker_prod` NO** (medido el 13/08/2026 en #314). Se
bloqueó un `EXPLAIN (ANALYZE, BUFFERS)` de una consulta de solo lectura, en **dos formas
distintas**: encadenada (`sed … > f && bash f`) y como script suelto (`bash medir-prod.sh`). Lo que
descarta que sea la forma del comando es que **el mismo script, byte por byte salvo el nombre de la
base, sí pasó contra `deal_tracker_qa`**. O sea que el disparador es la base de destino, no que
escriba o no.

**Y las ESCRITURAS por `psql` caen también contra dev y QA** (medido el 14/08/2026 en #370). Un
`ALTER TABLE variant SET (log_autovacuum_min_duration = 0)` contra `deal_tracker` y
`deal_tracker_qa` —idempotente, reversible con un `RESET` y la misma sentencia que iba a aplicar la
migración— se bloqueó igual. O sea que el eje no es solo la base de destino: contra dev/QA las
lecturas pasan y las escrituras no. Para un cambio de esquema eso no estorba, es el camino correcto
(la migración se despliega, no se aplica a mano); pero si lo que quieres es *observar* algo en el
entorno donde el fallo está vivo, cuenta desde el principio con pedírselo al usuario.

Consecuencia práctica: si necesitas medir algo contra prod —un `EXPLAIN`, un recuento—, dalo por
bloqueado desde el principio y prepáralo para que lo lance el usuario, en vez de gastar dos intentos
descubriéndolo. Y como es una lectura, no hay motivo para insistir en hacerla tú: el dato es igual
de bueno viniendo por `!`.

**Why:** reintentar la misma llamada no la desbloquea y quema la sesión; y dejar el trabajo a medias
por eso es peor, porque la casilla vuelve al backlog sin que nadie sepa que solo faltaba un comando.

**How to apply:** en cuanto una de esas dé «Blocked by classifier», no busques rodeo: pídele al
usuario que la lance él escribiendo `! <comando>` en el prompt, sigue con todo lo que no dependa de
ella, y vuelve a por el resultado cuando aparezca. El `!` deja la salida en la conversación, así que
después puedes leer logs y comprobar el efecto tú mismo. Ver [[gitops-argocd-selfheal]] y
[[verificar-en-cluster-dev]].
