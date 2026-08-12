/**
 * Regla de "bajada significativa" — el criterio único del producto.
 *
 * Función **pura** y sin dependencias: la usa el job de matching para decidir a quién avisar, y
 * la reutilizará el catálogo para etiquetar "oferta real vs precio inflado". Que sean el mismo
 * código es deliberado: si el catálogo dijera una cosa y el aviso otra, perderíamos la confianza
 * del usuario, que es lo único que este producto vende.
 *
 * Regla (ver README §"Regla de bajada significativa"):
 *   A) el precio es un **mínimo nuevo** dentro de `windowDays`   (solo si compareBase='recent_min')
 *   B) el descuento contra el **PVP honesto** alcanza `minDiscountPct`
 *
 * El PVP honesto nunca es el precio tachado a ciegas: si la tienda declara un PVP por encima de
 * lo que la prenda ha costado jamás, ese tachado está inflado y se usa el máximo realmente
 * observado.
 */

/** Margen de tolerancia sobre el máximo observado antes de considerar el PVP inflado. */
const INFLATED_LIST_MARGIN = 1.03;
/** Coma flotante: un 20,000000001 % debe contar como 20 %. */
const EPSILON = 1e-9;

export type CompareBase = 'recent_min' | 'list_price';

/** Por qué se avisó o, sobre todo, por qué no. Alimenta el log del dry-run y los tests. */
export type DealReason =
  | 'ok'
  | 'sin-historico'
  | 'no-es-minimo'
  | 'sin-rebaja'
  | 'descuento-insuficiente';

export interface DealInput {
  /** Precio nuevo observado. */
  price: string | number | null;
  /** Precio tachado que declara la tienda. Puede estar inflado; puede faltar. */
  listPrice: string | number | null;
  /** Mínimo de las observaciones **anteriores** dentro de la ventana del interés. */
  recentMin: string | number | null;
  /** Máximo de las observaciones **anteriores** (todo el histórico de la variante). */
  maxObserved: string | number | null;
  /** Cuántas observaciones anteriores hay. 0 = producto recién descubierto. */
  priorPoints: number;
  /**
   * Días que llevamos observando la variante (de la primera observación a la última). Lo consume
   * **solo `classifyHonesty`**, para decidir si nuestro histórico da para desmentir un tachado;
   * `evaluateDeal` no lo mira, así que el aviso de Telegram se comporta igual esté o no.
   *
   * Ausente o `null` cuenta como **cero evidencia**: el fallo por defecto es callarse, nunca acusar.
   */
  trackedDays?: number | null;
  minDiscountPct: string | number;
  compareBase: CompareBase;
}

export interface DealVerdict {
  notify: boolean;
  reason: DealReason;
  /** Referencia contra la que se midió el descuento; `null` si no hay ninguna creíble. */
  honestListPrice: number | null;
  /** Descuento real en %, redondeado a 2 decimales. */
  discountPct: number;
}

/**
 * PVP creíble contra el que medir una rebaja.
 *
 * Devuelve `null` cuando no hay ninguno: sin histórico **no se cae de vuelta al precio tachado
 * de la tienda**. Ese es justo el caso que delatamos, y afirmar un "-60 %" que no podemos
 * corroborar sería repetir el engaño con nuestra voz.
 */
export function honestListPrice(
  listPrice: string | number | null,
  maxObserved: string | number | null,
): number | null {
  const list = num(listPrice);
  const max = num(maxObserved);

  // Sin nada observado antes, el precio tachado no es corroborable: no hay referencia.
  if (max === null) return null;
  // Sin precio tachado, lo que la prenda llegó a costar sí es una referencia real.
  if (list === null) return max;
  // Tachado por encima de lo que costó nunca -> inflado; vale lo realmente observado.
  return list > max * INFLATED_LIST_MARGIN ? max : list;
}

/** Evalúa un precio nuevo contra los parámetros de un interés. */
export function evaluateDeal(input: DealInput): DealVerdict {
  const price = num(input.price);
  const minDiscount = num(input.minDiscountPct) ?? 0;

  const none = (reason: DealReason, honest: number | null = null, discount = 0): DealVerdict => ({
    notify: false,
    reason,
    honestListPrice: honest,
    discountPct: discount,
  });

  if (price === null) return none('sin-rebaja');

  // Arranque en frío: una prenda descubierta ya rebajada no tiene con qué corroborarse.
  // Guarda explícita además de la que impone `honestListPrice`, para que un refactor no la pierda.
  if (input.priorPoints <= 0) return none('sin-historico');

  const honest = honestListPrice(input.listPrice, input.maxObserved);
  if (honest === null) return none('sin-historico');

  // Condición A: solo avisamos de mínimos nuevos, no de rebajas permanentes.
  if (input.compareBase === 'recent_min') {
    const recentMin = num(input.recentMin);
    if (recentMin === null) return none('sin-historico', honest);
    if (price >= recentMin) return none('no-es-minimo', honest);
  }

  // Condición B: la rebaja contra el PVP honesto debe alcanzar el umbral del interés.
  const discount = honest > 0 && honest > price ? round2((1 - price / honest) * 100) : 0;
  if (discount <= 0) return none('sin-rebaja', honest);
  if (discount + EPSILON < minDiscount) return none('descuento-insuficiente', honest, discount);

  return { notify: true, reason: 'ok', honestListPrice: honest, discountPct: discount };
}

