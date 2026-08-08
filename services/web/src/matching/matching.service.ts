import { Inject, Injectable, Logger } from '@nestjs/common';
import { inArray, sql } from 'drizzle-orm';

import { generoCondition } from '../catalog/gender.sql';
import { Database, DRIZZLE } from '../database/database.module';
import { matchingScannedRun, notification } from '../database/schema';
import { TelegramApiClient } from '../telegram/telegram-api.client';
import { evaluateDeal } from './deal-rule';
import { collapseSameGarment } from './dedupe';
import { buildDigestChunks } from './message';
import type { CandidateRow, Deal, MatchingSummary } from './matching.types';

/** Identificador del job en `job_state`. */
const JOB = 'matching';

/**
 * Espera entre los trozos de un mismo resumen. La Bot API admite ~1 mensaje por segundo y chat, y
 * un resumen troceado los manda seguidos: sin pausa, un lote grande se gana un 429 y con él el
 * mismo atasco que #220 viene a quitar.
 */
const CHUNK_DELAY_MS = 1_100;

/**
 * Evalúa los precios recién scrapeados contra los intereses de los usuarios y avisa por Telegram.
 *
 * Incremental por **pasadas pendientes**: el lote son los `scrape_run` que tienen precios y que este
 * job no ha evaluado todavía. El estado son dos piezas complementarias: `job_state` guarda el
 * **suelo** (todo id por debajo está resuelto) y `matching_scanned_run` el libro de lo procesado por
 * encima de él.
 *
 * No basta con guardar el mayor id visto, que es lo que se hacía hasta #240: los `scrape_run` **no
 * se completan en orden de id**. Una pasada larga puede seguir en transacción cuando otra posterior
 * commitea, y sus filas nacerían por debajo de la marca — o sea invisibles para siempre, sin que
 * nada lo delate. Preguntando "¿qué he procesado ya?" en vez de "¿hasta dónde he llegado?", la
 * rezagada entra sola en el lote siguiente en cuanto commitea, que es lo único que se puede
 * observar: mientras está abierta no deja ningún rastro (su fila de `scrape_run` viaja dentro de su
 * propia transacción).
 */
@Injectable()
export class MatchingService {
  private readonly logger = new Logger(MatchingService.name);

