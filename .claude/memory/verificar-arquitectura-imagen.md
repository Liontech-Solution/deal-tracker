---
name: verificar-arquitectura-imagen
description: "Sin buildx ni scope read:packages; para comprobar que una imagen es multiarch, mirar en qué nodo arranca el pod"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d12b5869-f106-451f-93b4-72fbf4879073
  modified: 2026-08-01T18:24:42.074Z
---

En este equipo **no hay `docker buildx`**, así que `docker buildx imagetools inspect` no sirve para
comprobar si una imagen publicada es multiarch. Y la API de GHCR
(`https://ghcr.io/v2/liontech-solution/<img>/manifests/<tag>`) devuelve `DENIED` con el token de
`gh` por defecto: le falta el scope `read:packages`, que se añade con
`gh auth refresh -s read:packages`.

La vía que no depende de credenciales: **mirar en qué nodo arrancó el pod**. El cluster es mixto
(2 nodos amd64, 6 arm64 — ver [[kubeconfig-location]]), así que si el pod queda `Running` en un nodo
arm64, el manifiesto arm64 existe por fuerza.

```bash
kubectl get pods -n deal-tracker-dev -o wide | grep web
kubectl get node <nodo> -o jsonpath='{.status.nodeInfo.architecture}'
```

**Why:** verificado así el criterio pendiente de la issue #61 el 01/08/2026, tras el push de #68 a
`main`; el pod cayó en `worker3` (arm64) y con eso quedó probado sin tocar GHCR.

**How to apply:** ante «¿se publicó bien el multiarch?», no pelearse con buildx ni con el token —
esperar al despliegue y mirar el nodo. Si hiciera falta el manifiesto de verdad, primero
`gh auth refresh -s read:packages`. Relacionado: [[gitops-argocd-selfheal]],
[[verificar-en-cluster-dev]].
