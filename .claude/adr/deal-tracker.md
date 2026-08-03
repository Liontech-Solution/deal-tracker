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
  `scrape_run`. `ingest.py` hace la pasada completa en **una transacción atómica** y detecta altas
  y bajas. El **web** posee `app_user` (con el vínculo de Telegram), `interest`, `notification` y
  `job_state`.
- Las tiendas son **pluggable**: `stores/base.py` define `BaseStore`, `stores/registry.py` mapea
  slug → factoría. Hoy son **nueve**: `zara` (endpoints AJAX JSON públicos), `sfera` (Chromium,
  detrás de Akamai), `lefties` (Chromium, API `itxrest` de Inditex), `cacles` (Shopify,
  `products.json` público), `c-and-a` (GraphQL con persisted query, httpx puro), `hipercor`
  (Chromium, leída **por sus páginas** porque su `robots.txt` veta `/api`), `hm` (httpx pelado
  contra `api.hm.com`, que es otro host que el escaparate), `mango` (httpx con UA de Chrome, y la
  primera que publica su árbol de categorías) y `springfield` (httpx, y la primera que **no recorre
  hojas**: se lista por sitemap porque su `robots.txt` veta la rejilla de SFCC). Añadir tienda =
  añadir entrada en el registry. Las siete primeras tienen catálogo ingerido en `dev` desde el
  02/08/2026; `mango` y `springfield` se registraron el 03/08/2026, y ese mismo día `springfield`
  hizo su primera pasada (1112 productos, 8620 variantes, 25 min en frío) — `mango` sigue sin
  ninguna.
- **`price_history.retailer_min_30d` (0018) es el primer dato del contrato que no observamos
  nosotros**: es el mínimo de 30 días que la tienda **declara** por la directiva Ómnibus. Importa
  porque el detector de descuentos engañosos vivía de una sola fuente —nuestro propio histórico—,
  con la limitación de nacimiento de no poder decir nada de antes de que empezáramos a mirar. Ahora
  hay con qué contrastar, y **la discrepancia entre ambas cifras es en sí misma la señal**: medido
  en C&A el 02/08/2026, **67 de 364** variantes con precio tachado anuncian descuento mientras la
  propia tienda declara haberlas vendido más baratas dentro de esos 30 días. Hoy solo la puebla
  C&A; `NULL` significa **«esta tienda no lo declara»**, nunca «no hubo mínimo», y ninguna consulta
  puede tratarlo como un cero. Se captura desde la primera pasada aunque el detector aún no lo use,
  porque **el histórico no se reconstruye hacia atrás**.
