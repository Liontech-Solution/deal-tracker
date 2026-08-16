# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Both services are **built and deployed**. This is a **polyglot monorepo**: scraping/ingestion in
**Python** (`services/scraper`), user experience in **Node/TS** (`services/web`, NestJS API +
React/Vite SPA + Telegram bot). The two services never call each other — they integrate through the
**shared Postgres**, whose schema is the contract as the numbered series of neutral SQL under
`db/migrations` (`ls` it — a range written here goes stale every few weeks). The `scraper` service
owns writes to `retailer`/`product`/`variant`/`price_history`/`scrape_run`/`vigia_run`; the `web`
service owns `app_user` (Telegram link included)/`interest`/`notification`/`job_state`.

Running in the cluster today: namespaces `deal-tracker-dev` (auto-deployed on every push to `main`)
and `deal-tracker-qa` (semver releases, public at `dealtracker-qa.liontechsolution.com`).

Retailers implemented: **Zara** (public AJAX JSON endpoints), **Sfera** (headless Chromium — it sits
behind Akamai), **Lefties**, **Cacles Barefoot** (Shopify `products.json`; the first *natively*
barefoot store, so `barefoot='si'` is declared per-store instead of guessed), **C&A** (GraphQL
persisted query; the first to publish the Ómnibus 30-day minimum, though Springfield populates it
far more), **Hipercor** (headless
Chromium, and the first store scraped **through its own pages** rather than an API: its `robots.txt`
disallows `/api`, so the listing and the product sheet are read from the `dataLayer` and the
`ld+json` each page embeds), **H&M** (REST API on `api.hm.com`, outside the Akamai that guards
the storefront, so plain `httpx` gets in), **Mango Kids** (the only store that *publishes its own
category tree*: a public menu endpoint hands out the `catalogId` the listing API consumes, so no leaf
is guessed), **Springfield** (the first listed by **sitemap** — its `robots.txt` bans the SFCC
grid — which also hands over `lastmod` for free, though measuring it two days apart showed it is a
batch stamp of the sitemap generator and worthless as a signature, #227) and **Deditos Barefoot**
(WooCommerce **Store API**, and the second *natively* barefoot store, so `barefoot='si'` is declared
per-store like Cacles — a premise that was **re-measured before being coded**: 92 of its 431
children's products are conventional brands (Mustang, Joma, Gioseppo), but 88 of those 92 are the
store's own barefoot lines, named as such). Springfield closed the retailer list from the brief;
Deditos is catalogue on top of it.

Deditos is the one store where **there is no cheap fingerprint at all**. Its listing publishes
neither price nor stock per size, and its `price_range` is null on 276 of 431 products *including*
ones whose sizes differ in price — measured. So `list_catalog()` fetches every product sheet and
`fetch_details()` serves from cache (the Cacles shape, for the opposite reason), and conditional
detail saves nothing here by design. Two more traps live in its header: prices arrive in minor
units from the API and as serialized PHP floats from the sheet, and one sheet carries 5-9
`variations_form` blocks — the related products' — so the right one is picked by `data-product_id`,
never by position.

H&M is the only store so far whose **dead leaf is invisible**: an unresolvable `pageId` returns 200
with a full, plausible page — the whole `categoryId` bucket. It is detected with a *canary*: one
deliberately invented route per pass, whose first page is compared against each leaf's. It is also
the only one where **a listing row is a product+colour, not a product**, so `list_catalog()`
accumulates the pass before emitting instead of streaming page by page — which is also what lets it
mark as `unisex` the ~10 % of models the store publishes under both genders (the bug #98 found in
Hipercor).

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
just vigia                    # live smoke of every registered store (leaves + parse), no ingest
just check                    # ruff + ruff format --check + mypy + pytest
just docker-build             # image build (context = repo root)

# web (Node/TS) — from services/web
pnpm build:all                # API + frontend
pnpm start:dev                # API in watch mode
pnpm frontend:dev             # SPA dev server
pnpm migrate                  # apply db/migrations
pnpm job:matching             # deal matching + Telegram notifications
pnpm lint && pnpm typecheck && pnpm test
# CAREFUL: the tsconfig of services/web only includes src/ and test/, so that typecheck does NOT
# cover the frontend. Its own is a separate package, and CI does run it (web-ci.yml) — without
# these two, a green local run means less than it looks:
pnpm --filter @deal-tracker/frontend lint && pnpm --filter @deal-tracker/frontend typecheck
```

Validation of the deployed QA environment is a Claude Code skill, not a `just` recipe — it drives a
browser, the API, the cluster and the QA database at once:

```
/validar-qa                   # full sweep of QA + verdict APTO / NO APTO / NO CONCLUYENTE
/validar-qa rapida            # no cluster jobs, ~20 min; never grants APTO
/validar-qa --frente datos    # a single front (ui | api | datos)
```

Config comes from the environment (`.env`, see `.env.example` at the root for the scraper and
`services/web/.env.example` for the web). `DATABASE_URL` is required by both. `TEST_DATABASE_URL`
gates the scraper's ingestion tests — they are **skipped** if unset, so a green `just check` without
it proves less than it looks. The web has a **second** one, `TEST_DATABASE_URL_CTYPE_C`, and it is
the same trap one level deeper: the cluster's database is `UTF8 | C | C`, and with ctype `C`
`lower()` does not lowercase accented letters, so `size_canon`, `color_canon` and the search fold
behave differently there than under CI's locale. Without that second database those specs skip and
everything looks green (#105). Create it on the same server:
`CREATE DATABASE deal_tracker_ctype_c TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';`

Everything `KEYCLOAK_*` and `TELEGRAM_*` is **optional by design**: with them unset the auth is off
(the SPA works as a public catalog, user endpoints return 401) and the matching job forces
`--dry-run`. That is exactly how `dev` runs.

Which means the **catalogue lock cuts both ways**. Since v0.3.0 (#309) the four catalogue endpoints
require a session and the SPA sends anonymous visitors to `/acceso` — but only *where Keycloak is
configured*, because `CatalogAuthGuard` and `RequireSession` both hinge on `isAuthConfigured()`. In
`dev`, which deliberately drops the `KEYCLOAK_*`, the catalogue stays wide open. So **a green dev
proves nothing about access**: that is only observable in QA and prod. The home is public either
way, but with no session it shows neither products nor the retailer count.

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

**And the thing that protects a candidate is `blocked_ids`, never its absence from a list.**
`_delist()` does not delete from the candidate list `_confirm_candidates` built — it deletes with
its **own** `WHERE` (`missing_streak >= min_misses`), excluding only the ids in `blocked_ids`. So
filtering a product out of `_load_delist_candidates` doesn't spare it: it **delists it without ever
asking the store**. That is the whole reason the cooldown of #412 loads its candidates anyway and
puts them in `blocked_ids` — what it saves is the network request, never the confirmation. If you
ever add a reason to skip probing something, it goes in `blocked_ids` too, and
`test_el_excluido_por_la_ventana_sigue_protegido_de_la_baja` is the net that catches you.

**A store can break in a way that makes everything look sold out**, and `UNBUYABLE` deliberately
doesn't count as an error (#197), so that failure used to be silent. The discriminator is outside
the probe: `scrape_run.variants_in_stock` (#427). If the stock parser breaks it isn't the
candidates that come back empty — it's the **whole catalogue**, and that is unambiguous. Measured
over ~60 QA passes, the least-stocked pass ever recorded has 7 in-stock variants out of 55 written,
so the alarm fires on **zero** and needs no invented threshold — but it also demands
`variants_seen > 0`, because a pass where no signature changed writes nothing and would otherwise
read exactly like a broken parser.

**A 429 is not proof that you asked for too much.** Cacles' Cloudflare had *httpx's TLS fingerprint*
rate-limited: every httpx request got `429 local_rate_limited` — from the cluster too — while curl,
wget and urllib got 200 from the same IP with byte-identical headers. The culprit was the ALPN
extension, and httpcore forces it onto whatever SSL context you hand it, so `scraper/tls.py` exists
to strip it. Before blaming your own rate, **reproduce the request with curl**: it costs seconds and
separates the two hypotheses. The measured detail lives in the ADR.

**Ingestion is atomic.** A pass either commits fully or rolls back. A cold pass against Zara is
~58 min (4644 products / 47426 variants, measured 14/08/2026); steady state is ~1m35s thanks to
conditional detail fetching by signature. **That first figure tracks the catalogue, so it carries a
date**: it read ~30 min / 2219 products until the store's catalogue doubled under it, and nothing
said so. This is why the cluster CronJob carries a generous `activeDeadlineSeconds` and only one
retry. And #356 moved it again on purpose — mapping Zara's sale branch takes the listing from 62
leaves to **95** and the catalogue from 4778 to **5507 products** (measured 16/08/2026), so the next
cold pass will be longer than that ~58 min and the figure above is due a re-measure.

**A parent category leaf is not proof of what hangs below it.** Zara's `REBAJAS` leaf for boys
serves **8** products while its own by-age children serve **749**; the girls' one under-serves by 41.
Lefties had the same shape (parent 77/69, children 93/99) and it cost 46 garments. So when a branch
is worth ingesting, measure the parent **against the union of its children** before believing either
— and note it cuts both ways: H&M's `sportswear` parent is exactly complete (81 = 81 over its 11
children), which is what turned #208 from ~50 requests per pass into 4.

**The Drizzle schema is a mirror, not the source of truth.** `db/migrations` is. Three places can
drift silently: the SQL migrations, `services/web/src/database/schema.ts`, and the raw SQL in
`ingest.py`. The `revisor-contrato-esquema` subagent exists for exactly that — use it on any change
touching `db/migrations/**` or `schema.ts`.

**A function reachable from an index expression must pin its `search_path`.** `variant` carries
three expression indexes over our own functions (`size_canon`, `color_family`, `size_band`), and
**`ANALYZE` evaluates those expressions** while `VACUUM` does not. Autovacuum workers run with the
`search_path` deliberately emptied, so a function body that calls another one *unqualified* — as
`color_family` did with `color_canon` — blows up mid-analyze and the whole thing aborts. The vacuum,
already done, still gets reported. Net effect: the table is vacuumed forever and never analyzed, its
statistics rot, and the catalogue runs 2-2,5× slower with **nothing** in any log
(`log_autovacuum_min_duration` defaulted to 10 min, so even the failure was invisible). That was
#370, and it hid for months. Migration `0037` pins `search_path` on all of them; if you add another
expression index, its function goes in that list.

**The honesty rule is mirrored too, and the mirror is load-bearing.** `classifyHonesty()` in
`services/web/src/matching/deal-rule.ts` labels the card, but filtering (`onlyDeals`) and sorting
(`sort=ofertas`) have to decide **before the `LIMIT`**, so `deal-rule.sql.ts` reimplements it in SQL.
The two go **in the same commit**: a change to one compiles fine and lies.
`INFLATED_LIST_MARGIN` is the one thing that can no longer drift — since #375 the SQL side imports
it instead of repeating the literal — and that fix came from measuring how blind the net was: with
the margin moved on one side only, all 725 tests of the service passed. What the parity spec
compares now is three things, because **the `real` verdict is the least sensitive of them**: the
margin cannot move `real` on any row the database can actually produce, so it is the credible RRP
and the honest discount (which orders `sort=ofertas`) that see a divergence. The
`revisor-espejo-honestidad` subagent catches it before the fact, and is what you use on any change
touching either file, their consumers in `catalog.service.ts`, or its `stats` CTEs. Since #436 the
parity spec runs a **seventh axis** (`trackedDays`) and its cartesian no longer fits in one
round-trip — drizzle builds the query recursively and blows the stack — so it goes in batches of
5,760. Cutting cases to avoid that would pay for the convenience with the very coverage the spec
exists to have.

**And the rule now has an input that is not ours** (#354). C&A and Springfield publish the 30-day
minimum the Ómnibus directive obliges them to (`price_history.retailer_min_30d`, there since `0018`
and unread by anything until now). It enters as a **ceiling** on the credible RRP — never as a
reference on its own — and opens a second path to `suspicious` that **does not wait for
`HONESTY_EVIDENCE_DAYS`**, because the evidence is the store contradicting itself rather than our
own thin series. Two consequences worth knowing before reading a catalogue: the blackout #332
imposed until ~05/11/2026 (prod) **no longer covers those two stores** — 291 variants of 89 products
were already accusable in QA on 14/08/2026, a third of them on the first pass we ever see them — and
the accusation text differs by basis (`honestyBasis`), because saying "inflated against its history"
about a garment discovered yesterday would be false. What does *not* move is `real`: the ceiling can
only lower the credible RRP, so it can only remove a deal, never invent one.

**And since #436 the praise has a threshold too, which is what `real` never had.** #332 put one on
the accusation (`HONESTY_EVIDENCE_DAYS`, 90 d) and left the other side open, so the catalogue
stamped "Oferta real" off **one** prior observation — 176 of the 246 badged products in QA on
16/08/2026, all 246 under 90 days. `REAL_EVIDENCE_DAYS` (14 d) now gates it, and below that the drop
falls to a **third verdict, `reciente`** ("Bajada reciente"), which is the same drop with a label
that claims less. The two numbers are deliberately different because they assert different things —
accusing needs to have seen the garment outside its sale season, praising only needs the series it
compares against not to be a single point — and 14 is measured, not picked: of those 246, 238 survive
a 3-day bar, 212 a 7-day one, **26** a 14-day one and **0** a 30-day one. Three things worth knowing
before reading the catalogue: `onlyDeals` and `sort=ofertas` stay **strict** on `real` (so the home
rail shows its already-written empty state for weeks, on purpose), the **Telegram alert does not
move** — `evaluateDeal` still never looks at coverage, so this changed what the catalogue *claims*,
not who gets notified — and the card no longer paints a discount green once the rule has thrown out
the store's strikethrough, which was the other half of #436 and hit 88 of those 246.

**Migrations have two appliers, both idempotent**: the scraper (`--migrate`) and the web
(`pnpm migrate` / `node dist/database/migrate.js`, which runs as an initContainer in the cluster).
Neither owns them.

**Adding a retailer touches two repos.** The scraper entry in `registry.py` is not enough — without
a CronJob in the manifests repo it never runs in the cluster. The `nueva-tienda` skill walks the
whole flow, and `revisor-robustez-scraper` audits what fixture tests can't catch. The **vigía** is
the exception that needs no second repo: it iterates `available_slugs()` and its CronJob names no
stores, so registering a retailer watches it.

**A store breaks in two ways and only one is visible.** That the store *changed* shows up in the
pass summary; that the store stopped *letting us in* is silent — the Cacles TLS fix leans on an
httpcore internal, and a bump that breaks it brings back a 429 that reads like something else. So
`python -m scraper.vigia` (`just vigia`) sweeps every registered store weekly from the cluster:
category leaves plus a generic parse smoke on 5 products, opening one GitHub issue when it finds
something actionable. It runs **in the cluster and not in CI** on purpose — the question is whether
the stores let *us* in, and a GitHub runner exits from a different IP with a different reputation.
Two consequences worth knowing: it is the only CronJob shipping `suspend: false` in `base` (a paused
watchdog is the problem it exists to solve), and `check_leaves()` is effectively mandatory —
`just check` fails for a registered store that lacks it.

**But only one environment actually runs it, and since 2026-08-07 that is `prod`.** The three
namespaces share the cluster and leave for the internet through the **same public IP**, so a second
vigía is double the requests for zero extra signal. `dev` and `qa` both override `base` to
`suspend: true`; the reasoning is written out in `overlays/qa/patch-vigia.yaml` in the manifests
repo. Reading a suspended vigía in `qa` as a broken watchdog is a false alarm that has already been
made once — to sweep from there on purpose (after touching `tls.py`, say, or bumping httpx), fire it
by hand: `kubectl -n deal-tracker-qa create job vigia-manual --from=cronjob/deal-tracker-vigia`.

**Local verification does not need the cluster.** A throwaway Postgres in Docker covers the
ingestion tests and a real full pass. The dev cluster is only for verifying **deployment**, and that
does require merging to `main` (CI only publishes images on push to main).

**Nothing gets promoted to prod without a green `/validar-qa` report.** CI proves the code compiles
and its unit tests pass; it says nothing about what is actually deployed. There is not a single
browser test in this repo, and the e2e specs run against a hand-seeded Postgres with the auth guard
faked and CI's locale — not against the cluster's `UTF8 | C | C`. So the gate is the skill: it
sweeps the SPA in a real browser, the API contract, the data and ingestion state, and the cluster,
then writes a verdict to `.claude/qa-reports/<version>.md`. **That report is versioned on purpose**
— its `## Cifras` block is the baseline the next validation diffs against to catch a store whose
catalogue silently collapsed. A front that could not be exercised yields `NO CONCLUYENTE`, never a
pass by omission.

## Infrastructure

These are facts about the running system, not plans:

- **Deployment target:** an existing **k3s cluster**. Scheduled CronJobs re-scrape per retailer
  (one job per store — the profiles diverge: Zara is light httpx, Sfera needs 2Gi for Chromium) plus
  a matching job and the vigía. Base ships them `suspend: true` (the vigía is the one exception), so
  in **dev** a pass is fired by hand:
  `kubectl -n deal-tracker-dev create job <name> --from=cronjob/deal-tracker-scraper-<slug>`.
  In **QA** all nine scrapers and matching run **weekly** (Mondays, staggered 03:00→07:00), and the
  vigía is **suspended** there on purpose — `prod` is the one that sweeps (see the vigía above). QA
  is not production, so it only needs enough passes to grow `price_history` and let someone
  experiment. Note that QA's matching sends **real Telegram messages**. QA tracks
  **semver**, not `sha`, so a newly merged store stays `suspend: true` there until a `release-qa`
  puts it in the image — firing it earlier dies with `ValueError: Tienda desconocida`. And its slots
  are **not** base's, so a new store cannot inherit base's `schedule` without checking what it
  collides with.
- **Database:** the **CNPG** cluster `platform-postgres-dev` in namespace `data-dev` — *not* the
  cluster's `postgresql-generic`. Despite the `-dev` in its name, **all three environments share
  it**, one database each: `deal_tracker`, `deal_tracker_qa`, `deal_tracker_prod`. It also holds the
  price-history time series.
- **Auth/login:** **Keycloak**, already deployed in the cluster. The web service is a resource
  server: it validates access JWTs against the realm JWKS.
- **Environments:** `dev local`, `dev`, `qa`, `prod`. **`prod` is up**: deployed since 2026-08-07,
  public at `dealtracker.liontechsolution.com`, ingesting daily and with the matching job on. It has
  0 registered users on purpose — Keycloak users are created by hand with `kcadm.sh`.
- **CI/CD:** GitHub Actions (`scraper-ci.yml`, `web-ci.yml`, `release-qa.yml`, `release-prod.yml`)
  build and publish `ghcr.io/liontech-solution/deal-tracker-{scraper,web}`, then a `bump` job
  rewrites the image tag in the manifests repo; **ArgoCD** syncs it. dev tracks `sha-<7>`
  automatically; QA tracks semver via the manual `release-qa` workflow, which promotes **by digest**
  (no rebuild). PRs only validate on amd64 — the **multiarch build runs on `main`** (#61), so a PR
  check being green does not mean the arm64 image exists yet. `release-prod` promotes nothing — prod
  consumes the very tag QA already runs — it is a **gate**: it refuses to move unless
  `.claude/qa-reports/<version>.md` says `**Veredicto: APTO**`, QA is running that version, both
  images exist in GHCR and the prod overlay is where the contract says. The authority is that
  committed report, never the GitHub `prerelease` flag. And since #306 it **only moves forward**: a
  GATE 0 rejects a version with no published Release, or one that is not `>=` the current `latest`
  (`>=`, not `>`, because re-running it over the version prod already serves is a supported case).
  Rolling back needs a different path, and it is not this workflow.
- **`prerelease` is not archive, and it now expires.** Here a live `prerelease` is a failed attempt —
  `/validar-qa` lowers the flag *only* when it writes an `APTO` report — so `release-qa` ends by
  calling `prune-prereleases.yml`, which keeps the **5 newest by semver** (hard floor of 2) and
  deletes the rest with their git tag. It never touches non-`prerelease` releases, `latest`, the
  GHCR images (that axis is #283) or the QA reports, and the commits stay reachable on `main`.
- **GHCR retention is prod's rollback policy** (#283). `v*` images are **never** pruned
  (`exclude-tags: latest,v*`): the prod overlay paints a tag, not a digest, so the surviving `v*`
  window *is* how far back prod can go. Only the `sha-<7>` tail has a ceiling — raised from 10 to
  **30** on 2026-08-14, because at the measured rate (15 scraper / 26 web publishes per week) 10 was
  under three days of dev. **Past that window the image is gone and dev has no rollback**, by
  decision. If a `v*` ever gets pruned: GHCR deletes *versions*, not tags, and some versions carry
  two releases (`1106121923` of `deal-tracker-web` holds `v0.1.8` **and** `v0.1.9`), so resolve tags
  to digests first and refuse if the version carries a protected `v*`.
- **Manifests live in a separate repo:** `juanjocop/k3s-local-apps-manifests`, under
  `deal-tracker/{base,overlays/dev,overlays/qa,overlays/prod}`. The `images[].newTag` values there
  are **machine-edited — never hand-edit them**. ArgoCD runs with `selfHeal: true`, so a
  `kubectl patch` against the cluster gets reverted; cluster changes go through that repo.
- **Cluster access:** `~/.kube/k3slocal.yaml` is the kubeconfig for inspecting the cluster.

## Language note

The product brief and domain vocabulary are in Spanish (category names, retailer sections). Preserve
Spanish domain terms where they map to real UI/data concepts. Code comments and commit messages in
this repo are in Spanish.
