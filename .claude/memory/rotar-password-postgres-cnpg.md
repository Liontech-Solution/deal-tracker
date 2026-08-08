---
name: rotar-password-postgres-cnpg
description: "la contraseña de deal_tracker_prod la manda CNPG desde toolsuite-platform-gitops; un ALTER ROLE dura hasta la siguiente reconciliación y hay que resellar en dos repos, primero el de CNPG"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5dc6ebbf-6edc-4e72-ab8b-180406f658bf
  modified: 2026-08-08T13:30:02.512Z
---

`deal_tracker_prod` es un **role gestionado** por CNPG (`spec.managed.roles` con
`passwordSecret: deal-tracker-prod-db`). Un `ALTER ROLE ... PASSWORD` directo **no es una
rotación**: el bucle de reconciliación del operador devuelve el role a la contraseña del secret
unos minutos después.

Rotarla toca **dos repos**, y el orden importa:

1. `toolsuite-platform-gitops` → `apps/data/cnpg/environments/local/dev.yaml`, mapa `roleSecrets`,
   clave `deal-tracker-prod-db.password`. Sellado con
   `kubeseal --raw --namespace data-dev --name deal-tracker-prod-db`. **Este manda sobre el role.**
   Su Application (`cnpg-cluster-local-dev`) **no tiene `automated`**: hay que sincronizarla a mano
   (`kubectl -n argocd patch application ... --type merge -p '{"operation":{"sync":{...}}}'`,
   porque no hay CLI de argocd — ver [[kubeconfig-location]]).
2. `k3s-local-apps-manifests` → `deal-tracker/overlays/prod`, `DATABASE_URL` dentro de
   `deal-tracker-config`, con `./sella-clave.sh DATABASE_URL`.

**Why:** el 08/08/2026 lo hice al revés (ALTER ROLE + resellar solo el overlay) y tumbé prod ~40
min: app con la nueva, role con la vieja, `password authentication failed`, readiness 503 y
CrashLoopBackOff. El aviso estaba escrito en `dev.yaml:223-227` de ese tercer repo, que no había
leído. Es un mecanismo distinto del de [[gitops-argocd-selfheal]]: aquí no revierte ArgoCD, revierte
el operador de CNPG.

**How to apply:** antes de tocar cualquier credencial de una base del cluster, comprobar si el role
está en `spec.managed.roles` (`kubectl -n data-dev get cluster platform-postgres-dev -o
jsonpath='{.spec.managed}'`). Si lo está, la rotación empieza por el repo de CNPG y termina por el
overlay, nunca al revés — al revés deja la aplicación fuera hasta que sincronice el primero.
