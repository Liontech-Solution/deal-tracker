/**
 * Singleton de Keycloak (OIDC + PKCE) para la SPA. La config **no** viene del build: se pide en
 * runtime a `GET /api/config`, que la deriva de las variables que el pod ya tiene. Así la misma
 * imagen sirve para dev/qa/prod y el login se enciende según el entorno.
 *
 * Si la API no devuelve los tres campos (dev local sin realm) o algo falla, la auth queda
 * **deshabilitada** y el resto de la app funciona como catálogo público.
 */
import Keycloak from 'keycloak-js';

import { ApiError, apiGet } from '../api/client';
import { obtenerToken } from './refresh';

interface PublicAuthConfig {
  url: string | null;
  realm: string | null;
  clientId: string | null;
}

export interface AuthBootstrap {
  /** `true` solo si había config completa y Keycloak inicializó. */
  enabled: boolean;
  authenticated: boolean;
}

const DISABLED: AuthBootstrap = { enabled: false, authenticated: false };

let kc: Keycloak | null = null;

/** Instancia viva, o `null` si la auth está deshabilitada o aún no ha arrancado. */
export function getKeycloak(): Keycloak | null {
  return kc;
}

/**
 * Pide la config y arranca Keycloak. Memoizado (single-flight): en React StrictMode los efectos
 * se ejecutan dos veces en dev y un `init()` doble lanza excepción.
 */
let bootstrapPromise: Promise<AuthBootstrap> | null = null;

export function bootstrapAuth(): Promise<AuthBootstrap> {
  if (!bootstrapPromise) bootstrapPromise = doBootstrap();
  return bootstrapPromise;
}

async function doBootstrap(): Promise<AuthBootstrap> {
  let config: PublicAuthConfig;
  try {
    config = await apiGet<PublicAuthConfig>('/config');
  } catch (err: unknown) {
    console.error('Keycloak: no pude leer /api/config', err);
    return DISABLED;
  }

  const { url, realm, clientId } = config;
  if (!url || !realm || !clientId) return DISABLED;

  const instance = new Keycloak({ url, realm, clientId });
  try {
    // `check-sso` no fuerza login: solo detecta una sesión existente sin redirigir la página
    // (usa el iframe silencioso).
    const authenticated = await instance.init({
      onLoad: 'check-sso',
      pkceMethod: 'S256',
      silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
      checkLoginIframe: false,
    });
    kc = instance;
    return { enabled: true, authenticated };
  } catch (err: unknown) {
    console.error('Keycloak: init falló', err);
    return DISABLED;
  }
}

/**
 * Access token válido (lo refresca si le quedan <30 s). `null` **solo** si no hay sesión: ese es el
 * caso en que la petición anónima es la correcta (`/config` durante el arranque, y el catálogo allí
 * donde no hay realm).
 *
 * Si hay sesión y aun así no se consigue token, esto **lanza** en vez de devolver `null` (#262).
 * Devolver `null` hacía que `authHeaders()` omitiera la cabecera y la petición saliera anónima
 * contra un endpoint que exige sesión: un 401 garantizado, escrito en la consola por la pila de red
 * del navegador —donde el código de la app no puede silenciarlo— y que se curaba solo al siguiente
 * ciclo. Lanzando, la petición condenada no llega a salir: `apiGet`/`apiSend` rechazan antes del
 * `fetch`, y el `retry: 1` que el `QueryClient` ya trae le da su segunda oportunidad.
 *
 * La sesión muerta de verdad no se enmascara: sale por la misma puerta que un 401 real y, además,
 * `AuthProvider` la ve por `onAuthLogout` y `RequireSession` manda a `/acceso`.
 */
export async function getFreshToken(): Promise<string | null> {
  const r = await obtenerToken(kc);
  switch (r.estado) {
    case 'sin-sesion':
      return null;
    case 'token':
      return r.token;
    case 'sesion-muerta':
      throw new ApiError(401, 'Tu sesión ha caducado, vuelve a iniciar sesión');
    case 'refresco-fallido':
      throw new ApiError(401, 'No pude renovar la sesión');
  }
}
