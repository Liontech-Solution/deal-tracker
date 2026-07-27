---
name: gitops-argocd-selfheal
description: cambios en cluster deal-tracker van por el repo de manifiestos; ArgoCD tiene selfHeal y revierte kubectl patch
metadata: 
  node_type: memory
  type: project
  originSessionId: 0972b94f-41eb-4449-b43a-8c4fd0bbdfbf
  modified: 2026-07-24T22:49:59.830Z
---

Los manifiestos de deal-tracker viven en `~/Proyectos/k3s-local-apps-manifests`
(repo `github.com/juanjocop/k3s-local-apps-manifests`), estructura kustomize
`deal-tracker/base` + `overlays/{dev,qa}`. La Application de ArgoCD `deal-tracker-qa`
(ns `argocd`) vigila `targetRevision: main`, `path: deal-tracker/overlays/qa`, con
`automated: {prune: true, selfHeal: true}`.

**Un `kubectl patch/edit` en vivo NO persiste**: selfHeal lo revierte en segundos.
Para cambiar algo (p.ej. `suspend` de un cronjob) hay que editar el patch del overlay,
commit y push a `main`, y esperar el sync.

Los cronjobs: `base` los deja `suspend: true`; el overlay de QA los reactiva en
`overlays/qa/patch-{scraper-zara,scraper-sfera,matching}.yaml`. Para pausarlos se
vuelve `suspend: true` en esos patches (hecho 2026-07-25).

`images[].newTag` en `overlays/qa/kustomization.yaml` lo reescribe el workflow
`release-qa.yml` en cada release — no hand-editar.

**Forzar sync sin CLI de argocd** (no está instalado, ver [[kubeconfig-location]]):
`kubectl -n argocd annotate application deal-tracker-qa argocd.argoproj.io/refresh=hard --overwrite`.
Sin eso, el poll normal tarda ~3 min.
