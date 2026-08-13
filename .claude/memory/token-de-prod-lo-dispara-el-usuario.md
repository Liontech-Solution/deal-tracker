---
name: token-de-prod-lo-dispara-el-usuario
description: medir prod por HTTP exige que el comando lo lance el usuario con `!`; el token dura 300 s y no sobrevive al viaje de ida y vuelta
metadata:
  type: feedback
---

Para medir el catálogo de prod hace falta un access token de Keycloak, y **no hay equivalente de
`qa-token.sh` para ese entorno**: el client `deal-tracker-web` es público con PKCE y tiene
`directAccessGrantsEnabled: false`, así que no existe grant por contraseña. El token se saca a mano
de la SPA (DevTools → Network → cabecera `Authorization`) y **dura 300 s**.

**Why:** pedirle el token al usuario y usarlo yo no funciona — el viaje de ida y vuelta (copiar,
pegar, que yo lo reciba y lance el comando) se come la ventana entera. Pasó dos veces seguidas: la
primera llegó caducado por 24 s. Y el 401 resultante vuelve en ~80 ms, que parece un problema de
configuración y no lo es.

**How to apply:** dale al usuario el comando completo para que lo dispare él con el prefijo `!`, con
el token como hueco a rellenar. Dos detalles que fallaron y conviene meter en el propio comando:
la cabecera `Authorization` ya lleva `Bearer`, así que hay que quitárselo al valor pegado
(`T=${TOKEN#Bearer }`), y conviene imprimir la vida restante del token antes de gastar la medición.
El estado del shell no persiste entre invocaciones de `!`, así que cada comando tiene que traer el
token dentro. Ver [[qa-test-user]] para el camino equivalente en QA, que sí está automatizado.
