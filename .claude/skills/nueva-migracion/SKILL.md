---
name: nueva-migracion
description: Crear una migración de esquema en db/migrations manteniendo la paridad con el espejo Drizzle del servicio web y el SQL crudo del scraper. Usar al añadir/cambiar tablas o columnas del contrato compartido.
disable-model-invocation: true
---

# Nueva migración de esquema

El esquema de `db/migrations` es el **contrato entre los dos servicios**, y ningún test lo
verifica de punta a punta. Un cambio de esquema toca hasta tres sitios en dos lenguajes; el
riesgo real no es escribir mal el SQL, es olvidarse de uno de los espejos.

## Quién escribe qué

- El **scraper** posee `retailer`, `product`, `variant`, `price_history`, `scrape_run`.
- El **web** posee `app_user`, `interest`, `notification` (y las tablas de Telegram/jobs).
- El web **lee** las del scraper; Drizzle NO genera migraciones (no se usa drizzle-kit).

## Pasos

1. **Numerar.** El siguiente hueco correlativo: `ls db/migrations/` y suma uno.
   Formato `NNNN_verbo_objeto.sql` (p.ej. `0011_add_variant_ean.sql`). Los dos migradores
   —`scraper/migrate.py` y `web/src/database/migrate.ts`— aplican por orden de nombre de
   fichero y registran en la **misma** tabla `schema_migrations`. Renumerar o renombrar una
   migración ya aplicada la deja aplicada bajo su nombre viejo: no se toca.

2. **Escribir el SQL neutro.** Sin dialecto propietario ni extensiones. La cabecera del
   fichero es un comentario que explica **por qué** existe la columna y qué pasa con las
   filas antiguas (¿backfill posible? ¿arrancan en NULL?). Mira `0010_add_product_image.sql`
   como referencia de tono y nivel de detalle.

3. **Espejar en Drizzle.** `services/web/src/database/schema.ts`. Tipo, nullability y
   nombre de columna tienen que coincidir con el SQL — Drizzle no lo valida en tiempo de
   compilación contra la BD real, así que una divergencia solo revienta en runtime.

4. **Revisar el SQL crudo del scraper.** `services/scraper/src/scraper/ingest.py` escribe
   con `INSERT ... ON CONFLICT` a mano. Si la columna nueva se puebla al ingerir, hay que
   añadirla ahí (y a la lista de columnas del `DO UPDATE SET`).

5. **Revisar los consumidores.** `grep` por el nombre de la tabla en
   `services/web/src/catalog/`, `interests/` y `matching/` por si hay SELECT crudos.

## Verificar

```bash
just migrate                                    # aplica en local
cd services/scraper && .venv/bin/pytest          # ingesta (necesita TEST_DATABASE_URL)
cd services/web && pnpm typecheck && pnpm test   # espejo Drizzle
```

Si `TEST_DATABASE_URL` no está definido los tests de ingesta se **saltan en silencio** —
un `pytest` en verde no prueba que la migración funcione. Compruébalo en la salida.

Antes de dar por buena la migración, lanza el agente `revisor-contrato-esquema` sobre el
diff: es exactamente el desajuste que busca.
