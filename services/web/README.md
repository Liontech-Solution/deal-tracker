# Servicio web (Node/TS) — API + SPA

**Experiencia de usuario** de deal-tracker (Fase 2). API en **NestJS** que sirve el catálogo
(lectura de las tablas del scraper), la gestión de **intereses** de seguimiento y la autenticación
con **Keycloak** (OIDC, resource server), **y una SPA React** (en [`frontend/`](frontend)) servida
como estáticos por el propio Nest (imagen única). El punto de integración con el scraper es la
**Postgres compartida**, cuyo esquema vive en [`db/migrations`](../../db/migrations) como contrato
SQL neutro.

> **Estado**: API + SPA React con **experiencia autenticada**. Ya construido: migraciones del
> contrato, NestJS + Drizzle, OIDC Keycloak (resource server), API de catálogo (con orden y
> facetas) e intereses (enriquecidos), **login Keycloak en el navegador** (OIDC + PKCE con
> `keycloak-js`), **modal de seguimiento** y página **Mis seguimientos**, Home + Catálogo
> (filtros) + Detalle (gráfica de historial y etiqueta de descuento honesto), Dockerfile multiarch
> y CI, **Ajustes/Telegram**, el **bot de Telegram** (long-polling, apagado en `dev`: se activa en
> `qa`), el **job de matching de ofertas** y el **veredicto de descuento honesto como campo del
> catálogo** (calculado en backend con la misma regla que el aviso — ver §"Descuento honesto").

Este servicio es un **workspace pnpm**: la raíz es la API y [`frontend/`](frontend) es la SPA
(Vite + React + TS). El detalle del frontend está en la sección **Frontend (SPA)** más abajo.

Reparto de responsabilidad sobre las tablas:

- `scraper` (Python) → escribe `retailer`, `product`, `variant`, `price_history`, `scrape_run`.
- `web` (Node/TS) → dueño de `app_user`, `interest`, `notification`.

## Stack

- **NestJS 11** (API) · **Drizzle** sobre **postgres.js** (acceso a datos tipado; el esquema lo
  poseen las migraciones SQL, no drizzle-kit) · **passport-jwt + jwks-rsa** (validación del JWT de
  Keycloak) · **Vitest** (tests) · **pnpm** (gestor de paquetes, endurecido — ver abajo).

## Requisitos

- **Node 22+** y **pnpm** (vía Corepack: `corepack enable`).
- Una Postgres accesible por `DATABASE_URL`.

## Puesta en marcha (dev local)

```bash
cd services/web
corepack enable
pnpm install --frozen-lockfile
cp .env.example .env         # ajusta DATABASE_URL / Keycloak
pnpm build && pnpm migrate   # aplica db/migrations (idempotente, compatible con el scraper)
pnpm start:dev               # API en http://localhost:3000/api
```

## Comandos

```bash
pnpm lint          # ESLint
pnpm typecheck     # tsc --noEmit
pnpm test          # Vitest (integración contra Postgres si TEST_DATABASE_URL está definido)
pnpm build         # nest build -> dist/ (solo API)
pnpm build:all     # API + SPA (nest build + vite build de frontend)
pnpm migrate       # aplica db/migrations/*.sql (node dist/database/migrate.js)
pnpm job:matching  # evalúa bajadas y avisa por Telegram (acepta --dry-run)
pnpm frontend:dev  # server de Vite (SPA) con proxy /api -> :3000
pnpm frontend:build# compila la SPA a frontend/dist
pnpm audit --audit-level=high
```

Config por entorno (ver [`.env.example`](.env.example)): `DATABASE_URL` (requerido),
`KEYCLOAK_ISSUER_URL`, `KEYCLOAK_AUDIENCE`, `PORT`, `WEB_MIGRATIONS_DIR`.

## API (prefijo `/api`)

| Método | Ruta | Auth | Descripción |
| --- | --- | --- | --- |
| GET | `/health` | — | Liveness/readiness (incluye ping a la BD). |
| GET | `/catalog/products` | — | Lista con filtros `gender, section, category, size, color, retailer, inStock, activeOnly`, orden `sort` (`ofertas`\|`precio-asc`\|`precio-desc`\|`descuento`) + `limit/offset`. Cada ítem trae `priceFrom`, `listFrom`, `discountFrom` (de la variante mejor oferta) y `maxDiscount`. |
| GET | `/catalog/products/:id` | — | Producto + variantes con su último precio. |
| GET | `/catalog/variants/:id/price-history` | — | Serie temporal de precios (base de las gráficas). |
| GET | `/catalog/facets` | — | Valores distintos de género/sección/categoría/talla/color y tiendas (para poblar filtros). |
| GET | `/interests` | JWT | Intereses del usuario, **enriquecidos** con `retailerName`/`productName`/`variantLabel` (resueltos por id lógico cuando apuntan a un objetivo; `null` si no). |
| POST | `/interests` | JWT | Crea un interés (por producto/variante y/o filtros + regla de aviso). |
| DELETE | `/interests/:id` | JWT | Borra un interés propio. |

