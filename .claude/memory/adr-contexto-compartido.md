---
name: adr-contexto-compartido
description: "ADR indexados en codebase-memory para deal-tracker y k3s-local-apps-manifests; ahí vive el contrato entre ambos repos, versionado en .claude/adr/"
metadata: 
  node_type: memory
  type: project
  originSessionId: b87433e3-a8e3-41d8-beb2-5710fece9ab5
  modified: 2026-07-29T21:06:43.159Z
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

El grafo es local a cada equipo, así que el ADR se versiona como fichero en `.claude/adr/` de
cada repo: es lo que permite reconstruirlo en el otro portátil y revisarlo en un PR. La skill
`cerrar-sesion` se encarga de mantenerlo al día al terminar.

**El respaldo en fichero no es paranoia: el grafo pierde el ADR de verdad.** Visto dos veces el
2026-07-29, en los dos proyectos. El patrón que encaja con ambos casos: un ADR escrito por el
**servidor MCP** desaparece en el **primer `index_repository` posterior**; los re-publicados con
el **CLI** (`codebase-memory-mcp cli manage_adr --mode update`) han sobrevivido a todos los
reindexados siguientes. Por eso conviene re-publicar por CLI y mirar siempre `adr_present` en
la salida del indexado.

Dos detalles del indexador que cuestan tiempo redescubrir: el modo `fast` **excluye
`db/migrations/` y `tests/fixtures/`** (justo el contrato de deal-tracker), así que para este
repo hay que reindexar en `full`. Y `detect_changes` no vale como señal de caducidad, porque
`index_status` lee el git en vivo: `base_sha == head_sha` siempre y devuelve 0 cambios aunque
el índice esté viejo. Reindexar es incremental y tarda segundos, así que sale más barato
hacerlo siempre que intentar detectar si procede.

**Why:** el despliegue de deal-tracker vive en otro repo, así que trabajar solo en uno deja
la mitad del sistema fuera de contexto.

**How to apply:** al tocar CI, imágenes, migraciones o cualquier cosa que cruce a k8s, leer el
ADR antes de asumir. El modo `cross-repo-intelligence` no aporta nada aquí (0 aristas: el repo
de manifiestos es YAML, sin rutas HTTP que enlazar). Relacionado: [[gitops-argocd-selfheal]],
[[verificar-en-cluster-dev]], [[memoria-en-repo]].
