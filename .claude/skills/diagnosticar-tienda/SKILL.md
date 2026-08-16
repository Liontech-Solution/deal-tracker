---
name: diagnosticar-tienda
description: Diagnostica una tienda del scraper que ha dejado de funcionar — issue abierta por el vigía, pasada que falla o se cuelga, 429 o bloqueo, hojas de categoría caducadas, catálogo desplomado o bajas masivas. Ordena el diagnóstico para no empezar por la hipótesis equivocada. Úsala ante "la tienda X ha dejado de funcionar", "el vigía ha abierto una issue", "la pasada de X falla", "nos están bloqueando", "X ha perdido la mitad del catálogo".
---

# Diagnosticar una tienda rota

Una tienda se rompe de dos maneras y **sólo una es visible**: que la tienda *cambió* sale en el
resumen de la pasada; que la tienda dejó de *dejarnos entrar* es silencioso. Casi todo el tiempo
perdido en esta clase de avería viene de empezar por la hipótesis equivocada — casi siempre
«habremos pedido demasiado» — y bajar el ritmo antes de comprobar nada.

Los pasos van en orden. No te saltes el 1 ni el 3.

## 0. Antes: comandos

`just` puede no estar instalado, y en un worktree no hay venv. Con `just`:

```bash
just vigia --retailer <slug>       # hojas + smoke de parseo, como lo ve el vigía semanal
just check-categories <slug>       # sondea las hojas; sale != 0 si alguna ha caducado
just dry-run <slug>                # pasada completa sin escribir en BD
just tree <slug> <raiz>            # qué publica la tienda vs. qué ingerimos
```

Sin `just`, a mano contra el venv del **checkout principal**:

```bash
cd services/scraper && .venv/bin/python -m scraper.vigia --dry-run --retailer <slug> --muestra 2
cd services/scraper && .venv/bin/python -m scraper.run --retailer <slug> --check-categories
```

**Los dos exigen `DATABASE_URL` aunque no escriban nada** — `Config.from_env()` la pide antes de
mirar qué le has pedido. Si no hay `.env`, una de pega basta para arrancar:

```bash
export DATABASE_URL='postgresql://x:x@127.0.0.1:5432/x'
```

Con la de pega **pierdes una capa de diagnóstico, y es justo la del paso 2**: sin base no hay
`vigia_run` que consultar, así que salen los tiempos de esta pasada pero no hay contra qué
compararlos (`base: sin línea base — no se compara`). Para el caso «verde pero lento» necesitas una
base de verdad; una desechable en Docker vale.

## 1. Descarta primero los falsos positivos

Dos lecturas erróneas que ya se han hecho, y que no cuestan nada comprobar:

- **Un vigía suspendido en `qa` o `dev` no es un watchdog roto.** Desde 2026-08-07 el único que
  barre es `prod`: los tres namespaces salen a internet por la misma IP, así que un segundo vigía
  es el doble de peticiones para cero señal extra. `dev` y `qa` sobreescriben `base` a
  `suspend: true` a propósito. Para barrer desde QA aposta (tras tocar `tls.py`, o un bump de
  httpx), se lanza a mano:
  ```bash
  kubectl -n deal-tracker-qa create job vigia-manual --from=cronjob/deal-tracker-vigia
  ```
- **`ValueError: Tienda desconocida` en QA no es la tienda: es el ciclo de release.** QA sigue
  semver, no `sha`. Una tienda recién mergeada no está en la imagen que QA corre hasta que un
  `release-qa` la trae. La tienda está bien; el CronJob se reanudó antes de tiempo.

## 2. Separa «cambió» de «no nos deja entrar»

Es la bifurcación que ordena todo lo demás.

```bash
just vigia --retailer <slug>
```

El vigía responde cuatro preguntas distintas y conviene saber cuál falló: `revisar_hojas` (¿siguen
vivas las hojas que ingerimos?), `revisar_parseo` (¿la cadena entera sigue dando productos
usables?), `revisar_cobertura` (¿publica la tienda algo que no ingerimos?) y `comparar_con_base`
(¿nos dejan entrar al ritmo de siempre?).

**Un veredicto verde puede esconder la avería.** El 02/08/2026 el sondeo de Hipercor tardó 24 min
28 s desde el pod y 2 min 04 s desde fuera — verde las dos veces — porque la tienda seguía
regulándonos el paso tras el bloqueo de #107. Por eso cada capa se cronometra y se compara contra
la mediana de esa misma tienda en `vigia_run`. Si el veredicto es verde pero el tiempo se ha ido,
**estás en el caso «no nos dejan entrar»**, no en el de «todo bien».

Comprueba que esa comparación se ha hecho de verdad: si la salida dice `base: sin línea base — no
se compara`, esta capa no te ha dicho nada (o no hay base, §0, o la tienda aún no tiene historial).
No la des por verde — es la capa que destapó lo de Hipercor.

## 3. Si hay 429 o bloqueo: reproduce con `curl` ANTES de tocar el ritmo

Este es el paso que se salta todo el mundo y el que más tiempo ahorra. **Un 429 no es prueba de que
hayas pedido demasiado.**

