## PURPOSE

Plataforma que rastrea ofertas de ropa y calzado **barefoot infantil** para padres con presupuesto
ajustado. Scrapea precios de tiendas, guarda histórico, y avisa por **bot de Telegram** cuando un
artículo seguido baja de precio. El usuario elige qué seguir desde la web.

Requisitos de dominio que condicionan el diseño: seguimiento por **talla** y **modelo/color** (cada
combinación puede tener precio propio), separación **niño/niña** y **ropa vs zapatería**, detección
de altas y bajas de producto (exige un **identificador estable por tienda**), e histórico de precios
para graficar evolución y detectar **descuentos falsos**.

Vocabulario de dominio en español (pantalones, sudaderas, vestidos, ropa interior, zapatería). Se
preserva en datos y UI.

## STACK

Monorepo **poliglota**, dos servicios que no se llaman entre sí:

- `services/scraper` — **Python**. httpx para el camino barato, Playwright/Chromium para tiendas
  con antibot. CLI `python -m scraper.run --retailer <slug> [--migrate]`. `just check` = ruff +
  ruff format --check + mypy + pytest.
- `services/web` — **NestJS** (`@nestjs/*`, drizzle-orm, postgres, passport-jwt + jwks-rsa para
  validar tokens Keycloak) y frontend **React/Vite** en `services/web/frontend`, servido por el
  propio Nest vía `@nestjs/serve-static`. Gestor de paquetes: **pnpm**.
- `db/migrations` — **SQL crudo neutro** (`0001_init.sql` … `0020_size_canon_rango_colapsado.sql`).

Imágenes: `ghcr.io/liontech-solution/deal-tracker-scraper` y `-web`. Contexto de build en la **raíz
del repo**, no en el directorio del servicio.

## ARCHITECTURE

**El punto de integración es la base de datos, no una API.** Scraper y web solo se comunican a
través del Postgres compartido; el esquema SQL de `db/migrations` es el contrato.

- El **scraper** posee las escrituras de `retailer` / `product` / `variant` / `price_history` /
  `scrape_run` / `product_tag`. `ingest.py` hace la pasada completa en **una transacción atómica** y
  detecta altas y bajas. El **web** posee `app_user` (con el vínculo de Telegram), `interest`,
  `notification` y `job_state`.
- Las tiendas son **pluggable**: `stores/base.py` define `BaseStore`, `stores/registry.py` mapea
  slug → factoría. Hoy son **nueve**: `zara` (endpoints AJAX JSON públicos), `sfera` (Chromium,
  detrás de Akamai), `lefties` (Chromium, API `itxrest` de Inditex), `cacles` (Shopify,
  `products.json` público), `c-and-a` (GraphQL con persisted query, httpx puro), `hipercor`
  (Chromium, leída **por sus páginas** porque su `robots.txt` veta `/api`), `hm` (httpx pelado
  contra `api.hm.com`, que es otro host que el escaparate), `mango` (httpx con UA de Chrome, y la
  primera que publica su árbol de categorías) y `springfield` (httpx, y la primera que **no recorre
  hojas**: se lista por sitemap porque su `robots.txt` veta la rejilla de SFCC). Añadir tienda =
  añadir entrada en el registry. **Las nueve tienen catálogo ingerido en `dev`**, las siete primeras
  desde el 02/08/2026 y `mango` y `springfield` el 03/08/2026, el mismo día en que se registraron.
  Desde esa tarde las nueve corren también en **QA**, semanalmente y sin ninguna suspendida
  (juanjocop/k3s-local-apps-manifests#76). Que una tienda esté en el registry, tenga CronJob y salga
  verde en el vigía **no prueba que ingiera**: lo demostraron #93 y #99, y por eso lo que se cuenta
  aquí son pasadas con catálogo en una base, no altas en el registry. **Y ese «corren en QA» tampoco
  es ingerir**: medido el 04/08/2026 sobre v0.1.5, en QA solo **siete** tienen catálogo — Cacles a
  cero por el 429 de huella TLS e Hipercor sin fila ni en `retailer`. La distinción registry /
  CronJob / catálogo hay que rehacerla **por entorno**, porque dev y QA divergen por diseño.
- **`price_history.retailer_min_30d` (0018) es el primer dato del contrato que no observamos
  nosotros**: es el mínimo de 30 días que la tienda **declara** por la directiva Ómnibus. Importa
  porque el detector de descuentos engañosos vivía de una sola fuente —nuestro propio histórico—,
  con la limitación de nacimiento de no poder decir nada de antes de que empezáramos a mirar. Ahora
  hay con qué contrastar, y **la discrepancia entre ambas cifras es en sí misma la señal**: medido
  en C&A el 02/08/2026, **67 de 364** variantes con precio tachado anuncian descuento mientras la
  propia tienda declara haberlas vendido más baratas dentro de esos 30 días. La pueblan **dos**
  tiendas, y la que más no es la que se documentó primero: medido en QA el 06/08/2026, `springfield`
  **17 714 de 17 776** filas y `c-and-a` **1426 de 8341**; las otras siete, cero. `NULL` significa
  **«esta tienda no lo declara»**, nunca «no hubo mínimo», y ninguna consulta puede tratarlo como un
  cero — con dos tiendas poblándolo de forma tan desigual, confundirlos sesga hacia Springfield
  cualquier comparación. Un tercer candidato a vigilar: H&M sirve `priorPrice` en el nivel superior
  del producto (no dentro de `prices[]`, que es donde se miró), con `redPrice < priorPrice <
  whitePrice` en los casos vistos — pero solo en la sección de adulto, porque en infantil no hay ni
  una rebaja (0 de 118 197 filas en 30 días, #106). Se captura desde la primera pasada aunque el detector aún no lo use,
  porque **el histórico no se reconstruye hacia atrás**.
- **`cacles` es la primera tienda barefoot NATIVA**, y entró porque el foco barefoot (#30) dejaba la
  zapatería casi vacía: las otras tres son cadenas de moda convencional y entre ellas sumaban ~92
  referencias respetuosas. Eso convierte «tienda entera barefoot» en un caso que el modelo tiene que
  soportar, no una rareza — de ahí `classify(tienda_barefoot=True)` en `barefoot.py`, que es la
  tercera vía junto a la categoría propia de la tienda (Zara, Lefties) y la heurística de texto
  (Sfera). Se declara a nivel de tienda **en vez de** usar `category="barefoot"`: ese slug dejaría
  todo el catálogo bajo una sola categoría y mataría la faceta. Cacles es la primera tienda donde
  categoría y respetuosidad son ejes ortogonales; en Zara y Lefties siguen mezclados.
- **La heurística de texto tiene techo, y un cero redondo no es un fallo suyo.** Medido en Mango el
  04/08/2026 (#150): 137 zapatos en sus 10 hojas de `zapateria`, **0 con señal fuerte** y 3 con
  negativa, o sea toda la zapatería de la tienda en `desconocido` e invisible bajo el filtro por
  defecto. No había nada que arreglar — Mango no vende calzado respetuoso, y su menú tampoco publica
  ninguna hoja de ello (`--tree ninos`: 272 categorías, cero coincidencias). Lo que sí hay es una
  **trampa medida para el que venga a ampliar el vocabulario**: las viñetas de estas cadenas
  describen estética, no construcción, y «Punta redonda» sale en **92 de los 137**, así que meter en
  `_DEBILES` lo que *suena* a barefoot le pondría media señal a dos tercios del calzado. La regla de
  las dos señales débiles es lo que sostiene el sesgo de `barefoot.py` (5 productos con «Puntera
  redondeada» suelta se quedan en `desconocido`, correctamente). Consecuencia de producto: **una
  cadena de moda convencional puede aportar cero a la zapatería y eso es lo correcto**; lo que no
  puede es pasar inadvertido, y quien lo detecta es el caso D8 de `/validar-qa`, no el scraper.
- **Y el techo no se levanta dándole más texto**, que es el reflejo evidente ante un cero. Segunda
  medida, Hipercor el 06/08/2026 (#222): 286 zapatos, los 286 en `desconocido`, y ahí el scraper
  **sí** se estaba dejando texto sin leer —la ficha publica un bloque `attribute_groups` que
  `parse_pdp` ignora—. Se pidieron las 286 fichas y pasárselo al clasificador **no cambia ni un
  producto**: son 34 valores de una taxonomía cerrada (material exterior/interior, suela, puntera,
  cierre) que dicen **de qué está hecho** el zapato, no cómo está construido, que es lo único que
  la heurística puede usar. O sea que el límite no es cuánto texto se lee, es qué publican estas
  cadenas. Con dos tiendas medidas la conclusión operativa es: **ante un cero, medir el texto
  disponible antes de tocar `barefoot.py`, y esperar que la respuesta sea que no hay nada que
  hacer.** La trampa de vocabulario se repite idéntica —«Puntera Redonda» en 128 de 286 (45 %),
  que no casa con «puntera redondeada» de `_DEBILES` y que aflojarla marcaría `si` a 4 lonas de
  bebé—, y tampoco existe la vía preferente: Hipercor no declara el concepto en ninguna hoja ni
  faceta, igual que Mango.
- El **web** expone `/api/catalog/*`, `/api/interests`, `/api/settings/telegram`, `/api/config`,
  `/api/health`, y el job `dist/jobs/matching.job.js` que evalúa ofertas y notifica por Telegram.
- `services/web/src/database/schema.ts` (Drizzle) es un **espejo** del SQL, no la fuente de verdad.
  Los tres puntos — `db/migrations`, ese espejo, y el SQL crudo de `ingest.py` — pueden divergir en
  silencio; existe el subagente `revisor-contrato-esquema` precisamente para eso. **El espejo
  declara el contrato entero, no solo lo que el web consulta**: que una tabla no se lea desde aquí
  es justo lo que hace que el día que se necesite alguien escriba una migración que ya existe. La
  única ausencia deliberada es `schema_migrations`, que no está en `db/migrations` — la crean los
  dos aplicadores para su propia cuenta. Y la deriva no era hipotética: el barrido mecánico del
  14/08/2026 (#364) encontró cuatro huecos, el más viejo desde la `0001` —la tabla `scrape_run`
  entera—, todos invisibles porque nadie los leía desde el web. Por eso el revisor ahora empieza
  por `.claude/agents/barrido-espejo.py`, que compara el espejo contra `information_schema` columna
  a columna en vez de fiarlo a leer: el ojo encuentra lo que va buscando, y `missing_streak` salió
  de rebote revisando otra cosa.

**Las migraciones tienen dos aplicadores, ambos idempotentes**: el scraper con `--migrate` y el web
con `node dist/database/migrate.js` (initContainer del Deployment). Cualquiera de los dos puede
aplicarlas; ninguno es "el dueño".

**Y por eso el esquema y el código del scraper avanzan a velocidades distintas, con una ventana en
medio en la que una columna nueva existe y nadie la escribe.** El initContainer del web aplica la
migración **en el instante de la promoción**; el código del scraper que rellena esas columnas no
llega hasta que **cada CronJob vuelve a dispararse**. En QA esa ventana dura hasta **siete días**
(pasadas los lunes); en prod, horas (diarias). Medido el 10/08/2026 validando v0.2.0: el
`release-qa` aterrizó a las **11:39 UTC**, después de las nueve pasadas semanales (03:00 → 06:45),
así que la `0028` estaba aplicada y las cinco columnas `probes_*` salían a **0 en las nueve
tiendas** — no porque el código no escribiera, sino porque ninguna tienda había ingerido todavía
con la imagen nueva (`kubectl get pod <pasada> -o jsonpath='{...containers[0].image}'` lo dice, y es
la comprobación que zanja la duda). La consecuencia al validar: **un valor por defecto uniforme
justo después de una migración no prueba que el código esté roto**, y confundir las dos cosas es un
P0 falso. Si hay que ejercerlo antes de la siguiente pasada natural, se dispara a mano
(`kubectl -n <ns> create job <nombre> --from=cronjob/deal-tracker-scraper-<slug>`).

**Todo lo `KEYCLOAK_*` y `TELEGRAM_*` es opcional a propósito**: sin ellas la auth queda apagada
(la SPA funciona como catálogo público, los endpoints de usuario dan 401) y el job de matching
fuerza `--dry-run`. Así corre `dev`.

## PATTERNS

### El seam con el repo de manifiestos (contexto que ningún repo documenta entero)

El despliegue **no vive aquí**. Vive en `juanjocop/k3s-local-apps-manifests`, bajo
`deal-tracker/{base,overlays/dev,overlays/qa,overlays/prod}` (Kustomize + ArgoCD, auto-sync
prune+selfHeal). Este repo produce imágenes; aquel decide qué corre.

**Promoción dev → QA, el flujo completo:**

1. Push a `main` → `scraper-ci.yml` / `web-ci.yml` construyen **multiarch** (`amd64` + `arm64`) y
   publican en GHCR con tag `sha-<7>`. **En un PR se valida solo `amd64`** (#61).
2. Job `bump` (gated por la variable de repo `ENABLE_BUMP=true`, usa el secret `GITOPS_PAT`) hace
   checkout de `juanjocop/k3s-local-apps-manifests` en `path: gitops` y reescribe con `yq` el
   `images[].newTag` de **su** imagen en `deal-tracker/overlays/dev/kustomization.yaml`, commit y push.
3. ArgoCD auto-sincroniza el namespace `deal-tracker-dev`.
4. `release-qa.yml` (**manual**, `workflow_dispatch`, solo desde `main`, valida semver `vX.Y.Z`)
   lee del kustomization de **dev** qué sha corre hoy, re-etiqueta esas imágenes en GHCR
   **por digest** (copia la manifest list multiarch, sin pull ni rebuild), crea tag + GitHub
   Release, y reescribe `overlays/qa` a `vX.Y.Z`.

Consecuencia: **dev sigue `sha-<7>`, QA sigue semver**, y el binario de QA es bit a bit el que se
validó en dev. Los `newTag` de ambos kustomizations son **machine-edited — no editar a mano**.

**La cadena acabó en QA durante meses sin ninguna puerta funcional, y por eso existe `/validar-qa`**
(skill en
`.claude/skills/validar-qa/` + tres subagentes `validador-qa-*`, 04/08/2026). Lo que hay antes no
mira lo desplegado: el CI del web valida lint, typecheck y vitest, y del frontend **solo que
compila** —no hay ni un test de navegador en el repo—, y los e2e corren contra una Postgres sembrada
a mano, con el `JwtAuthGuard` sustituido por un usuario falso y el locale de CI, no contra el
`UTF8 | C | C` del cluster. `release-qa` promueve por digest y su «gate humano» es lanzar el
workflow. Consecuencia medida sobre v0.1.5, solo con mirar: Cacles con la última pasada en `failed`
por el 429 de huella TLS (#120), Hipercor **sin una sola fila** en `scrape_run` y sin fila siquiera
en `retailer`, con sus dos jobs muertos por `DeadlineExceeded` (#93), **7 de 9 tiendas** en
`/api/catalog/facets`, y una hoja de categoría de Sfera muerta escondida dentro de `errors = 15`.
Ninguna alarma se disparó.

Dos decisiones de diseño de esa puerta que no son obvias: el frente de UI **corre solo** porque el
navegador de Playwright es un MCP único y dos agentes se pisan las pestañas; y el veredicto tiene
tres valores, no dos — un frente que no se pudo ejercer da **NO CONCLUYENTE**, nunca APTO, porque
aprobar por omisión es la única forma real de que la puerta haga daño. Los informes se versionan en
`.claude/qa-reports/<version>.md` por su bloque de cifras: es la línea base contra la que la
validación siguiente detecta que una tienda pasó de 3381 productos a 40 con la pasada cerrando en
`success`, que es el daño que ninguna otra comprobación ve.

**El último eslabón, QA → prod, NO es una promoción, y confundirlo lleva a diseñar el workflow que
no era** (08/08/2026, #267). `release-qa` tiene trabajo que hacer porque dev corre `sha-<7>` y QA
necesita un `vX.Y.Z`: copia la manifest list al mismo digest. **Prod consume exactamente el mismo
tag que QA ya está corriendo**, así que no hay nada que re-etiquetar y `release-prod.yml` no lleva
`imagetools create`. Lo que es de verdad es un **gate**, con cinco comprobaciones que abortan:
la versión existe como Release publicada y **no va hacia atrás** (GATE 0, #306) · el informe dice
APTO · QA corre esa versión (si ya no la corre, el informe no habla de lo que se promueve) · las dos
imágenes existen en GHCR · el overlay de prod existe (aquí **falla**, mientras que `release-qa`
salta con un warning: la ausencia del de QA era un «todavía no», la del de prod es que alguien
renombró el contrato).

**El GATE 0 llegó el último y tapa lo único que ningún otro miraba: la dirección** (14/08/2026,
issue #306). La versión llega de un campo de texto libre, y los cuatro gates originales comprueban
*qué* se promueve pero no *hacia dónde se mueve prod*: `v0.1.8` tecleada por error los habría
pasado todos el día que tuviera informe APTO, y habría sido un downgrade silencioso. Se rechaza lo
que no tiene Release publicada —o la tiene en `draft`/`prerelease`— y lo que no es `>=` la `latest`
actual, comparando por `sort -V` porque un orden de texto pondría `v0.4.0` por encima de `v0.10.0`.
Dos matices que no son decorativos: es **`>=` y no `>`** a propósito, porque relanzar el workflow
sobre la versión que prod ya corre es un caso real y soportado —el estreno mismo fue con `v0.1.9`,
ya desplegada— y un `>` estricto rompería esa idempotencia sin bloquear ningún rollback que no
estuviera ya bloqueado; y **el GATE 0 no sustituye al GATE 1**, porque mira el flag `prerelease`,
que es la señal y nunca la decisión. Es un filtro barato que va **antes de los checkouts** y falla
en segundos; la autoridad sigue siendo el fichero. Para volver atrás hará falta otro camino: este
workflow solo avanza, y desde #306 eso está escrito y ejercido, no supuesto.

**Y el flag `prerelease` ha dejado de ser eterno, que es la otra mitad de #306.** Aquí una
`prerelease` viva no es un archivo histórico sino un intento fallido —`/validar-qa` baja el flag
*solo* al escribir un informe APTO, así que lo que sigue marcado o tuvo veredicto en contra o nunca
se miró—, y llegaron a acumularse **nueve, ninguna promocionable**. Desde #306 `release-qa` termina
llamando a `prune-prereleases.yml`, que conserva las **5 más recientes por semver** (`gh release
list` ordena por fecha, y un re-tag desordenaría la ventana) y borra el resto con su tag git. Va
como workflow reutilizable y no como paso, para que el `workflow_dispatch` manual que vació el
atraso y el automático sean **el mismo código**; y con `continue-on-error`, por lo mismo que la
retención de GHCR: la limpieza nunca debe tumbar la release. **Lo que no toca es lo que importa**:
ni las no-`prerelease`, ni `latest`, ni una sola imagen de GHCR —ese eje es #283, y borrar allí es
peligroso por otro motivo—, ni los informes, que son el registro de *por qué* aquella versión fue
defectuosa y la línea base de la siguiente. Hay además un suelo duro de 2, y conviene leer bien su
motivo porque el intuitivo es falso: **no es una ventana de rollback**. Una `prerelease` no es
promocionable por construcción, y el rollback de prod vive en las no-`prerelease` y en las imágenes.
El suelo está para que un `keep` mal tecleado en el dispatch no se lleve de golpe la historia de qué
se cortó y por qué.

**La retención de imágenes en GHCR es la política de rollback, y hasta el 14/08/2026 no estaba
escrita en ningún sitio** (issue #283): vivía en dos líneas de YAML que llegaron con el primer CI y
que nadie había vuelto a mirar. Hay **cuatro ejes que se mueven por su cuenta** —el `sha-<7>` en
GHCR, el `vX.Y.Z` en GHCR, el tag git con su GitHub Release, y el informe de QA— y solo el primero
tenía techo. Que derivan entre sí no es teórico: `v0.1.6` se promovió a QA y **se quedó sin
informe**, con informes a ambos lados.

La decisión, con lo medido delante: **las `v*` no se podan, y eso es deliberado**. El overlay de
prod pinta un *tag*, no un digest, así que la ventana de `v*` que sobreviva **es** la ventana de
rollback de prod: borrar una no deja un Release sin artefacto, deja un overlay que no puede recrear
el pod si ArgoCD resincroniza o el nodo reprograma. Y `exclude-tags: latest,v*` tiene un efecto de
rebote que conviene dejar dicho porque hoy se cumple por accidente: la `vX.Y.Z`, su `sha-<7>` y
`latest` comparten *version*, así que **el sha de una release promocionada está protegido también**.
Lo que sí se subió es la cola de `sha-<7>`, de 10 a **30**, y el motivo es un número: en los siete
días anteriores hubo **15 publicaciones del scraper y 26 del web**, o sea que con 10 dev no podía
volver a lo que corría hacía cinco días —tres en el web—, y dev es el único entorno que corre un
sha. Pasado ese umbral **la imagen ya no existe y dev no tiene rollback**; ahora es una decisión
escrita y no un olvido.

Y si algún día se poda una `v*`, hay dos trampas medidas que cualquier implementación tiene que
respetar. La primera: **`keep-n-tagged` cuenta solo las versions no excluidas**, así que la
retención no acota el package sino únicamente la cola de sha — cada release saca una version del
ciclo *para siempre*, y las inmunes ya son mayoría (12 de 22 en el scraper, 13 de 24 en el web) con
una fracción que solo sube. La segunda, que es la peligrosa: **GHCR borra versions, no tags**, y hay
versions que cargan dos releases a la vez —la `1106121923` del web lleva `v0.1.8` **y** `v0.1.9`, la
que sirvió prod; la `1055695859` del scraper lleva `v0.1.0` y `v0.1.1`—, así que podar «la vieja»
por fecha se lleva la buena por delante. Cualquier ventana tiene que **resolver tags a digest antes
de borrar y negarse si la version carga alguna `v*` protegida**, borrar en las dos packages a la vez
(que sobreviva una y no la otra es peor que borrar ambas), y dejar en pie el tag git, el Release y
sobre todo el informe. El criterio de *qué* podar lo aporta la poda de prereleases: **una imagen
`v*` cuyo Release ya no existe es la primera candidata evidente**, y desde el 14/08 ese conjunto ha
dejado de estar vacío — son las cuatro de `v0.1.0`…`v0.1.3`. No se han borrado: la política es lo
que faltaba, no el espacio.

**Y la autoridad de que una versión vale para producción es el informe commiteado, no el flag
`prerelease` de GitHub.** Los dos dicen lo mismo y solo uno deja rastro: el flag lo cambia
cualquiera con dos clics y no aparece en ningún diff; `.claude/qa-reports/<version>.md` está en git
y se revisa como cualquier otro fichero. Así que `/validar-qa` asciende el flag al escribir un
informe APTO —es la señal, para ver de un vistazo cuál pasó— y `release-prod` verifica **el
fichero**. De ahí sale un comportamiento que conviene leer como diseño y no como suerte: una
versión que se cortó en QA y **nunca se validó** no tiene informe, así que el gate la rechaza. El
silencio no promueve.

Un detalle de implementación que parece un tecnicismo y es el fallo entero: el veredicto se extrae
y se compara **completo**, porque un `grep APTO` a secas también casa con `NO APTO` — que es
exactamente el caso a rechazar. Medido el 08/08/2026 sobre los cuatro informes reales: `v0.1.9`
pasa los cuatro gates y el bump sale vacío (prod ya la corría, puesta a mano en el estreno del
07/08); `v0.1.8` muere en el primer gate con los cinco pasos siguientes en `skipped` y su release
intacta en `Pre-release`. **Con el GATE 0 delante, esas dos medidas cambian de sitio y conviene no
leerlas literales**: `v0.1.8` muere ahora antes, sin llegar a clonar nada, y `v0.1.9` ya **no
pasaría** — `latest` es `v0.4.0` y sería ir hacia atrás, que es justo lo que el gate nuevo existe
para impedir. **La ruta `deal-tracker/overlays/prod` es contrato entre los dos repos**,
igual que la de QA.

**En prod la ingesta se enciende antes que la notificación, y esa asimetría es deliberada.** Prod
estrenó el 07/08/2026 sirviendo lo que ingirió *una* pasada manual de Zara, y el 08/08 se
encendieron los nueve scrapers (banda diaria propia 21:00→01:00) dejando el **matching apagado**.
El coste de equivocarse no es simétrico: un catálogo pobre es una web fea y se arregla con la
pasada siguiente; un aviso mal es un mensaje que ya salió. Por eso el motivo va escrito **junto al
propio `suspend`** en `patch-matching.yaml` — suelto en un entorno recién estrenado se lee como un
olvido y el siguiente que pase lo enciende.

**Duró horas, y lo que lo encendió fue medir el gate en vez de creerlo.** El 08/08 se comprobó que
la premisa —«la llegada de un aviso no se ha ejercido nunca»— era falsa: en QA hay **17 avisos
entregados entre el 04 y el 06/08**, y una fila de `notification` superviviente es prueba de
entrega, no de intento, porque el envío reserva antes de mandar y suelta lo que Telegram no acepta.
Esto **ya estaba en este ADR** («El aviso no se puede provocar a voluntad», en PATTERNS, medido el
04/08); lo que faltaba era que la issue lo supiera. Dato para la próxima: cuando algo lleve semanas
bloqueado por «no se ha probado nunca», el ADR suele saber más que la issue.

Lo que sí era nuevo es **cuánto más estrecho** era lo que quedaba sin ejercer —el colapso de caras
duplicadas (#108/#121)— y que tampoco se puede provocar: **2114 variantes con rebaja creíble en QA
y ninguna en un grupo de dos caras**. A las causas ya documentadas en aquella sección se suma la que
las cierra: el caso de contraste (dos caras con URL distinta, que deben dar **dos** avisos) vive
**solo en H&M** —1007 grupos, ninguna otra tienda tiene uno— y H&M es justo la que no publica
tachado (#106), así que solo avisa si el precio baja de verdad.

De ahí la lección que ordena el resto: **un gate que no se puede ejercer a voluntad no es un gate,
es un bloqueo indefinido disfrazado**, y conviene reconocerlo antes de atarle lo único que el
producto hace. #122 pasó de gate a vigilancia —la señal de cierre no cambia, deja de haber alguien
esperándola— y la apuesta razonable es que el primer colapso real se vea en **prod**, que ingiere a
diario contra el semanal de QA.

**El corolario que ya ha mordido dos veces: en QA, capacidad nueva ≠ capacidad disponible.** Como
QA solo avanza con un `release-qa` manual, todo lo que se mergea a `main` llega a dev al instante y
a QA **nunca**, hasta que alguien corta versión. Así que un CronJob nuevo activado en el overlay de
QA se programa contra una imagen que aún no tiene el código: el vigía habría fallado por
`ModuleNotFoundError` (#67) y el scraper de C&A por `ValueError: Tienda desconocida` (#78) — los
dos, un fallo garantizado a fecha fija. La regla es que **el CronJob de una capacidad nueva nace
`suspend: true` en QA aunque sus hermanos estén encendidos**, con el motivo y los dos pasos
(cortar release → poner `false`) escritos en el propio patch, no solo en el PR. En dev no aplica:
el bump es automático.

**Y la otra mitad de esa puerta: `release-qa` no promueve lo que hay en `main`, sino lo que dev
ESTÁ CORRIENDO.** El workflow lee el `newTag` de `overlays/dev/kustomization.yaml` en el repo de
manifiestos y le cuelga el tag semver a ese mismo digest (`imagetools create`, sin rebuild). Entre
mergear a `main` y que dev lo corra hay dos pasos que no son instantáneos —el build multiarch, 10-13
min medidos, y el bump del overlay— más lo que tarde ArgoCD en sincronizar. Medido el 07/08/2026
promoviendo v0.1.9: el `scraper-ci` del merge seguía `in_progress` y dev aún corría el sha anterior;
lanzar el `release-qa` en ese momento habría publicado una **v0.1.9 con el scraper de antes del
arreglo**, con la validación cantando el mismo bloqueante por cuarta vez y el diagnóstico apuntando
a cualquier sitio menos al build. La regla que queda: **la señal de que se puede promover no es el
CI en verde, es que el CronJob de dev muestre el sha del merge**. Entre las dos cosas está el bump,
y el bump es automático pero no inmediato — que es exactamente el matiz que «en dev no aplica» se
come si se lee deprisa.

**Pero esa regla tiene un falso negativo, y es esperar un sha que no va a existir nunca.** Los dos
workflows filtran por `paths`: `web-ci` solo dispara con `services/web/**`, `db/migrations/**` o su
propio `.yml`, y `scraper-ci` con `services/scraper/**`, `db/migrations/**` o el suyo. Así que **un
merge a `main` que no toque ninguna de esas rutas —documentación, `README.md`, `CLAUDE.md`,
`.claude/**`— no construye imagen ni dispara el bump**: no genera *ningún* run. Dos consecuencias
que la lista de arriba no deja ver porque describe el flujo como incondicional:

- **El `sha-<7>` que corre dev no es la punta de `main`, y no tiene por qué serlo.** Medido el
  11/08/2026: `main` en `bb7afeb` (merge del PR #339, solo `README.md`), último run de `web-ci` en
  `main` sobre `00ad732` (merge del #338), y el overlay de dev en `sha-00ad732`. Quien aplique la
  regla de arriba tras un merge de documentación esperará indefinidamente.
- **Las dos imágenes van naturalmente desincronizadas**, porque cada workflow solo mira su servicio:
  en esa misma medida, el web iba en `sha-00ad732` y el scraper en `sha-394c314`. Que difieran no
  indica nada roto.

Y en el lado del PR el mismo filtro da un tercer estado que no es ni verde ni rojo: un PR de solo
documentación sale con **0 checks** (`gh pr view <n> --json statusCheckRollup` devuelve lista vacía;
el PR #337, de solo `CLAUDE.md`, y el #339 lo confirman). No es que falten por salir — no van a
salir. Lo que decide ahí es `mergeable`/`mergeStateStatus`, no la espera de un verde.

**QA no tiene Keycloak propio: se autentica contra el realm de dev.** `/api/config` de QA devuelve
`https://keycloak-dev.liontechsolution.com` y realm `deal-tracker-dev` — el mismo client
`deal-tracker-web` que usa dev. Los dos entornos comparten identidad y configuración de client, así
que **tocar ese client afecta a los dos**, y un fallo de config que parece «de dev» tumba QA sin que
nada en el overlay de QA lo insinúe. Medido el 06/08/2026 validando v0.1.7 (#219): el campo **Web
Origins** llevaba `https://dealtracker-qa.liontechsolution.com/*`, sintaxis de *redirect URI* donde
Keycloak espera un **origen desnudo**; como el navegador manda `Origin` sin ruta, no casaba, y
Keycloak no emitía `Access-Control-Allow-Origin` en la respuesta real de `/token`. Login roto para
todo usuario real de QA.

Dos trampas de diagnóstico que salen de ahí y valen para cualquier CORS contra Keycloak, porque las
dos llevan a concluir lo contrario de lo que pasa: el **preflight `OPTIONS` no prueba nada** —refleja
cualquier origen, incluso uno inventado—, y el dato bueno es el claim `allowed-origins` del propio
token. Y **sin navegador no hay CORS**: `.claude/qa-login.py` hace exactamente el mismo flujo que la
SPA (Authorization Code + PKCE, mismo client, mismo redirect URI) y funciona, así que el frente de
API de `/validar-qa` puede pasar sus 51 casos autenticados en verde con el login roto para todo el
mundo. Es justo el punto ciego que obliga a que el frente de UI se ejerza en un navegador de verdad.

**Y ese Keycloak no lo gobierna ninguno de los dos repos de este proyecto.** Vive en un tercero —
`open-liontechsolution/toolsuite-platform-gitops`, path `apps/security/keycloak`, chart `keycloakx`,
desplegado en `security-dev/keycloak-dev-0`—, así que el contrato de dos repos que describe este ADR
tiene un tercer vértice del que depende todo el login. Es **una sola instancia para los tres
entornos**: lo que se separa es el realm, no el servidor, así que si esa instancia cae, cae también
el login de producción. Está aceptado a cambio de no desplegar un Keycloak más.

**Y desde el 12/08/2026 el realm SÍ está declarado en git, lo que invierte lo que este ADR decía
aquí.** Hasta entonces la configuración de realms y clients existía solo en la Postgres de Keycloak
—el StatefulSet arranca sin `--import-realm`— y la conclusión era que «nada en git delata una
regresión de esa config». Ya no: `apps/security/keycloak/realms/*.yaml` los declara y
**keycloak-config-cli** los aplica por partida doble, con un Job `PostSync` en cada sync y un
**CronJob nocturno a las 04:00** (toolsuite `#49` y `#50`, las dos cerradas). Ese CronJob corre con
`cache.enabled: false` a propósito: por defecto config-cli cachea un checksum del fichero y se
saltaría la aplicación cuando el fichero no ha cambiado, que es justo el caso de un cambio hecho a
mano en la consola. Con la caché apagada, **la reconciliación revierte de verdad**. El Web Origins
roto de #219 ya no puede volver en silencio.

Lo que **no** cambia de arriba: QA sigue autenticándose contra el realm de dev. Lo que sí, y hay que
tenerlo presente al leer el párrafo de #219: **producción estrenó realm propio**,
`deal-tracker-prod`, con su client `deal-tracker-web` (mismo nombre, otro realm, no colisionan). Ese
era el punto de la #50 — compartiendo realm, el fallo que tumbó QA se habría llevado por delante
producción. El contrato con este repo son dos valores del SealedSecret de `overlays/prod`:
`KEYCLOAK_ISSUER_URL=https://keycloak-dev.liontechsolution.com/realms/deal-tracker-prod` y
`KEYCLOAK_AUDIENCE=deal-tracker-web`. Si el nombre del realm o del client cambia allí, aquí deja de
validar ningún token.

**No hay roles, y conviene saberlo antes de buscarlos.** La autorización del backend es
`issuer` + `aud` + que el token traiga `sub`, y nada más: no hay `RolesGuard`, ni `@Roles`, ni una
lectura de `realm_access`/`resource_access` en todo `services/web/src` — `KeycloakClaims` solo
declara `sub`, `email`, `name` y `preferred_username`. El realm de producción tampoco tiene ningún
rol propio, solo los tres que Keycloak crea solo. **El scoping de los datos de usuario es por
`app_user.id`**, no por rol. Así que dar de alta a alguien no lleva paso de roles, y añadir uno en
Keycloak esperando que cambie algo no cambiaría nada.

**Los usuarios son la excepción declarativa, y por la razón contraria a la intuitiva.** No se
declaran en `realms/`: la razón conocida es que config-cli no sabe borrarlos (`UserImportService`
registra «Purging users isn't supported» incluso con `users: []`), pero la de peso es que **ese
mismo CronJob de las 04:00 que hace segura la config del realm haría insostenible la baja de un
usuario** — volvería a habilitar cada noche a quien se hubiera deshabilitado. Se gestionan con
`scripts/keycloak-user.sh` de aquel repo (contraseña temporal + `UPDATE_PASSWORD` forzado; la baja
deshabilita en vez de borrar, porque el `sub` es la clave de `app_user` y borrarlo dejaría huérfanas
sus filas de `interest` y `notification`). El porqué completo, en su `docs/KEYCLOAK_USERS.md`.

**Y un alta no crea usuario en la aplicación.** `JwtStrategy.validate()` aprovisiona la fila de
`app_user` **en la primera petición autenticada**, no al crear el usuario ni al iniciar sesión en
Keycloak. Medido el 12/08/2026: el realm de producción tenía dos usuarios —ambos con
`UPDATE_PASSWORD` pendiente, o sea sin haber completado nunca el primer acceso— y `app_user` en
`deal_tracker_prod` estaba **vacío**. Si hay que comprobar que un alta llegó a su destino, el sitio
es esa tabla y no Keycloak.

Para verificar una credencial sin navegador, el client `deal-tracker-web` no sirve: lleva
`directAccessGrantsEnabled: false` a propósito (solo PKCE). El `admin-cli` del propio realm sí
admite `password` grant, y sus dos errores **no significan lo mismo** — `invalid_grant: Account is
not fully set up` dice que la contraseña **es correcta** y que lo que bloquea el token es el
`UPDATE_PASSWORD` pendiente, mientras que `Invalid user credentials` dice que no vale. El primero es
el resultado esperado de un alta recién hecha, y es la única forma de probar sin navegador que la
credencial se acepta y que el cambio forzoso se está ejerciendo.

**El arm64 solo se compila en `main`, y eso es deliberado.** El cluster son Raspberry Pi, así que
la variante arm64 es obligatoria para desplegar; pero emularla con QEMU en cada PR costaba ~9 min
por servicio *y se tiraba* (los PR construyen con `push: false`). Medido: el job `image` de un PR
pasó de 9m12s–11m20s a 19-20s en el scraper y de 2m43s–8m11s a ~1 min en el web; un PR que toca
los dos servicios, de 19m19s a 1m21s. La red de seguridad es el **orden**: el build multiarch de
`main` va *antes* del `bump`, así que si arm64 rompe el job falla, el `newTag` no se reescribe y el
cluster se queda con la imagen anterior — se rompe la entrega, no el entorno. Lo que no se puede
dar por hecho: **un check verde en un PR no prueba que la imagen arm64 exista**, y la caché de
buildx solo se escribe desde `main` (lo que escribe un PR solo lo ve ese PR, pero desaloja por LRU
la caché multiarch que leen todos).

**Contrato de secretos**: un único SealedSecret `deal-tracker-config` por namespace, con las claves
`DATABASE_URL`, `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE` (+ `TELEGRAM_*` en QA), más `ghcr-pull`
para tirar de GHCR privado. Los SealedSecrets son **namespace-bound**: los de dev no valen en QA.
Si se añade una variable de entorno nueva al web o al scraper, hay que sellarla allí o el pod
arranca sin ella.

**Convención base/overlay**: `base` nunca se aplica directa y trae **defaults seguros** —
cronjobs con `suspend: true` y matching con `--dry-run`. El overlay de QA los levanta con patches.
Dev se queda con los defaults y se dispara a mano:
`kubectl -n deal-tracker-dev create job X --from=cronjob/deal-tracker-scraper-zara`.

Desde el 02/08/2026 **QA corre los cronjobs con cadencia semanal** (lunes, 05:15→07:00,
conservando los desfases de base) en vez de la diaria — todos menos `c-and-a`, por lo del párrafo
anterior. Estuvieron suspendidos meses con el
argumento de que QA no es prod; el argumento se sostiene, el efecto colateral no: sin pasadas,
`price_history` no crecía, y **sin re-observaciones el detector de descuentos inflados no tiene con
qué comparar** — el propio dato que da sentido al producto. Semanal es la cadencia mínima que
resuelve eso sin pedirle a las tiendas siete veces lo mismo. Ojo a lo que enciende: el matching de
QA, sin `--dry-run` y con `TELEGRAM_BOT_TOKEN`, **manda mensajes reales**.

La única excepción a `suspend: true` en `base` es el **vigía** (ver más abajo): un vigía pausado no
vigila. **Solo puede haber uno**, no por prudencia sino porque los tres namespaces comparten
cluster y salen a internet por la misma IP — preguntarlo dos veces es el doble de peticiones a
cambio de cero señal. Lo corre **prod** desde el 07/08/2026 (antes, dev lo pausaba y lo corría QA);
manda el entorno cuya rotura importa. El apagado de QA se hizo **en el mismo paso** que el
despliegue de prod: antes habría dejado el cluster sin ninguno mientras prod no existiese, después
habría abierto una ventana con dos.

**Un CronJob por tienda**, porque los perfiles divergen: Zara es httpx (1 CPU / 1Gi), Sfera arrastra
Chromium (2Gi, `emptyDir` escribible, `HOME`/`TMPDIR` redirigidos, `runAsUser: 10001`). Comparten
imagen (~900 MB), así que el primer arranque en un nodo nuevo paga ~2m20s de pull. **El perfil se
decide por cómo scrapea la tienda, no por ser un scraper**, y el rango es amplio: la pasada en frío
de Zara son 2219 peticiones de detalle y 30 min, la de C&A son 46 peticiones, 37 s y 60 MiB de pico
de RSS. Copiar el manifiesto de Zara para una tienda nueva sobredimensiona.

**El eje que decide el coste son las peticiones por ficha, no si hace falta navegador.** Es la
intuición equivocada más fácil de tener aquí, porque el primer scraper con Chromium (Sfera) llegó
pidiendo 2Gi. Medido el 02/08/2026 con dos tiendas que van **las dos** por Chromium tras Akamai:
Hipercor tarda **3 h 26 min** (navega una vez por ficha: 1224 navegaciones) y Lefties **3 min 2 s**
(699 productos, pero el detalle va en lotes de 20 ids y el listado son 38 peticiones sin paginar).
Setenta veces de diferencia con la misma tecnología. Y el pod va capado a 1 CPU, así que el coste
por ficha medido en la máquina de desarrollo va **×2** en el cluster (medido en Hipercor: 1 h 42
extrapolada → 3 h 27 real, estrangulado de principio a fin). Consecuencia para el
`activeDeadlineSeconds`: dimensionarlo por la **pasada en frío**, que se paga una vez pero decide si
la tienda llega a existir — la ingesta es atómica, así que pasarse no es perder una pasada, es que
el catálogo no se pueble **nunca**. Hipercor consumió el 115 % de su deadline (no cabía) y Lefties
el 5 % del suyo.

**Base de datos real**: cluster CNPG `platform-postgres-dev` en el namespace `data-dev` — *no* el
`postgresql-generic` del cluster. **Los tres entornos lo comparten**, con una base por entorno
(`deal_tracker`, `deal_tracker_qa`, `deal_tracker_prod`); prod no estrenó cluster por coste, y el
disparador para revisarlo que quedó escrito no es el tamaño sino **quién la usa** — cuando entre
gente de fuera de la familia. QA y prod son públicos en `dealtracker-qa.liontechsolution.com` y
`dealtracker.liontechsolution.com`, pero **por túneles distintos**: `k3s-nonprod` para dev y qa,
`k3s-prod` para producción, para que un error en Zero Trust sobre uno no alcance al otro (la ruta
se configura en ese panel, no en Git).

### Canonicalizar el texto de las tiendas: función SQL, nunca el dato

Cada tienda escribe la talla y el color a su manera (`26` / `26 (16,3 cm)`, `Verde` / `VERDE` /
`120 Crudo`). El matching y el filtro comparaban por **igualdad exacta de texto**, así que un interés
no casaba con la misma prenda de otra tienda: el aviso no llegaba y **no fallaba nada ruidosamente**.
Resuelto dos veces con la misma forma — `size_canon` (migración 0014) y `color_canon` (0015) — y esa
forma es ya el patrón para cualquier campo de texto que venga de las tiendas:

1. **Función SQL `IMMUTABLE` en `db/migrations`**, no en el scraper ni duplicada en TS: los
   consumidores (filtro del catálogo, faceta, JOIN del matching) son SQL, así que la canónica tiene
   que existir *dentro* de la consulta. Una sola implementación, y en el contrato.
2. **Se aplica solo a la COMPARACIÓN, jamás a la columna.** Además de conservar el texto que la ficha
   enseña, es una restricción dura: `product_image.color` está clavada por el **texto** de
   `variant.color` (migración 0011) y sostiene la foto de la tarjeta y la galería. Canonicalizar el
   dato rompería ese join en silencio. Desde la `0023` la galería se clava **además** por
   `product_image.variant_url` = `variant.url` (ver «Una variante no es siempre una cosa
   comprable»): la restricción no cambia de forma, gana un segundo eje con la misma —texto crudo a
   los dos lados, y quien escriba los dos tiene que sacarlos del mismo campo.
3. **Índice por expresión obligatorio**, parcial por `delisted_at IS NULL`. Medido sobre el volumen de
   dev (33.311 variantes): el filtro por color pasa de 14,6 ms a 0,11 ms; el de talla, de ~1 s a
   1,4 ms. Y la faceta debe deduplicar el texto crudo **antes** de canonicalizar (866 ms → 13 ms en
   talla, 32 ms → 14 ms en color), porque si no la función se evalúa una vez por variante.
4. **Idempotente**, y por eso se normalizan los dos lados de cada comparación sin razonar sobre cuál
   venía ya limpio. El alta de interés guarda ya canónico.
5. **Cambiar el cuerpo de una de estas funciones obliga a `REINDEX`** del índice por expresión: guarda
   los valores ya calculados y, obsoleto, devuelve filas equivocadas *sin dar error*. Que esté puesto
   se comprueba en un minuto y conviene hacerlo, porque olvidarlo no rompe nada visible: se cuenta el
   mismo filtro con y sin índice (`SET enable_indexscan/bitmapscan = off`) y los dos números tienen
   que coincidir. Medido en la 0017 dejando fuera el `REINDEX` a propósito: 0 contra 12.
6. **Plegar es perder, y a veces se pierde algo que distinguía de verdad (#331).** El patrón da por
   hecho que las formas que colapsan son la misma cosa escrita de dos maneras, y casi siempre lo es
   —`26` y `26 (16,3 cm)` son el pie 26—. Pero el paréntesis no siempre repite: H&M lo usa para
   **discriminar**, y `0-1 meses (44 cm)` y `0-1 meses (50 cm)` son dos alturas de recién nacido
   distintas que `size_canon` funde en `0-1 meses` desde la 0024. Son 9 productos de 12.870, todos de
   H&M (medido el 11/08/2026), y la consecuencia es que un interés en esa canónica casa con las dos
   alturas. Lo que hay que retener no es el caso sino la pregunta al escribir una regla de plegado:
   *¿lo que estoy borrando es redundante en TODAS las tiendas, o solo en las que miré?* La ficha se
   salva porque el selector rotula con el texto crudo (#248), que es otra vez la regla 2.
7. **El resultado depende del `ctype` de la base, así que hay que probarlo con el del cluster.** Las
   dos funciones empiezan plegando la caja, y `lower()` **no baja las letras acentuadas** bajo ctype
   `C` — que es el de la CNPG: `deal_tracker` y `deal_tracker_qa` son `UTF8 | C | C`. Se pliega con
   un `translate()` explícito (0021) y **nunca con `lower()` a secas**. Detalle en el apartado de
   abajo, que es donde está lo transportable.

**«Solo a la comparación» no significa «solo a la comparación»: lo que el usuario LEE también va
canónico** (#223). El punto 2 protege la *columna*, y se leyó como si autorizara devolver el texto
crudo a cualquier consumidor. No: la canónica es el vocabulario del producto, así que todo lo que
nombre una variante delante del usuario tiene que hablarlo. Lo crudo se conserva en la columna
—para el join de las fotos y para la ficha de la tienda—, no para rotular.

Importa porque los sitios que nombran una variante son **tres**: la lista de seguimientos
(`GET /interests`), el aviso de Telegram y el modal de «Seguir esta variante» de la ficha, que
comparten `variantLabel()` precisamente para no divergir. Los dos primeros devolvían la talla de la
tienda mientras la faceta, el filtro y el propio
`interest.size` guardado decían otra cosa: el usuario seguía una «Talla 24» y el bot le hablaba de
una «Talla 24 (14,9 cm)». La canónica ya venía calculada por la base en los dos casos —en el
`SELECT` de `findCandidates` viaja como `sizeCanon`, al lado de la cruda—, así que el arreglo no
costó ni una consulta: costó darse cuenta.

Esto **invierte una decisión anterior** que solo estaba escrita en un test («el mensaje enseña la
talla de la tienda, es lo que el usuario verá al abrir el enlace»), y conviene saber por qué se
cambió de opinión: la ficha del retailer enseña sus propias tallas de todas formas al otro lado del
enlace, mientras que la coherencia entre lo que el usuario sigue y lo que el bot le dice es lo
único que sostiene que sean la misma prenda. El color, en cambio, sigue crudo **a propósito**:
`color_canon` niega devolviendo `NULL` (0016), así que canonizar la etiqueta no normalizaría el
color — lo borraría.

**El tercero apareció arreglando los otros dos, y por eso la etiqueta la sirve la API** (#248). El
modal de la ficha rehacía la etiqueta a mano en TypeScript con `variants[].size`, así que al dejar
canónicos el backend y el bot quedó él solo diciendo la cruda: el usuario confirmaba «Talla 2 años
(92 cm)» y su lista le enseñaba «Talla 2 años». La regla que evita un cuarto sitio es que **el
detalle del catálogo emite `variantLabel` ya montado** —`GET /catalog/products/:id` lo calcula con
la misma función y con `size_canon(v.size)` en el `SELECT`—, en vez de que cada consumidor
concatene. Rehacerla en el frontend no era una duplicación cosmética: canonizar en TypeScript sería
una segunda definición de «misma talla», que es justo lo que el punto 1 prohíbe.

Y el corolario que no se ve solo: `variants[].size` **sigue saliendo cruda** en esa misma respuesta,
porque es lo que pinta el selector de tallas, y en ropa infantil el paréntesis que `size_canon`
borra (`2 años (92 cm)` → `2 años`) es por lo que un padre elige. Las dos formas viajan juntas — la
cruda para elegir, la canónica para nombrar — igual que ya hacía `findCandidates` con `size` y
`sizeCanon`.

**Un mismo texto puede significar cosas distintas, y la sección NO es lo que lo decide.** `size_canon`
leía `25-34` o `20 /21` como rango de EDAD cuando en Cacles son números de pie: plantillas vendidas
por rango y calzado de primeros pasos con talla doble. El catálogo llegaba a ofrecer un chip de talla
«48-51 años». Este ADR y la propia #64 daban por hecho que el dato que faltaba era `section` («en
`zapateria` es pie, en `ropa` es edad»), y **medir lo desmontó**: de las 201 variantes afectadas,
**123 están en `ropa`** — son calcetines barefoot de Be Lenka y Plus12, categoría `ropa-interior`,
tallados por número de pie. Son ropa y son números de pie a la vez, así que mirar la sección habría
dejado mal a la mayoría de las filas. Nadie había mirado de qué **categoría** eran.

Lo que sí discrimina es **el propio número**, con el umbral 15 que la 0014 ya tenía medido para el
número suelto, ahora exigido en los dos extremos del rango (0017): las edades acaban en `13-14` y los
pies empiezan en `20 /21`, seis puntos de hueco. Y era además la única salida practicable:
`size_canon(size, section)` haría **imposible** el índice por expresión, porque un índice solo puede
referirse a columnas de su propia tabla y `variant` no tiene `section` — el filtro volvería de 1,4 ms
a ~1 s. Al ampliar una de estas funciones, antes de meter una dimensión nueva en la firma, mirar si
el propio valor ya distingue: sale más barato y no ata el índice. Cerrado en #64.

Los límites de cada función están fijados por tests que rompen si alguien los amplía sin decidirlo
(rangos de edad solapados y el umbral pie/edad en la talla; los acentos en el color). Uno de esos
límites ya se ha cruzado a propósito, y cómo se cruzó es el patrón para el siguiente:
**agrupar familias de color** — declarado fuera por la 0015 con el argumento de que «agrupar por
familia es producto, no formato» — lo pidió el producto en #291, y se resolvió con una **tercera
función encima**, no ampliando la que ya existía. Ver más abajo.
Ese candado es lo que obliga a que un cambio de criterio se vea: la 0017 no pudo mover el umbral sin
reescribir el test que lo fija. Una función puede
además **negar** una etiqueta devolviendo `NULL`: `color_canon` lo hace con un nombre que son solo
dígitos (0016), porque un chip que es un número no lo puede elegir nadie. Cuidado al hacerlo — un
consumidor puede leer ese `NULL` como «cualquier valor»: en `interest.color` significa exactamente
eso, así que el alta rechaza con 400 en vez de guardarlo.

Se decide **midiendo, no intuyendo**, y hay tres escarmientos, los tres del mismo tipo: una frase
escrita en el contrato con seguridad, que se cayó en cuanto alguien consultó las filas.

- **#49** — la cautela declarada sobre el código de tienda se cayó al ver que 9 de sus 11 colisiones
  eran de Sfera contra sí misma.
- **#51** — un límite documentado como imposible («recuperar el nombre exige la PDP de Sfera, tras
  Akamai») resultó estar atribuido a **la tienda equivocada**: eran colores de Zara, cuya API es
  pública. Nadie había medido de qué tienda eran las filas.
- **#64** — la 0014 escribió como certeza que «un rango sin unidad solo puede ser edad; ninguna talla
  de calzado se escribe `11-12`», y este mismo ADR propuso arreglarlo con la sección. Las dos cosas
  eran falsas, y bastó agrupar por sección y categoría para verlo.

Antes de escribir en el contrato que algo no se puede o que algo es siempre así, comprobar sobre los
datos de quién —y de qué— se está hablando. La consulta cuesta un minuto.

**El tercer piso: `color_family` (0029), encima de `color_canon` y sin tocarla.** Cuando el producto
pide plegar más —#291: el panel ofrecía **2.859 chips de color**, el 85,2 % compuestos tipo
`amarillo claro/bluey`, inservible en un móvil— la salida NO es ampliar la función que ya existe.
Se apila una nueva, y hay tres razones que valen para el siguiente que quiera plegar algo:

- **El índice por expresión guarda los valores ya calculados.** Cambiar el cuerpo de `color_canon`
  deja obsoleto `ix_variant_color_canon` en silencio, y entonces el filtro devuelve *filas
  equivocadas, no un error*. La propia 0015 dejó escrita la obligación de reindexar; construir
  encima la esquiva entera.
- **Se hereda el plegado de acentos de la 0021 gratis**, así que las reglas de familia funcionan
  igual bajo el `UTF8 | C | C` del cluster sin volver a razonar sobre el ctype (#105).
- **Cada piso tiene su propio consumidor, y no son el mismo.** Desde la 0029, `color` significa
  **cosas distintas** en dos sitios que se parecen: el `?color=` del catálogo es **familia**, y
  `interest.color` sigue siendo el **canónico exacto**, porque el aviso existe para no mentir y un
  interés plegado a familia dispararía por cualquier azul. Es deliberado y está escrito en la
  cabecera de la migración; si algún día se añade un «seguir esta búsqueda» que capture el filtro
  activo, ese es el sitio donde se cuela un interés de familia sin que nadie lo note.

Dos datos medidos que decidieron el alcance, y que no son evidentes desde el código:

- **El buscador libre no repesca el color.** `fold()` cubre `p.name || category || gender` y el
  color no entra ahí, así que un color sin familia no es alcanzable por **ningún** camino. Eso es lo
  que convierte «¿qué hacemos con lo que no encaja?» en una pregunta de producto y no de formato: la
  respuesta fue vocabulario (de 1.093 productos sin color filtrable a 11 sobre 16.517) más una
  familia `estampado` para lo que no nombra ningún color.
- **Plegar por el segmento anterior a la `/`, nunca por la cadena entera**: 385 colores (13,5 %)
  caen en la familia equivocada mirando la cadena completa, porque detrás de la barra va el nombre
  del dibujo o de la licencia.

Efecto colateral que conviene saber antes de tocar índices: **`ix_variant_color_canon` (0015) se
quedó sin consumidor** con este cambio. Era parcial por `delisted_at IS NULL` y solo servía al
filtro del catálogo, que es el que se mudó a `color_family`. La función sigue muy viva (matching,
alta de intereses, agrupación de la ficha), pero ninguno de esos usos calza con el patrón del
índice: el matching evalúa `color_canon(b.color)` sobre la CTE `batch`, sin `delisted_at` por
ningún lado. No se ha borrado — es mantenimiento muerto, no un fallo.

**Y cuando una tienda escribe la talla al revés, se le da la vuelta EN LA TIENDA, no en la
función.** Zara e Hipercor sirven la edad delante y los centímetros en el paréntesis
(`5-6 años (116 cm)`); H&M lo invierte: `122/128 (6-8Y)`, `74 (6-9M)`. Y `size_canon` descarta el
paréntesis en su CTE `prep`, que es justo donde H&M pone la edad — saldrían `122-128` y `74`: sin
edad, y cayendo en el espacio de los números de pie, que es la colisión de #64. Se remodela en
`hm._talla()` antes de emitir, y la razón de que sea ahí y no en una migración nueva es que la
función es **compartida y con índice detrás**: tocarla obliga a reconstruirlo (la 0014 lo deja
avisado en mayúsculas) y a re-verificar lo que producen las otras seis tiendas, mientras que
remodelar en la tienda no toca a nadie más y ya tenía precedente (`sfera._normalize_size`,
`hipercor`). Comprobado contra la BD, que es lo único que lo demuestra: de nueve etiquetas reales
de Zara e Hipercor, **cinco casan exactamente** con las canónicas de H&M; sin remodelar habrían
sido **cero**. La regla general: la función SQL fija el **vocabulario**, y cada tienda es
responsable de hablarlo.

El límite conocido de ese umbral 15, y ya por el otro extremo: H&M vende calzado de bebé en
`12/13` y `14/15`, que salen como `12-13 años`. Son 12 variantes de 2712, pero **colisionan con
una talla de ropa real** (`12-13 años` existe y es numerosa), así que esta vez no es cosmético.
Por debajo de 15 las dos lecturas son plausibles desde el texto y quien desambigua es la sección,
que la función no conoce — y meterla en la firma es justo lo que #64 descartó por el índice.

**Se decide declararlo y no tocar la función, y esta vez con la medida hecha** (#103, 02/08/2026,
repetida el 03/08 con `springfield` dentro): la consulta sobre las **ocho** tiendas ingeridas
devuelve **solo esas 12 variantes de H&M** — Springfield no aporta ninguna, porque su calzado va de
18 a 43 y ningún rango cae por debajo del umbral. La
sospecha era que los patucos de Cacles, Hipercor o Lefties aportarían más, y es falsa por partida
doble: los de Hipercor son de **talla única** (entran con `size` a NULL) y los de Cacles y Lefties
se tallan con **número suelto**, así que el patrón `^N-N$` por debajo de 15 es más raro de lo que
parecía. Cambiar la firma a `size_canon(size, section)` costaría los dos consumidores y el índice
funcional para 12 filas. La consulta de arriba es la señal barata que hace que esta decisión no
caduque en silencio: el día que salga otra tienda, la opción vuelve a estar sobre la mesa.

**Una tienda puede abreviar la unidad a UNA LETRA, y entonces el guardián no separa unidades: acota
cada una.** Springfield escribe la edad de tres maneras en el mismo catálogo (`5-6`, `8` y `4A`) y
los meses como `12-18M`. Las dos formas con letra caían hasta la regla «irreconocible» y salían
crudas, porque las reglas de unidad buscan la palabra (`mes`, `a[nñ]o`) y las de número puro exigen
solo dígitos. El daño es el de siempre —el chip partido— pero con una diferencia de grado que
conviene ver: la de años parte el catálogo de Springfield **consigo mismo** (89 variantes), mientras
que la de meses lo parte **contra las dos tiendas más grandes**: `12-18M` (1 variante) contra las
**1407** de `12-18 meses` de H&M y Zara. Un interés dado de alta sobre el chip que ve quien navega
no casaba nunca con la prenda de Springfield (#135, migración 0024).

Lo transportable no es la regla sino **por qué lleva tope y qué tope**. La letra ya declara la
unidad, así que el tope no está para distinguir años de números de pie —un pie no se escribe con
`A` ni con `M`—, sino para acotar el rango en el que esa unidad es plausible, porque la letra pegada
a un número es un sitio concurrido: `A` es también la copa de un sujetador y `M` es **Medium**, que
Springfield publica en el mismo catálogo. Por eso los topes son distintos —15 para años, el mismo
umbral de #64, y 36 para meses, que son 3 años y el mayor mes real del catálogo— y por eso los
patrones exigen dígitos delante: la talla por letra no los tiene y no entra. Y lo que se sale del
tope **cae a la regla 7 y sale crudo**, que es el estado anterior — un chip feo, nunca una etiqueta
equivocada. Esa asimetría es el criterio con el que ampliar estas funciones: ante la duda, no
canonicalizar.

**Y el escarmiento, que es el cuarto de la misma familia y esta vez encadenado con lo de la ingesta.**
La issue #135 se escribió desde una Postgres local de un solo uso y declaró dos cosas: que eran 89
variantes y que el rango con sufijo (`4-6A`) «no se ha visto». La primera pasada real, el mismo día,
encontró `8-9A` **y** una familia entera que la issue no mencionaba (75 variantes en meses): el
alcance real era el doble. No es que #135 midiera mal — es que **midió sobre lo único que había**,
porque la tienda estaba registrada y sin ingerir. De ahí el corolario que enlaza las dos secciones:
una tienda sin pasada no solo deja su pipeline sin ejercer, deja **sin base a todas las issues que se
escriban sobre ella**. La comprobación barata para esta familia concreta es una sola consulta,
`WHERE v.size ~ '[0-9][A-Za-z]'`, que antes de Springfield devolvía **0 filas en las siete tiendas** y
es la que hay que repetir cuando entre una nueva.

**La base del cluster es `UTF8 | C | C`, y eso condiciona toda normalización escrita en SQL.** Con
ctype `C`, `lower('ÍNDIGO')` devuelve `'Índigo'` y `lower('11/12 AÑOS')` devuelve `'11/12 aÑos'`:
el color no se plegaba y la talla dejaba de casar con el patrón `a[nñ]o`, así que caía hasta la regla
«irreconocible» y salía cruda. **748 variantes** en `dev` con la canónica a medias y **dos chips
partidos** en la faceta, con el daño de siempre — filtrar por «marrón» enseñaba media lista y un
interés sobre un chip no casaba con las prendas del otro. Tres cosas que quedan de ahí (#105, 0021):

- **Se pliega con `translate()`, no con `COLLATE "es-ES-x-icu"`.** El collation ataría el esquema a
  que exista en el servidor, y el migrador del web corre como initContainer: si no está, no arranca
  el servicio. Mismo criterio que llevó a evitar `unaccent` en la búsqueda.
- **Un helper SQL llamado desde una función indexada NO funciona.** Lo natural es factorizar el
  plegado en un `lower_es(text)` y llamarlo desde las dos. Desde PostgreSQL 15 las operaciones de
  mantenimiento (`CREATE INDEX`, `REINDEX`, `VACUUM FULL`, `CLUSTER`) corren con `search_path`
  restringido a `pg_catalog, pg_temp`: el nombre de la función indexada se resuelve por OID y
  sobrevive, pero **un nombre escrito dentro de su cuerpo se resuelve al ejecutar** y ya no hay
  `public`. Medidas las tres variantes: sin cualificar **ni siquiera deja crear el índice**
  (`ERROR: function lower_es(text) does not exist ... during inlining`), `public.lower_es(...)`
  funciona y el `translate()` en línea funciona. Se duplica el literal antes que clavar el nombre
  del esquema en el contrato SQL, que sería el único sitio del repo que lo hace y se rompería en
  silencio y solo al reindexar. Vale para cualquier futuro intento de factorizar estas funciones.
- **El arnés de test tiene que correr con el ctype del cluster.** El defecto llevaba desde la 0014 y
  nadie lo vio porque CI levanta `postgres:16-alpine` con su locale por defecto, donde
  `lower('ÍNDIGO')` sí da `'índigo'`: los specs estaban en verde mientras el cluster hacía otra cosa.
  Ahora hay una segunda base (`TEST_DATABASE_URL_CTYPE_C`, creada con
  `TEMPLATE template0 ... LC_CTYPE 'C'`) y los specs de canónica y de búsqueda corren contra las dos.
  Sin ella **no fallan: se saltan**, que es el modo de fallo peligroso. Es la versión de laboratorio
  del principio que ya rige el vigía: probar donde la pregunta se responde de verdad.

El corolario general, que va más allá de estas dos funciones: **cualquier cosa que dependa del
locale da un veredicto distinto según dónde se ejecute la consulta**, así que medir contra una
Postgres local con otro locale —o contra CI— puede confirmar lo contrario de lo que hace producción.

**La búsqueda por texto pliega distinto a propósito, y tenía el mismo agujero.** `fold()`
(`catalog.service.ts`) pliega caja **y** acento, al revés que las canónicas, que conservan el acento
porque un chip es una etiqueta que se enseña. Su tabla de `translate()` solo llevaba minúsculas
acentuadas y el `lower()` de delante se daba por suficiente: con ctype `C`, `PANTALÓN` era invisible
al teclear «pantalon» — **694 productos vivos** en `dev` (zara 679, lefties 11, c-and-a 3, sfera 1).
De paso queda cerrada la duda que arrastraba esa decisión desde #38: `pg_trgm` y `unaccent` **están
disponibles y marcadas `trusted`** en dev y en QA, y el usuario de la app tiene `CREATE` sobre la
base, así que desde PG 13 puede instalarlas **sin ser superusuario**. La premisa que descartó las
extensiones era falsa; el índice GIN trigram está disponible el día que el catálogo lo pida.

### El `robots.txt` decide el diseño del scraper, y a veces no es el que parece

Medido el 02/08/2026 al implementar Hipercor (#79/#88), y corrige dos supuestos del recon de #70.

**El `robots.txt` que devuelve 403 a `curl` se lee con Chromium.** Hipercor, Sfera y H&M lo sirven
tras Akamai, y el recon los dio por "no comprobables" — quedó escrito como límite declarado en tres
issues. Basta una navegación con el navegador que esas tiendas ya obligan a usar. Hay que hacerlo
**antes** de elegir el endpoint, porque puede tumbar la elección entera.

**`Disallow: /api` es un prefijo desde la raíz, y por eso dos tiendas de la misma casa acaban con
scrapers distintos.** Hipercor y Sfera publican la misma regla:

| tienda | ruta de su firefly | ¿le aplica? |
|---|---|---|
| **Hipercor** | `/api/firefly/vuestore/…` | **sí** — la ruta empieza por `/api` |
| **Sfera** | `/es/api/sfera-es/firefly/…` | **no** — empieza por `/es` |

O sea: la tienda que ya está en producción está limpia, y no por suerte. Y la nueva no podía usar
la API que el recon había mapeado. Rodearlo no era una opción; el criterio ya estaba fijado cuando
la issue #81 mandó Springfield a "solo lo que su robots permite" en vez de forzar su rejilla.

**El camino permitido puede traer MÁS dato que la API.** En Hipercor las páginas de categoría y de
ficha no están vetadas y son SSR: la rejilla embebe un `dataLayer` con id estable, precio, **precio
tachado** y estado —o sea, huella para el detalle condicional—, y la ficha un `ld+json` con talla,
precio y **stock por talla**. Comprobado abortando `/api/**` en el propio navegador: con la ruta
vetada muerta, la página sigue trayendo todo. Antes de descartar una tienda por su robots, mirar
qué publica en lo que sí permite.

**Lo que cambia es el coste, y eso es contrato con el repo de manifiestos.** Leer la ficha por su
página cuesta **una navegación por producto** (3,55 s medidos) en vez de salir de la caché del
listado como en Sfera. Consecuencias: la huella del listado deja de ser un ahorro cómodo y pasa a
ser lo que hace viable la tienda; el `activeDeadlineSeconds` sube a 3 h (pasada en frío ~90 min con
ingesta atómica); y `SCRAPER_DETAIL_REFRESH_MAX` baja a 250 — justo lo contrario que en Sfera, donde
refrescar es gratis. Descartar imágenes, fuentes y CSS en el navegador baja el coste un 13 % con el
mismo dato, y de paso ahorra tráfico a la tienda.

**El veto se cumple en el código, no en la intención** (`BrowserSession.bloquear()`): una página
SSR puede pedir la ruta vetada al hidratarse aunque el scraper no la escriba nunca. Con una trampa
que costó una auditoría descubrir: **Playwright evalúa las rutas de la última registrada a la
primera y se para en la que resuelve la petición**, así que un handler `**/*` que llame a
`route.continue_()` —el que descarta imágenes— se come todas y deja el bloqueo **sin ejecutarse
jamás**, sin error ni aviso. Se arregla con `route.fallback()`, que sí cede al siguiente handler.
No lo veía ningún test: el doble de test no pasa por Playwright.

**Los términos y condiciones son la otra mitad del cumplimiento, y hasta Springfield (03/08/2026)
no se había leído los de ninguna tienda.** Los de Springfield (Tendam) no traen **ninguna** cláusula
sobre scraping, robots, crawlers, acceso automatizado ni minería de datos; lo que traen es el
boilerplate de copyright —«reproducciones privadas… siempre que no se instalen en un servidor
conectado a internet»— y una de uso lícito, más una §17 que va de virus y denegación de servicio.
Lo que importa de esa lectura no es Springfield: es que **esa cláusula es genérica y la tienen
todos**, así que aplicarla como criterio no descarta una tienda, las descarta las nueve. La política
que queda fijada: el `robots.txt` decide el diseño (es específico y accionable), los T&C se leen y
se citan en la cabecera del módulo, y si algún día se quiere endurecer el criterio de copyright,
eso es una decisión de producto transversal y no algo que se resuelva tienda a tienda. Precedente
escrito: `c_and_a.py` y `springfield.py` documentan los dos qué se leyó y qué se encontró.

### Un sitemap puede ser el listado, y eso cambia qué es una «hoja»

Springfield (#81, 03/08/2026) es la primera tienda que **no recorre hojas de categoría**. Su
`robots.txt` veta la rejilla de SFCC y su paginación, pero deja un `Allow:` explícito para el
sitemap — y ese sitemap resulta traer más de lo que suele:

- **La taxonomía va en la propia URL** (`/{mundo}/{género}/{categoría}[/{subcat}]/{slug}/{id}.html`),
  así que el ámbito de cada producto se resuelve **sin una sola petición**.
- **Trae `lastmod` en las 12 842 URLs**, y eso *parecía* ser la `signature`. Medido justo tras la
  pasada en frío: **25-31 min contra 1m39s**, ×17. Ese número no sobrevive a la cadencia real; ver
  abajo.
- **Todo el listado son 4 peticiones** (el índice y tres ficheros de producto).

**El riesgo que quedó anotado aquí está medido, y la respuesta invierte el motivo** (06/08/2026,
ver #227). Se temía que *si `lastmod` no se moviera al cambiar solo el precio, el detalle
condicional congelaría los precios*. No pasa — pero no porque el `lastmod` siga al precio, sino porque **se
mueve para todo**. Sobre las dos pasadas de QA separadas por dos días (run 23 el 03/08, run 38 el
05/08):

| ¿cambió el precio entre las dos pasadas? | productos | rango de `lastmod` visto en la run 38 |
|---|---:|---|
| no | 958 | 04/08 08:07 → 05/08 07:00 |
| **sí** | **132** | 04/08 08:07 → 05/08 07:00 |

Idéntico: el `lastmod` **no lleva información de precio en ninguna de las dos direcciones**. Se
reescribe por tandas —315 productos el 04/08 a las 08:00, 734 el 04/08 a las 19:00, 125 el 05/08 a
las 07:00— porque es una marca del **generador del sitemap**, no del producto. Consecuencia: la
segunda pasada pidió ficha de **1183 de 1193 productos y tardó 27 min**, o sea que el detalle
condicional no filtró nada y el ×17 de arriba solo existe en la ventana de minutos que sigue a una
pasada. No es el refresco forzado quien lo provoca: `SCRAPER_DETAIL_MAX_AGE_DAYS` son 7 días y entre
las dos pasadas hay 2.

**Lo que se generaliza, y es lo que importa para la próxima tienda por sitemap:** un `lastmod` es
una propiedad del generador, no del producto, y su utilidad como huella **no se puede medir en la
misma sesión en que se implementa la tienda** — dos pasadas separadas por minutos siempre dirán que
funciona. Hay que medirla con dos pasadas separadas por la cadencia real, y hasta entonces el ahorro
es una hipótesis, no un número. Nada de esto rompe datos: como el `lastmod` se mueve para todo, los
precios nunca se congelan y `price_history` crece con el catálogo entero. El coste es solo tiempo de
pasada.

**Ese coste está aceptado como decisión (09/08/2026, #227), y con la serie completa aparece un dato
que invierte lo que promete el mecanismo de dos fases: la pasada «en régimen estable» no es más
barata que la fría.**

| | duración | productos |
|---|---:|---:|
| QA 03/08 (en frío) | 25m 29s | 1112 |
| QA 05/08 | 26m 44s | 1193 |
| QA 08/08 | 34m 08s | 1191 |
| prod 09/08 | 33m 44s | 1191 |

O sea que el ahorro no es pequeño: **no existe en ninguna pasada**. Se acepta porque no hay huella
más barata posible —precio y tallas solo viven en la ficha y el `robots.txt` veta la rejilla de
SFCC, así que cualquier alternativa exigiría la petición que la huella existe para evitar—, porque
cabe con ×2,2 sobre el `activeDeadlineSeconds` de 4500 s (el catálogo tendría que llegar a ~2650
productos para rozarlo) y porque **un tercio de esos 34 min es cortesía deliberada**
(`request_delay` con jitter sobre ~1300 peticiones) que no hay que quitar. Se descartó bajar la
cadencia en el repo de manifiestos: no hace falta con ese margen. Consecuencia para quien lea un
informe de validación: **Springfield es el más caro de la banda con diferencia** —33m 44s contra los
3m 36s del segundo— y eso es lo esperado, no una regresión.

**Lo que se generaliza es qué es una hoja cuando no hay hojas.** El vigía necesita algo que sondear
y las bajas necesitan ámbitos declarados, y aquí resultaron ser **dos listas distintas, con el error
barato en lados opuestos**:

| | qué declara | si te pasas | si te quedas corto |
|---|---|---|---|
| `scopes()` | el producto cartesiano género × categoría | inocuo: un ámbito sin productos no hace nada | productos **imposibles de descatalogar** |
| `HOJAS` (`check_leaves`) | solo las ramas que la tienda publica de verdad | **un aviso falso cada semana** | una rama muere en silencio |

La primera versión usaba el cartesiano para las dos y el vigía cantó `19/24` con cinco hojas
«retiradas» que sencillamente no existen (`nino/vestidos`, `nina/polos`…). Es exactamente lo que
degradó el vigía de Sfera a ruido de fondo (#129): un aviso que sale todas las semanas deja de
leerse. Con el sitemap ya descargado, sondear las 19 ramas cuesta **cero peticiones extra**.

**Y una trampa de la ingesta que cualquier tienda puede pisar: `variant` tiene
`UNIQUE (product_id, retailer_variant_id)` y absorbe un duplicado sin rechistar, pero
`_record_price()` corre una vez por variante EMITIDA.** O sea que un scraper que emita dos veces la
misma variante no ensucia `variant` —donde miraría cualquiera— sino `price_history`, con dos
observaciones del mismo precio en la misma pasada. Medido en la primera pasada de Springfield: 8329
emitidas para 8219 filas, **110 precios duplicados**. El síntoma es un contador descuadrado en el
resumen, que se lee como ruido. La regla: si los dos números no cuadran, el que miente es el
scraper, y el daño está en la tabla de la serie de precios.

### Un 200 no prueba nada: hay que verificar QUÉ vino, no si vino

La sección siguiente cataloga cómo miente una hoja muerta. C&A (02/08/2026) enseñó que el problema
es más ancho: **una respuesta 200, bien formada y con la forma exacta que espera el parser puede ser
sencillamente otra cosa**. Tres modos de fallo distintos en una sola tienda, y ninguno se detecta
por status:

- **Sin las cabeceras de locale, la API sirve el catálogo de OTRO PAÍS.** `POST /api?o=list` con
  solo `content-type` y `origin` responde 200 con `prod_products_DE_de`: nombres en alemán, URLs
  `/de/de/…` y precios de Alemania. Hacen falta `x-country: ES` y `x-language: es` **juntas** (cada
  una por su lado devuelve HTML). Es el peor de los tres porque **no falla**: habría poblado el
  catálogo con producto y precios alemanes sin que saltara ninguna red. El recon lo tuvo delante y
  lo leyó como «las etiquetas del árbol vienen en alemán».
- **La paginación puede arrancar en 0.** El recon usaba `page: 1` dando por hecho que era la
  primera; es la segunda, y las páginas son **disjuntas** (60+60+52 = 172 = `productCount`).
  Empezar en 1 se saltaba un tercio de cada hoja, en silencio.
- **Un contrato caducado devuelve 200 con el error en el cuerpo.** Con persisted queries (APQ), un
  `sha256Hash` que ya no existe da 200 y `{"errors":[{"extensions":{"code":
  "PERSISTED_QUERY_NOT_FOUND"}}]}`. Un parser que vaya directo a `data.list.products` vería la
  lista vacía en **todas** las hojas a la vez el día del despliegue, y lo leería como un catálogo
  desaparecido. Hay que mirar `errors` **antes** que `data`.

Dos consecuencias de diseño que se generalizan a las tiendas que faltan:

**Un identificador de contrato que cambia con el despliegue se resuelve en ejecución, no se
pinnea.** Van tres de la misma familia: el `buildId` de Next, el uuid de rejilla de Lefties y este
`sha256Hash`. La forma barata es **pinnear y auto-repararse**: se usa el pinneado, y solo cuando
falla se relee del bundle desplegado —que la propia respuesta fallida nombra con su cabecera
`x-release-hash`—. Cero peticiones extra en régimen normal, una el día del despliegue. Resolverlo
siempre habría costado 2,3 MB por pasada para no enterarse de nada nuevo.

**Y esa detección tiene que vivir donde está el reintento, no donde está el parseo.** La primera
implementación miraba el `PersistedQueryNotFound` en `parse_*`, que ocurre **después** de que la
petición haya vuelto: el bucle que iba a reintentar con el hash nuevo nunca llegaba a verlo, así
que la auto-reparación existía sobre el papel y no se disparaba jamás. Lo destapó un test, no la
revisión.

**Y el reverso, que costó una pasada de tres horas y media: a veces no vino nada, y un bucle que
mira el status no se entera.** El de `BrowserSession.get_html` reintentaba ante 429/5xx —o sea, ante
el status de *una respuesta que llegó*—, así que un `Page.goto: Timeout` elevaba por encima del
bucle y subía hasta `ingest`. Con el agravante de que en una tienda que va por navegador ese es el
fallo transitorio **más probable de todos**: la segunda pasada de Hipercor (02/08/2026) murió a los
28 minutos por una hoja lenta, la 32 de 32, llevándose las 31 ya leídas. El mismo agujero que #41
tapó para los 404, por un camino que aquella no cubrió, y compartido con Sfera por venir de la
sesión de navegador. La regla que se generaliza: **el reintento tiene que cubrir el fallo que no
trae status**, y lo que quede se absorbe como hoja ilegible —«no la he podido ver», que no es «está
vacía»— porque quien decide si han sido demasiadas es `SCRAPER_SCAN_MAX_DEAD_RATIO`. Barrer ancho es
seguro precisamente porque ese umbral sigue puesto: se pierde una hoja, no el criterio.

Su contrapartida hay que tenerla presente al dimensionar deadlines: con los reintentos, una hoja que
la tienda no sirve pasa de costar 45 s a ~3 min, así que un bloqueo **total** tarda ~100 min en
llegar al umbral que aborta en vez de ~25. Sale a cuenta porque el caso frecuente es el timeout
suelto y el raro es el bloqueo entero, pero el número entra en el cálculo.

**Y el caso más callado de todos: el 200 trae el dato entero y el parser lo tira porque la tienda
ha renombrado el campo con el que se distingue un producto.** Lefties (05/08/2026) **intercambió**
dos campos de cada componente de su rejilla: donde venía `kind="Product"` / `type="Footwear"` pasó
a venir al revés. `_product_components()` filtraba por `kind == "Product"`, así que las **38 hojas
parsearon 0 entradas** descartando 2207 componentes que traían su `identifier.productParentId`
intacto — 755 productos y 9873 variantes que dejaron de ingerirse.

Lo que lo hace peor que un fallo ruidoso es que **las tres señales que deberían haberlo cazado
estaban verdes**: los tests con fixtures (el fixture guardado decía `Product`), `check_leaves()`
—38/38 vivas, porque el menú no había cambiado— y la propia ingesta, porque la red de bajas por
ámbito ve caer todos los ámbitos a cero y **omite** las bajas, que es lo correcto y además silencia
el síntoma. La pasada no rompía nada: solo dejaba de traer. Lo cazó el vigía, y por la capa de
parseo («el listado no devolvió ni una entrada»), no por el campo.

La regla que se generaliza: **no se reconoce un producto por una etiqueta de familia, sino por
llevar el identificador que el parser necesita**. Un allowlist de `kind` vuelve a romperse con la
siguiente familia que publique la tienda —y fiarse de `type` habría sido repetir la apuesta que
acababa de fallar, porque los dos campos existen y solo se cambiaron de sitio—. Es el mismo
principio que las bajas conservadoras: se afirma sobre lo que la respuesta *tiene*, no sobre cómo
lo llama.

**Un caso más del mismo principio, y el que ataca al sondeo de bajas: seguir redirecciones convierte
un 200 en una mentira.** Medido en Springfield (03/08/2026) sobre la ficha `6801308`: un id
inventado (`9999999`) da un 404 honesto, pero el id **vecino plausible** `6801309` responde **301 a
una ficha DISTINTA** que sirve un 200 impecable. Con `follow_redirects=True` —el default razonable
para listar y para leer fichas— eso llega a `probe_alive()` como «sigue a la venta», mirando otra
prenda. Es la misma familia que el espejismo de Sfera (#54), pero por HTTP en vez de por
enrutamiento de la tienda. La regla: **el cliente del sondeo de bajas no debe seguir
redirecciones**, y un 3xx se queda **fuera del mapa** en vez de contarse como vivo o como
retirado — la tienda no ha dicho que no exista, ha dicho que mires a otro sitio. Abstenerse solo
retrasa una baja real; un `False` de más borra del catálogo algo que se sigue vendiendo.

**Y el reverso del reverso: la tienda perdona un identificador equivocado, y entonces el 200 tampoco
prueba que el identificador sea el bueno.** Medido en Lefties (14/08/2026, #393). Su menú tiene
**nodos alias** —las `_VIEWALL` apuntan a su padre y las `_MENU` a la hoja de otra rama— cuyo
`content.id` no es el uuid de una rejilla sino el **id numérico de otra categoría**.
`grid_ids_by_category()` lo devolvía tal cual, así que la pasada pedía `…/grids/1030680609` donde la
API espera `…/grids/d5e0b942-…`, y la tienda lo resolvía igual: mismos 21 y 25 modelos que por el
uuid. Ocho alias de 116 hojas en el menú de niña y seis de 108 en el de bebé, y **las cuatro hojas
`barefoot` que mapeamos son de esas**.

Lo que lo hace peligroso no es la petición, que funciona, sino **cómo se vería el día que dejara de
funcionar**: cuatro hojas dando 404 a la vez, `_hoja_comprometida()` leyéndolas como retiradas y
`SCRAPER_SCAN_MAX_DEAD_RATIO` decidiendo sobre un dato falso. El diagnóstico manda a buscar una baja
que no existe, que es el coste caro. La regla que se generaliza, y que cierra la familia de esta
sección por el otro extremo: **un identificador que se deriva del propio contenido de la tienda hay
que resolverlo hasta su forma canónica, y no dar por buena la que funciona** — verificarlo cuesta un
test de fixture, porque el menú ya trae las dos puntas del salto.

### El árbol de categorías de una tienda no es lo que parece

Dos cosas medidas sobre Zara y Sfera que se repiten y conviene dar por supuestas al mapear la
siguiente tienda:

**Los rangos de edad son ramas distintas, y el barefoot vive en la de bebé.** Las dos tiendas
parten el catálogo infantil en 6-14 y mini/bebé, con rutas separadas, y el calzado respetuoso está
sobre todo en la segunda: en Zara **78 de 86** referencias barefoot no se ingerían (#35), en Sfera
**5 de 6** (#33). Mapear solo la rama mayor parece cubrir la tienda y deja fuera el grueso de lo que
este producto existe para encontrar. Y el árbol **no es simétrico** entre rangos: la mayoría de
categorías de ropa de Sfera no existen en bebé.

**Y pueden ser más de dos: en Zara son TRES** (#186, 05/08/2026). Además de 6-14 y mini 1½-6 hay un
departamento de bebé 0-18 meses (`2428025`, `kids-baby`) con **143 nodos y ninguno mapeado**, que no
estaba descartado sino sin mencionar. La lección no es el número —es que *«dos rangos»* era una
generalización de dos tiendas, y la única forma de saber cuántos tiene la siguiente es enumerar su
árbol. Al mapearlo, sus once hojas del brief listan **1157 productos de los que 545 eran nuevos**:
el catálogo de Zara pasó de 3382 a 3927 (+16 %).

**Los rangos solapan, y eso convierte el orden de `CATEGORIES` en una decisión de datos.** Los otros
612 de esos 1157 ya entraban por una hoja con género, porque el bebé de Zara no separa niño de niña
y su rango pisa el de mini. Como `list_catalog()` deduplica por id y **gana la primera hoja que lo
ve**, poner bebé delante habría degradado 612 productos de `niña`/`niño` a `unisex` —sacándolos del
filtro que el brief pide— y además los habría contado como mudanza de ámbito (#174) en la primera
pasada. Van al final por eso, con un test que lo fija.

Nótese que esto es el **mismo resultado que `ambito_cruzado()` consigue en Hipercor por otro
camino**: allí lo declarado `unisex` se descarta antes de mirar el cruce; aquí no hay cruce que
mirar —Zara no pasa `tambien_unisex`— y la única protección es el orden. Quien añada una rama que
solape con otra ya mapeada tiene que elegir explícitamente cuál de las dos gana, y la respuesta por
defecto es **la más específica**: barefoot por delante del calzado genérico, y el género por delante
de `unisex`.

**Una hoja muerta casi nunca da 404, y cada tienda miente de una forma distinta.** Ya van **cuatro**
formas distintas (recon de #70, 02/08/2026), y la consecuencia siempre es la misma:

- **Sfera responde 200 con el catálogo del padre** a una ruta que no existe (`ninos/nina/loquesea` →
  las 30 páginas de `ninos/nina`). El sondeo de `--check-categories` informa «12 productos, viva», y
  una pasada ingeriría cientos de productos del género entero —ropa incluida— etiquetados con el
  ámbito de la hoja muerta. Se detecta comparando **los ids de la 1ª página contra los del padre**,
  nunca `data.title` (texto localizado de presentación).
- **Cacles/Shopify responde 200 con la lista VACÍA**, que es peor: no mete basura, pero una hoja
  muerta pasa por «este ámbito se ha quedado sin productos», que es exactamente el disparador de una
  baja masiva. Y la misma respuesta es el fin normal de la paginación, así que hay que desambiguarla
  por posición: vacía en la **primera** página es hoja retirada, a partir de la segunda es el final.
- **H&M devuelve una página LLENA Y PLAUSIBLE**, y es la peor de las cuatro. El selector real es
  `pageId`, y uno que no resuelve no da 404, ni vacío, ni el padre. **No se detecta por status ni
  por vacío**: una hoja renombrada sigue «funcionando» e ingiere productos de otra categoría en
  silencio, sin que caiga nada que las redes de seguridad puedan contar. Implementada la tienda
  (#77, 02/08/2026), la mentira resultó tener forma y por tanto detector: **cae al cubo de
  `categoryId`**, que es un parámetro que el recon había dado por decorativo. Se ve cambiándolo
  solo a él — `kids_all` → 9713 productos, `kids_shoes` → 244, `kids_clothing` → 4113. De ahí el
  **canario**: una ruta deliberadamente inventada, pedida **una vez por pasada**, contra la que se
  compara la primera página de cada hoja. Es `is_mirage` de Sfera cambiando el padre por el
  canario, y sale más barato porque no hay que saber cuál es el padre.
  **Se comparan los ids, no el contador**: `numberOfHits` del cubo deriva entre peticiones
  consecutivas (9713 → 9710 en segundos), así que una igualdad exacta declara **viva una hoja
  muerta**, que es el error caro. Solape medido: **100 %** en las muertas, **0-8 %** en las vivas.
  La defensa que el recon proponía —que la hoja declare qué espera y se contraste con el
  `mainCatCode` de los productos— **se probó y se descartó**: es demasiado ruidosa. Una hoja real
  de `/kids/boys/shoes` trae `kids_boys_outerwear_rainwear` y `..._nightwear_slippers` entre sus
  productos, y la coherencia por prefijo da 21-34 de 36 en las vivas contra 6 de 36 en el cubo —
  rangos que se solapan demasiado para un umbral honesto.
  **Y el canario hace falta también para MEDIR, no solo para ingerir** (#189, 05/08/2026). Un nodo
  que el menú publica no es siempre una página seleccionable, y **no se deduce del sitio que ocupa
  en el árbol**: `/kids/girls/school` y `/kids/boys/school` son espejismo, igual que
  `/kids/girls/clothing` y la propia `/kids/girls` —son contenedores y solo resuelven sus hijas—,
  mientras que `/kids/girls/sportswear` (109 modelos) y `/kids/girls/outerwear` (114) sí resuelven.
  La consecuencia es de método y muerde en la dirección cara: quien mida «cuánto hay en esta rama»
  pidiéndosela al padre sin compararla con el canario se lleva el cubo entero —~9700 productos— y
  concluye lo contrario de lo que dicen los datos. La medición de una rama candidata se hace hoja a
  hoja, y la del padre solo vale si el canario la avala.
- **Hipercor repite el espejismo de Sfera** —es el mismo firefly— pero trae el antídoto: su
  `data.paginatedDatalayer.page.hierarchy` refleja la ruta **realmente resuelta**, así que basta
  compararla con la pedida, sin cotejar ids de la primera página. Seis rutas inventadas a mano
  devolvieron las seis el catálogo del padre, en silencio.

Y dos que **sí lo resuelven bien**, que conviene tener como referencia de lo que se le puede pedir a
una tienda:

- **C&A** distingue las dos situaciones con un solo campo — hoja inexistente da 200 con
  `productCount: 0`, y hoja viva pasada de página da 200 con el `productCount` intacto. Es la trampa
  de Cacles con el desambiguador incluido, sin heurística posicional.
- **Mango es el mejor caso de las nueve** (#80, 03/08/2026): **404 honesto en los tres sitios** —ruta
  web, API de listado (`catalogs/inventada/filters`) y ficha (`/p/_99999999`)—, así que
  `check_leaves()` y `probe_alive()` se fían del status y no necesitan canario, ni comparar con el
  padre, ni desambiguar por posición. Y encima **no pagina**: `/filters` devuelve la hoja entera
  (1938 items medidos en una respuesta), con lo que desaparece la cuarta trampa del recon, la de
  contar como sana una hoja truncada.

  Con el 404 honesto disponible, deducir la muerte del **vacío** deja de ser prudencia y pasa a ser
  un falso positivo: 55 de las 111 hojas de Mango son de rebajas y se vacían legítimamente al acabar
  una campaña. Tratarlas como caídas dispararía `SCRAPER_SCAN_MAX_DEAD_RATIO` (0,34) contra una
  tienda sana y haría avisar al vigía cada semana. La regla que queda: **una hoja vacía con forma de
  listado está viva**; lo que compromete el ámbito es que la respuesta deje de tener esa forma.

  **Y aun así, un 404 honesto no es un 404 fiable: la primera pasada en el cluster lo midió.** En
  `run #37` (03/08/2026, `dev`) `rebajas_newborn.sudaderas_newborn` dio 404 y se marcó como caída
  sin estarlo — seguía publicada en el menú, el listado le respondía **200 con 2 items**, **20
  sondeos seguidos dieron 20 × 200** y la pasada siguiente, tres minutos después, la vio viva
  (111/111, `errors: 0`). O sea que «honesto» describe lo que el 404 *significa* cuando la tienda lo
  emite a conciencia, no la fiabilidad de **una** observación: sigue habiendo un transitorio por
  debajo, y la tienda mejor portada de las nueve no está exenta.

  Lo que salva el caso es que la consecuencia ya estaba dimensionada por el lado bueno: el ámbito se
  marca comprometido, sus bajas se omiten y la pasada cierra sin descatalogar nada de más. **El coste
  de un 404 transitorio es dejar de detectar bajas durante esa pasada, no dar de baja de más**, y por
  eso no hace falta reintentar para que el sistema sea correcto. Generaliza a toda tienda cuya salud
  de hoja descanse en un status: la asimetría es lo que permite fiarse de una señal ruidosa, y quien
  decide si han sido demasiadas sigue siendo `SCRAPER_SCAN_MAX_DEAD_RATIO`. Antes de añadir un
  reintento hay que medir cuánto se repite — con dos observaciones no se distingue el ruido del
  antibot de una hoja concreta que renquea, que es la misma disciplina de la §«un aviso que sale en
  la misma hoja todas las semanas ya no es un blip».

En todos los casos las redes de seguridad se apoyan en `GONE_STATUS` y quedan ciegas, sin que
`ScanReport` cuente ninguna caída. **Al añadir una tienda, probar una ruta inventada antes de fiarse
del 404** — es la primera comprobación del recon, no la última. Y probar **varias**: en Hipercor, las
seis rutas plausibles que se inventaron eran todas falsas y todas parecían vivas.

**La tienda publica su árbol: preguntárselo sale más barato que adivinarlo.** Corolario de lo
anterior — si una ruta inventada no se distingue de una real por la respuesta, la lista de rutas no
puede salir de nuestra cabeza. Sfera lo publica en `data.filters._menubar`, faceta `type:
"categories"`, con `slugs`/`count`/`has_children`; solo aparece con `showDimensions` **distinto de
`none`**, y `none` es justo lo que usa la ingesta (payload más ligero), así que son dos URLs sobre
el mismo endpoint. Esa faceta se descartó en #33 por no servir para clasificar barefoot, que era
cierto y llevó a no volver a mirarla en tres issues.

Lo que costó no preguntar, medido en #56: el rango bebé tiene **20 hojas reales** y buscarlas
copiando los nombres de la rama 6-14 encontraba **4**. La asimetría entre rangos no es solo de qué
categorías existen, es de **cómo se llaman**, y justo en las gordas: `pantalones-y-leggings` (30
productos) donde 6-14 tiene `pantalones` y `leggings` separadas, `blusas-y-camisetas` donde tiene
`camisetas` y `camisas-y-blusas`. Sfera pasó de **300 a 491 productos** al mapear las 12 que caen en
el brief, con cero colisiones de id. El mismo agujero estaba abierto en la rama 6-14 y se cerró
igual (#72, 02/08/2026): cuatro hojas de `pantalones` sin mapear —`leggings`, `shorts-y-bermudas`
en los dos géneros y `vaqueros` **solo en niña**, que mapeada en niño y no en niña le daba al
catálogo un sesgo de género que no respondía a nada de la tienda—, +55 productos y cero colisiones
entre las siete hojas de pantalones.

La capacidad es opcional y hermana de `SupportsLeafHealth`, porque son dos preguntas distintas:
`--check-categories` contesta «¿sigue vivo lo que ingiero?» y falla por lo accionable;
`SupportsCategoryTree` / `--tree` contesta «¿qué existe que no esté ingiriendo?» y solo informa.

**Una tienda sin endpoint de árbol puede publicarlo igual, embebido en su propia página.** H&M no
tiene faceta de categorías (los endpoints de navegación plausibles dan 404/503 y las 30 facetas de
la respuesta vienen con `values: []`). Pero **la página de categoría del escaparate trae el menú
entero incrustado**, y se lee una vez con Chromium (el escaparate es Akamai; la API no). Desde #179
eso ya no es reconocimiento a mano: es su `category_tree()`, y es lo único de esa tienda que
necesita navegador. Y el resultado justifica el rodeo: se probaron **18 rutas plausibles de bebé y
las 18 eran espejismo**, porque el árbol real dice dos cosas que nadie adivina:

- **Bebé no cuelga de infantil: es un departamento hermano** (`/baby/…`, no `/kids/baby/…`).
- **El rango 9-14 años es una rama aparte** (`/kids/boys-9-14y/…` junto a `/kids/boys/…`). Quedarse
  con la primera habría dejado fuera media tienda por edad — el mismo agujero que #56 y #72 en
  Sfera, ahora con la variante de que las dos ramas se llaman **casi** igual.

Y un matiz que costó 39 rutas fantasma: **leer «las rutas que aparecen en la página» no es leer el
menú**. La receta que #77 dejó anotada era un `re.findall` de rutas sobre el HTML entero y sacaba
690 donde el menú publica **651** (medido el 05/08/2026), porque también recoge el
`<meta name="contentPath">` de la propia página y los bloques `praData`, que nombran las mismas
categorías con **otro vocabulario** (`1/kids/kids_girls/kids_girls_clothing`). Se leen los pares
`title`+`path` de las entradas del menú, y de `path` —la URL navegable— y no de `targetPath`, que
es a dónde apunta la entrada y no lo que la entrada es: `jackets-coats` apunta a
`outerwear/view-all`, así que por ahí una hoja se leería con el nombre de otra.

**Y una lección de coste sobre Akamai que corrige lo que #160 sugiere en frío:** el documento
servido de H&M trae el mismo menú que el navegado, pero `pedir_html()` **en frío da 403** — hace
falta una navegación previa que siembre las cookies. Con la sesión ya sembrada son 0,96 s frente a
2,10 s, así que para **una** página salen dos peticiones donde `get_html()` hace una. El atajo de
Hipercor vale cuando se piden muchas páginas tras una siembra, no cuando se pide una.

Generaliza: al mapear una tienda, el rango de edad y el «departamento» pueden ser **hermanos** de
la rama que ya tienes, no hijos, y por eso no aparecen bajando desde ella.

**Mango cierra el argumento**, porque es el caso donde preguntar sale más barato que en ningún otro
(#80, 03/08/2026): tiene un **endpoint de menú público** (`api.shop.mango.com/ecs/menu-service/v5`)
y cada nodo trae su `catalogId`, que es **exactamente** el identificador que come la API de listado.
O sea que el árbol no hay que traducirlo a rutas nuestras: **el árbol ya ES la lista de hojas**, y
`CATEGORIES` se rellena copiando de ahí. Una petición para las 256 hojas que publica, de las que 111
caen en el brief.

Y repite la lección de las ramas hermanas por tercera vez, así que conviene darla por norma y no por
sorpresa: `ninos` tiene **cinco** ramas de género —`nina`, `nino`, `bebe-nina`, `bebe-nino` y
`newborn`—, y `teen` es hermano de `ninos`, no hijo. Quedarse en las dos evidentes habría dejado
fuera todo el bebé, igual que `/baby` en H&M y el rango mini en Sfera.

**El árbol dice la verdad sobre lo que hay, pero ni es exhaustivo ni sabe cuánto.** Dos cosas
medidas en Sfera el 02/08/2026 que matizan el «única fuente fiable» de arriba, y que se descubren
solo al cruzar la faceta con el listado:

- **El `count` de la faceta NO es lo que sirve la hoja.** Declara **8** en `ninos/nina/leggings` y
  el listado da **18**; declara **4** en `ninos/nino/shorts-y-bermudas` y da **15**. Sirve para
  orientarse sobre qué hojas pesan; **no** para dimensionar una pasada, elegir el
  `activeDeadlineSeconds` de su CronJob ni decidir si una hoja merece la pena.
- **Una hoja puede no salir en el árbol y estar viva.** `ninos/nino/vaqueros` no aparece en la
  faceta y sin embargo sirve **7 productos**, y el sondeo la da viva. Así que **la ausencia no
  prueba la baja** — para eso está `check_leaves()`, que la pide. Lo fiable es la otra dirección:
  lo que sale, existe.

Las dos tienen el mismo corolario práctico: `--tree` sirve para **descubrir**, no para **medir**.
En cuanto la cifra importe —cuánto ingiere una hoja, si una desapareció— hay que pedir el listado.

Dos trampas del endpoint que no se ven sin pedirlo en vivo, y que valen como aviso para la próxima
faceta de este tipo: para una categoría **sin descendencia** la faceta responde con el rastro de
**ancestros** en vez de hijos (`ninos/mini` devuelve «Sfera España» y «Niños»), y el nodo raíz de la
tienda viene **sin `slugs` ni `link`**. Se resuelve filtrando a descendientes estrictos de la raíz
pedida: una hoja devuelve lista vacía, que es la respuesta honesta a «qué cuelga de aquí».

Dos corolarios que costaron dinero descubrir:

- **El final de la paginación se decide con los productos CRUDOS, no con los parseados.** El parseo
  descarta tipos no seguibles (tarjeta regalo, medidor de pie), así que una página entera de
  excluidos parsea a cero: en la primera daría una hoja muerta falsa y en las siguientes cortaría la
  paginación dejándose catálogo sin ver.
- **Quedarse corto en la paginación tampoco puede contar como hoja sana.** Si se agota el tope de
  páginas sin llegar al final, se ha visto solo una parte del catálogo; contarla como sana deja sus
  ámbitos elegibles para bajas y, a las `SCRAPER_DELIST_MIN_MISSES` pasadas, descataloga producto
  vivo por no haber cabido en el tope. Se trata igual que una hoja retirada.

### Shopify cobra por complejidad, no por peticiones

> **Leer antes la sección *El 429 de Cacles va por conexión*.** Las cifras de aquí están medidas de
> verdad, pero la causa que se les atribuyó no explica el patrón real, y varias de las
> consecuencias de abajo salieron de ese malentendido.

Medido contra Cacles el 31/07-01/08/2026. Una página con `limit=250` puntúa
`shopify-complexity-score: 12400` (16810 el 03/08, con el catálogo ya crecido) y el cubo tarda
**minutos** en rellenarse. Consecuencias prácticas:

- **Bajar el tamaño de página no ayuda**: el coste es por producto devuelto, así que el mismo
  catálogo cuesta lo mismo repartido en más viajes.
- **La petición que sobra es la cara.** Shopify no da total, pero devuelve menos de `limit` al llegar
  al final: usar esa señal evita la petición extra que solo servía para recibir una lista vacía. Con
  el catálogo actual son 2 peticiones en vez de 3, y ese tercio es lo que costó una pasada entera —
  las páginas 1 y 2 se leyeron bien y el 429 llegó justo en la 3ª.
- Los defaults del scraper (`request_retries=3`, `retry_backoff=1.0`) suman ~7 s de espera, dos
  órdenes de magnitud por debajo de lo que tarda el cubo. El CronJob de `cacles` sube
  `SCRAPER_RETRY_BACKOFF`/`RETRIES`/`DELAY` por eso; con los defaults, una pasada que coincida con
  otro consumo aborta.
- **El recon agota el presupuesto para el resto del día.** Capturar fixtures a ráfagas dejó la IP
  bloqueada horas, con el castigo alargándose en cada reintento. Una pasada normal (2 peticiones
  diarias) no se acerca al límite; el problema es el desarrollo, no la producción. Capturar la
  fixture **una vez** y trabajar contra ella.

### Hay dos 429 distintos y solo uno se arregla esperando

Corrige lo que esta misma sección afirmaba: los 429 de Cacles **sí traen `Retry-After`** (60 s,
medido el 01/08/2026), y sobre todo, **no todos vienen de Shopify**. Delante hay un Cloudflare que
ficha la **huella TLS del cliente**, y ese 429 es de otra especie aunque comparta código de estado:

> **La tabla de abajo es falsa en su primera fila, y el 03/08/2026 costó una pasada de QA.** Se
> conserva porque explica de dónde salió el error; lo que hay que leer es la sección siguiente,
> *El 429 de Cacles va por conexión*. En resumen: los dos 429 son la **misma respuesta byte a
> byte** —cuerpo `local_rate_limited` de 18 bytes, `Retry-After: 60`, `server: cloudflare`—, así
> que el cuerpo **no** distingue la causa. Lo demás de esta sección (el ALPN, y que hay dos causas
> distintas) sigue siendo cierto y sigue haciendo falta.

| | 429 de presupuesto (Shopify) | 429 de huella (Cloudflare) |
|---|---|---|
| cuerpo | ~~JSON / vacío~~ **también `local_rate_limited`** | `local_rate_limited` |
| depende de | ~~cuántos productos has pedido~~ **de la conexión** | qué cliente eres |
| se arregla | esperando minutos | cambiando el ClientHello |

Lo medido: httpx recibía 429 en **todas** sus peticiones —también desde un pod del cluster—
mientras `curl`, `wget` y `urllib` pasaban con 200 desde la misma IP, con las mismas cabeceras byte
a byte y contra el mismo edge, en el mismo proceso y con segundos de diferencia. La única diferencia
era la extensión **ALPN**: JA4 `t13d17`**`13`**`h1` (httpx, 13 extensiones) contra
`t13d17`**`12`** (urllib, 12). Quitarla devuelve 200; control emparejado, alternando cuatro veces.

Tres cosas que llevarse:

- **No se quita por configuración**: `httpcore` llama a `set_alpn_protocols()` sobre el contexto que
  le pases, sea cual sea. La única vía es una subclase de `SSLContext` que ignore esa llamada —
  `scraper/tls.py`, hoy usado solo por `cacles`. Sin ALPN no hay HTTP/2, que es lo que el scraper ya
  hablaba de todas formas.
- **Es reutilizable y no es de esta tienda**: la huella de httpx es de las más conocidas, así que
  cualquier tienda tras Cloudflare puede ficharla. Al añadir una tienda, si el 429 llega desde la
  primera petición y sin ráfaga previa, sospechar de la huella antes que del ritmo.
- **Antes de culpar al ritmo, comparar clientes.** Un `curl` con las cabeceras exactas del scraper
  cuesta segundos y separa las dos hipótesis; sin esa comparación, el diagnóstico natural («me he
  pasado pidiendo») es plausible, encaja con los hechos y es falso.

El techo `httpx<0.29` en `pyproject.toml` está por esto: el arreglo se apoya en un detalle
**interno** de httpcore (que llame a `set_alpn_protocols()` sobre el contexto que recibe), así que
el bump tiene que ser un PR donde toque re-verificarlo. Re-verificado el 03/08/2026 con httpcore
1.0.9: sigue llamándolo, y el control emparejado sigue dando 200 sin ALPN y 429 con él.

### El 429 de Cacles va por conexión, y eso invalidó dos diagnósticos seguidos

Medido el 03/08/2026 (#120), después de que la primera ejecución programada de Cacles en QA muriera
en su segunda petición. **Una conexión sirve una petición pesada; la siguiente por esa misma
conexión se lleva un 429.** Control emparejado y alternado, dos rondas idénticas, páginas 1-2-3 de
`infantil`:

| cómo se piden | resultado |
|---|---|
| un `httpx.Client` compartido | `[200, 429, 429]` |
| compartido + cabecera `Connection: close` | `[200, 429, 429]` |
| compartido + `max_keepalive_connections=0` | `[200, 429, 429]` |
| **un `Client` nuevo por página** | **`[200, 200, 200]`** |

Ni pedir el cierre ni desactivar el keep-alive bastan: hay que abrir un cliente nuevo. Por eso
`cacles._pagina()` abre el suyo y `list_catalog()` **no** envuelve el bucle en un solo cliente —
volver a hacerlo parece una limpieza obvia y deja la tienda sin ingerir en la segunda página, así
que hay un test que lo fija.

**El mecanismo no está medido** (si Cloudflare cuenta peticiones por conexión, o si el presupuesto
de Shopify se atribuye a ella). Se deja escrito así a propósito: esta misma sección ha llevado dos
explicaciones plausibles y sin medir, y las dos costaron una pasada.

Lo que esto corrige de las dos secciones anteriores:

- **El cuerpo no distingue las dos causas.** `es_429_de_huella()` se llamaba así porque se creía que
  sí; renombrada a `tiene_marca_de_cloudflare()`, que es lo único que la marca prueba. El
  discriminante bueno es el **historial**: el rechazo por huella se decide en el handshake, así que
  un 200 previo lo descarta. `HuellaTLSRechazada` solo se eleva al agotar los reintentos sin un solo
  200, y el 429 con marca se reintenta siempre.
- **Elevar a la primera era peor que esperar.** Ahorraba ~10,5 min cuando la causa era la huella,
  pero costaba la pasada entera cuando no lo era — y siendo la ingesta atómica, no quedaba nada.
- **Bajar `_PAGE_SIZE` sigue sin ayudar, ahora por el motivo correcto**: con `limit=100` y `limit=50`
  el 429 llega igual en la 2ª petición de la misma conexión. No es volumen.
- **«El recon agota el presupuesto para el resto del día» era el mismo malentendido.** Las ráfagas
  de captura de fixtures compartían cliente; lo que se veía como castigo acumulado era el límite por
  conexión.

Lo que sí generaliza a otras tiendas: **al depurar un 429, el cliente es una variable del
experimento**. Si las sondas abren cliente por petición y el scraper lo comparte, se está midiendo
otra cosa que la que falla — y las dos dan resultados coherentes y contradictorios.

### Que la web esté tras Akamai no significa que su API lo esté

Medido en el recon de #70 (02/08/2026), y corrige un supuesto que la épica #4 arrastraba desde
julio: la nota de «la 2ª tienda tras Akamai (Mango, H&M o Lefties) reusando `BrowserSession`» daba
por hecho que un 403 en la portada obliga a Chromium. De las cinco tiendas pendientes del brief,
**solo una lo obliga**:

| tienda | front | API de listado | ¿navegador? |
|---|---|---|---|
| **H&M** | `www2.hm.com` 403 (Akamai, hasta para `robots.txt`) | **`api.hm.com`, otro host, 200 a httpx SIN cabeceras** | no |
| **Mango** | `shop.mango.com` pone `_abck`/`bm_sz` | mismo host, **200 con solo UA de Chrome** | no |
| **C&A** | Cloudflare, 200 | mismo host, 200 con `content-type` + **`origin`** | no |
| **Hipercor** | 403 (Akamai, cookie `_bman`) | mismo host, **403 a curl, wget, httpx y httpx sin ALPN** | **sí** |

Tres cosas que llevarse:

- **El anti-bot se despliega por host y por ruta, no por marca.** Antes de asumir Chromium, buscar
  el host de la API en `browser_network_requests` y **reintentarlo desde fuera del navegador**. Es
  una petición y decide entre un CronJob de 1Gi y uno de 2Gi.
- **Distinguir «Akamai está presente» de «Akamai exige el sensor JS».** Que la respuesta traiga
  `_abck`/`bm_sz` no implica que haga falta ejecutarlo: en Mango basta un User-Agent de navegador.
  El 403 sin UA y el 200 con UA, contra la misma URL y en el mismo minuto, separan los dos casos.
- **Esto es contrato con el repo de manifiestos**, no una curiosidad: el perfil de recursos del
  CronJob de cada tienda sale de aquí (Zara httpx 1Gi vs Sfera Chromium 2Gi). Elegir mal el perfil
  es pagar 2Gi por tienda para siempre, o que el pod muera sin memoria.

Corolario del mismo recon: **una API abierta puede pedir una cabecera tonta**. C&A responde
`403 Not allowed` a todo lo que no lleve `origin`, y con `content-type` + `origin` entra sin cookies,
sin UA y sin las `x-*` que manda su propio front. Antes de concluir «nos bloquean», probar la matriz
de cabeceras además de la matriz de clientes de la sección anterior.

Y cuando el navegador sí hace falta, la restricción que se paga en **cada** camino de código:
**la primera petición de una sesión de Akamai se gasta en el saludo**. Contesta 403 y entrega las
cookies con esa misma respuesta, así que quien abra una sesión y pregunte directamente a una API
pierde siempre su primera pregunta — sea cual sea. Medido en Sfera el 03/08/2026 invirtiendo
`CATEGORIES`: el 403 se muda de hoja con la posición, nunca se queda en una ruta concreta (#129).

Lo que eso obliga es una regla por **camino**, no por tienda: todo `with session_factory()` navega
a una página de documento antes de tocar una API. Basta una siembra por sesión (35 hojas de Sfera
con una sola). Y el modo de fallo es asimétrico, que es lo que lo hace caro: en la pasada de
ingesta el síntoma es una hoja que no aporta productos —visible—, pero en el **sondeo** es una hoja
sin veredicto, que la política de arriba degrada a aviso y nadie mira. Por eso el olvido vivió meses
en `sfera.check_leaves()` mientras `_iter_category()`, `category_tree()` y `probe_alive()` sí
sembraban, y con los tests en verde: el doble de test respondía a cualquier petición, o sea que era
**más permisivo que la tienda**. Un doble de una tienda con antibot que no simula el antibot no
prueba el camino que importa.

### Un scraper se rompe de dos maneras y solo una se ve (y la segunda tiene grados)

Que la tienda **cambie** (una hoja caduca, el JSON cambia de forma) sale en el resumen de la
pasada. Que la tienda deje de **dejarnos entrar** es silencioso, y es el modo de fallo caro: el
arreglo de la huella TLS se apoya en un detalle interno de httpcore, así que un bump de
dependencias puede devolvernos al 429 sin que nadie se entere hasta que alguien mire los logs
semanas después. La señal existía —`--check-categories`, los tests `*_LIVE=1`— pero solo corría a
mano, y nadie la lanza tres semanas después, que es justo cuando hace falta.

De ahí el **vigía** (`scraper/vigia.py`, `python -m scraper.vigia`, CronJob semanal). Tres
decisiones que no son obvias:

- **Corre en el cluster, no en GitHub Actions.** Es lo contrario de lo que pide el instinto (un
  workflow programado es más barato y avisa solo). Pero la pregunta que responde no es «¿la tienda
  está viva?» sino «**¿nos deja entrar a nosotros?**», y eso depende de por dónde salimos a
  internet: un runner de GitHub tiene otra IP y otra reputación ante Cloudflare/Akamai, así que
  contestaría por otro. Un vigía que mide a un tercero da tanto falsos positivos como falsas
  tranquilidades.
- **Lo gobierna el registro, no una lista.** Recorre `available_slugs()` y su CronJob no nombra
  tiendas, así que **registrar una tienda es vigilarla** — y, a diferencia del scraper, el vigía es
  la parte del seam que *no* obliga a tocar el repo de manifiestos al añadir una. Lo que el
  registro no puede garantizar (que la tienda implemente `check_leaves()`) lo cubre un meta-test
  que rompe `just check`. La capa de parseo es genérica sobre `BaseStore` por la misma razón:
  cubrir a las tiendas que todavía no existen sin que nadie tenga que acordarse de nada.
- **No puede ser pytest.** La imagen solo copia `src`, así que lo que corra en el cluster tiene que
  vivir ahí. Los tests `*_LIVE=1` se quedan como herramienta de mano, no como vigilancia.

La política de veredicto es la misma que ya tenía `--check-categories` y **se comparte, no se
copia** (`vigia.revisar_hojas`): solo lo accionable rompe, un 403 suelto de Akamai avisa y sigue.
Un vigía con falsas alarmas rutinarias acaba silenciado, que es peor que no tenerlo.

**Y ese es el filo de la tolerancia, medido a nuestra costa.** Aquí ponía que la del 02/08/2026
—sfera 18/19 hojas, con la que faltaba dando 403— era la prueba de que el diseño funcionaba. No lo
era: ese 403 **era nuestro**, no un blip de la tienda, y se repitió cada jueves hasta el
03/08/2026 (#129). La tolerancia no lo absorbió, lo **escondió** — degradó un fallo permanente y
reproducible a ruido de fondo, que es el precio que se paga por no romper con lo no accionable. Se
aprende de ahí una regla de lectura del informe, no un cambio de política: **un aviso que sale en
la misma hoja todas las semanas ya no es un blip, es un bug**, y el que distingue los dos casos no
es el veredicto sino la repetición. La línea base de #111 sirve para lo mismo un nivel más arriba.

Corolario aprendido en la misma sesión: **cuando una capa revienta, no se ejecuta la siguiente**.
Con Lefties sin Chromium, el segundo error salía derivado («usa la API async») y tapaba la causa
real. Un vigía que apunta a la pista falsa es peor que uno que dice una sola cosa cierta.

### La pregunta simétrica, y por qué el árbol de una tienda no es comparable tal cual (#156)

El vigía preguntaba «¿sigue viva la hoja que ingerimos?» y nada más, porque `check_leaves()` itera
`CATEGORIES`. Eso deja invisible la mitad contraria —una categoría **nueva**, o una de temporada que
vuelve— y no es teórico: es el tercer caso del mismo patrón, después de #56 y #72. Desde el
04/08/2026 hay una tercera capa (`vigia.revisar_cobertura`) que cruza el árbol publicado contra
`mapped_leaves()`, y **lo no cubierto es accionable, no aviso**: `main()` solo abre issue con lo
accionable, así que degradarlo a aviso lo habría dejado en el log del pod, que es el punto ciego que
la capa venía a tapar.

**Accionable dentro del vigía no significa bloqueante fuera de él, y confundirlo costó dos releases**
(#251, 06/08/2026). El listón de `/validar-qa` hacía P0 *cualquier* `✖`, metiendo en el mismo saco
las dos preguntas que el vigía responde y que no se parecen: «la tienda ha dejado de dejarnos
entrar» —hojas muertas, 429, parseo roto, el fallo silencioso para el que existe— y «hay una hoja
publicada que no cubrimos», que es una decisión de alcance de producto pendiente. Con las dos
indistinguibles, `banadores-bebe` —cinco prendas de bebé, etiquetadas `prioridad-4` por el propio
equipo— bloqueó v0.1.7 y v0.1.8 al mismo nivel que una tienda caída.

Desde entonces `revisar_cobertura` y el aviso estacional marcan su hallazgo (`MARCA_COBERTURA`,
`MARCA_ESTACIONAL`), y **eso convierte la salida del vigía en un contrato con un consumidor fuera de
este código**: el listón de la skill lee la marca, no la frase. La consecuencia práctica para quien
toque `vigia.py` es que reescribir esos dos mensajes sin la marca degrada la puerta de la release en
silencio, así que hay un test que los fija — la del estacional ya estaba sujeta, la de cobertura no
lo estaba por nada, y esa asimetría era justamente el agujero. Regla resultante: `✖` sin marca es
P0; `✖ [cobertura]` es P1 salvo que la hoja caiga en una de las cinco del brief, y entonces P0;
`⚠ [estacional]` está exento y no abre issue, porque el vigía ya declara en código que ese id vuelve
con la campaña.

Lo que hace defendible el cambio, y conviene no perderlo: **con la regla nueva v0.1.8 habría sido
NO APTO igual**, porque la otra hoja del mismo hallazgo (`punto-y-jerseis`) es `sudaderas/jerseys`.
Afina la severidad sin bajar el listón de lo que importa. La prueba de que el listón se revisa
editando la skill y no negociando dentro de una validación es que los dos informes lo aplicaron tal
cual y lo dejaron anotado en vez de rebajar el hallazgo sobre la marcha.

**Y desde el 14/08/2026 (#260) la capa hace también la pregunta simétrica**: de lo declarado, ¿qué
sigue publicando la tienda? Una declaración es una decisión sobre una ruta concreta, y cuando la
ruta se apaga la decisión se queda en el fichero apuntando al vacío sin que nada lo diga. No era
hipotético: al medirlo había **4 huérfanas en Lefties** —dos «Promoción» de zapatería y las dos
`REBAJAS HASTA -70%` de bebé— y **0 en c-and-a, hm, sfera y springfield**.

Sale como **aviso y sin marca nueva**, y las dos mitades de esa decisión importan. Aviso, al revés
que el hueco de cobertura, porque lo que cada uno esconde no se parece: un hueco esconde catálogo
que el usuario no ve; una huérfana no esconde nada, solo envejece — y muchas son de campaña, cuyo id
vuelve con la temporada, así que hacerla accionable abriría issue dos veces al año para borrar y
reescribir la misma decisión. Y sin marca porque **las marcas son el contrato con el listón de
`/validar-qa`**: añadir una obliga a tocar la skill, y este hallazgo no necesita que la puerta de la
release lo clasifique.

Lo que hay que saber antes de tocarla es que **«no mapeado» no es «hueco», y suponerlo la
inutiliza**. Medido sobre las tiendas que enumeran su árbol (ocho desde #179; las tres primeras
son las de #156):

| tienda | rutas publicadas | «sin mapear» en crudo | huecos reales |
|---|---:|---:|---:|
| c-and-a | 122 | 109 | **0** |
| sfera | 46 | 13 | **4** |
| mango | 197 | 87 | no aplica (ver abajo) |
| springfield | 65 | 33 | **0** |
| zara | 766 | 536 | no aplica (ver abajo) |
| cacles | 161 | 160 | no aplica (ver abajo) |
| hm | 393 (de 651 en el árbol entero) | 280 | **0** |
| lefties | 273 (de 301, sin los divisores) | 203 | **5 sin decidir (391 prendas)** |

Son medidas con fecha, no el estado de hoy: **Sfera cerró sus huecos en #212** (49 rutas, 49
cubiertas, 06/08/2026) y **Lefties los suyos en #260** (299 rutas, 299 cubiertas, 14/08/2026, tras
crecer de 6 a 12 rutas sin cubrir en cuatro barridos), y de paso enseñaron las dos formas de
cerrarlos, que son las únicas que hay.
`banadores-bebe` se **declara** —la decisión ya estaba tomada, pero solo en la prosa de la cabecera
de `CATEGORIES`, y `COBERTURA_DECLARADA` listaba tres de las cuatro ramas de baño: faltaba la del
slug asimétrico, la que no sale de copiar el nombre de sus hermanas—. `punto-y-jerseis` se
**ingiere**, porque es una de las cinco del brief. Y esa segunda es el caso de temporada que la capa
existía para cazar, ocurrido de verdad: #151 la había quitado de `CATEGORIES` al medir que la tienda
la retiró, la tienda la republicó días después y quien lo cantó fue `revisar_cobertura`, no una
revisión a mano. La lección para quien vea desaparecer una hoja: **no la borres dando por hecho que
se fue para siempre** — el comentario que documentaba aquella retirada estuvo afirmando lo falso
desde que la hoja volvió.

Las 109 de C&A eran ruido por dos motivos distintos, y los dos hay que descontarlos:

1. **53 son subcategorías de hojas que YA ingerimos** (`3-7-1-2` Camisetas cuelga de `3-7-1`, que
   está en `CATEGORIES`): sus productos entran por el padre. De ahí `SupportsCategoryTree.
   tree_separator()` y la comparación **con separador y no a prefijo pelado** — `3-1-11`
   (Calcetines) empieza por `3-1-1` (Camisetas) y son hermanas mapeadas por separado. Que el id
   jerárquico predice la contención no se supone: ya estaba medido en `c_and_a.CATEGORIES`, donde
   `3-7-2-3` (Shorts) aporta 0 productos nuevos por estar contenido entero en `3-7-2`.

   **La contención va en las dos direcciones, y la segunda no apareció hasta H&M** (#179): una ruta
   está contada si cuelga de una cubierta *o si es antepasada de una cubierta*.
   `/kids/boys/clothing` no es catálogo que falte — es el cajón donde están `trousers` y
   `nightwear`, que sí ingerimos. Salió ahí y no antes porque H&M es la primera tienda de taxonomía
   en **tres niveles** (rama/sección/hoja): Sfera y Springfield cuelgan las hojas de la raíz y
   nunca emiten un nodo intermedio. Son dos predicados y no uno, y confundirlos se paga: reducir el
   informe a las rutas **maximales** pregunta solo hacia abajo (`cuelga_de`), porque con la regla
   de antepasados una rama sin cubrir queda tapada por sus propias hijas sin cubrir y no sobrevive
   ninguna — lo cazó un test que ya existía. Y el límite de la regla nueva es lo que la hace
   segura: silencia el nodo intermedio, **nunca a sus hijas**, así que una hoja nueva bajo
   `clothing` sigue saliendo, que es justo donde vive el brief.

   El separador **vivía en `SupportsCoverageWatch` hasta #179**, y moverlo no fue cosmético: lo que
   decide si una rama cuelga de otra es el vocabulario, o sea cosa de quien enumera, mientras que
   lo que es una decisión de coste del barrido semanal es `tree_roots()`. Mientras estuvo del otro
   lado, las tiendas que enumeran **sin** vigilarse se quedaban sin él y su `--tree` pintaba como
   hueco todo lo que cuelga de una hoja ingerida: 139 de 153 en la rama de niña de Zara. Mango
   arrastraba el fallo desde #156 sin que se viera, porque su árbol tiene poca profundidad.
2. **El resto son ramas fuera del brief** (Baño, Chaquetas, Accesorios, Packs, Novedades…), que se
   declaran en `vigia.COBERTURA_DECLARADA` con el motivo. Declarar la rama calla a sus hijas, y eso
   es lo que mantiene la lista corta: 56 rutas huérfanas se declaran con 23 entradas. **Pero no se
   colapsa a ciegas**, y H&M es el contraejemplo: sus 280 rutas sin cubrir se declararían con 36
   entradas subiendo al nivel de `clothing`, y son **119** porque bajo `clothing` se declara hoja a
   hoja. Declarar el cajón entero ahorraría 83 líneas a cambio de dejar de ver una categoría nueva
   justo donde vive el brief, que es lo único que la capa existe para ver. La regla: se colapsa
   hasta el nodo que **no** sea antepasado de ninguna hoja mapeada, y ni uno más arriba.

   Esa lista es,
   literalmente, la prosa que `c_and_a.CATEGORIES` ya tenía en un comentario, convertida en algo
   comprobable — con un test que rompe si una declaración caduca, porque una entrada de sobra tapa
   exactamente lo que la capa existe para ver.

**Y la restricción que no se veía venir: hay tiendas cuyo «árbol» no es una taxonomía.** El de Mango
es su menú de navegación — promociones que rotan (`dest_toystory`, `dest_ramadam`,
`nuevosarticulosanadidos`) y un espejo `rebajas_*` de cada rama de prendas. Aun acotando las raíces
a lo que ingerimos hacen falta **72 declaraciones y caducan con la campaña siguiente**, o sea la
lista que se pudre. Por eso `SupportsCoverageWatch` es un protocolo **aparte** de
`SupportsCategoryTree`: enumerar a mano (`--tree`) vale para cualquiera que sepa hacerlo, pero
vigilar sin supervisión exige además que el ruido sea acotable. Mango conserva su `--tree` y se
declara en `COBERTURA_SIN_VIGILAR`, con la misma forma que `SIN_VIGILANCIA_DE_HOJAS`: la excepción,
explícita y revisable.

**#179 midió las otras seis tiendas y el reparto final quedó 5 vigiladas, 3 declaradas y 1 que no
puede enumerarse, que es el dato que conviene tener antes de prometer cobertura vigilada en una
tienda nueva.** Zara y Cacles se suman a Mango por motivos distintos entre sí: el árbol de Zara es su menú
(536 sin cubrir de 766, encabezados por «VER TODO» ×81 y «COLECCIÓN» ×20, más dividers y
editoriales) y las colecciones de Cacles son **planas** —Shopify no anida— con `infantil` haciendo
de paraguas de todo el catálogo infantil, así que las otras 160 parecen huecos y no lo son. En
Cacles además la pregunta de cobertura de verdad es otra: no «qué hoja falta» sino «qué
`product_type` no está mapeado», y esa ya la canta `_categoria_desde_tipo()` por el log en cada
pasada.

**Hipercor no puede enumerarse, y eso está medido y no supuesto** (05/08/2026). La faceta del árbol
vive bajo `/api`, que su `robots.txt` veta, y la pregunta que quedaba era si la página servida —que
sí podemos leer— lo publica por su cuenta. No lo hace: el documento trae **un solo enlace** a
`moda-infantil` (la miga de pan) y sus 16 bloques `"categories"` son todos la **cadena de
antepasados** de la página o del producto, el mismo dato que `page.hierarchy` del `dataLayer` —
sirve para detectar el espejismo, no para enumerar, porque no nombra ni una hermana ni una hija. Y
navegando **sin** bloquear `/api` tampoco aparecen: el menú que las listaría se despliega al
interactuar y las pide entonces a la ruta vetada. O sea que no es que nos las perdamos por no
ejecutar JS; es que ahí no están.

**Y Lefties, que era la que quedaba, desmiente que «menú de Inditex» implique el destino de Zara**
(05/08/2026). Es el mismo tipo de árbol y sin embargo se vigila: 273 nodos y **42 declaraciones**,
proporción mejor que la de H&M. La diferencia no es el vocabulario sino el **tamaño del
departamento**: los 766 nodos de Zara traen 81 «VER TODO» y 20 «COLECCIÓN», mientras que aquí la
rama infantil entera cabe en 273 y sus vistas transversales son ~17 por rama de género, las
mismas cada temporada. O sea que lo que decide no es de qué grupo es la tienda, es cuántas
declaraciones hacen falta y si caducan solas — que es lo que hay que medir antes de elegir, no
deducir del parecido.

Tres cosas suyas que se generalizan:

- **Un separador de menú no es una categoría.** 28 de los 301 nodos son rayas (`-`, tipo
  `marketing`, `key` con `SEPARACIÓN`). No se emiten desde `parse_category_tree`, porque emitirlos
  serían 28 huecos que nadie va a ingerir jamás o 28 declaraciones que envejecen con el menú. Es la
  misma decisión que en H&M con el `contentPath` y los `praData`: la mitad del trabajo de un parser
  de árbol es **no** recoger lo que no es árbol.
- **Pero filtrar por tipo sí habría roto algo, y por poco.** Las dos hojas `barefoot` que cuelgan de
  zapatos son de tipo `redirection`, o sea que descartar las redirecciones —que era la otra
  «limpieza obvia», 23 nodos— habría sacado del cruce dos hojas que **sí ingerimos**, y en el nicho
  que le da sentido al producto. Lo salvó medirlo antes de escribirlo.
- **Una sola raíz cuando el departamento no tapa nada.** Sfera, Springfield y H&M barren por rama de
  género porque su departamento arrastra casa, juguetes y vistas; de `Niños` en Lefties cuelgan
  exactamente las cinco ramas de género y un separador. Barrer el departamento entero cuesta lo
  mismo y da algo que las raíces declaradas a mano no dan: **una rama de género nueva se ve sola**.

**`mapped_leaves()` no puede necesitar la red, y esto es un contrato de la capa, no de una tienda.**
Lo llama `test_cobertura_declarada_no_solapa_con_lo_mapeado`, que corre en `just check`, así que una
tienda que resuelva sus rutas pidiendo el árbol mete Chromium y al antibot de turno en el camino
**por defecto** de CI — donde los smokes en vivo son opt-in (`SFERA_LIVE=1`) precisamente para
evitarlo, y donde el runner sale por una IP con otra reputación. Lefties lo resuelve escribiendo el
`parent` de cada `CategoryConfig` (seis cadenas para 38 hojas) con un test contra la captura del
menú que vigila que siga siendo cierto: 0,25 s frente a los 2,5 s que costaba resolverlo en vivo.
**Zara tiene hoy el mismo diseño y se libra por casualidad**: su `mapped_leaves()` también pide el
árbol, y no rompe CI solo porque está en `COBERTURA_SIN_VIGILAR` y ese test itera
`COBERTURA_DECLARADA`. El día que se decida vigilarla, esto sale primero.

**Springfield estrena una tercera forma de enumerar, y es la barata.** No publica endpoint de
categorías —su rejilla de SFCC está vetada por `robots.txt`, que es de lo que iba #81— pero no le
hace falta: la taxonomía viaja en la ruta de cada URL de producto, así que el árbol son las rutas
distintas del sitemap que `check_leaves()` ya se descarga. Cero peticiones nuevas, y una propiedad
que ninguna otra tienda puede dar: su `count` es de productos **servidos**, no declarados (en Sfera
los dos números no coinciden — 8 declarados contra 18 servidos, medido en #72). El precio de leer
el sitemap es acordarse de que **repite cada URL entre sus ficheros**: 3207 filas para 1382
productos, un factor de 2,3 que hay que deduplicar por id o los números salen inflados.

Dos consecuencias operativas: la capa **cuesta peticiones semanales contra la tienda** —122 rutas y
1m 29s en C&A, que las pide de una en una, contra 9,5 s y 46 rutas en Sfera— y por eso las raíces
las declara la tienda (`tree_roots()`) eligiendo coste, no cobertura máxima. Y el número entra en
`vigia_run` sin migración, que era justo lo que la `0022` había previsto al guardar una fila por
capa en vez de una columna por capa.

### La cobertura dice dónde mirar, no qué hay: el nombre de una hoja no predice su contenido (#175)

Los cuatro huecos que la tabla de arriba le contó a Sfera eran sus `ropa-deportiva`, y la issue que
salió de ahí daba por supuesto lo que decía la etiqueta: que faltaba una categoría deportiva y había
que crearla en todas las tiendas. **Medido el 04/08/2026, la etiqueta tapaba tres cosas distintas en
las tres tiendas que enumeran su árbol:**

| tienda | qué es «ropa de deporte» ahí | medida | qué se hizo |
|---|---|---|---|
| sfera | sus sudaderas con otro nombre | 91 productos: `Sudaderas sin capucha` 56, `Conjuntos` 25, `con capucha` 10 | mapeadas a `sudaderas` |
| c-and-a | vista transversal, como «Básicos» o «Novedades» | 42 de 45 ya entran por camisetas/pantalones/sudaderas; los 3 exclusivos son chaquetas y un chubasquero | declarada, con el número |
| mango | colección promocional | `dest_chandal_*`, y solo 3 ramas —sin `nina`— | documentada como transversal |

De ahí la regla: **antes de mapear una hoja hay que mirar qué tiene dentro, y hay una forma barata
de hacerlo.** Sfera publica en el mismo `_menubar` que ya lee `parse_category_tree()` una faceta
`attr.fashion_level3` («Tipo de producto») con valores y conteos, así que se sabe qué clases de
prenda hay en una hoja **sin pedir una sola ficha**. Lo que no se puede es filtrar después de
traer: el listado firefly **no** trae `attr` por producto (comprobado sobre el fixture), así que
quedarse con parte de una hoja exige pedirla ya acotada por la faceta, una petición más por hoja.

Dos consecuencias que sobreviven a esta issue:

- **Una hoja nueva se añade AL FINAL de `CATEGORIES`.** `list_catalog()` deduplica con «gana la
  primera», así que en Sfera los 47 de deporte que ya entraban por `sudaderas` conservaron su hoja
  y **ningún producto vivo cambió de ámbito** — que era el disparador de la falsa «caída
  sospechosa» hasta que #174 lo arregló (ver *La red de bajas comparaba dos vocabularios*). El
  orden sigue importando por lo otro: es lo que hace que el reparto por hojas no dependa de cuál se
  listó primero. Lo que aporta la hoja es entonces exactamente su residuo: +44 medidos con el
  catálogo en un solo instante (594 → 638), y no los ~80 que la faceta declaraba.
- **Una afirmación no medida envejece como si fuera dato.** Al retirar `bebe-nino/punto-y-jerseis`
  (#151) se escribió en el código que no había sustituta porque «`ropa-deportiva` … no son
  `sudaderas`». Nadie lo había mirado: son 14 sudaderas y 4 conjuntos que la tienda misma etiqueta
  así. El coste de esa frase fue que la rama de bebé de niño se quedó **sin ninguna sudadera** hasta
  hoy (ahora 18; en niña, 22). Cuando un comentario justifique dejar algo fuera, o lleva el número
  al lado o dice que no se ha medido.

**Y el límite que hay que tener presente al leerlo en verde: el vigía no dice nada de la ingesta.**
Responde «¿nos dejan entrar?» —hojas vivas y parseo de 5 productos, sin tocar el catálogo—,
así que una tienda puede llevar semanas con el vigía en verde y un CronJob desplegado **sin haber
ingerido jamás**. Medido el 02/08/2026 sobre `scrape_run`: **lefties e hipercor tenían cero pasadas
en `dev` y cero en `qa`** (en `dev` ni siquiera fila en `retailer`, que se crea en la primera), las
dos llevando semanas saliendo 38/38 y 32/32 hojas vivas. La causa no era un fallo sino el diseño:
en `dev` los CronJobs nacen `suspend: true` y la pasada se dispara a mano, y en `qa` se
desbloquearon tarde. La consecuencia sí era grave — scrapers en producción cuyo pipeline completo
(detalle, ingesta, altas/bajas) no se había ejercido nunca contra una base de datos real, y
**ninguna de las señales visibles lo delataba**: ni el registro, ni el manifiesto, ni el vigía.

Resuelto el mismo día para las tres tiendas que lo tenían (#93 hipercor, #99 lefties, y H&M al
mergearse), así que las siete de entonces tienen catálogo en `dev`. **Mango (#80) entra como la
octava y nace justo en el estado que esta sección describe**: registrada, con CronJob en el repo de
manifiestos y validada contra la tienda real en una Postgres local (1582 productos, dos pasadas),
pero **sin una sola pasada en `dev`** — que es exactamente lo que un CronJob y un vigía en verde no
prueban. Queda anotado en su issue, no fiado a que alguien lo recuerde.

**Springfield nació igual y se resolvió el 03/08/2026, y de paso enseñó lo que cuesta de verdad
estar en ese estado.** Su primera pasada en cualquier entorno —1112 productos, 8620 variantes, 25 min
en frío, sin bajas— se disparó a mano en `dev` no para cerrar este hueco sino porque hacía falta el
dato para otra issue. Y ahí está la lección que no estaba escrita: **una tienda sin ingerir deja sin
base a todas las issues que se escriban sobre ella**. #135 se había redactado desde una Postgres
local de un solo uso, y la primera pasada real duplicó su alcance el mismo día (detalle en la sección
de canonicalización). O sea que el coste de no ingerir no es solo el pipeline sin ejercer: es que
todo lo que se planifique encima está medido sobre una muestra que nadie puede volver a consultar.
Lo que sigue pendiente de Springfield es lo de siempre, el `suspend: false` de QA, que va por semver. Lo que queda es el método, que es lo
que se repetirá: **la comprobación que lo dice es una consulta a `scrape_run` por tienda y cuesta
un minuto**, y la secuencia para cerrar el círculo al añadir tienda es mergear → esperar el bump de
CI → **comprobar que ArgoCD ha sincronizado la imagen en el CronJob** (disparar antes muere con
`Tienda desconocida`) → disparar el job a mano → leer el log. Diez minutos de reloj, casi todos de
espera.

**Y una trampa de lectura en esa consulta: `scrape_run.errors` no cuenta productos perdidos.** Era
`len(sospechosos) + sondeos_sin_resolver + hojas_de_categoría_caídas`, tres cosas de gravedad muy
distinta sumadas en un entero. Medido el 04/08/2026 en QA: los `errors = 69` de Zara eran sondeos de
confirmación de baja sin resolver **que se reintentan en la pasada siguiente** —ningún producto
faltaba del catálogo—, mientras que los `errors = 15` de Sfera eran 14 sondeos **más una hoja de
categoría muerta de 35**, es decir una categoría entera que dejó de ingerirse y un ámbito sin
detección de bajas, que es un hallazgo propio y mucho peor. Leer el contador como «N productos
perdidos» es falso en los dos sentidos: alarma de más en un caso y esconde el grave en el otro. El
desglose estaba en la línea de resumen del log del pod (`confirmación activa: …` y
`⚠ N/M hojas de categoría no responden`), **que caduca cuando el pod se recolecta**.

**Desde #261 (10/08/2026) ese sumando ya no está, y el motivo cambia lo que el número significa.**
La hipótesis natural —el pool de candidatos supera el tope de 50 sondeos por pasada, luego hay
prendas retiradas que se quedan en catálogo para siempre— **se midió y es falsa**. Se sondearon a
mano 40 productos de Zara que llevaban 14+ días sin aparecer en ningún listado de QA: `probe_alive`
los dio vivos a los **40/40**, y al abrir la ficha **39/40 tenían `in_stock`** en alguna talla. O
sea que el sondeo **no miente** y la confirmación activa hace exactamente lo que debe. Lo que el
número mide no es una fuga de bajas: es **cobertura incompleta del listado**, prendas a la venta que
la pasada ha dejado de ver. La consecuencia práctica al leer una pasada: `errors` alto con
`message IS NULL` nunca fue una ingesta rota.

Dos mecanismos que hay que tener juntos para no volver a diagnosticarlo mal:

- **El tope no mata de hambre a nadie**, y por eso subirlo no arregla nada. `_load_delist_candidates`
  ordena `missing_streak DESC`, así que lo que se sale del tope entra **primero** en la pasada
  siguiente. Se ve en el dato: con 6 pasadas de Zara en QA el `missing_streak` máximo es **3**. Un
  tope proporcional al catálogo solo dispararía más peticiones para recibir más «sigue vivo».
- **El pool crece igualmente, porque nada lo drena.** Zara 25 → 60 → 106 y Sfera 1 → 15 → 31 → 33 →
  45 en pasadas sucesivas, con **`probes_dead = 0`**: Sfera no ha dado una sola baja nunca. Las
  únicas salidas del pool son «volver a verse» y «muerte confirmada», y la segunda no ocurre.

  **Corregido el 14/08/2026 (#357): esa serie es la columna `errors`, no el pool, y para Sfera
  significa otra cosa.** Se midió antes de que el código escribiera las `probes_*` (ver más abajo),
  así que el único número disponible era `errors`, que bajo la v0.1.9 valía
  `sospechosos + sin_veredicto + hojas_caídas` con `sin_veredicto` arrastrando todavía a los que no
  cabían en el tope. Para Zara la lectura de «pool» se sostiene. Para **Sfera no**: sus 45
  candidatas quedaban por debajo del tope de 50, luego `over_cap = 0` y el número entero es
  `probe.unresolved`. O sea **45 sondeos enviados y 45 sin veredicto**, no 45 candidatas que no se
  drenan. Que sea 45 de 45 y no una mezcla apunta al `except Exception: verdicts = {}` de
  `_confirm_candidates`, que deja a **todos** los candidatos sin veredicto de una vez, y a que
  `Sfera.probe_alive` abre con un `session.goto()` de siembra de Akamai sin protección — la misma
  llamada que mató la pasada del 02/08 con un timeout de 45 s. Consecuencia para el diagnóstico: a
  las prendas congeladas de Zara **se les pregunta y contestan que siguen vivas**; a las de Sfera
  **no se les ha llegado a preguntar con éxito nunca**, así que no son el mismo fenómeno aunque se
  parezcan en la tabla.

De ahí el reparto que deja la 0028: `errors` se queda con sospechosos + hojas caídas + **sondeos sin
veredicto**, y los que no caben en el tope se van a `scrape_run.probes_over_cap`. La distinción es
lo importante y no el sitio — *no cupo* es la rutina de una tienda con muchos candidatos, *sin
veredicto* es la tienda negándose a contestar, que es el fallo silencioso que el vigía existe para
cazar. Sacar los dos de `errors` habría apagado la alarma buena junto con el ruido. Las cinco
columnas `probes_{sent,alive,dead,over_cap,unresolved}` hacen que «¿el pool crece o se drena?» sea
una consulta y no una excavación en el log de un pod: `probes_sent + probes_over_cap` es el pool y
`probes_dead` el drenaje. **No hay backfill**, así que la serie empieza en la 0028 y las filas
anteriores tienen 0 en las cinco.

**Y un cero ahí tiene DOS causas, no una — que es la trampa que costó una sesión entera (#357).** A
la de arriba (la fila es anterior a la `0028`) se suma que **el código no escribió esas columnas
hasta la v0.2.0**: en la v0.1.9 el `UPDATE` final de `scrape_run` pone solo
`products_seen, variants_seen, errors, message`, así que con la migración ya aplicada las cinco se
quedaban en su `DEFAULT 0` **en todas las tiendas a la vez**. Se ve en la tabla de QA: el 10/08 Zara
aparece **dos veces**, una con `errors = 106` y los sondeos a cero y otra, ya con la imagen nueva,
con `probes_sent = 50` y `over_cap = 134`. La regla práctica al leer `probes_*`: **antes de concluir
que una tienda no sondea, comprueba con qué imagen corrió esa pasada**; y ojo con las tiendas
semanales, porque Sfera tardó una semana entera en tener su primera fila honesta. Es la misma
ventana migración-código que ya se describe arriba, vista desde el otro lado — allí engañaba al
validar una release, aquí engañó al diagnosticar un mecanismo.

**Y esa copia duradera no existía: `scrape_run.message` solo se rellenaba en el camino de fallo**
(`_record_failed_run`), o sea justo en el caso en el que la pasada NO cierra en `success`. La hoja
de Sfera cerró en `success`, así que su detalle no se guardó en ninguna parte y para cuando alguien
fue a buscarlo el pod ya se había reciclado: hubo que volver a sondear la tienda con
`--check-categories` para saber cuál era (`ninos/bebe-nino/punto-y-jerseis`, retirada entre el 24/07
y el 02/08/2026 — la rama `bebe-nina` sí la sigue publicando). Desde #151 **una pasada con éxito
también escribe `message`** cuando no está limpia —hojas caídas con su ruta, y ámbitos con caída
sospechosa— y lo deja a `NULL` cuando lo está. La columna pasa a significar «por qué esta pasada no
es limpia», no «por qué falló»; el `status` distingue los dos casos, y `WHERE message IS NOT NULL`
es la consulta que separa las pasadas que hay que mirar de las que no. Nombrar la hoja exige que la
tienda pase `leaf` a `ScanReport.leaf_gone()`, y **desde #155 lo hacen las nueve**: nació como kwarg
opcional, ocho de las nueve tiendas se olvidaron de él y durante semanas el mensaje dijo cuántas
hojas se habían caído pero no cuáles — que es exactamente el dato por el que se creó. La lección es
la del parámetro opcional: lo que la firma no exige, se omite. Ahora `leaf` es obligatorio y mypy lo
cobra en `just check`; al hacerlo obligatorio salieron **8 errores, uno por tienda**, que es la
medida del agujero.

Cada tienda lo escribe **en su propio vocabulario, el mismo que pone en `LeafHealth.leaf`** — la
ruta en Sfera e Hipercor, el `categoryId` en Zara y Lefties, el `catalogId` en Mango, el `pageId` en
H&M, el `ipimId` en C&A, el `handle` de la colección en Cacles. Que sea el mismo identificador en el
vigía y en la pasada es lo que hace que las dos nombren la misma hoja cuando hay que ir a buscarla.
**Springfield es la excepción deliberada**: su `check_leaves()` habla de ramas (`ninos/pantalones`) y
su pasada nombra el fichero de sitemap (`sitemap_4-Products.xml`), porque un sitemap es un corte
arbitrario del catálogo que no se corresponde con ninguna rama — y el fichero es lo que hay que ir a
mirar. Por lo mismo un solo fichero caído saca los 24 ámbitos de las bajas y cuenta como **una** hoja,
igual que la colección única de Cacles.

El coste medido de no tenerlo: en QA, 6 de los 10 productos vivos de `niño/ropa/sudaderas` llevaban
sin verse desde el 24/07 con `missing_streak = 0` y `delisted_at IS NULL`. Ese cero es el detalle
que lo hace invisible también en los datos — un ámbito excluido de las bajas no avanza la histéresis,
así que sus zombis no se distinguen de un producto sano ni por SQL.

Lo demás sigue igual: las `status.conditions` del Job son la otra copia duradera, y cuando
`kubectl logs` responde `error: timed out waiting for the condition`, eso no es red: es que ese job
ya no tiene pod.

**Y el segundo grado, que el vigía tampoco ve: «nos dejan entrar» no es binario.** Puede estar la
puerta abierta y el paso regulado, y el vigía solo sabe leer la puerta. Medido el 02/08/2026 (#107)
el día después de la pasada en frío de Hipercor —1.224 navegaciones de ficha en 3 h 27 min contra el
mismo host—: la tienda dejó de dejar entrar **al cluster** (timeout desde el pod, HTTP 200 en 9,2 s
desde fuera, la misma URL), y ese bloqueo duro remitió solo en menos de 3 h 45 min. Pero el mismo
sondeo del vigía, a la misma hora desde los dos sitios, tardó **24 min 28 s desde el pod contra
2 min 04 s desde fuera**: ×11,8, con veredicto `✔ todas nos dejan entrar` en ambos. El veredicto era
**cierto**; lo que no publicaba era el tiempo, que es donde estaba la única señal. Dos cosas que se
generalizan:

- **La duración delata el throttling, el veredicto no**, y hacer el smoke más pesado no lo arregla:
  con la puerta abierta también saldría verde, solo que más despacio (#111).
- **El factor local→cluster no es una constante del hardware.** El ×2,0 de la pasada en frío era CPU
  estrangulada; el ×11,8 es reputación gastada ante la tienda, y se paga justo después de la pasada
  que más falta hacía. O sea que un `activeDeadlineSeconds` calculado sobre un cluster limpio no
  tiene por qué valer la semana siguiente, y lo que protege de verdad no es un techo más alto sino
  que **fallar sea barato**.

Resuelto en #111 (03/08/2026), y con ello el vigía **empieza a escribir en la base**: cronometra
cada capa, la publica en el informe y la persiste en `vigia_run` (migración `0022`, propiedad del
scraper como el resto de tablas que él escribe). Tres cosas que se generalizan más allá de esta
tabla:

- **Lo que se compara no es la duración, es el ritmo**: segundos por hoja sondeada y por producto
  pedido. Un absoluto por tienda envejece mal porque los catálogos crecen, y el aviso se emite
  contra la **mediana de las últimas ejecuciones de esa misma tienda**, no contra un número escrito
  a mano ni contra la ejecución anterior sola (una muestra la mueve un jueves con el nodo ocupado).
- **`retailer_slug` en texto y sin FK a `retailer`.** Es la misma asimetría de dos párrafos más
  arriba, en la otra dirección: la fila de `retailer` la crea la primera ingesta, así que con FK la
  tienda que más interesa vigilar —la que aún no ha ingerido nunca— sería la única que no se podría
  medir.
- **Descalificar una muestra por tamaño absoluto es un error**, y se ve en cuanto se ejecuta de
  verdad: el primer diseño exigía ≥3 unidades para hacer de línea base, y **Cacles publica una sola
  hoja**, así que su capa de hojas se quedaba sin vigilancia para siempre — justo la tienda cuyo 429
  por huella TLS motivó todo esto. El criterio bueno es relativo: una muestra sirve si cubre al
  menos el 70 % de lo que cubre habitualmente **esa** tienda, lo que sigue excluyendo la parcial de
  «reventó a la segunda de 32 hojas».

Y una restricción de diseño que no es del vigía sino de dónde corre: **el historial nunca puede
costar el veredicto**. El vigía es el único CronJob con `suspend: false` y no aplica migraciones, así
que en QA —que se despliega por releases semver— hay una ventana real en la que el jueves llega
antes que la `0022`. Base inalcanzable, tabla que aún no existe o INSERT rechazado degradan a «sin
historial» y el sondeo sigue igual, como ya hacía el aviso de GitHub cuando falta el token.

**Y el corolario que solo aparece cuando el vigía muere: sus dos salidas se vuelcan al final, así
que un plazo agotado no persiste nada.** Medido el 07/08/2026 validando v0.1.9 en QA: el job murió
por `DeadlineExceeded` a los 2700 s dejando **cero filas** en `vigia_run` y **cero líneas** de log.
Las filas, porque se insertan en una sola transacción que se abre al arrancar y se compromete al
acabar — por eso todas comparten `ran_at`, que es el `now()` del **inicio** de la transacción y no
el de la escritura, y por eso `pg_stat_activity` enseña el vigía como `idle in transaction` durante
todo el barrido. El log, **no por buffering**: la imagen ya trae `PYTHONUNBUFFERED=1` en su etapa
`runtime` desde siempre. Era la **estructura del programa** — `main()` recorría las nueve tiendas
acumulando `Informe` en una lista y no imprimía ni una vez dentro del bucle, así que no había nada
escrito que volcar. La distinción no es académica: costó una casilla de #258 pidiendo poner en el
CronJob una variable que ya estaba, y un `PYTHONUNBUFFERED=1` no habría salvado una sola línea. La
segunda ejecución del mismo día sí completó, en **35m 21s** contra los **19m 38s** de la víspera,
con Hipercor en **18m 50s** contra 7m 13s.

Dos consecuencias, ya resueltas pero que conviene tener presentes al leer la serie vieja (#258):

- **La línea base de `vigia_run` tiene sesgo de supervivencia**: solo contiene ejecuciones que
  terminaron. La mediana contra la que se emite el aviso de ritmo no puede incluir la pasada que se
  agotó, que es precisamente la más lenta que hubo. El aviso llega tarde por construcción, y cuanto
  peor se pone la cosa menos lo refleja.
- **`activeDeadlineSeconds` no es un techo, es un borrado.** Rebasarlo no degrada el informe: lo
  elimina entero, medidas incluidas. Es la otra cara de «lo que protege es que fallar sea barato»
  de más arriba — aquí fallar cuesta la observación completa, que es lo caro.

Arreglado el 08/08/2026 emitiendo y persistiendo **por tienda** dentro del bucle en vez de al
final. El detalle que lo hace un patrón y no un parche: lo que de verdad sobrevive **no es el log**.
En `DeadlineExceeded` Kubernetes borra el pod, así que `kubectl logs` tampoco lo recupera después;
lo que queda son las filas de `vigia_run` de las tiendas que llegaron a cerrar. Un proceso que
observa y puede ser matado tiene que **persistir por unidad de trabajo**, y la salida estándar es
una comodidad para quien esté mirando en ese momento, no un registro. De regalo, la transacción
dejó de vivir el barrido entero: el `commit` por tienda la reduce a segundos, que es el caso
concreto del `idle in transaction` de #210 (sin resolver la decisión de fondo de aquella, que es de
cluster y afecta a cuatro proyectos ajenos de la misma CNPG).

**Y un contrato nuevo entre los dos repos: `SCRAPER_VIGIA_PLAZO_SEGUNDOS` vive por debajo del
`activeDeadlineSeconds` del CronJob.** Hoy 4800 aquí contra 5400 allí. Son dos números en dos
repositorios distintos que **solo significan algo juntos**: el de arriba es el hacha del
controlador, el de abajo es el vigía saliendo por su propio pie —cortando entre tiendas, nunca a
mitad de una, para no dejar una medida parcial que parezca lenta y envenene la línea base— y
publicando lo que llevaba. Subir el deadline sin subir el plazo desperdicia el margen; bajarlo por
debajo del plazo devuelve la muerte muda. La red de seguridad es el plazo; el deadline es lo que
pasa si la red falla.

### Descartar el residuo de una hoja da por hecho que hay otra puerta, y a veces no la hay (#289, #200)

`FiltroDeHoja` con `resto=None` existe porque el residuo de una hoja que reagrupa **no es una
categoría** (#192, sección de arriba). De ahí se siguió, sin medirlo, que descartarlo era gratis:
«lo que no casa ya entra por su propia hoja o el brief lo deja fuera». Esa disyuntiva tiene un
tercer caso, y es el que se comió 44 prendas.

Medido en Zara el 13/08/2026 pidiendo las 62 hojas mapeadas: la tienda publica **4490 ids** y la
pasada emitía **4417**. Los 73 de diferencia salen **solo** en una hoja-lookbook. Desglosados por la
familia que declara la tienda, **44 son prenda del brief** (30 pantalones, 8 sudaderas, 4 vestidos,
1 camiseta, 1 ropa-interior); los otros 29 sí se descartan con razón — 18 están fuera del brief
(gorro, cazadora, chaqueta) y 11 son los nodos `LOOK`, que ni son producto ni traen nombre.

**La consecuencia no es perder catálogo, es perderlo en silencio y para siempre.** Un producto que
solo publica una hoja filtrada no se emite nunca, así que su `last_seen_at` se congela el día que se
ingirió y ya no vuelve a moverse. No se da de baja —la confirmación activa lo encuentra vivo y
`_rescue` le pone la racha a cero en cada pasada—, o sea que se queda en el catálogo enseñando un
precio que nadie vuelve a comprobar. Ninguna métrica lo delata: la hoja responde 200, `filtro_vacio`
no salta porque el filtro sí casó con algo, y el `ScanReport` contaba hojas, no productos.

Esa última frase es la que **cerró #358**: el `ScanReport` cuenta ahora también productos, y publica
por hoja cuánto aporta el residuo tras el cruce contra `emitted` (lo que *aporta*, no lo que parsea
— `parse_listing_leftovers()` deduplica dentro de una hoja pero no entre hojas, así que sumar lo
parseado contaría dos veces al que sale en dos lookbooks). Sin eso, el arreglo de aquí abajo podía
irse a cero en silencio y el síntoma tardaría meses en reaparecer.

**Y de ahí sale una regla que vale para cualquier contador futuro, no solo para éste: lo que ocurre
en TODA pasada no puede ir al `scrape_run.message`.** El rescate aporta decenas de prendas en todas
las pasadas de Zara, así que publicar la cifra ahí dejaría el `message` distinto de `NULL` siempre y
rompería lo único que lo hace consultable (`WHERE message IS NOT NULL`, ver `_success_message()`).
El reparto que se adoptó —y que hay que respetar al añadir el siguiente contador— es: **la cifra al
resumen de `run.py`, la anomalía al `message`**. Aquí la anomalía es una hoja con filtro que ha
dejado de aportar, y **no** saca su ámbito de las bajas como sí hace `filtro_vacio()`: un lookbook
sin residuo clasificable es un estado legítimo, y tratarlo como sospecha metería falsos positivos en
el camino más delicado del scraper.

La cura tiene una propiedad que hay que conservar si alguien la reescribe: **el residuo se emite al
final de la pasada, no en su hoja**. Las hojas-lookbook van DELANTE a propósito, así que en el
momento de leerlas todavía no se sabe si su pantalón entrará luego por la hoja de pantalones.
Demorarlo hasta que `emitted` está completo es lo que hace el arreglo puramente aditivo: 4417 →
4461 productos, **0 re-etiquetados**. Emitirlo en su hoja habría movido de categoría a los que sí
tienen puerta propia, que es exactamente lo que #200 quería evitar.

Y la trampa que encontró el revisor, que es la que no se ve leyendo el cambio: **la familia decide
la categoría y la hoja decide el género, así que juntas pueden fabricar un ámbito que la tienda no
recorre**. Un `PETO` (que va a `vestidos`, decisión heredada de la hoja `PETOS | MONOS`) en el
lookbook de niño da `niño/ropa/vestidos`, y en Zara `vestidos` solo existe para niña y unisex. Eso
importa porque `store.scopes()` es la **única** fuente de `safe_scopes` en `ingest.py`: una fila en
un ámbito no declarado no entra jamás en `_advance_missing`, `_delist` ni `_confirm_candidates`, o
sea que **no se puede dar de baja nunca**, ni desapareciendo la tienda entera. Ni siquiera
`unscanned_scopes` lo vería, porque compara ámbitos declarados contra recorridos, no contra los que
de verdad salen en `entries`. Cualquier mecanismo que derive el ámbito de dos fuentes distintas
tiene que validarlo contra `scopes()` antes de emitir.

Vale para cualquier tienda con hojas que reagrupan, no solo Zara: hoy las tienen también Sfera, H&M
y C&A. Lo que cambia por tienda es si el residuo tiene otra puerta, y eso **se mide, no se supone**
— es el mismo error de método que #192 documenta un nivel más arriba.

### Una hoja puede estar viva y vacía, y eso no lo veía nadie (#289)

Zara sirvió el 13/08/2026 **200 con cero productos** en dos de sus 62 hojas (`2427530` y `2427980`,
las de `ropa interior | calcetines`). El sondeo semanal las daba sanas porque `check_leaves()` solo
miraba el código HTTP, y la pasada tampoco decía nada: `filtro_vacio()` solo cubre las hojas que
llevan filtro. Es el mismo punto ciego que en H&M obligó a inventar el canario, un escalón más
abajo — allí la hoja muerta devuelve contenido plausible; aquí devuelve contenido vacío.

El efecto se ve en el reparto de prendas congeladas, y **solo si se normaliza**: en crudo manda
`pantalones` (28 + 19 de 95), pero dividido por el tamaño de cada ámbito el peor con diferencia es
`niña/ropa-interior` con **7,0 %** (14 de 201) frente al 4,8 % de `niño/pantalones` y el 0,6 % de
`niña/camisetas`. El ranking crudo señalaba la categoría más grande; la que tenía la hoja rota era
otra.

Lo que se publica ahora es el número de productos en el detalle de `LeafHealth`, como ya hacía
Sfera. **El veredicto sigue siendo `True` a propósito**: una hoja vacía de verdad existe —Zara vacía
y rellena categorías con la campaña— y darla por muerta convertiría el vigía en un generador de
falsos positivos semanales, que es justo lo que no puede ser.

### El cuello de las tiendas de navegador era el `limits.cpu` del pod (#160, #258, #259)

La degradación del 07/08 —Hipercor ×2,6, Sfera ×4,1 en parseo, **las dos de Chromium y ninguna de
las siete de httpx**— parecía de las tiendas o de contención entre ellas. Medido el 08/08/2026
contra `deal-tracker-prod`, es del **cap de CPU del propio pod**:

| Hipercor sola, mismo nodo (worker6), misma imagen | total | hojas | parseo |
|---|---|---|---|
| `limits.cpu: 1` | **18m 32s** | 14m 54s (26,3 s/hoja) | 3m 38s (43,7 s/producto) |
| `limits.cpu: 3` | **8m 41s** | 5m 57s (10,5 s/hoja) | 2m 44s (32,8 s/producto) |

Y el uso real durante el barrido con el cap suelto: **1038m**, por encima del techo viejo de 1000m.
El pod pedía más CPU de la que el límite le dejaba coger. Tres cosas que se generalizan:

- **«Correr una tienda sola» no discrimina nada en el vigía, y creerlo hizo diseñar mal el
  experimento.** El barrido es un **bucle secuencial en un solo proceso**: las nueve tiendas nunca
  corren a la vez, así que «contención entre ellas» no existe como fenómeno. Sola, Hipercor tardó
  18m 32s contra 18m 50s acompañada, y Sfera 4m 26s contra 4m 40s — idénticas, como tenían que
  salir. Lo que compite es lo de **fuera** del proceso (el cap del pod, y los demás pods del nodo),
  y eso es invariante a con quién barras.
- **Un límite de CPU no se nota como error, se nota como lentitud**, y por eso se confunde con la
  tienda. Solo lo delatan dos números que hay que ir a buscar: el uso real contra el techo, y el
  mismo trabajo con el techo movido. Ninguno sale en el informe del vigía.
- **El nodo hay que fijarlo al comparar.** El primer intento cayó en `worker1` en vez de `worker6`
  y habría mezclado cap con nodo. Y al fijarlo con `nodeName` se salta el scheduler, así que subir
  también los *requests* dio `OutOfcpu`: worker6 está al **75 % de requests y 163 % de límites**, o
  sea sin 1 CPU libre que reservar. Se sube el **límite**, no el request.

Lo que esto **no** dice: sigue sin re-medirse bajo esta luz el ×11,8 local→cluster de #107, que se
atribuyó a reputación gastada ante la tienda. Aquello fue un evento distinto y con bloqueo duro
comprobado; esto es el régimen permanente.

### Una hoja de campaña no es una hoja retirada, y su categoría es del producto (#195, #176)

Dos tiendas publican hojas cuya vida depende de una campaña, y fallaban de formas opuestas que
resultaron ser la misma pregunta. En Mango, `rebajas_newborn.sudaderas_newborn` dio **404** y un día
después respondía con el **mismo `catalogId`**: mientras estuvo caída no se ingirió lo que había
dentro y `check_leaves()` la cantó como RETIRADA, que es un aviso semanal pidiendo un id nuevo que
ya existe. En Lefties, las dos `REBAJAS HASTA -70%` publicaban 26 prendas del brief que **no estaban
en ninguna de las 38 hojas mapeadas**, así que no mapearlas perdía catálogo que solo vive ahí.

Lo que hizo decidible el caso fue medir **por qué** no estaban en su categoría, y la respuesta no
era ninguna de las dos hipótesis de partida (06/08/2026, barrido de las 40 hojas):

| | componentes | temporada | rebajados |
|---|---:|---|---:|
| 38 hojas de categoría | 2207 | `I2026` (2207 de 2207) | 275 |
| 2 hojas de rebajas | 32 | `V2026` (32 de 32) | 32 |

O sea que **rebajar no saca la prenda de su categoría** —275 rebajados viven dentro de las hojas
normales—: lo que las separa es la temporada. Las hojas permanentes ya han pasado a la que entra y
en rebajas queda el saldo de la que sale, que no cuelga de ninguna categoría. La regla que sale:

> Una hoja de campaña **se mapea si su id es estable y publica producto propio**. Si mezcla
> categorías, la categoría se deriva **por producto**, no por hoja. Y su apagado —404, o desaparecer
> del menú— **no es una retirada**: es estacional, y no puede sonar como accionable.

Tres consecuencias que van más allá de estas dos tiendas:

- **`estacional` es una propiedad de la hoja, no un cuarto veredicto.** `LeafHealth.alive` sigue
  siendo `False` cuando la hoja está apagada —no se puede listar, y no se ingiere lo que hubiera
  dentro—; lo que añade la marca es que era *esperable*, y con eso el vigía avisa en vez de romper.
  En la pasada tiene un segundo efecto, y ese sí protege datos: una hoja estacional caída **no
  cuenta como caída**, porque al acabar una campaña se apagan muchas a la vez y eso dispararía
  `SCRAPER_SCAN_MAX_DEAD_RATIO` en una tienda sana — es el mismo razonamiento que ya hacía
  `mango.es_listado()` para la hoja *vacía*, aplicado a la hoja *ausente*.
- **La categoría por producto sale de `classification.family`, y `subfamily` no vale.** Medido sobre
  la hoja de rebajas de Lefties: `family` acertó en los 26 contra el nombre de la prenda y
  `subfamily` mintió en 4 (una falda como `Girls’ Chunky Knit Top`, un pijama como `…Long Sleeve
  Polo`). Y la tabla familia→dominio no se inventa: se construye contando en qué categoría cae cada
  familia **en las hojas que ya se ingieren**, por eso `SHORT` va a `vestidos` —es donde lo pone la
  hoja `faldas | shorts` de niña— y el short de niño llega como `BERMUDAS`. Lo que no se puede
  defender se descarta: `ENSEMBLE..SET` (el conjunto, que es la pregunta abierta de #192) cae en
  cuatro categorías distintas, así que decidirlo de tapadillo sería peor que perder la prenda.
- **El orden de `CATEGORIES` es lo que hace segura la hoja mezclada**, y es el mismo mecanismo de la
  sección anterior usado al revés: yendo **la última**, `list_catalog()` («gana la primera») le deja
  la categoría a quien ya la tiene por una hoja que la sabe mejor, y la de rebajas solo aporta su
  residuo. Moverla hacia arriba rompería la categoría de producto vivo **en silencio**, porque el
  producto seguiría entrando.

Y una deuda que esto deja abierta y **no está medida**: esas prendas son las únicas del catálogo que
no cuelgan de ninguna hoja permanente, así que al acabar la campaña dejan de verse del todo y decide
`probe_alive()`. El de Lefties da por vivo cualquier id que `productsArray` siga reconociendo aunque
esté agotado —Sfera usa dos señales, esta una— y a un producto confirmado vivo `ingest.py` le pone
la racha a cero (`_rescue`), así que un saldo agotado que la tienda siga sirviendo en el detalle se
quedaría en el catálogo indefinidamente. Hay que mirarlo al acabar esta campaña.

Con una calibración que ahorra dar por hecho el desenlace: **el temor análogo en Zara se midió y
salió al revés** (#261, ver la trampa de lectura de `scrape_run.errors` más arriba) — de 40
candidatos ausentes 14+ días, 39 tenían stock y `probe_alive` acertaba en los 40. Que el sondeo sea
la señal débil no implica que esté mintiendo; en Zara lo que fallaba era la cobertura del listado.
Lo de Lefties sigue en pie porque su sondeo es de una sola señal y el de Zara pide la ficha entera,
pero la conclusión hay que medirla, no deducirla.

### Una pasada muda no se puede depurar, y las dos tiendas que acumulan son ciegas por diseño

Hasta el 04/08/2026 la pasada **no escribía un solo byte hasta el resumen final**: `ingest.py` no
tenía ningún `print()` y `run.py` no configuraba handler de logging, así que ni siquiera se veían los
`logger.info()` que mango, hm y springfield ya tenían escritos — sin handler, el *last resort* de
Python solo emite `WARNING` y por encima. La pasada en frío de Hipercor corrió **5 horas y produjo
0 bytes** (#146).

El coste no es la incomodidad: es que **una pasada de horas y una colgada son el mismo log vacío**.
Cuatro intentos de poblar Hipercor (3 h en dev, 3 h y 5 h en QA) devolvieron un bit cada uno —cupo o
no cupo— y ninguno pudo decir dónde se iba el tiempo. Con el progreso publicado, la misma pregunta
se contestó en **15 minutos**.

Tres cosas que solo se ven al montarlo:

- **El latido va por tiempo, no por número de fichas.** Así el volumen del log no depende del tamaño
  del catálogo, y sale gratis el requisito de no ensuciar el modo normal: una pasada caliente de Zara
  (1m35s) no llega al primer aviso de 5 min. Un latido «cada N fichas» habría llenado el log de las
  nueve tiendas para resolver el problema de una.
- **Encender INFO a secas es peor que no encenderlo.** `httpx` emite una línea por petición, o sea
  2219 en Zara y 1224 en la fría de Hipercor: ahoga exactamente la señal que se acaba de añadir. Las
  librerías se quedan en WARNING (`SCRAPER_LOG_LEVEL=DEBUG` las devuelve).
- **`hipercor` y `hm` son invisibles desde `ingest.py`, y no se puede arreglar allí.** La ingesta
  late al recibir entradas del generador, y esas dos **acumulan la pasada entera antes de emitir
  ninguna** porque el cruce de géneros (#98) exige haber visto todas las hojas: el ámbito de una
  entrada ya emitida no se puede corregir. Es un contrato del pipeline, no un descuido — así que el
  latido de listado vive **dentro** de esas dos tiendas, que es el único sitio donde se sabe por qué
  hoja va. `Latido` está en `scraper/progreso.py` y no en `ingest.py` para que `stores/` no importe
  de la ingesta, que invertiría la capa.

### El coste de una pasada en frío no está donde parecía, y el cap de CPU muerde antes de las fichas

La §4 de #93 llevaba cuatro intentos leyendo el `activeDeadlineSeconds` como la enfermedad. No lo
era: el pod estuvo pegado a su cap de 1 CPU el **98 % de 295 minutos**. Pero al instrumentar la
pasada aparecieron dos correcciones más, y la segunda invalida un cálculo que parecía sólido:

- **La fase de listado son 30 min, y las hojas NO cuestan lo mismo ni de lejos.** Se estimaba en
  ~3,5 min extrapolando los 5,4 s/hoja de #111 — número del **sondeo del vigía**, que hace *una*
  petición por hoja, mientras que `list_catalog` pagina cada hoja con el navegador. Primera moraleja:
  una medida de una capa no vale para otra aunque la unidad se llame igual («segundos por hoja»).
  Pero la segunda es peor y me la comí entera el 04/08/2026: al medir las **tres primeras** hojas
  (4m17s, 4m42s, 4m52s con cpu 1) extrapolé ×32 y anuncié ~2h28m. **Falso.** La pasada completa
  publicó `listado: 1229 entradas en 30m`, porque las primeras hojas son las caras y a partir de la
  sexta bajan a **~35-40 s** (hoja 6→10: 4 hojas en 2m19s; 10→14: 4 en 2m30s). Extrapolar de las
  primeras hojas sobreestima **×3,4**. Con el instrumento puesto, el número real cuesta media hora de
  espera: no hace falta extrapolar nada, y aquí la extrapolación habría hecho dimensionar el CronJob
  por un fantasma.
- **Las fichas arrancan a 14,7 s, no a los ~10 s que se venían asumiendo**, con la estimación en
  4h59m sobre las 9 primeras (dato de arranque: el ritmo de Zara se estabilizó de 0,8 a 0,7 s/ficha
  en el primer minuto, así que este también puede bajar). Sumado a los 30 min de listado, la pasada
  en frío queda **en el filo de los 18000 s** del deadline, no holgadamente dentro.
- **Subir el cap de 1 a 2 da ×1,44 y sigue saturando.** Esto sí es comparación limpia, y sobrevive
  al error de arriba porque compara **las mismas tres hojas** contra sí mismas: mismo Job, mismo
  nodo, misma imagen, mismas env, solo cambia el límite. 3m06s / 3m13s / 3m18s (media **3m12s**)
  contra 4m37s, y las entradas acumuladas coinciden hoja a hoja (114, 161, 288), o sea el mismo
  trabajo. Pero el pod marca **1943m de 2000m**: dos cores también se saturan, así que ×1,44 es lo
  que da subir a 2, no el techo de la mejora.

Consecuencia práctica para dimensionar cualquier tienda de navegador: el `activeDeadlineSeconds` hay
que repartirlo entre **dos** fases que escalan distinto —hojas × páginas por hoja, y fichas × una
navegación cada una—, y la única forma barata de saber cuál manda es leer el progreso de una pasada
real durante quince minutos. El límite de CPU va en `base` y no en un overlay: el cuello es
estructural, y dev y qa tienen que rendir el mismo valor para que una medida de uno valga en el otro.

### Y el cuello no era la CPU: era renderizar páginas que no hacía falta renderizar (#160)

Todo lo de arriba está bien medido y aun así apuntaba al sitio equivocado, porque las tres medidas
comparten un supuesto que nadie puso a prueba: **que para leer la página hay que ejecutarla**. En una
tienda SSR es falso, y comprobarlo cuesta una petición.

El `dataLayer` y el `ld+json` de Hipercor —o sea las tallas con su precio y su stock, y el precio
tachado— vienen en el **documento servido**. `BrowserSession.pedir_html()` lo trae con
`page.request` (mismas cookies y mismo fingerprint que el navegador) sin ejecutar JS, sin layout y
sin subrecursos. Medido contra la tienda el 04/08/2026:

| | navegando | pidiendo | pasada en frío completa en dev |
|---|---|---|---|
| ficha | 1,14-1,41 s | **0,08-0,42 s** | 15,4 s → **0,9 s**, planas en 1225 fichas |
| rejilla | 1,34 s | **0,21 s** | listado 30 min → **2 min** |
| total | ~5h41m (proyectado, **nunca terminó**) | | **22m34s**, el 7,5 % del deadline |
| CPU del pod | **1943m clavados**, 98 % del tiempo | | mediana ~75m de un cap de 2000m |

`run #46 OK — 1225 en catálogo (1222 con detalle), 8886 variantes, 8886 precios; bajas: 0/0`. O sea
que la pregunta «¿cuántos cores le hacen falta?» era la pregunta equivocada: con el render fuera, el
cap de 2 sobra tanto que el patch que iba a subirlo a 3 se cerró sin mergear.

**Lo que no se puede ahorrar, y es lo que hace que esto no sea un cambio de una línea.** El ahorro se
paga en dato en cuanto la página deja de ser la fuente completa, así que hay dos excepciones y las
dos se deciden con lo que ya está descargado:

- **La ficha agotada del todo** pierde el `ProductGroup` y sus tallas solo quedan en el selector que
  pinta el JS: parseada de lo servido sale con **una variante sin talla en vez de ocho**. Ahí sí se
  navega.
- **La ficha de talla única** tampoco trae `ProductGroup` y por eso se parece a la anterior, pero su
  dato está entero en lo servido. Renderizarla sería el **peor caso de todos**: su selector no existe,
  así que se esperaría el `browser_hydrate_timeout` completo por cada una para leer lo mismo. Las
  separa `dataLayer.product.group_by` (`"Talla"` frente a `"None"`).

Y una precondición que no se ve venir: **sin cookies del origen la tienda contesta 403 a la ficha,
pero 200 a la rejilla**. Como la fase de detalle abre su propia sesión y ya no navega a ningún sitio,
sin una siembra explícita la pasada muere a la sexta ficha con cara de bloqueo de Akamai. La
asimetría entre los dos tipos de página es lo que lo hace difícil de leer: la mitad del scraper
funciona.

La pregunta a hacerse antes de optimizar un scraper de navegador no es cuánto recorta bloquear
recursos, sino **si esa página concreta necesita navegador en absoluto**. Se contesta comparando
`pedir_html()` contra `get_html()` sobre las mismas URLs y parseando las dos, que es media hora.

#### Pero esa pregunta solo aplica a una tienda que parsea HTML, y las otras dos no lo hacen (#168)

Aquí decía que esto era «generalizable a las otras dos tiendas de navegador (`sfera`, `lefties`),
que hoy siguen navegando». **Es falso**, y conviene saber por qué antes de repetir el análisis:
medido el 05/08/2026, ni Sfera ni Lefties parsean HTML. Las dos van por `BrowserSession.get_json()`,
que ya usa `page.request` — el mismo camino sin render que `pedir_html()`. No hay una sola llamada a
`get_html()` en `sfera.py` ni en `lefties.py`.

En una tienda así el navegador **no está para leer la página, está para el apretón de manos de
Akamai**: la primera petición de la sesión se lleva un 403 y las cookies llegan con esa misma
respuesta, así que hace falta una navegación real y no se puede quitar. El techo del ahorro es por
tanto muchísimo más bajo que el de Hipercor, y la pregunta útil es otra: **cuántas navegaciones hace
la pasada, y si todas hacen falta**.

Contestada, había una de más por hoja. `sfera._iter_category()` sembraba dentro del bucle de
`list_catalog()` — 38 navegaciones por pasada, cada una con el render completo de una página de
escaparate— cuando `check_leaves()` sembraba **una sola vez** desde #129 y hasta tenía un test
defendiéndolo. La misma clase se contradecía consigo misma:

| | antes | después |
|---|---|---|
| navegaciones de la pasada de listado | 38 | **1** |
| peticiones de API | 80 | 80 (igual) |
| entradas de listado | 663 | 663 (igual) |
| fase de listado | ~2m | **53s** |

Catálogo idéntico: mismos 663 ids y **0 variantes perdidas**. Y `probe_alive` pasó a **pedir** la
PDP en vez de navegarla —de ella solo se lee el status— con 14/14 veredictos idénticos sobre 5 URLs
vivas y 2 canarios, sembrado y sin sembrar. Lefties se queda como estaba, y eso también es
resultado: sus `goto` son uno por fase, no uno por página, y sus 76 rejillas se sirven con una sola
navegación.

Dos cosas de método que costaron tiempo y se repiten cada vez que se mide una tienda:

- **Un canario mal fabricado miente en la dirección peligrosa.** El id de Sfera va en la **ruta**
  (`/ninos/A200976492-...`); mutar el último número de la URL toca el `parentCategoryId` de la query
  y devuelve el mismo producto con un 200. Leído tal cual, ese 200 dice «esta tienda no da 404
  nunca», que es exactamente el hallazgo alarmante que no era.
- **Un subconjunto estricto entre dos pasadas no prueba que el cambio pierda datos.** La primera
  comparación dio 662 contra 663; repitiendo se vio que la diferencia seguía al reloj y no al código
  (la tienda da altas entre pasada y pasada). Contra una tienda viva, el A/B hay que cerrarlo
  repitiendo, no razonando sobre la dirección de la diferencia.

#### Y en la tienda que originó la regla quedaron dos rutas sin convertir, cinco días invisibles (#259)

Lo de arriba se aplicó a Hipercor y se comprobó en Sfera, y aun así **la conversión se quedó a
medias en la propia Hipercor**. Medido el 09/08/2026: `check_leaves()` seguía navegando sus 34 hojas
para acabar leyendo el `dataLayer` —el mismo que `_iter_category()` lee del documento servido dos
métodos más arriba— y `probe_alive()` navegaba una ficha entera por sondeo para quedarse solo con el
`status`, hasta el tope de 50 por pasada. La ironía del reparto: a Sfera **sí** se le convirtió el
`probe_alive` en #168 («14/14 veredictos idénticos»); a Hipercor, que es donde nació la regla, no.

Por qué sobrevivió tanto sin verse, que es la parte generalizable:

- **Las rutas frías no están en el camino crítico de la pasada.** `check_leaves()` y `probe_alive()`
  no se ejercen al medir una pasada de ingesta —la primera solo la usa el vigía, la segunda solo
  entra cuando hay candidatos a baja—, así que la métrica que disparó #160 (coste por ficha, pasada
  en frío) no las mira. Se convierte lo que se está midiendo.
- **Y su coste se leyó como problema de cluster.** El vigía se clavaba en 1038m contra un cap de
  1000m mientras el scraper de la misma tienda iba a ~75m de mediana contra uno de 2000m. Esa
  diferencia de ×14 tiene una explicación sencilla —no hacían lo mismo— y durante cinco días se
  atribuyó a contención de nodo, con dos issues (#258, #259) razonando sobre el `limits.cpu`.

La cabecera del módulo llevaba desde #160 declarando el invariante en presente («quedan **dos**
navegaciones de verdad, y **ninguna por producto**») y era falso desde el día que se escribió. Un
invariante afirmado en una docstring no se verifica solo: en `hipercor.py` ahora lo defienden dos
tests que afirman `navegadas == [BASE_URL]`, que es como ya se vigilaba `list_catalog()`.

Medido pidiendo y navegando **las mismas 12 rejillas alternadas** en un proceso, para que la
variación de la tienda la paguen las dos rutas: mediana **0,46 s contra 1,00 s, ×2,2**; y el barrido
del vigía, hojas 37,3 s → 20,0 s (1,1 → 0,6 s/hoja). Ese ×2,2 es **el suelo**: se midió en un
portátil, donde el cuello es la red, y lo que se ahorra es CPU.

Dos cosas comprobadas de camino que evitan repetir el trabajo:

- **El 404 sobrevive al cambio de transporte.** Es el riesgo real, porque `probe_alive()` alimenta
  las bajas y un id muerto que respondiera 200 redirigido al padre dejaría prendas retiradas en
  catálogo para siempre. Coinciden exactamente: vivo `pedir=200 navegar=200`, inventado
  `pedir=404 navegar=404`, sin redirección.
- **`page.request` no pasa por `route()`**, así que `descartar_recursos()` no aplica por esa vía.
  Con `bloquear()` pasaba lo mismo hasta #282, y el matiz importaba: no había violación, pero el
  `Disallow: /api` se cumplía **por construcción** —ninguna URL nuestra cae ahí, y una página que no
  se renderiza no pide nada por su cuenta— y no por el filtro. Lo que quedaba fuera de nuestro
  control era que redirigiera la tienda, porque `page.request.get()` sigue los 30x de forma
  transparente y sin veto.

Corolario para la siguiente tienda que se convierta: **la lista de rutas a mirar no es «el listado y
la ficha», es todo lo que implemente el `Protocol`** — `list_catalog`, `fetch_details`,
`check_leaves`, `probe_alive` y `category_tree`. Un `grep get_html` por el fichero contesta en
segundos. Estado hoy: en `hipercor.py` el único `get_html()` que queda es el respaldo de la ficha
agotada; `sfera.py` y `lefties.py` no tienen ninguno; `hm.py` conserva uno en `_menu_html()`, que
solo llama `category_tree()` —o sea `--tree` y el vigía, nunca la pasada normal—, y por eso sus
`limits.memory: 512Mi` no esconden un OOM latente pese a arrancar Chromium.

### El veto de rutas lo tiene que conocer la sesión, no la tabla de `route()` (#282)

La cura del punto anterior no fue parchear `pedir_html()`: **la asimetría era del transporte, no de
una tienda**, así que arreglar solo el camino que dolía la habría dejado esperando a la siguiente
conversión — y esa superficie crece sola (2 sitios en #160, 4 en #281, más el de Sfera).
`bloquear()` guarda ahora su patrón en la sesión además de registrarlo en Playwright, y
`pedir_html()` y `get_json()` comprueban contra él la URL pedida **y `resp.url`**, la final tras
redirecciones, elevando `RutaVetada`.

Tres cosas medidas que evitan rehacer el razonamiento:

- **`max_redirects=0` no vale, aunque exista.** Playwright lo ofrece desde 1.26 (aquí se fija
  `>=1.49,<1.62`), pero **eleva** al superar el límite en vez de devolver el 30x: una
  canonicalización de barra final tumbaría la pasada por nada. Por eso la comprobación es a
  posteriori sobre `resp.url`.
- **El emparejado va con `fnmatch` de la stdlib, no con `glob_to_regex_pattern` de Playwright**, que
  existe y haría lo mismo. Es un interno, y este repo ya paga uno en `tls.py`. Contrastados los dos
  sobre `**/api/**` con seis URLs reales de Hipercor **coinciden en las seis**, incluido que ninguno
  casa `…/api` sin barra final. Esa coincidencia es la condición de que esto sirva: lo que se
  comprueba tiene que ser exactamente lo que `route()` bloquea, o el veto significaría una cosa al
  navegar y otra al pedir.
- **Detecta, no previene, y conviene no leer de más.** Cuando se ve la redirección la petición ya
  salió; lo que se garantiza es que su contenido no se parsea ni se guarda y que la pasada se para.

Queda un hueco conocido y **deliberadamente no tapado**: `probe_alive()` de Hipercor hace
`except Exception: continue`, así que ahí una `RutaVetada` se traga como «sin veredicto». Es seguro
—no produce bajas falsas— pero es el único de los cinco sitios que no la hace visible; los otros
cuatro la elevan o la reportan como `LeafHealth`.

### Un pod muerto deja su transacción abierta, y la siguiente pasada se queda muda esperándola

Corolario operativo de que la ingesta sea atómica, y cuesta caro reconocerlo tarde. Al borrar un Job
de scraping a mitad —o al matarlo el `activeDeadlineSeconds`— el backend de Postgres puede quedarse
en `idle in transaction` **sosteniendo los locks** de las filas que ya tocó, porque el servidor tarda
en enterarse de que el cliente murió. La siguiente pasada arranca, hace su primer `INSERT` sobre
`retailer`, y se queda esperando ese lock.

Lo que se ve desde fuera es exactamente lo que se vería si la pasada fuera lentísima: pod `Running`,
log con una sola línea y ningún progreso. Lo que lo distingue es el consumo: **1m de CPU y 33Mi de
memoria** — sin Chromium levantado —, medido el 04/08/2026 tras trece minutos parada. Una pasada
lenta consume; una bloqueada, no.

```sql
SELECT pid, state, wait_event_type, now()-xact_start AS en_transaccion, query
  FROM pg_stat_activity WHERE datname='deal_tracker' AND pid <> pg_backend_pid();
```

`state='active'` con `wait_event_type='Lock'` es el bloqueado; `idle in transaction` con horas
encima es el culpable. Borrar el pod dueño suele bastar (se lleva su conexión y la transacción hace
rollback); si el backend sobrevive al pod, `pg_terminate_backend(<pid>)` — y comprobando antes que
el pod cliente ya no existe, porque en un cluster con varias sesiones eso puede ser el trabajo de
otro corriendo.

**Desde #169 la espera está acotada y el síntoma es otro**, así que quien se lo encuentre ya no verá
el silencio de arriba: `db.connect()` abre la sesión con `lock_timeout` (`SCRAPER_LOCK_TIMEOUT`,
30 s; `0` devuelve la espera infinita), y al saltar, `run.py` consulta `pg_stat_activity` por una
conexión nueva —la que falló se queda con la transacción abortada— y escribe un error que dice que
**no es lentitud**, con el `pid`, el estado y cuánto lleva en transacción quien retiene las filas.
Va como parámetro de la conexión y no como un `SET` posterior para que cubra lo primero que se
ejecuta, y alcanza a toda la sesión a propósito, **migraciones incluidas**: un `ALTER TABLE`
esperando su `ACCESS EXCLUSIVE` detrás de una pasada viva es este mismo fallo con peor cara. (Con
una excepción acotada desde #298, en la sección siguiente: la espera por el lock que serializa los
dos migradores es otra cosa y usa su propio valor, puesto con `SET LOCAL` para no pisar este.) Dos
consecuencias medidas: una pasada bloqueada tarda **~2× el timeout** en morir, porque
`_record_failed_run` vuelve a chocar con el mismo lock al dejar constancia; y el vigía se beneficia
sin tocarlo, porque `Historial` ya se degradaba ante cualquier excepción y ahora lo hace en 30 s en
vez de colgar el job.

Lo que **no** se hizo, y con un dato que lo desaconseja: `idle_in_transaction_session_timeout` en el
rol de la CNPG, que atacaría la causa en vez del síntoma. **La fase 1 de la ingesta no ejecuta ningún
SQL mientras lista el catálogo** —el bucle sobre `list_catalog()` corre dentro de la transacción ya
abierta—, así que nuestras pasadas legítimas están `idle in transaction` durante todo el listado:
36 s medidos en Zara (3957 entradas, 05/08/2026) y minutos en las tiendas lentas. Elegido corto,
ese timeout mataría pasadas buenas; el valor tendría que salir de medir el listado más largo, no de
la intuición.

### Dos migradores comparten `schema_migrations` a propósito, y por eso comparten un lock (#298)

Que cualquiera de los dos servicios pueda arrancar el esquema es el contrato, no un accidente: el
scraper migra con `--migrate` en cada pasada y el web lo hace en un initContainer en cada rollout.
La consecuencia es que **hay dos migradores corriendo solos en el cluster**, en prod los nueve
CronJobs en la banda 21:00 → 00:45 UTC más el web en cada despliegue.

Los dos tenían el mismo fallo de forma: leen el conjunto de aplicadas **antes** del bucle, así que
dos que arranquen a la vez ven la misma lista de pendientes e intentan aplicar el mismo fichero. No
es corrupción —`version` es `TEXT PRIMARY KEY` y cada fichero va en transacción con su `INSERT`, así
que el perdedor revierte limpio— pero **el perdedor falla**, y el fallo aparece lejos de la causa:
si es el initContainer, el pod no arranca y la promoción parece rota; si es un CronJob, esa tienda
se queda sin pasada. Reproducido el 13/08/2026 con el código previo: de dos migradores concurrentes
sobre una pendiente, **uno muere**, y ni siquiera por la clave primaria sino con un `UniqueViolation`
sobre `pg_type_typname_nsp_index` — el `CREATE TABLE` de la propia migración. La ventana solo existe
en el primer arranque tras un release **con migraciones nuevas**, que es exactamente lo que trae
cada versión.

Se cierra con un `pg_advisory_lock` tomado **antes de leer las aplicadas**, no alrededor del bucle:
envolver solo la escritura deja intacta la decisión, que es donde está el fallo. Cuatro cosas que no
son obvias y que costó medir:

- **El identificador vive por duplicado y nada del lenguaje lo ata.** `LOCK_MIGRACIONES = 1685351783`
  (`0x64746d67`, ASCII `dtmg`) está literal en `migrate.py` y en `migrate.ts`. Si alguien cambia uno,
  cada migrador se serializa consigo mismo, los dos servicios funcionan, los tests de concurrencia de
  cada lado pasan — y la carrera sigue abierta sin que nada lo diga. Lo único que lo sostiene es un
  test de paridad que lee los dos ficheros, y está deliberadamente **fuera** del gate de
  `TEST_DATABASE_URL`: es la comprobación que no puede depender del gate que más se salta.
- **Los advisory locks son por BASE DE DATOS, no por cluster** (verificado: `pg_locks.database` trae
  el OID y la misma clave se toma sin esperar desde otra base de la misma instancia). Es lo que hace
  que esto sea seguro en la CNPG compartida: `deal_tracker`, `deal_tracker_qa` y `deal_tracker_prod`
  no se estorban entre sí, y los cuatro proyectos ajenos que viven ahí ni se enteran.
- **La espera es otra distinta de la de #169, y tiene que serlo.** `SCRAPER_MIGRATION_LOCK_WAIT` /
  `WEB_MIGRATION_LOCK_WAIT`, 300 s por defecto contra los 30 s de `SCRAPER_LOCK_TIMEOUT`. Allí una
  espera larga ya es una huérfana; aquí quien retiene está aplicando el esquema legítimamente, y hay
  migraciones que obligan a un `REINDEX` (0014, 0029) que no cabe en 30 s — apretarlo haría fallar
  initContainers por una migración lenta y normal. Se pone con `set_config(..., is_local => true)`
  —o sea `SET LOCAL`— dentro de una transacción que se cierra en el acto: el valor revierte solo en
  el `commit` y el lock, que es de **sesión**, sobrevive. Un `SET` a secas dejaría la pasada entera
  corriendo con 300 s de espera por fila y devolvería el fallo mudo de #169 sin ruido.
- **Quien retiene el lock puede ser invisible para el diagnóstico de #169.** Un advisory lock es de
  sesión, así que su dueño está `idle` y sin transacción abierta buena parte del tiempo — y
  `transacciones_abiertas()` filtra por `xact_start IS NOT NULL`. Medido contra un retenedor `idle`:
  aquella consulta devuelve `[]` y habría escrito «reintentar debería bastar», lo contrario de la
  verdad. De ahí `db.retenedores_del_lock()`, que va por `pg_locks`, y un mensaje propio en `run.py`
  que nombra la variable correcta.

Y una trampa de la librería, porque volverá a morder a quien toque `sql.reserve()`: en `postgres`
3.4.9 **el objeto reservado NO tiene `.begin()` en tiempo de ejecución** —`begin` solo se le asigna
al pool—, aunque los tipos declaren `ReservedSql extends Sql` con ese método. Compila y revienta en
producción. La transacción por fichero va con `begin`/`commit`/`rollback` explícitos por `unsafe`.
Reservar la conexión no es opcional: un lock de sesión vive en SU backend, y hasta ahora que el
migrador del web usara una sola era un accidente del `max: 1`.

**Lo que el lock NO arregla**, y hay que saberlo antes de necesitarlo: `CREATE INDEX CONCURRENTLY`
no puede ir dentro de una transacción, así que una migración que lo use se sale de la red que hace
que el perdedor revierta limpio. Hoy ninguna lo usa; el día que haga falta, esto se replantea.

### Una ficha que no se puede leer se convierte en una baja falsa dos pasadas después

Es el contrato menos evidente entre `stores/*` e `ingest.py`, y no está escrito en ninguna firma.
`ingest.py` refresca `last_seen_at` por **dos** caminos y solo dos: `_touch_seen()` para lo que llega
con la huella intacta, y `_upsert_product()` para lo que `fetch_details()` **emite**. Un producto que
sale en el listado, entra en `to_fetch` y cuya ficha no llega no pasa por ninguno de los dos — así
que `_advance_missing()` le sube `missing_streak` exactamente igual que si hubiera desaparecido de
la tienda, y con `SCRAPER_DELIST_MIN_MISSES=2` lo descataloga a la segunda.

Las redes de seguridad no lo ven, y ese es el punto: el acotado por ámbito y
`SCRAPER_SCAN_MAX_DEAD_RATIO` miran el **listado**, que está lleno y sano. La avería está en la fase
de detalle, que no tiene informe propio.

Dónde muerde y dónde no: las tiendas cuyo listado ya trae el detalle (Cacles, C&A, H&M) sirven
`fetch_details()` desde caché y no pueden fallar aquí. Las que piden una ficha por producto —hoy
Hipercor y Mango— sí, y con dos perfiles de riesgo distintos:

- **Hipercor** falla ficha a ficha (un 403 de Akamai, un timeout de navegación).
- **Mango** falla en bloque: `parse_ficha()` depende de la plantilla RSC de Next.js, así que un
  cambio de la tienda rompe **todas** las fichas de golpe. Y las que caen son justo las de los
  productos cuya huella cambió, o sea **los que acaban de rebajar**.

De ahí la regla, que vale para toda tienda futura con detalle por producto: **solo `GONE_STATUS`
significa retirado**; un 403, un status inesperado, un fallo de transporte o un payload ilegible son
«no he podido verlo», y por encima de `_MAX_FICHAS_FALLIDAS` **seguidas** la pasada aborta entera
(`DetailUnavailable`) en vez de guardar un catálogo mutilado que parece sano. Una ficha leída —
incluido un 404 honesto— reinicia la cuenta, porque lo que el tope vigila es si la tienda nos sigue
dejando entrar, no cuántos productos han muerto.

Lo encontró `revisor-robustez-scraper` al auditar Mango (#80) y se confirmó leyendo
`_advance_missing()` antes de tocar nada. Hipercor ya lo tenía resuelto y documentado en su propio
fichero; el coste de que no estuviera aquí fue que la tienda siguiente nació sin ello.

### La red de bajas comparaba dos vocabularios, y por eso reclasificar parecía una avería (#174)

La red por umbral cruza dos números que hasta #174 no hablaban el mismo idioma: la población previa
por ámbito sale de las columnas `gender, section, category` **guardadas en `product`** —el
vocabulario que escribió la pasada anterior— y lo observado, del ámbito que el código **de ahora**
le asigna a cada entrada del listado. Consecuencia: cualquier cambio en cómo se clasifica hace que
los productos «se muden», el ámbito de origen parece desplomarse aunque no falte ni uno, se marca
sospechoso y **se omiten sus bajas** — o sea que lo retirado de verdad sigue visible en el catálogo
justo mientras dura el falso positivo.

Medido en Hipercor `niña/zapateria/zapatos` en `dev`: el arreglo unisex de #98 cayó entre pasadas y
las runs #45 y #46 avisaron de caída sospechosa con **21 productos vivos y ninguno perdido**. Avisó
dos veces porque la #45 solo redetalló 187 variantes —el detalle es condicional por huella—, así
que la mayoría de los géneros guardados seguían siendo los viejos al arrancar la #46.

**El arreglo es contar lo mudado como visto, y lo que lo hace barato es que nadie tiene que
reclasificar nada.** La entrada del listado ya trae la clasificación de ahora y `_load_existing()`
ya lee la fila de `product`: cruzarlas por `retailer_product_id` dice exactamente quién se ha mudado
y desde dónde (`_moved_out_counts()`). Descartada por eso la alternativa que parecía obligatoria
—aplicar al `prior_active` la misma clasificación que usa el listado—, que sí habría sido cara
porque la clasificación vive en la tienda, no en `ingest.py`.

Y descartada también, con un motivo que vale para futuras redes de seguridad, la variante barata de
**comparar totales de tienda**: si el total no cae, tratar la caída por ámbito como mudanza. Cubre
este caso pero afloja la red donde importa — una hoja pequeña que se vacía de verdad mientras otra
crece por temporada deja de estar protegida, porque el total aguanta. Contar la mudanza **por
producto** no tiene ese agujero: si un ámbito se vacía de verdad, sus productos no aparecen en
ningún otro y `moved_out` es cero. En una mudanza parcial, los que faltan de verdad siguen su camino
normal por histéresis y sondeo.

Dos detalles que no son evidentes y que sostienen la cuenta:

- **Solo cuentan los que estaban activos.** `prior_active` no incluye a los dados de baja, así que
  sumar uno de esos descontaría de una caída que sí es real — y el caso pasa: un producto se
  descataloga, la tienda lo repesca y de paso lo publica en otra rama.
- **Se cuenta en el ámbito de ORIGEN**, que es el que hay que rescatar de la sospecha; el de destino
  ya los ve llegar.

**El rescate se publica** (`ambitos remapeados` en `scrape_run.message`, `remapped_scopes` en el
resumen) aunque no sea un error y no sume en `errors`, por el mismo motivo que `generos conservados`
de #172: si fuera mudo, una regresión que dejara de detectar la mudanza se leería **exactamente
igual** que el arreglo funcionando. Es la única señal de que una pasada ha visto un cambio de
clasificación.

Verificado contra tienda real en local (Cacles, 426 productos) moviendo la clasificación guardada
entre dos pasadas: con el código anterior `errors = 1` y «ambitos con caida sospechosa:
niña/zapateria/botas»; con el arreglo, `errors = 0`, «ambitos remapeados» y bajas aplicadas. Cacles
sirve para esto porque su listado ya trae el detalle: una pasada real entera son 2-3 peticiones y 4
segundos, frente a los ~25 min de Chromium que costaría reproducirlo en Hipercor.

### El género `unisex` es la norma del barefoot, no una excepción

El brief pide separar **niño/niña**, y durante tres tiendas eso se implementó como igualdad exacta
(`p.gender = 'niño'`) porque ninguna emitía otra cosa. Con la primera tienda barefoot nativa deja de
valer: el calzado respetuoso infantil **se diseña unisex**, y Cacles publica así 342 de sus 428
referencias, con **ninguna** marcada solo de niño. Con igualdad estricta, un producto `unisex` no
salía en *ninguno* de los dos filtros: filtrar por «Niño» devolvía cero productos de la tienda que
entró justo para llenar la zapatería.

La regla —`gender = X OR gender = 'unisex'`— vive en **`services/web/src/catalog/gender.sql.ts`**, y
que esté en un módulo aparte es la decisión, no un detalle de organización: la comparten el listado
del catálogo y el JOIN del job de matching. Si el catálogo enseñara un zapato unisex bajo «Niño» y
el aviso configurado para niño no disparase con él, el usuario vería una promesa incumplida sin
poder explicársela. Mismo trato y misma razón que `matching/deal-rule.sql.ts`.

`unisex` **no se ofrece como chip** en la faceta: ya está incluido en Niño y en Niña, así que no
filtraría nada nuevo y sugeriría tres estanterías donde el brief pide dos.

**Y no es solo del barefoot: una tienda con hojas por género suele publicar producto en las dos.**
Medido en las cuatro que tienen hojas por género:

| tienda | cruzan niña y niño | % del catálogo |
|---|---:|---:|
| hipercor | 161 de 1222 | **13,2 %** |
| hm | 317 de 3401 | **9,3 %** |
| lefties | 14 de 698 | **2,0 %** |
| sfera | **0 de 548** | **0 %** |

El fenómeno es frecuente; **la magnitud no se puede extrapolar, y ni siquiera su existencia**. Con
las dos primeras parecía que rondaba el 10 % siempre; Lefties lo desmintió por un orden de magnitud
y Sfera lo desmiente del todo — con 35 hojas por género, no cruza ni uno. O sea que en una tienda
nueva hay que **medirlo, no estimarlo**: cuesta un recorrido del listado sin pedir una sola ficha
(33 s en Lefties, 111 s en Sfera). Con el dedup habitual de «gana la primera», ese producto se queda
con el género de la hoja declarada antes y **desaparece de la otra sección** — en Hipercor, un padre
que filtre «Niño» en zapatería veía 47 productos donde la tienda publica 140 (#98).

Detectar el cruce solo se puede hacer con **el listado entero delante**, porque `list_catalog()`
emitía según recorría y el ámbito de una entrada ya emitida no se corrige. El coste de acumular está
medido y no es el obstáculo que parecía: **91 MiB de pico** con la pasada entera de H&M en memoria
(3397 productos, 43362 variantes), y ninguno en las demás — `ingest.py` ya hacía
`list(store.list_catalog())`, así que el listado completo estaba en memoria de todas formas.

**La regla vive en `stores/base.py` y no en una tienda concreta**, desde que la tercera la necesitó
(#98). Son tres piezas, y una tienda con hojas por género que mida cruce distinto de cero necesita
las tres:

- **`ambito_cruzado(hojas)`** — géneros distintos → `unisex`; sección y categoría las fija la
  primera hoja, porque cruzar géneros tiene vocabulario y cruzar categorías no. Una hoja ya
  declarada `unisex` (las de bebé) **no cuenta como género propio**: se descarta antes de mirar el
  cruce, que es lo que permite que Hipercor ponga sus hojas con género por delante de la de bebé y
  lo declarado se quede con su género.
- **`con_unisex(scopes)`** en `scopes()` — declarar los ámbitos `unisex` aunque ninguna hoja los
  declare, porque un ámbito no declarado no cuenta como escaneado y sus productos **no se
  descatalogan nunca**. Mismo motivo por el que Cacles declara el producto cartesiano de lo que su
  parser *puede* emitir.
- **`ScanReport.leaf_gone(scope, leaf, tambien_unisex=True)`** — al marcar una hoja de género como
  caída, sacar de las bajas también el ámbito `unisex` equivalente: si cae una de las dos ramas, el
  producto cruzado se emitiría con el género de la superviviente y su ámbito `unisex` parecería
  vaciado. Cuenta **una** hoja caída y no dos, o `SCRAPER_SCAN_MAX_DEAD_RATIO` saltaría antes de
  tiempo — y por lo mismo el ámbito extra tampoco añade un nombre a `failed_leaves`.
- **`ScanReport.cross_gender_suspect` + `ingest._gender_a_escribir()`** — la misma hoja caída tiene
  una **segunda** consecuencia, en un ámbito distinto del suyo, y sacar el `unisex` de las bajas no
  la cubría (#172): la rama que **sí** se lista emite con su género productos que son `unisex`, y
  `_upsert_product` los guardaba así. `leaf_gone` anota la rama contraria y la ingesta conserva ahí
  el `unisex` ya guardado en vez de escribir el del listado. La protección es estrecha a propósito:
  solo un `unisex` guardado, y solo en esos ámbitos — un `niño`↔`niña` legítimo se escribe como
  siempre, y un producto nuevo se guarda con lo único que se sabe de él. Medido contra el catálogo
  real de Hipercor con `zapatos-infantiles/nino` rota: el listado se desplaza a 564/416/193 desde
  470/462/285, y la base se queda quieta (91 géneros conservados, que la pasada escribe en
  `scrape_run.message`).

Las dos consecuencias son distintas y conviene no confundirlas al leer un aviso: `failed_scopes`
son los ámbitos que **no se han podido mirar**, y `cross_gender_suspect` los que se han listado
perfectamente y justo por eso mienten. Que sea un campo propio y no una deducción sobre
`failed_scopes` es deliberado: ahí dentro también entra una hoja genuinamente `unisex` que se cae
—Mango tuvo una, #176— y esa no deja ninguna rama superviviente que pueda mentir.

Aplicado en H&M (#102), Hipercor y Lefties (#98); Sfera no lo necesita mientras siga en cero. Un
detalle de operación que conviene saber al arreglarlo en una tienda ya ingerida: el género de las
filas existentes **no se reescribe** hasta que se les vuelva a pedir la ficha. Un producto cuya
huella de listado no cambia pasa por `_touch_seen()`, que solo refresca `last_seen_at`, así que la
corrección llega por el **refresco forzado** (`SCRAPER_DETAIL_MAX_AGE_DAYS` / `_REFRESH_MAX`) y
tarda varias pasadas en barrer el catálogo.

**Y por eso existe `--refresh-all` (#143).** El umbral del refresco es un entero en días y el `0`
no significa «refresca todo» sino que lo **desactiva**, así que reparar un entorno recién ingerido
era imposible: en `dev` el Job de cura de Lefties refrescó **0 productos** porque al `last_detail_at`
más viejo le faltaban 2 h 33 min para cumplir el día, y la única salida era esperar al reloj. La
bandera (o `SCRAPER_DETAIL_REFRESH_ALL=1`) pide el detalle de todo lo sin cambios sin mirar la edad.
Salta el umbral de **edad**, no el de **presupuesto**: `SCRAPER_DETAIL_REFRESH_MAX` se sigue
aplicando, y no es un detalle burocrático — sin ese tope, esto contra Hipercor son 1 224 fichas a
~10 s cada una, o sea las tres horas y media de una pasada en frío disparadas sin querer desde un
Job de cura. La regla general que deja: **cualquier arreglo que cambie la forma de lo ingerido
necesita, además del código, una pasada de reobservación que lo propague** — el código nuevo solo
gobierna lo que se vuelva a mirar.

**Contar `unisex` en la base no mide el cruce, y confundir las dos cosas costó #139.** El `unisex`
almacenado tiene dos orígenes que la columna no distingue: el cruce, y las **hojas ya declaradas
`unisex`** en `CATEGORIES` (bebé en Hipercor, newborn en H&M y Mango). Lefties no declara ninguna,
así que su cifra sí es exactamente el cruce — y por eso su 0 en `dev` parecía una avería al lado del
10,9 % de H&M, que es casi todo rama de recién nacido. No lo era: la única pasada de Lefties en `dev`
(`lefties-frio-1/2`, 02/08 21:09 UTC, `sha-99b3642`) es **anterior** al commit que introdujo la regla
en esa tienda (`c4c6a58`, 03/08 11:33), o sea que el código nunca había corrido allí. Contra el
catálogo vivo del 03/08 el cruce está intacto: 700 productos, 14 `unisex` (2,0 %).

De ahí salen dos cosas que valen más allá de esta tienda:

- **Una tienda cuya última pasada es anterior al arreglo que la corrige produce datos que se leen
  como un bug**, y es el mismo patrón que ya documentan #99, #93 y #81 por otro camino. Antes de
  diagnosticar desde la base, mirar sobre qué imagen corrió la última pasada del entorno.
- La pasada publica desde #139 **`gender_counts`** (reparto del listado, también en `--dry-run`, que
  lo mide sin escribir ni pedir detalle) y **`gender_stale`** — productos cuyo género guardado no es
  el que dice el listado y que esa pasada no reescribe. Es la señal que faltaba: convierte el párrafo
  de arriba, que era una nota que había que recordar, en una línea del resumen de cada pasada.

### Una variante no es siempre una cosa comprable, y la URL es lo que distingue los dos casos

El modelo asume que `(producto, talla, color)` identifica una prenda comprable, y `variant` la
representa con el id que da la tienda. **En tres de las siete tiendas eso es falso**, y con dos
causas distintas que en la base se ven idénticas. Medido el 03/08/2026 sobre `dev`, contando grupos
`(producto, talla canónica, color canónico)` con más de una variante viva:

| tienda | grupos | misma URL de variante | URLs distintas |
|---|---:|---:|---:|
| lefties | 815 | **815** | 0 |
| hipercor | 108 | **108** | 0 |
| hm | 854 | 51 | **803** (105 productos) |
| zara, sfera, cacles, c-and-a | 0 | — | — |

**Cuando las dos caras comparten URL son la misma prenda con dos SKU.** Es lo de Lefties e
Hipercor: dos referencias de la tienda bajo un mismo `productParentId` —probablemente una
reposición—, comprobado contra el JSON crudo, así que es dato de la tienda y no artefacto del
parseo. El identificador **no** es el problema: los SKU son distintos y estables (segunda pasada de
Lefties con `missing_streak = 0` en las 9165 variantes).

**Cuando las URLs difieren son dos artículos distintos que nuestro `product` junta.** Es lo de H&M,
donde una fila del listado es producto+color y el `retailer_variant_id` es `{articleId}-{sizeId}`
con el color ya dentro del `articleId`: dentro de un artículo no puede haber duplicados. Lo que hay
son dos fichas de la tienda con el mismo nombre de color (`1315153003` y `1315153005`, las dos
«Azul marino»). Colapsarlas escondería un destino real, así que se dejan.

Eso obligó a admitir (#123, `0023`) que **dentro de un producto el nombre del color no siempre
identifica al color**, que es el supuesto sobre el que la `0011` clavó `product_image` por el texto
del color. Con ese supuesto roto, la ficha pedía las fotos por color y le llegaban las de los dos
artículos: 126 galerías así en una pasada completa de H&M, la peor con **17 fotos de cinco
vaqueros distintos** bajo un solo chip «Azul denim» — y con el segundo artículo inalcanzable, chip
y enlace incluidos. Es exactamente la coherencia foto↔precio que #26 existía para garantizar.

El discriminador no es un color mejor: es **la ficha de la tienda**, o sea la URL, el mismo
criterio de #108. Tres decisiones que conviene no volver a discutir:

- **`product_image.variant_url`, no cambiar la identidad del producto.** Hacer que un artículo de
  H&M fuese un producto nuestro es más honesto con la tienda, pero cambia el `retailer_product_id`
  de los 3.393 productos ya ingeridos —con `price_history` e intereses detrás—, deja a H&M sin
  selector de color y toca el agrupado por raíz del que depende la detección de `unisex`.
- **La columna NO entra en el `UNIQUE (product_id, color, position)`,** y `position` se sigue
  numerando por color. Es lo que mantiene una sola fila por `(producto, color, position = 0)`, que
  es lo que la consulta de la tarjeta da por hecho; quien necesita separar las dos referencias es
  la ficha, y le basta como atributo de filtrado.
- **Nada de sufijos en `variant.color`.** Era la vía sin migración y es la cara: ensucia
  `color_canon`, la faceta de color del catálogo y `interest.color`, que se canonicaliza al dar de
  alta y **no se recalcula**.

Se puebla como la `0011`: sin backfill, según el detalle condicional y el refresco forzado vuelvan
a pedir cada producto. Por eso la ficha lleva una cadena de respaldo cuyo segundo escalón —fotos de
ese color con `variant_url IS NULL`— es el comportamiento de siempre, y es el que ven las otras
seis tiendas y H&M hasta que le toque refresco. Aplicar la migración sin pasar el scraper detrás no
cambia nada de lo que se ve.

De ahí la clave con la que el web agrupa desde #108: **`(producto, talla canónica, color canónico,
URL de la variante)`**. No es una lista blanca por tienda —que envejecería mal— y añadir la URL
solo puede **partir** grupos, nunca unirlos, así que las cuatro tiendas limpias no pagan nada.

Se resuelve **aguas abajo, nunca en la ingesta**: colapsar al parsear descartaría un SKU que el
retailer considera real y obligaría a elegir cuál sobrevive; una elección que bailara entre pasadas
generaría bajas falsas. En el aviso, `collapseSameGarment()` colapsa las ofertas **ya evaluadas**
—una cara que no supera el umbral no debe silenciar a la otra— quedándose con el precio menor y, a
igualdad, el `variantId` menor: ese desempate es lo que hace el colapso determinista entre pasadas
y deja que el `UNIQUE (interest_id, variant_id, price_event_key)` de la `0005` siga protegiendo si
se rebobina la marca de agua. En la ficha, una fila por prenda con `BOOL_OR` del stock, porque las
dos caras discrepan en stock en **387 de 815 grupos** de Lefties y en todo `dev` hay **408 grupos
cuya talla solo se ve comprable haciendo ese OR**. `price_history` se deja con sus dos filas por
talla real: no rompe nada —mínimo histórico y honestidad se calculan por variante— y arreglarlo
exigiría tocar la ingesta.

Lo que conviene saber al **añadir tienda**: la comprobación es una consulta
(`count(*) - count(distinct (p.id, v.size, v.color))`, y la URL para saber cuál de los dos casos
es) y **no la hace ningún test con fixtures**, porque el patrón solo aparece con el catálogo entero
delante.

### El listado del catálogo paga la agregación del catálogo ENTERO en cada petición (#307, #314)

`listProducts()` no puede recortar la página antes de agregar. Su `ORDER BY` —los cuatro órdenes que
admite— usa `price_from`, `is_real_deal`, `honest_discount` y `max_discount`, que son valores **por
producto** calculados a partir de todas sus variantes; y `is_real_deal` se decide sobre las columnas
`*_repr`, que solo existen tras el `GROUP BY`. Así que cada petición del catálogo agrega las ~159.000
filas variante×precio vivas para devolver 12. Ese es el suelo, y **crece con el catálogo**: medido en
prod el 11/08/2026, ~1,2 s con el `Sort` por `product_id` derramando 8,7 MB a disco
(`external merge`) más el `GroupAggregate` de los `array_agg(... ORDER BY in_stock DESC, price ASC)`
que eligen la variante representativa. Bajar de ahí no es un índice: pide un agregado por producto
materializado, que es #314.

**Resuelto el 13/08/2026 con la migración `0035` (#314), y de camino se corrigió la cifra de
arriba.** Ese «~1,2 s» se midió cuando prod tenía 16.010 productos; el mismo `EXPLAIN` el 13/08 daba
**~1,9 s**. O sea que el suelo no solo crece con el catálogo: crecía más rápido de lo que la propia
issue creía, y el hueco hasta el criterio de hecho («por debajo de 1 s») era de ~900 ms, no de 250.

Antes del agregado se descartaron las dos salidas baratas, las dos con medida:

- **`work_mem` no llega, y el derrame nunca fue el problema.** Es `context=user`, así que se puede
  subir **solo para las conexiones del web** (`postgres(url, { connection: { work_mem: … } })`) sin
  tocar el servidor que comparten otros cuatro proyectos ni pasar por el tercer repo — el bloqueo
  que #314 daba por hecho no existía. Pero quitar el derrame entero (`external merge` →
  `quicksort`, 18,4 MB) solo baja de 1.890 a 1.513 ms en prod: **~380 ms, un 20 %**. El coste es CPU
  de ordenar y agregar 163.509 filas; el disco era la parte de esa CPU que además pasaba por disco.
- **El índice que la issue proponía crear ya existía** (`ix_variant_product`, desde la 0008). El
  planificador lo ignora porque el `Sort` no es sobre `variant` sino sobre el resultado del join
  variante ⋈ `latest` ⋈ `stats`, y **ningún índice de tabla pre-ordena el resultado de un join**.

La forma elegida sigue el precedente de la `0031`: **lo escribe Postgres, no `ingest.py`**. Una tabla
`product_agg` y una función `refresh_product_agg(retailer_id)`, las dos declaradas en la migración;
el scraper solo añade `SELECT refresh_product_agg(%s)` al final de la pasada, dentro de su
transacción ya atómica. La 0031 pudo usar una columna `GENERATED`, que aquí no vale porque el
agregado cruza filas — pero el argumento de fondo es el mismo y aquí pesa más: si lo escribiera el
scraper, tendría que aprenderse la **ventana de honestidad de 90 días**, o sea un tercer espejo de la
regla que #228 pelea por no duplicar. Por eso `product_agg` guarda los **estadísticos** y no el
veredicto: `is_real_deal` lo sigue calculando `deal-rule.sql.ts`, ahora sobre ~16.000 filas ya
agregadas en vez de sobre 163.509 sin agregar.

**El corte que hace correcto precomputarlo** es que solo tres filtros del catálogo son *de variante*
—`size`, `color`, `inStock`—; el resto son *de producto* y se aplican igual de bien sobre el agregado
ya hecho. `listProducts()` lee la tabla cuando los tres son nulos y cae al camino de siempre cuando
alguno está puesto. Y no se queda rancio con el reloj porque `recent_min` se mide contra
`l.scraped_at` —la última observación de la propia variante— y no contra `now()`: solo cambia cuando
cambia `price_history`, que tiene **un único escritor**.

| | QA | prod | dev (HTTP, desplegado) |
|---|---:|---:|---:|
| antes | 2.205 / 2.170 / 2.302 ms | 1.912 / 1.811 / 1.888 ms | — |
| solo `work_mem = 32MB` | 1.814 / 1.735 / 1.797 ms | 1.513 / 1.500 / 1.526 ms | — |
| **con `product_agg`** | **81 / 69 / 74 ms** | — | **85-92 ms** (14.733 productos) |

Lo que cuesta al escribir, medido contra los datos reales de dev: **1,2 s el refresco de Zara**
(2.778 productos / 31.359 variantes), 94 ms el de Sfera, 3,5 s el completo que solo hace el relleno
de la migración. Sobre una pasada de Zara en estado estable (~1m35s) es un **1,3 %**.

Dos consecuencias que no son evidentes y conviene tener escritas:

- **El agregado es una caché, y una caché desactualizada devuelve menos filas SIN dar síntoma.** En
  producción el estado no es alcanzable (la migración rellena al desplegar, cada pasada refresca al
  final, y solo `ingest.py` escribe `price_history`), pero **un test que siembre catálogo a mano sí
  lo alcanza**: 43 specs se pusieron en rojo hasta añadirles el refresco. De ahí
  `refrescarAgregado()` en los helpers. El día que aparezca un segundo escritor de `price_history`,
  tendrá que llamar al refresco.
- **`inStock` es un mal colapsador**, al revés que talla y color. El diseño se apoya en que un
  filtro de variante reduce el conjunto antes de agregarlo, y eso vale para talla y color; pero
  `inStock=true` casa con casi todas las variantes, así que ese camino paga la agregación casi
  entera (~1,1 s en dev con 14.733 productos, remedido en **~2,1 s** en QA el 14/08). No es una
  regresión —antes costaba lo mismo— y **no se puede resolver con este agregado**, porque filtrar
  por stock cambia cuál es la variante representativa y con ella `price_from` y la honestidad.

  **Resuelto el 14/08/2026 con la `0038` (#371), y la forma es la consecuencia directa de esa
  última frase**: como no se puede con *este* agregado, se hace con **dos**. `product_agg` gana un
  eje `scope` (`'todas'` | `'con_stock'`) y PK compuesta, y `refresh_product_agg` emite los dos
  ámbitos desde el **mismo** `GROUP BY` sobre `matched` —un `CROSS JOIN` con los dos valores—, que
  es lo que impide que uno derive del otro. Una tabla y no dos (`product_agg_in_stock`) porque el
  coste de una segunda no es el almacenamiento —33.688 filas donde había 16.844— sino **el
  espejo**: dos definiciones en `schema.ts`, dos ramas en la función y dos juegos de paridad.

  Antes se midió si el filtro merecía conservarse, porque la salida barata era reinterpretarlo como
  filtro de **producto** con el `any_in_stock` que ya existía (casi gratis: se queda en el camino
  precomputado). Los dos números no se parecen, y por eso no se hizo: **45.667 de 167.377 variantes
  vivas están agotadas (27,28 %)**, o sea que a nivel de variante el interruptor filtra de verdad;
  pero solo **294 de 16.844 productos** no tienen ninguna variante con stock, así que a nivel de
  producto habría dejado de filtrar nada. Cambiar la semántica habría sido barato y habría
  convertido el interruptor en un adorno.

  Dos detalles que no son evidentes. **El filtro va sobre `matched`, no dentro de `latest`**:
  `latest` es la última lectura de cada variante, y filtrar dentro haría que una variante agotada
  hoy cayera a una lectura anterior con stock y el agregado enseñara un precio que ya no existe. Y
  **`inStock=false` se queda en el camino vivo** a propósito: la SPA no lo pide
  (`CatalogPage` manda `inStock: filters.inStock || undefined`) y un tercer ámbito costaría otro
  tercio de refresco por un caso que nadie hace.

  El riesgo que introduce la forma, y que conviene tener escrito: **una lectura que olvide el
  predicado de `scope` duplica filas en silencio**. Lo sujeta que hay un solo lector
  (`agregadoPrecomputado`) y que la paridad lo cubre. Y cambió un contrato que sí tenía consumidor
  fuera del web: `refresh_product_agg` devuelve ahora filas producto×ámbito, así que el número casi
  se dobla — tres asserts de `test_ingest.py` pasaron de 2 a 4.

  **Lo que costó de verdad**, medido en dev el 14/08/2026 con la `0038` ya desplegada, HTTP, ×3, y
  el mismo catálogo de **14.733 productos** con el que se tomó la medida de arriba:

  | | antes | después |
  |---|---:|---:|
  | `inStock=true` | 1.119 / 1.139 / 1.473 ms | **102 / 86 / 83 ms** |
  | portada (`ofertas` + `onlyDeals` + `inStock`) | — | 51 / 48 / 46 ms |
  | control sin filtros | 127 / 100 / 111 ms | 102 / 99 / 94 ms |

  El control da lo mismo antes y después, que es lo que descarta que la mejora sea un hueco de
  ruido. `inStock` deja de ser el techo del panel y se pone al nivel del caso sin filtros.

  Y el precio declarado de la decisión: **`inStock=false` cuesta 699-747 ms**, porque se quedó en el
  camino vivo a propósito. Sigue por debajo del umbral P1 de `/validar-qa` y la SPA no lo ofrece,
  así que no justifica un tercer ámbito — pero el día que alguien lo ponga en la interfaz, ese
  número es el que hay que volver a mirar.

### `array_agg(... ORDER BY …)[1]` elige a suertes si el orden no desempata (#314)

El patrón con el que el listado elige la «variante representativa» —la que pone precio, color y foto
en la tarjeta— era `(array_agg(x ORDER BY in_stock DESC, price ASC))[1]`. Ese orden **no es total**:
un producto con varias variantes al mismo precio y mismo estado de stock deja el `[1]` a merced de lo
que el ejecutor entregue primero. O sea que **la tarjeta podía enseñar un color y una foto distintos
entre dos peticiones idénticas**, sin que nada cambiara en la base y sin que ningún test lo viera.

Llevaba ahí desde que existe el listado y no lo destapó una revisión, sino **contrastar dos
implementaciones del mismo agregado** sobre datos reales: `EXCEPT ALL` entre el camino vivo y el
precomputado sobre los 16.517 productos de QA. Coincidían todos los agregados deterministas
(`price_from`, `list_from`, `discount_from`, `price_repr`, `any_in_stock`, `variant_count`) y
discrepaban **2.393 `color_repr`, 316 `recent_min`, 312 `max_observed` y 12 `is_real_deal`** — todos
empates. Con `variant_id` como último criterio en los dos lados, 0 diferencias en ambos sentidos.

La lección que se lleva a cualquier otro sitio del esquema: **un `ORDER BY` dentro de un agregado
necesita un desempate único, o el resultado no es una función de los datos**. Y la de método, más
cara: un seed pequeño **no** puede cazar esto. Quitando el desempate del servicio, los 19 casos de
paridad del spec seguían en verde, porque con pocas filas Postgres elige el mismo plan en los dos
caminos y coincide por casualidad. Lo que sí lo caza es un test sobre la **forma** del SQL (que los
dos lados sigan llevando el desempate), y por eso `catalog-agregado-paridad.spec.ts` lleva uno
además de la comparación por datos.

### Para volcar la consulta del servicio no hace falta `log_statement` (#314)

El apartado anterior dice —y sigue siendo cierto— que hay que medir **la consulta que ejecuta el
servicio**, no una escrita a mano. La vía que describía era `log_statement` en una Postgres
desechable. Hay una más barata y sin base de datos: `new CatalogService(fakeDb)` con un `db` de pega
cuyo `execute(q)` captura la plantilla, y `new PgDialect().sqlToQuery(captured)`, que devuelve el
texto con `$1..$n` y sus parámetros. Un spec de usar y tirar en `services/web/test/` lo escribe a un
fichero; vitest ya está montado con SWC, así que no hace falta runner de TS.

Tres detalles sin los cuales el volcado no sirve: los parámetros hay que **meterlos en línea** (con
`$1` sueltos el planificador puede elegir un *generic plan* que no es el que sufre el usuario);
hay que **quitar los comentarios `--` antes** de plegar a una línea, o el primero comenta el resto y
Postgres responde `syntax error at end of input` señalando al final del texto; y conviene
comprobarlo con un `EXPLAIN` sin `ANALYZE`, que parsea y planifica sin ejecutar.

De paso, esto permite algo que evita tocar un entorno para medirlo: **aplicar una migración dentro de
una transacción que termina en `ROLLBACK`**. Así se midió la `0035` contra QA y se contrastó la
paridad sobre sus 16.517 productos sin dejar nada — QA se quedó en la `0029`, comprobado después. Es
una tabla nueva, así que no hay contención con nadie y las lecturas van por MVCC.

**Y una fuente de ruido que descoloca cualquier medida de latencia: las estadísticas rancias.** Tras
la pasada de verificación en dev, `variant` llevaba **58.326 modificaciones sin analizar desde 9 días
antes** y el autovacuum no había llegado. Con esas estadísticas, el catálogo sin filtros daba 190-255
ms y el caso `inStock` 2,2-2,4 s; un `ANALYZE variant` los dejó en 85-92 ms y 1,1-1,5 s. Antes de
atribuir una latencia al código, mirar `pg_stat_user_tables.n_mod_since_analyze`.

Encima de ese suelo se pueden apilar costes que **sí** son evitables, y hay uno resuelto que fija la
convención. `variant_count` —el «N tallas» de la tarjeta, el contador de prendas comprables de #108—
vivía dentro del `GROUP BY` como `COUNT(DISTINCT ROW(size_canon, color_canon, url))`, y para
calcularlo Postgres ordenaba las 159.037 variantes **por un valor calculado**, llamando a
`size_canon()` y `color_canon()` una vez por variante: **24 s** en la vista por defecto, contra ~1,2 s
sin él. Con un filtro puesto no se veía, porque el conjunto colapsa a unos cientos de filas y el sort
sale gratis — de ahí que el catálogo filtrado fuera a 1 s y el catálogo entero no.

La regla que sale de esto: **lo que es puramente de presentación va en el `SELECT` de fuera, después
del `LIMIT`**, para que se evalúe sobre la página y no sobre el catálogo. Vale para un dato que no
aparezca en ningún `ORDER BY` ni en ningún `WHERE` —hay que comprobarlo, no suponerlo— y ya lo usan
`tags` y `variant_count`. Dos trampas medidas al aplicarla:

- **Al salir del conjunto ya filtrado hay que repetir sus filtros, y solo los de nivel variante**
  (`delisted_at`, talla, color y stock). Los de producto no cambian el recuento. `activeOnly` es el
  que engaña: levanta el filtro del **producto**, nunca el de la variante.
- **Correlar contra un CTE materializado es la forma lenta**, y no se ve venir: un CTE no tiene
  índice, así que se recorre entero una vez por fila de la página. Contra el CTE `latest` son
  **603 ms** por página; la misma subconsulta contra las tablas base, apoyada en
  `ix_variant_product` y `ix_price_history_variant_time`, **16 ms**. El precio es expresar «la última
  fila de precio» dos veces en el mismo fichero, y eso se sujeta con un test del filtro `inStock`.

Y una lección de método que costó dos números escritos en dos issues: **para medir, hay que medir la
consulta que ejecuta el servicio, no una escrita a mano que se le parezca**. #307 estimó 0,33 s a
partir de un probe recortado y el arreglo real da ~1,25 s; la diferencia era que el probe también
dejaba fuera los `array_agg`. La consulta de verdad se saca del driver, no del editor: `log_statement`
en la Postgres desechable, una petición, y del log salen el SQL y sus parámetros para pasarlos a
`PREPARE`/`EXECUTE` contra los datos reales. También es lo único que deja comprobar en el plan lo que
de verdad importa al mover algo fuera del `LIMIT`: que la subconsulta corra con **`loops` = tamaño de
la página** y no una vez por producto, porque entre la agregación y el `LIMIT` hay un nodo `Sort`.

**Corrección medida el 12/08/2026: «el catálogo filtrado va a 1 s» ya no es cierto para el color
(#342).** La frase de arriba —que con un filtro puesto el conjunto colapsa y el sort sale gratis— se
escribió con el filtro de color siendo `color_canon`. Desde que #291 lo mudó a **familia**, hay
combinaciones que cuestan **más que el catálogo entero**. Medido contra QA bajo `v0.3.0`, con control
sin filtros antes y después de cada tanda:

| consulta | tiempo | items |
|---|---:|---:|
| sin filtros (control) | 1,85 / 1,80 s | 20 |
| `color=azul` | 1,13 / 1,17 / 1,19 s | 20 |
| `retailer=zara` | 1,36 / 1,32 s | 20 |
| `section=ropa&retailer=zara` | 1,25 / 1,46 s | 20 |
| `gender=niña&retailer=zara` | 2,02 / 1,37 s | 20 |
| **`color=azul&size=1 mes`** | **5,02 / 4,92 / 5,02 s** | 8 |
| **`color=azul&retailer=zara`** | **23,25 / 23,57 / 23,16 s** | 20 |
| **`color=negro&retailer=hm`** | **27,05 / 27,64 s** | 20 |

Tres cosas que acotan el fallo y que conviene no volver a derivar: **es el color** —`section` y
`gender` combinados con la misma tienda se quedan en ~1,3 s—, **escala con el tamaño del catálogo de
la tienda** —`color=azul&retailer=cacles` son 1,55 s— y **la consulta más selectiva es la más lenta**
(8 ítems, 5 s), que es justo lo contrario de lo que predice el modelo de coste constante de #314.

~~La sospecha, sin confirmar, la da la propia tabla del apartado de #329: una familia de color no es
un predicado selectivo.~~ **Desmentida el 12/08/2026 con el `EXPLAIN ANALYZE` que este párrafo
pedía.** No era la selectividad del predicado: era que `color_family()` estaba declarada sin `COST`,
así que el planificador la creía gratis y prefería comprobar el color **fila a fila** sobre los
productos de la tienda antes que entrar por su índice. Con el coste declarado (migración `0030`), la
misma consulta pasa de 23.698 ms a **1.127 ms**, o sea que **«el catálogo filtrado va a 1 s» vuelve a
ser cierto**, también para el color. El detalle, los números por llamada de las tres funciones y lo
que hay que llevarse a la siguiente, en el apartado *«Declarar la forma del predicado no basta si la
función miente sobre lo que cuesta»*.

Lo que sí queda en pie de esa sospecha, porque es un dato y no una hipótesis: tres familias de color
tocan 36.530 filas contra las 4.723 de cinco tallas, y el `Bitmap Index Scan` de 7,13 ms mide **el
predicado**, no el plan completo. Esa segunda frase es la lección de método que sobrevive.

**Y una trampa de medición que casi mete un P0 falso en un informe:** las primeras medidas dieron
**45 s** donde luego había 23. QA corre con **una sola réplica**, y esa tanda se lanzó con los
frentes de datos y API de `/validar-qa` machacando la misma API y la misma base. Cualquier número de
latencia de QA sin un control `sin filtros` antes y después no vale: no distingue una regresión de la
carga del que mide.

**Cerrado sobre lo desplegado el 14/08/2026, validando `v0.4.0`.** Todo lo de arriba se midió por
`EXPLAIN` o contra `v0.3.0`; esto es por HTTP contra el artefacto que va a producción, con el control
estable antes (0,17-0,24 s) y después (0,17-0,32 s), ×3 por combinación:

| consulta | bajo v0.3.0 | bajo v0.4.0 |
|---|---:|---:|
| sin filtros | 1,85 / 1,80 s | **0,18 s** |
| `color=negro&retailer=hm` | 27,05 / 27,64 s | **1,04 s** |
| `color=negro&retailer=zara` | 23,25 / 23,57 s | 1,02 s |
| `inStock=true` | — | 2,1 s |

Dos cosas que solo se ven midiendo así y no con `EXPLAIN`. La primera: los **0,18 s** de extremo a
extremo incluyen HTTP y validación de token, o sea que el suelo real que ve el usuario está **por
debajo** del criterio de hecho de #314, no rozándolo. La segunda: `inStock=true` sale en **2,1 s**,
por encima del ~1-1,5 s que #373 daba por bueno, y se queda como el techo del panel —es el único
filtro que no aprovecha `product_agg` (#371)—, aunque siga bajo el umbral P1 de 3 s del listón.

**Y `product_agg` cuadra después de ingerir, que es lo que no se había comprobado nunca.** El
apartado de la `0035` avisa de que una caché desactualizada devuelve menos filas *sin dar síntoma* y
argumenta que en producción ese estado no es alcanzable. Verificado ya empíricamente: antes de una
pasada real 16.517 = 16.517 productos vivos, y después de una pasada real de zara **16.844 =
16.844**. El argumento deja de ser solo estructural.

### La faceta describe la vista, y cruzarla tiene una frontera de coste declarada (#292, #291)

`getFacets()` es lo que llena el panel de filtros, y hasta la v0.3.0 solo se acotaba por `barefoot`,
`section` y el eje `deportiva`. La consecuencia es que **ofrecía chips que no devuelven nada**: el
panel enseñaba las tallas del catálogo entero aunque ya hubiera una categoría elegida, se pinchaba
una y el catálogo salía vacío. Medido sobre una copia de `dev` (12.870 productos, 127.567 variantes),
en `ropa`: de las **165** tallas ofrecidas, con una categoría puesta solo **82** devuelven algo, y con
género y color quedan **71**. O sea que la mitad larga de los chips era una promesa falsa, y no un
caso de borde.

Desde #292 la faceta recibe los mismos filtros que el listado. Tres reglas, y ninguna es opcional:

- **Cada faceta omite su propio eje.** La lista de tallas se acota por categoría, color, tienda,
  género y búsqueda, pero **no** por la talla ya elegida. Si se acotara, quedaría esa sola talla y no
  habría forma de cambiar de idea sin limpiar el filtro. Es la regla clásica del filtrado por
  facetas y es lo único que lo hace usable.
- **`sections` no la acota nada.** Es el eje de navegación con el que se sale de la vista, y desde
  #292 también lo que eligen las pestañas del grupo de talla; unas pestañas que desaparecen según lo
  filtrado encierran al usuario en la sección en la que está.
- **Los filtros de variante (talla y color) se aplican a la MISMA fila de variante**, no por
  separado. Una prenda cuenta como «azul en 4 años» si tiene una variante que es las dos cosas, no si
  tiene una azul y otra de 4 años. Es la semántica de `matched` en `listProducts`, y cualquier otra
  haría que la faceta prometiera lo que el listado no devuelve.

**La frontera es de coste, y está declarada en `CatalogFilterDto`.** Cruzan solo los ejes que se
resuelven con `product` + `variant`; `inStock`, `onlyDeals` y el rango `minPrice`/`maxPrice` **no**,
porque obligarían a la faceta a montar el CTE `latest` sobre `price_history` y las facetas se piden
ahora en **cada** cambio de filtro. Cruzar lo barato cuesta **63 ms**. Y la frontera **se nota**: el
`ValidationPipe` global va con `forbidNonWhitelisted`, así que mandarle `inStock` a `/catalog/facets`
no se ignora, responde **400**. Eso es lo que se quiere —una frontera silenciosa se cruza sin
enterarse— pero obliga a la SPA a enumerar lo que manda en vez de reenviar su objeto de filtros
entero, y por eso `FacetQuery` se deriva de `ProductQuery` con un `Pick` explícito.

**Los recuentos por chip se descartaron con números**, y conviene que quede escrito porque es la
propuesta que vuelve sola: dar a cada chip su «(12)» y apagar los de 0 costó entre **6,8 y 19,3 s**
en las cuatro formas que se probaron, contra los 63 ms del cruce. El coste no es la canonicalización
sino el `count(DISTINCT product_id)` por grupo, y bajarlo pide materializar un agregado por producto
—o sea migración—, que es el mismo techo que #314.

**Y la sección no es cosmética, es de corrección.** Sin sección elegida la faceta de talla ofrecía
**205 chips de los cuales 36 son ambiguos**: `36-38` es un calcetín en `ropa` y un número de pie en
`zapateria`, `24-25` igual. Pinchar uno filtraba las dos cosas a la vez. De ahí que el grupo de talla
abra con dos pestañas y que cambiar de pestaña **limpie talla y categoría**: un filtro que se queda
puesto cambiando de significado es peor que un filtro que se pierde.

Un dato que ordena el panel y que no es evidente: **el vocabulario de talla lo fija la tienda, no la
prenda.** Medido en `ropa`, todas las categorías publican 4-6 vocabularios distintos (meses, años,
altura en cm, números, letras), mientras que por tienda se separan limpio — Sfera solo usa años (12
tallas), Cacles solo números (16), C&A alturas en cm, Springfield los cinco (50), H&M cuatro (57).
Por eso elegir tienda deja la lista en una o dos formas de medir y el panel pone *Tienda* justo
encima de *Talla*. No se obliga a elegirla: el catálogo existe para no ir tienda por tienda, y
condicionar el filtro de talla a una tienda le quitaría el sentido al producto.

### Un filtro de varios valores tiene UNA forma obligatoria, o se lleva el índice por delante (#329)

El apartado anterior deja medido que **el vocabulario de talla lo fija la tienda**. La consecuencia
tardó en verse: mientras el filtro admitió **un solo valor**, elegir una talla no acotaba la
búsqueda, la **partía por un eje que el usuario no había elegido y no podía ver**. Medido en `ropa`
sobre la copia de dev (11/08/2026):

| filtro | productos | tiendas |
|---|---:|---|
| solo `4 años` | 1.485 | hipercor, mango, sfera, springfield, zara |
| solo `104` | 331 | **c-and-a** |
| las dos a la vez | 1.816 | las seis |

`4 años` y `104` son **la misma talla física**. Quien pinchaba la primera perdía C&A entera sin que
nada se lo dijera. Por eso `size`, `color` y `retailer` admiten varios valores desde la v0.3.0, y
`category`/`gender` no: en género la regla `unisex` haría que niño+niña devolviera casi todo, y
`section` es el eje de navegación que además corta la ambigüedad de las tallas (#292).

**El transporte es parámetro repetido, y lo decide el dato.** `?size=4 años&size=104`, nunca una
lista separada por comas: hay tallas con una coma dentro (`26 (16,3 cm)`), así que ese separador
partiría un valor legítimo en dos que no existen. Express entrega `string` con un valor y `string[]`
con dos o más, de modo que el DTO normaliza con un `@Transform` — y eso es también lo que mantiene
vivos los enlaces de un solo valor, que son los marcadores anteriores al cambio.

**Y la forma del SQL no es indiferente, es la mitad del asunto.** El plegado va dentro de un
`ARRAY(SELECT size_canon(x) FROM unnest($1::text[]) AS x)` **no correlado**, que Postgres resuelve
una vez como InitPlan dejando delante un `columna = ANY($0)` plano. Así el predicado sigue siendo
indexable y `ix_variant_size_canon` / `ix_variant_color_family` se conservan. Plegar fila a fila
dentro del `ANY` los perdería, con el coste que ya midió #307 (1,4 ms → 1 s). Comprobado con
`EXPLAIN (ANALYZE)`:

| | tiempo | plan |
|---|---:|---|
| 1 talla | 0,89 ms | `Bitmap Index Scan on ix_variant_size_canon` |
| 5 tallas | 2,37 ms | `Bitmap Index Scan on ix_variant_size_canon` (4.723 filas) |
| 3 familias de color | 7,13 ms | `Bitmap Index Scan on ix_variant_color_family` (36.530 filas) |

Vive en `ejeMultiple()` de `catalog.service.ts` y lo usan **tres** sitios que no pueden separarse: el
`WHERE` de `matched`, la subconsulta de `variant_count` y `deVariante()`/`deProducto()` de
`getFacets`. Cualquier filtro nuevo de varios valores —#325 con `size_band` es el siguiente— se monta
con esa función, no a mano.

Dos trampas que costaron tiempo y no se deducen leyendo el código:

- **En una plantilla `sql` de Drizzle, un array de JS se aplana en parámetros sueltos.** Así que
  `${valores}::text[]` no manda un array: manda un escalar, y Postgres responde
  `malformed array literal: "26"`. La lista se construye como `ARRAY[$1, $2, ...]` con `sql.join`.
- **Los dos índices son PARCIALES** (`WHERE delisted_at IS NULL`), así que una consulta de prueba
  que no lleve ese predicado hace *seq scan* y parece demostrar que el índice no sirve. Y una copia
  recién restaurada de `pg_dump` no tiene estadísticas hasta que se le pasa `ANALYZE`, ni
  necesariamente todos los índices —el `CREATE INDEX` de `color_family` falla durante la restauración
  por orden de dependencias—. Las dos cosas juntas producen un falso negativo muy convincente.

**Y una corrección que llegó después: la forma correcta conserva el índice, pero no obliga a usarlo.**
Todo lo de arriba sigue siendo cierto y sigue siendo obligatorio — y aun así no bastó. Las medidas de
este apartado son todas de **un solo predicado**, y con dos el planificador abandonó el índice igual.
Lo cuenta el apartado siguiente.

### Declarar la forma del predicado no basta si la función miente sobre lo que cuesta (#342)

Es la segunda mitad de la lección anterior, y costó 23 segundos por petición en producción durante
una versión entera.

El apartado de arriba deja escrito que `ejeMultiple()` conserva `ix_variant_color_family` porque el
plegado va en un `ARRAY(SELECT ...)` no correlado. Es verdad, está medido, y no cambia. Lo que no
dice —porque cuando se escribió solo se había medido con un filtro— es que **conservar el índice no
es lo mismo que usarlo**. Medido contra `deal_tracker_qa` (12/08/2026) con la consulta que ejecuta
`listProducts()`, volcada del servicio y no escrita a mano:

| | tiempo |
|---|---:|
| sin filtros | 1.932 ms |
| `color=azul` | 1.285 ms |
| `retailer=zara` | 1.368 ms |
| **`color=azul` + `retailer=zara`** | **23.698 ms** |

La consulta **más selectiva** era la más lenta, que es lo que descarta que sea el suelo del catálogo
(coste constante de agregar todo antes del `LIMIT`, el apartado de #307). El nodo culpable:

```
->  Index Scan using ix_variant_product on variant v
        (actual time=8.139..10.476 rows=2 loops=4271)
      Index Cond: (product_id = p.id)
      Filter: (color_family(color) = ANY ($6))
```

4.271 vueltas —los productos vivos de la tienda— × 10,5 ms = 44,7 s de CPU entre dos workers. Con el
segundo predicado, al planificador le sale más barato recorrer los productos de la tienda y
comprobar el color **fila a fila** que entrar por el índice del color.

**La causa es que una función SQL sin `COST` declarado vale 100 unidades de `cpu_operator_cost`, o
sea 0,25 — del orden de un par de comparaciones.** Lo que cuestan de verdad, medido sobre 20.000
variantes:

| función | por llamada | veces lo que el planificador supone |
|---|---:|---:|
| `color_family` | **0,50 ms** | ~2.000× |
| `size_canon` | 0,095 ms | ~40× |
| `color_canon` | 0,008 ms | en línea |

`color_family` sale 65 veces más cara que la función sobre la que se apila, porque son ~20 regexes
encadenados **sobre** el resultado de `color_canon`. El planificador no tiene forma de saberlo.

La `0030` lo arregla con una línea —`ALTER FUNCTION color_family(text) COST 10000`— y el filtro
combinado pasa a **1.127 ms**, por debajo del catálogo sin filtros. El valor se eligió midiendo, no
por criterio: con `COST 1000` el caso malo también se arregla pero `color=azul` solo empeora ~250 ms;
con 10.000 no empeora ninguno. `COST` no cambia lo que la función devuelve, así que —al revés de lo
que exige la cabecera de la 0029 para cualquier cambio de *cuerpo*— **no invalida el índice por
expresión y no hace falta `REINDEX`**; comprobado con el `relfilenode` intacto y `indisvalid = t`.

Tres cosas que se llevan a cualquier función futura de este tipo:

- **Toda función que se apile sobre otra nace con su `COST` medido.** El patrón `color_canon` →
  `color_family` se repite en #325 con `size_canon` → `size_band`, y ahí la trampa está esperando
  igual. Medir es un `EXPLAIN (ANALYZE, TIMING OFF)` de un `count()` sobre la columna: dos minutos.
- **Un índice por expresión no es una garantía, es una opción que el planificador puede rechazar.**
  Y lo rechaza en silencio: no hay error, solo una consulta 20 veces más lenta cuando alguien combina
  dos filtros.
- **Medir con un solo filtro no dice nada sobre dos.** Es el error de método que dejó pasar esto: el
  apartado de #329 midió con un predicado y dio el asunto por cerrado.

### Una función SQL re-evalúa su valor una vez por cada sitio donde lo nombra (#325)

El piso de abajo de la lección de #342, y el más caro de los dos. Allí el planificador no sabía lo
que costaba llamar a la función; aquí **la función se impide a sí misma desaparecer**.

`size_band` pliega las tallas a bandas de edad apilándose sobre `size_canon`, igual que
`color_family` sobre `color_canon`. Escrita como todas las demás del esquema —`LANGUAGE sql`, un
`SELECT` con el resultado de `size_canon` en un `FROM`— costaba **6,89 ms por llamada**. Medido
sobre 20.000 evaluaciones (13/08/2026, Postgres 16, el método de la 0030):

    size_canon sola                                     1.546 ms / 20.000  =  0,077 ms
    una función SQL con DOS referencias al resultado    11.207 ms / 20.000  =  0,56  ms      x7
    size_band en SQL (~10 referencias)                 137.590 ms / 20.000  =  6,89  ms      x90
    size_band con la valla `OFFSET 0`                    6.239 ms / 20.000  =  0,31  ms
    size_band en plpgsql, con variable                   1.884 ms / 20.000  =  0,094 ms

**La causa no es el `WITH`.** Fue la primera hipótesis —una CTE impide el *inline*— y es falsa:
quitarla no cambió nada, y el experimento que aisló el coste lo dejó claro. Lo que pasa es que al
hacer *inline* de una función SQL, Postgres **sustituye el cuerpo en el sitio de la llamada**, y
entonces cada referencia textual al valor se convierte en una evaluación entera. `size_band` nombra
su `s` unas diez veces —los cinco brazos del CASE de meses, los dos del final, el número—, así que
ejecutaba `size_canon` diez veces por talla.

`plpgsql` lo arregla por lo que parece un detalle de lenguaje y no lo es: **no se inlinea, y una
variable es una variable**. `size_canon` se evalúa una vez y las diez referencias son diez lecturas
de memoria. Sale en 0,094 ms contra los 0,077 de `size_canon` sola, o sea el suelo teórico.

**Lo que estaba en juego no era la consulta sino el ÍNDICE, y esa es la parte que no se ve venir.**
A 6,89 ms por llamada, `CREATE INDEX` sobre las 163.143 variantes vivas de prod son **~19 minutos**
con la tabla en `ACCESS EXCLUSIVE`. A 0,094 son 15 segundos. Una función que solo se llamara desde
la faceta habría pasado desapercibida —70 llamadas tras deduplicar—; en cuanto entra en un índice
por expresión, el coste por llamada se multiplica por el catálogo entero.

Consecuencias que se llevan a cualquier función futura del esquema:

- **Si una función del esquema nombra su valor intermedio más de dos veces, va en `plpgsql`.** La
  regla de estilo «todas son `LANGUAGE sql`» era buena mientras las funciones eran de un solo brazo.
- **La valla `OFFSET 0` también sirve** (0,31 ms) y deja la función en SQL, pero sigue siendo 4× el
  suelo y es un truco que el siguiente lector quita «limpiando», y entonces el coste vuelve sin que
  nada falle. plpgsql no se puede deshacer por accidente.
- **Esto probablemente aplica a `color_family` (0029)**, que nombra su `seg` una vez por brazo del
  CASE —unos veinte—. Los 0,50 ms/llamada que midió la 0030 y atribuyó a «~20 regex encadenados»
  pueden ser esto en realidad. No se ha comprobado y no urge, porque #327 la materializó en columna
  generada y ya no se llama por consulta; pero si alguna vez se revive el camino de calcularla, ese
  es el primer sitio donde mirar.
- **El método que lo encontró es reproducible y cuesta dos minutos**: `EXPLAIN (ANALYZE, TIMING OFF)`
  de un `count(f(t))` sobre 20.000 filas generadas, comparado contra la función de debajo. Sin esa
  comparación, 6,89 ms parece «lo que cuesta plegar una talla».

### Los contadores de uso de un índice no miden nada en un entorno sin tráfico (#317)

`ix_variant_color_canon` se quedó sin consumidor cuando #291 mudó el filtro de color a familias, y
la issue pedía confirmarlo de la forma obvia: mirar `idx_scan` en `pg_stat_user_indexes` **dos veces
separadas en el tiempo**, porque el contador es acumulado. Se hizo, con 2 h 13 min de separación
sobre `deal_tracker_prod`:

    índice                     09:24:39   11:37:09   delta
    ix_variant_color_canon     12         12         0
    ix_variant_color_family     6          6         0
    ix_variant_size_canon      10         10         0

**Y no concluye nada.** El delta es 0 en los tres, incluido el del filtro vivo. La ventana no mide
desuso: mide que nadie consultó el catálogo — que es lo esperable desde que #309 lo cerró tras
sesión y prod tiene un usuario aprovisionado a propósito. Un delta de 0 aquí solo probaría algo si
en la misma ventana el otro hubiera subido.

Lo que sí decide es el **plan**, que es reproducible y no depende del tráfico: `EXPLAIN` de cada
llamante que queda, **más un control positivo** con el patrón para el que se creó el índice. El
control es la mitad que no se puede omitir: sin él, «el índice no aparece» podría ser una manía del
planificador en vez de la ausencia de consumidor. Con él queda demostrado que el índice funciona,
que se elige en cuanto alguien pide su patrón, y que ninguno de los tres llamantes se lo pide
—`matching` lo evalúa como `Filter` fila a fila sobre la CTE de la pasada, el alta de intereses no
toca tabla, y la ficha agrupa dentro de un solo `product_id`—.

La generalización, que aplica a cualquier medida futura sobre prod: **este proyecto tiene un entorno
de producción sin usuarios a propósito**, así que toda métrica que dependa de tráfico real (contadores
de índice, caché, latencia observada) es inútil ahí. Lo que se puede medir en prod es lo estructural
—planes, tamaños, conteos— y lo que hace la ingesta, que sí corre a diario.

### Un índice de expresión ata el `ANALYZE` a una función nuestra, y el autovacuum no perdona (#370)

`ANALYZE` **evalúa las expresiones de los índices de expresión** para construir sus estadísticas.
`VACUUM` no. Esa asimetría, que no aparece en ninguna parte hasta que muerde, es la que dejó
`variant` sin analizar durante meses sin que nada lo dijera.

`variant` es la única tabla del esquema con índices de expresión sobre funciones propias:
`ix_variant_size_canon` (`size_canon(size)`), `ix_variant_color_family` (`color_family(color)`) e
`ix_variant_size_band` (`size_band(size)`). Y los workers de autovacuum corren con el `search_path`
**vaciado a propósito** —es la defensa de PostgreSQL para que nadie cuele una función suya en medio
de una operación de mantenimiento—, así que un cuerpo que llame a otra función **sin cualificar**
no resuelve. `color_family` llamaba así a `color_canon`, y `size_band` a `size_canon`.

El resultado es un modo de fallo que despista en las tres direcciones a la vez:

- **La tabla se vacúa y no se analiza.** El vacuum va primero y queda reportado; el analyze revienta
  después y se aborta entero. En `pg_stat_user_tables` eso se lee como `last_autovacuum` de hoy y
  `last_autoanalyze` de hace días, que parece un problema de planificación del autovacuum.
- **El umbral no explica nada**, y de ahí sale la hipótesis equivocada. Medido el 14/08/2026:
  `variant` tenía 50.614 modificaciones contra un umbral de 17.108 en QA (3×) y **354.920 contra
  17.153 en prod (20×)**, o sea que bajar `autovacuum_analyze_scale_factor` —la salida obvia— no
  podía arreglar nada: ya disparaba.
- **Y no deja rastro.** `log_autovacuum_min_duration` viene en 10 minutos, así que solo registra el
  autovacuum lento. El `ERROR: function color_canon(text) does not exist` sí estaba en los logs de
  la CNPG, y se había leído como ruido de alguna sesión.

Lo que se paga mientras dura: con las estadísticas rancias el catálogo va **2-2,5× más lento**, y
—peor— **distorsiona cualquier medida de latencia que se tome sin saberlo**, incluido el bloque
`## Cifras` con el que `/validar-qa` decide una promoción.

Se descartó la inanición de workers con una comprobación que vale la pena repetir si esto reaparece:
mirar la firma «sobre umbral y sin analizar» en **todas** las bases de la CNPG compartida. `variant`
era la única tabla así de todo el servidor.

**El arreglo es fijar el `search_path` de la función** (`0037`), no cualificar las llamadas internas.
Lo segundo obliga a re-pegar los cuerpos enteros en la migración —40 líneas de regex en
`color_family`— y esa duplicación es la deriva que este esquema evita en todas partes. La objeción
seria era el plan, porque **una función SQL con cláusula `SET` no se puede hacer *inline***; se midió
antes con `EXPLAIN` y los planes son idénticos con y sin `SET`, con índice (`Index Cond:
(color_family(color) = …)`) y sin él, porque `color_family` ya no se inlineaba de todos modos: su
cuerpo lleva un `FROM (SELECT ...)`.

Regla que queda, y es la parte reutilizable: **toda función alcanzable desde un índice de expresión
—cierre transitivo incluido— lleva `search_path` fijado**. Arreglar una sola no vale; con
`color_family` ya arreglada el `ANALYZE` se caía a continuación en `size_band`.

**Verificado de punta a punta en dev el 14/08/2026**, y hacía falta forzarlo porque `variant` estaba
quieta: se disparó una pasada de Zara a mano, y en cuanto la ingesta —que es atómica— hizo COMMIT,
la tabla saltó a **49.731 modificaciones sobre un umbral de 14.550** y quedó **analizada 40 segundos
después**. En el log aparecen entonces las dos fases, `automatic vacuum` y `automatic analyze` de
`deal_tracker.public.variant`, donde en las 2 horas previas no había ni una línea. Que salgan las
dos, y no solo la del vacuum, es la forma más directa de ver el arreglo: la asimetría entre esas dos
líneas *era* el fallo.

Queda un cabo suelto honesto: el mecanismo es determinista en local, pero **no explica por qué el
autoanalyze acertaba de vez en cuando** (QA el 10/08, prod el 12/08). Y ya no se puede perseguir en
dev, porque con la `0037` puesta el fallo no se reproduce — que es justo lo que se quería.

Lo que sí deja la `0036` —que baja `log_autovacuum_min_duration` a 0 *para esa tabla*, como
reloption declarada y no como GUC de un servidor que comparten otros cuatro proyectos— no es la
explicación sino **la capacidad de verlo si vuelve**: un `automatic vacuum` de `variant` sin su
`automatic analyze` detrás es ahora una señal legible en el log, y el instrumento está probado
contra el caso real, no supuesto. Antes ese mismo suceso no dejaba absolutamente nada.

### Un plan de Postgres medido en el portátil no predice el del cluster (#292)

Es la vuelta de tuerca a la lección de método de #307 —«mide la consulta que ejecuta el servicio, no
una escrita a mano»—: **medir la consulta buena tampoco basta si se mide en la máquina equivocada**.

El caso, con los mismos datos y el mismo SQL (faceta de color, 2.695 formas crudas en `ropa`,
11/08/2026):

|                              | portátil (PG 16.14) | dev, CNPG (PG 16.4) |
|------------------------------|--------------------:|--------------------:|
| sin filtros de producto      |              560 ms |            1.415 ms |
| sin filtros + `AS MATERIALIZED` |           1.090 ms |            2.658 ms |
| con categoría y género       |          **4.035 ms** |          **454 ms** |
| con cat. y género + valla    |              339 ms |              818 ms |

Las dos facetas que canonicalizan deduplican el texto crudo **antes** de llamar a la función, para
que `size_canon`/`color_family` se evalúen una vez por forma distinta y no una por variante. En el
portátil el planificador elige un **Nested Loop** y empuja `color_family(color)` **dentro del Index
Scan**, deshaciendo el ahorro: 21.536 llamadas en vez de 794. En dev elige un **Hash Join** y no
ocurre. Es la misma trampa que la migración 0014 documentó al descartar esa forma para el filtro de
talla, pero con una diferencia que cambia la conclusión: **aparece o no según la máquina**.

Lo importante es lo que casi se hace: poner `AS MATERIALIZED` para arreglar los 4 s. Habría sido una
optimización tuneada al portátil que en dev —y por tanto en QA y prod, que corren la misma CNPG—
dejaba la faceta **un 80 % más lenta en los dos casos**. Así que la regla operativa es:

- **La Postgres desechable local prueba corrección, no planes.** Vale para los tests y para
  comprobar que una consulta devuelve lo que debe; no vale para decidir una optimización que dependa
  del plan (`MATERIALIZED`, reescribir un `EXISTS` como `JOIN`, forzar un índice).
- **Antes de fijar una de esas, medirla contra la CNPG**, que se lee sin desplegar nada:
  `kubectl -n data-dev exec platform-postgres-dev-1 -c postgres -- psql -d deal_tracker -c "..."`.
  Son segundos y es la única cifra que representa dónde corre el código.
- **Y si las dos máquinas discrepan, se escriben las dos columnas** junto a la decisión. Un solo
  número invita a que el siguiente lo «arregle» con el suyo.

Dos trampas de la copia local, medidas el mismo día y las dos silenciosas:

- **`pg_dump` a través de `kubectl exec` puede truncarse.** Un volcado de 50 MB llegó cortado a
  mitad de una fila del `COPY` de `variant`, y lo que se vio no fue un error de red sino
  `date/time field value out of range: "20"` al restaurar — el trozo de un `timestamp` partido. La
  tabla quedó vacía y el resto de la base parecía bien. La defensa es comprimir en el pod
  (`bash -c "pg_dump ... | gzip -9"`): baja a 4,8 MB y `gzip -t` detecta el corte, que un `.sql`
  plano no hace.
- **Una restauración puede quedarse sin un índice por expresión** y no decirlo. Faltaba
  `ix_variant_color_family`, y con él ausente la faceta de color tardaba 8,5 s en local mientras en
  dev tardaba 1,4 s. Se atribuyó a deuda de #291 antes de comprobarlo, y era la copia. Tras
  restaurar, `SELECT indexname FROM pg_indexes WHERE tablename='variant'` contra las dos bases; si
  no coinciden, cualquier medida de rendimiento local habla de un esquema que no existe en ningún
  sitio.

### `image_url` es una cadena opaca de la tienda, y el consumidor no puede suponerle forma (#207)

`product.image_url` lo escribe el scraper y lo consume la SPA, así que es contrato entre servicios
como cualquier columna compartida — pero es el único donde **el valor no lo produce el proyecto**:
es la URL que publica el CDN de la tienda, tal cual, y su forma es distinta en cada una. Cuatro de
las nueve traen query (`?ts=`, `?v=`, `?impolicy=`) y **cinco no traen ninguna**.

La SPA daba por hecho lo primero y componía el ancho con `&` siempre. Con las cuatro tiendas que
había cuando se escribió era cierto; las cinco que llegaron después salían con un `&` sin `?` y su
CDN rechazaba la petición. Medido el 05/08/2026 sobre `dev`: C&A 400, Hipercor y Mango 403, H&M y
Springfield 404 — **8.560 de los 12.787 productos con foto**, dos tercios del catálogo desplegado,
enseñando el placeholder de «SIN FOTO». Ninguna alarma: para el navegador es una imagen que no
carga, y el componente ya tenía un respaldo bonito que lo tapaba.

Y el separador correcto no era el arreglo entero, que es la parte que no se ve venir: **pedir el
ancho tampoco es igual en dos tiendas**. Puesto el `?`, H&M devuelve 200 pero ignora `w` y sirve
**2,5 MB por foto** sobre 3.393 productos; su parámetro es `imwidth` y la deja en 181 KB. O sea que
el fallo de «tienda sin fotos» se habría cambiado por uno de 60 MB por rejilla, que además no da
error en ninguna parte.

De ahí la forma de la solución, en `frontend/src/lib/image.ts`:

- **La tabla se indexa por host del CDN, no por slug de tienda.** La regla la tiene el CDN, no el
  retailer, y la correspondencia tienda↔host **no es uno a uno en ninguna de las dos direcciones**:
  Zara y Lefties (Inditex) comparten regla; `dam.elcorteingles.es` lo comparten **Sfera e Hipercor**;
  e H&M publica por **dos hosts distintos**, `image.hm.com` y `media.arket.com` (Arket es marca del
  grupo). Además el componente solo recibe la URL: no sabe de qué tienda es, y no debería.
- **Un host desconocido se deja intacto**, no cae en el `w` por defecto. Es el fallo seguro —una foto
  más pesada de la cuenta se ve, una URL rota no— y es lo que impide que registrar una tienda rompa
  el catálogo por omisión. Pero tiene un coste medido, y conviene no venderlo como gratis: **es
  invisible**. `media.arket.com` estuvo fuera de la tabla tres validaciones sirviendo **557 KB por
  foto** en 187 productos, el peor caso del catálogo, sin aparecer en ninguna comprobación (#300).
  Por eso la vigilancia no puede vivir en un test —un vitest no ve la base— sino en un caso de
  `/validar-qa` (`casos-datos.md`) que agrupa `image_url` por host y lo contrasta con la tabla.
- **«No pedir ancho» es una respuesta legítima, y quedan dos.** C&A no acepta ninguno (Cloudinary con
  las transformaciones vetadas para `productimages/`: `?w=` lo ignora y la transformación en la ruta
  da 400) y el `sw` de Springfield **no es determinista y encima empeora** — 74 KB en crudo, 387 KB
  con `?sw=563`, así que añadirle cualquier query puede quintuplicar el peso.
- **Si la URL ya trae pedido el ancho, no se pide otra vez** (#300). Manda el que puso la tienda. No
  es una elegancia: es la condición para poder tocar `dam.elcorteingles.es`, donde **396 de las 864
  fotos de Sfera llegan ya con `?impolicy=Resize&width=516`** y las 512 de Hipercor no traen nada. Sin
  la regla, a las primeras se les concatenaría el parámetro por segunda vez — el CDN lo aguanta (200,
  33 KB) pero la precedencia pasa a ser suya. Es el caso general de lo anterior: **el mismo host
  puede necesitar trato distinto según qué tienda escribió la URL**, y eso no se puede resolver en
  una tabla indexada por host; se resuelve mirando la URL.

Y una advertencia sobre la tabla misma, que es lo que #300 enseñó por las malas: **sus `null` son
afirmaciones sobre terceros, y caducan sin avisar**. De sus nueve entradas, tres estaban mal a la vez
y ninguna había fallado nunca: Arket no estaba; `dam.elcorteingles.es` decía «ya trae su
`impolicy&width` desde el scraper» cuando `hipercor.py` **no lo añade en ningún camino** (las URL
salen literales del `ld+json`, y el parámetro que se veía lo ponía el JSON de origen de Sfera); y
Shopify decía «`width` da 404» cuando hoy responde **200 y sirve 44 KB en vez de 222 KB**. Las tres
eran suposiciones que nadie volvió a medir, y el fallo seguro las mantuvo calladas. Al leer esta
tabla, remedir antes de creerla.

Lo que conviene saber al **añadir tienda**: hay un paso que no está en ningún test y que nadie
adivina, porque el fallo no se parece a un fallo — medir su CDN con `curl` (cruda, con `?w=`, y con
el parámetro que use la familia a la que pertenezca) y añadirlo a la tabla. Sin hacerlo la tienda
funciona, solo que sirviendo la foto entera; equivocarse en el separador, en cambio, la deja sin
fotos y con el placeholder puesto. Y ojo con dar por hecho que una tienda trae **un** host: se
comprueba con la consulta de `casos-datos.md`, que es la que habría cazado a Arket.

### Toda petición autenticada de la SPA puede llevar dentro un salto a Keycloak (#266)

`apiSend`/`apiGetAuth` piden la cabecera a `authHeaders()`, que llama a `getFreshToken()`
(`frontend/src/auth/keycloak.ts`), y ese hace `kc.updateToken(30)`: **si al access token le quedan
menos de 30 s, sale a la red a refrescarlo antes de que salga la petición de la API**. O sea que
`await apiSend(...)` no es un salto de red, son hasta dos, y el segundo va a un host distinto del
de la API.

**Desde #309 esto alcanza también al catálogo**: `apiGet` dejó de ser el GET público que nunca
adjuntaba token y pasa por el mismo `authHeaders()`, así que las cuatro peticiones del catálogo
—las más frecuentes de la SPA con diferencia— pueden llevar dentro ese salto. Lo que era un
mecanismo del rincón autenticado ahora es el camino normal, y el sospechoso de #262 se mide sobre
un denominador mucho mayor.

Casi siempre da igual. Importa en un caso concreto y poco intuitivo: **cualquier cosa que el
navegador solo permita bajo un gesto del usuario deja de estar permitida después de un `await` a
una función autenticada.** Los navegadores conceden una *activación transitoria* al gesto, que
caduca sola; medido en Chromium 149 muestreando el decaimiento de un solo clic, sin consumirlo:

| ms desde el clic | `navigator.userActivation.isActive` |
|---:|---|
| 10 – 5200 | `true` |
| 6000 | `false` |

Unos 5 s, que es el valor por defecto de la especificación. Así que el patrón «clic → `await`
petición autenticada → abrir ventana» funciona con la red rápida y **falla en silencio** cuando la
suma de los dos saltos se pasa; y en Safari, cuya política es más estricta, falla casi siempre.

Eso fue #266: `SettingsPage` abría el deep-link de Telegram con `window.open` desde el `onSuccess`
de la mutación, o sea después de los dos saltos. **La solución no es acortar la cadena sino no
depender de ella**: se pinta un `<a>` y lo pulsa el usuario, porque un clic sobre un ancla siempre
es gesto. Vale como regla general — si algo necesita gesto, que lo dispare el gesto, no el
`then` de una petición.

Y una trampa de diagnóstico que hace que esto sea difícil de ver desde el propio código:
**`window.open(url, target, 'noopener')` devuelve `null` siempre**, haya abierto o no, porque con
`noopener` no se entrega la referencia. Así que el valor de retorno no sirve para detectar el
bloqueo, y comprobarlo «a ver si se abre» con Playwright tampoco: su Chromium no trae el bloqueador
de pop-ups de un navegador de escritorio. Lo que sí es portable y decisivo es leer
`navigator.userActivation.isActive` justo antes de la llamada.

**El 401 aislado de #262 salía de este mecanismo, pero no era una carrera** (medido el 14/08/2026
contra `keycloak-js@26.2.4`). `getFreshToken()` colapsaba tres desenlaces en un solo `null` —«no hay
sesión», «el refresco falló» y «la sesión está muerta»— y `authHeaders()` traduce `null` a *no mandar
la cabecera*. Así que un refresco fallido con la sesión viva no mandaba un token caducado: mandaba la
petición **anónima** a un endpoint que exige sesión. El 401 estaba garantizado y se curaba solo al
ciclo siguiente, que es exactamente lo que se observó y lo que hacía parecer que la causa era una
carrera.

Dos hechos de la librería que sostienen el arreglo y que conviene no volver a deducir:

- **El refresco es single-flight.** `updateToken()` encola en `#refreshQueue` y solo el primer
  llamante sale a la red. O sea que un sondeo no atropella un refresco en curso por construcción, y
  no hace falta protegerlo. La otra cara es que un fallo **rechaza a todos los encolados a la vez**,
  así que un solo tropiezo puede volver anónimas varias peticiones de golpe.
- **La sesión muerta se distingue sola, y no hay que adivinarla.** La librería solo llama a
  `clearToken()` —que pone `authenticated` en `false`— cuando el endpoint de token responde **400**,
  o sea cuando el refresh token ya no vale. Un fallo de red la deja declarada viva.

Con esa distinción, la política es: reintentar el **refresco** una vez y forzado (`updateToken(-1)`;
con `30` el segundo intento repite la misma comprobación de caducidad que acaba de fallar), y si no
cuaja, **lanzar `ApiError(401)` en vez de devolver `null`**, de modo que la petición condenada no
llegue a salir. Reintentar el refresco no es reintentar una petición que ya volvió 401: eso sigue
propagándose intacto, que es lo que evita enmascarar una caducidad real.

Y una restricción del navegador que cierra la otra mitad de la issue: **un 401 en la consola no se
puede silenciar desde la aplicación**. Ese `Failed to load resource: … 401` lo emite la pila de red,
no el código, así que no hay `catch` que lo tape. La única forma de que no aparezca es que la
petición no se mande — motivo suficiente por sí solo para preferir fallar antes del `fetch`.

Un defecto hermano que salió de tirar de este hilo: **nadie registraba `onAuthLogout`**, así que una
sesión muerta de verdad dejaba la SPA con el chrome de sesión iniciada, 401-eando en silencio para
siempre. Lo registra `AuthProvider`, y `RequireSession` —que ya sabía mandar a `/acceso` conservando
el destino— pasa a tener quien lo alimente.

### Un control condicionado al entorno solo se puede verificar en el entorno que lo tiene (#309)

Desde v0.3.0 el catálogo pide sesión: `CatalogAuthGuard` protege los cuatro endpoints y
`RequireSession` envuelve las rutas que los consumen. Pero **el candado es condicional a
`isAuthConfigured()`** — sin `KEYCLOAK_ISSUER_URL` deja pasar.

No es una concesión: el overlay de dev **borra las `KEYCLOAK_*` a propósito** (decisión de #23), y
un candado incondicional dejaría ese entorno sin nada visible, que es justo donde la épica manda
trabajar issue por issue. La alternativa —darle realm a dev— cuesta un cliente de Keycloak más y un
secreto más en el repo de manifiestos para un entorno que existe para iterar rápido.

La consecuencia es la que hay que recordar, porque no se deduce leyendo el código de una sola
llamada: **dev en verde no prueba absolutamente nada sobre el acceso**. Ni el despliegue, ni un
recorrido manual, ni el CI. El comportamiento que la issue existe para producir solo es observable
donde hay realm, o sea **QA y prod**, y a QA no llega hasta que un `release-qa` corta la versión.
Eso mueve trabajo de verificación *después* del merge por construcción, no por descuido, y por
eso #309 cerró con dos casillas vivas en una issue aparte (#311) en vez de quedarse abierta
esperando un despliegue.

Generalizado, que es lo que vale para la próxima: **cuando un control de seguridad se apaga solo en
algún entorno, la matriz de verificación deja de ser «pasa / no pasa» y se convierte en «pasa
dónde»**. Hay que decir de antemano en qué entorno se comprueba cada mitad, o la mitad que no se
puede ver aquí se da por buena sin que nadie lo haya decidido. Las dos mitades de #309:

| afirmación | dónde se prueba |
|---|---|
| Con auth configurada, 401 sin token | spec e2e, forzando `KEYCLOAK_ISSUER_URL` antes de cargar `AppModule`. No hace falta Keycloak vivo: sin bearer, passport corta antes de mirar el JWKS |
| Sin `KEYCLOAK_*`, el catálogo sigue abierto | los e2e de catálogo de siempre, que corren en ese entorno — son el grupo de control, y su valor es que **no** hubo que tocarlos |
| El muro y el enlace compartido, sobre lo desplegado | solo QA y prod (#311) |

Dos detalles del diseño que se decidieron aquí y conviene no revertir por descuido:

- **El guard del catálogo es otro guard, no `JwtAuthGuard`.** Aquel lanza 401 cuando *no* hay auth
  configurada, porque un recurso de usuario al que nadie puede autenticarse es exactamente un 401.
  El catálogo quiere la regla simétrica. Los dos se apoyan en el mismo `isAuthConfigured()`, y
  reutilizar el primero habría dejado dev sin catálogo.
- **El candado de la SPA va en la ruta, no en los hooks.** Además de no pisar `useProducts`, envolver
  la página impide que sus hooks lleguen a montarse: si dispararan antes de que `AuthProvider`
  resuelva `/api/config`, el token sería `null` todavía y la primera carga daría 401 con sesión
  válida. Por eso la rama `!ready` del wrapper no es cosmética.

### El `Job` conserva la imagen de su disparo, así que una validación puede medir otra release (#378)

Corolario incómodo del apartado anterior, descubierto validando **v0.4.0** el 14/08/2026. Cuando se
pregunta «¿con qué versión se ingirió esto?», el sitio natural donde mirar —el `CronJob`— **miente
por construcción**: ArgoCD ya le ha sincronizado la plantilla, así que enseña la imagen desplegada
hoy. Pero el `Job` que de verdad escribió las filas es un **snapshot inmutable del momento en que se
disparó** y conserva la que hubiera entonces.

```bash
kubectl -n deal-tracker-qa get jobs -o json | jq -r '.items[]
  | select(.metadata.name|test("scraper|matching"))
  | "\(.metadata.name)\t\(.status.startTime)\t\(.spec.template.spec.containers[0].image)"'
```

En QA, con `v0.4.0` desplegado, las nueve pasadas del 10/08 salían con
`…/deal-tracker-scraper:v0.1.9`. **Tres releases por detrás.** Y no es un despiste de una sesión: el
informe de v0.3.0 dio por hecho que su dato era de v0.3.0 y era de v0.1.9, porque no había forma
barata de verlo. Su bloque `## Cifras` describe, en realidad, la ingesta de v0.1.9.

La causa es la cadencia, no un fallo: QA solo ingiere **los lunes**, y `release-qa` corta cuando
corta. Entre una promoción y el lunes siguiente, **el entorno sirve dato viejo con binarios nuevos**,
que es un estado perfectamente sano y perfectamente engañoso.

Lo que hay que llevarse, porque es lo que decide si una validación vale:

- **La imagen desplegada y la imagen que escribió el dato son dos preguntas distintas**, y solo la
  primera es fácil. Un informe que no las separe afirma sobre la ingesta más de lo que ha medido.
- **El riesgo no es constante: depende de si la release toca `services/scraper/`.** v0.3.0 podía
  permitírselo porque no tenía ni un cambio ahí (su informe lo dice y lo usa como coartada). v0.4.0
  no: `stores/zara.py` +140 líneas (#289), más `ingest.py`, `migrate.py`, `db.py` y `run.py`. Ahí
  validar contra filas de v0.1.9 no prueba nada del código que va a producción.
- **La salida barata es disparar una pasada a mano** desde el `CronJob`, que sí toma la imagen
  vigente. En la validación de v0.4.0 se hizo con zara —la tienda que cambiaba— y salió `success`, 0
  errores, 13 min, +7,3 % de catálogo. Trece minutos compran la afirmación entera.

**Resuelto el 14/08/2026 por la primera vía**: `.claude/skills/validar-qa/scripts/qa-procedencia.sh`
empareja cada última fila de `scrape_run` con el `Job` que la escribió y la Fase 0 lo pone en el
informe, conforme o no. La segunda —una columna de versión en `scrape_run`— sigue siendo la que
cierra el agujero de verdad (contestable en SQL, sin `kubectl`, y también en prod), y se pospuso
porque toca `db/migrations/`, que en la v0.5.0 tiene otra dueña.

Tres cosas del emparejamiento que no son obvias y que cuestan una tarde si se descubren de nuevo:

- **La clave del join son los args del contenedor, no el nombre del `Job`.** Los nombres siguen un
  patrón sólo mientras los crea el `CronJob`: en cuanto alguien dispara uno a mano aparecen
  `validacion-v040-zara`, `hipercor-frio-1` o `springfield-qa-1`, que no lo siguen. Todos, en cambio,
  llevan `--retailer <slug>` en sus args, y ese slug es **el mismo string** que `retailer.slug`
  (`c-and-a` con guiones, ver `SLUG` en `stores/c_and_a.py`). El `ownerReferences` al `CronJob`
  tampoco vale del todo, porque el nombre del CronJob no siempre es el slug.
- **La fila se fecha sola.** `scrape_run.started_at` es la hora de inicio de la transacción de la
  pasada, y el `INSERT` vive dentro de ella, así que va 2-5 s por detrás del `startTime` del `Job`
  (medido: 2 s en zara el 14/08, 5 s en hipercor el 10/08). Con eso basta para elegir el `Job`
  correcto sin ambigüedad.
- **La procedencia caduca, y esa es la frontera real.** Los CronJob traen
  `successfulJobsHistoryLimit: 3` en el repo de manifiestos, y QA ingiere semanalmente: pasadas de
  más de ~3 semanas ya no tienen `Job` y su procedencia es **incontestable**, no «buena por defecto».
  Medido contra `deal-tracker-dev`, donde tres tiendas ya están en ese estado.

Y la política, que es lo que convierte el dato en decisión: **dato heredado es P1 de proceso si la
release no toca `services/scraper/`, y P0 si lo toca**. Es exactamente la coartada de v0.3.0 vuelta
regla, y el script la evalúa solo si se le pasa el rango de la release.

### Callar y acusar son decisiones asimétricas, y `max_observed` no significa lo que su nombre dice (#332)

`max_observed` es «lo más caro que hemos visto la prenda **desde que la descubrimos**», no «lo que
ha costado jamás». Las dos cosas convergen con el tiempo, y hasta que convergen la diferencia es
justo lo que separa un hallazgo de una acusación infundada.

El aviso de Telegram y el badge del catálogo **compartían regla a propósito** —`classifyHonesty`
deriva de `evaluateDeal` para que no digan cosas distintas de la misma prenda— y ahí se coló el
error: son asimétricos. Ante la duda, el aviso **calla**, y eso es correcto porque el coste es un
aviso perdido. El catálogo reutilizaba ese mismo «ante la duda» como si fuera «ante la duda,
acusa». Medido en prod el 13/08/2026: **15.928 acusaciones de «Precio inflado» apoyadas en una
media de 2,27 días de observación** (máximo 4,08), el 99,7 % sobre prendas que nunca habíamos visto
por encima de su precio actual.

**El umbral no se pudo calibrar con nuestra propia serie, y el motivo es estructural.** El
histórico más largo de cualquier variante del proyecto era de **17,3 días**, y las subidas de
precio —el único suceso capaz de sustentar la acusación— le ocurren al **0,03-0,1 %** de las
variantes (37 de 127.567 en dev, 178 de 166.655 en qa). No hay de dónde deducirlo mirando el dato.
Sale del **calendario comercial**: las rebajas corren ~2 meses, así que para haber visto la prenda
fuera de la suya hay que superar los 60 días; con 90 los dos extremos de la ventana no pueden caer
en la misma temporada (7 ene + 90 = 7 abr; 1 jul + 90 = 29 sep), y eso hace innecesario un mínimo
de observaciones aparte. Se reutiliza `HONESTY_WINDOW_DAYS`, que ya era la ventana de `recent_min`.

Tres consecuencias que no son evidentes:

- **`real` no puede verse afectado por el umbral, y está demostrado**: ya implica
  `max_observed > price`, porque `recent_min <= max_observed` —uno es un MIN y otro un MAX sobre
  las mismas observaciones— y la condición A cae antes. Por eso `evaluateDeal`, `deal-rule.sql.ts`,
  `onlyDeals` y `sort=ofertas` quedaron intactos. La implicación se apoya en ese invariante **de
  los datos**, no de la regla: si la CTE `stats` dejara de cumplirlo, se cae con ella.
- **El estado que más se ve no es ninguno de los dos que había.** `unverified` es hoy 15.968 de
  16.303 veredictos no vacíos en prod, frente a 335 `real` y 0 acusaciones. Cualquier interfaz que
  explique «dos etiquetas» está describiendo la minoría.
- **El camino de la acusación no es ejercitable en ningún entorno hasta ~05/11/2026** (qa
  ~22/10/2026), porque ninguna serie llega a 90 días. Su única cobertura hasta entonces son tests
  unitarios, y la **ausencia** de badges «Precio inflado» es el estado esperado — declarado así en
  `/validar-qa` (U26b, U26c, A31d) para que la validación no abra un P0 inventado.

**Comparar el veredicto `real` no vigila la regla: el margen del PVP inflado es invisible por ahí,
y no por un descuido del corpus.** El espejo `deal-rule.ts` ↔ `deal-rule.sql.ts` lo vigila
`test/deal-rule-paridad.spec.ts` (#228) sobre el producto cartesiano de las cinco entradas. Medido
el 13/08/2026 ejerciendo el subagente `revisor-espejo-honestidad` contra una divergencia deliberada,
**cero** de aquellos 1.200 casos caían en la franja `(M×1.03, M×1.05]` — el borde elegido, `30.90`,
es exactamente `30.00 × 1.03` y la comparación es estricta—, así que mover `INFLATED_LIST_MARGIN`
*hacia arriba* en un solo lado no lo veía nadie. Al cerrarlo (#375, 14/08/2026) el corpus resultó
ser lo de menos: con el margen a 1.05 en el SQL y a 1.03 en el TS **pasaban los 725 tests del
servicio**, y añadir el tachado que faltaba solo hace fallar el veredicto por **dos** casos, los dos
apoyados en filas que la base no puede producir.

El motivo es estructural y conviene no volver a derivarlo: para que el margen mueva `real` hace
falta `price ∈ [max_observed, list_price)`, y la condición A exige `price < recent_min`; como
`recent_min` es un MIN sobre un subconjunto de las observaciones de las que `max_observed` es el MAX
—y las `*_repr` de `product_agg` salen todas del mismo `array_agg` ordenado, o sea de la misma
variante—, en la base `recent_min <= max_observed` siempre y las dos condiciones se contradicen.
**Sobre datos realizables, `isRealDealSql` es insensible a `INFLATED_LIST_MARGIN`.**

Lo que sí lo ve es lo que el spec no comparaba: el **PVP creíble**, donde vive el margen, y el
**descuento honesto**, donde está el daño. Ese descuento alimenta el `ORDER BY` de `sort=ofertas`
sobre todas las filas, no solo las `real`, así que la divergencia no se manifiesta como una etiqueta
equivocada sino como un adelantamiento: 19,35 % frente al 16,67 % que muestra la etiqueta, con
máximo 30,00 / tachado 31,00 / precio 25,00. Y su espejo no podía ser `DealVerdict.discountPct`, que
se pone a 0 en cuanto falla la condición A —condición que el orden no aplica—: de ahí sale
`honestDiscountPct()`, extraída de `evaluateDeal` para tener a qué apuntar.

Desde #375 el margen ya no es dos números: `deal-rule.sql.ts` **importa** la constante y la
interpola con `sql.raw`, no como parámetro ligado (un número de JS viaja como `float8` y
`numeric * float8` no redondea como `numeric * numeric`). La clase de fallo desaparece en vez de
solo detectarse, y lo que queda por vigilar es que nadie vuelva a escribir el número a mano.

### El aviso no se puede provocar a voluntad: hace falta una bajada real, y el tachado no sirve

Ejercer el camino del aviso de punta a punta (#122) es caro por un motivo que no es técnico: **el
único disparador es que un precio baje de verdad**, y ni la configuración del interés ni una pasada
forzada lo sustituyen. Medido en QA el 04/08/2026, después de refrescar los dos catálogos enteros
con `--refresh-all` (lefties 9.867 filas de precio nuevas, hm 46.659):

| intento de atajo | por qué no vale |
|---|---|
| `--refresh-all` para fabricar el lote | `_record_price` escribe fila por cada variante cuyo detalle se pide, **sin comparar precios**, así que da `prior_points` a todo el catálogo — pero el precio es el mismo: `price < max_observado` salió **0 de 63.948** |
| Bajar `min_discount_pct` a 0 | Con `compare_base='recent_min'` quien corta es la condición A (*precio < mínimo reciente*), no el umbral |
| Apoyarse en el tachado de la tienda | `honestListPrice` devuelve el **máximo observado** cuando el tachado supera ese máximo en más del 3 %, así que el descuento sale 0. Pasó con los **304 tachados de Lefties de ese día, los 304** |
| Escribir `price_history` a mano | Probaría el envío, no que una pasada real produce el lote — que es justo lo que la issue pedía |

O sea que la regla de honestidad, funcionando bien, **es también lo que impide validar el aviso con
una tienda cuyo tachado no podemos corroborar**: rechazó 304 descuentos en una tarde.

> **Ojo con leer esos 304 como 304 mentiras de la tienda.** Aquí decía «rechazó 304 descuentos
> falsos», y es más de lo que el dato aguanta: los 304 son `precio 3,99 · PVP 4,99 ·
> max_observed 3,99`, o sea el patrón de una prenda **descubierta ya rebajada**, donde el máximo
> observado es su propio precio de rebaja porque no la habíamos visto antes. Que el tachado sea
> falso es una hipótesis que no se comprobó, no un hallazgo (#332). Como argumento de por qué no se
> puede fabricar un lote de avisos vale igual —el descuento honesto sale 0 en los dos casos—, que
> es para lo que está esta tabla.

El camino que sí
funciona es una tienda con movimiento real de precios y un histórico de más de un punto — Zara, con
3 pasadas, dio 31 candidatos → 13 ofertas → **13 avisos entregados**, el primer envío real de
Telegram del proyecto. H&M no sirve como banco de pruebas del tachado: **0 de 46.659 filas** lo
traen, tercera medida del mismo cero (#106).

Y una precondición que no estaba escrita en ningún sitio: `findCandidates` hace
`JOIN app_user … telegram_chat_id IS NOT NULL`, así que **sin el bot vinculado el lote sale vacío
haga lo que haga el scraper**. Vincular exige a un humano pulsando «Start» sobre un deep-link de un
solo uso (`POST /api/settings/telegram/link`); no hay forma de hacerlo desde el cluster.

**Confirmado con pasadas normales, y hay un tercer filtro que no estaba contado.** Revalidado en QA
el 06/08/2026 durante la validación de v0.1.8, esta vez **sin** `--refresh-all` —o sea con el
comportamiento de una pasada cualquiera— y por los dos caminos de `compare_base`:

| intento | lote | candidatos | ofertas | qué cortó |
|---|---|---:|---:|---|
| pasada de H&M (`run #39`, 20.251 precios) | `[39]` | 10.469 | **0** | condición A: `mínimos_nuevos = 0` de 11.020, y `mejor_diferencia = 0.00` — el mejor caso de la tienda es un precio **igual** a su mínimo, nunca por debajo |
| pasada de Lefties (`run #40`) + interés con `compare_base='list_price'` | `[40]` | 75 | **0** | `honestListPrice`: `precio 3,99 · PVP 4,99 · max_observed 3,99` → descuento honesto **0 %** |

El dato nuevo es el tercero, y muerde a quien quiera **fabricar un lote grande** (por ejemplo para
ejercer el troceo de #220): el foco barefoot de `findCandidates`
(`p.section IS DISTINCT FROM 'zapateria' OR p.barefoot = 'si'`) **recorta el lote un 78 %**. En la
pasada de Lefties, de 59 prendas rebajadas solo **13** sobreviven al filtro, porque lo que más
rebaja una tienda de moda es zapatería que no es barefoot. Con 13 viñetas (~2.650 caracteres) no se
llega ni a los 4.096 de un solo mensaje, así que **acotar el interés no es el problema: el techo lo
pone el catálogo barefoot rebajado, y en QA no da**. Es la razón por la que #220 sigue sin prueba
sobre artefacto desplegado pese a tres intentos con autorización expresa.

### El aviso no falla, se atasca: el resumen tiene un límite duro y el fallo se realimenta (#220)

`sendMessage` de la Bot API admite **4096 caracteres** y el job mandaba **un único resumen por
usuario y pasada**, sin trocear. Medido en QA el 06/08/2026 sobre el lote real: **87 prendas =
17 717 caracteres**, cuatro veces el límite. Lo que convierte eso en algo peor que un mensaje
perdido es la realimentación, y es la parte que no se ve venir leyendo el código de arriba abajo:

> Telegram devuelve 400 → `sendMessage` da `false` → `failedSends++` → **la marca de agua no se
> guarda** (solo avanza con `failedSends === 0`) → la pasada siguiente reprocesa el lote entero,
> ahora más grande → vuelve a pasarse de 4096.

O sea que **cuanto más tarda, más imposible se vuelve**, y el único síntoma es un Job en rojo. Es la
explicación más probable de por qué #122 llevaba semanas diciendo que el aviso duplicado «nunca se
ha visto llegar»: no es que no se generase, es que ninguno salía. El camino de entrega estaba bien
—acotando los intereses a 4 variantes el mensaje llegó y lo confirmó el operador—; lo único roto era
la longitud.

Cuatro decisiones del troceo que conviene no volver a discutir:

- **Se parte por oferta entera, nunca por línea.** Una oferta son 2-3 líneas y viajan juntas, así no
  se puede cortar dentro de una etiqueta HTML ni de una entidad escapada — que con `parse_mode:
  'HTML'` no es un error cosmético, es un mensaje que Telegram rechaza.
- **Hay tope de mensajes (10, ~200 prendas) y lo que sobra se resume en «y N prendas más», pero esas
  N conservan su fila en `notification`.** Soltarlas parece lo honesto y es lo contrario: con la
  marca de agua ya avanzada, su evento de precio queda por debajo y no se vuelve a evaluar nunca. Se
  perderían en silencio.
- **La entrega parcial se contabiliza por trozo.** El comportamiento anterior —soltar todas las
  reservas ante un fallo y aceptar un duplicado ocasional— era correcto con un solo mensaje y
  duplicaría de verdad con cinco. Lo que hace seguro el corte al primer rechazo es justamente la
  condición de arriba: como la marca no avanza, la pasada siguiente reprocesa el lote entero y lo ya
  entregado choca contra el `UNIQUE`. Sin duplicados y sin silencios.
- **La pausa entre trozos (~1 mensaje/segundo y chat) vive en una propiedad, no en el constructor.**
  El motivo era estructural: nada montaba `MatchingModule` en los tests —solo lo hacía
  `jobs/matching.job.ts`—, así que una dependencia más que Nest tuviera que resolver pasaba CI en
  verde y rompía en el cluster. **Desde #239 ese agujero está tapado** (ver abajo); la propiedad se
  queda porque los e2e necesitan bajarla a 0 sin pasar por la DI, pero ya no es una restricción.

### El grafo de DI de un job no lo monta nadie salvo el propio job (#239)

`AppModule` no importa `MatchingModule` —es un CronJob, no una ruta HTTP— y `matching.e2e.spec.ts`
construye el servicio a mano con `new MatchingService(db, telegram)`. Resultado: **el contenedor de
Nest no resolvía `MatchingService` en ningún test**, así que un parámetro de constructor irresoluble
pasaba `lint`, `typecheck`, `test` y el CI entero, y reventaba al arrancar el CronJob — Job en
`Error` sin haber evaluado nada, el mismo síntoma que costó dos sesiones distinguir en #220 y #221.
Y no lo cubría nada más: no hay smoke de despliegue, y `/validar-qa` mira el resultado de una
pasada, no que el contexto levante.

Tres decisiones del arreglo que vale la pena no volver a discutir:

- **El módulo del job vive en su propio fichero** (`jobs/matching-job.module.ts`), separado del
  entrypoint. El test tiene que montar el módulo *real* —una copia de sus `imports` en el spec se
  desincroniza sola— y sin separarlo habría que importar el CLI desde vitest, cuyo arranque depende
  del guard `require.main === module` y del build CommonJS que corre en el cluster.
- **El spec no necesita Postgres y por eso no se salta.** `postgres(url, {max: 10})` es perezoso: no
  abre conexión hasta la primera consulta, y montar el contenedor no ejecuta ninguna. Corre siempre,
  también en un `pnpm test` sin base — el agujero que tapa es de CI, no de datos, y un spec que se
  salta cuando falta `TEST_DATABASE_URL` no lo taparía.
- **El call site a mano de los e2e era media red, y solo media.** Un parámetro nuevo *requerido*
  rompe `typecheck` en `new MatchingService(db, telegram)`; uno **con valor por defecto** no, y ese
  pasa el compilador y los e2e intactos. Medido: es el único caso donde falla exclusivamente
  `test/jobs-di.spec.ts`, y es el que define para qué sirve el spec.

Vale para cualquier job futuro de `src/jobs/` — hoy solo está el de matching, y `database/migrate.ts`
no cuenta porque no usa DI. `AppModule` sí estaba cubierto: `test/helpers.ts` lo compila con
`Test.createTestingModule` en cada e2e.

### La marca de agua mide lo ESCANEADO, no lo que produjo aviso (#221)

`job_state.last_scrape_run_id` se derivaba de las filas candidatas, o sea de lo que sobrevive al
JOIN con `interest`. Medido en QA el 06/08/2026: la marca quedó en **34** habiendo pasadas correctas
hasta la **38**. Las cuatro que faltaban (mango, sfera, zara, springfield) se escanearon enteras,
pero los intereses activos eran de Lefties y H&M, así que no aportaron ni una fila y no movieron la
marca — y volverían a escanearse en cada ejecución, con un coste que crece con el histórico en vez
de estabilizarse.

Lo caro no es el trabajo repetido: es que **la marca deja de ser un indicador**. Un observador
externo —el caso D11 de `/validar-qa`— no puede distinguir «el matching está atascado» de «esas
tiendas no le interesan a nadie», y por eso dio P0 a lo que no lo era. Se agrava cuantos menos
intereses haya, o sea en un sistema recién arrancado: el caso normal, no el raro.

Se mide sobre `price_history` a pelo, sin cruzar con `interest` ni filtrar por stock ni por el foco
barefoot: lo que decide que una pasada está vista es haberla mirado, no que produjera aviso. Así
cubre también una pasada cuyas filas fueran todas de agotado.

### Una pasada en vuelo es INVISIBLE, así que el lote son las pasadas pendientes (#240)

Aquí decía que el riesgo del solape era preexistente y aceptado. Se materializó, y con filas dentro.

`max(scrape_run_id)` da por hecho que las pasadas se completan **en orden de id**, y nada lo
garantiza. Medido en QA el 06/08/2026: la pasada **33** (`hm`, 25.544 filas) arrancó antes pero
terminó a las 20:49:04, **11 s después** que la **34** (`lefties`, 20:48:53). El matching marcó 34 y
las 25.544 filas de la 33 quedaron por debajo de la marca: ningún lote futuro las mira. No costó
ningún aviso por casualidad —ninguna de esas filas bajaba de su mínimo de 90 días, que es lo que
lleva midiendo de H&M la #122— y **esa casualidad es la razón de que nadie se enterara**: el Job
sale en verde, `notification` no tiene un hueco visible y la marca avanza con normalidad.

La restricción que decide el diseño, y que no es evidente hasta que se busca: **una pasada abierta
no deja ningún rastro observable**. Su fila de `scrape_run` se inserta *dentro* de la transacción de
la propia pasada (`ingest.py`), así que hasta que commitea no existe para nadie más. O sea que la
corrección intuitiva —«no avanzar por encima de la pasada en curso más antigua»— **no se puede
implementar**: no hay forma de preguntar cuáles están abiertas.

Así que se invierte la pregunta: en vez de «¿hasta dónde he llegado?», **«¿qué he procesado ya?»**.
Una pasada rezagada aparece cuando commitea, no está en el libro, y entra sola en el lote siguiente
— no hace falta verla mientras está abierta, que era justo lo imposible. El estado son dos piezas:
`job_state.last_scrape_run_id` es el **suelo** (todo id por debajo está resuelto) y
`matching_scanned_run` (migración `0027`) el libro de lo procesado por encima.

Dos consecuencias que conviene no confundir con un fallo:

- **El suelo se queda atrás a propósito** cuando hay un hueco en la secuencia de ids. Un hueco es o
  una pasada en vuelo (su lote está por llegar) o un id quemado por un rollback (la abortada no deja
  fila; `_record_failed_run` inserta una **nueva**), y desde el job son indistinguibles. Se elige la
  lectura conservadora: esperar. Lo único que cuesta equivocarse es que el libro conserve unas filas
  de más. Por eso el caso D11 de `/validar-qa` ya no juzga por `last_scrape_run_id` a solas —un
  suelo retrasado es normal— sino por qué pasadas quedan pendientes.
- **Descartar una constante de tiempo fue deliberado.** Un «hueco de más de N horas ya se puede
  saltar» habría evitado que el libro crezca, pero devuelve el fallo original en cuanto una pasada
  tarde más que N, y en silencio — que es exactamente lo que esto viene a quitar. Se prefiere una
  tabla que crece ~470 filas al año en el peor caso.

De camino apareció la otra mitad del problema, que el `UNIQUE` de la `0005` **no** cubre: el
`price_event_key` es `<scrape_run_id>:<precio>`, así que el mismo precio en dos pasadas son dos
claves distintas y una rezagada reavisaba lo que la posterior ya había mandado. Comprobado con un
spec antes de arreglarlo. La corrección no es tocar la clave sino descartar en `findCandidates` los
precios **ya superados** por otro más reciente de la misma variante, que además es correcto por sí
solo: un aviso llega al móvil de alguien, y mandarle un precio que ya no existe es peor que callar.

### Un job que muere no deja rastro por defecto, y el rastro tiene dos mitades que no se sustituyen (#278)

El matching de producción se encendió el 08/08/2026 y su **única** ejecución murió en 26 s con
`BackoffLimitExceeded`. Cuando se fue a mirar no quedaba **nada**: el pod borrado, `kubectl logs`
devolviendo `No resources found` y `job_state` vacío. Reconstruir qué había pasado costó cruzar los
timestamps de git de **tres repos** —la rotación de la contraseña a las 12:08:03Z, el `suspend`
quitado a las 13:19:49Z, el Job muerto a las 13:20:34Z y el reselle en el repo de CNPG a las
13:27Z— para concluir que el Job cayó dentro de la ventana de credenciales roscas, siete minutos
antes de que se arreglara. Con rastro habría sido una consulta.

Lo que generaliza el caso del vigía (#258) es que **el rastro tiene dos mitades y cada una cubre lo
que la otra no puede**:

- **La fila en la base** dice «llegué al final». Antes, `job_state` solo se escribía cuando el suelo
  **avanzaba**, así que su ausencia confundía tres cosas: «no había pasadas pendientes», «no pude
  entregar» y «no llegué a mirar». El latido se escribe al final de cada pase no-`dry-run` tocando
  solo `updated_at` —el suelo lo sigue mandando `advanceFloor` y nadie más—, así que la señal es que
  `updated_at` esté **viejo**. Consecuencia de contrato: `job_state` significa ahora dos cosas a la
  vez, el suelo y la vitalidad del job.
- **El log del pod** dice «por qué morí», y es la mitad que la base **no puede** cubrir: si el pase
  muere porque no hay base a la que hablar —que es justo lo que pasó— ninguna fila puede registrarlo.
  Esa mitad se compra en el repo de manifiestos con `restartPolicy: Never`, porque con `OnFailure`
  hay un solo pod y el controlador lo borra al rendirse el Job.

O sea que «que deje rastro» no es una tarea, son dos, y en repos distintos. Escribir solo la de la
base habría dejado exactamente el fallo de este día sin explicar.

Un corolario de método, porque costó dos hipótesis descartadas: **un fallo en 26 s no es trabajo, es
arranque**, y antes de buscar el defecto en el propio job conviene comparar su spec con el del
entorno donde sí funciona. Aquí eran idénticos salvo el `schedule` —mismo `command`, mismas env,
mismos `resources`, mismo `backoffLimit`— y QA completaba en 10 s, lo que movió la sospecha del
código al entorno, que es donde estaba.

### Un interés se identifica por su ALCANCE, y por eso la baja es lógica

La protección contra el aviso repetido no vive solo en el `UNIQUE (interest_id, variant_id,
price_event_key)` de la `0005`: vive en que **el `interest_id` sobreviva**. Todo lo que borre esa
fila reabre el duplicado, porque `notification.interest_id` es `ON DELETE CASCADE` y se lleva por
delante las filas que protegían. Hasta la `0025` la API solo sabía **borrar** un interés, así que
dejar de seguir una prenda —un clic que el usuario lee como «ya no me interesa esto»— borraba el
historial de avisos entregados y con él la garantía (#149; reproducido en QA sobre el interés de
Zara con sus 13 avisos, que hubo que desactivar por columna en vez de por la API para no perderlos).

Son **dos mitades y hacen falta las dos**, que es lo que se puede deshacer sin querer:

1. La baja es lógica (`active = false`). Ya la respetaban el listado del usuario y `findCandidates`;
   lo que faltaba era que alguien la escribiera.
2. **Volver a seguir el mismo alcance reactiva la fila que ya existía.** Conservar el historial sin
   conservar el id no arregla nada: el aviso del mismo evento de precio vuelve a salir por un id
   nuevo, con el UNIQUE mirando. Por eso la `0025` añade `interest_alcance_uniq` sobre las **nueve
   columnas del alcance** (`user_id` + los tres apuntados + los cinco filtros) y el alta es un
   `ON CONFLICT DO UPDATE`, no un `INSERT`.

La regla de aviso (`min_discount_pct`, `compare_base`, `window_days`) **no** entra en la clave:
volver a seguir lo mismo con otro umbral es cambiar de opinión sobre el mismo seguimiento. `active`
tampoco, o la clave dejaría de impedir el duplicado justo en el caso que existe para cubrir.

Dos detalles medidos que sostienen la implementación:

- **`NULLS NOT DISTINCT` no es cosmético** (Postgres 15+; el cluster corre 16.4). Aquí un `NULL`
  significa «cualquiera», no «desconocido»: con la semántica por defecto dos intereses de «cualquier
  talla» nunca colisionarían, el `ON CONFLICT` no dispararía jamás y la reactivación no ocurriría.
- **La inferencia del árbitro por lista de columnas SÍ encuentra un índice `NULLS NOT DISTINCT`**
  (comprobado contra Postgres 16 el 04/08/2026). Importa porque Drizzle no sabe expresar
  `ON CONFLICT ON CONSTRAINT`, y pasar a SQL crudo cambiaría el contrato de la API: la respuesta en
  camelCase sale de su mapeo.

El contrato HTTP no cambió — `DELETE` sigue devolviendo 204 y `POST` 201 — así que la SPA no se
enteró. Eso es lo que hace el cambio fácil de revertir sin darse cuenta: un `create()` «simplificado»
a un `INSERT` a secas, o un borrado físico reintroducido como endpoint, pasan los tests de contrato
y solo se notan cuando alguien recibe dos veces el mismo aviso.

### El vocabulario de categorías diverge entre tiendas, y es deliberado

`sandalias` y `botas` existen en `cacles` y `lefties` —las dos tiendas que dan esa distinción
gratis, la primera en `product_type` y la segunda en hojas propias que antes se colapsaban a
`zapatos`— y **no** en `zara` ni `sfera`, donde sacarla exigiría clasificar por nombre o comprobar
si `attr.fashion_level3` viene por producto. El web no se entera: categoría y facetas son
dinámicas. Pero filtrar por `sandalias` hoy no devuelve las sandalias de Zara aunque las venda, así
que el vocabulario es **una deuda declarada, no una inconsistencia accidental**. Lo mismo pasa con
`barefoot` usado como slug de categoría en Zara y Lefties, que deja esos productos sin categoría
real.

**El criterio para la prenda que no es ninguna de las cinco (#187, #192): ¿tiene una de las cinco
como casa natural?** Si la respuesta es sí, va ahí, y quien la contesta no somos nosotros sino el
resto de tiendas: el pijama entra por `ropa-interior` en Zara, H&M, Hipercor y C&A, así que
Springfield —la única que lo dejaba fuera— se alineó (63 prendas). Si es no, categoría propia, y
sale barata: `product.category` es TEXT libre sin `CHECK` (`0001_init.sql:37`) y la faceta del web
se deriva del dato (`catalog.service.ts`, `pick('category', true)`), así que una categoría nueva
aparece sola en el filtro **sin migración y sin tocar el servicio web** — verificado contra la API
con `conjuntos` el 05/08/2026. La lista de cinco del brief no está codificada en ningún sitio
ejecutable; vive en un comentario.

**Y el ORDEN de una hoja nueva decide qué gana, así que no es cosmético.** Con el «gana la primera»
de `list_catalog()` (en H&M, `ambito_cruzado` fijando sección y categoría con la primera hoja), ir
detrás significa que un producto que la tienda **también** publica bajo una de las cinco conserva
esa categoría — la taxonomía de la tienda arbitra en vez de quien mapea. Sea cual sea la elección,
se protege con un test de orden por tienda, no con un comentario.

Hasta #200 la regla era «SIEMPRE detrás». Ya no, y el criterio que la sustituye es **el tamaño de
la hoja**, medido antes de elegir (06/08/2026, contando modelos sobre pasadas de listado reales):

| tienda | hoja delante → categoría nueva | modelos que cambian de categoría |
|---|---:|---:|
| zara | 84 | 72 |
| sfera | 28 | 9 |
| hm | 560 | **555** (483 saldrían de `pantalones`, de 1418 a 936) |

Donde la hoja es un **residuo** (Zara, Sfera) va delante: mueve decenas de prendas y lo que se gana
es la etiqueta correcta, que es de lo que van #187 y #192. Donde es un **catálogo paralelo** —H&M
publica 495 modelos en `sets-outfits`, casi todos también bajo su prenda— va detrás: adelantarla
vaciaría un tercio de una categoría del brief, y quien busque «pantalones de niño» perdería un
tercio de lo que hay. Eso ya no es etiquetar mejor un residuo, es vaciar una categoría. C&A e
Hipercor van detrás desde #192 por el motivo original: allí la hoja es limpia y solo se buscaba lo
exclusivo.

#### El residuo de una hoja que reagrupa NO es una categoría (#192)

Esto costó dos reversiones en la misma sesión y es la parte que hay que leer antes de mapear una
hoja de «conjuntos», «packs», «total look» o similares.

Medir los productos **exclusivos** de una hoja —los que no entran por ninguna otra que ingerimos—
parece decir «estos no tienen casa natural». **No dice eso.** También son exclusivos los que tienen
una casa que hemos decidido *no* ingerir, y en una hoja que reagrupa las dos poblaciones se
confunden. El residuo no es la categoría nueva: es todo lo que la tienda archiva ahí y nosotros
excluimos por otra vía. Medido el 05/08/2026, ingiriendo de verdad y mirando los nombres:

| tienda | hoja | ingeridos | legítimos | qué era el resto |
|---|---|---:|---:|---|
| c-and-a | `3-1-18`/`3-7-17` | 5 | **5** | — |
| hipercor | `bebe-*/conjuntos` | 1 | **1** | — |
| hm | `*/clothing/sets-outfits` ×7 | 20 | ~8 | 11 disfraces + 1 bikini, o sea `fancy-dress-costumes` y `swimwear`, dos ramas que su propia cabecera declara fuera |
| zara | `CONJUNTOS`/`TOTAL LOOK` ×3 | 41 | 7 | gorros, capotas, cazadoras, blazers |

Las dos últimas se revirtieron. El indicio estructural estaba a la vista en Zara y no se leyó: sus
tres hojas cuelgan de `TOTAL LOOK | CHÁNDAL`, no del eje de prenda — son **lookbooks**, agrupan las
prendas sueltas que componen un look. Springfield tenía la versión extrema: sus `total-looks` son
páginas «Shop the look» que devuelven 200 con 273 KB de HTML y **cero** `ld+json`, `size-data` y
`data-color-info`; no hay prenda, ni talla, ni precio.

Tres consecuencias operativas:

- **Esto no lo ve un test con fixtures ni un `--dry-run`.** Hace falta ingerir contra una Postgres
  desechable y **leer los nombres** de lo que entró. Es una consulta de diez segundos
  (`SELECT name FROM product WHERE category = '<nueva>'`) y es la única que distingue las dos
  poblaciones.
- **Cuando la hoja se descarta, la declaración de `COBERTURA_DECLARADA` vuelve mejor de lo que
  estaba**: con el motivo medido en vez de supuesto. Es el ciclo que esa capa existe para producir,
  y la vuelta cuenta tanto como la ida.
- **El camino que sí queda abierto es filtrar por nombre dentro de la hoja.** H&M rotula «Conjunto
  de N piezas» y C&A «conjunto - … - 2 prendas», sistemáticamente. Era maquinaria nueva; la
  construyó #200, y es lo que cuenta el apartado siguiente.

  **Pero «el nombre» no es un dato único, y en Lefties el del listado está en otro idioma.** Medido
  el 06/08/2026 sobre los cuatro conjuntos de `Recién Nacido`: la rejilla (`grids/{uuid}`) los
  llama `Snoopy Peanuts™ Waffle-Knit T-Shirt and Bermuda Shorts Co-ord` y la ficha
  (`productsArray`) `Conjunto Snoopy Peanuts™ gofrado camiseta y bermuda`, **con el mismo
  `languageId=-5` en las dos URLs**. Importa porque la categoría se fija en `list_catalog()`, o sea
  con el nombre del listado a la vista y sin haber pedido la ficha: un predicado «empieza por
  Conjunto» daría **cero** ahí, y en silencio — indistinguible de «esta hoja ya no trae conjuntos».
  Antes de meter el filtro en Lefties hay que comprobar el idioma del listado; solo está medido en
  una tienda.

#### Partir una hoja en dos: `FiltroDeHoja` (#200)

`CategoryConfig` mapea una hoja a UNA categoría, y la hoja mezclada necesita decir «de ésta, solo lo
que cumpla X». Eso es `stores.base.FiltroDeHoja`: un patrón, un `resto` para lo que no casa
(descartarlo, o mandarlo a otra categoría) y un `excepto` para lo que casa y no queremos. El tipo se
comparte; el **campo** no puede compartirse, porque `CategoryConfig` no es una clase sino **nueve
dataclasses independientes**, una por tienda. Solo lo llevan las tres que lo necesitan.

Cuatro cosas que costó medir y que no se deducen:

- **Se filtra con el texto que la tienda ya sirve en el LISTADO**, nunca pidiendo la ficha. Sfera
  publica la faceta `attr.fashion_level3` con el recuento exacto, que es el dato más limpio, y aun
  así se usa el título: el listado firefly no trae `attr` por producto, así que la faceta costaría
  una petición más **por hoja**. El título da lo mismo gratis (66 «Sudadera» y 25 «Conjunto», y la
  faceta dice 66/25).
- **Puede hacer falta más de una señal, y en Zara ninguna basta sola.** Su `familyName` es taxonomía
  de la tienda y coge 13 que el título pierde (`PACK BODY Y LEGGING`, `SET PRIMERA PUESTA`, y un
  `CONJUTO` con la errata de la tienda); el título coge 40 que la familia pierde, archivados bajo
  `CHANDAL BEBE` — familia que no vale como señal porque también lleva prendas sueltas.
- **El nombre de la hoja no predice si tiene lo que busca, ni siquiera cuando se llama igual.** Las
  dos hojas de Zara rotuladas `CONJUNTOS` para 6-14 publican **cero** conjuntos; los tienen tres que
  #192 había descartado (`CHÁNDAL` niña 13, `CHANDAL` niño 20, `PACKS|CONJUNTOS` 18). Mapear las que
  se llaman bien habría dejado el ámbito permanentemente vacío.
- **El filtro también tiene que saber decir que NO.** H&M publica «Conjunto de disfraz», que el
  patrón acepta encantado: de los 7 conjuntos que llegaron a ingerirse, **3 eran disfraces** — o sea
  `fancy-dress-costumes`, rama declarada fuera del brief, volviendo por la puerta de atrás. Es el
  fallo de #192 un nivel más abajo: allí se colaba por la hoja y aquí por el nombre. Lo destapó la
  consulta de los nombres uno a uno, no el fixture ni los 574 tests.
- **Y el aviso de Lefties de arriba —el listado en otro idioma— no es de una sola tienda.** H&M
  publica parte de su catálogo sin traducir dentro de la misma hoja: 20 filas rotuladas
  `2-piece cotton set`, `3-piece denim set`, `2-piece T-shirt and joggers set`, junto a las que sí
  dicen «Conjunto de …». Van dos tiendas de dos en las que se ha mirado, así que **el idioma del
  listado se comprueba antes de escribir el patrón, no después**: aquí se resolvió aceptando las dos
  formas, y dejarlo en una habría hecho que el criterio fuese «los que la tienda haya traducido».

Y el caso silencioso, que es el que importa a tres meses vista: **una hoja que responde pero cuyo
filtro no casa con nada es indistinguible de un cambio de rotulación de la tienda**. Callarse
descatalogaría de golpe todo lo que la hoja etiquetaba. `ScanReport.filtro_vacio()` saca ese ámbito
de las bajas y nombra la hoja en `scrape_run.message`, **sin contarla como hoja caída**: la hoja se
listó, y sumarla a `leaves_failed` inflaría `dead_ratio` disparando `SCRAPER_SCAN_MAX_DEAD_RATIO`
por algo que no es un bloqueo. Es el mismo razonamiento que el ámbito extra de `tambien_unisex`.

#### Reclasificar no se ve el día del despliegue: llega por goteo

Consecuencia de #200 que descoloca si no se sabe, y vale para **cualquier** cambio de categoría, no
solo para éste. Sobre una base que ya tiene catálogo, el re-etiquetado **no llega en la primera
pasada**: `category` solo la escribe `ingest._upsert_product()`, o sea solo al pedir la ficha, y a
un producto cuya huella no ha cambiado no se le pide — `_touch_seen()` solo marca que se le ha
visto. Medido el 06/08/2026 devolviendo a mano los 84 conjuntos de Zara a `pantalones`: una pasada
normal los dejó donde estaban, y una con `--refresh-all` (tope 400) recuperó 33.

Es exactamente el mismo comportamiento que #172 documenta para el género, y la misma cura. Importa
para validar QA tras una release: ver la categoría casi vacía no prueba que el filtro falle.

Un aviso de calibración para el que lea la tabla: un número de #192 estaba **caducado** y casi
decide el trabajo. Decía «207 prendas nuevas en Zara»; se había medido mientras se implementaba la
issue #186, o sea contra el catálogo de antes de que existieran las hojas de bebé. Con ellas
dentro son 242 en la hoja y 212 ya entraban. **Un número medido en una issue lleva implícito el
estado del catálogo de ese día**, y si entre medias se ha mapeado una rama entera, hay que volver
a medirlo.

### Hay prendas que no piden una categoría nueva sino un EJE, y se modelan aparte (#180)

La contrapartida de la sección anterior. Allí la pregunta es a qué categoría va una prenda; aquí
la respuesta es que **la pregunta no aplica**: la ropa deportiva ya está en su categoría real y lo
que falta es poder cruzarla. Se distinguen por una prueba barata — enumerar el cajón de la tienda y
cruzarlo contra el catálogo. Si la mayoría **ya entra** por otra hoja, no es una categoría:

| tienda | publica | ya dentro | medido |
|---|---:|---:|---|
| c-and-a | 48 | 45 | 05/08/2026, pasada real |
| lefties | 181 | 167 | 05/08/2026, pasada real |
| sfera | 91 | 47 | #175, sobre las cuatro hojas |

Cuatro tiendas de cuatro dijeron lo mismo. Una categoría `ropa-deportiva` solo podría llenarse
robándole prendas a `camisetas` o `pantalones`, y entonces el mismo pantalón caería en una u otra
según qué hoja lo listó primero — que es exactamente lo que el «gana la primera» hace inevitable.

**Tabla `product_tag` (0026), no columna.** `barefoot` (0012) es el precedente estructural —una
marca ortogonal a la categoría, que escribe el scraper y filtra el catálogo— pero es una sola, y ya
hay un segundo eje con la misma forma esperando (#189, el uniforme escolar de H&M). Una columna por
eje repite migración + ingesta + espejo Drizzle + faceta + SPA cada vez. Coste aceptado: un `EXISTS`
en el listado, la ficha y las facetas.

Cinco decisiones que no son obvias y que cuestan caro al revés:

- **El calzado queda fuera.** La zapatilla deportiva ya se encuentra cruzando la categoría
  `zapatillas` con el filtro barefoot, y esa categoría la pueblan Cacles (que mapea ahí
  `deportivas`, `de fútbol`, `de running`, `de gimnasia y baile`), Zara y Lefties. No es teoría: de
  los 167 productos de la rama de Lefties que están en el catálogo, **37 son calzado** y el eje los
  descarta. Marcarlos crearía dos formas de pedir lo mismo con resultados distintos por tienda. La
  regla vive en `tags.SECCION_APLICABLE` y la aplica la INGESTA, no cada scraper.
- **La marca es del producto, no de la hoja que ganó el listado.** En Lefties 130 de esos 167 salen
  *también* por su categoría, y `list_catalog()` se queda con la primera hoja que ve: marcar por
  hoja ganadora haría que la marca dependiera del orden de `CATEGORIES`. En las tiendas que emiten
  según recorren (Sfera) hay que anotar **antes** del `continue` del dedup.
- **Se escribe desde el LISTADO, no desde `fetch_details()`.** El detalle solo se pide para lo
  nuevo, cambiado o rancio, así que colgar la marca del `ScrapedProduct` —que es lo natural— la
  dejaría vacía para casi todo el catálogo en régimen estacionario. El protocolo
  `SupportsProductTags` se consulta tras agotar el generador, igual que `scan_report()`.
- **Una hoja que solo etiqueta no entra en `scopes()`.** Si entrara, pasaría a poder provocar bajas
  — y una hoja transversal es justo la que más se mueve. El precio es que sus productos exclusivos
  (14 en Lefties, 3 en C&A) se quedan fuera del catálogo: su categoría real no la dice nadie.
- **Una fuente caída no reconcilia su eje.** La reconciliación borra lo que la tienda ya no declara,
  así que sin este acote la pasada siguiente a un 404 se llevaría las marcas de toda la tienda de
  una vez. Aquí no hay histéresis ni sondeo detrás como en las bajas: una pasada mala basta. En C&A
  el caso peligroso ni siquiera lanza — una hoja retirada responde 200 con la lista vacía.

**Y una trampa medida que invalida números escritos en las issues:** en Lefties el grid del nodo
padre **no devuelve su subárbol**. Medido el 05/08/2026 pidiendo las dos cosas: la rama de niña da
77 en el padre y 93 en la unión de sus seis hijas; la de niño, 69 y 99. O sea que quedarse en el
padre se deja fuera 46 prendas, casi un cuarto. El «146» que circula por #180 y por las
declaraciones del vigía es la cifra del padre, no la de la rama. Las hijas se resuelven del menú en
ejecución, que la pasada ya se descarga, en vez de escribirse.

**La limitación honesta es la cobertura**: solo cinco de las nueve tiendas publican un cajón de
deporte identificable. Zara, Hipercor, Springfield y Cacles no lo dicen, así que filtrar por el eje
las excluye enteras. Eso no es un hueco que se rellene solo, y por eso la SPA lo dice en el propio
interruptor en vez de esconderlo. Vacío **no** significa «no es deportiva»: significa «su tienda no
lo declara», y ningún consumidor debe leerlo como una negación.

### Local

Postgres desechable en Docker para tests e ingesta. `TEST_DATABASE_URL` decide si corren los tests
de ingesta (se saltan si no está, así que un `just check` verde sin ella prueba menos de lo que
parece). El cluster dev solo sirve para verificar el **despliegue**, y eso exige mergear a `main`.

## TRADEOFFS

- **SQL neutro como contrato en vez de un ORM compartido**: permite que Python y TypeScript escriban
  la misma base sin acoplarse. Coste: el espejo Drizzle se mantiene a mano y puede divergir.
- **Ingesta atómica**: si el deadline corta una pasada en frío, hace rollback y no guarda *nada*, así
  que el catálogo no se puebla nunca. Por eso `activeDeadlineSeconds: 7200` y `backoffLimit: 1` —
  reintentar en bucle no arregla un bloqueo de la tienda y sí se come el presupuesto. Medido contra
  Zara real: 1ª pasada (2219 productos / 25623 variantes) ~30m18s, siguientes ~1m35s gracias al
  detalle condicional por huella.
- **Promoción por digest en vez de rebuild**: QA no puede diferir de dev por un build no
  determinista. Coste: no se puede parchear QA sin pasar por dev.
- **selfHeal de ArgoCD activado**: un `kubectl patch` en el cluster se revierte solo. Todo cambio de
  cluster pasa por el repo de manifiestos.
- **Añadir una tienda son dos repos, no uno**: el registro en `stores/registry.py` no la ejecuta en
  el cluster sin su CronJob en el repo de manifiestos. Fue el drift real de `lefties`, ya resuelto
  (tiene CronJob en `base` y patch en el overlay de QA), y es el que hay que comprobar cada vez.

## PHILOSOPHY

Defaults seguros en la base y activación explícita en el overlay: lo que puede escribir en
producción o gastar cuota nace apagado.

Los comentarios del repo de manifiestos guardan **mediciones, no opiniones** (tiempos de pasada,
tamaño de imagen, por qué 2 intentos y no 3). Al tocar límites o schedules, actualizar el número
medido en vez de borrar la justificación.

Trabajar con tiendas reales es adversarial: los identificadores mueren, las categorías devuelven
404, y una hoja muerta no debe tumbar la pasada entera. Los tests con fixtures no detectan eso —
para ahí está el subagente `revisor-robustez-scraper`.

## OPERACIÓN DEL PROPIO ÍNDICE

Este ADR se versiona en `.claude/adr/deal-tracker.md`; el grafo de `codebase-memory` es solo la
copia consultable, y es local a cada equipo. El fichero es lo que permite reconstruirlo en otro
portátil y revisarlo en un PR. La skill `cerrar-sesion` lo mantiene al día al terminar.

Al reindexar, modo **`full`**: `fast` excluye `db/migrations/` y `tests/fixtures/`, justo el
contrato del proyecto. `detect_changes` no sirve como señal de caducidad —`index_status` lee el
git en vivo, así que `base_sha == head_sha` siempre—, pero el reindexado es incremental y tarda
segundos, así que sale más barato hacerlo siempre.

**El reindexado PUEDE borrar el ADR del grafo, y no se puede predecir cuál de las dos cosas pasa.**
Esta sección afirmaba «siempre», con el «no es un fallo intermitente» explícito, a partir de dos
medidas del 31/07 y el 02/08/2026 (`adr_present: false` las dos veces, y `manage_adr --mode
sections` pasando de 16 secciones a lista vacía). **Ese "siempre" es falso**: el 02/08/2026 por la
tarde, un `index_repository` en modo `full` sobre este mismo repo devolvió `adr_present: true`, y
`--mode sections` seguía listando las 16 secciones y `--mode get` devolvía el contenido entero.

Es el cuarto escarmiento del mismo tipo que ya colecciona la sección de canonicalización
(#49, #51, #64): una frase escrita aquí con seguridad, que se cae en cuanto alguien vuelve a
medir. Y tiene un agravante propio — se escribió *sobre la propia herramienta con la que se
escribe*, así que nadie iba a cuestionarla desde el código.

La consecuencia operativa **no cambia**, y por eso el error no llegó a costar nada: republicar
desde `.claude/adr/` sigue siendo el **último** paso de la sesión, después del último reindexado.
Con el comportamiento indeterminado, ese orden es lo único que garantiza el resultado en los dos
casos. Lo que sí cambia es la comprobación: **no asumir en ninguna de las dos direcciones**, mirar
`manage_adr --mode sections` al terminar — el `adr_present` de `index_repository` solo describe
cómo quedó en ese instante. Y por eso el fichero versionado es la fuente de verdad: el grafo puede
perder esto en cualquier momento.

**Una referencia `#N` nunca debe quedar a principio de línea en este fichero.** El parser de
secciones trata cualquier `#` en esa posición como encabezado, así que un `#227).` al que el reflow
del párrafo dejó abriendo línea aparece en `--mode sections` como una sección más, con media frase
por título (medido el 06/08/2026: 40 secciones en vez de 39). Ensucia justo lo que se usa para
navegar el ADR. La comprobación es `grep -n '^#[0-9]' .claude/adr/deal-tracker.md`, y el arreglo es
mover una palabra al principio de la línea siguiente.

Y un aviso sobre cómo se verifica ese arreglo, porque costó dos PR el día que se encontró: **el
`{"status":"updated"}` de `manage_adr` no dice que el grafo tenga lo que tú crees**, solo que
aceptó lo que le mandaste. Si tras republicar la sección fantasma sigue ahí, la primera hipótesis
correcta no es «se ha perdido la republicación» sino «el fichero sigue mal». Se distingue mirando el
fichero, no el grafo.
