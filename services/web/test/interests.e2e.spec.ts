import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { makeApp, makeSql, resetSchema, seedCatalog, seedUser, TEST_DB } from './helpers';

describe.skipIf(!TEST_DB)('intereses (e2e)', () => {
  let sql: postgres.Sql;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    await seedCatalog(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  it('sin token -> 401', async () => {
    const app = await makeApp(); // sin override: guard real de Keycloak
    try {
      await request(app.getHttpServer()).get('/api/interests').expect(401);
    } finally {
      await app.close();
    }
  });

  it('CRUD del interés propio (crear, listar, borrar)', async () => {
    const user = await seedUser(sql);
    const app = await makeApp(user);
    try {
      // crear
      const created = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ gender: 'niña', section: 'zapateria', minDiscountPct: 30, compareBase: 'list_price' })
        .expect(201);
      expect(created.body.gender).toBe('niña');
      expect(created.body.minDiscountPct).toBe('30.00');
      expect(created.body.compareBase).toBe('list_price');
      const id = created.body.id;

      // listar
      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(1);
      expect(listed.body[0].id).toBe(id);

      // borrar
      await request(app.getHttpServer()).delete(`/api/interests/${id}`).expect(204);
      await request(app.getHttpServer())
        .get('/api/interests')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(0));
    } finally {
      await app.close();
    }
  });

  it('rechaza un interés vacío (sin objetivo ni filtro) con 400', async () => {
    const user = await seedUser(sql, 'kc-sub-empty');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer()).post('/api/interests').send({}).expect(400);
    } finally {
      await app.close();
    }
  });

  it('no permite borrar el interés de otro usuario (404)', async () => {
    const owner = await seedUser(sql, 'kc-owner');
    const ownerApp = await makeApp(owner);
    let interestId: number;
    try {
      const created = await request(ownerApp.getHttpServer())
        .post('/api/interests')
        .send({ category: 'pantalones' })
        .expect(201);
      interestId = created.body.id;
    } finally {
      await ownerApp.close();
    }

    const other = await seedUser(sql, 'kc-other');
    const otherApp = await makeApp(other);
    try {
      await request(otherApp.getHttpServer()).delete(`/api/interests/${interestId}`).expect(404);
    } finally {
      await otherApp.close();
    }
  });
});
