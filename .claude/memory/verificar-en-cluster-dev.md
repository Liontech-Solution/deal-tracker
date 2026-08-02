---
name: verificar-en-cluster-dev
description: Cómo ejecutar pasadas de scraper y SQL contra deal-tracker-dev; esta máquina no tiene Postgres ni psql
metadata: 
  node_type: memory
  type: project
  originSessionId: 842d188a-5bed-4fff-9049-9f5776ad8a66
  modified: 2026-07-31T09:52:13.305Z
---

Esta máquina no tiene `postgres`, `psql` ni `pg_isready` instalados (ver
[[kubeconfig-location]]), **ni `docker` ni `podman`** — comprobado el 02/08/2026; lo que este
apunte decía antes («sí tiene docker funcionando sin sudo») ya no vale y cuesta un rodeo creérselo.
Para los tests de ingesta y para probar el scraper contra las tiendas reales **sigue sin hacer
falta el cluster**, pero la Postgres se levanta en espacio de usuario, sin root: paquetes Arch
`postgresql` + `postgresql-libs` + `numactl` extraídos a un prefijo local y arrancados con
`initdb`/`pg_ctl` fijando `LD_LIBRARY_PATH`. El detalle, con sus dos gotchas de locale, está en la
memoria de usuario `dev-local-postgres`.

Un atajo que ahorra los dos gotchas: crear el cluster con
`initdb --auth=trust --username=postgres --encoding=UTF8 --locale=C.UTF-8`. Así cualquier
`CREATE DATABASE` hereda UTF8 + ctype `C.UTF-8` y sirve **a la vez** para los tests del scraper
(ingesta incluida) y para los del web, que con ctype `C` fallan en `color-canon`.

La verificación **del despliegue** sí va contra el cluster:

**SQL contra la BD de dev o de qa**, sin montar nada y sin escribir en el cluster:

```bash
kubectl -n deal-tracker-dev exec deploy/deal-tracker-web -- node -e "
const postgres=require('postgres'); const sql=postgres(process.env.DATABASE_URL,{max:1});
(async()=>{ console.table(await sql\`SELECT ...\`); await sql.end(); })()"
```

El pod del web ya tiene el cliente `postgres` y la `DATABASE_URL` del entorno, así que es un
`exec` y nada más. Es bastante más barato que el pod efímero `postgres:16-alpine` + ConfigMap que
recomendaba este apunte, y funciona con el modo de permisos automático. Sirve igual para
`deal-tracker-qa`.

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
