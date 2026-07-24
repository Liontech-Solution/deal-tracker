#!/usr/bin/env python3
"""Login OIDC (Authorization Code + PKCE) contra el Keycloak de QA y volcado de un access token.

Es el mismo flujo que hace la SPA (keycloak-js): sirve para obtener un Bearer de un usuario real y
ejercitar la API protegida de QA en pruebas, sin navegador.

Uso:
    python3 .claude/qa-login.py            # imprime claims + guarda el token en qa_access_token.local
    TOKEN=$(python3 .claude/qa-login.py --token-only)
    curl -H "Authorization: Bearer $TOKEN" https://dealtracker-qa.liontechsolution.com/api/interests

Las credenciales se leen de .claude/qa-test-user.local (gitignored). El client `deal-tracker-web`
es público con PKCE y NO permite direct grant, por eso se hace el flujo por código.
"""
import base64, hashlib, html, os, re, secrets, sys, json, urllib.parse as up
import urllib.request as ur
from http.cookiejar import CookieJar

KC = "https://keycloak-dev.liontechsolution.com"
REALM = "deal-tracker-dev"
CLIENT_ID = "deal-tracker-web"
REDIRECT = "https://dealtracker-qa.liontechsolution.com/"  # debe estar en los redirect URIs del client
AUTH = f"{KC}/realms/{REALM}/protocol/openid-connect/auth"
TOKEN = f"{KC}/realms/{REALM}/protocol/openid-connect/token"

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(HERE, "qa-test-user.local")
# Perfil por si Keycloak fuerza VERIFY_PROFILE en el primer login (una vez).
PROFILE = {"email": "test-qa@liontechsolution.com", "firstName": "Test", "lastName": "QA"}


def read_creds():
    if not os.path.exists(CREDS):
        sys.exit(f"Falta {CREDS} (gitignored). Recréalo con QA_KC_USERNAME/QA_KC_PASSWORD.")
    kv = {}
    for line in open(CREDS):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv["QA_KC_USERNAME"], kv["QA_KC_PASSWORD"]


def main():
    user, password = read_creds()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    cj = CookieJar()

    class NoRedirect(ur.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    follow = ur.build_opener(ur.HTTPCookieProcessor(cj))
    noredir = ur.build_opener(ur.HTTPCookieProcessor(cj), NoRedirect)

    def post(url, payload):
        data = up.urlencode(payload).encode()
        try:
            r = noredir.open(ur.Request(url, data=data))
            return r.status, r.headers.get("Location"), r.read().decode()
        except ur.HTTPError as e:
            return e.code, e.headers.get("Location"), e.read().decode()

    q = up.urlencode(dict(client_id=CLIENT_ID, response_type="code", scope="openid",
                          redirect_uri=REDIRECT, state="s", nonce="n",
                          code_challenge=challenge, code_challenge_method="S256"))
    page = follow.open(f"{AUTH}?{q}").read().decode()
    m = re.search(r'action="(https[^"]+authenticate[^"]*)"', page)
    if not m:
        sys.exit("No renderizó el formulario de login (¿redirect_uri no registrado?).")
    _, loc, _ = post(html.unescape(m.group(1)),
                     {"username": user, "password": password, "credentialId": ""})

    hops = 0
    while loc and "code=" not in loc and hops < 4:
        hops += 1
        p = follow.open(loc).read().decode()
        action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', p).group(1))
        names = set(re.findall(r'name="([^"]+)"', p))
        payload = {k: v for k, v in PROFILE.items() if k in names}
        for h in re.findall(r'<input[^>]+type="hidden"[^>]*>', p):
            n = re.search(r'name="([^"]+)"', h)
            v = re.search(r'value="([^"]*)"', h)
            if n:
                payload[n.group(1)] = v.group(1) if v else ""
        _, loc, _ = post(action, payload)

    if not loc or "code=" not in loc:
        sys.exit(f"Login falló (¿credenciales?). Última redirección: {loc}")

    code = up.parse_qs(up.urlparse(loc).query)["code"][0]
    td = up.urlencode(dict(grant_type="authorization_code", code=code, client_id=CLIENT_ID,
                           redirect_uri=REDIRECT, code_verifier=verifier)).encode()
    tj = json.loads(ur.urlopen(ur.Request(TOKEN, data=td)).read().decode())
    at = tj["access_token"]

    if "--token-only" in sys.argv:
        print(at)
        return
    pl = json.loads(base64.urlsafe_b64decode(at.split(".")[1] + "=="))
    print("access_token OK · expires_in", tj["expires_in"])
    print("  preferred_username:", pl.get("preferred_username"), "· email:", pl.get("email"))
    print("  iss:", pl["iss"], "· aud:", pl.get("aud"))
    out = os.path.join(HERE, "qa_access_token.local")
    open(out, "w").write(at)
    print("  token en", out)


if __name__ == "__main__":
    main()
