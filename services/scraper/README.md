# Servicio scraper (Python)

Scraping e ingesta de precios del deal-tracker. Es dueño de las escrituras del
catálogo y del historial de precios en Postgres. Diseño **pluggable por tienda**:
cada scraper implementa `BaseStore.discover()` y devuelve productos normalizados;
la ingesta no conoce las particularidades de cada web.

## Estructura

```
src/scraper/
  config.py            # configuración desde entorno / .env
  db.py                # conexión Postgres (psycopg 3)
  migrate.py           # migrador SQL minimalista (aplica ../../db/migrations)
  ingest.py            # pipeline: upsert catálogo -> append historial -> altas/bajas
  run.py               # CLI del job (python -m scraper.run)
  stores/
    base.py            # contrato: BaseStore (list_catalog/fetch_details), ListingEntry, ScrapedProduct, ScrapedVariant
    zara.py            # primer scraper real (endpoints AJAX JSON de Zara)
    registry.py        # slug -> scraper
tests/                 # parsing (fixtures reales) + ingesta (Postgres)
```

## Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp ../../.env.example ../../.env   # y ajusta DATABASE_URL

# Migrar + ejecutar una pasada de scraping
python -m scraper.run --retailer zara --migrate

# Recorrer sin escribir en BD
python -m scraper.run --retailer zara --dry-run

# Calidad
ruff check . && ruff format --check . && mypy && pytest
```

Los tests de ingesta requieren `TEST_DATABASE_URL`; si no está, se omiten
(en CI lo aporta un servicio `postgres`).

## Cómo scrapea (dos fases + antibloqueo)

El scrapeo se hace en dos fases para no martillear la tienda:

1. **`list_catalog()`** — barre las categorías (pocas peticiones) y devuelve una
   `ListingEntry` por producto con una *huella* barata (precio por color del listado).
2. **`fetch_details()`** — la ingesta compara la huella con la del scrape anterior
   (`product.listing_signature`) y **solo pide el detalle** (tallas/stock) de los
   productos nuevos o con la huella cambiada. Si nada cambió, solo se refresca
   `last_seen_at`. En una pasada sin cambios se pasa de ~1 petición por producto a
   ~1 por categoría.

Las peticiones llevan **pausa con jitter** entre ellas y **reintentos con backoff
exponencial** ante `429`/`5xx`/errores de red (respetando `Retry-After`). Ajustable por
entorno: `SCRAPER_REQUEST_DELAY`, `SCRAPER_REQUEST_RETRIES`, `SCRAPER_RETRY_BACKOFF`,
`SCRAPER_USER_AGENT`, `SCRAPER_REQUEST_TIMEOUT`.

## Añadir una tienda

1. Crear `stores/<tienda>.py` con funciones `parse_*` puras + una clase que implemente
   `BaseStore` (`slug`, `name`, `base_url`, `list_catalog()`, `fetch_details()`).
2. Registrarla en `stores/registry.py`.
3. Guardar respuestas reales como fixtures en `tests/fixtures/` y testear el parsing.

## Docker

La imagen se construye con el contexto en la **raíz del repo** (necesita
`db/migrations`):

```bash
docker build -f services/scraper/Dockerfile -t deal-tracker-scraper .
```
