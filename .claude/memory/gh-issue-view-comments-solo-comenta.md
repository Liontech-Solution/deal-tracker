---
name: gh-issue-view-comments-solo-comenta
description: "`gh issue view --comments` imprime SOLO los comentarios, no el cuerpo; y en una issue sin comentarios no imprime nada"
metadata: 
  node_type: memory
  type: reference
  originSessionId: c851e851-841a-447f-9c34-10bc9c61bf3c
  modified: 2026-08-05T20:30:31.899Z
---

`gh issue view <n> --comments` **no** muestra el cuerpo de la issue: imprime únicamente los
comentarios. Y si la issue no tiene ninguno, la salida es **vacía**, sin error y con exit 0 — que
se lee igual que un fallo de red o de permisos, y lleva a reintentar la llamada en balde.

**Why:** en este repo el cuerpo es el plan y los comentarios son el estado, así que `revisar-backlog`
necesita las dos cosas. Leer solo `--comments` deja fuera justo la mitad que dice qué pide la issue,
y una salida vacía se confunde con un error del CLI.

**How to apply:** dos llamadas por issue — `gh issue view <n>` para el cuerpo y
`gh issue view <n> --comments` para el estado. Volcarlas a fichero y leerlas de ahí sale mejor que
encadenar pipes: la salida grande se pierde a veces al pasar por `head`. Un `--comments` vacío es
«no hay comentarios», no un fallo: el campo `comments:` de la vista normal lo confirma antes de
gastar otra llamada.

Relacionado: [[buscar-issue-antes-de-abrir]], [[cerrar-issue-desde-el-pr]].
