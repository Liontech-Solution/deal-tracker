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
    base.py            # contrato: BaseStore (list_catalog/fetch_details), ListingEntry, ScrapedProduct, ScrapedVariant, ScrapedImage
    browser.py         # navegador headless (Playwright) para las tiendas tras Akamai
    zara.py            # Zara (endpoints AJAX JSON, httpx puro)
    sfera.py           # Sfera (SFCC "firefly", vía navegador)
    lefties.py         # Lefties (Inditex `itxrest`, vía navegador)
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

# ¿Sigue viva cada hoja de categoría? (vigilancia, no ingiere)
python -m scraper.run --retailer zara --check-categories

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

### Refresco periódico forzado

Ese ahorro tiene un efecto que hay que compensar: una prenda de precio estable **nunca** cambia de
huella, así que sin más no volvería a observarse jamás. Y sin re-observaciones no hay serie
temporal: el aviso de bajada y el veredicto de "descuento honesto" exigen histórico **previo**
—con un solo punto por variante todo el catálogo sale `none` y no se avisa de nada— y el stock por
talla se queda congelado, porque cambia sin tocar el precio del listado.

Por eso, además de nuevos y cambiados, cada pasada vuelve a pedir el detalle de los productos cuyo
`product.last_detail_at` supera `SCRAPER_DETAIL_MAX_AGE_DAYS` (7 por defecto; **0 desactiva** el
refresco), **los más rancios primero** y como mucho `SCRAPER_DETAIL_REFRESH_MAX` por pasada. Como
lo refrescado estrena marca y se va al final de la cola, el barrido es round-robin y sin ráfagas.
El coste depende de la tienda: en Zara es una petición por producto, mientras que Sfera sirve el
detalle desde la caché del listado (gratis: ahí conviene subir el tope).

Las peticiones llevan **pausa con jitter** entre ellas y **reintentos con backoff
exponencial** ante `429`/`5xx`/errores de red (respetando `Retry-After`). Ajustable por
entorno: `SCRAPER_REQUEST_DELAY`, `SCRAPER_REQUEST_RETRIES`, `SCRAPER_RETRY_BACKOFF`,
`SCRAPER_USER_AGENT`, `SCRAPER_REQUEST_TIMEOUT`.

## Detección de bajas (acotada y con red de seguridad)

Una "baja" (`delisted_at`) se detecta por ausencia: lo no visto en la pasada se da por
descatalogado. Para que esto no genere falsos positivos:

- **Acotada al ámbito escaneado** — cada tienda declara sus ámbitos `(gender, section,
  category)` en `scopes()`. Solo se dan de baja productos de ámbitos realmente recorridos en
  esa pasada, y **un ámbito con alguna hoja caída no cuenta como recorrido** (ver más abajo):
  lo que no se ha podido mirar no está retirado.
- **Red de seguridad por umbral** — si en un ámbito con al menos `SCRAPER_DELIST_MIN_BASELINE`
  productos activos lo observado cae por debajo de `SCRAPER_DELIST_DROP_RATIO` (p.ej. una
  categoría que devuelve 0 por un bloqueo blando), se **omiten sus bajas** y el `scrape_run`
  queda con `errors > 0` como aviso.
- **Histéresis** — no se da de baja a la primera: cada ausencia suma una pasada a
  `product.missing_streak` / `variant.missing_streak` y solo se marca `delisted_at` al llegar a
  `SCRAPER_DELIST_MIN_MISSES` (2 por defecto; 1 = comportamiento sin histéresis). Volver a verse
  reinicia el contador, y una pasada de ámbito sospechoso **no** lo avanza, así que un fallo
  puntual no gasta intentos. El resumen del job imprime los ausentes pendientes de confirmar.
