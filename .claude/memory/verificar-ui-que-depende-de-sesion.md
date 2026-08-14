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

Ojo también a que **QA se autentica contra el realm `deal-tracker-dev`** en
`keycloak-dev.liontechsolution.com` — el `-dev` del nombre no es el entorno.

Relacionado: [[verificar-en-cluster-dev]], [[web-tests-sin-env-con-docker]], [[qa-test-user]].
