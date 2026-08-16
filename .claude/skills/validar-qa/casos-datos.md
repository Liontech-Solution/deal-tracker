# Casos del frente de datos e ingesta

Todo se lanza con `.claude/skills/validar-qa/scripts/qa-sql.sh` (solo lectura de verdad: la
transacción es `READ ONLY` y el motor rechaza cualquier escritura).

Una advertencia que vale por todo el fichero: **un `status = 'success'` no significa que la tienda
esté bien**. La pasada de Zara del 3 de agosto cerró en `success` con `errors = 69`, y la de Sfera
con 15.

Pero `errors` **no cuenta productos perdidos**, y confundirlo manda a alguien a buscar prendas que
están perfectamente en el catálogo. `ingest.py` lo compone así:

```
errors = len(sospechosos) + sondeos_sin_resolver + hojas_de_categoría_caídas
```

Zara: los 69 eran sondeos de confirmación de baja sin resolver, que se reintentan en la pasada
siguiente. Sfera: 14 sondeos sin confirmar **más una hoja de categoría muerta de 35**, que es un
hallazgo mucho peor y quedaba escondido dentro del mismo número.

Por eso un `errors > 0` **nunca se reporta como cifra suelta**: hay que abrirlo (D3) y decir de qué
está hecho. «69 sondeos de baja sin confirmar, se reintentan» y «una categoría entera dejó de
ingerirse» son dos frases muy distintas que salen del mismo contador.

**Y la evidencia caduca.** El desglose vive en el log del pod, y los pods se recolectan en días. Si
validas una semana después de la pasada, `kubectl logs` ya no tiene nada que darte: la copia
duradera es `scrape_run.message` y las condiciones del Job. D3 lo explica.

---

## D1 · Las nueve tiendas existen y han ingerido

La lista de tiendas sale de `registry.available_slugs()`, nunca de una lista escrita a mano aquí:

```bash
.claude/skills/validar-qa/scripts/qa-slugs.sh
```

(El script resuelve el venv del checkout principal: desde un worktree no hay ninguno, porque no
está versionado, y llamar a `services/scraper/.venv/bin/python` a pelo falla.)

Compárala con lo que hay en la base:

```sql
SELECT slug, active, created_at FROM retailer ORDER BY slug;
```

**Un slug registrado que no tiene fila en `retailer` es P0**: en QA nunca ha corrido, y el catálogo
que ve el usuario no tiene esa tienda por mucho que el ADR diga que sí. Es exactamente el caso de
`hipercor` en v0.1.5 — ocho filas para nueve slugs.

## D2 · Última pasada de cada tienda

```sql
SELECT r.slug,
       s.status,
       date_trunc('second', now() - s.started_at)::text AS hace,
       s.products_seen, s.variants_seen, s.errors,
       left(coalesce(s.message, ''), 60) AS mensaje
FROM retailer r
LEFT JOIN LATERAL (
  SELECT * FROM scrape_run WHERE retailer_id = r.id ORDER BY started_at DESC LIMIT 1
) s ON true
ORDER BY (s.status IS DISTINCT FROM 'success') DESC, r.slug;
```

El `LEFT JOIN LATERAL` es deliberado: un `JOIN` normal **esconde** la tienda que nunca ha corrido,
que es justo la que hay que ver.

- `status = 'failed'` → **P0**, con su `message` literal en el informe.
- `status IS NULL` (nunca ha corrido) → **P0**.
- `status = 'running'` de hace horas → **P0**: la pasada murió sin cerrar la fila.
- `errors > 0` en una pasada `success` → **P1**, y **hay que abrirlo en D3**: reportar el número
  suelto no es un hallazgo, es un dato sin interpretar.
- Última pasada de más de 8 días en QA (donde el ciclo es semanal) → **P1**.
- **`message IS NOT NULL` en una pasada `success` → léelo ENTERO, no por el preview.** Hay anomalías
  que **no suman en `errors`** y salen con `status='success'` y `errors=0`, así que decidir por
  `errors` se las pierde. Y el `left(…, 60)` de arriba es solo para que la tabla se lea: `message`
  es una lista de anomalías unida por ` · `, de hasta 500 caracteres (`_MAX_FAIL_MESSAGE`).

  **Si empieza por `señal de stock sospechosa` o por `sondeo sin respuesta`, eso es lo primero que
  hay que mirar.** Las dos alarmas del sondeo van delante del resto a propósito desde #456
  (`_alarmas_del_sondeo`, `ingest.py`), y el motivo conviene conocerlo para no revertirlo: son
  cortas y **no se pueden reconstruir desde ninguna otra columna**, mientras que las enumeraciones
  de hojas y ámbitos que van detrás se autolimitan con su `+N` y tienen sus propios contadores. Lo
  que el tope se coma será de esas, no de las alarmas.

