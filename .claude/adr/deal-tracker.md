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
- `db/migrations` — **SQL crudo neutro** (`0001_init.sql` … `0016_color_canon_solo_digitos.sql`).

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
  Akamai), `lefties`, `cacles` (Shopify, `products.json` público). Añadir tienda = añadir entrada
  en el registry.
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

**Un CronJob por tienda**, porque los perfiles divergen: Zara es httpx (1 CPU / 1Gi), Sfera arrastra
Chromium (2Gi, `emptyDir` escribible, `HOME`/`TMPDIR` redirigidos, `runAsUser: 10001`). Comparten
imagen (~900 MB), así que el primer arranque en un nodo nuevo paga ~2m20s de pull.

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
   los valores ya calculados y, obsoleto, devuelve filas equivocadas *sin dar error*.

**Un mismo texto puede significar cosas distintas según la sección**, y estas funciones hoy no lo
saben: `size_canon` lee `25-34` o `20 /21` como rango de EDAD, que es correcto en `ropa` y falso en
`zapateria`, donde son números de pie (78 variantes de Cacles, que estrena esa forma con las
plantillas por rango y el calzado de primeros pasos; el catálogo llega a ofrecer un chip de talla
«48-51 años»). Al ampliar una de estas funciones, la pregunta no es solo qué texto entra sino **en
qué sección entra**. Abierto en #64.

Los límites de cada función están fijados por tests que rompen si alguien los amplía sin decidirlo
(rangos de edad solapados en la talla; familias de color y acentos en el color). Una función puede
además **negar** una etiqueta devolviendo `NULL`: `color_canon` lo hace con un nombre que son solo
dígitos (0016), porque un chip que es un número no lo puede elegir nadie. Cuidado al hacerlo — un
consumidor puede leer ese `NULL` como «cualquier valor»: en `interest.color` significa exactamente
eso, así que el alta rechaza con 400 en vez de guardarlo.

Se decide **midiendo, no intuyendo**, y hay dos escarmientos: en #49 la cautela declarada sobre el
código de tienda se cayó al ver que 9 de sus 11 colisiones eran de Sfera contra sí misma; y en #51,
un límite documentado como imposible («recuperar el nombre exige la PDP de Sfera, tras Akamai»)
resultó estar atribuido a **la tienda equivocada** — eran colores de Zara, cuya API es pública.
Nadie había medido de qué tienda eran las filas. Antes de escribir en el contrato que algo no se
puede, comprobar sobre los datos de quién se está hablando.

### El árbol de categorías de una tienda no es lo que parece

Dos cosas medidas sobre Zara y Sfera que se repiten y conviene dar por supuestas al mapear la
siguiente tienda:

**Los rangos de edad son ramas distintas, y el barefoot vive en la de bebé.** Las dos tiendas
parten el catálogo infantil en 6-14 y mini/bebé, con rutas separadas, y el calzado respetuoso está
sobre todo en la segunda: en Zara **78 de 86** referencias barefoot no se ingerían (#35), en Sfera
**5 de 6** (#33). Mapear solo la rama mayor parece cubrir la tienda y deja fuera el grueso de lo que
este producto existe para encontrar. Y el árbol **no es simétrico** entre rangos: la mayoría de
categorías de ropa de Sfera no existen en bebé.

**Una hoja muerta casi nunca da 404, y cada tienda miente de una forma distinta.** Ya van dos, con
formas opuestas y la misma consecuencia:

- **Sfera responde 200 con el catálogo del padre** a una ruta que no existe (`ninos/nina/loquesea` →
  las 30 páginas de `ninos/nina`). El sondeo de `--check-categories` informa «12 productos, viva», y
  una pasada ingeriría cientos de productos del género entero —ropa incluida— etiquetados con el
  ámbito de la hoja muerta. Se detecta comparando **los ids de la 1ª página contra los del padre**,
  nunca `data.title` (texto localizado de presentación).
- **Cacles/Shopify responde 200 con la lista VACÍA**, que es peor: no mete basura, pero una hoja
  muerta pasa por «este ámbito se ha quedado sin productos», que es exactamente el disparador de una
  baja masiva. Y la misma respuesta es el fin normal de la paginación, así que hay que desambiguarla
  por posición: vacía en la **primera** página es hoja retirada, a partir de la segunda es el final.

En los dos casos las redes de seguridad se apoyan en `GONE_STATUS` y quedan ciegas, sin que
`ScanReport` cuente ninguna caída. **Al añadir una tienda, probar una ruta inventada antes de fiarse
del 404** — es la primera comprobación del recon, no la última.

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

**El reindexado puede dejar `adr_present: false`** y borrar el ADR del grafo sin avisar (visto el
31/07/2026). Por eso el fichero es la fuente de verdad: mirar siempre ese campo en la salida de
`index_repository` y republicar desde `.claude/adr/` si viene en falso.
