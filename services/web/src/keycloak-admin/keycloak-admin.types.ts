/**
 * Tipos de lo que consumimos de la Admin API de Keycloak. Como en el cliente de correo, no es un
 * espejo del contrato entero: solo el sobre de las dos llamadas que hacemos.
 */

/** Respuesta de `POST /protocol/openid-connect/token` con `grant_type=client_credentials`. */
export interface TokenResponse {
  access_token?: string;
  /** Segundos de validez. Se usa para saber cuándo toca pedir otro, no para confiar en él. */
  expires_in?: number;
}

/** Lo que hay que saber para crear la cuenta del alta. El correo hace también de nombre de usuario. */
export interface NewUser {
  email: string;
  firstName?: string;
  password: string;
}

/**
 * - `exists`: Keycloak devolvió 409 — ya hay un usuario con ese correo. **Se distingue a propósito**
 *   de un error genérico: es un caso real (se puede invitar a alguien que ya tiene cuenta) y qué
 *   hacer con él lo decide el endpoint del alta (#549), pero poder distinguirlo es de aquí.
 * - `disabled`: no hay secreto de administración en este entorno (`dev` siempre).
 * - `auth`: no nos dieron token — el client está mal configurado o el secreto no vale.
 * - `http`: Keycloak contestó otra cosa que no esperábamos.
 * - `network`: no hubo respuesta (red caída, timeout).
 */
export type KeycloakAdminFailure = 'exists' | 'disabled' | 'auth' | 'http' | 'network';

/** Resultado de crear un usuario. Discriminado por la misma razón que el del correo (#547). */
export type CreateUserResult = { ok: true; userId: string } | { ok: false; reason: KeycloakAdminFailure };
