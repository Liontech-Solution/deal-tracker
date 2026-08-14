---
name: validar-qa
description: Validación profunda del entorno QA antes de promover una versión a producción — recorre la interfaz con un navegador real, el contrato de la API, el estado de los datos y de la ingesta, y el cluster, y emite un veredicto APTO / NO APTO / NO CONCLUYENTE con evidencia e issues. Úsala cuando se vaya a promocionar o publicar una versión, ante cualquier cambio mayor o menor que no sea un parche, y siempre que se pregunte "¿esto está listo para prod?", "valida QA", "revisa que QA funciona", "damos el visto bueno a la release".
---

# Validar QA a fondo

Esta skill decide si una versión puede salir a producción. Es lo único que hay entre un merge y el
usuario final, así que su valor entero está en que el listón sea **el mismo cada vez** y en que no
apruebe nada por omisión.

El hueco que tapa es real y se mide: `release-qa.yml` promueve por digest y el «gate humano» es
lanzar el workflow; el CI del web valida lint, typecheck y vitest, y del frontend solo que compila;
no hay ni un test de navegador en el repo; y los e2e corren contra una Postgres sembrada a mano, con
el guard de autenticación falseado y el locale de CI, no contra el `UTF8 | C | C` del cluster. Nada
de eso mira lo que de verdad está desplegado.

Cuando se escribió, QA corría v0.1.5 y bastó con mirar para encontrar: la última pasada de Cacles en
`failed` con un 429 de huella TLS, Hipercor **sin una sola fila** en `scrape_run` pese a que el ADR
afirmaba que las nueve tiendas tenían catálogo ingerido en QA, dos de nueve tiendas invisibles en las
facetas del catálogo, y pasadas en `success` con 69 y 15 errores. Ninguna alarma se disparó.

## Invocación

| Forma | Qué hace |
|---|---|
| `/validar-qa` | Completa: las cinco fases, ~60-90 min por el vigía |
| `/validar-qa rapida` | Sin jobs en el cluster ni vigía. ~20 min. **Nunca da APTO**, solo detecta lo obvio |
| `/validar-qa --frente ui\|api\|datos` | Un solo frente, para depurar o repetir |
| `/validar-qa v0.1.6` | Declara qué versión esperas encontrar desplegada; si no coincide, para |

---

## Fase 0 · Identidad de la versión

Sin esto el informe no vale nada: un «APTO» que no dice sobre qué artefacto se dio no es un
veredicto, es una opinión.

```bash
.claude/skills/validar-qa/scripts/qa-estado.sh
```

Recoge tag de imagen del web, del scraper y del matching, estado de ArgoCD, reinicios de pods, jobs
fallados, `/api/health` y `/api/config`. Además:

- **Los tres tags deben coincidir.** Si no, el `release-qa` quedó a medias: **P0** y se para.
- **Si se pidió una versión concreta** y la desplegada es otra, para y dilo. Validar una versión
  creyendo que es otra es peor que no validar.
- **`/api/config` con los tres campos nulos** en QA es **P0**: sin Keycloak la mitad de los frentes
  no aplica y el resultado sería un falso verde.
- **Delta desde la validación anterior.** Mira el informe más reciente de `.claude/qa-reports/` y
  saca `git log <tag-anterior>..<tag-actual> --oneline`. Eso dirige el esfuerzo: lo que cambió se
  mira con lupa, lo demás se cubre por catálogo.

### La versión desplegada no es la que escribió el dato

Y hasta que no se comprueba, **no se sabe sobre qué versión habla el informe**:

```bash
.claude/skills/validar-qa/scripts/qa-procedencia.sh --rango <tag-anterior>..<tag-actual>
```

