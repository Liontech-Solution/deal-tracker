import { variantLabel } from '../interests/interests.service';
import type { Deal } from './matching.types';

/**
 * Texto del aviso de Telegram. Un solo mensaje por usuario y pasada: quien sigue diez prendas no
 * debe recibir diez notificaciones seguidas (ni chocar con el rate-limit de la Bot API).
 *
 * Formato HTML (`parse_mode: 'HTML'`), que es lo que envía `TelegramApiClient`.
 */
export function buildDigest(deals: Deal[]): string {
  const header =
    deals.length === 1
      ? '🎉 <b>Ha bajado de precio una prenda que sigues</b>'
      : `🎉 <b>Han bajado de precio ${deals.length} prendas que sigues</b>`;

  return [header, '', ...deals.map(dealLine)].join('\n');
}

/** Una oferta: nombre enlazado, tienda, variante y precio con su rebaja real. */
function dealLine(deal: Deal): string {
  const { row, verdict } = deal;
  const name = escapeHtml(row.productName);
  const title = row.productUrl ? `<a href="${escapeHtml(row.productUrl)}">${name}</a>` : name;
  const label = variantLabel(row.size, row.color);

  const parts = [`• ${title} — ${escapeHtml(row.retailerName)}`];
  if (label) parts.push(`  ${escapeHtml(label)}`);

  // El PVP mostrado es el honesto (el que resistió la comprobación contra el histórico), no el
  // tachado de la tienda: es la cifra que podemos defender.
  const before = verdict.honestListPrice !== null ? ` (antes ${money(verdict.honestListPrice)})` : '';
  parts.push(`  <b>${money(Number(row.price))}</b>${before} · <b>-${round(verdict.discountPct)}%</b>`);

  return parts.join('\n');
}

function money(value: number): string {
  return `${value.toFixed(2).replace('.', ',')} €`;
}

function round(pct: number): string {
  return String(Math.round(pct));
}

/** Los nombres de producto vienen de las tiendas: escapar antes de meterlos en HTML. */
function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
