---
name: gh-pr-merge-desde-worktree
description: "gh pr merge desde un worktree falla al final con «main is already used by worktree», pero el merge SÍ se hizo y la rama remota NO se borra"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ac186882-11d8-4a3c-b9db-e8b3a5090b7c
  modified: 2026-08-04T10:21:22.165Z
---

`gh pr merge <n> --merge --delete-branch` lanzado **desde un worktree** termina con:

```
failed to run git: fatal: 'main' is already used by worktree at '/home/juanjocop/Proyectos/deal-tracker'
```

Parece que ha fallado el merge y no es eso. `gh` mergea por la API —eso ya está hecho— y luego
intenta hacer checkout local de `main` para borrar la rama; ese checkout es el que revienta, porque
`main` está ocupado por el directorio canónico (ver [[verificar-en-cluster-dev]], que explica por qué
aquí hay siempre varios worktrees).

**Why:** reaccionar al error reintentando el merge es perder el tiempo con algo ya hecho, y peor:
como el paso que falló era el del `--delete-branch`, la rama remota **se queda viva** y el cierre de
sesión da por limpio lo que no lo está.

**How to apply:** ante ese error, comprobar el estado real antes de tocar nada —
`gh pr view <n> --json state,mergedAt,mergeCommit`— y si sale `MERGED`, lo único pendiente es borrar
la rama a mano:

```bash
gh api -X DELETE repos/<owner>/<repo>/git/refs/heads/<rama>
```

Ojo con `gh api .../branches` justo después: devolvió la rama todavía listada cuando ya estaba
borrada. La comprobación fiable es pedir la ref concreta y ver un 404
(`gh api repos/<owner>/<repo>/git/ref/heads/<rama>`). Medido el 04/08/2026 cerrando #151.

El `git fetch --prune` local no hace falta si el worktree se va a borrar de todas formas — y ojo,
que en modo automático puede estar bloqueado por el clasificador.
