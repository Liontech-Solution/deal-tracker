---
name: reindexar-tras-actualizar-main
description: "index_repository lee el árbol de trabajo del checkout principal, que NO se actualiza solo al mergear desde un worktree"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 533daaf3-5b39-4898-8312-8a143daa1ed0
  modified: 2026-08-05T11:30:04.112Z
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

El orden que sí funciona es **salir del worktree primero**: `ExitWorktree` devuelve la sesión al
checkout principal y allí `git fetch && git merge --ff-only origin/main` va sin problema. O sea que
la limpieza del worktree se adelanta al reindexado, al revés de lo que dice la skill — y es seguro
porque su comprobación (`git status` vacío + `merge-base --is-ancestor HEAD origin/main`) ya se
puede hacer en cuanto el PR está mergeado. Ojo al aviso de `ExitWorktree` sobre commits «a
descartar»: es el falso positivo de [[exitworktree-falso-positivo]].

Y ojo con lo contrario al terminar: si otra sesión mergea *después* de tu reindexado, el grafo
vuelve a quedarse corto — comparar `git log --oneline -1` contra lo indexado y repetir si hace
falta, republicando el ADR otra vez ([[adr-update-por-cli]]).

Relacionado: [[gh-pr-merge-desde-worktree]], [[exitworktree-falso-positivo]],
[[adr-contexto-compartido]].
