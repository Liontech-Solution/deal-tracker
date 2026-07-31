import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { makeApp, makeSql, resetSchema, seedCatalog, seedUser, TEST_DB } from './helpers';
import type { SeedIds } from './helpers';

describe.skipIf(!TEST_DB)('intereses (e2e)', () => {
  let sql: postgres.Sql;
  let ids: SeedIds;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    ids = await seedCatalog(sql);
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

  it('enriquece la lista con nombre de producto/variante/tienda al apuntar a un objetivo', async () => {
    const user = await seedUser(sql, 'kc-sub-enrich');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId, variantId: ids.variantId })
        .expect(201);

      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      expect(listed.body).toHaveLength(1);
      const view = listed.body[0];
      expect(view.productName).toBe('Botas niña');
      expect(view.retailerName).toBe('Zara');
      expect(view.variantLabel).toBe('Talla 24 · rojo');
    } finally {
      await app.close();
    }
  });

  it('un interés por filtros (sin objetivo) trae los nombres a null', async () => {
    const user = await seedUser(sql, 'kc-sub-filter');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ gender: 'niña', section: 'zapateria' })
        .expect(201);
      const listed = await request(app.getHttpServer()).get('/api/interests').expect(200);
      const view = listed.body[0];
      expect(view.productName).toBeNull();
      expect(view.variantLabel).toBeNull();
      expect(view.retailerName).toBeNull();
    } finally {
      await app.close();
    }
  });

  it('guarda la talla en canónico, venga como venga (#43)', async () => {
    // El chip del filtro ya manda la canónica, pero un alta por API con el texto crudo de la tienda
    // tiene que seguir a la misma prenda: si se guardara '26 (16,3 cm)', ese interés solo casaría
    // con Zara y nunca con el mismo pie en otra tienda.
    const user = await seedUser(sql, 'kc-talla-canon');
    const app = await makeApp(user);
    try {
      for (const [entrada, esperada] of [
        ['26 (16,3 cm)', '26'],
        ['11-12', '11-12 años'],
        ['26', '26'],
      ]) {
        const created = await request(app.getHttpServer())
          .post('/api/interests')
          .send({ size: entrada })
          .expect(201);
        expect(created.body.size, `entrada «${entrada}»`).toBe(esperada);
      }
    } finally {
      await app.close();
    }
  });

  it('guarda el color en canónico, venga como venga (#49)', async () => {
    // Mismo razonamiento que la talla: un interés guardado con 'VERDE' solo casaría con la tienda
    // que lo escribe así, y el aviso no llegaría para la misma prenda de la de al lado.
    const user = await seedUser(sql, 'kc-color-canon');
    const app = await makeApp(user);
    try {
      for (const [entrada, esperada] of [
        ['VERDE', 'verde'],
        ['Azul Marino', 'azul marino'],
        ['  Gris   Topo ', 'gris topo'],
        ['verde', 'verde'],
      ]) {
        const created = await request(app.getHttpServer())
          .post('/api/interests')
          .send({ color: entrada })
          .expect(201);
        expect(created.body.color, `entrada «${entrada}»`).toBe(esperada);
      }
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
