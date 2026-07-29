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
- `db/migrations` — **SQL crudo neutro** (`0001_init.sql` … `0013_scrape_run_message.sql`).

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
  Akamai), `lefties`. Añadir tienda = añadir entrada en el registry.
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

1. Push a `main` → `scraper-ci.yml` / `web-ci.yml` construyen y publican en GHCR con tag `sha-<7>`.
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
- **Drift conocido**: `lefties` está registrado en `stores/registry.py` pero **no tiene CronJob** en
  el repo de manifiestos — nunca se ejecuta en el cluster. Añadir una tienda son dos repos, no uno.

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
