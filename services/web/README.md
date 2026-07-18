# Servicio web (Node/TS) — placeholder

Reservado para la **experiencia de usuario** (Fase 2+): API + frontend donde las
familias configuran sus prendas de interés, login con **Keycloak**, y el **bot de
Telegram** que notifica las ofertas.

Aún **sin código**. Se separa del servicio `scraper` a propósito: si el scraping
se bloquea o falla, la app de usuario sigue sirviendo los últimos datos. El punto
de integración entre ambos servicios es la **Postgres compartida**, cuyo esquema
vive en [`db/migrations`](../../db/migrations) como contrato SQL neutro.

Reparto de responsabilidad sobre las tablas:

- `scraper` (Python) → escribe `retailer`, `product`, `variant`, `price_history`, `scrape_run`.
- `web` (Node/TS) → será dueño de usuarios / intereses / notificaciones (tablas de Fase 2).
