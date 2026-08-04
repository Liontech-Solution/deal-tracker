---
name: exitworktree-falso-positivo
description: ExitWorktree avisa de «commits que se van a descartar» aunque el PR esté mergeado; con merge commit el sha de la rama nunca coincide con el de main
metadata: 
  node_type: memory
  type: project
  originSessionId: f7134910-25bd-4262-b9e3-badce27e011e
  modified: 2026-08-03T23:23:09.161Z
---

Al cerrar sesión, `ExitWorktree` con `action: "remove"` se niega con *«Worktree has N commits on
<rama>. Removing will discard this work permanently»* **aunque el PR ya esté mergeado**. Aquí los PR
se cierran con `--merge` (merge commit), así que el sha de la rama nunca es un sha de `main` y la
comprobación de la herramienta lo lee como trabajo pendiente. Pasó el 03/08/2026 con `issue-143`.

**Why:** el aviso es idéntico al de un worktree con trabajo de verdad sin subir, que es el caso en
el que borrar sí destruye algo. Tratarlo como ruido por costumbre es exactamente cómo se pierde una
sesión ajena.

**How to apply:** antes de re-invocar con `discard_changes: true`, demostrar que el commit está a
salvo — no basta con que el PR figure como mergeado:

```bash
git -C <repo-canónico> merge-base --is-ancestor <sha-del-worktree> origin/main && echo "está en main"
```

Si sale bien, el `discard_changes` no descarta nada y el mensaje final («Discarded 1 commit») es
igual de engañoso. Si falla, el aviso era real: `action: "keep"`. Ver [[cerrar-sesion]] y
[[verificar-en-cluster-dev]].