`qa-estado.sh` lee la imagen del **CronJob**, que ArgoCD sincroniza al desplegar, así que enseña la
versión nueva desde el minuto uno. Pero las filas de `scrape_run` las escribió un **Job**, que es un
snapshot inmutable de su disparo y conserva la imagen que hubiera entonces. Las dos cosas se parecen
tanto que la validación de v0.3.0 dio por hecho que su bloque `## Cifras` describía su propia
ingesta, y describía la de v0.1.9 (#378).

La severidad **depende de si esta release toca el scraper**, y el script la decide solo si le pasas
el `--rango` que acabas de sacar en el punto anterior:

- **`services/scraper/` sin cambios en el rango → P1 de proceso.** El dato es de otra versión, pero
  de la misma ingesta: se dice en el informe y se sigue. Era la coartada de v0.3.0.
- **`services/scraper/` con cambios → P0, y no se valida el frente de datos así.** Medir el catálogo
  contra filas que escribió el scraper anterior no prueba nada del código que va a producción.
  El remedio es el que se aplicó a mano en v0.4.0, y el script lo imprime ya escrito:
  `kubectl -n deal-tracker-qa create job validacion-<version>-<slug> --from=cronjob/deal-tracker-scraper-<slug>`.
  Basta con las tiendas que el cambio toca; si toca `ingest.py` o `base.py`, son todas.
- **`procedencia desconocida`** (el `Job` caducó: los CronJob traen `successfulJobsHistoryLimit: 3`)
  **no es un aprobado**. Es exactamente el supuesto silencioso que esto viene a quitar: se declara
  no cubierto y se trata como el caso anterior si la release toca el scraper.

Sale en el informe siempre, conforme o no: es media línea y es lo que permite releer un informe
viejo sabiendo de qué versión era su dato.

## Fase 1 · Disparar el vigía (solo en modo completo)

```bash
kubectl -n deal-tracker-qa create job validacion-vigia-<version> --from=cronjob/deal-tracker-vigia
```

Tarda 25-40 minutos, por eso arranca al principio y se recoge en la Fase 5. Corre **desde el
cluster** a propósito: la pregunta es si las tiendas nos dejan entrar a *nosotros*, y esta máquina
sale por otra IP con otra reputación. Escribe filas en `vigia_run` y puede abrir o comentar una
issue `[vigía]` — es su comportamiento normal, no hay que evitarlo.

Anota el nombre del job: lo necesitas al final.

## Fase 2 · Datos y API, en paralelo

Lanza los dos subagentes a la vez; ninguno toca el navegador:

- `validador-qa-datos` — catálogo `casos-datos.md`, D1–D15.
- `validador-qa-api` — catálogo `casos-api.md`, A1–A54.

## Fase 3 · Interfaz

`validador-qa-ui` — catálogo `casos-ui.md`, U1–U50. **Solo**, y después de los otros dos: el
navegador de Playwright es un MCP único y dos agentes usándolo se pisan las pestañas. Además, saber
ya qué tiendas están vacías evita que el frente de UI reporte como roto lo que solo es dato ausente.

### Dependencias entre frentes

Dos casos no se sostienen solos, y hay que saberlo antes de correr un frente aislado:

- **D6** (prendas duplicadas) necesita contrastar la base contra la respuesta de la API. Con
  `--frente datos` el agente lo resuelve con `curl`, pero en la pasada completa la respuesta ya la
  tiene el frente de API: pásale el `product_id` en lugar de que lo repita. Desde v0.3.0 ese `curl`
  **va firmado** (`scripts/qa-token.sh`): el catálogo ya no es público (#309), y sin token devuelve
  401 — que a mitad de un frente de datos se lee como una API caída y no lo es.
- **D13** (vigía) depende del job de la Fase 1. Con `--frente datos` **no se lanza**: se declara
  fuera de alcance de esa ejecución. Una dependencia no satisfecha no es un fallo, y reportarla como
  tal mete un P0 falso.

## Fase 4 · Checkpoint manual de Telegram

El canje del `/start` y la llegada del aviso necesitan a una persona con la app abierta. El usuario
`test-qa` está vinculado al Telegram del operador precisamente para esto. Para y pregunta así:

```
✋ CHECKPOINT MANUAL — Telegram

1. Abre este enlace desde tu Telegram: https://t.me/<bot>?start=<token>
2. El bot debe responder, literal:
   "✅ ¡Listo! Te avisaré por aquí cuando bajen de precio las prendas que sigues."
3. La pestaña /ajustes debe pasar sola a "@<usuario>" en menos de 4 s, sin recargar

¿Qué ves?
```

Con la respuesta, cierra el caso. **Sin respuesta, el frente queda NO CUBIERTO**, y eso arrastra el
veredicto a NO CONCLUYENTE. No se aprueba por silencio.

## Fase 5 · Consolidar y decidir

1. **Recoge el vigía**: `kubectl -n deal-tracker-qa logs job/validacion-vigia-<version> --tail=200`.
   Su código de salida ya es un veredicto — 0 nada accionable, 1 algo lo es. La severidad de cada
   `✖` y cada `⚠` la decide su marca, no el símbolo: ver «El vigía» en el listón. Si no terminó, el
   frente queda no cubierto.
2. **Escribe el informe** en `.claude/qa-reports/<version>.md` con `informe-plantilla.md`.
3. **Si el veredicto es APTO, asciende la release**:
   `gh release edit <version> --prerelease=false`. Todas nacen `prerelease` desde `release-qa`, así
   que esto es lo que deja ver de un vistazo cuáles pasaron. **Es la señal, no la autoridad**: quien
   decide es el informe commiteado, y es lo que `release-prod.yml` verifica antes de promover. Si el
   veredicto no es APTO, no toques el flag.
4. **Abre issues** de los P0 y P1 (ver más abajo).
5. **Emite el veredicto** en el terminal, en tres líneas: veredicto, cuántos P0/P1/P2, y la frase
   que lo justifica.

---

## El listón

**P0 — bloquea la promoción.** `/api/health` en 503 · cualquier 5xx · login roto · catálogo vacío o
sin fotos · precios ≤ 0 · talla o color sin canonicalizar en proporción apreciable · calzado no
barefoot colado en el catálogo por defecto · alta o baja de interés que no funciona · migraciones
sin aplicar · los tres tags de imagen descuadrados · una tienda con la última pasada `failed`, en
`running` colgada, o sin ninguna pasada · drift entre la versión pedida y la desplegada · caída de
más del 30 % en las cifras de una tienda respecto al informe anterior · un `✖` **sin marca** del
vigía · un «oferta real» sobre un PVP inflado · **una acusación de «Precio inflado» sobre una prenda
con menos de 90 días de histórico** (#332: es afirmar un fraude sin haberlo comprobado, y el error
simétrico del anterior) · **una combinación de filtros que el propio panel ofrece por encima de
10 s** · **dato escrito por una imagen anterior cuando la release toca `services/scraper/`** (#378:
el frente de datos estaría midiendo el scraper de la versión pasada).

> Ojo con el simétrico de esto, que es un falso rojo fácil: **que no aparezca ni un solo badge
> «Precio inflado» es lo esperado**, no una regresión. Acusar exige 90 días cubiertos y la serie de
> QA arranca el 24/07/2026, así que hasta ~22/10/2026 no puede haber ninguno. Ver U26b/U26c.

**P1 — no bloquea, pero se abre issue.** `errors > 0` en una pasada `success` · hoja de categoría
retirada · aviso de ritmo del vigía · `✖ [cobertura]` del vigía · error en la consola del navegador ·
faceta que no devuelve resultados · **una combinación de filtros del panel entre 3 s y 10 s** ·
regresión de UX no crítica · `ValueError: Tienda desconocida`
(que es P1 **de proceso**: la tienda está en `main` pero el `release-qa` aún no la ha promovido, no
está rota) · **dato escrito por una imagen anterior cuando la release NO toca `services/scraper/`**
(también de proceso: el dato es viejo pero lo escribió el mismo scraper).

**P2 — se anota y ya.** Cosmético · `⚠ [estacional]` del vigía.

### La latencia del catálogo

Este criterio lo trae la validación de **v0.3.0** (12/08/2026), y **rige a partir de la siguiente**:
v0.3.0 se validó con el listón anterior, que no medía latencia, y salió **APTO** con #342 abierta.
Quien lea esto y vea esa issue viva no está ante una promoción mal dada, sino ante el hallazgo que
escribió la regla.

Lo que la motiva: la épica #308 prometía «el catálogo deja de tardar 24 s y **los filtros se pueden
usar de verdad**», y la validación encontró el catálogo sin filtros en 1,8 s pero
`color=negro&retailer=hm` en **27 s** — la misma espera que la versión existía para quitar, escondida
detrás de una combinación normal de filtros. No lo cazó ningún frente porque **ningún caso medía
tiempos**; salió de comprobar a mano la promesa titular de la épica. Si una versión promete
rendimiento, mídelo aunque no haya caso que lo pida.

**Qué se mide:** combinaciones que el panel de filtros **ofrece de verdad** (no URLs inventadas), al
menos una con dos ejes sobre la tienda de catálogo más grande, que es donde se ve. Hoy eso es H&M o
Zara.

**Cómo se mide, que es la parte que se falla:** en **ventana tranquila y con control**. Los frentes
de datos y API machacan la misma API y la misma base, y QA es de **una sola réplica**: midiendo con
subagentes en marcha la lectura llega a doblarse —en la validación de v0.3.0 salieron **45 s donde
luego había 23 s**— y sobre eso se escribe un P0 falso. La proporción es la lección; esos segundos
son de aquella medida y no describen el sistema de hoy. Lanza un
`sin filtros` **antes y después** de la tanda; si el control se mueve, la medida no vale y se repite.

```bash
TOKEN=$(.claude/skills/validar-qa/scripts/qa-token.sh)
B=https://dealtracker-qa.liontechsolution.com/api/catalog/products
curl -s -o /dev/null -w '%{time_total}s\n' -H "Authorization: Bearer $TOKEN" "$B"
curl -s -o /dev/null -w '%{time_total}s\n' -H "Authorization: Bearer $TOKEN" "$B?color=negro&retailer=hm"
```

### El vigía

Su símbolo dice que pasa algo; **la marca dice de qué clase es**, y eso cambia la severidad. Se lee
la marca y no se interpreta la prosa: `vigia.py` las emite como constantes (`MARCA_COBERTURA`,
`MARCA_ESTACIONAL`) y hay un test que las fija.

| Qué trae el log | Severidad | Por qué |
|---|---|---|
| `✖` sin marca — hojas retiradas, parseo roto, ninguna hoja viva, un barrido que revienta | **P0** | Es la razón de ser del vigía: la tienda ha dejado de dejarnos entrar, y es el fallo silencioso que no se ve en ningún otro sitio |
| `✖ [cobertura]` — hay una hoja publicada que no cubrimos | **P1**, y **P0 si alguna de las hojas que nombra cae en una de las cinco categorías del brief** (pantalones, camisetas, sudaderas/jerseys, vestidos, ropa interior) | No es que la tienda esté rota: es una decisión de alcance de producto, pendiente. Vale desde nada —un bañador— hasta prendas del brief que el usuario no ve |
| `⚠ [estacional]` — hoja de campaña apagada | **P2**, exento: se anota y **no** se abre issue | El vigía ya declara en código que su id vuelve con la campaña (`LeafHealth.estacional`). Abrir issue por algo que la herramienta califica de benigno por diseño es el hallazgo de relleno que esta skill prohíbe |
| `⚠` sin marca — hojas sin veredicto, aviso de ritmo | **P1** | Sin cambios |

Esta granularidad la trae #251, y **el motivo hay que conocerlo para no volver atrás**: el listón
anterior hacía P0 cualquier `✖`, y con eso `banadores-bebe` —cinco prendas de bebé que el equipo
había etiquetado `prioridad-4`— bloqueó las releases v0.1.7 y v0.1.8. La regla nueva **no habría
salvado ninguna de las dos**: la otra hoja del mismo hallazgo, `punto-y-jerseis`, es
`sudaderas/jerseys` y por tanto P0 igual. Eso es justo lo que la hace defendible — afina la
severidad sin bajar el listón de lo que de verdad importa.

### El veredicto

- **APTO** — cero P0 **y** los cuatro frentes ejecutados enteros.
- **NO APTO** — al menos un P0.
- **NO CONCLUYENTE** — cero P0 pero algún frente sin ejecutar o incompleto. Incluye siempre el modo
  `rapida`.

No hay cuarta opción y no se negocia sobre la marcha. Un frente que no se pudo correr **no cuenta
como aprobado**: el valor entero de esta skill es que nunca aprueba por omisión, y basta con
saltárselo una vez para que deje de servir.

**Y una versión que no se validó nunca tampoco llega a producción**, aunque nadie la haya declarado
mala. Se puede cortar una release de QA y no pasarle esta skill jamás: se queda en `prerelease` y
`release-prod.yml` la rechaza dos veces — el GATE 0 por seguir en `prerelease`, y el GATE 1 por no
encontrar `.claude/qa-reports/<version>.md`. No es un accidente feliz, es el diseño — el silencio no
promueve.

Lo que sí ha cambiado con #306: **no se queda ahí para siempre**. `release-qa` poda al publicar y
solo sobreviven las **5 `prerelease` más recientes por semver**, así que una versión sin validar
acaba desapareciendo con su tag. No afecta al veredicto —una prerelease no era promocionable de
todos modos—, pero sí a mirar atrás: si hace falta conservar el rastro de por qué se cortó una
versión, lo que lo conserva es **su informe**, que la poda no toca nunca.

### Las issues

**Buscar antes de abrir es obligatorio, y no basta con mirar los títulos.** Un hallazgo puede estar
ya registrado en una issue que trata de otra cosa y lo menciona de pasada — que es justo donde está
el contexto que evita duplicarlo:

```bash
gh issue list --state open --limit 60
gh issue list --state all --search "<término del hallazgo>"
gh issue view <n> --comments        # el estado real vive en el último comentario
```

Es el mismo patrón que usa `services/scraper/src/scraper/avisos.py` con su marcador `[vigía]`:
**una issue viva por asunto, y comentarios dentro**. Tres desenlaces posibles, y hay que elegir uno
a conciencia:

- **Existe y sigue vigente** → comenta en ella con la versión validada y lo nuevo que aportas. No
  abras otra.
- **Existe y ya está resuelta** → dilo en el informe. Una issue obsoleta detectada es tan útil como
  un hallazgo, y en este repo pasa: el estado real está en los comentarios, no en el título.
- **No existe** → ábrela.

Título: `[validación QA] <qué falla, en una frase>`. Cuerpo: versión validada, evidencia (comando y
salida, o captura), qué le pasa al usuario y severidad.

---

## Qué puede tocar en QA

QA no es producción, pero tiene datos reales y manda mensajes reales.

| Acción | Cuándo |
|---|---|
| Crear y borrar intereses de `test-qa` | Siempre. Limpieza obligatoria, también si un frente aborta |
| Job del vigía | Modo completo, automático. Escribe `vigia_run` y puede abrir issue `[vigía]` |
| Pasada de scraper de una tienda | **Solo si el frente de datos la encuentra rota y el operador lo confirma.** Avisa de la duración: Zara en frío son ~30 min |
| Job de matching | **Nunca sin que el operador lo pida en esta sesión.** En QA envía Telegram **reales** a personas y avanza la marca de agua de `job_state`, así que no es repetible: lo que se envió, enviado está |

Todo lo demás es lectura. El SQL va por `scripts/qa-sql.sh`, que abre transacción `READ ONLY` de
verdad — el motor rechaza la escritura, no un filtro de palabras. En agosto de 2026 un pytest
despistado se llevó por delante el histórico de `vigia_run`; esa valla es de aquello.

Y **nada de `kubectl patch`** sobre el cluster: ArgoCD corre con `selfHeal: true` y lo revierte en
segundos. Los cambios de cluster van por el repo de manifiestos.

## Qué no hacer

**No aprobar por omisión.** Es la única forma real de que esta skill haga daño: dar APTO con un
frente sin correr. Si algo no se pudo ejercer, el veredicto es NO CONCLUYENTE y se dice qué falta.

**No inventar hallazgos de relleno.** Un frente limpio se reporta en dos líneas. Un informe con
P2 decorativos se deja de leer, y entonces el P0 de la semana siguiente tampoco se lee.

**No confundir vacío con roto.** Una tienda que no ha ingerido deja pantallas vacías y filtros sin
resultados: la interfaz está bien, el dato no está. Son dueños distintos y mezclarlos manda a
alguien a depurar el sitio equivocado.

**No arreglar lo que encuentres.** Esto es una validación, no una sesión de trabajo. Se documenta,
se abre issue y se decide después. Un arreglo a mitad de validación invalida lo ya medido y deja QA
en un estado que nadie ha visto entero.

**Y no transcribas lo que el código ya fija.** Es la lección de #343, donde siete expectativas
caducaron a la vez: la copia del bot está clavada por dos specs, el tipo de la respuesta del
catálogo vive en `catalog.types.ts`, y el catálogo de casos las había **copiado a mano**, así que
envejecieron sin que nada lo delatara — una de ellas llegó a dar por buena una regresión. Cuando el
valor lo fija el código, **el caso cita el símbolo y su fichero** además del valor, para que quien
lo lea pueda contrastarlo en diez segundos en vez de creérselo. Un caso que no se puede contrastar
contra nada es un caso que va a caducar en silencio.
