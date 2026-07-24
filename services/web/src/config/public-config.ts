/**
 * Config pública que la SPA necesita para arrancar Keycloak, derivada de las variables que el pod
 * **ya tiene**. Así una única imagen sirve para dev/qa/prod: el login se enciende según el entorno,
 * no según el build.
 */

export interface PublicAuthConfig {
  /** URL base de Keycloak (sin el `/realms/<realm>`). `null` si no hay auth configurada. */
  url: string | null;
  realm: string | null;
  clientId: string | null;
}

const AUTH_DISABLED: PublicAuthConfig = { url: null, realm: null, clientId: null };

/**
 * Parte el issuer, que siempre tiene la forma `<base>/realms/<realm>` (es la misma URL sobre la que
 * `jwt.strategy.ts` monta el `jwksUri`). Devuelve los tres campos a `null` cuando la auth no está
 * configurada o el issuer no parsea: la SPA lo interpreta como "auth deshabilitada" y sigue
 * funcionando como catálogo público, en vez de romper el arranque.
 */
export function buildPublicConfig(issuerUrl: string, clientId: string): PublicAuthConfig {
  const issuer = issuerUrl.trim().replace(/\/+$/, '');
  const id = clientId.trim();
  if (!issuer || !id) return AUTH_DISABLED;

  const marker = '/realms/';
  const at = issuer.lastIndexOf(marker);
  if (at <= 0) return AUTH_DISABLED;

  const url = issuer.slice(0, at);
  const realm = issuer.slice(at + marker.length);
  // Un realm con `/` significa que el issuer trae path extra: no es una URL de realm válida.
  if (!url || !realm || realm.includes('/')) return AUTH_DISABLED;

  return { url, realm, clientId: id };
}
