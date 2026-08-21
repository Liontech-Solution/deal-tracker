import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import { makeApp, TEST_DB } from './helpers';

/**
 * `GET /api/config` es el endpoint que la SPA lee al arrancar para saber si hay login. Se ejercita
 * **sin `Authorization`** a propósito: tiene que ser público, porque el navegador lo necesita antes
 * de poder autenticarse.
 *
 * Cada escenario hace `vi.resetModules()` antes de levantar la app: `ConfigModule.forRoot()` valida
 * el entorno **al evaluarse `app.module.ts`**, y `makeApp()` lo carga con un `import()` dinámico que
 * el registro de módulos cachea. Sin el reset, el segundo escenario reutilizaría la config
 * congelada por el primero.
 */
describe.skipIf(!TEST_DB)('config pública (e2e)', () => {
  describe('sin Keycloak configurado', () => {
    let app: INestApplication;
    const saved = { issuer: process.env.KEYCLOAK_ISSUER_URL, clientId: process.env.KEYCLOAK_CLIENT_ID };

    beforeAll(async () => {
      delete process.env.KEYCLOAK_ISSUER_URL;
      delete process.env.KEYCLOAK_CLIENT_ID;
      vi.resetModules();
      app = await makeApp();
    });

    afterAll(async () => {
      await app.close();
      if (saved.issuer !== undefined) process.env.KEYCLOAK_ISSUER_URL = saved.issuer;
      if (saved.clientId !== undefined) process.env.KEYCLOAK_CLIENT_ID = saved.clientId;
    });

    it('responde 200 con los tres campos a null y sin registro (auth deshabilitada)', async () => {
      const res = await request(app.getHttpServer()).get('/api/config').expect(200);
      expect(res.body).toEqual({ url: null, realm: null, clientId: null, invitesEnabled: false });
    });
  });

  describe('con Keycloak configurado', () => {
    let app: INestApplication;
    const saved = {
      issuer: process.env.KEYCLOAK_ISSUER_URL,
      audience: process.env.KEYCLOAK_AUDIENCE,
      clientId: process.env.KEYCLOAK_CLIENT_ID,
    };

    beforeAll(async () => {
      process.env.KEYCLOAK_ISSUER_URL = 'https://kc.example/realms/deal-tracker-qa';
      // Sin KEYCLOAK_CLIENT_ID: se comprueba el fallback a la audiencia.
      delete process.env.KEYCLOAK_CLIENT_ID;
      process.env.KEYCLOAK_AUDIENCE = 'deal-tracker-web';
      vi.resetModules();
      app = await makeApp();
    });

    afterAll(async () => {
      await app.close();
      for (const [key, value] of [
        ['KEYCLOAK_ISSUER_URL', saved.issuer],
        ['KEYCLOAK_AUDIENCE', saved.audience],
        ['KEYCLOAK_CLIENT_ID', saved.clientId],
      ] as const) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    });

    it('deriva url/realm del issuer y el client-id de la audiencia, con el registro apagado', async () => {
      const res = await request(app.getHttpServer()).get('/api/config').expect(200);
      expect(res.body).toEqual({
        url: 'https://kc.example',
        realm: 'deal-tracker-qa',
        clientId: 'deal-tracker-web',
        // Hay auth pero faltan los secretos del alta: es exactamente el estado de qa/prod hasta
        // que se sellan, y el motivo de que `isInvitesConfigured()` exista aparte.
        invitesEnabled: false,
      });
    });
  });

  /**
   * La mitad de la tabla de #548 que sí se puede probar aquí. La otra —que el alta cree de verdad
   * el usuario en el realm— **solo se observa en QA y en prod**: `dev` no trae ninguna `KEYCLOAK_*`
   * por construcción, así que un `dev` en verde no prueba nada del registro (#309).
   */
  describe('con Keycloak y los secretos del alta', () => {
    let app: INestApplication;
    const saved = {
      issuer: process.env.KEYCLOAK_ISSUER_URL,
      audience: process.env.KEYCLOAK_AUDIENCE,
      clientId: process.env.KEYCLOAK_CLIENT_ID,
      adminSecret: process.env.KEYCLOAK_ADMIN_CLIENT_SECRET,
      resend: process.env.RESEND_API_KEY,
    };

    beforeAll(async () => {
      process.env.KEYCLOAK_ISSUER_URL = 'https://kc.example/realms/deal-tracker-qa';
      process.env.KEYCLOAK_AUDIENCE = 'deal-tracker-web';
      delete process.env.KEYCLOAK_CLIENT_ID;
      process.env.KEYCLOAK_ADMIN_CLIENT_SECRET = 'secreto-de-pega';
      process.env.RESEND_API_KEY = 're_de_pega';
      vi.resetModules();
      app = await makeApp();
    });

    afterAll(async () => {
      await app.close();
      for (const [key, value] of [
        ['KEYCLOAK_ISSUER_URL', saved.issuer],
        ['KEYCLOAK_AUDIENCE', saved.audience],
        ['KEYCLOAK_CLIENT_ID', saved.clientId],
        ['KEYCLOAK_ADMIN_CLIENT_SECRET', saved.adminSecret],
        ['RESEND_API_KEY', saved.resend],
      ] as const) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    });

    it('publica invitesEnabled para que la SPA pueda ofrecer el alta', async () => {
      const res = await request(app.getHttpServer()).get('/api/config').expect(200);
      expect(res.body.invitesEnabled).toBe(true);
    });
  });
});
