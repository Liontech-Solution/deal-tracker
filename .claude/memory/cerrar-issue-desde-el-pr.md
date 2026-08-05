---
name: cerrar-issue-desde-el-pr
description: "«Cierra #N» en el cuerpo del PR no cierra nada — GitHub solo entiende las palabras clave en inglés"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ac186882-11d8-4a3c-b9db-e8b3a5090b7c
  modified: 2026-08-05T11:36:45.690Z
---

En este repo todo se escribe en español (commits, PR, issues), así que sale solo poner **«Cierra
#151»** al principio del cuerpo del PR. GitHub **no lo reconoce**: solo autocierra con
`close/closes/closed`, `fix/fixes/fixed`, `resolve/resolves/resolved`. El PR se mergea, la issue se
queda abierta y nadie se entera.

**Why:** una issue cuyo trabajo ya está en `main` pero sigue abierta es exactamente lo que
`revisar-backlog` no puede detectar leyendo títulos — la propone como candidata y se planifica algo
que ya está hecho. Medido el 04/08/2026: el PR #154 decía «Cierra #151» y hubo que cerrarla a mano
en el paso 5 de `cerrar-sesion`.

**How to apply:** dos opciones, y la segunda es la que encaja con el idioma del repo:

- escribir la keyword en inglés (`Closes #151`) aunque el resto del cuerpo vaya en español, o
- dejar el texto en español y **cerrar explícitamente al cerrar sesión**:
  `gh issue close <n> --reason completed --comment "…"`.

Lo que no vale es dar por cerrada la issue porque el PR la mencione. Comprobarlo cuesta un comando:
`gh issue view <n> --json state`. Y ojo con cerrarla por tu cuenta — [[buscar-issue-antes-de-abrir]]
y la propia skill de cierre dicen que la decisión de cerrar es del usuario, no del agente.

**Cuándo comprobarlo, que es lo que falla:** en el mismo momento de comentar la issue en el paso 5,
no al final. El 05/08/2026 (PR #206, issue #180) volvió a pasar exactamente igual que con la #151:
escribí un comentario largo diciendo «hecho», di el cierre por bueno y la issue seguía abierta —
lo cazó el usuario, no yo. Comentar y comprobar el estado van juntos: si estás escribiendo «esto ya
está hecho», ese es el momento de mirar si además está cerrada y de preguntar si toca cerrarla.
