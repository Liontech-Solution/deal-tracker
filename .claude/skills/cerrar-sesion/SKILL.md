---
name: cerrar-sesion
description: Cierre de una sesión de trabajo — reindexa el grafo de codebase-memory, actualiza el ADR si la sesión cambió algo estructural, deja por escrito en las issues todo lo pendiente y los hallazgos nuevos (abriendo issue si se salen del scope), avisa de trabajo sin commitear y limpia lo que sobra: worktrees de la sesión y ramas ya mergeadas. Usar siempre que se dé por terminada la sesión, aunque no se pida explícitamente nada de esto: "vamos a cerrar sesión", "lo dejamos por hoy", "ya está por hoy", "me voy", "terminamos", o cualquier señal de que se cierra el terminal.
---

# Cerrar sesión

El grafo de `codebase-memory` es una **foto del momento**: no se actualiza mientras se trabaja.
Si la sesión acaba sin reindexar, la siguiente arranca consultando un índice viejo — y lo peor no
es que falte información, es que responde con seguridad sobre código que ya no existe.

El ADR es lo que evita releer medio repo para entender el proyecto. Mantenerlo cierto es el
verdadero objetivo de esta skill; reindexar es solo el trámite barato.

Y por encima de todo lo demás hay una regla que ordena el resto: **la conversación no es un sitio
donde guardar cosas**. Cuando se cierra el terminal desaparece entera, y lo que solo se dijo aquí
—un pendiente, un porqué, un hallazgo de camino— se pierde sin dejar rastro de que existió. Así
que nada puede terminar la sesión con trazabilidad únicamente de sesión: cada cosa viva se queda
escrita donde se la va a volver a encontrar (la issue, el ADR, un commit) o deja de existir. Al
recorrer los pasos siguientes, esa es la pregunta de fondo: *si mañana no me acuerdo de nada de
hoy, ¿esto sigue estando en algún sitio?*

## 1. Reindexar

`index_repository` en modo **`full`**. No uses `fast` en deal-tracker: excluye `db/migrations/` y
`tests/fixtures/`, que son justo el contrato del proyecto.

Reindexa solo lo que la sesión tocó:

- `/home/juanjocop/Proyectos/deal-tracker`
- `/home/juanjocop/Proyectos/k3s-local-apps-manifests` (si se tocaron manifiestos)

Esas rutas son las de siempre, no la de donde estés parado: si la sesión ha ido en un worktree, el
`cwd` es `.claude/worktrees/<algo>` e indexar eso metería en el grafo una copia que vas a borrar
dentro de un rato. Indexa el repo canónico, y hazlo con el trabajo ya mergeado en `main`, que es lo
que la siguiente sesión se va a encontrar.

El reindexado es incremental y tarda segundos, así que no merece la pena averiguar antes si hace
falta. `detect_changes` no sirve para eso: `index_status` lee el git en vivo, así que
`base_sha == head_sha` siempre y devuelve 0 cambios aunque el índice esté caducado.

## 2. El ADR vive en un fichero, el grafo es solo la copia consultable

La fuente de verdad del ADR es el fichero versionado de cada repo:

- `deal-tracker/.claude/adr/deal-tracker.md`
- `k3s-local-apps-manifests/.claude/adr/k3s-local-apps-manifests.md`

Está en git porque el grafo es **local a cada equipo**: es lo que permite reconstruirlo en el otro
portátil y revisarlo en un PR como cualquier otro documento.

**Reindexar borra el ADR del grafo, siempre.** No es intermitente: `index_repository` devuelve
`adr_present: false` y `manage_adr --mode sections` se queda en lista vacía, aunque lo hubieras
republicado un minuto antes (medido el 02/08/2026, dos veces seguidas). Así que republicar va
**después del último `index_repository`** — si reindexas después «por si acaso», lo vuelves a
perder. Lo que viene luego (issues y limpieza) no toca el grafo, así que no lo estropea; pero si
por lo que sea acabas reindexando otra vez, republica otra vez:

```bash
codebase-memory-mcp cli manage_adr --project <proyecto> --mode update \
  --content "$(cat .claude/adr/<proyecto>.md)"
```

Y compruébalo de verdad con `manage_adr --mode sections`, que lee el grafo. El `adr_present` de
`index_repository` solo te dice cómo quedó **en ese momento**.

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

## 4. Dejar por escrito lo pendiente y lo descubierto

Aquí es donde se aplica la regla de arriba. La issue es el único sitio donde un pendiente o un
hallazgo sobrevive a la sesión, así que el listón es: **si sigue vivo, se escribe**.

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

### Lo que no cabe en ninguna issue abierta: issue nueva

El hallazgo típico de una sesión no encaja donde estabas trabajando — arreglando una cosa se ve
otra rota al lado. Meterlo como comentario en la issue de hoy lo entierra: esa issue se cierra y
el hallazgo se cierra con ella, sin que nadie lo haya decidido.