El catálogo es **público** (browsing sin login); los intereses exigen un **JWT de Keycloak**
(`Authorization: Bearer`). En la primera petición autenticada el usuario se **aprovisiona JIT** en
`app_user` a partir del `sub` del token.

## Frontend (SPA)

SPA en [`frontend/`](frontend): **Vite + React + TypeScript** (sin librería de UI; tokens del
diseño "barefoot" portados a variables CSS en `src/styles/app.css`, claro/oscuro). Datos con
**TanStack Query** contra `/api`. Fuentes self-host (`@fontsource`), iconos SVG propios, gráfica
de historial de precios en SVG. Pantallas de esta fase: **Home**, **Catálogo** (filtros por
sección/género/categoría/talla/color/tienda, orden, estados de carga/error/vacío, paginación),
**Detalle** (variantes talla/color, bloque de precio y **etiqueta de descuento honesto**),
**modal de seguimiento** y **Mis seguimientos** (`/seguimientos`).

**Fotos de producto** (`components/ProductImage.tsx`): se sirven por **hotlink** al CDN de la
tienda desde `product.image_url` (`imageUrl` en la API), pidiendo el ancho que hace falta
(`&w=563` en la tarjeta, `&w=1024` en el detalle: la foto completa pesa ~124 KB y a 563 px baja a
~10 KB). Sin proxy ni almacenamiento propio: el tráfico de imágenes no pasa por el pod. Si el
producto aún no tiene foto —Sfera todavía no la da— o la carga falla, se cae al placeholder de
rayas del diseño (`lib/section.ts`).

```bash
# Dev: dos procesos. 1) API Nest, 2) SPA con proxy a /api.
pnpm start:dev            # API en :3000
pnpm frontend:dev         # SPA en http://localhost:5173 (proxy /api -> :3000)

# Producción local (imagen única): la API sirve la SPA compilada.
pnpm build:all && pnpm start   # todo en http://localhost:3000
```

### Login con Keycloak (navegador)

La SPA usa **`keycloak-js`** con **OIDC + PKCE** (`src/auth/keycloak.ts` + `AuthProvider`). El
access token se adjunta como `Authorization: Bearer` en las llamadas a `/interests` y se refresca
antes de expirar. Config por variables `VITE_*` (ver [`frontend/.env.example`](frontend/.env.example)):

| Variable | Descripción |
| --- | --- |
| `VITE_KC_URL` | URL base de Keycloak (con barra final). |
| `VITE_KC_REALM` | Realm. |
| `VITE_KC_CLIENT_ID` | Client-id público de la SPA (PKCE, sin secreto). |

**Modo placeholder (dev local):** si las tres variables están vacías, la auth queda
**deshabilitada** — la app funciona como catálogo público y "Iniciar sesión"/"Seguir" muestran un
aviso. La conexión real con Keycloak se **valida al desplegar en el cluster (namespace `dev`)**,
donde el realm y el client existen; allí las `VITE_*` se inyectan en el build y la config del
resource server (`KEYCLOAK_ISSUER_URL`/`KEYCLOAK_AUDIENCE`) llega como `Secret`.

> **Descuento honesto**: la clasificación "oferta real vs precio inflado" la calcula el **backend**
> como campo del catálogo (`ProductListItem.honesty` y `VariantWithPrice.honesty`), reutilizando la
> **misma función pura** que el aviso (`classifyHonesty` sobre `evaluateDeal`, en
> [`src/matching/deal-rule.ts`](src/matching/deal-rule.ts)) — así el catálogo y el aviso de Telegram
> nunca se contradicen. `real` = el job avisaría (mínimo reciente con rebaja honesta); `suspicious` =
> la tienda muestra un tachado que no es una bajada real (PVP inflado o no es mínimo reciente);
> `none` = sin tachado o **sin histórico** con el que corroborar (arranque en frío → sin etiqueta).
> La ventana del "mínimo reciente" del catálogo es `HONESTY_WINDOW_DAYS` (90 días).

## Bot de Telegram

El bot (`src/telegram/`) hace dos cosas muy distintas, y por eso no necesita ni proceso ni
manifiesto propio:

- **Enviar avisos** (`TelegramApiClient.sendMessage`) es una llamada HTTP saliente sin estado. La
  usará el job de matching; no requiere ningún proceso vivo.
- **Canjear `/start <token>`** (`TelegramPollingService`) sí necesita un bucle persistente, que
  vive **dentro del proceso de la API** por long-polling.

Es la contrapartida del deep-link que emite `POST /api/settings/telegram/link`: el usuario pulsa
«Start», el bot recibe el token, lo cambia por su `chat_id` (un solo uso, 15 min de validez) y la
SPA lo ve en el siguiente tick de su auto-poll. Se usa long-polling y no webhook porque `dev` no
tiene hostname público con TLS.

