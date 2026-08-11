# deal-tracker

Rastrea automáticamente ofertas de **ropa y calzado barefoot infantil** y avisa por **Telegram**
cuando una prenda que sigues baja de precio de verdad.

Nació de un problema concreto: la ropa barefoot para niños es cara, está repartida entre tiendas que
no se parecen en nada y las rebajas duran poco. En vez de vigilar nueve webs a mano, dices qué te
interesa y el sistema mira por ti.

## Estado

**En producción desde el 07/08/2026**, en `dealtracker.liontechsolution.com`. Ingiere a diario, con
el trabajo de matching y el vigía encendidos. Hay además un entorno de QA público en
`dealtracker-qa.liontechsolution.com`.

Nueve tiendas, más de 150.000 variantes vivas y su histórico de precios. Las altas de usuario se
hacen a mano: el registro está cerrado.

## Qué hace

- **Seguimiento por prenda, talla y color.** El precio cambia según la variante en casi todas las
  tiendas, así que el aviso se configura sobre la variante y no sobre el modelo.
- **Aviso por Telegram** cuando una prenda seguida baja de forma significativa.
- **Catálogo con sesión.** Navegar el catálogo requiere cuenta; sin ella solo se ve la portada.
- **Filtros que acotan de verdad**: sección (Ropa / Zapatería), niño / niña, categoría, talla, color,
  tienda y rango de precio. Talla, color y tienda admiten varios valores a la vez, y las facetas se
  cruzan con lo que ya has filtrado en lugar de ofrecerte lo que dentro de tu selección no existe.
- **Historial de precios** por variante, con su gráfica en la ficha.
- **Detección de descuentos engañosos.** Guardar el precio a lo largo del tiempo permite contrastar
  el tachado que anuncia la tienda con lo que la prenda ha costado de verdad. Dos tiendas (C&A y
  Springfield) publican además el mínimo de 30 días que exige la directiva Ómnibus.
- **Altas y bajas de catálogo.** El scraper detecta lo que desaparece para dejar de pedirlo, con
  confirmación activa antes de dar una prenda por retirada: una hoja muerta no puede tumbar la pasada.

## Cómo funciona

Monorepo **políglota**, dos servicios que **no se llaman entre sí**:

| | |
|---|---|
| `services/scraper` | **Python.** Rastrea las tiendas e ingiere el catálogo y los precios |
| `services/web` | **Node/TS.** API NestJS + SPA React/Vite + bot de Telegram |

Se integran por la **Postgres compartida**, y el contrato entre ambos es su esquema: la serie
numerada de SQL neutro bajo `db/migrations` (haz `ls`; cualquier número escrito aquí caduca en
semanas). El scraper es dueño de las escrituras de `retailer`, `product`, `variant`, `price_history`,
`scrape_run` y `vigia_run`; el web, de `app_user`, `interest`, `notification` y `job_state`.

Los scrapers son **enchufables**: uno por tienda bajo `services/scraper/src/scraper/stores/`,
registrados por slug en `registry.py`. Cómo sacar los precios no se decidió de antemano — depende de
cada web y de lo que deje hacer.

### Las nueve tiendas

| tienda | por dónde entra |
|---|---|
| **Zara** | endpoints AJAX JSON públicos |
| **Lefties** | la misma API `itxrest` de Inditex que Zara, pero tras Akamai: va por navegador headless |
| **Sfera** | navegador headless — está tras Akamai |
| **Cacles Barefoot** | `products.json` de Shopify. Primera tienda *nativamente* barefoot, así que ahí no se adivina: se declara |
| **C&A** | GraphQL con *persisted query* |
| **Hipercor** | sus propias páginas, no una API: su `robots.txt` veta `/api`, así que se leen el `dataLayer` y el `ld+json` que cada página trae incrustados |
| **H&M** | API REST en `api.hm.com`, fuera del Akamai que guarda la tienda |
| **Mango Kids** | la única que publica su propio árbol de categorías: un endpoint de menú da el `catalogId` que consume el listado |
| **Springfield** | por **sitemap** — su `robots.txt` veta la rejilla SFCC |

## Arrancar en local

Hace falta un **PostgreSQL** y un `DATABASE_URL`; el resto de la configuración sale del entorno
(`.env.example` en la raíz para el scraper, `services/web/.env.example` para el web). Todo lo
`KEYCLOAK_*` y `TELEGRAM_*` es **opcional a propósito**: sin ello la autenticación se apaga y el
trabajo de matching se fuerza a `--dry-run`.

```bash
# scraper (Python) — el justfile de la raíz cubre solo este servicio
just setup                    # venv + instalación editable con dependencias de desarrollo
just run [tienda]             # rastrea (por defecto zara), aplicando migraciones antes
just dry-run [tienda]         # rastrea sin escribir en la base
just check-categories         # sondea las hojas de categoría; falla si alguna ha caducado
just tree [tienda] [raiz]     # el árbol que publica la tienda, marcando lo que ingerimos
just vigia                    # smoke en vivo de todas las tiendas registradas, sin ingerir
just check                    # ruff + ruff format --check + mypy + pytest

# web (Node/TS) — desde services/web
pnpm build:all                # API + frontend
pnpm start:dev                # API en watch
pnpm frontend:dev             # servidor de desarrollo de la SPA
pnpm migrate                  # aplica db/migrations
pnpm lint && pnpm typecheck && pnpm test
```

> **El `typecheck` de `services/web` no mira el frontend**, que es un paquete aparte. El CI sí lo
> comprueba, así que un verde local sin estos dos vale menos de lo que parece:
> `pnpm --filter @deal-tracker/frontend lint` y `... typecheck`.

Dos avisos más sobre el verde local: los tests de ingesta del scraper **se saltan** si no defines
`TEST_DATABASE_URL`, y los del web necesitan además un `TEST_DATABASE_URL_CTYPE_C` —una base con
ctype `C`, como la del cluster, donde `lower()` no baja los acentos y la canonicalización se comporta
distinto—. Sin ellas, buena parte de la suite no llega a ejecutarse.

## Despliegue

Va a un cluster **k3s**: CronJobs por tienda para re-rastrear, más el de matching y el del vigía. La
base es un cluster **CNPG** y el login es **Keycloak**. El CI (GitHub Actions) publica las imágenes y
reescribe el tag en un **repo de manifiestos aparte** (`juanjocop/k3s-local-apps-manifests`), que
**ArgoCD** sincroniza; esos manifiestos no se editan a mano.

Cuatro entornos: `dev local`, `dev` (cada push a `main`), `qa` (semver) y `prod`. **Nada llega a
producción sin un informe de QA en verde**: `release-prod` se niega a promover si
`.claude/qa-reports/<versión>.md` no empieza por `Veredicto: APTO`.

## Más contexto

- [`CLAUDE.md`](./CLAUDE.md) — la guía operativa del repo: qué se rompe de qué manera, qué trampas
  tiene el verde local y qué hay que saber antes de tocar cada cosa.
- **El ADR**, en el MCP `codebase-memory` — la arquitectura y el contrato con el repo de manifiestos.
  Léelo antes de cualquier cambio que cruce servicios o llegue a k8s.
- `/validar-qa` — la skill que valida el entorno de QA de punta a punta (navegador, API, datos y
  cluster) y emite el veredicto que abre la puerta a producción.