- **Confirmación activa** — antes de descatalogar se le **pregunta a la tienda** por el producto
  (`SupportsAliveProbe.probe_alive`, capacidad opcional por tienda). Solo se da de baja lo
  confirmado como retirado: un veredicto "sigue a la venta" pone la racha a cero (típicamente
  un producto que se movió de categoría) y **la falta de veredicto** (bloqueo, error de red, o
  candidatos que exceden `SCRAPER_DELIST_PROBE_MAX`) deja la baja para otra pasada y suma a
  `errors`. Se desactiva con `SCRAPER_DELIST_PROBE=0` (vuelve a la baja por histéresis).
  - **Zara**: el endpoint de detalle responde 200 con lista vacía si ya no conoce el id.
  - **Sfera**: `firefly/stock` prueba que sigue comprable (barato, sin renderizar); si no, la
    PDP decide, porque enruta por id y da **404** con uno que ya no existe (un producto
    agotado pero vivo responde 200).
  - **Lefties**: el mismo `productsArray` del detalle responde `_ERR_PRODUCT_NOT_FOUND` para un
    id que ya no existe, y admite varios ids por llamada: el sondeo sale casi gratis.

## Hojas de categoría que caducan

Las cinco redes de arriba actúan **después** del listado, así que ninguna cubre lo que pasa
**durante**. Y lo que pasa es que los ids de categoría caducan: el `2428332` de Zara devolvía
404 **cuatro días** después de verificarlo vivo, y como el error propagaba y la ingesta es
atómica, **una hoja muerta de 47 tumbaba las 47**, en silencio y en cada pasada.

Ahora una hoja que la tienda ya no sirve (**404/410**, ver `GONE_STATUS`) se salta y se apunta en
el `ScanReport` de la tienda (`SupportsScanReport`, capacidad opcional como `SupportsAliveProbe`):

- **Solo se toleran las hojas retiradas.** Un 403 es un bloqueo y un 5xx un fallo del servidor
  —que además ya se reintenta con backoff—: esos siguen abortando la pasada. Tragárselos
  convertiría un problema transitorio en un catálogo mutilado que se da por bueno.
- **El ámbito de una hoja caída sale de las bajas**, aunque sus otras hojas hayan respondido. Sin
  esto, sus productos contarían como ausentes y acabarían descatalogados; la red por umbral no lo
  salva, porque un ámbito alimentado por seis hojas solo pierde un 17 % de lo observado al caerse
  una — lejos del 50 % que dispara la sospecha.
- **Si cae más de `SCRAPER_SCAN_MAX_DEAD_RATIO` (0,34 por defecto), la pasada aborta** sin
  escribir (`CatalogScanAborted`): tantas hojas muertas no son categorías retiradas, son un
  bloqueo o un cambio de API.
- **Se nota**: las hojas caídas suman a `scrape_run.errors` y el resumen del job las canta con la
  cuenta de ámbitos que se quedan sin detección de bajas. Al verlo, toca buscar el id nuevo en el
  árbol de categorías de la tienda.

Lo implementan las tres tiendas. En Lefties cuenta igual la hoja que **desaparece del menú**, que
es su forma de la misma avería.

### Vigilancia: `--check-categories`

Que la pasada sobreviva no arregla el problema de fondo — mientras nadie busque el id nuevo, esa
categoría no se ingiere. Para enterarse antes que el usuario:

```bash
python -m scraper.run --retailer zara --check-categories   # no ingiere, no escribe
```

Sondea cada hoja configurada (`SupportsLeafHealth`) y sale **≠ 0 solo por lo accionable**: una
hoja **retirada** pide un id nuevo y hace fallar el chequeo; una hoja **sin veredicto** se avisa
pero no rompe, porque medido contra Sfera un chequeo normal ya trae un 403 suelto de Akamai y un
vigía que da falsas alarmas de rutina acaba silenciado. La excepción es que **ninguna** hoja se
confirme viva: eso ya no es un blip, es un bloqueo, y sí falla.

Cada tienda usa su señal más barata: Zara pide el listado (no hay endpoint para preguntar "¿existe
esta categoría?"), **Lefties resuelve las 38 hojas con UNA petición** al menú (la hoja retirada es
justo la que ya no aparece en él) y Sfera pide la primera página firefly de cada ruta.

## Pasadas fallidas en `scrape_run`

Una pasada que revienta deshace su transacción entera, y con ella se iba **la propia fila de la
pasada**: en la BD, una tienda que llevaba días sin poder ingerir no se distinguía de una que
nadie había programado todavía, y el único rastro estaba en unos logs que rotan. Ahora el fallo se
registra aparte, con `status = 'failed'` y el motivo en `message` (migración `0013`):

