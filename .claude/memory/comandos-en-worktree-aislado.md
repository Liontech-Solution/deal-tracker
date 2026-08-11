---
name: comandos-en-worktree-aislado
description: "En un worktree el clasificador rechaza el prefijo VAR=valor y los heredoc con cd; usa `env`, y el pod de la CNPG tiene el filesystem en solo lectura"
metadata: 
  node_type: memory
  type: project
  originSessionId: 299671df-4011-4d32-b2b1-df4fc0d7fa27
  modified: 2026-08-11T10:13:09.398Z
---

Trabajando dentro de un worktree, el clasificador de comandos rechaza cosas que fuera pasan, con el
mensaje *«too complex to verify that it stays inside the worktree»*. No es un permiso que falte: es
la forma del comando. Dos que cuestan varios intentos si no se sabe:

- **El prefijo de asignación de variable.** `KUBECONFIG=~/.kube/x kubectl ...` o
  `TEST_DATABASE_URL=... pnpm test` se rechazan. Con **`env`** delante pasan:
  `env TEST_DATABASE_URL=... TEST_DATABASE_URL_CTYPE_C=... pnpm test`. Y para `kubectl` ni hace
  falta: `~/.kube/config` ya es el symlink a `k3slocal.yaml`, así que el `kubectl` pelado funciona
  (ver [[kubeconfig-location]]).

  **Pero `env` solo salva el caso simple.** Si el comando que envuelve lleva flags, vuelve a
  rechazarse: *«runs env with -n, whose effect on the command it wraps can't be verified»*. Y
  cambiar `-n` por `--namespace=` tampoco vale, porque la queja es que hay un flag, no su forma. La
  salida es **no usar `env`** y pasarle la configuración al propio comando con su flag:
  `kubectl --kubeconfig=/home/juanjocop/.kube/k3slocal.yaml --namespace=data-dev exec ...`. Con
  rutas absolutas, que dentro de `env` el `~` no se expande.
- **`cd X && cat > fichero <<EOF`** también se rechaza. Para escribir un fichero, la herramienta
  `Write`; los redirects simples (`cmd > fichero`) sí pasan.
- **Encadenar `sleep` está bloqueado**, y esto no es del worktree: `sleep 45 && gh pr view ...` se
  rechaza pidiendo un bucle. Para esperar al CI, la condición dentro de un `until`:
  `until gh pr view N --json statusCheckRollup -q '.statusCheckRollup[].status' | grep -qv IN_PROGRESS; do sleep 10; done`
- **Los ficheros de fuera del worktree no se pueden editar** desde una sesión aislada, y la memoria
  es uno: `~/.claude/projects/.../memory/` es un symlink al `.claude/memory/` del checkout principal
  (ver [[memoria-en-repo]]), así que `Edit` sobre esa ruta se rechaza. Hay que editar **la copia del
  worktree** (`<worktree>/.claude/memory/...`), y eso significa que una nota de memoria necesita su
  propia rama y su commit, porque si no se cuela en el PR de la issue que estés haciendo.

**Y el pod de la CNPG tiene el filesystem en solo lectura**, así que `kubectl cp` de un `.sql` falla
con `tar: Cannot open: Read-only file system` — ni siquiera en `/tmp`. Las consultas largas contra
`deal_tracker_qa` van con `psql -c "..."` en **una sola línea**, que sí admite sentencias grandes
(probado con un `CASE` de 20 ramas). Ver [[verificar-en-cluster-dev]] para el resto del camino.
