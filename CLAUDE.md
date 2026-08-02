# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Both services are **built and deployed**. This is a **polyglot monorepo**: scraping/ingestion in
**Python** (`services/scraper`), user experience in **Node/TS** (`services/web`, NestJS API +
React/Vite SPA + Telegram bot). The two services never call each other — they integrate through the
**shared Postgres**, whose schema is the contract as neutral SQL in `db/migrations` (`0001` …
`0016`). The `scraper` service owns writes to `retailer`/`product`/`variant`/`price_history`/
`scrape_run`; the `web` service owns `app_user` (Telegram link included)/`interest`/`notification`/
`job_state`.

Running in the cluster today: namespaces `deal-tracker-dev` (auto-deployed on every push to `main`)
and `deal-tracker-qa` (semver releases, public at `dealtracker-qa.liontechsolution.com`).

Retailers implemented: **Zara** (public AJAX JSON endpoints), **Sfera** (headless Chromium — it sits
behind Akamai), **Lefties**, **Cacles Barefoot** (Shopify `products.json`; the first *natively*
barefoot store, so `barefoot='si'` is declared per-store instead of guessed). Still pending from the
brief: Mango Kids, H&M, Springfield Kids, C&A, Hipercor.

> **Deeper architectural context lives in the ADR**, not in this file. Read it before any change
> that crosses services or reaches k8s — it documents the contract between this repo and the
> manifests repo, which neither CLAUDE.md covers in full:
> `manage_adr(project='home-juanjocop-Proyectos-deal-tracker', mode='get')` via the
> `codebase-memory` MCP. There is a companion ADR for
> `home-juanjocop-Proyectos-k3s-local-apps-manifests`.

### Commands

The `justfile` at the repo root covers the **scraper** only; the web service uses `pnpm`.

```bash
# scraper (Python)
just setup                    # venv + editable install with dev deps
just run [retailer]           # scrape (default zara), applies migrations first
just dry-run [retailer]       # scrape without writing to DB
just check-categories         # probe category leaves; fails if any expired (no ingest)
just tree [retailer] [root]   # list the category tree the store publishes, marking what we ingest
just check                    # ruff + ruff format --check + mypy + pytest
just docker-build             # image build (context = repo root)

# web (Node/TS) — from services/web
pnpm build:all                # API + frontend
pnpm start:dev                # API in watch mode
pnpm frontend:dev             # SPA dev server
pnpm migrate                  # apply db/migrations
pnpm job:matching             # deal matching + Telegram notifications
pnpm lint && pnpm typecheck && pnpm test
```

Config comes from the environment (`.env`, see `.env.example` at the root for the scraper and
`services/web/.env.example` for the web). `DATABASE_URL` is required by both. `TEST_DATABASE_URL`
gates the scraper's ingestion tests — they are **skipped** if unset, so a green `just check` without
it proves less than it looks.

Everything `KEYCLOAK_*` and `TELEGRAM_*` is **optional by design**: with them unset the auth is off
(the SPA works as a public catalog, user endpoints return 401) and the matching job forces
`--dry-run`. That is exactly how `dev` runs.

Docker images build with the build context at the **repo root**:
`docker build -f services/scraper/Dockerfile -t deal-tracker-scraper .`

## What the product is

A platform that automatically tracks deals on **barefoot clothing and footwear for children**, aimed
at parents on a tight budget. It scrapes prices from retailers and notifies interested users via a
**Telegram bot** when a tracked item drops significantly in price. Users configure which items they
want tracked through a web platform.

