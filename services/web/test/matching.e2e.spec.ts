import { drizzle } from 'drizzle-orm/postgres-js';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { schema } from '../src/database/schema';
import { InterestsService } from '../src/interests/interests.service';
import { MatchingService } from '../src/matching/matching.service';
import { TELEGRAM_MAX_CHARS } from '../src/matching/message';
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
    const service = new MatchingService(drizzle(sql, { schema }) as never, telegram);
    // Sin espera entre trozos: la pausa real es para no chocar con el rate-limit de la Bot API, y
    // aquí el cliente es un doble.
    service.chunkDelayMs = 0;
    return service;
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

  /**
   * `n` variantes más de la misma prenda, cada una con su color, todas con la misma bajada. Sirven
   * para llenar un resumen: colores distintos para que `collapseSameGarment` no las funda en una.
   */
  async function seedVariantes(n: number): Promise<void> {
    for (let i = 0; i < n; i += 1) {
      const [v] = await sql<{ id: number }[]>`
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
        VALUES (${seeded.productId}, ${`ZARA-1-24-c${i}`}, '24', ${`color-${i}`}, ${`SKU24C${i}`})
        RETURNING id`;
      await sql`
        INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                   scrape_run_id)
        VALUES (${v.id}, 39.99, 39.99, 0, true, now() - interval '2 days', NULL),
               (${v.id}, 19.99, 39.99, 50, true, now(), ${runId})`;
    }
  }

  async function countNotifications(): Promise<number> {
    const [row] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM notification`;
    return Number(row.n);
  }

  /** Suelo de `job_state`, o `null` si el matching no ha dejado ni latido (#278). */
  async function suelo(): Promise<number | null> {
    const [state] = await sql<{ last_scrape_run_id: string }[]>`
      SELECT last_scrape_run_id FROM job_state WHERE job = 'matching'`;
    return state ? Number(state.last_scrape_run_id) : null;
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

  it('dejar de seguir y volver a seguir no reabre el aviso ya entregado (#149)', async () => {
    // El ciclo completo por el que existe la 0025, con el servicio de intereses de verdad y no con
    // un UPDATE a mano: mientras la baja fue física, el CASCADE de `notification.interest_id` se
    // llevaba la fila que protegía este evento de precio y el aviso volvía a salir por un id nuevo.
    const user = await seedLinkedUser('kc-match-refollow', 903);
    const { client, sent } = fakeTelegram();
    const service = makeService(client);
    const interests = new InterestsService(drizzle(sql, { schema }) as never);

    const creado = await interests.create(user.id, { productId: seeded.productId });
    await service.run(false);
    expect(sent).toHaveLength(1);

    await interests.remove(user.id, creado.id);
    const reactivado = await interests.create(user.id, { productId: seeded.productId });
    expect(reactivado.id).toBe(creado.id); // la misma fila, con su historial debajo
    expect(await countNotifications()).toBe(1);

    // Con la marca de agua rebobinada el lote se vuelve a evaluar entero, que es el escenario que
    // de verdad puede repetir el mensaje.
    await sql`UPDATE job_state SET last_scrape_run_id = 0 WHERE job = 'matching'`;
    const second = await service.run(false);

    expect(second.candidates).toBe(1);
    expect(second.notified).toBe(0);
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
    expect(await suelo()).toBeNull(); // ni marca de agua ni latido: el dry-run no escribe
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
    // Y el mensaje enseña la talla CANÓNICA (#223). Esto invierte lo que afirmaba este test hasta
    // ahora —«la de la tienda, es lo que el usuario verá al abrir el enlace»—, y conviene dejar
    // escrito por qué se cambió de opinión: el aviso y la web tienen que nombrar la variante igual.
    // El usuario sigue un seguimiento que su lista rotula «Talla 24»; que el bot le hablara de una
    // «Talla 24 (14,9 cm)» le obliga a deducir que son la misma. La ficha de la tienda, al otro
    // lado del enlace, enseña sus propias tallas de todas formas.
    expect(sent[0].text).toContain('Talla 24 · rojo');
    expect(sent[0].text).not.toContain('14,9 cm');
  });

  it('no confunde tallas distintas al normalizar', async () => {
    // La red de seguridad del test anterior: si `size_canon` fundiera de más, esto pasaría a avisar.
    const user = await seedLinkedUser('kc-match-talla-distinta', 911);
    await sql`UPDATE variant SET size = '34 (21,6 cm)' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { size: '24' });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  /**
   * El otro modo de fallo de #64, el que no se ve en la faceta. Antes de 0017, un rango de número de
   * pie ('25-34', las plantillas de Cacles) se canonicalizaba como '25-34 años', así que quedaba
   * indistinguible de un rango de EDAD de ropa escrito con esa etiqueta. Dos tallas que no tienen
   * nada que ver casaban, y el aviso llegaba mal sin que fallara nada ruidosamente.
   */
  it('un rango de número de pie no casa con un rango de edad (#64)', async () => {
    const user = await seedLinkedUser('kc-match-rango-pie', 912);
    await sql`UPDATE variant SET size = '25-34' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { size: '25-34 años' });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).candidates).toBe(0);
  });

  it('avisa de una plantilla por su rango de número de pie (#64)', async () => {
    // Y el lado positivo: el chip '48-51' de la faceta sí encuentra la variante, venga la talla con
    // guion o con la barra que usa Cacles en el calzado de primeros pasos.
    const user = await seedLinkedUser('kc-match-rango-pie-ok', 913);
    await sql`UPDATE variant SET size = '48-51' WHERE id = ${seeded.variantId}`;
    await seedInterest(user.id, { size: '48-51' });
    const { client } = fakeTelegram();

    expect((await makeService(client).run(false)).notified).toBe(1);
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

    // La marca de agua avanza aunque el lote no dé candidatos (#221), así que se rebobina en cada
    // vuelta para que todas vean el mismo lote: lo que se comprueba aquí es el filtro barefoot, y
    // sin esto las vueltas 2 y 3 saldrían a 0 candidatos por no mirar nada.
    const rebobinar = () => sql`DELETE FROM job_state WHERE job = 'matching'`;

    for (const marca of ['no', 'desconocido', null]) {
      await sql`UPDATE product SET barefoot = ${marca} WHERE id = ${seeded.productId}`;
      await rebobinar();
      const summary = await makeService(client).run(false);
      expect(summary.candidates, `barefoot=${marca}`).toBe(0);
    }
    expect(sent).toHaveLength(0);
    expect(await countNotifications()).toBe(0);

    // ...y con la marca puesta, el mismo caso sí avisa: el filtro es lo único que cambiaba.
    await sql`UPDATE product SET barefoot = 'si' WHERE id = ${seeded.productId}`;
    await rebobinar();
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
    // El latido deja fila —el pase llegó al final— pero el suelo se queda a 0 (#278).
    expect(await suelo()).toBe(0);

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
    // El latido deja fila —el pase llegó al final— pero el suelo se queda a 0 (#278).
    expect(await suelo()).toBe(0);
  });

  /**
   * #220: el resumen se mandaba de una pieza. Con el lote real de QA —87 prendas, 17 717
   * caracteres— Telegram devolvía 400, el job lo contaba como envío fallido, la marca de agua no
   * avanzaba y el lote volvía más grande a la pasada siguiente. Se atascaba solo.
   */
  it('un lote grande se trocea y ningún mensaje pasa del límite de Telegram (#220)', async () => {
    const user = await seedLinkedUser('kc-match-troceo', 930);
    await seedInterest(user.id);
    await seedVariantes(40);
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.deals).toBe(41); // las 40 sembradas aquí más la de seedCatalog
    expect(summary.notified).toBe(41);
    expect(summary.usersNotified).toBe(1);
    expect(summary.failedSends).toBe(0);

    expect(sent.length).toBeGreaterThan(1);
    for (const mensaje of sent) {
      expect(mensaje.chatId).toBe(930);
      expect(mensaje.text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    }
    expect(await countNotifications()).toBe(41);
  });

  it('si falla un trozo, lo entregado no se repite y solo se reintenta el resto (#220)', async () => {
    const user = await seedLinkedUser('kc-match-troceo-fallo', 931);
    await seedInterest(user.id);
    await seedVariantes(40);

    // Telegram acepta el primer mensaje y rechaza el segundo.
    const enviados: string[] = [];
    const client = {
      enabled: true,
      sendMessage: (_chatId: number, text: string) => {
        enviados.push(text);
        return Promise.resolve(enviados.length === 1);
      },
    } as unknown as TelegramApiClient;

    const parcial = await makeService(client).run(false);

    // Corta al primer rechazo en vez de seguir insistiendo: si es un 429 por ritmo, insistir lo
    // empeora.
    expect(enviados).toHaveLength(2);
    expect(parcial.failedSends).toBe(1);
    expect(parcial.usersNotified).toBe(1);
    const entregadas = parcial.notified;
    expect(entregadas).toBeGreaterThan(0);
    expect(entregadas).toBeLessThan(41);

    // Solo conservan su reserva las ofertas del trozo que sí salió; las demás se sueltan para que
    // la pasada siguiente vuelva a evaluarlas.
    expect(await countNotifications()).toBe(entregadas);
    // El latido deja fila —el pase llegó al final— pero el suelo se queda a 0 (#278).
    expect(await suelo()).toBe(0);

    // Telegram vuelve. El lote se reprocesa entero, pero lo ya entregado choca contra el UNIQUE:
    // solo se avisa de lo que faltaba, y nadie recibe dos veces la misma prenda.
    const ok = fakeTelegram(true);
    const retry = await makeService(ok.client).run(false);

    expect(retry.notified).toBe(41 - entregadas);
    expect(await countNotifications()).toBe(41);
    const [state] = await sql<{ last_scrape_run_id: string }[]>`
      SELECT last_scrape_run_id FROM job_state WHERE job = 'matching'`;
    expect(Number(state.last_scrape_run_id)).toBe(runId);
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

  /**
   * #278: el matching de producción murió en 26 s dejando `job_state` vacío, y con el pod borrado
   * no había forma de saber si había corrido alguna vez. La fila solo se escribía cuando el suelo
   * **avanzaba**, así que su ausencia tampoco distinguía «no había pasadas pendientes» de «no
   * llegué a mirar». El latido separa las dos cosas.
   */
  it('un pase sin pasadas pendientes deja latido aunque no haya nada que hacer (#278)', async () => {
    const { client } = fakeTelegram();
    // La primera consume la pasada del seed y deja el suelo en `runId`.
    await makeService(client).run(false);
    await sql`UPDATE job_state SET updated_at = now() - interval '1 day' WHERE job = 'matching'`;

    // La segunda no tiene nada pendiente: sin latido no escribiría nada.
    const segunda = await makeService(client).run(false);

    expect(segunda.candidates).toBe(0);
    // La frescura se pregunta en SQL: el reloj que importa es el del servidor, no el del test.
    const [state] = await sql<{ last_scrape_run_id: string; fresco: boolean }[]>`
      SELECT last_scrape_run_id, updated_at > now() - interval '1 minute' AS fresco
        FROM job_state WHERE job = 'matching'`;
    // El suelo no se mueve —no había nada que absorber— pero `updated_at` sí: eso es lo que
    // convierte «¿desde cuándo no termina el matching?» en una consulta.
    expect(Number(state.last_scrape_run_id)).toBe(runId);
    expect(state.fresco).toBe(true);
  });

  /**
   * #221: la marca salía de las filas candidatas, o sea de lo que sobrevive al JOIN con `interest`.
   * En QA se quedó en 34 con pasadas correctas hasta la 38 —mango, sfera, zara y springfield no
   * tenían a nadie que las siguiera—, así que cada ejecución volvía a escanearlas para nada.
   */
  it('la marca de agua avanza aunque la pasada no produzca ningún candidato (#221)', async () => {
    const user = await seedLinkedUser('kc-match-wm-sin-candidatos', 912);
    await seedInterest(user.id);

    // Pasada posterior, con precios de una prenda que no sigue nadie: se escanea entera y no deja
    // ni una fila candidata.
    const [otra] = await sql<{ id: number }[]>`
      INSERT INTO scrape_run (retailer_id, status, finished_at)
      VALUES (${seeded.retailerId}, 'success', now()) RETURNING id`;
    const [p2] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${seeded.retailerId}, 'ZARA-2', 'Camiseta que no sigue nadie', 'niño', 'ropa',
              'camisetas', 'desconocido', 'https://x/2')
      RETURNING id`;
    const [v2] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p2.id}, 'ZARA-2-4', '4 años', 'azul', 'SKU4A') RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                 scrape_run_id)
      VALUES (${v2.id}, 9.99, 19.99, 50, true, now(), ${otra.id})`;

    const { client } = fakeTelegram();
    const summary = await makeService(client).run(false);

    // El aviso sale del lote de siempre y la pasada nueva no aporta candidatos...
    expect(summary.deals).toBe(1);
    // ...pero la marca llega hasta ella igual, en vez de clavarse en la última que sí avisó.
    expect(summary.watermark).toBe(Number(otra.id));

    const [state] = await sql<{ last_scrape_run_id: string }[]>`
      SELECT last_scrape_run_id FROM job_state WHERE job = 'matching'`;
    expect(Number(state.last_scrape_run_id)).toBe(Number(otra.id));
  });

  /**
   * #240, el escenario entero. Una pasada que sigue en transacción **no existe para nadie**: su
   * fila de `scrape_run` viaja dentro de su propia transacción. Aquí se reproduce quitándola de la
   * base y devolviéndola después con su id original, que es exactamente lo que ve el job.
   *
   * Con el diseño anterior —guardar el mayor id visto— este spec falla: la marca saltaba a la
   * pasada posterior y las filas de la rezagada quedaban por debajo para siempre.
   */
  it('una pasada que commitea tarde se evalúa cuando aparece, no se pierde (#240)', async () => {
    const user = await seedLinkedUser('kc-match-rezagada', 913);
    await seedInterest(user.id);

    // La pasada `runId` (la rezagada) todavía no ha commiteado: ni su fila ni sus precios se ven.
    await sql`UPDATE price_history SET scrape_run_id = NULL WHERE scrape_run_id = ${runId}`;
    await sql`DELETE FROM scrape_run WHERE id = ${runId}`;

    // Mientras tanto arranca y termina una pasada POSTERIOR, con su propio precio.
    const [posterior] = await sql<{ id: number }[]>`
      INSERT INTO scrape_run (retailer_id, status, finished_at)
      VALUES (${seeded.retailerId}, 'success', now()) RETURNING id`;
    const [p2] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${seeded.retailerId}, 'ZARA-9', 'Prenda de la pasada posterior', 'niño', 'ropa',
              'camisetas', 'desconocido', 'https://x/9')
      RETURNING id`;
    const [v2] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p2.id}, 'ZARA-9-4', '4 años', 'azul', 'SKU9A') RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                 scrape_run_id)
      VALUES (${v2.id}, 9.99, 19.99, 50, true, now(), ${posterior.id})`;

    const { client, sent } = fakeTelegram();
    const primera = await makeService(client).run(false);

    // Nadie sigue esa prenda, así que no hay aviso; lo que importa es dónde se queda el suelo.
    expect(primera.deals).toBe(0);
    // NO avanza hasta la posterior: el id de la rezagada es un hueco en la secuencia, y un hueco
    // puede ser una pasada en vuelo. Pasarlo es justo lo que perdía el lote.
    expect(primera.watermark).toBe(runId - 1);
    // La posterior sí queda anotada, para no reevaluarla mientras el suelo espera.
    const libro = await sql<{ scrape_run_id: string }[]>`
      SELECT scrape_run_id FROM matching_scanned_run ORDER BY 1`;
    expect(libro.map((r) => Number(r.scrape_run_id))).toEqual([Number(posterior.id)]);

    // Y ahora la rezagada commitea, con el id que tenía reservado desde el principio.
    await sql`
      INSERT INTO scrape_run (id, retailer_id, status, finished_at)
      OVERRIDING SYSTEM VALUE
      VALUES (${runId}, ${seeded.retailerId}, 'success', now())`;
    await sql`
      UPDATE price_history SET scrape_run_id = ${runId}
      WHERE variant_id = ${seeded.variantId} AND price = 19.99`;

    const segunda = await makeService(client).run(false);

    // Se evalúa y se avisa: es el aviso que antes no llegaba nunca.
    expect(segunda.deals).toBe(1);
    expect(segunda.notified).toBe(1);
    expect(sent).toHaveLength(1);
    // Y con el hueco ya resuelto, el suelo cruza las dos de una vez.
    expect(segunda.watermark).toBe(Number(posterior.id));
  });

  /**
   * La cara incómoda de recuperar la pasada rezagada, y la casilla 3 de #240 mirada de verdad: el
   * `price_event_key` es `<scrape_run_id>:<precio>`, así que para el MISMO precio en dos pasadas la
   * clave cambia y el UNIQUE de la 0005 **no** protege. Quien tiene que cortar es `evaluateDeal`.
   *
   * Solo puede darse si se solapan dos pasadas de la MISMA tienda (una variante vive en una sola
   * tienda, y hay un `scrape_run` por tienda y pasada). Se deja pinchado para que quien toque la
   * regla vea qué depende de ella.
   */
  it('la rezagada no repite el aviso que ya mandó la posterior por el mismo precio (#240)', async () => {
    const user = await seedLinkedUser('kc-match-rezagada-dup', 915);
    await seedInterest(user.id);

    // La rezagada aún no ha commiteado: sus filas no existen para nadie, ni siquiera como
    // histórico. Se borran de verdad, que es lo único fiel — con la fila ahí, la posterior la vería
    // como mínimo previo y no avisaría, y el spec probaría otra cosa.
    await sql`DELETE FROM price_history WHERE scrape_run_id = ${runId}`;
    await sql`DELETE FROM scrape_run WHERE id = ${runId}`;

    // La pasada posterior ve la MISMA variante al MISMO precio, un minuto después.
    const [posterior] = await sql<{ id: number }[]>`
      INSERT INTO scrape_run (retailer_id, status, finished_at)
      VALUES (${seeded.retailerId}, 'success', now()) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                 scrape_run_id)
      VALUES (${seeded.variantId}, 19.99, 39.99, 50, true, now() + interval '1 minute',
              ${posterior.id})`;

    const { client, sent } = fakeTelegram();
    const primera = await makeService(client).run(false);
    expect(primera.notified).toBe(1); // la posterior avisa de la bajada

    // Y ahora commitea la rezagada, con su precio idéntico y su sello ANTERIOR.
    await sql`
      INSERT INTO scrape_run (id, retailer_id, status, finished_at)
      OVERRIDING SYSTEM VALUE
      VALUES (${runId}, ${seeded.retailerId}, 'success', now())`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                 scrape_run_id)
      VALUES (${seeded.variantId}, 19.99, 39.99, 50, true, now(), ${runId})`;

    const segunda = await makeService(client).run(false);

    expect(segunda.notified).toBe(0);
    expect(sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);
  });

  it('el libro no crece cuando las pasadas van en orden: el suelo las absorbe (#240)', async () => {
    const user = await seedLinkedUser('kc-match-libro', 914);
    await seedInterest(user.id);
    const { client } = fakeTelegram();

    await makeService(client).run(false);

    // Todo lo que queda por debajo del suelo lo dice `job_state`: guardarlo también en el libro
    // sería duplicar el estado y hacerlo crecer sin tope.
    const [{ n }] = await sql<{ n: string }[]>`SELECT count(*) AS n FROM matching_scanned_run`;
    expect(Number(n)).toBe(0);
    const [state] = await sql<{ last_scrape_run_id: string }[]>`
      SELECT last_scrape_run_id FROM job_state WHERE job = 'matching'`;
    expect(Number(state.last_scrape_run_id)).toBe(runId);
  });

  /**
   * Segunda cara de la MISMA talla y color, con otro SKU: es lo que publican Lefties, H&M e
   * Hipercor (#108). Baja al mismo precio y en la misma pasada que la sembrada, que es lo que
   * pasa siempre en la realidad —las dos caras comparten precio en el 100 % de los grupos—.
   *
   * `url` es lo que decide si son la misma prenda (misma ficha en la tienda) o dos artículos
   * distintos que la tienda publica por separado.
   */
  async function seedSegundaCara(url: string | null): Promise<number> {
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url)
      VALUES (${seeded.productId}, 'ZARA-1-24-rojo-bis', '24', 'rojo', 'SKU24BIS', ${url})
      RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at,
                                 scrape_run_id)
      VALUES (${v.id}, 39.99, 39.99, 0, true, now() - interval '2 days', NULL),
             (${v.id}, 19.99, 39.99, 50, true, now(), ${runId})`;
    return Number(v.id);
  }

  it('dos SKU de la misma prenda: un solo aviso (#108)', async () => {
    const user = await seedLinkedUser('kc-match-dup', 911);
    await seedInterest(user.id);
    const bis = await seedSegundaCara(null); // misma ficha que la sembrada (las dos con url NULL)
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    // Las dos caras son candidatas y las dos son oferta, pero es una sola prenda comprable.
    expect(summary.candidates).toBe(2);
    expect(summary.deals).toBe(1);
    expect(summary.duplicatesCollapsed).toBe(1);
    expect(sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);

    // Gana la de menor id a igualdad de precio: determinista entre pasadas.
    const [n] = await sql<{ variant_id: string }[]>`SELECT variant_id FROM notification`;
    expect(Number(n.variant_id)).toBe(Number(seeded.variantId));
    expect(Number(n.variant_id)).not.toBe(bis);
  });

  it('dos artículos distintos con el mismo color: siguen siendo dos avisos (el caso de H&M)', async () => {
    // Los 803 grupos de H&M no son dos caras de lo mismo: son dos referencias con su propia
    // ficha. Sin esta prueba, "simplificar" la clave quitando la URL las borraría en silencio.
    const user = await seedLinkedUser('kc-match-dup-hm', 912);
    await seedInterest(user.id);
    await seedSegundaCara('https://x/1315153005.html');
    const { client, sent } = fakeTelegram();

    const summary = await makeService(client).run(false);

    expect(summary.deals).toBe(2);
    expect(summary.duplicatesCollapsed).toBe(0);
    expect(sent).toHaveLength(1); // un digest por usuario, con las dos prendas dentro
    expect(await countNotifications()).toBe(2);
  });

  it('el colapso es estable: rebobinar la marca de agua no cuela el aviso por la otra cara', async () => {
    const user = await seedLinkedUser('kc-match-dup-rewind', 913);
    await seedInterest(user.id);
    await seedSegundaCara(null);
    const { client, sent } = fakeTelegram();
    const service = makeService(client);

    await service.run(false);
    await sql`UPDATE job_state SET last_scrape_run_id = 0 WHERE job = 'matching'`;
    const second = await service.run(false);

    // Si el representante cambiara entre pasadas, la fila de notification del primero no
    // protegería al segundo y el usuario recibiría el mismo aviso otra vez.
    expect(second.candidates).toBe(2);
    expect(second.notified).toBe(0);
    expect(sent).toHaveLength(1);
    expect(await countNotifications()).toBe(1);
  });
});
