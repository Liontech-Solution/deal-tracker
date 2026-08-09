---
name: verificar-arquitectura-imagen
description: "Sin buildx; el token de gh YA tiene read:packages (desde 09/08/2026), y el registro se lee con un token de registro sacado por basic auth"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d12b5869-f106-451f-93b4-72fbf4879073
  modified: 2026-08-09T00:00:00.000Z
---

En este equipo **no hay `docker buildx`**, así que `docker buildx imagetools inspect` no sirve para
comprobar si una imagen publicada es multiarch.

**El token de `gh` ya tiene `read:packages`** — añadido el 09/08/2026 con
`gh auth refresh -h github.com -s read:packages`. Es un OAuth de la app GitHub CLI (`gho_`, en
`~/.config/gh/hosts.yml`), **no un PAT**: no se edita desde la web, solo con `auth refresh`. Sin ese
scope la API de packages daba 403 y el registro anónimo devolvía 0 tags, que engaña porque parece un
package vacío en vez de un permiso que falta.

Con el scope hay dos APIs, y hacen falta **las dos**:

```bash
# 1) qué versiones y qué tags hay (la API de GitHub)
gh api '/orgs/liontech-solution/packages/container/<img>/versions?per_page=100' --paginate \
  --jq '.[] | [.id, .created_at, (.metadata.container.tags|join(","))] | @tsv'

# 2) qué hijos tiene una manifest list (la API del registro; token aparte, por basic auth)
TOK=$(gh auth token)
REG=$(curl -s -u "juanjocop:$TOK" \
  "https://ghcr.io/token?scope=repository:liontech-solution/<img>:pull&service=ghcr.io" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $REG" \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  "https://ghcr.io/v2/liontech-solution/<img>/manifests/<tag-o-digest>"
```

Dos cosas que no son obvias y cuestan un rato descubrir:

- **El token de `gh` no vale directo contra `ghcr.io/v2`**: hay que canjearlo por un token de
  registro en `ghcr.io/token` con *basic auth* (`-u usuario:token`). Sin eso, `DENIED`.
- **Una versión no es una imagen**: cada build multiarch deja 3 versiones (la lista + 2 hijos sin
  tag), y los hijos solo se atan a su padre resolviendo el manifiesto — por timestamp **no**, porque
  se crean 1-2 s antes que la lista y un cruce por segundo exacto los da todos por huérfanos.

La vía que no depende de credenciales sigue valiendo y es más barata: **mirar en qué nodo arrancó el
pod**. El cluster es mixto (2 nodos amd64, 6 arm64 — ver [[kubeconfig-location]]), así que si el pod
queda `Running` en un nodo arm64, el manifiesto arm64 existe por fuerza.

```bash
kubectl get pods -n deal-tracker-dev -o wide | grep web
kubectl get node <nodo> -o jsonpath='{.status.nodeInfo.architecture}'
```

**Why:** el truco del nodo salió de verificar #61 el 01/08/2026 (el pod cayó en `worker3`, arm64).
Las recetas del registro salieron de #283 el 09/08/2026, auditando la retención de GHCR, donde hacía
falta el manifiesto de verdad y no bastaba con saber que arrancaba.

**How to apply:** para «¿se publicó bien el multiarch?», el nodo basta y no cuesta nada. Para
cualquier cosa sobre **qué hay guardado en GHCR** (retención, tags, digests compartidos, hijos
huérfanos), hacen falta las dos APIs de arriba. Relacionado: [[gitops-argocd-selfheal]],
[[verificar-en-cluster-dev]].
