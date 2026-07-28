---
name: verificar-en-cluster-dev
description: Cómo ejecutar pasadas de scraper y SQL contra deal-tracker-dev; esta máquina no tiene Postgres ni psql
metadata: 
  node_type: memory
  type: project
  originSessionId: 842d188a-5bed-4fff-9049-9f5776ad8a66
  modified: 2026-07-28T12:17:15.473Z
---

La verificación con datos reales va **contra el cluster**, no en local: esta máquina no tiene
`postgres`, `psql` ni `pg_isready` instalados (ver [[kubeconfig-location]]).

**SQL contra la BD de dev**: pod efímero `postgres:16-alpine` + ConfigMap montado en `/sql`,
tomando `DATABASE_URL` del secret `deal-tracker-config` del namespace `deal-tracker-dev`. El pod
`pgrun` que hay en el namespace es el ejemplo a copiar.

**Pasada de scraper a mano**: los CronJobs están `suspend: true` en dev. `kubectl create job
--from=cronjob/...` **no admite añadir env**, así que hay que volcar el `jobTemplate` a un Job y
inyectar las variables (p. ej. `SCRAPER_DETAIL_MAX_AGE_DAYS`). Al Job de un solo uso **no** hay que
ponerle la etiqueta `app.kubernetes.io/instance: deal-tracker` ni dejarle el `ownerReference`
(ver [[gitops-argocd-selfheal]]).

**Para probar un cambio de scraper en dev hay que mergear a `main`**: el CI solo publica imagen en
`push` a main; en los PR el build es solo validación. Una vez publicada, se puede fijar el tag
`sha-<7>` directamente en el Job sin esperar a que ArgoCD sincronice.

La API de dev es alcanzable sin port-forward: `svc/deal-tracker-web` es LoadBalancer en
`192.169.2.16`. `limit` máximo del catálogo = 100, hay que paginar.
