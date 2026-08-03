---
name: cerrar-sesion
description: Cierre de una sesión de trabajo — consolida en `main` todo lo que la sesión ha hecho (commit, PR y merge de los PR propios que sigan abiertos, siempre con los checks en verde), reindexa el grafo de codebase-memory, actualiza el ADR si la sesión cambió algo estructural, deja por escrito en las issues todo lo pendiente y los hallazgos nuevos (abriendo issue si se salen del scope) y limpia lo que sobra: el worktree de la propia sesión (nunca los de otras) y las ramas ya mergeadas. Usar siempre que se dé por terminada la sesión, aunque no se pida explícitamente nada de esto: "vamos a cerrar sesión", "lo dejamos por hoy", "ya está por hoy", "me voy", "terminamos", o cualquier señal de que se cierra el terminal.
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

## 1. Consolidar: que el trabajo de hoy acabe en `main`

Va primero porque casi todo lo que viene después lo da por hecho: el reindexado quiere ver el
`main` definitivo, y la limpieza del paso 6 solo puede borrar lo que ya esté mergeado.

Piensa en el trabajo como una escalera, de menos a más a salvo:

1. **Sin commitear** — invisible, y se va con el portátil. Es el escalón malo.
2. **Commiteado sin subir** — existe en un solo disco.
3. **Rama subida sin PR** — está a salvo, pero nadie sabe que existe.
4. **PR abierto** — a salvo y visible; se puede dejar así *a propósito*.
5. **Mergeado en `main`** — hecho.

El cierre consiste en subir cada cosa hasta donde le toque, y lo que esté listo llega arriba. El
fallo típico no es dejar código a medias: es dejar en el escalón 4 un PR verde que solo necesitaba
un clic, y descubrirlo tres días después. Un PR abierto se ve en GitHub, sí, pero un PR que nadie
va a revisar porque era tuyo y estaba listo no es trazabilidad, es un olvido con URL.

Así que **consolida y mergea por defecto**, incluidos los PR que abriste antes en esta misma sesión
y siguen abiertos:

```bash
git status --short                    # ¿queda algo por commitear?
gh pr list --state open               # ¿PR de esta sesión sin cerrar?
gh pr checks <n>                      # ← antes de mergear, siempre
gh pr merge <n> --merge --delete-branch
```

### Verde antes de mergear, sin excepción

`gh pr checks` antes de cada merge. Si hay algo en rojo o todavía corriendo, **no mergees**: espera
o déjalo abierto y dilo. Aquí eso no es formalismo — un merge a `main` publica imagen y ArgoCD la
despliega en `dev`, así que mergear en rojo por cerrar la sesión antes deja roto el entorno para
quien llegue mañana, que es justo lo contrario de cerrar bien.

Si los checks siguen corriendo y no quieres esperar, `gh pr merge --auto` mergea solo cuando pasen.
Es la salida buena: no te quedas mirando y tampoco dejas el PR olvidado.

### Solo lo de esta sesión

Mergear es publicar en nombre de alguien, así que el alcance es el mismo que con los worktrees:
**lo que ha hecho esta sesión y nada más**. Un PR abierto de otra persona —o de otra sesión— no se
mergea aunque esté verde y aunque parezca terminado; no sabes si espera una revisión, una prueba
en `dev` o una decisión que no está escrita. Si no distingues cuáles son tuyos, mira la fecha y la
rama contra los commits de hoy, y si aún dudas, pregunta en vez de mergear.

Y no todo lo de la sesión merece subir: los experimentos, los ficheros de prueba y las ramas que
nacieron para descartar se dicen y se tiran, no se commitean «por si acaso».

### El listón: consolidado o no está cerrado

Lo demás sí sube, y sube entero. **Una sesión con trabajo suyo por debajo del escalón 5 no está
cerrada**, y hay que decirlo con esas palabras en vez de dar el cierre por bueno con una nota al
pie. No es una cuestión de orden: el trabajo que se queda a mitad de escalera es exactamente el
que nadie retoma, porque para retomarlo primero hay que acordarse de que existe.

Solo dos cosas justifican parar antes de arriba, y las dos son verificables, no opiniones:

- **Los checks no están en verde.** Espera, o `--auto`, o déjalo abierto explicando en el PR qué
  falla.
- **El trabajo está de verdad a medias** y mergearlo dejaría `main` con algo incompleto.

En cualquiera de los dos casos, el destino mínimo es el escalón 4 —PR abierto, con el estado
escrito en él— y queda anotado en su issue según el paso 5. Dejar trabajo a medias está bien;
dejarlo a medias *y solo en tu disco*, no.

Termina comprobándolo, que es lo que separa cerrar de creer que has cerrado:

```bash
git status --short          # vacío
gh pr list --state open     # sin nada tuyo de hoy, o con el porqué dicho en voz alta
```

## 2. Reindexar

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

## 3. El ADR vive en un fichero, el grafo es solo la copia consultable

La fuente de verdad del ADR es el fichero versionado de cada repo:

- `deal-tracker/.claude/adr/deal-tracker.md`
- `k3s-local-apps-manifests/.claude/adr/k3s-local-apps-manifests.md`

Está en git porque el grafo es **local a cada equipo**: es lo que permite reconstruirlo en el otro
portátil y revisarlo en un PR como cualquier otro documento.

**Reindexar puede borrar el ADR del grafo, y no se puede predecir cuándo.** Aquí ponía «siempre,
no es intermitente», apoyado en dos medidas del 31/07 y el 02/08/2026 que dieron
`adr_present: false` con `manage_adr --mode sections` en lista vacía. Es falso: esa misma tarde del
02/08 un `full` sobre este repo devolvió `adr_present: true` y las 16 secciones intactas (está
contado en la sección *OPERACIÓN DEL PROPIO ÍNDICE* del ADR, que es la versión buena de esto).

