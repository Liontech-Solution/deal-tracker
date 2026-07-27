---
name: nueva-tienda
description: Añadir un scraper de una tienda nueva al servicio scraper (Mango Kids, H&M, Springfield, C&A, Hipercor, Lefties...). Cubre el reconocimiento de la web, la elección del identificador estable, la implementación de BaseStore, el registro y los tests con fixtures.
---

# Añadir una tienda al scraper

Objetivo: pasar de "una tienda de la lista del brief" a un `stores/<slug>.py` registrado,
tipado en estricto y con tests de parsing que no dependen de la red.

Referencias vivas del repo (léelas, no las asumas):
- Contrato: `services/scraper/src/scraper/stores/base.py`
- Ejemplo HTTP puro (endpoints AJAX JSON): `services/scraper/src/scraper/stores/zara.py`
- Ejemplo tras anti-bot (Akamai, navegador headless): `services/scraper/src/scraper/stores/sfera.py`
- Helper de navegador: `services/scraper/src/scraper/stores/browser.py`
- Registro: `services/scraper/src/scraper/stores/registry.py`

## 1. Reconocimiento (antes de escribir código)

No adivines la estructura de la web. Ábrela con el MCP de Playwright, navega a una
categoría de niño/niña y mira `browser_network_requests`: casi siempre hay un endpoint
JSON detrás del listado que evita parsear HTML (así funciona Zara).

Preguntas que hay que responder **antes** de teclear:

1. **¿Hay endpoint JSON o toca HTML?** JSON → `httpx` puro (imagen ligera, sin navegador).
   Solo si hay anti-bot que exige ejecutar el sensor JS se usa navegador (patrón Sfera).
2. **¿Cuál es el identificador estable del producto?** Requisito duro del brief: tiene que
   sobrevivir a cambios de temporada, de URL y de precio. En Zara es `seo.discernProductId`,
   **no** el id de la ficha. Un id inestable rompe la detección de altas/bajas y duplica el
   histórico de precios. Si no encuentras uno estable, párate y consúltalo antes de seguir.
3. **¿Y el de la variante?** Debe identificar talla+color de forma estable
   (`{producto}-{color}-{talla}` en Zara).
4. **¿La tienda permite preguntar por un producto concreto?** Si sí, implementa
   `SupportsAliveProbe` (opcional, ver punto 4).
5. **¿Qué categorías hoja mapean a nuestro vocabulario?** Alinea los slugs con los que ya
   usan Zara y Sfera (`ropa`/`zapateria`; `pantalones`, `camisetas`, ...) para que las dos
   tiendas sean comparables en la SPA. Varias hojas de la tienda pueden mapear al mismo slug.

Captura las respuestas crudas a `services/scraper/tests/fixtures/<slug>_<que_es>.json`.
Esas fixtures son el golden file de los tests: sin ellas no hay tests deterministas.

## 2. Implementar el scraper

`stores/<slug>.py`, siguiendo el reparto de `base.py` en **dos fases**:

- `list_catalog()` — barato, pocas peticiones. Devuelve un `ListingEntry` por producto con
  una `signature` construida con lo que ya se ve en el listado (típicamente precio por
  color). Si la huella no cambia, la ingesta se ahorra la petición de detalle.
- `fetch_details(entries)` — caro. Solo lo llama la ingesta para lo nuevo o lo cambiado.
- `scopes()` — los ámbitos (género/sección/categoría) que este scraper recorre. Acota la
  detección de bajas: solo se descataloga dentro de ámbitos realmente escaneados.

Reglas que el revisor va a mirar:
- Las funciones `parse_*` son **puras** (JSON/HTML → dataclasses), sin red. Es lo que se testea.
- Precios en `Decimal`, nunca `float`. Ojo a las tiendas que sirven céntimos como entero.
- Educación con el servidor: reintento con backoff solo en `{429, 500, 502, 503, 504}` y
  jitter entre peticiones (ver `_RETRYABLE_STATUS` en `zara.py`).
- `mypy` corre en `strict`: anota todo, incluidos los `dict[str, Any]` de las respuestas.

## 3. Registrar

Añade la entrada en `stores/registry.py`. Es literalmente una línea; si no la añades el
scraper no existe para `python -m scraper.run --retailer <slug>`.

## 4. Baja de productos (opcional pero muy recomendable)

La ausencia en el listado es una señal **indirecta**: un bloqueo o una reestructura de
categorías la falsea. Si la tienda deja sondear un producto concreto, implementa
`probe_alive()`. Semántica de tres estados con dos valores: `True` vivo, `False` retirado,
**ausente del mapa** = no concluyente. La ingesta solo da de baja lo confirmado; devolver
`False` ante un error de red provocaría bajas masivas falsas.

## 5. Tests

- `tests/test_<slug>_parse.py` — parsing contra las fixtures. Selecciona categorías **por
  atributos, no por índice**, para que reordenar la lista no rompa el test (ver la cabecera
  de `test_zara_parse.py`).
- Los tests que tocan red van aparte y no deben correr en CI por defecto (patrón
  `test_sfera_live.py` / `test_sfera_probe.py`).
- Los de ingesta necesitan `TEST_DATABASE_URL`; sin él se saltan (`tests/conftest.py`).

## 6. Verificar

```bash
just dry-run <slug>   # pasada completa sin escribir en BD
just check            # ruff + ruff format --check + mypy + pytest
```

`just dry-run` es el que de verdad valida el scraper: los tests solo prueban el parsing
contra una foto congelada de la web.
