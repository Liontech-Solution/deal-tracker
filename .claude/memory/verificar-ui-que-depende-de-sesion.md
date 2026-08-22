---
name: verificar-ui-que-depende-de-sesion
description: "las ramas del SPA que exigen `auth.enabled` no se pueden ejercer ni en local ni en dev, y apuntar el dev server a la API de QA no basta"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6323b303-54b3-4c3a-bc56-6821d96bd7ac
  modified: 2026-08-14T14:15:56.943Z
---

Una rama del SPA que dependa de **visitante anónimo con Keycloak encendido** (`auth.enabled === true`
y sin sesión) **no es observable ni en local ni en `dev`**. En `dev` los `KEYCLOAK_*` están sin poner
a propósito, así que `conCatalogo` sale `true` y esas ramas no se pisan nunca. Además
`deal-tracker-dev` **no tiene ingress**: no es navegable sin port-forward.

El atajo que parece que funciona y no funciona: `API_PROXY_TARGET=https://dealtracker-qa.liontechsolution.com`
en el dev server de vite sí hace que `GET /api/config` devuelva los tres campos del realm, pero
entonces `bootstrapAuth()` monta el iframe de silent-SSO con
`silentCheckSsoRedirectUri = http://localhost:<puerto>/silent-check-sso.html`, que el cliente
`deal-tracker-web` no admite. El iframe se queda colgado y **`auth.ready` no llega a ser `true`**, o
sea que acabas en un tercer estado que tampoco es el que querías. Medido el 14/08/2026 trabajando
#383.

**Cómo verificar entonces**: lo que sí se puede medir en local es todo lo que no depende de sesión
(por ejemplo el contraste de #384, con `getComputedStyle` en los dos temas, aislando la causa
quitando y devolviendo la propiedad en la misma sesión de navegador). Lo que depende de sesión se
comprueba **contra QA desplegado**, y si el cambio aún no está allí, se mide el camino equivalente
que QA ya sirve — para #383 valió leer el `redirect_uri` que `/acceso` manda a Keycloak. No toques
los `redirectUris` del cliente de Keycloak para poder probar en local: es un realm compartido.

**Y hay una tercera salida, que es la buena cuando el cambio todavía no está en QA** (medido el
22/08/2026 trabajando #551, la tarjeta de invitaciones de `/ajustes`). Lo que ata las manos arriba
no es que el SPA no pueda tener sesión en local: es que el realm es **compartido**. Con un Keycloak
**desechable en Docker** eso desaparece — realm propio, cliente propio con
`redirectUris: ["http://localhost:5173/*"]` y `webOrigins` a juego, usuario propio con contraseña.
El silent-SSO resuelve, `auth.ready` llega a `true` y la sesión es real:

```
docker run -d --name kc -p 8081:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.0 start-dev
# realm + cliente + usuario por la Admin API con el token de `admin-cli` del realm master
```

El API local apunta ahí con `KEYCLOAK_ISSUER_URL=http://localhost:8081/realms/<realm>`, y con
`KEYCLOAK_ADMIN_CLIENT_SECRET` y `RESEND_API_KEY` **de pega** `isInvitesConfigured()` se enciende:
basta para recorrer todo lo que no llama de verdad a Keycloak ni a Resend. Dos límites que conviene
saber antes de montarlo: el usuario de `app_user` **nace en la primera petición autenticada** (JIT),
así que el cupo se reparte por SQL después de entrar una vez; y el endpoint de Resend está **fijo en
el código** (`RESEND_ENDPOINT`, sin variable que lo desvíe), o sea que el envío bueno no se puede
simular — lo que sí se ve, y es el caso que más importa, es su **502**.

Con eso se recorrieron en navegador los ocho estados de esa pantalla sin tocar nada compartido. La
regla corta: **la sesión en local no es imposible, lo prohibido es el realm de otros.**

Ojo también a que **QA se autentica contra el realm `deal-tracker-dev`** en
`keycloak-dev.liontechsolution.com` — el `-dev` del nombre no es el entorno.

Relacionado: [[verificar-en-cluster-dev]], [[web-tests-sin-env-con-docker]], [[qa-test-user]].
