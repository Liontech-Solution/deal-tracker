import { drizzle } from 'drizzle-orm/postgres-js';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { schema } from '../src/database/schema';
import { MatchingService } from '../src/matching/matching.service';
import { TelegramApiClient } from '../src/telegram/telegram-api.client';
import { makeSql, resetSchema, seedCatalog, seedUser, TEST_DB } from './helpers';
import type { SeedIds } from './helpers';

/**
 * Job de matching contra BD real. El cliente de Telegram es un doble (nada de red), pero el SQL
 * —que es la parte con miga: LATERAL, filtros parciales, dedupe— se ejercita de verdad.
 *
 * `seedCatalog` deja la variante a 19,99 € desde 39,99 € (dos puntos de histórico), que es
 * exactamente el caso de aviso.
 */
describe.skipIf(!TEST_DB)('job de matching (e2e)', () => {
  let sql: postgres.Sql;
  let seeded: SeedIds;
  let runId: number;

  /** Doble del cliente: captura los mensajes en vez de mandarlos. */
  function fakeTelegram(delivered = true) {
    const sent: Array<{ chatId: number; text: string }> = [];
    const client = {
      enabled: true,
      sendMessage: (chatId: number, text: string) => {
        sent.push({ chatId, text });
        return Promise.resolve(delivered);
      },
    } as unknown as TelegramApiClient;
    return { client, sent };
  }

  function makeService(telegram: TelegramApiClient): MatchingService {
    // Drizzle sobre el mismo cliente de test; no hace falta levantar Nest para un servicio con
    // dos dependencias.
    return new MatchingService(drizzle(sql, { schema }) as never, telegram);
  }

  /** Usuario con Telegram ya vinculado (el job ignora a quien no lo tiene). */
  async function seedLinkedUser(sub: string, chatId: number) {
    const user = await seedUser(sql, sub);
    await sql`UPDATE app_user SET telegram_chat_id = ${chatId} WHERE id = ${user.id}`;
    return user;
  }

  async function seedInterest(userId: number, extra: Record<string, unknown> = {}) {
    const [row] = await sql<{ id: number }[]>`
      INSERT INTO interest ${sql({
        user_id: userId,
        product_id: seeded.productId,
        min_discount_pct: 20,
        compare_base: 'recent_min',
        window_days: 30,
        ...extra,
      })}
      RETURNING id`;
    return row.id;
  }

  async function countNotifications(): Promise<number> {
    const [row] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM notification`;
    return Number(row.n);
  }

  beforeAll(() => {
    sql = makeSql();
  });

  beforeEach(async () => {
    await resetSchema(sql);
    seeded = await seedCatalog(sql);
    // seedCatalog no crea scrape_run; el job filtra por scrape_run_id, así que lo asociamos.
    const [run] = await sql<{ id: number }[]>`
      INSERT INTO scrape_run (retailer_id, status, finished_at)
      VALUES (${seeded.retailerId}, 'success', now()) RETURNING id`;
    runId = Number(run.id); // postgres.js devuelve BIGINT como string en SQL crudo
    // Solo el precio MÁS RECIENTE es "nuevo": el de hace 2 días es histórico previo.
    await sql`
      UPDATE price_history SET scrape_run_id = ${runId}
      WHERE variant_id = ${seeded.variantId} AND price = 19.99`;
  });

  afterAll(async () => {
    await sql.end();
  });

  it('bajada real: avisa una vez y deja rastro en notification', async () => {
    const user = await seedLinkedUser('kc-match-ok', 900);
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.deals).toBe(1);
    expect(summary.notified).toBe(1);
    expect(summary.usersNotified).toBe(1);
    expect(summary.failedSends).toBe(0);

    expect(sent).toHaveLength(1);
    expect(sent[0].chatId).toBe(900);
    expect(sent[0].text).toContain('Botas niña');
    expect(sent[0].text).toContain('19,99 €');

    const [n] = await sql<{ price: string; discount_pct: string; price_event_key: string }[]>`
      SELECT price, discount_pct, price_event_key FROM notification`;
    expect(Number(n.price)).toBe(19.99);
    expect(Number(n.discount_pct)).toBe(50.01);
    expect(n.price_event_key).toBe(`${runId}:19.99`);
  });

  it('idempotencia: una segunda pasada no repite el aviso', async () => {
    const user = await seedLinkedUser('kc-match-idem', 901);
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();
    const service = makeService(client);

    await service.run(false);
    const second = await service.run(false);

    // La marca de agua ya dejó fuera el lote; ni siquiera hay candidatos.
    expect(second.candidates).toBe(0);
    expect(second.notified).toBe(0);
    expect(sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);
  });

  it('aunque se rebobine la marca de agua, el UNIQUE evita el aviso duplicado', async () => {
    const user = await seedLinkedUser('kc-match-rewind', 902);
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();
    const service = makeService(client);

    await service.run(false);
    await sql`UPDATE job_state SET last_scrape_run_id = 0 WHERE job = 'matching'`;
    const second = await service.run(false);

    expect(second.candidates).toBe(1); // vuelve a evaluarlo...
    expect(second.notified).toBe(0); // ...pero no vuelve a avisar
    expect(sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);
  });

  it('usuario sin Telegram vinculado: ni envío ni fila (recibirá la próxima bajada)', async () => {
    const user = await seedUser(sql, 'kc-match-sin-telegram');
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.candidates).toBe(0);
    expect(sent).toHaveLength(0);
    expect(await countNotifications()).toBe(0);
  });

  it('dry-run: informa pero no toca nada', async () => {
    const user = await seedLinkedUser('kc-match-dry', 903);
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(true);

    expect(summary.dryRun).toBe(true);
    expect(summary.deals).toBe(1);
    expect(sent).toHaveLength(0);
    expect(await countNotifications()).toBe(0);
    const state = await sql`SELECT * FROM job_state WHERE job = 'matching'`;
    expect(state).toHaveLength(0);
  });

  it('varios seguimientos del mismo usuario: un solo mensaje, una fila por oferta', async () => {
    const user = await seedLinkedUser('kc-match-digest', 904);
    await seedInterest(user.id); // por producto
    await seedInterest(user.id, { product_id: null, variant_id: seeded.variantId }); // por variante
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.deals).toBe(2);
    expect(summary.usersNotified).toBe(1);
    expect(sent).toHaveLength(1);
    expect(sent[0].text).toContain('2 prendas');
    expect(await countNotifications()).toBe(2);
  });

  it('umbral por encima del descuento real: silencio', async () => {
    const user = await seedLinkedUser('kc-match-umbral', 905);
    await seedInterest(user.id, { min_discount_pct: 70 });
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.candidates).toBe(1);
    expect(summary.deals).toBe(0);
    expect(sent).toHaveLength(0);
  });

  it('interés inactivo: no se evalúa', async () => {
    const user = await seedLinkedUser('kc-match-inactivo', 906);
    await seedInterest(user.id, { active: false });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  it('filtro por talla que no casa: no se evalúa', async () => {
    const user = await seedLinkedUser('kc-match-talla', 907);
    await seedInterest(user.id, { size: '99' }); // la variante sembrada es talla 24
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  /**
   * El fallo silencioso de #43: la talla se guardaba tal como la escribe cada tienda, y el JOIN
   * casaba por igualdad de texto. Un interés con '24' —la talla que ofrece el filtro mirando Sfera—
   * no avisaba de la misma prenda en Zara, guardada como '24 (14,9 cm)'. Y no fallaba ruidosamente:
   * el aviso simplemente no llegaba.
   */
  it('avisa aunque la tienda escriba la talla con el cm', async () => {
    const user = await seedLinkedUser('kc-match-talla-canon', 908);
    await sql`
      UPDATE variant SET size = '24 (14,9 cm)' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { size: '24' });
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.notified).toBe(1);
    // Y el mensaje enseña la talla de la tienda, no la canónica: es lo que el usuario verá al abrir
    // el enlace.
    expect(sent[0].text).toContain('24 (14,9 cm)');
  });

  it('no confunde tallas distintas al normalizar', async () => {
    // La red de seguridad del test anterior: si `size_canon` fundiera de más, esto pasaría a avisar.
    const user = await seedLinkedUser('kc-match-talla-distinta', 911);
    await sql`UPDATE variant SET size = '34 (21,6 cm)' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { size: '24' });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  it('avisa aunque la tienda escriba el color en mayúsculas', async () => {
    // El fallo de #49: el interés se guarda con el color del chip ('verde') y la tienda escribe
    // 'VERDE'. Con igualdad de texto crudo el aviso no llegaba, y no fallaba nada ruidosamente.
    const user = await seedLinkedUser('kc-match-color-canon', 912);
    await sql`UPDATE variant SET color = 'VERDE' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { color: 'verde' });
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.notified).toBe(1);
    // Y el mensaje enseña el color de la tienda, no el canónico, por lo mismo que la talla.
    expect(sent[0].text).toContain('VERDE');
  });

  it('no confunde colores distintos al normalizar', async () => {
    // La red de seguridad del anterior: si `color_canon` fundiera familias, esto pasaría a avisar.
    const user = await seedLinkedUser('kc-match-color-distinto', 913);
    await sql`UPDATE variant SET color = 'Verde pato' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { color: 'verde' });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  it('calzado no respetuoso: no se avisa aunque el interés y la bajada casen', async () => {
    // Foco barefoot (#30). El aviso es más intrusivo que una tarjeta del catálogo —llega solo al
    // móvil de alguien—, así que mandar ahí lo que el catálogo esconde sería el mismo error a lo
    // grande. El producto sembrado es zapatería, así que basta con degradar su marca.
    const user = await seedLinkedUser('kc-match-barefoot', 909);
    await seedInterest(user.id);
    const { client, sent } = fakeTelegram();

    for (const marca of ['no', 'desconocido', null]) {
      await sql`UPDATE product SET barefoot = ${marca} WHERE id = ${seeded.productId}`;
      const summary = await makeService(client).run(false);
      expect(summary.candidates, `barefoot=${marca}`).toBe(0);
    }
    expect(sent).toHaveLength(0);
    expect(await countNotifications()).toBe(0);

    // ...y con la marca puesta, el mismo caso sí avisa: el filtro es lo único que cambiaba.
    await sql`UPDATE product SET barefoot = 'si' WHERE id = ${seeded.productId}`;
    expect((await makeService(client).run(false)).deals).toBe(1);
  });

  it('la ropa nunca la filtra el foco barefoot', async () => {
    // En ropa la marca es NULL porque la pregunta no aplica; si el filtro la tratara como "sin
    // clasificar", los avisos de ropa —la mitad del catálogo— desaparecerían en silencio.
    const user = await seedLinkedUser('kc-match-ropa', 910);
    await seedInterest(user.id);
    await sql`
      UPDATE product SET section = 'ropa', category = 'camisetas', barefoot = NULL
      WHERE id = ${seeded.productId}`;
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).deals).toBe(1);
  });

  it('arranque en frío: producto nuevo ya rebajado no avisa', async () => {
    const user = await seedLinkedUser('kc-match-frio', 908);
    await seedInterest(user.id);
    // Se borra el histórico previo: queda solo el precio nuevo, sin nada que lo corrobore.
    await sql`
      DELETE FROM price_history
      WHERE variant_id = ${seeded.variantId} AND scrape_run_id IS NULL`;
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.candidates).toBe(1);
    expect(summary.deals).toBe(0);
    expect(sent).toHaveLength(0);
    expect(await countNotifications()).toBe(0);
  });

  it('envío fallido: no se da por avisado, se reintenta y acaba llegando', async () => {
    const user = await seedLinkedUser('kc-match-fallo', 909);
    await seedInterest(user.id);
    const caido = fakeTelegram(false);

    const failed = await makeService(caido.client).run(false);

    expect(failed.failedSends).toBe(1);
    expect(failed.notified).toBe(0);
    // Ni fila reservada ni marca de agua: un aviso no entregado no puede darse por hecho, o el
    // usuario nunca se enteraría de esta bajada.
    expect(await countNotifications()).toBe(0);
    expect(await sql`SELECT * FROM job_state WHERE job = 'matching'`).toHaveLength(0);

    // Telegram vuelve: la siguiente pasada sí lo entrega.
    const ok = fakeTelegram(true);
    const retry = await makeService(ok.client).run(false);

    expect(retry.notified).toBe(1);
    expect(ok.sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);
  });

  it('un envío caído no bloquea al resto de usuarios ni pierde su aviso', async () => {
    const ok = await seedLinkedUser('kc-match-mixto-ok', 920);
    await seedInterest(ok.id);
    const ko = await seedLinkedUser('kc-match-mixto-ko', 921);
    await seedInterest(ko.id);

    // Falla solo el envío al segundo usuario.
    const sent: Array<{ chatId: number; text: string }> = [];
    const client = {
      enabled: true,
      sendMessage: (chatId: number, text: string) => {
        sent.push({ chatId, text });
        return Promise.resolve(chatId !== 921);
      },
    } as unknown as TelegramApiClient;

    const summary = await makeService(client).run(false);

    expect(summary.notified).toBe(1);
    expect(summary.failedSends).toBe(1);
    // Solo queda la reserva del que sí recibió el aviso.
    const rows = await sql<{ user_id: string }[]>`SELECT user_id FROM notification`;
    expect(rows).toHaveLength(1);
    expect(Number(rows[0].user_id)).toBe(Number(ok.id));
    // La marca de agua no avanza: el lote se reevaluará para recuperar al que falló.
    expect(await sql`SELECT * FROM job_state WHERE job = 'matching'`).toHaveLength(0);
  });

  it('la marca de agua avanza al mayor scrape_run del lote', async () => {
    const user = await seedLinkedUser('kc-match-wm', 910);
    await seedInterest(user.id);
    const { client } = fakeTelegram();

    await makeService(client).run(false);

    const [state] = await sql<{ last_scrape_run_id: string }[]>`
      SELECT last_scrape_run_id FROM job_state WHERE job = 'matching'`;
    expect(Number(state.last_scrape_run_id)).toBe(runId);
  });
});
