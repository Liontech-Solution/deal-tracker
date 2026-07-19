/** Usuario autenticado tal y como queda en `req.user` tras validar el JWT de Keycloak. */
export interface AuthUser {
  /** Id interno en `app_user` (aprovisionado JIT). Se usa para scoping de intereses. */
  id: number;
  /** `sub` de Keycloak (estable por usuario). */
  keycloakSub: string;
  email: string | null;
  displayName: string | null;
}

/** Claims relevantes del token OIDC de Keycloak. */
export interface KeycloakClaims {
  sub: string;
  email?: string;
  name?: string;
  preferred_username?: string;
}
