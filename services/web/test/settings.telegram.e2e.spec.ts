import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { makeApp, makeSql, resetSchema, seedUser, TEST_DB } from './helpers';

/**
 * Flujo de vínculo de Telegram sobre BD real (guard + rutas + persistencia). El endpoint
 * `POST /link` depende de `TELEGRAM_BOT_USERNAME`, que el ConfigModule hornea una vez por
 * proceso; su lógica (deep-link / 503) se cubre en el unit test `settings.service.spec.ts`.
 * Aquí simulamos por SQL lo que hará el bot (fijar chat, limpiar token).
 */
describe.skipIf(!TEST_DB)('ajustes · vínculo Telegram (e2e)', () => {
  let sql: postgres.Sql;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  it('sin token -> 401', async () => {
    const app = await makeApp(); // sin override: guard real de Keycloak
    try {
      await request(app.getHttpServer()).get('/api/settings/telegram').expect(401);
    } finally {
      await app.close();
    }
  });

  it('estado: sin vincular -> enlace en curso -> vinculado -> desvincular', async () => {
    const user = await seedUser(sql, 'kc-tg-flow');
    const app = await makeApp(user);
    try {
      // sin vincular
      const initial = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(initial.body).toMatchObject({ linked: false, pendingLink: false, telegramUsername: null });

      // enlace en curso: token vivo sin chat (lo que deja POST /link)
      await sql`
        UPDATE app_user
        SET telegram_link_token = 'tok-vivo',
            telegram_link_token_expires_at = now() + interval '15 minutes'
        WHERE id = ${user.id}`;
      const pending = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(pending.body).toMatchObject({ linked: false, pendingLink: true });

      // token caducado -> ya no cuenta como pendiente
      await sql`
        UPDATE app_user SET telegram_link_token_expires_at = now() - interval '1 minute'
        WHERE id = ${user.id}`;
      const expired = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(expired.body).toMatchObject({ linked: false, pendingLink: false });

      // el bot confirma: fija chat y limpia token
      await sql`
        UPDATE app_user
        SET telegram_chat_id = 12345, telegram_username = 'papa_test',
            telegram_linked_at = now(), telegram_link_token = NULL,
            telegram_link_token_expires_at = NULL
        WHERE id = ${user.id}`;
      const confirmed = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(confirmed.body).toMatchObject({
        linked: true,
        pendingLink: false,
        telegramUsername: 'papa_test',
      });
      expect(typeof confirmed.body.linkedAt).toBe('string');

      // desvincular -> 204 y vuelta a no vinculado
      await request(app.getHttpServer()).delete('/api/settings/telegram').expect(204);
      const after = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(after.body).toMatchObject({ linked: false, telegramUsername: null });
      const [dbRow] = await sql<{ telegram_chat_id: number | null }[]>`
        SELECT telegram_chat_id FROM app_user WHERE id = ${user.id}`;
      expect(dbRow.telegram_chat_id).toBeNull();
    } finally {
      await app.close();
    }
  });

  it('no filtra el vínculo de otro usuario', async () => {
    const owner = await seedUser(sql, 'kc-tg-owner');
    await sql`UPDATE app_user SET telegram_chat_id = 999, telegram_username = 'owner'
              WHERE id = ${owner.id}`;
    const other = await seedUser(sql, 'kc-tg-other');
    const app = await makeApp(other);
    try {
      const status = await request(app.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(status.body).toMatchObject({ linked: false, telegramUsername: null });
    } finally {
      await app.close();
    }
  });
});