```sql
SELECT r.slug, s.status, s.errors, s.message
FROM retailer r
JOIN LATERAL (
  SELECT * FROM scrape_run WHERE retailer_id = r.id ORDER BY started_at DESC LIMIT 1
) s ON true
WHERE s.message IS NOT NULL;
```

Y una que esta consulta **no puede contestar**: un `success` reciente no dice **qué versión** escribió
la fila. En QA el ciclo es semanal y las promociones son más frecuentes, así que lo normal es que la
última pasada sea de una imagen anterior a la desplegada — con la release del 10/08 escribiendo dato
de `v0.1.9` mientras QA servía `v0.4.0`. Eso lo resuelve `qa-procedencia.sh` en la Fase 0 (#378), y
**todo este frente descansa en su respuesta**: si el dato es de otra versión y la release toca
`services/scraper/`, lo que se mide aquí abajo es el scraper anterior.

## D2b · La señal de stock, que se lee en pareja o no se lee

`scrape_run.variants_in_stock` (0043, #427) cuenta las variantes con stock **entre las que esa
pasada escribió**, y su denominador es `variants_seen` de la misma fila. Sueltas no dicen nada:

```sql
SELECT r.slug, s.variants_seen, s.variants_in_stock,
       round(100.0 * s.variants_in_stock / nullif(s.variants_seen, 0), 1) AS pct
FROM retailer r
JOIN LATERAL (
  SELECT * FROM scrape_run WHERE retailer_id = r.id AND status = 'success'
  ORDER BY started_at DESC LIMIT 1
) s ON true
ORDER BY pct NULLS FIRST;
```

| qué ves | qué es |
|---|---|
| `variants_in_stock = 0` **con `variants_seen = 0`** | la pasada no escribió nada. No es de stock: mírala en D2 |
| `variants_in_stock = 0` **con `variants_seen > 0`** | **P0.** El parser de stock de esa tienda ha dejado de entender la respuesta |
| proporción baja pero > 0 | normal. **No hay umbral que elegir** y por eso el caso no lo pone: la pasada con menos stock de la historia del proyecto (hipercor) trae 7 de 55, un **12,7 %**, así que ninguna pasada sana se acerca al cero |

> **`column s.variants_in_stock does not exist` NO es un hallazgo.** La columna entra con la `0043`
> (v0.6.0), y QA sirve semver: hasta que la release esté desplegada, la base de QA no la tiene.
> Comprobado el 16/08/2026 — no existe ni en QA ni en `dev`, que iba por `sha-4adcbca`. Si el error
> aparece, lo que hay que mirar es **qué versión está desplegada** (Fase 0, `qa-procedencia.sh`), y
> el caso se declara **fuera de alcance de esa ejecución**, no P0. Es la misma trampa que D13 con su
> dependencia de la Fase 1.

**Por qué el cero es P0 y no una anomalía de datos**: mientras dure, el mecanismo de confirmación de
bajas de esa tienda está **inoperante**. No produce bajas falsas —`UNBUYABLE` no descataloga nunca—
pero deja de producir las verdaderas, y eso no se ve en ninguna otra cifra.

La ingesta lo canta sola cuando se dan las dos mitades a la vez, con esta frase en `message`:

```
señal de stock sospechosa: {N} de {N} candidatos agotados y 0 de {M} variantes con stock en el listado
```

**Y esa frase NO suma en `errors`**: la pasada sale `status='success'`, `errors=0` y `message`
distinto de NULL. Es exactamente el caso que la última viñeta de D2 persigue, y la razón de que
haya que leer `message` entero en vez de por el preview. Exige **las dos mitades juntas** a
propósito (`_success_message`, `ingest.py`): por separado cada una es un estado sano y frecuente.

**Y va la primera del mensaje** desde #456, así que no hay que ir a buscarla al final. Ese arreglo
salió de mirar este caso: hasta entonces la alarma se emitía la última y, medido sobre una pasada
realista —6 hojas caídas y 4 ámbitos sospechosos, justo el tipo de pasada en la que además querrías
enterarte de que el stock no se lee—, el mensaje llegaba a los 500 del tope y **la alarma se perdía
entera**. Una alarma contra un fallo silencioso, fallando en silencio.

## D3 · Caracterizar lo que salió mal

Esto aplica a dos casos, no solo a uno: los jobs **fallados**, y las pasadas `success` con
`errors > 0`.

```bash
# 1. Qué jobs fallaron y por qué motivo (esto NO caduca)
kubectl -n deal-tracker-qa get job -o json | jq -r '
  .items[] | select(.status.failed) |
  "\(.metadata.name)\t\([.status.conditions[]? | .reason] | join(","))"'

# 2. El log del pod, si todavía existe
kubectl -n deal-tracker-qa logs job/<nombre> --tail=80
```

**El log caduca y el mensaje de error engaña.** Cuando el pod ya ha sido recolectado, `kubectl logs`
responde `error: timed out waiting for the condition`, que parece un problema de red y significa
«este job ya no tiene pod». Compruébalo con
`kubectl -n deal-tracker-qa get pods -l job-name=<nombre>`. Cuando pase, tienes dos fuentes
duraderas y **son suficientes**: `scrape_run.message` (D2), que guarda el texto entero de la
excepción, y las `status.conditions` del Job del comando de arriba.

Cinco cosas que buscar, porque significan cosas muy distintas y tienen dueños distintos:

- `ValueError: Tienda desconocida` → la tienda está en `main` pero **no en la imagen semver de QA**.
  No es un fallo de la tienda: es que el `release-qa` aún no la ha promovido. **P1 de proceso**, y se
  dice así.
- `DeadlineExceeded` en las condiciones del Job → la pasada no falló, **no le dio tiempo**: superó
  el `activeDeadlineSeconds` del CronJob. **P0 si la tienda se queda sin catálogo** (que es el
  efecto), pero el arreglo es el presupuesto de tiempo o el rendimiento de la tienda, no el parseo.
  Trae el `activeDeadlineSeconds` configurado y cuánto duró, o el hallazgo no es accionable.
- `CatalogScanAborted` → saltó `SCRAPER_SCAN_MAX_DEAD_RATIO`. La ingesta se abortó **a propósito**
  para no dar de baja media tienda. **P0** para promover, pero la causa está en la tienda.
- Un 429 → distingue `HuellaTLSRechazada` (nos han vetado el fingerprint TLS: esperar no lo arregla)
  del 429 de presupuesto de peticiones. Son la misma respuesta HTTP y significan lo contrario.
- La **línea de resumen** de una pasada `success`, que es donde se desglosa `errors`:
  `confirmación activa: N sondeos (… X sin confirmar)` y
  `⚠ N/M hojas de categoría no responden`. Una hoja caída escondida dentro de `errors` es un
  hallazgo propio: esa categoría **dejó de ingerirse** y su ámbito se queda sin detección de bajas.

## D4 · Sanidad del precio

```sql
SELECT count(*) FILTER (WHERE price <= 0)                       AS precio_no_positivo,
       count(*) FILTER (WHERE currency IS NULL OR currency = '') AS sin_moneda,
       count(*) FILTER (WHERE list_price IS NOT NULL AND list_price < price) AS pvp_menor_que_precio,
       count(*)                                                  AS filas
FROM price_history
WHERE scraped_at > now() - interval '30 days';
```

Cualquier `precio_no_positivo` es **P0**: es dato corrupto que además se propaga al histórico y a
las gráficas. `pvp_menor_que_precio` es **P1** salvo que sea masivo.

## D5 · Canonicalización de talla y color

La base del cluster es `UTF8 | C | C`, y con ctype `C` el `lower()` de Postgres no baja las
acentuadas. Por eso esto se mira **aquí** y no en local: en CI sale en verde.

```sql
SELECT r.slug,
       count(*) AS variantes,
       count(*) FILTER (WHERE v.size  IS NOT NULL AND size_canon(v.size)   IS NULL) AS talla_sin_canon,
       count(*) FILTER (WHERE v.color IS NOT NULL AND color_canon(v.color) IS NULL) AS color_sin_canon
FROM variant v
JOIN product  p ON p.id = v.product_id
JOIN retailer r ON r.id = p.retailer_id
WHERE v.delisted_at IS NULL
GROUP BY r.slug
ORDER BY 3 DESC, 4 DESC, 1;
```

Una talla sin canónica no se puede filtrar en el catálogo ni casar con un interés: el usuario pide
la 24 y no le llega el aviso. Umbral, sobre las variantes activas de **esa** tienda: **P0 por encima
del 2 %, P1 por debajo**. Es un listón bajo a propósito — no es un fallo de presentación, es dato
que no se puede consultar.

Trae siempre los valores concretos que fallan, del campo que haya dado positivo:

```sql
SELECT DISTINCT v.size  FROM variant v
WHERE v.delisted_at IS NULL AND v.size  IS NOT NULL AND size_canon(v.size)   IS NULL LIMIT 10;

SELECT DISTINCT v.color FROM variant v
WHERE v.delisted_at IS NULL AND v.color IS NOT NULL AND color_canon(v.color) IS NULL LIMIT 10;
```

El `delisted_at IS NULL` no es decorativo: sin él salen valores de variantes dadas de baja que la
consulta principal nunca contó, y la evidencia no se corresponde con el hallazgo.

## D6 · Prendas duplicadas (#108)

El usuario ve dos veces la misma prenda cuando una tienda publica dos SKU para
`(producto, talla, color, url)`. Debe estar colapsado:

```sql
SELECT r.slug, count(*) AS grupos_duplicados, sum(n - 1) AS filas_de_mas
FROM (
  SELECT v.product_id, size_canon(v.size) AS t, color_canon(v.color) AS c, v.url, count(*) AS n
  FROM variant v
  WHERE v.delisted_at IS NULL
  GROUP BY 1, 2, 3, 4
  HAVING count(*) > 1
) d
JOIN product  p ON p.id = d.product_id
JOIN retailer r ON r.id = p.retailer_id
GROUP BY r.slug ORDER BY 3 DESC;
```

Que existan filas duplicadas **no es el fallo** — las tiendas hacen eso. El fallo es que lleguen a
la interfaz. Saca un producto concreto de los peores:

```sql
SELECT v.product_id, size_canon(v.size) AS talla, color_canon(v.color) AS color, count(*) AS filas
FROM variant v
WHERE v.delisted_at IS NULL
GROUP BY 1, 2, 3, v.url
HAVING count(*) > 1
ORDER BY 4 DESC LIMIT 5;
```

Y contrasta ese `product_id` contra la API:

```bash
# En el LISTADO el campo es variantCount; en el DETALLE es el array variants[].
curl -s "$API/catalog/products/<id>" | jq '.variants | length'
```

Si la API devuelve menos que la base y sin repetidos por `(talla, color, url)`, está colapsando bien
y esto es **P2** informativo. Si los duplicados llegan a la respuesta, es **P0**: el usuario ve dos
veces la misma prenda.

## D7 · Fotos

```sql
SELECT r.slug,
       count(p.id) AS activos,
       count(*) FILTER (WHERE p.id IS NOT NULL AND p.image_url IS NULL) AS sin_foto,
       round(100.0 * count(*) FILTER (WHERE p.id IS NOT NULL AND p.image_url IS NULL)
             / nullif(count(p.id), 0), 1) AS pct
FROM retailer r
LEFT JOIN product p ON p.retailer_id = r.id AND p.delisted_at IS NULL
GROUP BY r.slug ORDER BY pct DESC NULLS LAST;
```

Un catálogo de ropa sin fotos no es usable. **P0 por encima del 20 % en una tienda**, P1 por debajo.

Fíjate en que se parte de `retailer` con `LEFT JOIN`, igual que en D2 y por el mismo motivo: una
tienda con **cero productos** desaparece de un `JOIN` normal, y esa es precisamente la que hay que
ver. Un `activos = 0` aquí es **P0**, y se corresponde con una tienda ausente de `/catalog/facets`.

### D7b · y además resuelven: `image_url` poblada no es `image_url` viva

D7 comprueba que el campo no sea nulo, y **una URL muerta pasa esa comprobación**. Por ahí se
colaron las fotos de Cacles de #429: `image_url` al 100 % en las nueve tiendas y aun así ~12 % de su
catálogo saliendo «SIN FOTO» en la tarjeta y en la ficha.

```sql
SELECT r.slug, p.image_url,
       round(extract(epoch FROM (p.last_seen_at - p.last_detail_at))/86400) AS desfase
FROM product p JOIN retailer r ON r.id = p.retailer_id
WHERE p.delisted_at IS NULL AND p.image_url IS NOT NULL;
```

Muestrea por tienda (40 basta para ver un 10 %; Cacles entero son 424) y resuelve cada URL con un
`HEAD`. **Tres reglas, y las tres se aprendieron fallando** (16/08/2026):

- **Secuencial y con pausa.** A 6 en paralelo salieron 403 en 40/40 de Zara y 38/40 de Lefties, y
  las mismas URL dieron 200 una a una. Medido así, este caso habría escrito un P0 sobre dos
  catálogos enteros que están perfectamente.
- **Solo `404`/`410` cuenta como muerta, y reintentando una vez.** Un 403, un 429, un 5xx o un
  timeout son ritmo nuestro: van a «sin veredicto», que se reporta aparte y **no** es un hallazgo.
  De los 9 403 de Zara, 2 dieron 200 al reintentar.
- **La cifra es por tienda**, no global: el 11,6 % de Cacles desaparece diluido entre 16.844
  productos.

**P0 por encima del 20 % en una tienda** (mismo listón que D7: un catálogo sin fotos no es usable),
**P1 entre el 5 % y el 20 %**, **P2 por debajo**. Y `sin veredicto > 25 %` en una tienda **no es un
hallazgo, es una medición inválida**: se repite más despacio.

**Cruza siempre con el desfase**, que es lo que separa las dos causas posibles y evita acusar a la
tienda de lo que es nuestro:

| desfase `last_seen_at - last_detail_at` | qué significa una foto muerta ahí |
|---|---|
| bajo (el producto pasó por detalle hace poco) | la **tienda** publica una URL que su CDN no sirve. Lo mismo que caza `⚠ [fotos]` del vigía |
| alto | **nuestra** fila envejecida: el producto no vuelve a pasar por detalle, así que `image_url` no se reescribe (`_needs_detail`, `ingest.py`). Es #443, y mientras siga abierta esta es la causa probable |

Medido en Cacles el 16/08/2026, y por eso el cruce está aquí: **49 de 424 muertas (11,6 %), y las
49 con desfase ≥ 9 días**. Entre los 187 productos con detalle de ≤ 6 días, **cero**.

### Y de qué CDN salen: ningún host fuera de la tabla de anchos

Las fotos van hotlinked desde el CDN de cada tienda, y a cada uno hay que pedirle el ancho con el
parámetro que entienda. Qué entiende cada cual vive en `ANCHO_POR_HOST`
(`services/web/frontend/src/lib/image.ts`), y **un host que no esté en la tabla se sirve crudo**: la
foto se ve, así que no rompe nada y no aparece en ninguna otra comprobación.

Por ahí se colaron los 187 productos que H&M publica en `media.arket.com`, a 557 KB por foto durante
tres validaciones (#300). Esto es lo único que ve a la vez la tabla y la base:

```sql
SELECT split_part(split_part(p.image_url, '://', 2), '/', 1) AS host,
       count(*) AS fotos, count(DISTINCT r.slug) AS tiendas, min(r.slug) AS ejemplo_tienda
FROM product p JOIN retailer r ON r.id = p.retailer_id
WHERE p.image_url IS NOT NULL AND p.delisted_at IS NULL
GROUP BY 1 ORDER BY 2 DESC;
```

Contrasta cada `host` con las claves de `ANCHO_POR_HOST`. **Un host que no esté en la tabla es P2**
—no está roto, está sin optimizar— y se arregla midiendo su CDN con `curl` y añadiéndolo:

```bash
curl -s -o /dev/null -w '%{http_code} %{size_download} %{time_total}\n' "<url>"
curl -s -o /dev/null -w '%{http_code} %{size_download} %{time_total}\n' "<url>?<param>=563"
```

Dos cosas que la salida enseña y conviene no malinterpretar: **un host puede ser de dos tiendas**
(`dam.elcorteingles.es` es de Sfera y de Hipercor, y solo la primera trae el ancho de origen), y una
tienda puede publicar por **dos hosts** (H&M por `image.hm.com` y `media.arket.com`). Así que el
recuento por host no cuadra con el de tiendas, y no es un error.

## D8 · Barefoot, sección y género

```sql
SELECT r.slug, p.section, p.barefoot, p.gender, count(p.id) AS n
FROM retailer r
LEFT JOIN product p ON p.retailer_id = r.id AND p.delisted_at IS NULL
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
```

Otra vez `LEFT JOIN` desde `retailer`, y aquí importa especialmente: la regla estrella de este caso
es sobre Cacles, y con un `JOIN` normal **Cacles desaparece de la salida justo cuando está a cero**.
La comprobación pasaría en silencio en el único escenario que pretende detectar.

Tres lecturas concretas, no una inspección genérica:

- **Cacles** es barefoot nativa: su calzado debe ser `barefoot = 'si'` al 100 %. Cualquier otra cosa
  es **P0**, porque la tienda entera se cae del catálogo por defecto (que filtra `barefoot=si`).
- **H&M** publica ~10 % de modelos bajo los dos géneros y deben quedar `unisex`. Un cero redondo en
  `unisex` es **P1**: se perdió la detección.
- Una tienda con **todo** en `desconocido` en calzado desaparece del catálogo por defecto sin que
  nadie lo note. **P1**.

## D9 · Bajas de la última pasada

```sql
SELECT r.slug,
       count(*) FILTER (WHERE p.delisted_at > now() - interval '7 days') AS bajas_7d,
       count(*) FILTER (WHERE p.id IS NOT NULL AND p.delisted_at IS NULL) AS activos,
       round(100.0 * count(*) FILTER (WHERE p.delisted_at > now() - interval '7 days')
             / nullif(count(*) FILTER (WHERE p.id IS NOT NULL AND p.delisted_at IS NULL), 0), 1) AS pct
FROM retailer r LEFT JOIN product p ON p.retailer_id = r.id
GROUP BY r.slug ORDER BY pct DESC NULLS LAST;
```

El delisting es adversarial y conservador a propósito. Una tienda que da de baja **más del 15 % de
su catálogo activo en una semana** es **P0**: o cambió el listado, o el `probe_alive()` está
devolviendo `False` ante errores de red, que es el fallo más caro del proyecto.

## D10 · El histórico crece

Sin puntos nuevos no hay gráfica, no hay mínimo reciente y el matching no puede disparar nada.

Pero **en QA el ciclo es semanal**: la mayoría de los días de una ventana de 14 salen a cero y eso
es lo normal, no un hallazgo. Lo que importa es un día con **pasada `success` y sin puntos**. Por
eso la consulta cruza las dos cosas y no se limita a contar:

```sql
SELECT d.dia,
       coalesce(ph.puntos, 0) AS puntos,
       coalesce(sr.pasadas_ok, 0) AS pasadas_ok,
       CASE WHEN coalesce(sr.pasadas_ok, 0) > 0 AND coalesce(ph.puntos, 0) = 0
            THEN '<<< ingesta muda' END AS alerta
FROM generate_series(current_date - 13, current_date, interval '1 day') AS d(dia)
LEFT JOIN (
  SELECT date_trunc('day', scraped_at)::date AS dia, count(*) AS puntos
  FROM price_history WHERE scraped_at > now() - interval '14 days' GROUP BY 1
) ph ON ph.dia = d.dia::date
LEFT JOIN (
  SELECT date_trunc('day', started_at)::date AS dia, count(*) AS pasadas_ok
  FROM scrape_run WHERE status = 'success' AND started_at > now() - interval '14 days' GROUP BY 1
) sr ON sr.dia = d.dia::date
ORDER BY d.dia DESC;
```

Una fila con `alerta` es **P0**: la pasada escribió catálogo pero no precio. Un día a cero **sin**
pasada es simplemente un día sin pasada y no se reporta.

## D11 · Notificaciones, suelo y pasadas pendientes

```sql
SELECT job, last_scrape_run_id, updated_at FROM job_state;

-- Pasadas ya evaluadas que siguen por encima del suelo: si hay muchas, el suelo está frenado por
-- un hueco en la secuencia de ids (#240).
SELECT count(*) AS en_el_libro, min(scrape_run_id), max(scrape_run_id) FROM matching_scanned_run;

-- Lo que de verdad importa: pasadas con precios que el matching NO ha evaluado.
SELECT DISTINCT ph.scrape_run_id
FROM price_history ph
WHERE ph.scrape_run_id > (SELECT last_scrape_run_id FROM job_state WHERE job = 'matching')
  AND NOT EXISTS (SELECT 1 FROM matching_scanned_run m WHERE m.scrape_run_id = ph.scrape_run_id)
ORDER BY 1;

SELECT count(*) AS avisos_7d, count(DISTINCT user_id) AS usuarios,
       max(sent_at) AS ultimo
FROM notification WHERE sent_at > now() - interval '7 days';

-- La clave única debe hacer imposible el duplicado; si esto devuelve algo, la restricción no está.
SELECT interest_id, variant_id, price_event_key, count(*)
FROM notification GROUP BY 1, 2, 3 HAVING count(*) > 1;
```

Pasadas pendientes de hace más de una semana son **P0**: el matching no las está consumiendo y
nadie recibe sus avisos. Duplicados en `notification` son **P0**.

Ojo con leer `last_scrape_run_id` a solas, que es lo que se hacía hasta #240: el suelo **se queda
atrás a propósito** cuando hay un hueco en la secuencia (un id quemado por una pasada que hizo
rollback, o una todavía en vuelo), y eso no significa que el matching esté parado. Quien contesta
esa pregunta es la consulta de pasadas pendientes.

## D12 · Migraciones

```sql
SELECT count(*) AS aplicadas, max(version) AS ultima FROM schema_migrations;
```

`version` guarda el **nombre del fichero** (`0024_size_canon_sufijos_de_unidad.sql`), no un número.
Ordena bien por el prefijo, pero no lo compares como entero.

Compara con `ls db/migrations/*.sql | wc -l` **en el tag desplegado**, no en tu rama de trabajo:

```bash
git ls-tree --name-only <tag> db/migrations/ | grep -c '\.sql$'
```

Menos filas que ficheros es **P0**: el initContainer `migrate` no llegó a aplicarlas y el esquema
va por detrás del código.

## D13 · El vigía

```sql
SELECT retailer_slug, capa,
       max(ran_at) AS ultima,
       round(avg(segundos / nullif(unidades, 0))::numeric, 2) AS s_por_unidad_media,
       count(*) AS muestras
FROM vigia_run
WHERE ran_at > now() - interval '60 days'
GROUP BY 1, 2 ORDER BY 1, 2;
```

Y el resultado del job del vigía lanzado en la Fase 1:

```bash
kubectl -n deal-tracker-qa logs job/validacion-vigia-<version> --tail=200
```

Su código de salida ya es un veredicto: **0** nada accionable, **1** algo lo es. Si el vigía no llegó
a terminar, el frente queda **NO CUBIERTO** — no se aprueba por silencio.

**La severidad la decide la marca del hallazgo, no el símbolo** (#251). La tabla manda y está en
`SKILL.md`, sección «El vigía»; en corto:

- `✖` **sin marca** (hojas retiradas, parseo roto, ninguna hoja viva) → **P0**. Es el caso de «la
  tienda dejó de dejarnos entrar», que es para lo que existe el vigía.
- `✖ [cobertura]` (hay una hoja publicada que no ingerimos) → **P1**, y **P0 solo si alguna de las
  hojas que nombra es una de las cinco categorías del brief**. No es que la tienda esté rota: es
  alcance de producto pendiente.
- `⚠ [estacional]` (hoja de campaña apagada) → **P2 exento, no abre issue**: el propio vigía declara
  que su id vuelve con la campaña.
- `⚠ [huerfana]` (declaración de `COBERTURA_DECLARADA` que la tienda ya no publica) → **P2 exento,
  no abre issue**: no esconde catálogo, solo envejece, y muchas son de campaña.
- `⚠ [fotos]` (foto publicada que el CDN devuelve 404) → **P2**, y solo abre issue si **D7b** lo
  confirma: el vigía mira cinco productos y eso no es una prevalencia.
- `⚠` sin marca → **P1**, como siempre.

**Este caso depende de la Fase 1 del orquestador**, que es quien lanza el job. Si corres el frente
de datos solo (`/validar-qa --frente datos`), no lo lances tú: declara D13 **fuera de alcance de
esta ejecución** y dilo en el informe. Es una dependencia no satisfecha, no un fallo, y confundir
las dos cosas mete un P0 falso.

Ojo también con la consulta de arriba: `vigia_run` puede estar **vacía del todo** (en QA lo estaba
en agosto de 2026, con el CronJob activo pero sin haberse disparado nunca). Cero filas no es una
tienda lenta: es que no hay serie histórica todavía, y entonces el ×3 de la comparación de ritmo no
se puede evaluar. Dilo así.

## D14 · Cifras comparables

Este bloque se copia tal cual al informe: es la línea base contra la que se compara la **siguiente**
versión, y lo que convierte «parece que va bien» en «Zara pasó de 3381 productos a 40».

```sql
SELECT r.slug,
       count(p.id) FILTER (WHERE p.delisted_at IS NULL) AS productos,
       (SELECT count(*) FROM variant v
          JOIN product p2 ON p2.id = v.product_id
         WHERE p2.retailer_id = r.id AND v.delisted_at IS NULL) AS variantes,
       (SELECT count(*) FROM price_history ph
          JOIN variant v2  ON v2.id = ph.variant_id
          JOIN product p3  ON p3.id = v2.product_id
         WHERE p3.retailer_id = r.id AND ph.scraped_at > now() - interval '30 days') AS puntos_30d
FROM retailer r LEFT JOIN product p ON p.retailer_id = r.id
GROUP BY r.id, r.slug ORDER BY r.slug;
```

Es `count(p.id) FILTER (…)` y no `count(*) FILTER (…)` por un motivo que muerde de verdad: con
`LEFT JOIN`, una tienda sin productos produce igualmente una fila con todo a `NULL`, y esa fila
**satisface** `p.delisted_at IS NULL`. Con `count(*)` la tienda vacía sale con «1 producto» y la
línea base del informe queda mal para siempre. `count(p.id)` ignora la fila fantasma.

Frente al bloque `## Cifras` del informe anterior en `.claude/qa-reports/`:

- Caída de más del **30 %** en productos o variantes de una tienda → **P0**, aunque la pasada dijera
  `success`. Es el daño que ninguna otra comprobación ve.
- Subida brusca inexplicable → **P1**: suele ser duplicación de identificadores.
- Si no hay informe anterior, dilo: la primera pasada **no puede** detectar regresión, y fingir que
  sí es peor que reconocerlo.

## D15 · Prendas vivas que ninguna pasada ve (#289)

El complemento de D9, y mira justo lo contrario: D9 vigila lo que **se da de baja**; esto vigila lo
que **nunca** se da de baja y tampoco se vuelve a ver. Una prenda así no está descatalogada, así que
sigue contando como activa, pero no recibe puntos nuevos: no se puede seguir, no se puede notificar
y su gráfica se congela. Hoy no lo mira nadie, y desde #261 tampoco sale ya en `errors`.

```sql
SELECT r.slug,
       count(*) FILTER (WHERE p.last_seen_at < now() - interval '14 days') AS atrapados,
       count(*) AS vivos,
       round(100.0 * count(*) FILTER (WHERE p.last_seen_at < now() - interval '14 days')
             / nullif(count(*), 0), 1) AS pct,
       max(date_trunc('day', now() - p.last_seen_at))::text AS mas_viejo
  FROM product p JOIN retailer r ON r.id = p.retailer_id
 WHERE p.delisted_at IS NULL
 GROUP BY r.slug ORDER BY atrapados DESC;
```

**Catorce días son dos pasadas perdidas**, no dos días: en QA las tiendas corren **semanalmente**.
Con la ventana de 7 días de D9 la mitad de la tabla sería ruido de calendario.

**`missing_streak` es la columna equivocada, y hay que decirlo** porque es la que cualquiera elegiría
al «mejorar» esta consulta. `_rescue()` (`ingest.py`) la pone a **cero** cada vez que un sondeo
confirma que la prenda sigue viva, que es exactamente lo que le pasa a esta población: medido en QA
el 13/08/2026, 29 prendas de Zara llevaban 20 días sin verse **con la racha a 0**. La columna que no
miente es `last_seen_at`.

### Línea base conocida — no es un hallazgo nuevo cada semana

Zara y Sfera tienen población medida y con issue propia (**#356**, las hojas de rebajas sin mapear;
**#357**, las prendas que la tienda no lista en ninguna hoja). Medido en QA el 13/08/2026:

| tienda | atrapados | vivos | pct |
|--------|-----------|-------|-----|
| zara   | 95        | 4454  | 2,1 |
| sfera  | 58        | 864   | 6,7 |
| las otras siete | 0 | —   | 0,0 |

Que **siete de nueve estén a cero** es lo que convierte esto en vigilancia útil: no es un achaque
general del mecanismo de bajas, es algo que le pasa a dos tiendas concretas. Lo que se vigila es el
**cambio**, no el número absoluto:

- Una tienda que estaba a **0 y desarrolla población** → **P1**. Es cobertura de catálogo que se
  pierde en silencio, y ninguna otra comprobación la ve.
- Zara o Sfera creciendo de forma marcada sobre esta línea base → **P1**.
- Que Zara baje ~44 tras la v0.4.0 es **lo esperado**, no una anomalía: es el residuo del lookbook
  que arregló el PR #355. Ese mismo arreglo sube su catálogo de 4417 a 4461 en D14.

No es **P0**: no corrompe datos ni tumba la validación. Hace visible una pérdida de cobertura que
hoy no mide nadie.

### El coste que no se ve en esta tabla

Estas prendas **gastan presupuesto de sondeo en cada pasada**. Son candidatas a baja permanentes:
suben de racha, se sondean, salen vivas, se rescatan a 0, y vuelta a empezar. Con
`SCRAPER_DELIST_PROBE_MAX=50`, la última pasada de Zara en QA (10/08/2026) mandó **50 sondeos —el
tope—, dejó 134 candidatas sin sondear y encontró 0 bajas reales**. La serie está en
`scrape_run.probes_sent` / `probes_over_cap`, el instrumento que dejó #261; su síntoma en el resumen
de la pasada es el `confirmación activa: N sondeos` que ya lee D3. Si `probes_over_cap` crece a la
vez que esta tabla, la lectura es que las bajas de verdad se están quedando sin presupuesto detrás
de una cola de prendas que siguen vivas. Va a **#357**.

**Desde la v0.6.0 ese gasto ya no se repite entero, y el par de columnas lo dice** (0042, #412): a
un candidato confirmado vivo hace poco no se le vuelve a preguntar, y eso se cuenta en
`probes_skipped_fresh`. Léela **junto a `probes_over_cap`**, nunca sola: la primera subiendo con la
segunda bajando es la ventana haciendo su trabajo; las dos a cero con `probes_sent` en el tope es
que no está activa. Y no la sumes a los fallos — como `over_cap`, va bloqueada frente a la baja.

> **Pendiente concreto para la validación de la v0.6.0**, que no se puede hacer en local porque
> exige pasadas reales sobre un catálogo real: **medir `probes_over_cap` antes y después** de que la
> ventana de #412 esté activa, contra el bloque `## Cifras` del informe de v0.5.0. La línea de
> partida está ahí: la última pasada de Zara en QA mandó 50 sondeos —el tope— y dejó 134 candidatas
> sin sondear. Si tras la v0.6.0 `over_cap` no baja con `probes_skipped_fresh` subiendo, la ventana
> está puesta pero no está ahorrando nada, y eso es un hallazgo de la propia #412.
