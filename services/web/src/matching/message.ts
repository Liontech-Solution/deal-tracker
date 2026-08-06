import { variantLabel } from '../interests/interests.service';
import type { Deal } from './matching.types';

/**
 * Texto del aviso de Telegram. Un resumen por usuario y pasada: quien sigue diez prendas no debe
 * recibir diez notificaciones seguidas (ni chocar con el rate-limit de la Bot API).
 *
 * Ese resumen va **troceado** (#220). `sendMessage` admite 4096 caracteres y pasarse no falla de
 * forma inocente: Telegram devuelve 400, el job lo cuenta como envío fallido, la marca de agua no
 * avanza, y el lote vuelve más grande a la pasada siguiente, donde falla otra vez. Se atasca solo.
 * Medido en QA el 06/08/2026: 87 prendas eran 17 717 caracteres.
 *
 * Formato HTML (`parse_mode: 'HTML'`), que es lo que envía `TelegramApiClient`.
 */

/** Límite de `sendMessage` de la Bot API. */
export const TELEGRAM_MAX_CHARS = 4096;

/**
 * Tope de mensajes por usuario y pasada (~200 prendas detalladas). Lo que sobra se resume en la
 * cola del último trozo: veinte mensajes seguidos son spam y además chocan con el límite de ~1
 * mensaje por segundo y chat de la Bot API, que es justo lo que volvería a atascar el job.
 */
export const MAX_DIGEST_MESSAGES = 10;

/** Sitio reservado en cada trozo para su cabecera y para la cola de desbordamiento. */
const HEADER_RESERVE = 120;
const TAIL_RESERVE = 120;

/**
 * Presupuesto del cuerpo de un trozo, y el de una oferta suelta. El de la oferta descuenta también
 * la cola, porque cualquier oferta puede acabar en el trozo que la lleva: así una oferta cabe
 * siempre en un trozo, y el recorte por tope nunca deja un trozo vacío.
 */
const BODY_BUDGET = TELEGRAM_MAX_CHARS - HEADER_RESERVE;
const BLOCK_BUDGET = BODY_BUDGET - TAIL_RESERVE;

/**
 * Los textos vienen de las tiendas, así que se acotan **antes** de escapar a HTML — escapar puede
 * quintuplicar un carácter (`&` → `&amp;`), y no queremos que eso decida si el mensaje cabe.
 */
const NAME_MAX = 120;
const LABEL_MAX = 120;
const RETAILER_MAX = 80;
const URL_MAX = 300;

/** Un trozo del resumen: el texto que se manda y las ofertas de las que responde. */
export interface DigestChunk {
  /** Listo para `sendMessage`; siempre por debajo de `TELEGRAM_MAX_CHARS`. */
  text: string;
  /**
   * Ofertas que este trozo cubre: las que detalla y, en el último, las que resume en «y N más».
   * El servicio las necesita para soltar solo las reservas de lo que no se llegó a entregar.
   */
  deals: Deal[];
}

/** Bloque de texto de una oferta, con la oferta de la que salió. */
interface Bloque {
  deal: Deal;
  text: string;
}

/**
 * Trocea el resumen en mensajes que quepan en Telegram. Devuelve `[]` si no hay ofertas, y un solo
 * trozo —idéntico al mensaje de siempre— mientras el lote sea pequeño, que es el caso normal.
 */
export function buildDigestChunks(deals: Deal[]): DigestChunk[] {
  if (deals.length === 0) return [];

  const grupos = empaquetar(deals.map((deal) => ({ deal, text: dealBlock(deal) })));

  // Por encima del tope, las ofertas sobrantes no se detallan: se cuentan en la cola del último
  // trozo. Siguen siendo suyas —conservan su fila en `notification`— porque soltarlas las perdería
  // en silencio en cuanto la marca de agua avanzase.
  const sobrantes: Bloque[] = grupos.splice(MAX_DIGEST_MESSAGES).flat();
  if (sobrantes.length > 0) {
    const ultimo = grupos[grupos.length - 1];
    while (ultimo.length > 1 && largo(ultimo) > BODY_BUDGET - TAIL_RESERVE) {
      sobrantes.unshift(ultimo.pop() as Bloque);
    }
  }

  return grupos.map((grupo, i) => {
    const esUltimo = i === grupos.length - 1;
    const partes = [cabecera(deals.length, i, grupos.length), '', ...grupo.map((b) => b.text)];
    if (esUltimo && sobrantes.length > 0) partes.push('', cola(sobrantes.length));

    return {
      text: partes.join('\n'),
      deals: [...grupo, ...(esUltimo ? sobrantes : [])].map((b) => b.deal),
    };
  });
}

/** Reparte las ofertas en grupos que quepan en el cuerpo de un mensaje, sin partir ninguna. */
function empaquetar(bloques: Bloque[]): Bloque[][] {
  const grupos: Bloque[][] = [];
  let actual: Bloque[] = [];

  for (const bloque of bloques) {
    if (actual.length > 0 && largo([...actual, bloque]) > BODY_BUDGET) {
      grupos.push(actual);
      actual = [];
    }
    actual.push(bloque);
  }
  if (actual.length > 0) grupos.push(actual);

  return grupos;
}

/** Longitud del cuerpo que formarían estos bloques, contando el `\n` que los separa. */
function largo(bloques: Bloque[]): number {
  return bloques.reduce((n, b) => n + b.text.length, bloques.length - 1);
}

function cabecera(total: number, indice: number, trozos: number): string {
  const base =
    total === 1
      ? '🎉 <b>Ha bajado de precio una prenda que sigues</b>'
      : `🎉 <b>Han bajado de precio ${total} prendas que sigues</b>`;

  return trozos === 1 ? base : `${base} (${indice + 1}/${trozos})`;
}

function cola(sobrantes: number): string {
  const prendas = sobrantes === 1 ? '1 prenda más' : `${sobrantes} prendas más`;
  return `… y ${prendas}. Puedes verlas todas en la web.`;
}

/**
 * Una oferta: nombre enlazado, tienda, variante y precio con su rebaja real.
 *
 * Si con enlace no cabe —una URL desmesurada es lo único que puede desbordarla, porque el resto de
 * campos van recortados— se emite sin él. Sin enlace el bloque está acotado por construcción, así
 * que ninguna oferta puede por sí sola dejar un trozo por encima del límite.
 */
function dealBlock(deal: Deal): string {
  const conEnlace = dealLine(deal, true);
  return conEnlace.length <= BLOCK_BUDGET ? conEnlace : dealLine(deal, false);
}

function dealLine(deal: Deal, enlazar: boolean): string {
  const { row, verdict } = deal;
  const name = escapeHtml(recorta(row.productName, NAME_MAX));
  const url = row.productUrl;
  const title =
    enlazar && url && url.length <= URL_MAX ? `<a href="${escapeHtml(url)}">${name}</a>` : name;
  // La talla CANÓNICA (#223): el aviso tiene que nombrar la variante igual que la web, y la web
  // la nombra por `size_canon`. La canónica ya viene en la fila —la calcula la base en el mismo
  // SELECT de `findCandidates`, con la misma función—, así que esto no cuesta ninguna consulta.
  const label = variantLabel(row.sizeCanon, row.color);

  const parts = [`• ${title} — ${escapeHtml(recorta(row.retailerName, RETAILER_MAX))}`];
  if (label) parts.push(`  ${escapeHtml(recorta(label, LABEL_MAX))}`);

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

/** Recorta lo que se pase de largo, sin tocar lo que ya cabe (que es todo el texto real). */
function recorta(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** Los nombres de producto vienen de las tiendas: escapar antes de meterlos en HTML. */
function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
