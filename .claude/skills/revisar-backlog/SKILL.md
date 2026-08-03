---
name: revisar-backlog
description: Revisa todas las issues y épicas abiertas del proyecto con sus comentarios, las ordena por prioridad (qué desbloquea a las demás y qué está corrompiendo datos en cada pasada) y propone cuál pasar a plan para atenderla en esta sesión. Úsala siempre que se pregunte por dónde seguir, qué hay pendiente, qué toca hoy, qué issue atacar, "repasa el backlog", "revisa las issues", "qué es lo más urgente", "por dónde empiezo" — y también al arrancar una sesión sin tarea asignada, aunque no se nombren las issues explícitamente.
---

# Revisar el backlog y elegir la issue de la sesión

En este repo **el cuerpo de una issue es el plan y los comentarios son el estado**. Se escriben
largos, con mediciones y SQL, y la conclusión de la sesión anterior vive en el último comentario,
no en el título. Consecuencia práctica: hay issues abiertas cuyo trabajo ya está hecho y solo
esperan una observación (#67 lo dijo con todas las letras), e issues cuya decisión ya está tomada
y solo falta escribirla. Ordenar leyendo títulos produce una lista plausible y equivocada.

El otro riesgo es el opuesto: leerlo todo entero son ~38k tokens, y la mayoría es historia de
trabajo ya cerrado. Por eso esto va en dos tiempos — se mide todo, se lee a fondo solo la lista
corta.

## 1. Medir: el triaje

```bash
python3 .claude/skills/revisar-backlog/scripts/triaje.py --json <scratchpad>/triaje.json
```

Una sola llamada a `gh` y una ficha por issue con lo que sirve para *ordenar*: etiquetas, edad,
casillas hechas/pendientes, referencias entrantes y salientes entre issues abiertas, y la cola del
último comentario. Unos 1.7k tokens en lugar de 38k.

El `--json` cachea la respuesta cruda. Reutilízalo si vuelves a pasar por aquí en la misma sesión,
pero **no** si han pasado horas o se han tocado issues: bórralo y vuelve a tirar.

Dos lecturas del triaje que conviene no equivocar:

- **`ref<-` es la métrica de taponamiento.** El script ya descuenta las épicas, porque colgar de
  una épica lo hace todo el mundo y eso mide pertenencia, no dependencia. Un `ref<- 4` significa
  que cuatro issues abiertas han necesitado hablar de esa: casi siempre es que esperan su dato.
- **La cola del último comentario es un indicio, no un veredicto.** Está para decidir a quién
  leer entero, y a veces la conclusión no está al final del texto.

Añade un dato barato que el triaje no trae: `gh pr list --state open` para ver si alguna candidata
ya tiene trabajo en marcha. Proponer algo que ya está a medio hacer en una rama es la manera más
tonta de fallar esta revisión.

## 2. Leer a fondo la lista corta

Escoge 4-6 candidatas del triaje y léelas de verdad, comentarios incluidos:

```bash
gh issue view <n> --comments
```

Lo que buscas en los comentarios, que es justo lo que el cuerpo no puede saber:

- **Si la decisión ya está tomada.** Muchas issues plantean opciones y un comentario posterior mide
  y elige. Eso cambia por completo lo que cuesta atenderla.
- **Si el trabajo ya está hecho** y la issue sigue abierta a propósito, esperando un evento (una
  ejecución verde, un release a QA). Esas no son candidatas: no hay nada que planificar.
- **Si ha aparecido un hallazgo que se fue a otra issue.** Pasa mucho aquí y es lo que crea las
  dependencias reales entre issues.
- **Si la issue ha quedado desfasada** porque otro PR resolvió el fondo del asunto.

### El grafo del código está indexado: úsalo si dudas del alcance

Este proyecto está indexado en `codebase-memory`
(`project='home-juanjocop-Proyectos-deal-tracker'`), así que cuando una issue no diga *dónde* toca
—o lo diga por encima— no hace falta adivinarlo ni barrer el repo a grep: `search_code` encuentra
el sitio, `search_graph` / `query_graph` dicen quién lo llama y `trace_path` si el cambio se queda
en una tienda o cruza a `ingest.py` y de ahí al esquema. Eso es exactamente lo que decide la
columna **Coste** y el **Encaje en la sesión**, que si no se rellenan a ojo.

Es una consulta de apoyo, no un paso obligatorio: tíralo solo para las candidatas cuyo alcance
esté en duda, y con una pregunta concreta. Explorar el grafo entero cuesta más contexto que el
triaje que acabas de ahorrar.

Y ojo con la caducidad: el índice se reconstruye en `cerrar-sesion`, así que refleja el último
cierre, no el `main` de ahora mismo. Sirve para orientarte; antes de afirmar en la ficha que algo
existe o que un cambio es de dos ficheros, confírmalo abriendo el fichero.

### Las épicas no son candidatas

Una épica es un contenedor, no trabajo. No la ordenes junto a las demás. Lo que sí hay que hacer
es **abrir sus casillas pendientes** y, para cada una, comprobar si ya tiene issue propia. Si la
tiene, la candidata es esa issue y la casilla es solo el índice. Si no la tiene, es trabajo real
que no aparece en ninguna lista — dilo explícitamente, porque es el punto ciego típico.

## 3. Ordenar

Dos criterios mandan, en este orden:

1. **Qué desbloquea a las demás.** Una issue que da el dato que otras están esperando vale más que
   una hoja aislada, aunque la hoja sea más vistosa. `ref<-` es el indicio; confírmalo leyendo por
   qué la mencionan (a veces es solo una nota de contexto).
2. **Qué está corrompiendo datos ahora mismo.** Lo que mete filas malas en la base en cada pasada
   tiene un coste que crece solo, y arreglarlo tarde obliga además a reparar lo acumulado. Una
   canonización errónea o una duplicación de variantes pesan más que una mejora que no ensucia nada.

El coste, y el que la decisión ya esté tomada en los comentarios, **no mueven el ranking**: van en
la ficha como contexto para que la elección final se haga con los ojos abiertos. Una issue cara y
desbloqueante sigue yendo arriba; lo que se decide después es si hoy cabe.

Si dos criterios chocan, di cuál has hecho pesar y por qué, en una frase. Un ranking sin argumento
es una opinión disfrazada de tabla.

## 4. Presentar

Siempre en este formato, en el terminal (nada de ficheros: envejecen en cuanto se toca una issue).

```markdown
## Ranking

| # | Issue | Por qué aquí | Coste | Estado real |
|---|-------|--------------|-------|-------------|
| 1 | #99 Lefties nunca ingirió | 4 issues esperan su dato | M | a medias, 0/5 casillas |
| 2 | … | … | … | … |

## Candidatas

### #99 — <título corto>
**Qué pide** — una o dos frases.
**Qué dicen los comentarios** — lo que cambia respecto al cuerpo: decisiones tomadas, trabajo ya
hecho, hallazgos que se fueron a otra issue.
**Desbloqueo / datos** — a quién destraba, o qué está ensuciando en cada pasada.
**Encaje en la sesión** — si se verifica en local con la Postgres de usuario o exige cluster (y
por tanto mergear a `main`), y si cabe en una sesión.
```

El ranking lleva **todas** las issues no-épicas; la ficha, solo las 3 primeras. Las que has
descartado por estar terminadas o desfasadas van en una línea al final diciendo por qué — es
información útil, y a veces lo que toca es cerrarlas.

## 5. Proponer y pasar a plan

Cierra con una recomendación explícita y su porqué, en dos o tres frases, y **espera**. La
elección es del usuario: puede querer la tercera porque hoy tiene media hora, o ninguna.

Cuando confirme, entra en plan mode con esa issue: para entonces ya la has leído entera con sus
comentarios, así que no vuelvas a tirar de `gh` ni rehagas el análisis. Lo que falta es mirar el
código que toca y escribir el plan.

### El plan empieza por un worktree

Aquí puede haber más de una sesión abierta a la vez sobre este repo, y dos sesiones compartiendo
el mismo directorio se pisan de una forma difícil de ver: una cambia de rama debajo de la otra, o
los cambios de las dos acaban en el mismo `git status`. Sale más barato aislarse antes de tocar
nada que descubrirlo a mitad. Así que **el primer paso del plan es siempre entrar en un worktree
propio para la sesión**, aunque hoy parezca que no hay nadie más trabajando — la prevención solo
sirve si es incondicional.

Esto cuenta como instrucción del proyecto para `EnterWorktree`: llámalo con un `name` que se
reconozca luego, típicamente la issue (`issue-99`). Por defecto ramifica desde
`origin/main`, que es lo que quieres para empezar limpio.

Dos consecuencias que el plan debe recoger, porque el worktree nace sin lo que no está en git:

- **Falta la configuración.** `.env` y `services/web/.env` están ignorados, así que hay que
  copiarlos del directorio original o el primer comando fallará por `DATABASE_URL`.
- **Faltan las dependencias.** `just setup` en el scraper y `pnpm install` en `services/web`, y son
  minutos: si la issue es de las que se verifican con una pasada real, cuéntalos en el coste.

No cierres el worktree al terminar la revisión ni por tu cuenta: mientras la sesión siga viva ahí
está el trabajo, y qué hacer con él (conservarlo o descartarlo) se decide al cerrar sesión.

## Qué no hacer

No inventes una prioridad que el repo no tiene. No hay milestones y la etiqueta `prioridad-1`
existe pero no está puesta en ninguna issue; si algún día la lleva alguna, respétala y dilo. El
resto del orden es tuyo y hay que argumentarlo, no presentarlo como si lo dijera GitHub.

No marques casillas, no comentes ni cierres issues durante la revisión. Esto es una lectura para
decidir; poner las issues al día es trabajo de `cerrar-sesion`, al final. Si encuentras algo que
merece un comentario, apúntalo y dilo — pero no lo escribas tú por tu cuenta.
