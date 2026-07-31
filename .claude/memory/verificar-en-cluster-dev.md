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
[[kubeconfig-location]]), pero **sí tiene `docker` funcionando sin sudo**. Para los tests de
ingesta y para probar el scraper contra las tiendas reales **no hace falta el cluster**:

```bash
docker run -d --rm --name dt-test-pg -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=deal_tracker_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL="postgresql://postgres:test@127.0.0.1:55432/deal_tracker_test" .venv/bin/pytest
```

Con eso corren los ~30 tests de ingesta que si no se saltan, y usando la misma URL como
`DATABASE_URL` se puede hacer una pasada real completa (`python -m scraper.run --migrate`) y
consultarla con `docker exec dt-test-pg psql`. Acordarse de `docker stop dt-test-pg` al acabar.

La verificación **del despliegue** sí va contra el cluster:

**SQL contra la BD de dev**: pod efímero `postgres:16-alpine` + ConfigMap montado en `/sql`,
tomando `DATABASE_URL` del secret `deal-tracker-config` del namespace `deal-tracker-dev`. El pod
`pgrun` que hay en el namespace es el ejemplo a copiar.

**Pasada de scraper a mano**: los CronJobs están `suspend: true` en dev. `kubectl create job
--from=cronjob/...` **no admite añadir env**, así que hay que volcar el `jobTemplate` a un Job y
inyectar las variables (p. ej. `SCRAPER_DETAIL_MAX_AGE_DAYS`). Al Job de un solo uso **no** hay que
ponerle la etiqueta `app.kubernetes.io/instance: deal-tracker` ni dejarle el `ownerReference`
(ver [[gitops-argocd-selfheal]]).

**Para probar un cambio de scraper EN DEV hay que mergear a `main`**: el CI solo publica imagen en
`push` a main; en los PR el build es solo validación. (Para probar el cambio *en sí*, la Postgres
en docker de arriba llega y no exige mergear nada.) Una vez publicada, se puede fijar el tag
`sha-<7>` directamente en el Job sin esperar a que ArgoCD sincronice.

La API de dev es alcanzable sin port-forward: `svc/deal-tracker-web` es LoadBalancer en
`192.169.2.16`. `limit` máximo del catálogo = 100, hay que paginar.

**Para MEDIR el vocabulario de dev no hace falta tocar el cluster, y conviene no tocarlo**: el
modo automático de permisos bloquea los escritos con `kubectl` (crear el ConfigMap y el pod de
arriba, y también `kubectl exec` contra el pod de CNPG), así que ese camino se queda a medias.
`GET /api/catalog/facets?barefoot=all` devuelve **la lista completa de colores, tallas, categorías
y tiendas vivas** — es un GET público, lo mismo que pide la SPA. Con eso volcado a un CSV y
cargado en la Postgres de docker se miden en local cosas como cuántos valores funde una
normalización, sin datos inventados. `?barefoot=all` es imprescindible: por defecto la faceta va
acotada a `si` y esconde parte del catálogo. Para saber **de qué tienda** es cada valor,
`GET /api/catalog/products?color=<valor>&barefoot=all&limit=100` y mirar `retailerName`.
