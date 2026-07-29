---
name: cerrar-sesion
description: Cierre de una sesión de trabajo — reindexa el grafo de codebase-memory, actualiza el ADR si la sesión cambió algo estructural, pone al día las issues que se estaban tocando (casillas del checklist, hallazgos sin reportar) y avisa de trabajo sin commitear. Usar siempre que se dé por terminada la sesión, aunque no se pida explícitamente nada de esto: "vamos a cerrar sesión", "lo dejamos por hoy", "ya está por hoy", "me voy", "terminamos", o cualquier señal de que se cierra el terminal.
---

# Cerrar sesión

El grafo de `codebase-memory` es una **foto del momento**: no se actualiza mientras se trabaja.
Si la sesión acaba sin reindexar, la siguiente arranca consultando un índice viejo — y lo peor no
es que falte información, es que responde con seguridad sobre código que ya no existe.

El ADR es lo que evita releer medio repo para entender el proyecto. Mantenerlo cierto es el
verdadero objetivo de esta skill; reindexar es solo el trámite barato.

## 1. Reindexar

`index_repository` en modo **`full`**. No uses `fast` en deal-tracker: excluye `db/migrations/` y
`tests/fixtures/`, que son justo el contrato del proyecto.

Reindexa solo lo que la sesión tocó:

- `/home/juanjocop/Proyectos/deal-tracker`
- `/home/juanjocop/Proyectos/k3s-local-apps-manifests` (si se tocaron manifiestos)

El reindexado es incremental y tarda segundos, así que no merece la pena averiguar antes si hace
falta. `detect_changes` no sirve para eso: `index_status` lee el git en vivo, así que
`base_sha == head_sha` siempre y devuelve 0 cambios aunque el índice esté caducado.

## 2. El ADR vive en un fichero, el grafo es solo la copia consultable

La fuente de verdad del ADR es el fichero versionado de cada repo:

- `deal-tracker/.claude/adr/deal-tracker.md`
- `k3s-local-apps-manifests/.claude/adr/k3s-local-apps-manifests.md`

Está en git porque el grafo es **local a cada equipo**: es lo que permite reconstruirlo en el otro
portátil y revisarlo en un PR como cualquier otro documento.

De paso, la salida de `index_repository` trae `adr_present`. Si alguna vez viene `false`, el grafo
ha perdido el ADR y se re-publica desde el fichero:

```bash
codebase-memory-mcp cli manage_adr --project <proyecto> --mode update \
  --content "$(cat .claude/adr/<proyecto>.md)"
```

## 3. ¿Cambia el ADR lo aprendido hoy?

Este es el paso que importa, y el que exige criterio. Pregúntate qué habría ahorrado tiempo saber
al empezar la sesión de hoy. Merece entrar en el ADR:

- Una decisión de arquitectura tomada hoy, **con su porqué** y las alternativas descartadas.
- Un contrato nuevo o cambiado entre los dos servicios, o entre este repo y el de manifiestos.
- Una restricción descubierta a base de chocar con ella (un límite del cluster, un antibot, un
  comportamiento raro de una herramienta) y el número medido que la respalda.
- Un drift entre repos, del tipo "esto está en el código pero no en los manifiestos".

**No** merece entrar: qué ficheros se tocaron, qué bug se arregló, ni nada que el `git log` ya
cuente. El ADR no es un diario — es lo que sigue siendo cierto dentro de tres meses. Si la sesión
fue rutinaria, lo correcto es no tocarlo y decirlo.

Si hay que actualizarlo: edita el fichero de `.claude/adr/` (no el grafo directamente), re-publica
con `manage_adr` en modo `update` pasándole el contenido del fichero, y deja el fichero listo para
commitear. Así el fichero y el grafo nunca divergen.

## 4. Poner al día las issues que se estaban tocando

Lo que se descubre durante una sesión y solo se dice en la conversación **se pierde al cerrar**.
La issue es el único sitio donde sobrevive para la siguiente persona (o para ti dentro de un mes).

Identifica sobre qué issues iba la sesión: el nombre de la rama, los mensajes de commit, los PR
abiertos, o lo que se haya mencionado explícitamente. Si no está claro, `gh issue list --assignee
@me` en los repos tocados.

Para cada una, `gh issue view <n>` y revisa:

- **Casillas del checklist.** Marca las que de verdad quedaron hechas. Una a medias se queda sin
  marcar: marcarla "porque casi" es peor que dejarla, porque la siguiente sesión se la salta.
- **Lo que se quedó fuera.** Si aparecieron trabajo, un bloqueo o una decisión pendiente que no
  estaban en la issue, coméntalo ahí. Basta una frase con el hallazgo y por qué importa.
- **Si la issue ha quedado desfasada.** A veces la sesión demuestra que lo que pide ya no aplica o
  está mal planteado. Decirlo en un comentario vale más que dejarla pudrirse.

No cierres issues por tu cuenta ni marques casillas a bulto: si dudas de si algo cuenta como hecho,
pregunta en vez de decidirlo tú.

## 5. Cerrar el trabajo pendiente

Antes de despedirte, mira `git status` en los repos tocados y di explícitamente:

- Qué queda sin commitear, y si debería commitearse o es basura de pruebas.
- Qué ramas quedan sin mergear o qué PR quedan abiertos.
- Si el ADR ha cambiado, que ese fichero también va en el commit.

No commitees por tu cuenta salvo que se te pida. El objetivo es que no se pierda nada por olvido,
no decidir por el usuario.

## Qué NO hacer

No reescribas el ADR entero por costumbre: reescribirlo cada día lo convierte en prosa genérica y
se pierde precisamente lo que lo hacía útil, que son los detalles concretos y medidos.

Tampoco des por hecho que el reindexado fue bien. Mira `nodes`/`edges` y `adr_present` en la
salida; un índice a cero o un ADR perdido en silencio son peores que no haber reindexado, porque
la siguiente sesión confiará en ellos.
