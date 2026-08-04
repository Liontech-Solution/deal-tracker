---
name: reindexar-tras-actualizar-main
description: "index_repository lee el árbol de trabajo del checkout principal, que NO se actualiza solo al mergear desde un worktree"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 533daaf3-5b39-4898-8312-8a143daa1ed0
  modified: 2026-08-04T14:03:25.088Z
---

Al cerrar sesión desde un worktree, `index_repository` sobre
`/home/juanjocop/Proyectos/deal-tracker` indexa **los ficheros en disco de ese checkout**, no
`origin/main`. Mergear el PR desde el worktree actualiza `origin/main` pero deja el `main` local
del checkout principal donde estaba, así que el reindexado sale silenciosamente viejo.

**Por qué:** el 04/08/2026, tras mergear el PR #162, el checkout principal seguía en `37ac897`
mientras `origin/main` iba por `d706013` — le faltaban mi merge y el #161 de otra sesión. El índice
se construyó igual y devolvió `status: indexed` sin avisar de nada: no hay señal de que el árbol
esté atrasado.

**Cómo aplicarlo:** antes de `index_repository`, comprobar y poner al día el checkout canónico
(`git -C /home/juanjocop/Proyectos/deal-tracker status --short` para confirmar que está limpio, y
`git -C ... merge --ff-only origin/main`). Y ojo con lo contrario al terminar: si otra sesión
mergea *después* de tu reindexado, el grafo vuelve a quedarse corto — comparar `git log --oneline -1`
contra lo indexado y repetir si hace falta, republicando el ADR otra vez ([[adr-update-por-cli]]).

Relacionado: [[gh-pr-merge-desde-worktree]], [[exitworktree-falso-positivo]],
[[adr-contexto-compartido]].
