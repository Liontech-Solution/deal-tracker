---
name: escrituras-contra-prod-las-pide-el-usuario
description: "consultar prod lo puedes hacer tú (permiso permanente del usuario, 18/08/2026); escribir en prod o mergear los repos de GitOps lo bloquea el clasificador y se pide con `!`; lo que necesite modificar algo se reproduce en QA"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7b3fc60e-45f5-4c57-9ef4-c06e6a29669e
  modified: 2026-08-18T10:35:29.659Z
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

~~**Pero `kubectl exec` con `psql` contra `deal_tracker_prod` NO**~~ **— caducado el 18/08/2026, y
además hay permiso permanente.** Se midió bloqueado el 13/08/2026 en #314 (un `EXPLAIN (ANALYZE,
BUFFERS)` de solo lectura, en dos formas distintas, mientras el mismo script contra
`deal_tracker_qa` sí pasaba). El 18/08/2026, en #357, un `psql` de solo lectura contra
`deal_tracker_prod` por `kubectl exec` **pasó a la primera**, y varias veces seguidas.

Y por encima del clasificador, **el usuario ha dado permiso explícito y permanente** (18/08/2026):
*consultar* prod se puede hacer directamente, **sin pedírselo y sin `!`**. Lo que no puede haber es
**ninguna modificación**: si una prueba necesita tocar algo —aunque sea temporal, idempotente o
reversible— **se reproduce en QA**, no en prod. Esa es la línea, y no es el clasificador quien la
pone: es una instrucción del usuario, así que vale aunque el comando pasara.

**Y las ESCRITURAS por `psql` caen también contra dev y QA** (medido el 14/08/2026 en #370). Un
`ALTER TABLE variant SET (log_autovacuum_min_duration = 0)` contra `deal_tracker` y
`deal_tracker_qa` —idempotente, reversible con un `RESET` y la misma sentencia que iba a aplicar la
migración— se bloqueó igual. O sea que el eje no es solo la base de destino: contra dev/QA las
lecturas pasan y las escrituras no. Para un cambio de esquema eso no estorba, es el camino correcto
(la migración se despliega, no se aplica a mano); pero si lo que quieres es *observar* algo en el
entorno donde el fallo está vivo, cuenta desde el principio con pedírselo al usuario.

Consecuencia práctica: si necesitas **medir** algo contra prod —un `EXPLAIN`, un recuento—, hazlo tú
directamente; ya no hay que preparárselo al usuario. Lo que sí sigue siendo suyo es todo lo que
**escriba** o despliegue (el `create job` contra prod, los merges de los repos de GitOps), y ahí
sigue valiendo el `!`.

**Why:** reintentar la misma llamada no la desbloquea y quema la sesión; y dejar el trabajo a medias
por eso es peor, porque la casilla vuelve al backlog sin que nadie sepa que solo faltaba un comando.

**How to apply:** en cuanto una de esas dé «Blocked by classifier», no busques rodeo: pídele al
usuario que la lance él escribiendo `! <comando>` en el prompt, sigue con todo lo que no dependa de
ella, y vuelve a por el resultado cuando aparezca. El `!` deja la salida en la conversación, así que
después puedes leer logs y comprobar el efecto tú mismo. Ver [[gitops-argocd-selfheal]] y
[[verificar-en-cluster-dev]].