```sql
SELECT r.slug, s.status, s.started_at, s.message
FROM scrape_run s JOIN retailer r ON r.id = s.retailer_id
ORDER BY s.started_at DESC LIMIT 20;
```

Vale para cualquier excepción, no solo para el aborto por hojas caídas. El contenido sigue sin
escribirse: el registro va en una transacción nueva **después** del rollback, y si él mismo
fallara se traga el error para no tapar el original.

## Añadir una tienda

1. Crear `stores/<tienda>.py` con funciones `parse_*` puras + una clase que implemente
   `BaseStore` (`slug`, `name`, `base_url`, `list_catalog()`, `fetch_details()`).
2. Registrarla en `stores/registry.py`.
3. Guardar respuestas reales como fixtures en `tests/fixtures/` y testear el parsing.
4. Opcional pero recomendado: implementar `probe_alive()` (`SupportsAliveProbe`) si la tienda
   sabe responder por un producto suelto — es lo que evita falsas bajas. Y si el catálogo se
   recorre por hojas de categoría, `scan_report()` (`SupportsScanReport`): sin él, la primera
   hoja que caduque tumba la pasada entera de esa tienda.
5. Opcional: rellenar `ScrapedProduct.images` (galería por color) si la tienda las expone en algo
   que ya se pide. Se guardan en `product_image` (una fila por foto, con el color y su posición) y
   `image_url` sale de la primera, para no tener dos fuentes de verdad. Hecho en Zara
   (`detail.colors[].xmedia[]`, hasta 8 por color) y en Sfera (`_my_colors[].all_images[]`, del
   propio listado). Si se deja vacía, la ficha se pinta con el placeholder del diseño y **no se
   borra** lo que hubiera: una lista vacía significa "esta pasada no sabe de fotos", no "este
   producto se quedó sin fotos" (mismo criterio que el `COALESCE` de `image_url`).

   **Regla que no se puede saltar:** el `color` de cada foto tiene que salir del **mismo campo**
   que el `color` de las variantes, y en el mismo recorrido. Es la clave con la que la ficha
   empareja foto y precio —el precio cuelga de la variante, que es talla+color— y si los dos
   nombres se desalinean el emparejamiento falla en silencio: se enseñaría la foto de un color con
   el precio de otro. Un color que no produzca ninguna variante utilizable no debe aportar fotos.
   Los tests de parseo de cada tienda fijan la invariante (`{color de foto} ⊆ {color de variante}`)
   y hay un SQL para comprobarlo sobre una pasada real:

   ```sql
   SELECT count(*) FROM product_image i
   WHERE i.color IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM variant v WHERE v.product_id = i.product_id AND v.color = i.color);  -- debe dar 0
   ```

   Antes de guardar la URL conviene comprobar en vivo que el CDN **no tiene antihotlink** (200 sin
   `Referer` y con `Referer` de tercero) y con qué parámetro se le pide el ancho: no es el mismo en
   las dos tiendas — Zara acepta `&w=` y el de El Corte Inglés lo ignora (lleva el tamaño en
   `impolicy=Resize&width=...`), así que ahí el ancho que se guarda es el definitivo.

## Docker

La imagen se construye con el contexto en la **raíz del repo** (necesita
`db/migrations`):

```bash
docker build -f services/scraper/Dockerfile -t deal-tracker-scraper .
```

### Imagen publicada (multiarch)

El workflow `scraper-ci` construye la imagen **multiarch** (`linux/amd64` + `linux/arm64`,
para el cluster k3s sobre Raspberry Pi) con `buildx` + QEMU. En los PR solo valida que
compila; **al mergear a `main`** hace push a GHCR:

- `ghcr.io/liontech-solution/deal-tracker-scraper:latest` (tag móvil)
- `ghcr.io/liontech-solution/deal-tracker-scraper:<git-sha>` (inmutable, para rollback)

Se conservan las **10 imágenes etiquetadas más recientes** (paso de retención); el resto se
elimina para no saturar el almacenamiento. El paquete es **privado**: el cluster necesita un
`imagePullSecret` con permiso de lectura sobre el paquete (se configura en el repo de
manifiestos, no aquí).

Comprobar la imagen publicada:

```bash
docker manifest inspect ghcr.io/liontech-solution/deal-tracker-scraper:latest
```