- **`cacles` es la primera tienda barefoot NATIVA**, y entró porque el foco barefoot (#30) dejaba la
  zapatería casi vacía: las otras tres son cadenas de moda convencional y entre ellas sumaban ~92
  referencias respetuosas. Eso convierte «tienda entera barefoot» en un caso que el modelo tiene que
  soportar, no una rareza — de ahí `classify(tienda_barefoot=True)` en `barefoot.py`, que es la
  tercera vía junto a la categoría propia de la tienda (Zara, Lefties) y la heurística de texto
  (Sfera). Se declara a nivel de tienda **en vez de** usar `category="barefoot"`: ese slug dejaría
  todo el catálogo bajo una sola categoría y mataría la faceta. Cacles es la primera tienda donde
  categoría y respetuosidad son ejes ortogonales; en Zara y Lefties siguen mezclados.
- El **web** expone `/api/catalog/*`, `/api/interests`, `/api/settings/telegram`, `/api/config`,
  `/api/health`, y el job `dist/jobs/matching.job.js` que evalúa ofertas y notifica por Telegram.
- `services/web/src/database/schema.ts` (Drizzle) es un **espejo** del SQL, no la fuente de verdad.
  Los tres puntos — `db/migrations`, ese espejo, y el SQL crudo de `ingest.py` — pueden divergir en
  silencio; existe el subagente `revisor-contrato-esquema` precisamente para eso.

**Las migraciones tienen dos aplicadores, ambos idempotentes**: el scraper con `--migrate` y el web
con `node dist/database/migrate.js` (initContainer del Deployment). Cualquiera de los dos puede
aplicarlas; ninguno es "el dueño".

**Todo lo `KEYCLOAK_*` y `TELEGRAM_*` es opcional a propósito**: sin ellas la auth queda apagada
(la SPA funciona como catálogo público, los endpoints de usuario dan 401) y el job de matching
fuerza `--dry-run`. Así corre `dev`.

## PATTERNS

### El seam con el repo de manifiestos (contexto que ningún repo documenta entero)

El despliegue **no vive aquí**. Vive en `juanjocop/k3s-local-apps-manifests`, bajo
`deal-tracker/{base,overlays/dev,overlays/qa}` (Kustomize + ArgoCD, auto-sync prune+selfHeal).
Este repo produce imágenes; aquel decide qué corre.

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

**El corolario que ya ha mordido dos veces: en QA, capacidad nueva ≠ capacidad disponible.** Como
QA solo avanza con un `release-qa` manual, todo lo que se mergea a `main` llega a dev al instante y
a QA **nunca**, hasta que alguien corta versión. Así que un CronJob nuevo activado en el overlay de
QA se programa contra una imagen que aún no tiene el código: el vigía habría fallado por
`ModuleNotFoundError` (#67) y el scraper de C&A por `ValueError: Tienda desconocida` (#78) — los
dos, un fallo garantizado a fecha fija. La regla es que **el CronJob de una capacidad nueva nace
`suspend: true` en QA aunque sus hermanos estén encendidos**, con el motivo y los dos pasos
(cortar release → poner `false`) escritos en el propio patch, no solo en el PR. En dev no aplica:
el bump es automático.

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
vigila. Lo pausa **dev**, no por prudencia sino porque dev y QA comparten cluster y salen por la
misma IP — preguntarlo dos veces es el doble de peticiones a cambio de cero señal.

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
`postgresql-generic` del cluster. QA es público en `dealtracker-qa.liontechsolution.com` a través
del túnel compartido `cloudflared` (la ruta se configura en el panel de Zero Trust, no en Git).

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
6. **El resultado depende del `ctype` de la base, así que hay que probarlo con el del cluster.** Las
   dos funciones empiezan plegando la caja, y `lower()` **no baja las letras acentuadas** bajo ctype
   `C` — que es el de la CNPG: `deal_tracker` y `deal_tracker_qa` son `UTF8 | C | C`. Se pliega con
   un `translate()` explícito (0021) y **nunca con `lower()` a secas**. Detalle en el apartado de
   abajo, que es donde está lo transportable.

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
(rangos de edad solapados y el umbral pie/edad en la talla; familias de color y acentos en el color).
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
- **Trae `lastmod` en las 12 842 URLs**, y eso *es* la `signature`. Es lo que hace viable la tienda:
  el detalle cuesta una petición por ficha, así que sin huella cada pasada costaría el catálogo
  entero. Medido: **25-31 min en frío contra 1m39s** en régimen estable, ×17.
- **Todo el listado son 4 peticiones** (el índice y tres ficheros de producto).

Riesgo abierto que no se puede cerrar en una sesión y queda anotado: **si `lastmod` no se moviera al
cambiar solo el precio, el detalle condicional congelaría los precios**. Exige observar el mismo
producto en dos días. La red que lo cubre mientras tanto es el refresco periódico forzado
(`last_detail_at` + `SCRAPER_DETAIL_MAX_AGE_DAYS`), que ya existe por otro motivo.

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

### El árbol de categorías de una tienda no es lo que parece

Dos cosas medidas sobre Zara y Sfera que se repiten y conviene dar por supuestas al mapear la
siguiente tienda:

**Los rangos de edad son ramas distintas, y el barefoot vive en la de bebé.** Las dos tiendas
parten el catálogo infantil en 6-14 y mini/bebé, con rutas separadas, y el calzado respetuoso está
sobre todo en la segunda: en Zara **78 de 86** referencias barefoot no se ingerían (#35), en Sfera
**5 de 6** (#33). Mapear solo la rama mayor parece cubrir la tienda y deja fuera el grueso de lo que
este producto existe para encontrar. Y el árbol **no es simétrico** entre rangos: la mayoría de
categorías de ropa de Sfera no existen en bebé.

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
la respuesta vienen con `values: []`), así que no implementa `SupportsCategoryTree`. Pero **la
página de categoría del escaparate trae el menú entero incrustado**: 507 rutas `/kids/…` y 183
`/baby/…` en un solo HTML, que se lee una vez con Chromium (el escaparate es Akamai; la API no).
No es capacidad de ejecución, es reconocimiento — pero cubre lo que importa, que es no sacarse las
rutas de la cabeza. Y el resultado justifica el rodeo: se probaron **18 rutas plausibles de bebé y
las 18 eran espejismo**, porque el árbol real dice dos cosas que nadie adivina:

- **Bebé no cuelga de infantil: es un departamento hermano** (`/baby/…`, no `/kids/baby/…`).
- **El rango 9-14 años es una rama aparte** (`/kids/boys-9-14y/…` junto a `/kids/boys/…`). Quedarse
  con la primera habría dejado fuera media tienda por edad — el mismo agujero que #56 y #72 en
  Sfera, ahora con la variante de que las dos ramas se llaman **casi** igual.

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
- **`ScanReport.leaf_gone(scope, tambien_unisex=True)`** — al marcar una hoja de género como caída,
  sacar de las bajas también el ámbito `unisex` equivalente: si cae una de las dos ramas, el
  producto cruzado se emitiría con el género de la superviviente y su ámbito `unisex` parecería
  vaciado. Cuenta **una** hoja caída y no dos, o `SCRAPER_SCAN_MAX_DEAD_RATIO` saltaría antes de
  tiempo.

Aplicado en H&M (#102), Hipercor y Lefties (#98); Sfera no lo necesita mientras siga en cero. Un
detalle de operación que conviene saber al arreglarlo en una tienda ya ingerida: el género de las
filas existentes **no se reescribe** hasta que se les vuelva a pedir la ficha. Un producto cuya
huella de listado no cambia pasa por `_touch_seen()`, que solo refresca `last_seen_at`, así que la
corrección llega por el **refresco forzado** (`SCRAPER_DETAIL_MAX_AGE_DAYS` / `_REFRESH_MAX`) y
tarda varias pasadas en barrer el catálogo.

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

### El vocabulario de categorías diverge entre tiendas, y es deliberado

`sandalias` y `botas` existen en `cacles` y `lefties` —las dos tiendas que dan esa distinción
gratis, la primera en `product_type` y la segunda en hojas propias que antes se colapsaban a
`zapatos`— y **no** en `zara` ni `sfera`, donde sacarla exigiría clasificar por nombre o comprobar
si `attr.fashion_level3` viene por producto. El web no se entera: categoría y facetas son
dinámicas. Pero filtrar por `sandalias` hoy no devuelve las sandalias de Zara aunque las venda, así
que el vocabulario es **una deuda declarada, no una inconsistencia accidental**. Lo mismo pasa con
`barefoot` usado como slug de categoría en Zara y Lefties, que deja esos productos sin categoría
real.

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
