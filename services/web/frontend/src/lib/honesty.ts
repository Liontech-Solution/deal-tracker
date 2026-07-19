/**
 * Heurística de "descuento honesto" — SUSTITUTO PROVISIONAL.
 *
 * El brief del producto quiere delatar descuentos engañosos (precio tachado inflado).
 * La versión definitiva la calculará el job de matching del backend (ver
 * services/web/README.md §"bajada significativa"). Hasta entonces clasificamos en cliente:
 *
 *  - `real`: hay rebaja y el precio actual está en (o cerca de) su mínimo reciente real.
 *  - `suspicious`: el PVP tachado está por encima de lo que la prenda ha costado nunca,
 *    o la "rebaja" es permanente (el precio actual no es un mínimo reciente).
 *  - `none`: no hay rebaja apreciable, no afirmamos nada.
 *
 * Encapsulado aquí para poder cambiarlo por el veredicto del backend sin tocar la UI.
 */
import { parseMoney } from './format';
import type { PricePoint } from '../api/types';

export type Honesty = 'real' | 'suspicious' | 'none';

const SUSPICIOUS_LIST_MARGIN = 1.03; // PVP >3% por encima del máximo histórico real = sospechoso
const HIGH_DISCOUNT = 55; // sin histórico, un % de rebaja muy alto es señal clásica de PVP inflado

/** Clasificación a partir del último dato de una variante (tarjetas del catálogo, sin histórico). */
export function honestyFromLatest(
  price: string | null,
  listPrice: string | null,
  discountPct: string | null,
): Honesty {
  const p = parseMoney(price);
  const list = parseMoney(listPrice);
  const disc = parseMoney(discountPct) ?? (p !== null && list !== null && list > 0 ? (1 - p / list) * 100 : 0);
  if (disc <= 0) return 'none';
  return disc >= HIGH_DISCOUNT ? 'suspicious' : 'real';
}

/** Clasificación con histórico completo (página de detalle). `windowDays` acota el mínimo reciente. */
export function honestyFromHistory(history: PricePoint[], windowDays = 90): Honesty {
  if (history.length === 0) return 'none';
  const last = history[history.length - 1];
  const current = parseMoney(last.price);
  const list = parseMoney(last.listPrice);
  if (current === null) return 'none';

  const cutoff = Date.now() - windowDays * 24 * 60 * 60 * 1000;
  const prices = history
    .map((h) => ({ v: parseMoney(h.price), t: Date.parse(h.scrapedAt) }))
    .filter((x): x is { v: number; t: number } => x.v !== null);

  const recent = prices.filter((x) => x.t >= cutoff).map((x) => x.v);
  const recentMin = (recent.length ? recent : prices.map((x) => x.v)).reduce(
    (m, v) => Math.min(m, v),
    Infinity,
  );
  const maxSeen = prices.map((x) => x.v).reduce((m, v) => Math.max(m, v), 0);

  const hasMarkdown = list !== null && list > current;
  if (!hasMarkdown) return 'none';

  // PVP declarado por encima de lo que jamás costó de verdad → tachado inflado.
  if (list !== null && maxSeen > 0 && list > maxSeen * SUSPICIOUS_LIST_MARGIN) return 'suspicious';
  // La "rebaja" es habitual: no estamos en un mínimo reciente.
  if (current > recentMin * 1.001) return 'suspicious';
  return 'real';
}
