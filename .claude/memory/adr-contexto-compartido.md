---
name: adr-contexto-compartido
description: Hay ADR indexados en codebase-memory para deal-tracker y k3s-local-apps-manifests; ahí vive el contrato entre ambos repos
metadata: 
  node_type: memory
  type: project
  originSessionId: b87433e3-a8e3-41d8-beb2-5710fece9ab5
  modified: 2026-07-29T10:45:15.902Z
---

El 2026-07-29 se indexaron en el MCP `codebase-memory` dos proyectos y se les escribió un ADR:
`home-juanjocop-Proyectos-deal-tracker` y `home-juanjocop-Proyectos-k3s-local-apps-manifests`.
Se consultan con `manage_adr(project=..., mode='get')`.

El ADR no repite los CLAUDE.md: recoge el **seam entre los dos repos**, que ningún CLAUDE.md
documenta entero — promoción dev (`sha-<7>`, bump automático) → QA (semver, por digest vía
`release-qa.yml` manual), contrato del SealedSecret `deal-tracker-config`, convención de
defaults seguros en `base/` que el overlay de QA levanta, y el mapa de qué repo posee qué
(Cilium y labels de nodo → `kxs-ansible`, backups Longhorn → `toolsuite-platform-gitops`,
rutas públicas → panel de Zero Trust, no Git).

**Why:** el despliegue de deal-tracker vive en otro repo, así que trabajar solo en uno deja
la mitad del sistema fuera de contexto.

**How to apply:** al tocar CI, imágenes, migraciones o cualquier cosa que cruce a k8s, leer el
ADR antes de asumir. El modo `cross-repo-intelligence` no aporta nada aquí (0 aristas: el repo
de manifiestos es YAML, sin rutas HTTP que enlazar) — el ADR es el mecanismo, no las aristas.
Reindexar con `index_repository` tras cambios grandes. Relacionado: [[gitops-argocd-selfheal]],
[[verificar-en-cluster-dev]].