Con el comportamiento indeterminado, la consecuencia práctica no cambia y por eso el orden importa
más, no menos: republicar va **después del último `index_repository`**, que es lo único que
garantiza el resultado en los dos casos. Lo que viene luego (issues y limpieza) no toca el grafo,
así que no lo estropea; pero si acabas reindexando otra vez, republica otra vez:

```bash
codebase-memory-mcp cli manage_adr --project <proyecto> --mode update \
  --content "$(cat .claude/adr/<proyecto>.md)"
```

Y compruébalo de verdad con `manage_adr --mode sections`, que lee el grafo: no asumas en ninguna
de las dos direcciones. El `adr_present` de `index_repository` solo te dice cómo quedó **en ese
momento**.

## 4. ¿Cambia el ADR lo aprendido hoy?

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

Si hay que actualizarlo: edita el fichero de `.claude/adr/` (no el grafo directamente) y re-publica
con `manage_adr` en modo `update` pasándole el contenido del fichero. Así el fichero y el grafo
nunca divergen.

Y **ese fichero es trabajo de la sesión como cualquier otro**, aunque el paso 1 ya haya pasado: le
toca su commit, su PR y su merge. Es el despiste fácil de esta skill —el ADR se decide tarde,
cuando la parte de git parece cerrada— y deja el grafo diciendo algo que el repo no tiene escrito
en ninguna parte, que es la divergencia exacta que este paso existe para evitar. Si sabes de
antemano que la sesión toca el ADR, decídelo antes de consolidar y ahórrate el segundo PR.

## 5. Dejar por escrito lo pendiente y lo descubierto

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

## 6. Limpieza: worktrees y ramas que ya no sirven

Va **al final**, y el orden no es capricho: borrar es lo único irreversible de esta skill, y solo
se puede decidir con seguridad cuando ya sabes qué se commiteó, qué se subió y qué se mergeó. Lo
que aquí se tira ya no está en ninguna otra parte.

La regla que gobierna los dos casos: **primero se demuestra que el trabajo está a salvo en otro
sitio, y solo entonces se borra**. En el orden inverso no hay vuelta atrás.

### Worktrees: solo el tuyo

**El único worktree que esta sesión puede borrar es el que ha creado esta sesión.** Los demás no
se tocan: ni se borran, ni se desbloquean, ni se proponen para borrar. Se ignoran.

El motivo es que no puedes distinguir un worktree abandonado de uno vivo. Aquí hay varias sesiones
trabajando sobre el mismo repo, y otra puede tener el suyo abierto ahora mismo, o haberlo dejado
aparcado a medias a propósito para retomarlo mañana. Desde aquí las dos cosas se ven idénticas:
una rama, unos ficheros modificados y una fecha. Y la asimetría no perdona — un worktree de más
solo estorba en un `git worktree list`; uno de menos puede ser media sesión de otro, y encima de
trabajo que no está en ningún remoto.

Para **el tuyo**, comprueba antes de decidir:

```bash
git -C <ruta-worktree> status --short                     # ¿queda algo sin commitear?
git -C <ruta-worktree> log --oneline origin/main..HEAD    # ¿commits que no están en main?
```

Si las dos salen vacías, no hay nada que no esté ya en `origin/main` y se puede tirar:
`ExitWorktree` con `action: "remove"`. Si sale algo, `action: "keep"` y dilo — el trabajo sigue a
medias y mañana se retoma. La herramienta hace además su propia comprobación y se niega a borrar
si encuentra algo; trátalo como un aviso real, no como un obstáculo: `discard_changes: true` solo
con el visto bueno explícito del usuario, nunca como reacción automática a que fallara.

De los ajenos, lo único que se hace es **mirar y no tocar**. `ExitWorktree` ya los ignora (solo
gestiona el suyo) y en este repo suelen quedar `locked`, que es justamente lo que impide que un
`prune` se los lleve por delante — esa cerradura está puesta a propósito, no es suciedad. Si al
hacer `git worktree list` ves que se acumulan, puedes mencionarlo de pasada, sin lista de borrado
y sin pedir permiso para borrarlos: es información, no una propuesta.

Solo si el usuario nombra uno explícitamente y pide borrarlo entra en juego lo de siempre —
comprobar primero, borrar después:

```bash
git worktree unlock <ruta> && git worktree remove <ruta>
```

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

Tres detalles que evitan un susto:

- `git branch --merged` sin argumento compara contra donde estés parado, no contra `main`. Nómbralo
  siempre — si lo lanzas desde una rama de trabajo la lista que sale no significa lo que crees.
- **`-d` también mira el upstream, no solo `main`.** Si borras la rama remota antes que la local,
  `git branch -d` se niega con *«not deleting branch X that is not yet merged to
  origin/X, even though it is merged to HEAD»*: se ha quedado comparando contra un upstream que ya
  no existe. Léelo entero antes de alarmarte — ese «even though it is merged to HEAD» es la prueba
  de que el contenido está a salvo. Se arregla con `git fetch --prune`, que borra la referencia
  muerta, y entonces `-d` pasa. Nunca hace falta `-D` para esto (medido el 03/08/2026 borrando
  `docs/adr-ctype-c`).
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

Sobre todo, no toques el worktree de otra sesión ni para proponerlo. Que esté a medias no
significa que esté abandonado: significa que alguien lo dejó a medias, que es como está el trabajo
la mayor parte del tiempo.
