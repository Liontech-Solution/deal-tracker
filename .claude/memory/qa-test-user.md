---
name: qa-test-user
description: Usuario de prueba de QA para el login OIDC y cómo obtener un token para probar la API
metadata:
  type: reference
---

QA tiene un usuario de prueba en Keycloak (realm `deal-tracker-dev`, que QA reutiliza — ver
[[kubeconfig-location]] para el acceso al cluster): **username `test-qa`**. El login real (OIDC +
PKCE) quedó verificado end-to-end el 2026-07-25 contra `https://dealtracker-qa.liontechsolution.com`.

**Para obtener un access token en pruebas** (el client `deal-tracker-web` es público con PKCE y no
permite direct grant, así que hay que hacer el flujo por código):

```bash
python3 .claude/qa-login.py                       # claims + guarda qa_access_token.local
TOKEN=$(python3 .claude/qa-login.py --token-only)
curl -H "Authorization: Bearer $TOKEN" https://dealtracker-qa.liontechsolution.com/api/interests
```

- **Contraseña**: en `.claude/qa-test-user.local` (gitignored vía `*.local`, NO se versiona). En un
  equipo nuevo hay que recrear ese fichero a mano (formato `QA_KC_USERNAME=` / `QA_KC_PASSWORD=`).
- El perfil del user se completó en el primer login (email `test-qa@liontechsolution.com`, nombre
  `Test QA`) para salvar el `VERIFY_PROFILE` de Keycloak; logins posteriores ya entran directos.
- El token dura 300 s. Redirect URI registrado: `https://dealtracker-qa.liontechsolution.com/*`
  (la LAN `.17` NO está registrada, a propósito).
- **`qa-token.sh` y `qa-login.py` solo funcionan con el `cwd` en la raíz del repo**: el script busca
  `./.claude/qa-login.py` por ruta relativa, así que llamarlo por ruta absoluta desde otro
  directorio (un script en el scratchpad, por ejemplo) falla con `✖ no encuentro ./.claude/qa-login.py`
  y devuelve **cadena vacía en vez de error**, con lo que el `curl` siguiente da un 401 que se lee
  como API caída. En Python: `subprocess.run([...], cwd='/home/juanjocop/Proyectos/deal-tracker')`.