| Variable | Efecto |
| --- | --- |
| `TELEGRAM_BOT_USERNAME` | Nombre del bot (sin `@`), para el deep-link. Sin ella, `POST /link` → 503. |
| `TELEGRAM_BOT_TOKEN` | Token de BotFather. Sin él no se envía nada: los avisos quedan en el log. |
| `TELEGRAM_POLLING_ENABLED` | `true` enciende el bucle `getUpdates`. Apagado por defecto. |

**Modo placeholder (dev):** sin `TELEGRAM_BOT_TOKEN` el bot no arranca y nada cambia de
comportamiento. El bot **se activa a partir de `qa`**, donde el token llega como `SealedSecret`.

> ⚠️ `getUpdates` no admite dos consumidores simultáneos: con `TELEGRAM_POLLING_ENABLED=true` el
> Deployment del web debe quedarse en **replica 1**.

## Migraciones

Las migraciones son el **SQL neutro** de [`db/migrations`](../../db/migrations), compartido con el
scraper. El migrador del web (`pnpm migrate`) es un espejo del de Python: aplica los ficheros en
orden y registra lo aplicado en la misma tabla `schema_migrations`, de forma **idempotente y
compatible** (cualquiera de los dos servicios puede arrancar el esquema).

## Job de matching y regla de "bajada significativa"

`pnpm job:matching` (o `node dist/jobs/matching.job.js`) evalúa los precios recién scrapeados
contra los `interest` de los usuarios y manda **un resumen por Telegram** a quien tenga una bajada
real. Corre como **CronJob de k3s** después del scraper, sobre la misma imagen del web.

La regla (`src/matching/deal-rule.ts`, función pura) para cada precio nuevo de una variante en
stock, siendo `prior` sus observaciones anteriores:

```
recentMin    = MIN(price) de prior dentro de window_days
maxObservado = MAX(price) de prior
pvpHonesto   = listPrice     si listPrice <= maxObservado * 1.03
               maxObservado  en caso contrario   ← el precio tachado está inflado

Avisa (compare_base='recent_min', el default) si:
   A) price < recentMin                       → es un mínimo nuevo de verdad
   B) descuento(price vs pvpHonesto) >= min_discount_pct

compare_base='list_price' → solo la condición B.
```

**Nunca se mide contra el precio tachado a ciegas.** Si la tienda declara un PVP por encima de lo
que la prenda ha costado jamás, ese tachado se descarta y vale el máximo realmente observado.

> **Arranque en frío.** Una prenda **descubierta ya rebajada** no tiene con qué corroborar su PVP.
> Ahí no se cae de vuelta al precio de la tienda: sin histórico no hay referencia, `pvpHonesto`
> colapsa al precio actual y el descuento sale 0 — **silencio**. Anunciar un "-60 %" que no podemos
> verificar sería repetir el engaño que este producto existe para delatar. La prenda sí avisará más
> adelante, cuando haya subido y vuelto a bajar, que es cuando la rebaja es demostrable.

### Garantías de entrega

- **Incremental** por marca de agua (`job_state`, migración 0007): solo mira
  `price_history.scrape_run_id > last_scrape_run_id`. Guardar el mayor id procesado —en vez de "el
  último run"— recupera el hueco si una ejecución se pierde, y hay un `scrape_run` **por tienda**.
- **Idempotente**: se reserva la fila en `notification` (UNIQUE
  `interest_id, variant_id, price_event_key`) **antes** de enviar, así un reintento no duplica.
- **Sin avisos perdidos**: si el envío falla se **suelta la reserva** y **no avanza la marca de
  agua**, de modo que el siguiente intento lo reevalúa. El job sale con código ≠ 0 para que el Job
  de k8s lo reintente. Prioriza un duplicado raro sobre un silencio permanente.
- Quien **no tiene Telegram vinculado** no genera fila: recibirá la próxima bajada en lugar de
  quemar el evento en el vacío.

| Variable | Efecto |
| --- | --- |
| `DATABASE_URL` | Requerido. |
| `TELEGRAM_BOT_TOKEN` | Sin él se **fuerza `--dry-run`**: no hay forma de avisar. |

`--dry-run` registra qué avisos habría mandado y **no cambia nada** (ni `notification` ni marca de
agua). Es lo que corre en `dev`, donde el bot está apagado.

## Endurecimiento de cadena de suministro (pnpm)

Fijado por [`.npmrc`](.npmrc) y `package.json`:

- **pnpm** pinneado con Corepack (`packageManager`), siempre `--frozen-lockfile`.
- **Scripts de ciclo de vida bloqueados** salvo allowlist explícita (`pnpm.onlyBuiltDependencies`).
- **Cooldown** de versiones (`minimum-release-age=1440`): no instala publicaciones de menos de 1 día.
- Registro oficial pinneado; `pnpm audit` en CI.

## Docker

```bash
# Contexto = raíz del repo (la imagen necesita db/migrations).
docker build -f services/web/Dockerfile -t deal-tracker-web .
```

Imagen multiarch (`linux/amd64` + `linux/arm64`) publicada por CI en
`ghcr.io/liontech-solution/deal-tracker-web`. El migrador se invoca aparte
(`node dist/database/migrate.js`) como initContainer/job en el despliegue.