Key functional requirements from the brief:
- Track by **size** and **model/color** (each may carry a different price depending on the retailer's site).
- Split by **boy/girl**, and separate **clothing** vs **footwear** into clearly distinct sections.
- Clothing has at least 5 categories: pantalones, camisetas, sudaderas/jerseys, vestidos, ropa interior.
- Detect **new and delisted products** so the scraper stops querying items that no longer exist — this requires finding a **stable per-retailer unique product identifier**.
- Store **price history** to later chart price evolution and detect fake/exaggerated discounts.

The scraping approach varies per store and is figured out iteratively — per-retailer scrapers are
pluggable (`services/scraper/src/scraper/stores/base.py`, registered by slug in `registry.py`) and
expect to work around anti-scraping obstacles.

## Working on this repo

**Delisting is adversarial and deliberately conservative.** Retailer IDs die, category leaves start
returning 404, and a dead leaf must not take down the whole pass. The safety nets are tunable via
`SCRAPER_DELIST_*` and `SCRAPER_SCAN_MAX_DEAD_RATIO` (see `.env.example`). Don't tighten them
without understanding which failure they absorb.

**A 429 is not proof that you asked for too much.** Cacles' Cloudflare had *httpx's TLS fingerprint*
rate-limited: every httpx request got `429 local_rate_limited` — from the cluster too — while curl,
wget and urllib got 200 from the same IP with byte-identical headers. The culprit was the ALPN
extension, and httpcore forces it onto whatever SSL context you hand it, so `scraper/tls.py` exists
to strip it. Before blaming your own rate, **reproduce the request with curl**: it costs seconds and
separates the two hypotheses. The measured detail lives in the ADR.

**Ingestion is atomic.** A pass either commits fully or rolls back. A cold pass against Zara is
~30 min (2219 products / 25623 variants); steady state is ~1m35s thanks to conditional detail
fetching by signature. This is why the cluster CronJob carries a generous `activeDeadlineSeconds`
and only one retry.

**The Drizzle schema is a mirror, not the source of truth.** `db/migrations` is. Three places can
drift silently: the SQL migrations, `services/web/src/database/schema.ts`, and the raw SQL in
`ingest.py`. The `revisor-contrato-esquema` subagent exists for exactly that — use it on any change
touching `db/migrations/**` or `schema.ts`.

**Migrations have two appliers, both idempotent**: the scraper (`--migrate`) and the web
(`pnpm migrate` / `node dist/database/migrate.js`, which runs as an initContainer in the cluster).
Neither owns them.

**Adding a retailer touches two repos.** The scraper entry in `registry.py` is not enough — without
a CronJob in the manifests repo it never runs in the cluster. The `nueva-tienda` skill walks the
whole flow, and `revisor-robustez-scraper` audits what fixture tests can't catch.

**Local verification does not need the cluster.** A throwaway Postgres in Docker covers the
ingestion tests and a real full pass. The dev cluster is only for verifying **deployment**, and that
does require merging to `main` (CI only publishes images on push to main).

## Infrastructure

These are facts about the running system, not plans:

- **Deployment target:** an existing **k3s cluster**. Scheduled CronJobs re-scrape per retailer
  (one job per store — the profiles diverge: Zara is light httpx, Sfera needs 2Gi for Chromium) plus
  a daily matching job. They ship `suspend: true` by default, so in dev a pass is fired by hand:
  `kubectl -n deal-tracker-dev create job <name> --from=cronjob/deal-tracker-scraper-<slug>`.
- **Database:** the **CNPG** cluster `platform-postgres-dev` in namespace `data-dev` — *not* the
  cluster's `postgresql-generic`. It also holds the price-history time series.
- **Auth/login:** **Keycloak**, already deployed in the cluster. The web service is a resource
  server: it validates access JWTs against the realm JWKS.
- **Environments:** `dev local`, `dev`, `qa` (`prod` not yet stood up).
- **CI/CD:** GitHub Actions (`scraper-ci.yml`, `web-ci.yml`, `release-qa.yml`) build and publish
  `ghcr.io/liontech-solution/deal-tracker-{scraper,web}`, then a `bump` job rewrites the image tag
  in the manifests repo; **ArgoCD** syncs it. dev tracks `sha-<7>` automatically; QA tracks semver
  via the manual `release-qa` workflow, which promotes **by digest** (no rebuild). PRs only validate
  on amd64 — the **multiarch build runs on `main`** (#61), so a PR check being green does not mean
  the arm64 image exists yet.
- **Manifests live in a separate repo:** `juanjocop/k3s-local-apps-manifests`, under
  `deal-tracker/{base,overlays/dev,overlays/qa}`. The `images[].newTag` values there are
  **machine-edited — never hand-edit them**. ArgoCD runs with `selfHeal: true`, so a `kubectl patch`
  against the cluster gets reverted; cluster changes go through that repo.
- **Cluster access:** `~/.kube/k3slocal.yaml` is the kubeconfig for inspecting the cluster.

## Language note

The product brief and domain vocabulary are in Spanish (category names, retailer sections). Preserve
Spanish domain terms where they map to real UI/data concepts. Code comments and commit messages in
this repo are in Spanish.
