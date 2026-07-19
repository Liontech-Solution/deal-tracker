# Servicio web (Node/TS) — API + SPA

**Experiencia de usuario** de deal-tracker (Fase 2). API en **NestJS** que sirve el catálogo
(lectura de las tablas del scraper), la gestión de **intereses** de seguimiento y la autenticación
con **Keycloak** (OIDC, resource server), **y una SPA React** (en [`frontend/`](frontend)) servida
como estáticos por el propio Nest (imagen única). El punto de integración con el scraper es la
**Postgres compartida**, cuyo esquema vive en [`db/migrations`](../../db/migrations) como contrato
SQL neutro.

> **Estado**: API + catálogo público en React. Ya construido: migraciones del contrato, NestJS +
> Drizzle, OIDC Keycloak, API de catálogo (con orden y facetas) e intereses, **SPA React** con
> Home + Catálogo (filtros) + Detalle (gráfica de historial y etiqueta de descuento honesto),
> Dockerfile multiarch y CI. **Diferido**: login Keycloak en el navegador, Mis seguimientos +
> modal de aviso, Ajustes/Telegram, bot de Telegram y job de matching de ofertas.

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
| GET | `/interests` | JWT | Intereses del usuario. |
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
sección/género/categoría/talla/color/tienda, orden, estados de carga/error/vacío, paginación) y
**Detalle** (variantes talla/color, bloque de precio y **etiqueta de descuento honesto**).

```bash
# Dev: dos procesos. 1) API Nest, 2) SPA con proxy a /api.
pnpm start:dev            # API en :3000
pnpm frontend:dev         # SPA en http://localhost:5173 (proxy /api -> :3000)

# Producción local (imagen única): la API sirve la SPA compilada.
pnpm build:all && pnpm start   # todo en http://localhost:3000
```

> **Descuento honesto**: mientras no exista el job de matching en backend, la clasificación
> "oferta real vs precio inflado" se calcula en cliente (`src/lib/honesty.ts`) a partir del
> historial de precios y el PVP. Está encapsulada para sustituirla por el veredicto del backend.

## Migraciones

Las migraciones son el **SQL neutro** de [`db/migrations`](../../db/migrations), compartido con el
scraper. El migrador del web (`pnpm migrate`) es un espejo del de Python: aplica los ficheros en
orden y registra lo aplicado en la misma tabla `schema_migrations`, de forma **idempotente y
compatible** (cualquiera de los dos servicios puede arrancar el esquema).

## Regla de "bajada significativa" (aún solo como datos)

`interest` lleva ya los parámetros del aviso (`min_discount_pct`, `compare_base`, `window_days`).
El job de matching que los evalúa se implementará más adelante. Default previsto: avisar cuando el
precio caiga por debajo del **mínimo reciente** en `window_days` **y** el descuento frente al
`list_price` **real** supere `min_discount_pct` — nunca contra el precio tachado inflado.

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
