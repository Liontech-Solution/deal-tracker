import { Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import type { EnvConfig } from '../config/configuration';
import { splitIssuer } from '../config/public-config';
import type { CreateUserResult, NewUser, TokenResponse } from './keycloak-admin.types';

/** Margen con el que se considera caducado el token: se renueva antes de que expire de verdad. */
const TOKEN_MARGEN_MS = 30_000;

/**
 * Cliente de la **Admin API** de Keycloak: la primera escritura contra el realm que sale de este
 * repo. Hasta ahora el servicio solo sabía *validar* tokens (`issuer` + `aud` + `sub`, sin roles ni
 * `realm_access`); el alta por invitación necesita además **crear** la cuenta.
 *
 * ── NO USA EL CLIENT DE LA SPA, Y NO PODRÍA ──
 *
 * `deal-tracker-web` es **público**, con PKCE y `directAccessGrantsEnabled: false` a propósito.
 * Esto habla con un client **confidencial** aparte (`KEYCLOAK_ADMIN_CLIENT_ID`, por defecto
 * `deal-tracker-api`) que tiene service account y el rol `manage-users` de `realm-management`. Ese
 * client se declara en los dos realms desde `toolsuite-platform-gitops`, y su secreto llega aquí
 * como `KEYCLOAK_ADMIN_CLIENT_SECRET`. Sin secreto el cliente queda **deshabilitado**, igual que el
 * de Telegram y el de correo: es lo que corre en `dev`, que no trae ninguna `KEYCLOAK_*`.
 *
 * Mismo molde que los otros dos: `fetch` nativo, sin SDK, y el fallo viaja en el resultado en vez de
 * subir como excepción.
 */
@Injectable()
export class KeycloakAdminClient {
  private readonly logger = new Logger(KeycloakAdminClient.name);
  private readonly clientId: string;
  private readonly clientSecret: string;
  /** Base de Keycloak y realm, del mismo issuer que valida los tokens. `null` si no hay auth. */
  private readonly realmParts: { url: string; realm: string } | null;
  private cachedToken: { value: string; expiresAt: number } | null = null;

  constructor(@Inject(ConfigService) config: ConfigService<EnvConfig, true>) {
    this.clientId = config.get('KEYCLOAK_ADMIN_CLIENT_ID', { infer: true });
    this.clientSecret = config.get('KEYCLOAK_ADMIN_CLIENT_SECRET', { infer: true });
    // Se reutiliza el parseo de la config pública para no tener dos ideas distintas de qué realm
    // estamos tocando: el que administramos es exactamente el que emite los tokens que validamos.
    this.realmParts = splitIssuer(config.get('KEYCLOAK_ISSUER_URL', { infer: true }));
  }

  /** Hay realm y secreto: se puede administrar de verdad. */
  get enabled(): boolean {
    return this.realmParts !== null && this.clientSecret !== '';
  }

  /**
   * Crea la cuenta del alta. Devuelve el `id` que Keycloak pone en la cabecera `Location`.
   *
   * **`emailVerified: true` es una decisión, no un atajo**: el token que el invitado acaba de
   * canjear llegó a ese buzón y a ningún otro, así que el correo ya está probado por el propio
   * flujo. Es lo que permite que el alta sea **un solo correo con nuestra marca** en vez de dos, el
   * nuestro y el de verificación de Keycloak.
   *
   * La contraseña va como credencial **no temporal**: quien acaba de elegirla en el formulario del
   * alta no debe encontrarse un "cámbiala" en el primer login.
   */
  async createUser(user: NewUser): Promise<CreateUserResult> {
    if (!this.enabled || !this.realmParts) {
      this.logger.warn('Admin API de Keycloak deshabilitada (sin KEYCLOAK_ADMIN_CLIENT_SECRET); alta omitida');
      return { ok: false, reason: 'disabled' };
    }

    const token = await this.token();
    if (!token) return { ok: false, reason: 'auth' };

    const { url, realm } = this.realmParts;
    try {
      const res = await fetch(`${url}/admin/realms/${realm}/users`, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${token}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          username: user.email,
          email: user.email,
          firstName: user.firstName,
          enabled: true,
          emailVerified: true,
          credentials: [{ type: 'password', value: user.password, temporary: false }],
        }),
        signal: AbortSignal.timeout(15_000),
      });

      if (res.status === 409) {
        // Ya existe alguien con ese correo. No es un error del sistema: es un caso del negocio.
        this.logger.warn('Keycloak rechazó el alta: ya existe un usuario con ese correo (409)');
        return { ok: false, reason: 'exists' };
      }
      if (!res.ok) {
        this.logger.error(`Keycloak rechazó el alta (${res.status})`);
        return { ok: false, reason: 'http' };
      }

      const userId = res.headers.get('location')?.split('/').pop() ?? '';
      if (!userId) {
        // 201 sin `Location` utilizable: el usuario puede existir, pero no sabemos su id y quien
        // llama no podría enlazarlo con nada. Se trata como fallo para no afirmar de más.
        this.logger.error('Keycloak creó el usuario pero no devolvió su id en Location');
        return { ok: false, reason: 'http' };
      }
      return { ok: true, userId };
    } catch (err) {
      this.logger.error(`Alta en Keycloak falló: ${err instanceof Error ? err.message : String(err)}`);
      return { ok: false, reason: 'network' };
    }
  }

  /**
   * Token de la service account por `client_credentials`, **cacheado** hasta poco antes de expirar.
   *
   * Pedir uno por alta también funcionaría —el volumen es ridículo— pero el caché son cuatro líneas
   * y evita dejar escrito en el repo el patrón «autenticarse en cada llamada», que es el que luego
   * alguien copia para algo que sí tiene volumen. Devuelve `null` ante cualquier fallo, dejando
   * traza; el secreto no se registra nunca.
   */
  private async token(): Promise<string | null> {
    if (!this.realmParts) return null;
    if (this.cachedToken && this.cachedToken.expiresAt > Date.now()) {
      return this.cachedToken.value;
    }

    const { url, realm } = this.realmParts;
    try {
      const res = await fetch(`${url}/realms/${realm}/protocol/openid-connect/token`, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'client_credentials',
          client_id: this.clientId,
          client_secret: this.clientSecret,
        }).toString(),
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) {
        this.logger.error(`Keycloak no dio token a ${this.clientId} (${res.status})`);
        return null;
      }
      const payload = (await res.json()) as TokenResponse;
      if (!payload.access_token) {
        this.logger.error('Keycloak devolvió 200 sin access_token');
        return null;
      }
      // Sin `expires_in` no se cachea: mejor pedir otro que arrastrar uno muerto.
      const vidaMs = (payload.expires_in ?? 0) * 1000;
      this.cachedToken =
        vidaMs > TOKEN_MARGEN_MS
          ? { value: payload.access_token, expiresAt: Date.now() + vidaMs - TOKEN_MARGEN_MS }
          : null;
      return payload.access_token;
    } catch (err) {
      this.logger.error(`No se pudo pedir token a Keycloak: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    }
  }
}
