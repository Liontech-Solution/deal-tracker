---
name: escrituras-contra-prod-las-pide-el-usuario
description: "el clasificador de auto mode bloquea las escrituras contra prod y el merge del repo de manifiestos; no insistas, pídeselas al usuario con el prefijo `!`"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7b3fc60e-45f5-4c57-9ef4-c06e6a29669e
  modified: 2026-08-08T21:57:10.972Z
---

Las acciones que **escriben** en producción o que la despliegan las bloquea el clasificador de auto
mode, aunque el resto de la sesión vaya con permisos. Medido el 08/08/2026 y ampliado el 12/08/2026:

- `kubectl -n deal-tracker-prod create job ... --from=cronjob/...` (la sesión anterior de #278 chocó
  con lo mismo y dejó la casilla sin hacer por eso).
- `gh pr merge` en `k3s-local-apps-manifests`, que es el que llega a prod por ArgoCD.
- `gh release edit <tag> --prerelease=false`, el paso con el que `/validar-qa` asciende una release
  al dar **APTO** (medido el 12/08/2026 en la validación de v0.3.0). Aquí el bloqueo alcanza
  **también a la lectura**: `gh release view` cae igual, así que ni siquiera puedes comprobar el
  flag antes ni después. Es la excepción a la regla de abajo.

Las **lecturas** contra prod sí pasan: `kubectl get/logs/kustomize` y `kubectl exec` con `psql` al pod
de la CNPG para un `SELECT`.

**Why:** reintentar la misma llamada no la desbloquea y quema la sesión; y dejar el trabajo a medias
por eso es peor, porque la casilla vuelve al backlog sin que nadie sepa que solo faltaba un comando.

**How to apply:** en cuanto una de esas dé «Blocked by classifier», no busques rodeo: pídele al
usuario que la lance él escribiendo `! <comando>` en el prompt, sigue con todo lo que no dependa de
ella, y vuelve a por el resultado cuando aparezca. El `!` deja la salida en la conversación, así que
después puedes leer logs y comprobar el efecto tú mismo. Ver [[gitops-argocd-selfheal]] y
[[verificar-en-cluster-dev]].
