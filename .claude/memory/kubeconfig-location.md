---
name: kubeconfig-location
description: Ruta del kubeconfig del cluster k3s local en cada equipo (se trabaja desde dos)
metadata: 
  node_type: memory
  type: reference
  originSessionId: 5e6b9c8b-2e34-4756-86ce-3eea453dc60a
  modified: 2026-07-23T23:02:12.869Z
---

CLAUDE.md documenta el kubeconfig como `~/.kube/k3slocal.yaml`. Este `.claude` se usa en **dos equipos**.

En el equipo actual el fichero estaba como `~/.kube/config` (default de kubectl). El 2026-07-24 se
renombró a `~/.kube/k3slocal.yaml` (nombre canónico de CLAUDE.md) y se dejó un symlink
`~/.kube/config → k3slocal.yaml` para que `kubectl` a secas siga funcionando. Contexto: `default`.

Para comandos, usar `--kubeconfig "$HOME/.kube/k3slocal.yaml"` (funciona en ambos equipos). `argocd`
CLI no está instalado; inspeccionar ArgoCD vía `kubectl -n argocd get application ...`.
