import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { INestApplication } from '@nestjs/common';
import type postgres from 'postgres';

import { TelegramLinkService } from '../src/telegram/telegram-link.service';
import { makeApp, makeSql, resetSchema, seedUser, TEST_DB } from './helpers';

/**
 * Canje del token de vínculo contra BD real: cierra el flujo que `settings.telegram.e2e.spec.ts`
 * simulaba por SQL. Aquí ejercitamos el servicio del bot de verdad y comprobamos que
 * `GET /api/settings/telegram` — lo que consulta el auto-poll de la SPA — refleja el vínculo.
 * La red a Telegram no interviene: `TelegramLinkService` solo habla con Postgres.
 */
describe.skipIf(!TEST_DB)('bot Telegram · canje de /start <token> (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;
  let links: TelegramLinkService;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    app = await makeApp();
    links = app.get(TelegramLinkService);
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  /** Deja un token vivo en el usuario, como haría `POST /settings/telegram/link`. */
  async function giveToken(userId: number, token: string, interval = "interval '15 minutes'") {
    await sql.unsafe(
      `UPDATE app_user
       SET telegram_link_token = $1, telegram_link_token_expires_at = now() + ${interval}
       WHERE id = $2`,
      [token, userId],
    );
  }

  it('token vivo: vincula el chat, limpia el token y la API lo refleja', async () => {
    const user = await seedUser(sql, 'kc-redeem-ok');
    await giveToken(user.id, 'tok-ok');

    await expect(links.redeemStartToken('tok-ok', 111, 'papa_test')).resolves.toBe('linked');

    // postgres.js devuelve BIGINT como string en SQL crudo (Drizzle sí lo convierte a number).
    const [row] = await sql<
      { telegram_chat_id: string | null; telegram_link_token: string | null }[]
    >`SELECT telegram_chat_id, telegram_link_token FROM app_user WHERE id = ${user.id}`;
    expect(row.telegram_chat_id).toBe('111');
    expect(row.telegram_link_token).toBeNull();

    // Lo que ve la SPA al siguiente tick del auto-poll.
    const authed = await makeApp(user);
    try {
      const res = await request(authed.getHttpServer()).get('/api/settings/telegram').expect(200);
      expect(res.body).toMatchObject({ linked: true, pendingLink: false, telegramUsername: 'papa_test' });
    } finally {
      await authed.close();
    }
  });

  it('token de un solo uso: el segundo canje ya no vale', async () => {
    const user = await seedUser(sql, 'kc-redeem-once');
    await giveToken(user.id, 'tok-once');

    await expect(links.redeemStartToken('tok-once', 222)).resolves.toBe('linked');
    await expect(links.redeemStartToken('tok-once', 333)).resolves.toBe('invalid');
  });

  it('token caducado -> expired, sin vincular', async () => {
    const user = await seedUser(sql, 'kc-redeem-expired');
    await giveToken(user.id, 'tok-expired', "interval '-1 minute'");

    await expect(links.redeemStartToken('tok-expired', 444)).resolves.toBe('expired');

    const [row] = await sql<{ telegram_chat_id: number | null }[]>`
      SELECT telegram_chat_id FROM app_user WHERE id = ${user.id}`;
    expect(row.telegram_chat_id).toBeNull();
  });

  it('token inexistente -> invalid', async () => {
    await expect(links.redeemStartToken('no-existe', 555)).resolves.toBe('invalid');
  });

  it('releaseChat suelta el chat de la cuenta anterior (chat_id es UNIQUE)', async () => {
    const first = await seedUser(sql, 'kc-chat-first');
    const second = await seedUser(sql, 'kc-chat-second');
    await giveToken(first.id, 'tok-first');
    await giveToken(second.id, 'tok-second');

    await expect(links.redeemStartToken('tok-first', 666)).resolves.toBe('linked');
    // Mismo chat, otra cuenta: sin soltarlo antes chocaría contra la restricción UNIQUE.
    await expect(links.releaseChat(666)).resolves.toBe(1);
    await expect(links.redeemStartToken('tok-second', 666)).resolves.toBe('linked');

    await expect(links.chatIdForUser(first.id)).resolves.toBeNull();
    await expect(links.chatIdForUser(second.id)).resolves.toBe(666);
  });
});
