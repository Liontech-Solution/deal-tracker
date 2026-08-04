---
name: verificar-en-cluster-dev
description: Cómo ejecutar pasadas de scraper y SQL contra deal-tracker-dev; esta máquina no tiene Postgres ni psql
metadata: 
  node_type: memory
  type: project
  originSessionId: 842d188a-5bed-4fff-9049-9f5776ad8a66
  modified: 2026-08-04T14:16:16.429Z
---

**Comprueba el equipo antes de creerte cualquiera de los dos caminos de abajo**: son [[kubeconfig-location|dos máquinas]] y no tienen lo mismo. `command -v docker postgres psql` cuesta un segundo y evita el rodeo. Medido:

- **02/08/2026**: ni `postgres`/`psql`/`pg_isready` ni `docker`/`podman`.
- **04/08/2026 (#149)**: `docker` **sí** está (29.7.1) y `psql`/`postgres` **no**. Un `postgres:16`
  desechable con las tres bases de la sesión salió más barato que el montaje en espacio de usuario:
  `docker run -d --name dt<issue>-pg -e POSTGRES_PASSWORD=… -e POSTGRES_USER=dt -p 55<issue>:5432
  postgres:16`, y dentro `CREATE DATABASE` para `dt_<issue>`, `dt_<issue>_test` y la de ctype `C`.
  Puerto propio por sesión, y `docker rm -f` al cerrar en vez de `dropdb`.

Para los tests de ingesta y para probar el scraper contra las tiendas reales **sigue sin hacer
falta el cluster**. Si no hay docker, la Postgres se levanta en espacio de usuario, sin root: paquetes Arch
`postgresql` + `postgresql-libs` + `numactl` extraídos a un prefijo local y arrancados con
`initdb`/`pg_ctl` fijando `LD_LIBRARY_PATH`. El detalle, con sus dos gotchas de locale, está en la
memoria de usuario `dev-local-postgres`.

Un atajo que ahorra los dos gotchas: crear el cluster con
`initdb --auth=trust --username=postgres --encoding=UTF8 --locale=C.UTF-8`. Así cualquier
`CREATE DATABASE` hereda UTF8 + ctype `C.UTF-8` y sirve para los tests del scraper (ingesta
incluida) y para los del web.

**Pero eso ya no basta para el web**: desde #105 los specs de canónica y de plegado de la búsqueda
se ejecutan contra DOS bases, y la segunda tiene que reproducir la del cluster, que es
`UTF8 | C | C` (`datcollate` y `datctype` a `C` a secas, en dev y en qa). Se crea en el mismo
servidor, sin otro `initdb`:

```sql
CREATE DATABASE deal_tracker_ctype_c TEMPLATE template0 ENCODING 'UTF8'
  LC_COLLATE 'C' LC_CTYPE 'C';   -- TEMPLATE template0 es obligatorio para cambiar el locale
```

y se pasa en `TEST_DATABASE_URL_CTYPE_C` junto a `TEST_DATABASE_URL`. Sin ella los specs no fallan:
**se saltan**, que es peor de lo que parece — con el locale bueno `lower('ÍNDIGO')` da `'índigo'` y
todo sale verde mientras el cluster hace otra cosa.

Los paquetes de Postgres se extraen sin sudo desde un mirror de Arch (`postgresql`,
`postgresql-libs` y `numactl`, que hace falta para el binario del servidor) a
`~/.local/share/pgsql-local`, y los binarios necesitan `LD_LIBRARY_PATH=<prefijo>/usr/lib`.

**Al arrancarlo hay que decirle dónde pone el socket**, o falla con un `FATAL` que no menciona el
socket sino un `.lock`: `could not create lock file "/run/postgresql/.s.PGSQL.5432.lock"`. Ese
directorio es del paquete del sistema, que aquí no está instalado. Las dos bases ya existen
(`deal_tracker_test` y `deal_tracker_ctype_c`), así que arrancar y usarlo es:

```bash
export PG=~/.local/share/pgsql-local LD_LIBRARY_PATH=~/.local/share/pgsql-local/usr/lib
$PG/usr/bin/pg_ctl -D $PG/data -l $PG/server.log -o "-k /tmp" start
export TEST_DATABASE_URL=postgres://dealtracker@127.0.0.1:5432/deal_tracker_test
export TEST_DATABASE_URL_CTYPE_C=postgres://dealtracker@127.0.0.1:5432/deal_tracker_ctype_c
```

**Con varias sesiones a la vez, una base por sesión.** El servidor es uno y el `conftest.py` del
scraper hace `TRUNCATE` de las tablas de datos antes de **cada** test, así que compartir
`deal_tracker_test` entre sesiones paralelas borra lo que otra estaba mirando — y a uno mismo: el
03/08/2026 un `pytest` se llevó por delante el histórico de `vigia_run` que acababa de generar a
mano con el vigía. La convención que ya había en la máquina (`dt_98`) es la buena:
`createdb -h /tmp -U dealtracker dt_<issue>`; las migraciones las aplica sola la fixture `db_conn`.

**Y dentro de la propia sesión hacen falta DOS bases, no una.** Una base por sesión resuelve el
choque entre sesiones, no el de la sesión consigo misma: si `DATABASE_URL` (la pasada real) y
`TEST_DATABASE_URL` (pytest) apuntan a la misma, el primer `pytest` borra el catálogo que acabas de
ingerir. Pasó el 03/08/2026 en #80 — 1568 productos y 12386 variantes de una pasada de 45 minutos,
truncados por correr la suite después. La separación buena es `dt_<issue>` para la ingesta y
`dt_<issue>_test` para los tests, y **exportarlas en el comando**, no dejarlas colgando del entorno,
que es como se cuelan cruzadas. Ojo también con las medidas de proceso mientras hay varias sesiones:
`ps -C python | sort -rn | head -1` coge el python más grande de la MÁQUINA, no el tuyo — para
medir RSS de una pasada, por PID.

Un worktree tampoco trae `.venv` ni `node_modules`, así que en él hay que rehacer
`python -m venv` + `pip install -e "services/scraper[dev]"` y `pnpm install` (ver
[[scraper-sin-just-ni-env]], que además explica que `just` no está instalado).

La verificación **del despliegue** sí va contra el cluster:

**SQL contra la BD de dev o de qa**, sin montar nada y sin escribir en el cluster.

**El camino corto es el pod de la propia CNPG, que sí trae `psql`** (medido el 04/08/2026, y es
bastante más barato que todo lo que sigue — un `-c` y ya):

```bash
kubectl -n data-dev exec platform-postgres-dev-1 -c postgres -- \
  psql -U postgres -d deal_tracker_qa -c "SELECT ..."
```

El pod es el de la cluster CNPG `platform-postgres-dev` (ver [[kubeconfig-location]]), y desde ahí
se llega a las bases de los dos entornos: `deal_tracker` (dev) y `deal_tracker_qa`. Ojo con las
comillas si la consulta lleva `$$` o `%`. El resto de este apartado es el rodeo que hacía falta
cuando se creía que solo se podía entrar por el pod del web:

```bash
kubectl -n deal-tracker-dev exec deploy/deal-tracker-web -- node -e "
const postgres=require('postgres'); const sql=postgres(process.env.DATABASE_URL,{max:1});
(async()=>{ console.table(await sql\`SELECT ...\`); await sql.end(); })()"
```

El pod del web ya tiene el cliente `postgres` y la `DATABASE_URL` del entorno, así que es un
`exec` y nada más. Es bastante más barato que el pod efímero `postgres:16-alpine` + ConfigMap que
recomendaba este apunte, y funciona con el modo de permisos automático. Sirve igual para
`deal-tracker-qa`.

**Ojo con la resolución de módulos en ese `exec`** (medido el 03/08/2026): `node -e "require('pg')"`
falla con `MODULE_NOT_FOUND`, y `require('postgres')` también si el cwd del exec no es `/app` —
meterle un `cd /app &&` por delante **tampoco** lo arregló. Lo que sí funciona es un fichero copiado
al pod (`kubectl cp`) que importe por ruta absoluta:
`import postgres from '/app/node_modules/postgres/src/index.js'`. Y el driver es `postgres`, no
`pg`: `pg` no está en la imagen. Tampoco hay `psql` dentro del contenedor.

**Pasada de scraper a mano**: los CronJobs están `suspend: true` en dev. `kubectl create job
--from=cronjob/...` **no admite añadir env**, así que hay que volcar el `jobTemplate` a un Job y
inyectar las variables (p. ej. `SCRAPER_DETAIL_MAX_AGE_DAYS`). Al Job de un solo uso **no** hay que
ponerle la etiqueta `app.kubernetes.io/instance: deal-tracker` ni dejarle el `ownerReference`
(ver [[gitops-argocd-selfheal]]).

**Para probar un cambio de scraper EN DEV hay que mergear a `main`**: el CI solo publica imagen en
`push` a main; en los PR el build es solo validación. (Para probar el cambio *en sí*, la Postgres
local de arriba llega y no exige mergear nada.) Una vez publicada, se puede fijar el tag
`sha-<7>` directamente en el Job sin esperar a que ArgoCD sincronice.

La API de dev es alcanzable sin port-forward: `svc/deal-tracker-web` es LoadBalancer en
`192.169.2.16`. `limit` máximo del catálogo = 100, hay que paginar.

**Para MEDIR el vocabulario de dev tampoco hace falta tocar el cluster**:
`GET /api/catalog/facets?barefoot=all` devuelve **la lista completa de colores, tallas, categorías
y tiendas vivas** — es un GET público, lo mismo que pide la SPA. Con eso volcado a un CSV y
cargado en la Postgres local se miden cosas como cuántos valores funde una
normalización, sin datos inventados. Ojo con lo que la faceta **no** dice: lista las tiendas con
producto vivo, así que una tienda registrada que no aparece puede no haber ingerido nunca (fue así
con lefties e hipercor, #99 y #93) — eso se ve en `scrape_run`, no aquí.
`?barefoot=all` es imprescindible: por defecto la faceta va
acotada a `si` y esconde parte del catálogo. Para saber **de qué tienda** es cada valor,
`GET /api/catalog/products?color=<valor>&barefoot=all&limit=100` y mirar `retailerName`.
