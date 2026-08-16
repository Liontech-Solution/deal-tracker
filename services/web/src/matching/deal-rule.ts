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
 * observado. Y desde #354 hay una segunda referencia, la única que no es nuestra: el mínimo de 30
 * días que C&A y Springfield publican por obligación de la Ómnibus, que entra como **techo** del PVP
 * creíble y abre una vía de acusación que no necesita esperar a `HONESTY_EVIDENCE_DAYS`.
 */

/**
 * Margen de tolerancia sobre el máximo observado antes de considerar el PVP inflado.
 *
 * Se exporta para que el espejo SQL (`deal-rule.sql.ts`) la interpole en vez de repetir el
 * literal: mientras fueron dos números, moverlo aquí y no allí compilaba, pasaba los tests y
 * cambiaba el orden de «Ofertas» sin que nadie se enterara (#375).
 */
export const INFLATED_LIST_MARGIN = 1.03;
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
  /**
   * Mínimo de los últimos 30 días que **declara la propia tienda** por obligación de la directiva
   * Ómnibus (`price_history.retailer_min_30d`, migración `0018`). Solo lo publican C&A y
   * Springfield; en las otras siete es `null` y todo se comporta como antes.
   *
   * Es el único insumo **anterior a nuestra primera pasada** que tenemos, y por eso es lo único que
   * acorta el apagón de acusaciones de #332 (ver `HONESTY_EVIDENCE_DAYS`).
   */
  retailerMin30d?: string | number | null;
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
 *
 * El mínimo declarado de 30 días entra como **techo**, nunca como referencia por sí solo (#354): si
 * la tienda declara haber vendido la prenda a 4,24 €, ninguna referencia por encima de esa cifra es
 * creíble, la publique quien la publique. No sustituye a `max_observed` —es un mínimo, y la
 * acusación se apoya en un máximo— y no puede *crear* una referencia donde no la había, porque el
 * arranque en frío se sigue resolviendo antes con `null`.
 *
 * Consecuencia que conviene tener presente: el techo solo puede **bajar** el PVP creíble, así que
 * solo puede reducir el descuento honesto, nunca inventarlo. De ahí que este cambio no pueda
 * convertir en «oferta real» nada que no lo fuera ya.
 */
export function honestListPrice(
  listPrice: string | number | null,
  maxObserved: string | number | null,
  retailerMin30d: string | number | null = null,
): number | null {
  const list = num(listPrice);
  const max = num(maxObserved);
  const min30 = num(retailerMin30d);

  // Sin nada observado antes, el precio tachado no es corroborable: no hay referencia.
  if (max === null) return null;
  // Sin precio tachado, lo que la prenda llegó a costar sí es una referencia real.
  // Tachado por encima de lo que costó nunca -> inflado; vale lo realmente observado.
  const base = list === null ? max : list > max * INFLATED_LIST_MARGIN ? max : list;

  // El techo de la propia tienda. Un `min30` de 0 o menos no hace falta descartarlo aquí: los
  // consumidores ya exigen `honest > 0` antes de medir nada contra él.
  return min30 !== null && min30 < base ? min30 : base;
}

/**
 * Descuento real en % contra el PVP creíble; 0 cuando no hay rebaja que medir contra él.
 *
 * Está fuera de `evaluateDeal` porque es **lo que ordena el catálogo**: `sort=ofertas` desempata
 * por `honestDiscountSql`, su espejo en SQL, y sin una función a la que apuntar el espejo solo
 * podía compararse contra `DealVerdict.discountPct` — que no vale, porque `evaluateDeal` lo pone a
 * 0 en cuanto falla la condición A y el `ORDER BY` no aplica esa condición. Ver #375.
 */
export function honestDiscountPct(
  price: string | number | null,
  honest: number | null,
): number {
  const p = num(price);
  if (p === null || honest === null || honest <= 0 || honest <= p) return 0;
  return round2((1 - p / honest) * 100);
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

  const honest = honestListPrice(input.listPrice, input.maxObserved, input.retailerMin30d);
  if (honest === null) return none('sin-historico');

  // Condición A: solo avisamos de mínimos nuevos, no de rebajas permanentes.
  if (input.compareBase === 'recent_min') {
    const recentMin = num(input.recentMin);
    if (recentMin === null) return none('sin-historico', honest);
    if (price >= recentMin) return none('no-es-minimo', honest);
  }

  // Condición B: la rebaja contra el PVP honesto debe alcanzar el umbral del interés.
  const discount = honestDiscountPct(price, honest);
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

/**
 * Días de histórico que hace falta cubrir para **elogiar** una bajada como «Oferta real» (#436).
 *
 * Es el simétrico de `HONESTY_EVIDENCE_DAYS`, y existe porque #332 le puso umbral a la acusación y
 * no al elogio. `real` implica `price < recent_min`, o sea que **algo bajó**; lo que no garantiza es
 * que la referencia contra la que se mide signifique nada. Con una sola observación previa, «el
 * precio más bajo de los últimos meses» es *ayer*. Medido en QA el 16/08/2026: de los 246 productos
 * con el badge, 176 (71,5 %) tenían **una sola** observación previa y los 246 menos de 90 días.
 *
 * **Por qué 14 y no 90.** No son el mismo número porque no afirman lo mismo: acusar a una tienda de
 * inflar un tachado exige haber visto la prenda *fuera* de su temporada de rebajas (de ahí los 90
 * días del calendario comercial), mientras que decir «esto ha bajado de verdad» solo exige que la
 * serie contra la que se compara no sea un único punto. Y hay un dato que decide el valor concreto:
 *
 * | umbral | de las 246 sobreviven |
 * |---|---|
 * | ≥ 3 días  | 238 |
 * | ≥ 7 días  | 212 |
 * | ≥ 14 días | **26** |
 * | ≥ 30 días | **0** |
 *
 * El histórico más largo de todo QA son 22,4 días, así que 30 apaga el elogio por completo y 7 no
 * filtra casi nada. 14 es el único valor que separa de verdad sin apagarlo.
 *
 * Un solo eje, en días, y **no** un mínimo de observaciones además: exigir `priorPoints >= 3` sobre
 * los 14 días baja de 26 productos a 13 y no compra nada que el umbral en días no dé ya. Ojo a que
 * el eje mide cosas distintas según el entorno: QA pasa semanal y prod diario, así que 14 días son
 * ~2-3 observaciones allí y ~14 aquí.
 *
 * Consecuencia asumida: lo que baja sin cobertura **no desaparece del catálogo**, cae al veredicto
 * `reciente`. Sí desaparece de «Solo ofertas reales» y de la portada, que se quedan estrictos a
 * propósito hasta que la serie madure.
 */
export const REAL_EVIDENCE_DAYS = 14;

/** Etiqueta de honestidad del descuento que consume el catálogo (tarjetas y detalle). */
export type HonestyVerdict = 'real' | 'reciente' | 'suspicious' | 'unverified' | 'none';

/**
 * Veredicto de "descuento honesto" para el catálogo, construido **sobre `evaluateDeal`** para que
 * catálogo y aviso de Telegram nunca digan cosas distintas de la misma prenda.
 *
 *  - `real`: el job avisaría (mínimo reciente con rebaja honesta contra el PVP creíble) **y**
 *    llevamos observando la prenda lo bastante como para que esa referencia signifique algo
 *    (`REAL_EVIDENCE_DAYS`).
 *  - `reciente`: exactamente la misma bajada, pero sin esa cobertura (#436). Ha bajado —eso lo
 *    sabemos— y no podemos llamarlo honesto, porque el único precio contra el que lo comparamos lo
 *    vimos anteayer. Es la simétrica de `unverified`: allí no podemos sostener una acusación, aquí
 *    no podemos sostener un elogio, y en los dos casos el catálogo dice lo que sabe en vez de
 *    afirmar de más.
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
 *
 * **Y tampoco como "ante la duda, elogia"** (#436): eso es lo que añade el corte `real`/`reciente`.
 * El veredicto que consume el **aviso de Telegram** no se mueve — `evaluateDeal` no mira
 * `trackedDays` y sigue sin mirarlo—, así que esto cambia lo que el catálogo *afirma*, no a quién
 * se avisa.
 */
export function classifyHonesty(input: DealInput): HonestyVerdict {
  // La vía declarada se resuelve antes que nada porque **no depende de nuestro histórico** (#354):
  // ver `desmentidaPorLaTienda`.
  const declarado = desmentidaPorLaTienda(input);

  // Arranque en frío: una prenda sin observaciones previas no tiene con qué corroborarse... salvo
  // que la tienda se desmienta a sí misma, que es lo único que podemos afirmar sin haberla visto.
  if (input.priorPoints <= 0) return declarado ? 'suspicious' : 'none';

  const verdict = evaluateDeal({ ...input, minDiscountPct: 0, compareBase: 'recent_min' });
  if (verdict.reason === 'sin-historico') return declarado ? 'suspicious' : 'none';
  // Ha bajado. Que además podamos llamarlo «real» depende de si la referencia contra la que se ha
  // medido significa algo, y eso lo decide la cobertura (#436). Ausente cuenta como cero, igual que
  // en la vía acusatoria: el fallo por defecto es afirmar menos, nunca más.
  if (verdict.notify) {
    return (input.trackedDays ?? 0) >= REAL_EVIDENCE_DAYS ? 'real' : 'reciente';
  }

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
  return (superaElMaximo && cobertura >= HONESTY_EVIDENCE_DAYS) || declarado
    ? 'suspicious'
    : 'unverified';
}

/**
 * La tienda se contradice con su propia voz: anuncia una rebaja **y** declara haber vendido esa
 * misma prenda más barata dentro de los últimos 30 días (#354).
 *
 * **Por qué esto no necesita `HONESTY_EVIDENCE_DAYS`, y no es una excepción a #332.** El umbral de
 * 90 días existe porque `max_observed` es *nuestra* observación y no significa nada hasta que
 * madura. Aquí no se usa nada nuestro: la evidencia es una cifra que publica la propia tienda sobre
 * un periodo ya ocurrido. Y no se le pide a la tienda que sea sincera, solo que sea **coherente** —
 * la acusación es «tu cifra A contradice tu cifra B», y para eso da igual si A es cierta.
 *
 * Por eso también vale **en arranque en frío**: la afirmación es cierta la primera vez que vemos la
 * prenda. Medido en QA el 14/08/2026, 104 de las 291 variantes que caen aquí son de arranque en frío
 * — un tercio del hallazgo se perdería exigiéndoles un histórico que la afirmación no usa.
 *
 * **Lo que NO es esta comparación.** No es «el tachado supera el mínimo declarado»: eso le pasa al
 * 98,7 % de las prendas con los dos datos (8.545 de 8.654 en QA) porque el tachado es el precio
 * inicial y el mínimo es una disclosure aparte que las dos tiendas enseñan al lado — C&A en texto
 * plano bajo el precio, Springfield en un tooltip. Acusar por ahí sería repetir #332 con otro dato.
 * Lo que delata es el **precio actual** por encima del mínimo declarado.
 *
 * Comparte `INFLATED_LIST_MARGIN` con la vía observada a propósito: un concepto, una constante.
 *
 * Nota sobre el orden en `classifyHonesty`: esto y `real` son **excluyentes por construcción**, no
 * por suerte de los datos. Si `price > min30 · margen`, el techo de `honestListPrice` deja el PVP
 * creíble en `min30 < price`, así que la condición B cae y `evaluateDeal` no puede avisar.
 */
function desmentidaPorLaTienda(input: DealInput): boolean {
  const price = num(input.price);
  const list = num(input.listPrice);
  const min30 = num(input.retailerMin30d);
  if (price === null || min30 === null) return false;
  // Sin tachado no hay rebaja anunciada, y sin rebaja anunciada no hay nada que desmentir: una
  // prenda a precio normal por encima de su mínimo de hace tres semanas es sencillamente una prenda
  // que ya no está rebajada.
  if (list === null || list <= price) return false;
  return price > min30 * INFLATED_LIST_MARGIN;
}

/** De dónde sale una acusación: de nuestro histórico o de lo que declara la tienda (#354). */
export type HonestyBasis = 'observado' | 'declarado';

/**
 * En qué se apoya el veredicto `suspicious`, para que la ficha pueda decir la verdad de cada caso en
 * vez de una sola frase que sería falsa en la mitad de ellos.
 *
 * `null` en todo lo que no sea una acusación. Cuando las dos vías se cumplen a la vez gana
 * `declarado`: es la más fuerte de las dos —no depende de cuánto llevemos mirando— y es la que el
 * texto puede sostener con una cifra concreta de la propia tienda.
 */
export function honestyBasis(input: DealInput): HonestyBasis | null {
  if (classifyHonesty(input) !== 'suspicious') return null;
  return desmentidaPorLaTienda(input) ? 'declarado' : 'observado';
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
