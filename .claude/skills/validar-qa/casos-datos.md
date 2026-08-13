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