/**
 * Ventana del "mínimo reciente" para el veredicto del **catálogo** (donde no hay un interés que
 * fije `window_days`). El detalle mostraba "el precio más bajo de los últimos meses" → 90 días.
 */
export const HONESTY_WINDOW_DAYS = 90;

/**
 * Días de histórico que hace falta cubrir para **acusar** a una tienda de inflar un tachado (#332).
 *
 * Por qué existe: `max_observed` no es "lo que la prenda ha costado jamás", es "lo más caro que la
 * hemos visto **desde que la descubrimos**". Una prenda descubierta ya rebajada tiene por máximo su
 * propio precio de rebaja, así que a la segunda pasada la regla concluía que el tachado estaba
 * hinchado y acusaba. Medido el 13/08/2026 en prod: 15.928 acusaciones apoyadas en una media de
 * **2,27 días** de observación.
 *
 * Por qué 90 y no otro número. Nuestra propia serie no puede calibrarlo —el histórico más largo de
 * cualquier variante del proyecto era de 17,3 días, y las subidas de precio, el único suceso capaz
 * de sustentar una acusación, le pasan al 0,03-0,1 % de las variantes—, así que el criterio es el
 * **calendario comercial**: las rebajas corren ~2 meses (enero-febrero y julio-agosto), luego para
 * haber visto la prenda *fuera* de su rebaja hay que superar los 60 días. Con 90 los dos extremos
 * de la ventana no pueden caer en la misma temporada (7 ene + 90 = 7 abr; 1 jul + 90 = 29 sep), y
 * eso hace innecesario un mínimo de observaciones aparte.
 *
 * Es a propósito `HONESTY_WINDOW_DAYS`: la misma ventana sobre la que se calcula `recent_min` y la
 * que la ficha promete cuando dice "el precio más bajo de los últimos meses". Un concepto, una
 * constante.
 *
 * Consecuencia asumida: hasta que la serie madure (prod ~05/11/2026) no habrá ni una sola
 * acusación. Es el estado honesto — hoy no sabemos si esos tachados están hinchados.
 */
export const HONESTY_EVIDENCE_DAYS = HONESTY_WINDOW_DAYS;

/** Etiqueta de honestidad del descuento que consume el catálogo (tarjetas y detalle). */
export type HonestyVerdict = 'real' | 'suspicious' | 'unverified' | 'none';

/**
 * Veredicto de "descuento honesto" para el catálogo, construido **sobre `evaluateDeal`** para que
 * catálogo y aviso de Telegram nunca digan cosas distintas de la misma prenda.
 *
 *  - `real`: el job avisaría (mínimo reciente con rebaja honesta contra el PVP creíble).
 *  - `suspicious`: la tienda muestra un tachado que **podemos desmentir** — está por encima del
 *    máximo que hemos observado y llevamos siguiendo la prenda lo bastante como para que ese
 *    máximo signifique algo (`HONESTY_EVIDENCE_DAYS`).
 *  - `unverified`: hay tachado y no es una bajada real, pero **no podemos corroborar nada**: o el
 *    tachado es creíble contra lo que hemos visto (y lo único que pasa es que el precio no es un
 *    mínimo nuevo), o no llevamos suficiente tiempo mirando. No es una acusación.
 *  - `none`: no hay tachado, o no hay histórico con el que corroborar nada (arranque en frío).
 *
 * La asimetría entre `suspicious` y `unverified` es deliberada y es el fondo de #332: el aviso de
 * Telegram, ante la duda, **calla**, y eso está bien porque el coste es un aviso perdido; el
 * catálogo no puede reutilizar ese "ante la duda" como si fuera "ante la duda, acusa".
 */
export function classifyHonesty(input: DealInput): HonestyVerdict {
  // Arranque en frío: una prenda sin observaciones previas no tiene con qué corroborarse.
  if (input.priorPoints <= 0) return 'none';

  const verdict = evaluateDeal({ ...input, minDiscountPct: 0, compareBase: 'recent_min' });
  if (verdict.reason === 'sin-historico') return 'none';
  if (verdict.notify) return 'real';

  // No es una bajada real. Si la tienda no enseña tachado, no hay nada que juzgar.
  const price = num(input.price);
  const list = num(input.listPrice);
  if (price === null || list === null || list <= price) return 'none';

  // Hay tachado y no es una bajada real. Para llamarlo inflado hacen falta las dos cosas: que el
  // tachado supere lo que la prenda ha llegado a costar —misma comparación que `honestListPrice`,
  // misma constante— y que ese máximo se apoye en histórico suficiente para significar algo.
  const max = num(input.maxObserved);
  const superaElMaximo = max !== null && list > max * INFLATED_LIST_MARGIN;
  const cobertura = input.trackedDays ?? 0;
  return superaElMaximo && cobertura >= HONESTY_EVIDENCE_DAYS ? 'suspicious' : 'unverified';
}

/** `numeric` de Postgres viaja como string: parsear siempre, nunca comparar lexicográficamente. */
function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
