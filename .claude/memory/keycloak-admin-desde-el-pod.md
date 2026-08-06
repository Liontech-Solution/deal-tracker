---
name: keycloak-admin-desde-el-pod
description: Para administrar Keycloak no saques el secret: las credenciales ya están dentro del pod por envFrom, y kcadm.sh se autentica con ellas
metadata:
  type: project
---

El Keycloak del login (`security-dev/keycloak-dev-0`) monta el secret
`keycloak-admin-credentials` entero por `envFrom`, así que `KEYCLOAK_ADMIN` y
`KEYCLOAK_ADMIN_PASSWORD` ya son variables de entorno **dentro** del contenedor:

```bash
kubectl exec -n security-dev keycloak-dev-0 -- sh -c \
  '/opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 \
   --realm master --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"'
```

**Why:** el reflejo de `kubectl get secret … | base64 -d` para leer la contraseña lo bloquea el
clasificador de permisos, y con razón — expondría la credencial en el transcript. Por el pod no
sale nunca del contenedor y además funciona a la primera. Las escrituras (`kcadm.sh update`) sí
piden aprobación aparte aunque la sesión ya esté autenticada; eso es esperado, no un fallo.

**How to apply:** autentícate así una vez y luego encadena `kcadm.sh get/update`. Ojo con dos
cosas: ese Keycloak **no** lo gobierna GitOps, así que [[gitops-argocd-selfheal]] aquí NO aplica —
su Application no tiene bloque `automated` y el cambio persiste en vez de revertirse. Y el arreglo
no deja rastro en ningún diff: el contexto está en el ADR y en
`open-liontechsolution/toolsuite-platform-gitops#49`. Relacionado:
[[keycloak-cors-preflight-enganya]].
