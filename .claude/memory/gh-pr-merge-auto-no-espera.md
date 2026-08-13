---
name: gh-pr-merge-auto-no-espera
description: "en este repo los checks no son obligatorios, así que `gh pr merge --auto` mergea al instante en vez de esperar al CI"
metadata: 
  node_type: memory
  type: project
  originSessionId: 988a575e-48f6-4310-87fc-2eee639eea1a
  modified: 2026-08-13T09:52:01.754Z
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

**Y no descartes ningún check por su nombre.** El job se llama
**`build & push (multiarch en main)`** y eso invita a leerlo como que en un PR no corre — CLAUDE.md
refuerza el malentendido diciendo que «el multiarch corre en main». **Sí corre en el PR**: lo que
condiciona `github.event_name == 'push'` son las *plataformas* y el `push` a GHCR
(`platforms: ... && 'linux/amd64,linux/arm64' || 'linux/amd64'`, `push: ${{ ... == 'push' }}`), no
el job. O sea que en un PR compila amd64 como validación real y tarda ~1 min más que
`lint-type-test`. Verlo `pending` y darlo por fantasma es mergear sin la validación de imagen —
comprobado el 13/08/2026 en el PR #359, donde acabó en `pass`. Los que sí se saltan de verdad son
los `bump GitOps (dev)`, que salen como `skipping`.
