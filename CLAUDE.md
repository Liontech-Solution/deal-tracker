# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This is a **greenfield project** — no application code exists yet. The only content is `base.txt`, a Spanish-language product brief. There is no build/lint/test tooling, no `package.json`/`go.mod`/etc., and this directory is not yet a git repository. Update this file as the stack and commands take shape.

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
