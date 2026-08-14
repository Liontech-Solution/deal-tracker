---
name: numeros-de-issue-y-pr-son-la-misma-serie
description: no escribas el numero de una issue antes de crearla; con sesiones en paralelo el hueco que reservas mentalmente se lo lleva el PR de otra
metadata: 
  node_type: memory
  type: project
  originSessionId: 3968c4a0-ab62-41bc-9ead-983d1835c1de
  modified: 2026-08-14T21:28:59.132Z
---

En GitHub las issues y los PR **comparten la misma numeración**, así que con varias sesiones
trabajando a la vez sobre este repo no puedes predecir el número de una issue que aún no has creado:
entre que lo calculas y la abres, otra sesión abre un PR y se lleva ese número.

Pasado el 14/08/2026 cerrando #354: escribí «se fue a #418» en el ADR para un hallazgo que iba a
sacar a issue propia, y #418 resultó ser el PR de la sesión S11. La issue salió #419. Estuvo a punto
de quedar una referencia cruzada falsa en un documento que se lee durante meses.

**Cómo aplicarlo:** crea la issue **primero**, con `gh issue create`, y solo entonces escribe su
número en el ADR, en el commit o en el comentario que la referencia. Si el texto tiene que ir antes
por lo que sea, déjalo sin número y vuelve a pasar por él.

Es el mismo motivo por el que conviene [[buscar-issue-antes-de-abrir]], y del mismo bloque de
cuidados que [[adr-update-por-cli]]: lo que dos sesiones escriben a la vez se pisa sin dar error.
