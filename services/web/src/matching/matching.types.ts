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

  /**
   * Talla y color CANÓNICOS, y el producto y la URL a los que cuelga la variante: son las cuatro
   * piezas con las que `collapseSameGarment` decide que dos variantes son la misma prenda
   * comprable (#108). Las canónicas las calcula la base con las mismas `size_canon`/`color_canon`
   * que ya casan el interés, para no tener una segunda definición de "misma talla" en TypeScript.
   */
  productId: number;
  sizeCanon: string | null;
  colorCanon: string | null;

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
  /**
   * Suelo tras la ejecución: todo `scrape_run_id` por debajo está resuelto. **No** es el mayor id
   * del lote — se queda a propósito por debajo de un hueco en la secuencia, porque un hueco puede
   * ser una pasada que aún no ha commiteado (#240). En `dryRun` no se mueve.
   */
  watermark: number;
  candidates: number;
  /** Ofertas a avisar, ya colapsadas las caras duplicadas. */
  deals: number;
  /**
   * Caras descartadas por ser la misma prenda comprable que otra del lote (#108). Es la señal
   * barata para ver si el fenómeno crece o si lo estrena una tienda nueva.
   */
  duplicatesCollapsed: number;
  /** Avisos realmente insertados (los ya avisados no cuentan). */
  notified: number;
  usersNotified: number;
  /** Mensajes que Telegram rechazó o no se pudieron entregar. */
  failedSends: number;
}
