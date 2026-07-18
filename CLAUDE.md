# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Phase 1 is in place.** This is a **polyglot monorepo**: scraping/ingestion in **Python**
(`services/scraper`, built), user experience in **Node/TS** (`services/web`, placeholder for
Phase 2+). The two services integrate through the **shared Postgres**, whose schema is the
contract as neutral SQL in `db/migrations`. The `scraper` service owns writes to
`retailer`/`product`/`variant`/`price_history`/`scrape_run`.

Phase 1 delivered: the data schema, a pluggable per-store scraper interface
(`services/scraper/src/scraper/stores/base.py`), the ingestion pipeline with new/delisted
detection (`ingest.py`), the CLI job runner (`python -m scraper.run`), one real scraper
(**Zara**, via public AJAX JSON endpoints), tests (parsing + ingestion), an optimized
`Dockerfile`, and CI (`.github/workflows/scraper-ci.yml`).

Deferred to Phase 2+: the `web` service (API + frontend + Keycloak + Telegram bot), more
store scrapers, fake-discount charts, and the k8s manifests (separate repo).

### Commands (services/scraper)

```bash
just setup                    # venv + editable install with dev deps
just run                      # scrape Zara (applies migrations first)
just dry-run                  # scrape without writing to DB
just check                    # ruff + ruff format --check + mypy + pytest
# or directly:
python -m scraper.run --retailer zara --migrate
```

Config comes from the environment (`.env`, see `.env.example`): `DATABASE_URL` is required;
`TEST_DATABASE_URL` gates the ingestion tests (skipped if unset). Docker image builds with the
build context at the **repo root**: `docker build -f services/scraper/Dockerfile -t deal-tracker-scraper .`.

## What the product is

A platform that automatically tracks deals on **barefoot clothing and footwear for children**, aimed at parents on a tight budget. It scrapes prices from retailers and notifies interested users via a **Telegram bot** when a tracked item drops significantly in price. Users configure which items they want tracked through a web platform.

Key functional requirements from the brief:
- Track by **size** and **model/color** (each may carry a different price depending on the retailer's site).
- Split by **boy/girl**, and separate **clothing** vs **footwear** into clearly distinct sections.
- Clothing has at least 5 categories: pantalones, camisetas, sudaderas/jerseys, vestidos, ropa interior.
- Detect **new and delisted products** so the scraper stops querying items that no longer exist — this requires finding a **stable per-retailer unique product identifier**.
- Store **price history** to later chart price evolution and detect fake/exaggerated discounts.

Target retailers to scrape: Mango Kids, Sfera, H&M, Springfield Kids, Zara, C&A, Hipercor, Lefties. The scraping approach is expected to vary per store and be figured out iteratively — treat per-retailer scrapers as pluggable and expect to work around anti-scraping obstacles.

## Infrastructure decisions already made

These constraints are fixed by the brief — design around them rather than reinventing:

- **Deployment target:** an existing **k3s cluster**. Services run as microservices or a monolith (undecided). Expect scheduled **jobs/cronjobs** that periodically re-scrape and refresh deals.
- **Database:** a **HA Postgres** already running in the cluster. Default to it unless there's a strong reason otherwise. It also holds the price-history time series.
- **Auth/login:** **Keycloak**, already deployed in the cluster.
- **Environments:** `dev local` (local device), `dev`, `qa`, `prod` (the last three on the cluster).
- **CI/CD:** CI via **GitHub Actions**; deployment via **ArgoCD** (already set up in k3s).
- **Repos:** app code lives in a new repo under the GitHub org **liontechsolution**. This repo builds an **artifact/image** to be deployed to the cluster — a heavily-optimized `Dockerfile` belongs here. The Kubernetes manifests live in a **separate** repo (`k3s-local-manifest` or similar, under user `juanjocop`), not here.
- **Cluster access:** `~/.kube/k3slocal.yaml` is the kubeconfig for inspecting the cluster when context is needed.

## Language note

The product brief and domain vocabulary are in Spanish (category names, retailer sections). Preserve Spanish domain terms where they map to real UI/data concepts.
