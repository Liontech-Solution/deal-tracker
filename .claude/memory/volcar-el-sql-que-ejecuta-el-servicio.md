---
name: volcar-el-sql-que-ejecuta-el-servicio
description: "Para un EXPLAIN fiel hay que sacar el SQL del propio servicio (db de pega + PgDialect en un spec de vitest), no reescribirlo a mano ni medirlo con parámetros"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1c0ec96c-fd3f-4b9f-ba43-5ccf92303aba
  modified: 2026-08-12T22:58:54.763Z
---

El ADR repite como lección de método de #307 que hay que **medir la consulta que ejecuta el
servicio, no una escrita a mano**. Cómo sacarla no está escrito en ninguna parte, y reconstruirlo
cuesta media hora.

La consulta de `listProducts()` se arma inline en una plantilla `sql` de Drizzle, así que no hay
función que devuelva el texto. Se saca sin base de datos: `new CatalogService(fakeDb)` con un `db`
de pega cuyo `execute(q)` captura la plantilla y devuelve `[]`, y luego
`new PgDialect().sqlToQuery(captured)` da el texto con `$1..$n` y sus parámetros.

Tres detalles sin los cuales el volcado no sirve:

- **Los parámetros hay que meterlos en línea.** `psql` no acepta `$1` sueltos, y además con
  parámetros genéricos el planificador puede elegir **otro plan** (generic plan) que no es el que
  sufre el usuario.
- **Quitar los comentarios `--` antes de plegar a una línea**, o el primero comenta el resto. Está
  contado en [[comandos-en-worktree-aislado]].
- **Hace falta un runner de TS y aquí no hay `tsx` ni `npx`** (ver [[mcp-sin-npx-pnpm]]). La salida
  barata es un spec de usar y tirar en `services/web/test/` —vitest ya está configurado con SWC— que
  escriba el SQL a un fichero del scratchpad. Nace para borrarse: **acuérdate de borrarlo antes de
  commitear**, que `test/**` sí entra en el `include` de `vitest.config.ts`.

Con eso, el `EXPLAIN (ANALYZE, BUFFERS)` va contra la base del cluster por `psql` en el pod de la
CNPG, que es lectura y no necesita token — mucho más cómodo que medir por HTTP, sobre todo contra
prod, donde el token dura 300 s. Y el plan hay que medirlo **en el cluster**: el ADR tiene un
apartado entero sobre que un plan medido en el portátil no predice el del cluster.
