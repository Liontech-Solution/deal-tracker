/**
 * Singleton de Keycloak (OIDC + PKCE) para la SPA. La config viene de variables de entorno de
 * Vite (`VITE_KC_*`); si faltan (dev local sin realm) la auth queda **deshabilitada** y el resto
 * de la app funciona como catálogo público. La validación real del flujo se hace al desplegar en
 * el cluster (namespace `dev`), donde el realm y el client existen de verdad.
 */
import Keycloak from 'keycloak-js';

const url = import.meta.env.VITE_KC_URL;
const realm = import.meta.env.VITE_KC_REALM;
const clientId = import.meta.env.VITE_KC_CLIENT_ID;

/** `true` solo si las tres variables están configuradas. */
export const authEnabled: boolean = Boolean(url && realm && clientId);

export const keycloak: Keycloak | null = authEnabled
  ? new Keycloak({ url: url as string, realm: realm as string, clientId: clientId as string })
  : null;

/**
 * Inicializa Keycloak una sola vez (memoizado): en React StrictMode los efectos se ejecutan dos
 * veces en dev y `init()` doble lanza excepción. `check-sso` no fuerza login: solo detecta una
 * sesión existente sin redirigir la página (usa el iframe silencioso).
 */
let initPromise: Promise<boolean> | null = null;

export function initKeycloak(): Promise<boolean> {
  if (!keycloak) return Promise.resolve(false);
  if (!initPromise) {
    initPromise = keycloak
      .init({
        onLoad: 'check-sso',
        pkceMethod: 'S256',
        silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
        checkLoginIframe: false,
      })
      .catch((err: unknown) => {
        console.error('Keycloak: init falló', err);
        return false;
      });
  }
  return initPromise;
}

/** Access token válido (lo refresca si le quedan <30 s). `null` si no hay sesión. */
export async function getFreshToken(): Promise<string | null> {
  if (!keycloak?.authenticated) return null;
  try {
    await keycloak.updateToken(30);
    return keycloak.token ?? null;
  } catch {
    return null;
  }
}
