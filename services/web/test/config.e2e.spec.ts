import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { makeApp, TEST_DB } from './helpers';

/**
 * `GET /api/config` es el endpoint que la SPA lee al arrancar para saber si hay login. Se ejercita
 * **sin `Authorization`** a propósito: tiene que ser público, porque el navegador lo necesita antes
 * de poder autenticarse.
 */
describe.skipIf(!TEST_DB)('config pública (e2e)', () => {
  describe('sin Keycloak configurado', () => {
    let app: INestApplication;
    const saved = { issuer: process.env.KEYCLOAK_ISSUER_URL, clientId: process.env.KEYCLOAK_CLIENT_ID };

    beforeAll(async () => {
      delete process.env.KEYCLOAK_ISSUER_URL;
      delete process.env.KEYCLOAK_CLIENT_ID;
      app = await makeApp();
    });

    afterAll(async () => {
      await app.close();
      if (saved.issuer !== undefined) process.env.KEYCLOAK_ISSUER_URL = saved.issuer;
      if (saved.clientId !== undefined) process.env.KEYCLOAK_CLIENT_ID = saved.clientId;
    });

    it('responde 200 con los tres campos a null (auth deshabilitada)', async () => {
      const res = await request(app.getHttpServer()).get('/api/config').expect(200);
      expect(res.body).toEqual({ url: null, realm: null, clientId: null });
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

    it('deriva url/realm del issuer y el client-id de la audiencia', async () => {
      const res = await request(app.getHttpServer()).get('/api/config').expect(200);
      expect(res.body).toEqual({
        url: 'https://kc.example',
        realm: 'deal-tracker-qa',
        clientId: 'deal-tracker-web',
      });
    });
  });
});
