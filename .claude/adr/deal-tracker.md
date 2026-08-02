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
- `db/migrations` — **SQL crudo neutro** (`0001_init.sql` … `0019_size_canon_singular.sql`).

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
  slug → factoría. Hoy: `zara` (endpoints AJAX JSON públicos), `sfera` (Chromium, detrás de
  Akamai), `lefties`, `cacles` (Shopify, `products.json` público) y `c-and-a` (GraphQL con
  persisted query, httpx puro). Añadir tienda = añadir entrada en el registry.
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
   dato rompería ese join en silencio.
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
- **H&M devuelve una página LLENA Y PLAUSIBLE**, y es la peor de las cuatro. Su `categoryId` es
  decorativo —el selector real es `pageId`— y un `pageId` inventado (`/ruta/totalmente/inventada`)
  responde 908 hits y 36 productos, ni 404 ni vacío ni el padre. **No se detecta por status ni por
  vacío**: una hoja renombrada sigue «funcionando» e ingiere productos de otra categoría en silencio,
  sin que caiga nada que las redes de seguridad puedan contar. La única defensa barata es que la hoja
  declare qué espera y se contraste con lo devuelto (en H&M, el `mainCatCode` de los productos).
- **Hipercor repite el espejismo de Sfera** —es el mismo firefly— pero trae el antídoto: su
  `data.paginatedDatalayer.page.hierarchy` refleja la ruta **realmente resuelta**, así que basta
  compararla con la pedida, sin cotejar ids de la primera página. Seis rutas inventadas a mano
  devolvieron las seis el catálogo del padre, en silencio.

Y una que **sí lo resuelve bien**, que conviene tener como referencia de lo que se le puede pedir a
una tienda: **C&A** distingue las dos situaciones con un solo campo — hoja inexistente da 200 con
`productCount: 0`, y hoja viva pasada de página da 200 con el `productCount` intacto. Es la trampa de
Cacles con el desambiguador incluido, sin heurística posicional.

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

Medido contra Cacles el 31/07-01/08/2026. Una página con `limit=250` puntúa
`shopify-complexity-score: 12400` y el cubo tarda **minutos** en rellenarse. Consecuencias
prácticas:

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

| | 429 de presupuesto (Shopify) | 429 de huella (Cloudflare) |
|---|---|---|
| cuerpo | JSON / vacío | `local_rate_limited` |
| depende de | cuántos productos has pedido | qué cliente eres |
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

Desde el 02/08/2026 esa distinción **está en el código y no solo aquí**: `es_429_de_huella()` mira
el cuerpo y el de la huella se eleva a la primera (`HuellaTLSRechazada`) en vez de reintentarse.
No es una optimización, es que reintentarlo era activamente malo: con los ajustes del CronJob
(6 reintentos, backoff 8 s y el `Retry-After: 60`) son **~10,5 min de `activeDeadlineSeconds`**
quemados para acabar elevando igual, y con un mensaje que apunta a la causa equivocada. El techo
`httpx<0.29` en `pyproject.toml` está por lo mismo: el arreglo se apoya en un detalle **interno**
de httpcore (que llame a `set_alpn_protocols()` sobre el contexto que recibe), así que el bump
tiene que ser un PR donde toque re-verificarlo.

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
### Un scraper se rompe de dos maneras y solo una se ve

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
Medido el 02/08/2026 sobre las cuatro tiendas, esa tolerancia se ejerció a la primera: sfera 18/19
hojas, con la que faltaba dando 403. Un vigía con falsas alarmas rutinarias acaba silenciado, que
es peor que no tenerlo.

Corolario aprendido en la misma sesión: **cuando una capa revienta, no se ejecuta la siguiente**.
Con Lefties sin Chromium, el segundo error salía derivado («usa la API async») y tapaba la causa
real. Un vigía que apunta a la pista falsa es peor que uno que dice una sola cosa cierta.

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
