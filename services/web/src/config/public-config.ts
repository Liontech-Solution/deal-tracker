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
  /**
   * `true` si este entorno puede dar de alta a alguien por invitación (#548). Va aquí y no en un
   * endpoint aparte porque la SPA lo necesita en el mismo momento y por la misma razón que el
   * resto: para esconder lo que no se puede usar, igual que hoy esconde el login.
   */
  invitesEnabled: boolean;
}

const AUTH_DISABLED: PublicAuthConfig = { url: null, realm: null, clientId: null, invitesEnabled: false };

/**
 * Parte el issuer, que siempre tiene la forma `<base>/realms/<realm>` (es la misma URL sobre la que
 * `jwt.strategy.ts` monta el `jwksUri`). Devuelve `null` si no parsea.
 *
 * Vive aquí y se exporta porque lo necesitan dos sitios: esta config pública y el cliente de la
 * Admin API (`keycloak-admin/`), que arma sus URLs a partir de la misma base y el mismo realm.
 * Duplicar el parseo sería tener dos ideas distintas de qué realm estamos tocando.
 */
export function splitIssuer(issuerUrl: string): { url: string; realm: string } | null {
  const issuer = issuerUrl.trim().replace(/\/+$/, '');
  if (!issuer) return null;

  const marker = '/realms/';
  const at = issuer.lastIndexOf(marker);
  if (at <= 0) return null;

  const url = issuer.slice(0, at);
  const realm = issuer.slice(at + marker.length);
  // Un realm con `/` significa que el issuer trae path extra: no es una URL de realm válida.
  if (!url || !realm || realm.includes('/')) return null;

  return { url, realm };
}

/**
 * Devuelve los tres campos de auth a `null` cuando la auth no está configurada o el issuer no
 * parsea: la SPA lo interpreta como "auth deshabilitada" y sigue funcionando como catálogo público,
 * en vez de romper el arranque.
 *
 * Y en ese caso `invitesEnabled` se fuerza a `false` **aunque quien llame diga que sí**: el alta
 * necesita que la SPA sepa autenticarse después, así que publicar `true` sobre una auth que la SPA
 * no puede usar sería mentirle. Puede pasar de verdad — issuer puesto pero mal formado, con los dos
 * secretos presentes: `isInvitesConfigured()` solo mira que el issuer no esté vacío.
 */
export function buildPublicConfig(issuerUrl: string, clientId: string, invitesEnabled = false): PublicAuthConfig {
  const id = clientId.trim();
  const partes = splitIssuer(issuerUrl);
  if (!id || !partes) return AUTH_DISABLED;

  return { url: partes.url, realm: partes.realm, clientId: id, invitesEnabled };
}
