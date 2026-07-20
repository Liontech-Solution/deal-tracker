import type { CompareBase, DealVerdict } from './deal-rule';

/**
 * Fila candidata: un precio nuevo cruzado con un interés que lo sigue, más los estadísticos del
 * histórico de esa variante. Los importes llegan como string (`numeric` de Postgres).
 */
export interface CandidateRow {
  interestId: number;
  userId: number;
  telegramChatId: number;
  minDiscountPct: string;
  compareBase: CompareBase;
  windowDays: number;

  variantId: number;
  price: string;
  listPrice: string | null;
  scrapeRunId: number;
  size: string | null;
  color: string | null;

  productName: string;
  productUrl: string | null;
  retailerName: string;

  recentMin: string | null;
  maxObserved: string | null;
  priorPoints: number;
}

/** Candidato ya evaluado: lo que se envía y se persiste. */
export interface Deal {
  row: CandidateRow;
  verdict: DealVerdict;
  /** Clave de idempotencia del evento de precio: `<scrape_run_id>:<price>`. */
  priceEventKey: string;
  /** Id de la fila reservada en `notification`; solo tras reservar (permite soltarla si falla). */
  notificationId?: number;
}

/** Resumen de una ejecución, para el log y para los tests. */
export interface MatchingSummary {
  dryRun: boolean;
  /** Mayor `scrape_run_id` visto en el lote; 0 si no había nada nuevo. */
  watermark: number;
  candidates: number;
  deals: number;
  /** Avisos realmente insertados (los ya avisados no cuentan). */
  notified: number;
  usersNotified: number;
  /** Mensajes que Telegram rechazó o no se pudieron entregar. */
  failedSends: number;
}
