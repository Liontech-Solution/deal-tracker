import { Inject, Injectable, Logger } from '@nestjs/common';
import { inArray, sql } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { notification } from '../database/schema';
import { TelegramApiClient } from '../telegram/telegram-api.client';
import { evaluateDeal } from './deal-rule';
import { buildDigest } from './message';
import type { CandidateRow, Deal, MatchingSummary } from './matching.types';

/** Identificador del job en `job_state`. */
const JOB = 'matching';

/**
 * Evalúa los precios recién scrapeados contra los intereses de los usuarios y avisa por Telegram.
 *
 * Incremental por marca de agua: solo mira `price_history.scrape_run_id > last_scrape_run_id`.
 * Es seguro porque el scraper ingesta cada pasada en una sola transacción (nunca se ve un run a
 * medias), y guardar el mayor id procesado —en vez de "el último run"— recupera solo el hueco si
 * una ejecución se pierde.
 */
@Injectable()
export class MatchingService {
  private readonly logger = new Logger(MatchingService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly telegram: TelegramApiClient,
  ) {}

  /**
   * Ejecuta una pasada. En `dryRun` no escribe nada (ni `notification` ni marca de agua) y solo
   * registra qué habría enviado: es lo que corre en `dev`, donde no hay bot.
   */
  async run(dryRun: boolean): Promise<MatchingSummary> {
    const watermark = await this.readWatermark();
    const rows = await this.findCandidates(watermark);

    const deals: Deal[] = [];
    let maxRunId = watermark;
    for (const row of rows) {
      maxRunId = Math.max(maxRunId, row.scrapeRunId);
      const verdict = evaluateDeal(row);
      if (verdict.notify) {
        deals.push({ row, verdict, priceEventKey: `${row.scrapeRunId}:${row.price}` });
      }
    }

    this.logger.log(
      `Lote desde scrape_run > ${watermark}: ${rows.length} candidato(s), ${deals.length} oferta(s)`,
    );

    const summary: MatchingSummary = {
      dryRun,
      watermark: maxRunId,
      candidates: rows.length,
      deals: deals.length,
      notified: 0,
      usersNotified: 0,
      failedSends: 0,
    };

    for (const [userId, userDeals] of groupByUser(deals)) {
      if (dryRun) {
        this.logger.log(`[dry-run] usuario ${userId}: ${userDeals.length} aviso(s)\n${buildDigest(userDeals)}`);
        summary.notified += userDeals.length;
        summary.usersNotified += 1;
        continue;
      }

      // Reservar ANTES de enviar: si el proceso muere tras el envío, el reintento no duplica.
      // Lo ya avisado choca contra el UNIQUE y no vuelve a salir.
      const fresh = await this.reserve(userDeals);
      if (fresh.length === 0) continue;

      const sent = await this.telegram.sendMessage(fresh[0].row.telegramChatId, buildDigest(fresh));
      if (sent) {
        summary.notified += fresh.length;
        summary.usersNotified += 1;
        continue;
      }

      // Envío fallido: soltar la reserva para que el siguiente intento vuelva a evaluarlo. Si no,
      // la fila quedaría marcada como avisada y el usuario **nunca** se enteraría de esta bajada.
      // Puede duplicar un aviso solo si Telegram lo entregó pero no llegamos a ver su respuesta;
      // un duplicado ocasional es preferible a un silencio permanente.
      await this.release(fresh);
      summary.failedSends += 1;
      this.logger.error(`No se pudo entregar el aviso al usuario ${userId}; se reintentará`);
    }

    // La marca de agua solo avanza si todo se entregó: si no, el próximo intento (o el reintento
    // del Job de k8s) reprocesa el lote. Los ya avisados están protegidos por su fila.
    if (!dryRun && maxRunId > watermark && summary.failedSends === 0) {
      await this.saveWatermark(maxRunId);
    }

    this.logger.log(
      `Fin${dryRun ? ' (dry-run, sin cambios)' : ''}: ${summary.notified} aviso(s) para ` +
        `${summary.usersNotified} usuario(s), marca de agua ${summary.watermark}`,
    );
    return summary;
  }

  /**
   * Precios nuevos × intereses que los siguen × estadísticos del histórico de la variante.
   *
   * El patrón `(i.x IS NULL OR i.x = b.x)` es el filtro parcial del catálogo invertido: aquí el
   * criterio sale de la fila `interest` (NULL = "cualquiera"). El JOIN a `app_user` descarta a
   * quien no tiene Telegram: sin canal no hay aviso, y tampoco se quema el evento en `notification`.
   */
  private async findCandidates(watermark: number): Promise<CandidateRow[]> {
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
        WHERE ph.scrape_run_id > ${watermark} AND ph.in_stock
          -- Foco barefoot (#30): no se avisa de calzado que no sea respetuoso. Un aviso es más
          -- intrusivo que una tarjeta del catálogo — llega solo al móvil de alguien — así que
          -- mandar ahí lo que el catálogo esconde sería la peor versión del mismo error.
          -- IS DISTINCT FROM y no <>: con section NULL, <> daría NULL y tiraría la fila.
          AND (p.section IS DISTINCT FROM 'zapateria' OR p.barefoot = 'si')
      )
      SELECT i.id AS interest_id, i.user_id, i.min_discount_pct, i.compare_base, i.window_days,
             u.telegram_chat_id,
             b.variant_id, b.price, b.list_price, b.scrape_run_id, b.size, b.color,
             b.product_name, b.product_url, b.retailer_name,
             st.recent_min, st.max_observed, st.prior_points
      FROM batch b
      JOIN interest i ON i.active
        AND (i.variant_id  IS NULL OR i.variant_id  = b.variant_id)
        AND (i.product_id  IS NULL OR i.product_id  = b.product_id)
        AND (i.retailer_id IS NULL OR i.retailer_id = b.retailer_id)
        AND (i.gender   IS NULL OR i.gender   = b.gender)
        AND (i.section  IS NULL OR i.section  = b.section)
        AND (i.category IS NULL OR i.category = b.category)
        -- La talla se compara CANÓNICA (#43). Con igualdad de texto crudo, un interés guardado con
        -- '26' —la talla que ofrece el filtro— nunca casaba con un zapato de Zara almacenado como
        -- '26 (16,3 cm)', y el aviso no fallaba ruidosamente: no llegaba, y nadie se enteraba.
        -- Se normalizan los dos lados aunque el alta ya guarde canónico: size_canon es idempotente
        -- y así una fila escrita a mano en la base tampoco se queda sin avisar.
        AND (i.size     IS NULL OR size_canon(i.size) = size_canon(b.size))
        AND (i.color    IS NULL OR i.color    = b.color)
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

  private async readWatermark(): Promise<number> {
    const rows = await this.db.execute(
      sql`SELECT last_scrape_run_id FROM job_state WHERE job = ${JOB}`,
    );
    const row = (rows as unknown as { last_scrape_run_id: string | number }[])[0];
    return row ? Number(row.last_scrape_run_id) : 0;
  }

  private async saveWatermark(runId: number): Promise<void> {
    await this.db.execute(sql`
      INSERT INTO job_state (job, last_scrape_run_id) VALUES (${JOB}, ${runId})
      ON CONFLICT (job) DO UPDATE SET last_scrape_run_id = ${runId}, updated_at = now()
    `);
  }
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
  product_name: string;
  product_url: string | null;
  retailer_name: string;
  recent_min: string | null;
  max_observed: string | null;
  prior_points: string | number | null;
}