Medido contra Cacles el 01/08/2026: Cloudflare devolvía `429 local_rate_limited` a *todas* las
peticiones de `httpx` —también desde un pod del cluster— mientras `curl`, `wget` y `urllib` pasaban
con 200 desde la misma IP, con las mismas cabeceras byte a byte y contra el mismo edge. La
diferencia estaba en una sola extensión del ClientHello: httpx anuncia ALPN y urllib no
(JA4 `t13d1713h1` contra `t13d1712`). No se quita por configuración — `httpcore` llama a
`set_alpn_protocols()` sobre el contexto que le pases, sea cual sea— y de ahí existe
`scraper/tls.py`.

Así que:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -A "$SCRAPER_USER_AGENT" '<url que da 429>'
```

- **curl pasa y httpx no** → es la huella del cliente, no tu ritmo. Mira `scraper/tls.py`.
- **curl también come 429** → ahí sí, es IP, ritmo o reputación. Sigue al paso 4.

**Un bump de `httpx` o `httpcore` es sospechoso de primera:** `tls.py` se apoya en un detalle
interno de `httpcore`, y si un bump lo rompe vuelve un 429 que se lee como otra cosa. Comprueba si
el fallo empezó junto a un cambio de dependencias antes que nada.

## 4. Fíjate desde dónde estás midiendo

La pregunta no es «¿la tienda está viva?» sino «¿nos deja entrar **a nosotros**?». Un runner de CI
sale por otra IP con otra reputación y contestaría por otro — por eso el vigía corre en el cluster
y no en CI. Si el diagnóstico desde tu equipo no cuadra con el del cronjob, mide desde el cluster:

```bash
kubectl -n deal-tracker-qa create job diag-<slug> --from=cronjob/deal-tracker-scraper-<slug>
kubectl -n deal-tracker-qa logs -f job/diag-<slug>
```

Contra `prod` no lo lances tú: pídeselo al usuario con `!`.

## 5. Si lo que ves es una baja masiva

La baja es adversarial y **deliberadamente conservadora**. Antes de tocar un solo umbral, averigua
qué red absorbe cada uno:

| Variable | Por defecto | Qué absorbe |
|---|---|---|
| `SCRAPER_SCAN_MAX_DEAD_RATIO` | `0.34` | aborta la pasada si demasiadas hojas mueren a la vez |
| `SCRAPER_DELIST_MIN_BASELINE` | `5` | no dar de baja con un histórico ridículo |
| `SCRAPER_DELIST_DROP_RATIO` | `0.5` | caída de catálogo que se considera sospechosa |
| `SCRAPER_DELIST_MIN_MISSES` | `2` | ausencias seguidas antes de dar por muerto |
| `SCRAPER_DELIST_PROBE` | `1` | confirmar la baja pidiendo el producto |

Aflojarlos para «arreglar» una pasada convierte un fallo ruidoso en **corrupción silenciosa**: la
pasada pasa a verde y el catálogo se vacía sin que nadie se entere. Si la pasada aborta por
`dead_ratio`, casi siempre la tienda ha reorganizado sus categorías y lo que toca es re-mapear las
hojas (`just tree <slug> <raiz>`), no subir el umbral.

Recuerda además que la ingesta es **atómica**: una pasada o commitea entera o revierte. Una pasada
muerta por `activeDeadlineSeconds` no deja datos a medias, pero tampoco deja nada.

## 6. Trampas por tienda

- **H&M** — la hoja muerta es **invisible**: un `pageId` irresoluble devuelve 200 con una página
  plausible y completa (el bucket entero del `categoryId`). Se caza con el **canario**
  (`es_espejismo()` en `hm.py`): una ruta inventada aposta por pasada, comparada contra la primera
  página de cada hoja — 100 % de coincidencia en las muertas, 0-8 % en las vivas. Si diagnosticas
  H&M sin el canario te llevas ~9700 productos y sacas la conclusión contraria. Además, aquí una
  fila de listado es **producto+color**, no producto.
- **Sfera / Hipercor** — Chromium headless, 2Gi. Un fallo puede ser de memoria o de
  `SCRAPER_BROWSER_NAV_TIMEOUT` / `SCRAPER_BROWSER_HYDRATE_TIMEOUT`, no de la tienda. Hipercor
  además se lee de sus **propias páginas** (`dataLayer` + `ld+json`), porque su `robots.txt` prohíbe
  `/api`: un cambio de maquetación la rompe donde a otras no les pasaría nada.
- **Springfield** — se lista por **sitemap**. Su `lastmod` es un sello de lote del generador, no una
  firma útil: no lo uses para decidir si algo cambió (#227).
- **Cacles** — lo del paso 3.
- **C&A y Springfield** — publican el mínimo de 30 días de la Ómnibus. Si lo que ves raro son
  acusaciones de precio inflado y no un fallo de scraping, eso es la regla de honestidad haciendo su
  trabajo, no una avería.

## 7. Cerrar

- **Busca si el hallazgo ya tiene issue antes de abrir otra**, aunque sólo esté mencionado de pasada
  en un comentario.
- La keyword de cierre en el PR va **en inglés** (`Closes #N`): «Cierra #N» no autocierra nada.
- Si el arreglo toca `tls.py`, `httpx` o `httpcore`, deja dicho en la issue que el vigía de `prod`
  es quien lo confirma — y considera un barrido manual desde QA (paso 1) antes de esperar al lunes.
