---
name: keycloak-cors-preflight-enganya
description: El preflight OPTIONS de Keycloak refleja cualquier origen; para saber si un origen está permitido hay que mirar el claim allowed-origins del token
metadata: 
  node_type: memory
  type: reference
  originSessionId: e66724d8-4b64-4942-8ca4-71585d0e5434
  modified: 2026-08-06T09:12:29.654Z
---

Diagnosticando un "blocked by CORS policy" contra Keycloak, **el preflight `OPTIONS` no sirve como
prueba**: devuelve 200 con `access-control-allow-origin` reflejando *cualquier* origen, incluso uno
inventado (`https://no-existe.example.com`). Ver el header ahí no significa que el origen esté
permitido.

Lo que sí lo dice es el claim **`allowed-origins`** del access token, que sale directo del campo
**Web Origins** del client:

```bash
python3 .claude/qa-login.py --token-only  # el flujo PKCE completo, sin navegador
# y decodificar el payload del JWT: claim allowed-origins
```

Trampa de configuración que costó encontrar (QA v0.1.7, #219): Web Origins llevaba
`https://host/*` — sintaxis de *redirect URI*. Keycloak compara contra la cabecera `Origin`, que
**nunca lleva ruta**, así que no casaba y no emitía el header en la respuesta real de `/token`. Debe
ser el origen desnudo (`https://host`) o `+`.

Corolario que despista mucho: **sin navegador no hay CORS**. `qa-login.py` hace exactamente el mismo
flujo que la SPA y funciona, así que un frente de API entero puede pasar en verde con el login roto
para todo usuario real. Ver [[qa-test-user]] y [[verificar-en-cluster-dev]].
