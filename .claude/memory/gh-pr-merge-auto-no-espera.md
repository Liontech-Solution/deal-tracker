---
name: gh-pr-merge-auto-no-espera
description: en este repo los checks no son obligatorios, así que `gh pr merge --auto` mergea al instante en vez de esperar al CI
metadata:
  type: project
---

En `deal-tracker` la protección de rama **no exige ningún check**, así que
`gh pr merge <n> --merge --auto` **no espera al CI: mergea inmediatamente**, aunque
`scraper-ci` esté todavía `QUEUED`. Comprobado el 06/08/2026 en el PR #241 — quedó `MERGED`
al instante con el workflow encolado.

**Why:** `cerrar-sesion` recomienda `--auto` justamente como la salida segura para no mergear en
rojo sin quedarse mirando. Aquí esa garantía no existe, y encima el síntoma engaña por partida
doble: `gh pr checks` responde `no checks reported` (el check aún no se ha registrado) mientras
`mergeStateStatus` ya dice `UNSTABLE`, que es lo único que delata que hay algo encolado. Un merge
a `main` publica imagen y ArgoCD la despliega en `dev`.

**How to apply:** antes de mergear, mira `gh pr view <n> --json statusCheckRollup` en vez de fiarte
de `gh pr checks`: si aparece un `CheckRun` en `QUEUED`/`IN_PROGRESS`, espera con
`gh run watch <id> --exit-status` y mergea después. Si ya se ha mergeado sin esperar, no lo des por
bueno: sigue el run equivalente en `main` hasta verlo verde y dilo. Ver también
[[gh-pr-merge-desde-worktree]], que es el otro error de `gh pr merge` que llega después de haber
hecho la operación.
