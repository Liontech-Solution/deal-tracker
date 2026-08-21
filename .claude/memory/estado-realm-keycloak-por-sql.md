---
name: estado-realm-keycloak-por-sql
description: para LEER la config de un realm de Keycloak, SQL contra el esquema `keycloak` de keycloak_dev, que es más barato que kcadm desde el pod
metadata:
  type: reference
---

Para **leer** el estado de un realm (flags de registro, SMTP, política de contraseña, cuántos
usuarios tiene) no hace falta `kcadm.sh` ni exec al pod de Keycloak: todo está en la base
`keycloak_dev` de `platform-postgres-dev`, esquema **`keycloak`** (no `public`, y por eso
`from realm` falla con «relation does not exist»).

```sql
select r.name, count(u.id), r.registration_allowed, r.reset_password_allowed,
       r.verify_email, coalesce(r.password_policy,'(ninguna)')
  from keycloak.realm r
  left join keycloak.user_entity u on u.realm_id = r.id
 group by r.name, r.registration_allowed, r.reset_password_allowed, r.verify_email, r.password_policy;
```

`keycloak.realm_smtp_config` es una tabla de claves: **cero filas para un realm = `smtpServer: {}`**.

Sirve para medir de una tirada los dos realms (`deal-tracker-dev`, que es contra el que se autentica
QA, y `deal-tracker-prod`) y de paso cruzarlo con `app_user` de la base de la aplicación, que es
donde de verdad se ve si un alta llegó a su destino. Ver [[verificar-en-cluster-dev]] y
[[keycloak-admin-desde-el-pod]] — aquél sigue siendo el camino para **escribir**.