Así que cuando algo se salga del scope de la issue en curso, **ábrele issue propia** con
`gh issue create`, y enlázala desde la que estabas tocando (una línea basta: «visto de camino,
va en #N»). Ese enlace es lo que luego lee `revisar-backlog` como dependencia real entre issues.

Escribe el cuerpo pensando en alguien que no estuvo hoy aquí: qué se vio, cómo reproducirlo, y el
dato medido si lo hay. Una issue que dice «revisar el canon de tallas» sin más es un recordatorio
de que algo pasaba, no un plan — y en tres semanas cuesta más reconstruirla que haberla escrito.

Si dudas entre comentario e issue nueva, el criterio es si tiene vida propia: ¿se puede cerrar la
issue de hoy dejando esto sin hacer? Si la respuesta es sí, es una issue.

## 5. Cerrar el trabajo pendiente

Antes de despedirte, mira `git status` en los repos tocados y di explícitamente:

- Qué queda sin commitear, y si debería commitearse o es basura de pruebas.
- Qué ramas quedan sin mergear o qué PR quedan abiertos.
- Si el ADR ha cambiado, que ese fichero también va en el commit.

No commitees por tu cuenta salvo que se te pida. El objetivo es que no se pierda nada por olvido,
no decidir por el usuario.

## 6. Limpieza: worktrees y ramas que ya no sirven

Va **al final**, y el orden no es capricho: borrar es lo único irreversible de esta skill, y solo
se puede decidir con seguridad cuando ya sabes qué se commiteó, qué se subió y qué se mergeó. Lo
que aquí se tira ya no está en ninguna otra parte.

La regla que gobierna los dos casos: **primero se demuestra que el trabajo está a salvo en otro
sitio, y solo entonces se borra**. En el orden inverso no hay vuelta atrás.

### Worktrees

Cada sesión que arranca desde `revisar-backlog` crea el suyo, así que si nadie los recoge se
acumulan — y no son gratis: cada uno se queda con su rama, su copia del árbol y sus dependencias
instaladas.

```bash
git worktree list          # los que hay, y en qué rama va cada uno
git -C <ruta-worktree> status --short          # ¿queda algo sin commitear?
git -C <ruta-worktree> log --oneline origin/main..HEAD   # ¿commits que no están en main?
```

Esas dos preguntas son la comprobación completa: si las dos salen vacías, en el worktree no hay
nada que no esté ya en `origin/main` y se puede tirar sin pensarlo. Si sale algo, **no lo borres**
— dilo, con el detalle de qué es, y deja que el usuario decida entre subirlo, mergearlo o
descartarlo. Un worktree de más solo estorba; uno de menos puede ser media sesión perdida.

Para el worktree **de esta sesión** (el que creaste con `EnterWorktree`), sal con `ExitWorktree`:
`action: "remove"` si las dos comprobaciones salieron limpias, `action: "keep"` si el trabajo sigue
a medias y mañana se retoma. La herramienta hace su propia comprobación y se niega a borrar si
encuentra algo — trátalo como un aviso real, no como un obstáculo: `discard_changes: true`
solo con el visto bueno explícito del usuario, y no como reacción automática a que fallara.

Los worktrees de sesiones **anteriores** los ignora `ExitWorktree` (solo gestiona el suyo), y en
este repo suelen quedar `locked`, que es lo que impide que un `prune` se los lleve por delante.
Aplica las mismas dos comprobaciones y, si están limpios, propónselos al usuario en bloque:

```bash
git worktree unlock <ruta> && git worktree remove <ruta>
```

Que estén ahí no significa que sobren: puede ser trabajo aparcado a propósito. Por eso se propone,
no se ejecuta a bulto.

### Ramas ya mergeadas

Una rama cuyo PR ya está mergeado no aporta nada y ensucia la lista donde luego se busca. Lo
barato es que se borren solas al mergear, así que **al cerrar el PR usa `--delete-branch`** y no
habrá nada que limpiar aquí:

```bash
gh pr merge <n> --squash --delete-branch     # o --merge, según cómo se cierre
```

Para las que ya se quedaron atrás, primero la comprobación y después el borrado:

```bash
git fetch --prune
git branch --merged origin/main              # ojo: contra origin/main, no contra el HEAD actual
gh pr list --state merged --limit 20         # el PR de cada rama, y si de verdad se mergeó
git branch -d <rama>                         # -d, nunca -D: se niega si no está mergeada
git push origin --delete <rama>              # solo si la rama remota también sobra
```

Dos detalles que evitan un susto:

- `git branch --merged` sin argumento compara contra donde estés parado, no contra `main`. Nómbralo
  siempre — si lo lanzas desde una rama de trabajo la lista que sale no significa lo que crees.
- Un PR cerrado con **squash o rebase** deja la rama como *no mergeada* para git, aunque su
  contenido esté en `main`: el commit tiene otro sha. Ahí `-d` se niega con razón y la prueba
  buena es el estado del PR en `gh`. Ese es el único caso donde `-D` está justificado, y aun así
  se pregunta antes: es exactamente el mismo síntoma que tendría una rama con trabajo de verdad
  sin mergear.

Presenta la limpieza como una lista de lo que vas a borrar y por qué se puede (rama → PR mergeado
→ comprobación que lo confirma), y ejecútala cuando el usuario dé el visto bueno.

## Qué NO hacer

No reescribas el ADR entero por costumbre: reescribirlo cada día lo convierte en prosa genérica y
se pierde precisamente lo que lo hacía útil, que son los detalles concretos y medidos.

Tampoco des por hecho que el reindexado fue bien. Mira `nodes`/`edges` y `adr_present` en la
salida; un índice a cero o un ADR perdido en silencio son peores que no haber reindexado, porque
la siguiente sesión confiará en ellos.

Y no borres nada —worktree o rama— por parecer viejo, por estar `locked` o porque «seguro que ya
estaba mergeado». La comprobación son dos comandos y el error no tiene deshacer. Si te falta el
dato que la confirma, esa es la respuesta: se queda.
