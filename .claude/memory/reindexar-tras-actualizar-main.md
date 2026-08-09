---
name: reindexar-tras-actualizar-main
description: "index_repository lee el árbol de trabajo del checkout principal, que NO se actualiza solo al mergear desde un worktree"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 533daaf3-5b39-4898-8312-8a143daa1ed0
  modified: 2026-08-09T21:05:49.505Z
---

Al cerrar sesión desde un worktree, `index_repository` sobre
`/home/juanjocop/Proyectos/deal-tracker` indexa **los ficheros en disco de ese checkout**, no
`origin/main`. Mergear el PR desde el worktree actualiza `origin/main` pero deja el `main` local
del checkout principal donde estaba, así que el reindexado sale silenciosamente viejo.

**Por qué:** el 04/08/2026, tras mergear el PR #162, el checkout principal seguía en `37ac897`
mientras `origin/main` iba por `d706013` — le faltaban mi merge y el #161 de otra sesión. El índice
se construyó igual y devolvió `status: indexed` sin avisar de nada: no hay señal de que el árbol
esté atrasado.

**Cómo aplicarlo:** poner al día el checkout canónico antes de `index_repository`. **Pero desde una
sesión aislada en worktree no se puede**: el sandbox rechaza `git -C /home/juanjocop/Proyectos/deal-tracker …`
(«this command redirects git to the shared checkout via -C»), y pedírselo al usuario con el prefijo
`!` tampoco vale — corre en la misma sesión y falla igual. Medido el 05/08/2026 cerrando #180.

**El veto es al checkout compartido de ESTE repo, no a `git -C` en general.** Medido el 09/08/2026
desde un worktree de deal-tracker: `git -C /home/juanjocop/Proyectos/k3s-local-apps-manifests …`
funciona entero —`commit`, `push`, `rebase`, `branch -D`— porque es **otro repositorio**. Lo que se
rechaza ahí es la forma `cd <ruta absoluta fuera del worktree> && git …`, con «too complex to
verify that it stays inside the worktree», aunque el `git` de detrás fuera legítimo: el filtro mira
la forma del comando, no el destino. O sea que una sesión en worktree **sí puede** llevar entero el
trabajo del repo de manifiestos sin salir; lo único que exige salir es poner al día el `main` del
propio repo.

El orden que sí funciona es **salir del worktree primero**: `ExitWorktree` devuelve la sesión al
checkout principal y allí `git fetch && git merge --ff-only origin/main` va sin problema. O sea que
la limpieza del worktree se adelanta al reindexado, al revés de lo que dice la skill — y es seguro
porque su comprobación (`git status` vacío + `merge-base --is-ancestor HEAD origin/main`) ya se
puede hacer en cuanto el PR está mergeado. Ojo al aviso de `ExitWorktree` sobre commits «a
descartar»: es el falso positivo de [[exitworktree-falso-positivo]].

**Salir no basta: hay que BORRAR el worktree.** Si mergeaste con `gh pr merge --delete-branch`
desde dentro, al desaparecer su rama el worktree se queda en **`main`** — y entonces el checkout
canónico no puede ir a main: `fatal: 'main' is already used by worktree at …`. O sea que la
eliminación del worktree deja de ser el último paso opcional y pasa a ser **requisito del
reindexado**. Medido el 05/08/2026 cerrando #207.

Dos cosas más de ese mismo cierre: el checkout canónico puede estar aparcado en una rama vieja ya
mergeada (estaba en `docs/springfield-pijamas-187`), así que lo que hace falta es `git checkout
main` y no un `merge --ff-only`; y ese checkout lo bloquean los ficheros de `.claude/memory/`
—modificados o sin seguimiento— aunque sean **idénticos** a los de main. Compruébalo antes de
tocarlos (`git show main:<ruta>` y `git diff main -- <ruta>`) y resuélvelo con
`git checkout main -- <ruta>`, nunca a ciegas: ahí vive la memoria de [[memoria-en-repo]].

Y ojo con lo contrario al terminar: si otra sesión mergea *después* de tu reindexado, el grafo
vuelve a quedarse corto — comparar `git log --oneline -1` contra lo indexado y repetir si hace
falta, republicando el ADR otra vez ([[adr-update-por-cli]]).

Relacionado: [[gh-pr-merge-desde-worktree]], [[exitworktree-falso-positivo]],
[[adr-contexto-compartido]].