  /**
   * Espera entre trozos, en ms. Propiedad y no parámetro del constructor: cuando se escribió,
   * nada montaba `MatchingModule` en los tests —solo lo hacía el job—, así que un parámetro más
   * que Nest tuviera que resolver habría roto en el cluster sin que nadie se enterase aquí. Desde
   * #239 eso ya no es cierto (`test/jobs-di.spec.ts` levanta el contexto del job y pide este
   * servicio al contenedor), pero la propiedad se queda: el doble de Telegram de los e2e sigue
   * necesitando bajarla a 0 sin pasar por la DI.
   */
  chunkDelayMs = CHUNK_DELAY_MS;

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly telegram: TelegramApiClient,
  ) {}

  /**
   * Ejecuta una pasada. En `dryRun` no escribe nada (ni `notification` ni marca de agua) y solo
   * registra qué habría enviado: es lo que corre en `dev`, donde no hay bot.
   */
  async run(dryRun: boolean): Promise<MatchingSummary> {
    const floor = await this.readFloor();
    const pendingRuns = await this.readPendingRuns(floor);
    const rows = pendingRuns.length === 0 ? [] : await this.findCandidates(pendingRuns);

    const evaluadas: Deal[] = [];
    for (const row of rows) {
      const verdict = evaluateDeal(row);
      if (verdict.notify) {
        evaluadas.push({ row, verdict, priceEventKey: `${row.scrapeRunId}:${row.price}` });
      }
    }

    // Dos SKU de la misma prenda son un solo aviso (#108). Ver `collapseSameGarment`.
    const { kept: deals, collapsed } = collapseSameGarment(evaluadas);

    // Los ids del lote, y no solo cuántos: una pasada que aparece con id **menor** que el suelo
    // anterior es la firma de #240 —una rezagada que commiteó tarde— y sin verlos no se distingue
    // de una pasada normal.
    this.logger.log(
      `Lote (suelo ${floor}): pasada(s) [${pendingRuns.join(', ')}], ` +
        `${rows.length} candidato(s), ${deals.length} oferta(s)` +
        (collapsed > 0 ? `, ${collapsed} cara(s) duplicada(s) colapsada(s)` : ''),
    );

    const summary: MatchingSummary = {
      dryRun,
      watermark: floor,
      candidates: rows.length,
      deals: deals.length,
      duplicatesCollapsed: collapsed,
      notified: 0,
      usersNotified: 0,
      failedSends: 0,
    };

    for (const [userId, userDeals] of groupByUser(deals)) {
      if (dryRun) {
        const chunks = buildDigestChunks(userDeals);
        this.logger.log(
          `[dry-run] usuario ${userId}: ${userDeals.length} aviso(s) en ${chunks.length} mensaje(s)\n` +
            chunks.map((c) => c.text).join('\n---\n'),
        );
        summary.notified += userDeals.length;
        summary.usersNotified += 1;
        continue;
      }

      // Reservar ANTES de enviar: si el proceso muere tras el envío, el reintento no duplica.
      // Lo ya avisado choca contra el UNIQUE y no vuelve a salir.
      const fresh = await this.reserve(userDeals);
      if (fresh.length === 0) continue;

      const entregados = await this.sendDigest(userId, fresh);
      summary.notified += entregados.length;
      if (entregados.length > 0) summary.usersNotified += 1;
      if (entregados.length < fresh.length) summary.failedSends += 1;
    }

    // El estado solo avanza si todo se entregó: si no, el próximo intento (o el reintento del Job
    // de k8s) reprocesa el lote. Los ya avisados están protegidos por su fila.
    if (!dryRun && pendingRuns.length > 0 && summary.failedSends === 0) {
      await this.markScanned(pendingRuns);
      summary.watermark = await this.advanceFloor(floor);
    }
    if (!dryRun) await this.touchHeartbeat(summary.watermark);

    this.logger.log(
      `Fin${dryRun ? ' (dry-run, sin cambios)' : ''}: ${summary.notified} aviso(s) para ` +
        `${summary.usersNotified} usuario(s), suelo ${summary.watermark}`,
    );
    return summary;
  }

  /**
   * Manda el resumen de un usuario, troceado (#220). Devuelve las ofertas realmente entregadas.
   *
   * Corta al primer trozo que Telegram rechaza: si es un 429 por ritmo, insistir solo lo empeora.
   * Suelta la reserva de lo que no salió para que la pasada siguiente vuelva a evaluarlo — si no,
   * esas filas quedarían marcadas como avisadas y el usuario **nunca** se enteraría de la bajada.
   * Lo ya entregado, en cambio, conserva su fila, y es eso lo que impide que el reintento lo
   * repita: la marca de agua no avanza mientras haya un envío fallido, así que el lote entero se
   * reprocesa y lo entregado choca contra el UNIQUE.
   *
   * Queda un duplicado posible, el de siempre: que Telegram entregase el trozo y no llegásemos a
   * ver su respuesta. Un duplicado ocasional es preferible a un silencio permanente.
   */
  private async sendDigest(userId: number, fresh: Deal[]): Promise<Deal[]> {
    const chatId = fresh[0].row.telegramChatId;
    const chunks = buildDigestChunks(fresh);
    const entregados: Deal[] = [];

    for (let i = 0; i < chunks.length; i += 1) {
      // Pausa entre trozos, no antes del primero: un aviso normal es un solo mensaje y no espera.
      if (i > 0) await sleep(this.chunkDelayMs);

      if (await this.telegram.sendMessage(chatId, chunks[i].text)) {
        entregados.push(...chunks[i].deals);
        continue;
      }

      const pendientes = chunks.slice(i).flatMap((c) => c.deals);
      await this.release(pendientes);
      this.logger.error(
        `No se pudo entregar el mensaje ${i + 1}/${chunks.length} del aviso al usuario ${userId}; ` +
          `${pendientes.length} oferta(s) se reintentarán`,
      );
      break;
    }

    return entregados;
  }

  /**
   * Las pasadas con precios que este job todavía no ha evaluado.
   *
   * **Sin tope por arriba** a propósito: una pasada rezagada commitea con un id *menor* que el de
   * otra ya procesada, y cualquier `max(...)` la volvería a dejar fuera — que es el fallo entero de
   * #240.
   *
   * Se mide sobre `price_history` a pelo, sin cruzar con `interest` ni filtrar por stock ni por el
   * foco barefoot (#221): lo que decide que una pasada está vista es haberla mirado, no que
   * produjera aviso. Derivarlo de las filas candidatas dejaba el estado clavado en la última pasada
   * con candidato — en QA se quedó en 34 con pasadas correctas hasta la 38, porque mango, sfera,
   * zara y springfield no tenían a nadie que las siguiera—, así que cada ejecución volvía a
   * escanearlas y el coste crecía con el histórico. Se agrava cuantos menos intereses haya, o sea
   * al arrancar.
   *
   * Las filas con `scrape_run_id` NULL no son de ninguna pasada (histórico sembrado a mano) y el
   * `>` ya las descarta.
   */
  private async readPendingRuns(floor: number): Promise<number[]> {
    const rows = await this.db.execute(sql`
      SELECT DISTINCT ph.scrape_run_id AS id
        FROM price_history ph
       WHERE ph.scrape_run_id > ${floor}
         AND NOT EXISTS (
               SELECT 1 FROM matching_scanned_run m WHERE m.scrape_run_id = ph.scrape_run_id)
       ORDER BY 1
    `);
    return (rows as unknown as { id: string | number }[]).map((r) => Number(r.id));
  }

  /**
   * Precios nuevos × intereses que los siguen × estadísticos del histórico de la variante.
   *
   * El patrón `(i.x IS NULL OR i.x = b.x)` es el filtro parcial del catálogo invertido: aquí el
   * criterio sale de la fila `interest` (NULL = "cualquiera"). El JOIN a `app_user` descarta a
   * quien no tiene Telegram: sin canal no hay aviso, y tampoco se quema el evento en `notification`.
   */
  private async findCandidates(runIds: number[]): Promise<CandidateRow[]> {
    const rows = await this.db.execute(sql`
      WITH batch AS (
        SELECT ph.variant_id, ph.price, ph.list_price, ph.scraped_at, ph.scrape_run_id,
               v.product_id, v.size, v.color,
               p.retailer_id, p.gender, p.section, p.category,
               p.name AS product_name, coalesce(v.url, p.url) AS product_url,
               r.name AS retailer_name
        FROM price_history ph
        JOIN variant  v ON v.id = ph.variant_id
        JOIN product  p ON p.id = v.product_id
        JOIN retailer r ON r.id = p.retailer_id
        -- Sin paréntesis alrededor del array: drizzle despliega los ids en un parámetro por
        -- elemento y ya los envuelve él, así que un par de más los convierte en un record y la
        -- comparación revienta con "bigint = record". readPendingRuns garantiza que no viene vacío.
        WHERE ph.scrape_run_id IN ${runIds} AND ph.in_stock
          -- Solo el precio VIGENTE de la variante. Importa desde que el lote puede traer una
          -- pasada rezagada (#240): sus precios pueden estar ya superados por otra posterior, y
          -- avisar de uno superado es mandar al móvil de alguien un precio que ya no existe. De
          -- paso cierra el duplicado que asomaba por ahí — la clave del evento lleva el
          -- scrape_run_id, así que el mismo precio en dos pasadas son dos claves y el UNIQUE de la
          -- 0005 no lo habría parado.
          AND NOT EXISTS (
                SELECT 1 FROM price_history nueva
                 WHERE nueva.variant_id = ph.variant_id AND nueva.scraped_at > ph.scraped_at)
          -- Foco barefoot (#30): no se avisa de calzado que no sea respetuoso. Un aviso es más
          -- intrusivo que una tarjeta del catálogo — llega solo al móvil de alguien — así que
          -- mandar ahí lo que el catálogo esconde sería la peor versión del mismo error.
          -- IS DISTINCT FROM y no <>: con section NULL, <> daría NULL y tiraría la fila.
          AND (p.section IS DISTINCT FROM 'zapateria' OR p.barefoot = 'si')
      )
      SELECT i.id AS interest_id, i.user_id, i.min_discount_pct, i.compare_base, i.window_days,
             u.telegram_chat_id,
             b.variant_id, b.price, b.list_price, b.scrape_run_id, b.size, b.color,
             -- Las cuatro piezas con las que se decide que dos variantes son la misma prenda
             -- comprable (#108). Las canónicas salen de la base, que es donde vive la única
             -- definición de "misma talla" y "mismo color" que usa también el WHERE de arriba.
             b.product_id, size_canon(b.size) AS size_canon, color_canon(b.color) AS color_canon,
             b.product_name, b.product_url, b.retailer_name,
             st.recent_min, st.max_observed, st.prior_points
      FROM batch b
      JOIN interest i ON i.active
        AND (i.variant_id  IS NULL OR i.variant_id  = b.variant_id)
        AND (i.product_id  IS NULL OR i.product_id  = b.product_id)
        AND (i.retailer_id IS NULL OR i.retailer_id = b.retailer_id)
        -- El género casa por generoCondition y no por igualdad: un zapato unisex tiene que
        -- disparar el aviso de quien sigue "niño" y el de quien sigue "niña". Es la misma regla
        -- que usa el catálogo, y a propósito el mismo fichero — si el catálogo enseñara bajo
        -- "Niño" un zapato con el que luego el aviso no dispara, la promesa incumplida la vería
        -- el usuario sin poder explicársela.
        AND ${generoCondition(sql.raw('i.gender'), sql.raw('b.gender'))}
        AND (i.section  IS NULL OR i.section  = b.section)
        AND (i.category IS NULL OR i.category = b.category)
        -- La talla se compara CANÓNICA (#43). Con igualdad de texto crudo, un interés guardado con
        -- '26' —la talla que ofrece el filtro— nunca casaba con un zapato de Zara almacenado como
        -- '26 (16,3 cm)', y el aviso no fallaba ruidosamente: no llegaba, y nadie se enteraba.
        -- Se normalizan los dos lados aunque el alta ya guarde canónico: size_canon es idempotente
        -- y así una fila escrita a mano en la base tampoco se queda sin avisar.
        AND (i.size     IS NULL OR size_canon(i.size) = size_canon(b.size))
        -- Y el color CANÓNICO por lo mismo (#49): con igualdad de texto crudo, un interés guardado
        -- con 'Verde' nunca casaba con una prenda que la tienda escribió 'VERDE'.
        AND (i.color    IS NULL OR color_canon(i.color) = color_canon(b.color))
      JOIN app_user u ON u.id = i.user_id AND u.telegram_chat_id IS NOT NULL
      LEFT JOIN LATERAL (
        SELECT MIN(h.price) FILTER (
                 WHERE h.scraped_at >= b.scraped_at - (i.window_days || ' days')::interval
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points
        FROM price_history h
        WHERE h.variant_id = b.variant_id AND h.scraped_at < b.scraped_at
      ) st ON true
      ORDER BY i.user_id, b.variant_id
    `);

    return (rows as unknown as RawCandidate[]).map((r) => ({
      interestId: Number(r.interest_id),
      userId: Number(r.user_id),
      telegramChatId: Number(r.telegram_chat_id),
      minDiscountPct: String(r.min_discount_pct),
      compareBase: r.compare_base === 'list_price' ? 'list_price' : 'recent_min',
      windowDays: Number(r.window_days),
      variantId: Number(r.variant_id),
      price: String(r.price),
      listPrice: r.list_price === null ? null : String(r.list_price),
      scrapeRunId: Number(r.scrape_run_id),
      size: r.size,
      color: r.color,
      productId: Number(r.product_id),
      sizeCanon: r.size_canon,
      colorCanon: r.color_canon,
      productName: r.product_name,
      productUrl: r.product_url,
      retailerName: r.retailer_name,
      recentMin: r.recent_min === null ? null : String(r.recent_min),
      maxObserved: r.max_observed === null ? null : String(r.max_observed),
      priorPoints: Number(r.prior_points ?? 0),
    }));
  }

  /**
   * Inserta las filas de aviso y devuelve solo las nuevas. El UNIQUE
   * `(interest_id, variant_id, price_event_key)` de la migración 0005 es la garantía de que un
   * mismo evento de precio no se avisa dos veces.
   */
  private async reserve(deals: Deal[]): Promise<Deal[]> {
    const inserted = await this.db
      .insert(notification)
      .values(
        deals.map((d) => ({
          userId: d.row.userId,
          interestId: d.row.interestId,
          variantId: d.row.variantId,
          price: d.row.price,
          listPrice: d.verdict.honestListPrice === null ? null : String(d.verdict.honestListPrice),
          discountPct: String(d.verdict.discountPct),
          priceEventKey: d.priceEventKey,
        })),
      )
      .onConflictDoNothing()
      .returning({
        id: notification.id,
        interestId: notification.interestId,
        variantId: notification.variantId,
        priceEventKey: notification.priceEventKey,
      });

    // La clave incluye el evento de precio: un lote puede abarcar dos pasadas del scraper, y la
    // misma variante pudo bajar en ambas.
    const fresh = new Map(
      inserted.map((r) => [`${r.interestId}:${r.variantId}:${r.priceEventKey}`, r.id]),
    );
    return deals
      .map((d) => ({
        ...d,
        notificationId: fresh.get(`${d.row.interestId}:${d.row.variantId}:${d.priceEventKey}`),
      }))
      .filter((d) => d.notificationId !== undefined);
  }

  /** Deshace las reservas de un envío que no llegó, para poder reintentarlo. */
  private async release(deals: Deal[]): Promise<void> {
    const ids = deals.map((d) => d.notificationId).filter((id): id is number => id !== undefined);
    if (ids.length === 0) return;
    await this.db.delete(notification).where(inArray(notification.id, ids));
  }

  /** Suelo: todo `scrape_run_id` por debajo o igual está resuelto y no vuelve a mirarse. */
  private async readFloor(): Promise<number> {
    const rows = await this.db.execute(
      sql`SELECT last_scrape_run_id FROM job_state WHERE job = ${JOB}`,
    );
    const row = (rows as unknown as { last_scrape_run_id: string | number }[])[0];
    return row ? Number(row.last_scrape_run_id) : 0;
  }

  /** Anota el lote en el libro. `DO NOTHING` porque dos ejecuciones solapadas verían lo mismo. */
  private async markScanned(runIds: number[]): Promise<void> {
    await this.db
      .insert(matchingScannedRun)
      .values(runIds.map((id) => ({ scrapeRunId: id })))
      .onConflictDoNothing();
  }

  /**
   * Sube el suelo por el prefijo **contiguo** ya resuelto y poda el libro por debajo.
   *
   * Un id está resuelto si está en el libro o si es una pasada commiteada que no escribió ningún
   * precio (una que falló, por ejemplo: no hay nada que evaluar en ella). Lo que **frena** el suelo
   * es un id que no tiene fila en `scrape_run`, y eso es exactamente lo que hay que respetar: o es
   * una pasada en vuelo cuya fila aún no ha commiteado —y su lote está por llegar— o es un id
   * quemado por un rollback, que no volverá. Los dos son indistinguibles desde aquí, así que se
   * elige la lectura conservadora: el suelo espera. Lo único que cuesta la lectura equivocada es que
   * el libro conserve unas filas de más.
   */
  private async advanceFloor(floor: number): Promise<number> {
    const rows = await this.db.execute(sql`
      WITH tope AS (SELECT coalesce(max(id), ${floor}) AS id FROM scrape_run),
           freno AS (
             SELECT min(g) AS id
               FROM generate_series(${floor} + 1, (SELECT id FROM tope)) g
              WHERE NOT EXISTS (SELECT 1 FROM matching_scanned_run m WHERE m.scrape_run_id = g)
                AND NOT EXISTS (
                      SELECT 1 FROM scrape_run sr
                       WHERE sr.id = g
                         AND NOT EXISTS (
                               SELECT 1 FROM price_history ph WHERE ph.scrape_run_id = g))
           )
      SELECT coalesce((SELECT id FROM freno) - 1, (SELECT id FROM tope)) AS suelo
    `);
    const nuevo = Number((rows as unknown as { suelo: string | number }[])[0].suelo);
    if (nuevo <= floor) return floor;

    await this.db.execute(sql`
      INSERT INTO job_state (job, last_scrape_run_id) VALUES (${JOB}, ${nuevo})
      ON CONFLICT (job) DO UPDATE SET last_scrape_run_id = ${nuevo}, updated_at = now()
    `);
    // El libro solo guarda lo que está por encima del suelo: por debajo ya lo dice `job_state`.
    await this.db.execute(
      sql`DELETE FROM matching_scanned_run WHERE scrape_run_id <= ${nuevo}`,
    );
    return nuevo;
  }

  /**
   * Deja constancia de que este pase llegó al final, aunque no hubiera nada que hacer.
   *
   * Sin esto `job_state` solo se escribe cuando el suelo **avanza** (`advanceFloor`), así que su
   * ausencia no distingue «no había nada que evaluar» de «no llegué a mirar». Es como el fallo del
   * matching de prod del 08/08/2026 no dejó rastro de ninguna clase (#278): el Job murió en 26 s,
   * el pod se borró con él y `job_state` seguía vacío, así que no había forma de saber si había
   * corrido alguna vez. Con el latido, «¿desde cuándo no termina el matching?» es una consulta.
   *
   * Va al **final** a propósito: lo que significa algo es que `updated_at` esté viejo. Y el
   * `DO UPDATE` no toca `last_scrape_run_id` — el suelo lo manda `advanceFloor()` y nadie más.
   *
   * Ojo con lo que esto NO cubre: si el pase muere porque no hay base a la que hablar, ninguna fila
   * en la base puede registrarlo. Esa mitad es del CronJob, que por eso pasa a `restartPolicy:
   * Never` para que el pod del intento fallido sobreviva con su log.
   */
  private async touchHeartbeat(floor: number): Promise<void> {
    await this.db.execute(sql`
      INSERT INTO job_state (job, last_scrape_run_id) VALUES (${JOB}, ${floor})
      ON CONFLICT (job) DO UPDATE SET updated_at = now()
    `);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Agrupa por usuario para mandar un único resumen por pasada. */
function groupByUser(deals: Deal[]): Map<number, Deal[]> {
  const byUser = new Map<number, Deal[]>();
  for (const deal of deals) {
    const list = byUser.get(deal.row.userId);
    if (list) list.push(deal);
    else byUser.set(deal.row.userId, [deal]);
  }
  return byUser;
}

/** Forma cruda de la query (snake_case, `numeric`/`bigint` como string). */
interface RawCandidate {
  interest_id: string | number;
  user_id: string | number;
  telegram_chat_id: string | number;
  min_discount_pct: string;
  compare_base: string;
  window_days: number;
  variant_id: string | number;
  price: string;
  list_price: string | null;
  scrape_run_id: string | number;
  size: string | null;
  color: string | null;
  product_id: string | number;
  size_canon: string | null;
  color_canon: string | null;
  product_name: string;
  product_url: string | null;
  retailer_name: string;
  recent_min: string | null;
  max_observed: string | null;
  prior_points: string | number | null;
}
