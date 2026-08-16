import { drizzle } from 'drizzle-orm/postgres-js';
import request from 'supertest';
import { afterAll, beforeEach, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { schema } from '../src/database/schema';
import { MatchingService } from '../src/matching/matching.service';
import { TelegramApiClient } from '../src/telegram/telegram-api.client';
import {
  makeApp,
  makeSql,
  refrescarAgregado,
  resetSchema,
  seedCatalog,
  seedUser,
  TEST_DB,
} from './helpers';
import type { SeedIds } from './helpers';

/**
 * Favoritos (#435): guardar una prenda SIN pedir aviso.
 *
 * El bloque que de verdad importa aquí es el último. Todo lo demás es CRUD; lo que esta issue tenía
 * que demostrar —y lo que no se podía dar por supuesto— es que una fila de `favorite` **no puede
 * generar un aviso de Telegram**, ejecutando el job de matching de verdad y no razonando sobre él.
 */
describe.skipIf(!TEST_DB)('favoritos (e2e)', () => {
  let sql: postgres.Sql;
  let ids: SeedIds;

  beforeAll(() => {
    sql = makeSql();
  });

  beforeEach(async () => {
    await resetSchema(sql);
    const crudos = await seedCatalog(sql);
    await refrescarAgregado(sql);
    // `SeedIds` los declara `number`, pero postgres.js devuelve los BIGINT del SQL crudo como
    // STRING: el resto de specs lo sortea con `Number()` en el sitio (ver matching.e2e.spec.ts).
    // Aquí se normaliza una vez, porque este spec los compara contra la respuesta de la API, que
    // sí son números de verdad (Drizzle los mapea con `mode: 'number'`).
    ids = {
      retailerId: Number(crudos.retailerId),
      productId: Number(crudos.productId),
      variantId: Number(crudos.variantId),
    };
  });

  afterAll(async () => {
    await sql.end();
  });

  it('sin token -> 401', async () => {
    const app = await makeApp(); // sin override: guard real de Keycloak
    try {
      await request(app.getHttpServer()).get('/api/favorites').expect(401);
    } finally {
      await app.close();
    }
  });

  it('CRUD del favorito propio (marcar, listar enriquecido, quitar)', async () => {
    const user = await seedUser(sql);
    const app = await makeApp(user);
    try {
      const created = await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
      expect(created.body.productId).toBe(ids.productId);

      const listed = await request(app.getHttpServer()).get('/api/favorites').expect(200);
      expect(listed.body).toHaveLength(1);
      const fila = listed.body[0];
      expect(fila.productId).toBe(ids.productId);
      // Enriquecido con la PRENDA, que es lo que la página tiene que pintar.
      expect(fila.productName).toBeTruthy();
      expect(fila.retailerName).toBe('Zara');
      expect(fila.priceFrom).toBeTruthy();
      expect(fila.delisted).toBe(false);
      expect(fila.seguido).toBe(false);

      // El DELETE va por productId, no por el id de la fila.
      await request(app.getHttpServer())
        .delete(`/api/favorites/${ids.productId}`)
        .expect(204);
      await request(app.getHttpServer())
        .get('/api/favorites')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(0));
    } finally {
      await app.close();
    }
  });

  it('marcar dos veces el mismo producto no falla ni duplica', async () => {
    const user = await seedUser(sql, 'kc-fav-idem');
    const app = await makeApp(user);
    try {
      const uno = await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
      const dos = await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);

      // La MISMA fila, no una nueva: el `ON CONFLICT DO NOTHING` no devuelve nada y el servicio
      // recupera la que ya estaba.
      expect(dos.body.id).toBe(uno.body.id);
      expect(dos.body.createdAt).toBe(uno.body.createdAt);

      const [{ n }] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM favorite`;
      expect(Number(n)).toBe(1);
    } finally {
      await app.close();
    }
  });

  it('quitar algo que no está en favoritos -> 404', async () => {
    const user = await seedUser(sql, 'kc-fav-404');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer()).delete(`/api/favorites/${ids.productId}`).expect(404);
    } finally {
      await app.close();
    }
  });

  it('el favorito de otro usuario no se ve ni se puede quitar', async () => {
    const ajeno = await seedUser(sql, 'kc-fav-ajeno');
    await sql`INSERT INTO favorite (user_id, product_id) VALUES (${ajeno.id}, ${ids.productId})`;

    const user = await seedUser(sql, 'kc-fav-propio');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .get('/api/favorites')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(0));
      await request(app.getHttpServer()).delete(`/api/favorites/${ids.productId}`).expect(404);
    } finally {
      await app.close();
    }
  });

  /**
   * La baja NO es un estado terminal: `delisted_at` significa «lleva N pasadas sin aparecer» y se
   * deshace sola cuando el producto vuelve (`ingest.py`, `ON CONFLICT ... delisted_at = NULL`). Por
   * eso el favorito no se borra nunca solo, y por eso el GET no filtra: la fila tiene que salir
   * para poder pintarse apagada, y volver viva sin que el usuario haga nada.
   */
  it('una prenda de baja sale marcada, no desaparece, y revive sola si el producto vuelve', async () => {
    const user = await seedUser(sql, 'kc-fav-baja');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);

      await sql`UPDATE product SET delisted_at = now() WHERE id = ${ids.productId}`;
      const deBaja = await request(app.getHttpServer()).get('/api/favorites').expect(200);
      expect(deBaja.body).toHaveLength(1);
      expect(deBaja.body[0].delisted).toBe(true);
      // Sigue enriquecida: la fila apagada tiene que poder enseñar de qué prenda habla.
      expect(deBaja.body[0].productName).toBeTruthy();

      // Resurrección, tal cual la hace la ingesta.
      await sql`UPDATE product SET delisted_at = NULL WHERE id = ${ids.productId}`;
      const revivida = await request(app.getHttpServer()).get('/api/favorites').expect(200);
      expect(revivida.body).toHaveLength(1);
      expect(revivida.body[0].delisted).toBe(false);
    } finally {
      await app.close();
    }
  });

  it('`seguido` distingue el favorito que además tiene seguimiento activo', async () => {
    const user = await seedUser(sql, 'kc-fav-seguido');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
      await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId })
        .expect(201);

      const conSeguimiento = await request(app.getHttpServer()).get('/api/favorites').expect(200);
      expect(conSeguimiento.body[0].seguido).toBe(true);

      // Y la lista sigue teniendo UNA fila: la subconsulta no puede multiplicar el favorito.
      expect(conSeguimiento.body).toHaveLength(1);
    } finally {
      await app.close();
    }
  });

  it('favorito y seguimiento de la misma prenda conviven, y quitar uno no toca el otro', async () => {
    const user = await seedUser(sql, 'kc-fav-convive');
    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
      const interes = await request(app.getHttpServer())
        .post('/api/interests')
        .send({ productId: ids.productId })
        .expect(201);

      // Quitar el corazón deja el seguimiento en pie.
      await request(app.getHttpServer()).delete(`/api/favorites/${ids.productId}`).expect(204);
      await request(app.getHttpServer())
        .get('/api/interests')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(1));

      // Y al revés: dejar de seguir no toca el favorito.
      await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
      await request(app.getHttpServer()).delete(`/api/interests/${interes.body.id}`).expect(204);
      await request(app.getHttpServer())
        .get('/api/favorites')
        .expect(200)
        .expect((r) => expect(r.body).toHaveLength(1));
    } finally {
      await app.close();
    }
  });

  /**
   * **El criterio de aceptación que no se podía dar por supuesto.**
   *
   * Se siembra exactamente el caso que SÍ produce aviso —`seedCatalog` deja la variante a 19,99 €
   * desde 39,99 €, con dos puntos de histórico— y un usuario con Telegram vinculado, o sea todo lo
   * que el job necesita salvo el `interest`. Con solo un favorito, el job tiene que quedarse mudo.
   *
   * Se ejecuta `MatchingService.run(false)` de verdad (envío real al doble de Telegram, no
   * dry-run), porque lo que se está comprobando es justo lo que un razonamiento sobre el código
   * daría por bueno sin mirar.
   */
  it('un favorito NO genera aviso: el job de matching corre y se queda mudo', async () => {
    const user = await seedUser(sql, 'kc-fav-sin-aviso');
    await sql`UPDATE app_user SET telegram_chat_id = 4242 WHERE id = ${user.id}`;

    // La pasada que trae la bajada, igual que en matching.e2e.spec.ts.
    const [run] = await sql<{ id: number }[]>`
      INSERT INTO scrape_run (retailer_id, status, finished_at)
      VALUES (${ids.retailerId}, 'success', now()) RETURNING id`;
    await sql`
      UPDATE price_history SET scrape_run_id = ${Number(run.id)}
      WHERE variant_id = ${ids.variantId} AND price = 19.99`;

    const app = await makeApp(user);
    try {
      await request(app.getHttpServer())
        .post('/api/favorites')
        .send({ productId: ids.productId })
        .expect(201);
    } finally {
      await app.close();
    }

    // Marcar el corazón no ha escrito NADA en la tabla de la que depende el aviso.
    const [{ n: intereses }] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM interest`;
    expect(Number(intereses)).toBe(0);

    const enviados: Array<{ chatId: number; text: string }> = [];
    const telegram = {
      enabled: true,
      sendMessage: (chatId: number, text: string) => {
        enviados.push({ chatId, text });
        return Promise.resolve(true);
      },
    } as unknown as TelegramApiClient;
    const service = new MatchingService(drizzle(sql, { schema }) as never, telegram);
    service.chunkDelayMs = 0;

    const summary = await service.run(false);

    expect(enviados).toEqual([]);
    expect(summary.notified).toBe(0);
    expect(summary.usersNotified).toBe(0);
    const [{ n: avisos }] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM notification`;
    expect(Number(avisos)).toBe(0);
  });
});
