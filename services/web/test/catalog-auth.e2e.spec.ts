import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import { makeApp, TEST_DB } from './helpers';

/**
 * El candado del catálogo (#309). Es la **única aserción de seguridad real** de esa issue: que con
 * Keycloak configurado el catálogo no se sirve sin token.
 *
 * No hace falta un Keycloak vivo. Sin cabecera `Authorization`, passport corta antes de mirar el
 * JWKS, y `passportJwtSecret` no hace ninguna petición al construirse — el issuer inventado de
 * aquí nunca llega a resolverse.
 *
 * El `vi.resetModules()` y el guardado del entorno son la misma receta que `config.e2e.spec.ts`:
 * `ConfigModule.forRoot()` valida el entorno al evaluarse `app.module.ts`, y `makeApp()` lo carga
 * con un `import()` dinámico que el registro de módulos cachea. Sin el reset, este fichero
 * heredaría la config congelada por otro.
 *
 * El caso simétrico —**sin** `KEYCLOAK_*` el catálogo sigue abierto— no necesita spec propio: es
 * exactamente el entorno en el que corre todo `catalog.e2e.spec.ts`, y esos siguen en verde.
 */
describe.skipIf(!TEST_DB)('catálogo con sesión · candado de #309 (e2e)', () => {
  let app: INestApplication;
  const saved = {
    issuer: process.env.KEYCLOAK_ISSUER_URL,
    audience: process.env.KEYCLOAK_AUDIENCE,
  };

  beforeAll(async () => {
    process.env.KEYCLOAK_ISSUER_URL = 'https://keycloak.invalido/realms/deal-tracker';
    process.env.KEYCLOAK_AUDIENCE = 'deal-tracker-web';
    vi.resetModules();
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    if (saved.issuer === undefined) delete process.env.KEYCLOAK_ISSUER_URL;
    else process.env.KEYCLOAK_ISSUER_URL = saved.issuer;
    if (saved.audience === undefined) delete process.env.KEYCLOAK_AUDIENCE;
    else process.env.KEYCLOAK_AUDIENCE = saved.audience;
    vi.resetModules();
  });

  // Los cuatro del `CatalogController`. Se listan uno a uno y no en bucle sobre el controlador
  // para que añadir un endpoint nuevo sin guard no pase inadvertido: aquí habría que sumarlo.
  const endpoints = [
    '/api/catalog/products',
    '/api/catalog/products/1',
    '/api/catalog/variants/1/price-history',
    '/api/catalog/facets',
  ];

  it.each(endpoints)('%s responde 401 sin token', async (path) => {
    const res = await request(app.getHttpServer()).get(path);
    expect(res.status).toBe(401);
  });

  it.each(endpoints)('%s responde 401 con un token basura, no 500', async (path) => {
    const res = await request(app.getHttpServer()).get(path).set('Authorization', 'Bearer basura');
    expect(res.status).toBe(401);
  });

  it('deja públicos los dos que la SPA necesita antes de poder autenticarse', async () => {
    // Sin estos dos no hay forma de ofrecer login: la página de acceso quedaría muerta.
    await request(app.getHttpServer()).get('/api/config').expect(200);
    await request(app.getHttpServer()).get('/api/health').expect(200);
  });
});
