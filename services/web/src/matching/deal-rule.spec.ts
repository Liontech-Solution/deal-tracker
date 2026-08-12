import { describe, expect, it } from 'vitest';

import { classifyHonesty, evaluateDeal, honestListPrice, HONESTY_EVIDENCE_DAYS } from './deal-rule';
import type { DealInput } from './deal-rule';

/**
 * La regla de aviso es el criterio que sostiene la promesa del producto, así que se prueba
 * exhaustivamente y en aislamiento. Los importes van como **string**, tal y como llegan de
 * Postgres (`numeric`), para que un fallo de parseo salte aquí.
 */

/** Caso base: la prenda costó 39,99 €, su mínimo reciente fue 24 € y ahora está a 19,99 €. */
function deal(overrides: Partial<DealInput> = {}): DealInput {
  return {
    price: '19.99',
    listPrice: '39.99',
    recentMin: '24.00',
    maxObserved: '39.99',
    priorPoints: 5,
    minDiscountPct: '20',
    compareBase: 'recent_min',
    ...overrides,
  };
}

describe('arranque en frío · no amplificar el engaño de la tienda', () => {
  it('producto recién descubierto ya rebajado: NO avisa aunque el PVP diga -60%', () => {
    // Solo se ha visto una vez, a 19,99 €, con un tachado de 49,99 €. No hay nada que corrobore
    // ese PVP: avisar sería repetir el engaño con nuestra voz.
    const verdict = evaluateDeal(
      deal({ priorPoints: 0, recentMin: null, maxObserved: null, listPrice: '49.99' }),
    );

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('sin-historico');
    expect(verdict.honestListPrice).toBeNull();
  });

  it('producto recién descubierto sin precio tachado: tampoco avisa', () => {
    const verdict = evaluateDeal(
      deal({ priorPoints: 0, recentMin: null, maxObserved: null, listPrice: null }),
    );

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('sin-historico');
  });

  it('con una sola observación previa ya puede avisar, pero medido contra ella', () => {
    // Se vio una vez a 39,99 € y ahora está a 19,99 €: la bajada es demostrable.
    const verdict = evaluateDeal(
      deal({ priorPoints: 1, recentMin: '39.99', maxObserved: '39.99', listPrice: '99.99' }),
    );

    expect(verdict.notify).toBe(true);
    // El tachado de 99,99 € se descarta: nunca costó eso.
    expect(verdict.honestListPrice).toBe(39.99);
    expect(verdict.discountPct).toBe(50.01);
  });

  it('nunca cae de vuelta al precio tachado cuando no hay histórico', () => {
    expect(honestListPrice('49.99', null)).toBeNull();
  });
});

describe('PVP honesto', () => {
  it('tachado por encima de lo que costó nunca -> se usa el máximo observado', () => {
    expect(honestListPrice('59.99', '39.99')).toBe(39.99);
  });

  it('tachado creíble -> se respeta', () => {
    expect(honestListPrice('39.99', '39.99')).toBe(39.99);
  });

  it('tolera un 3% de margen sobre el máximo observado (redondeos de la tienda)', () => {
    expect(honestListPrice('41.00', '39.99')).toBe(41); // 41 <= 39.99 * 1.03
    expect(honestListPrice('41.50', '39.99')).toBe(39.99); // se pasa del margen
  });

  it('sin tachado, el máximo observado es referencia válida', () => {
    expect(honestListPrice(null, '39.99')).toBe(39.99);
  });
});

describe('condición A · mínimo nuevo (compare_base = recent_min)', () => {
  it('mínimo nuevo con descuento suficiente -> avisa', () => {
    const verdict = evaluateDeal(deal());

    expect(verdict.notify).toBe(true);
    expect(verdict.reason).toBe('ok');
    expect(verdict.discountPct).toBe(50.01);
  });

  it('rebaja permanente (no es mínimo nuevo) -> silencio', () => {
    // 30 € es un buen descuento sobre 39,99 €, pero ya estuvo a 24 €: no es noticia.
    const verdict = evaluateDeal(deal({ price: '30.00' }));

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('no-es-minimo');
  });

  it('empatar el mínimo reciente no basta: tiene que bajarlo', () => {
    const verdict = evaluateDeal(deal({ price: '24.00' }));

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('no-es-minimo');
  });
});

describe('condición B · umbral de descuento', () => {
  it('descuento por debajo del umbral del interés -> silencio', () => {
    // 23,99 € sobre 39,99 € es un 40%, pero este usuario pidió 45%.
    const verdict = evaluateDeal(deal({ price: '23.99', minDiscountPct: '45' }));

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('descuento-insuficiente');
    expect(verdict.discountPct).toBe(40.01);
  });

  it('justo en el umbral -> avisa (comparación >=, sin perder por coma flotante)', () => {
    // 20 € sobre 40 € es exactamente el 50%.
    const verdict = evaluateDeal(
      deal({ price: '20.00', listPrice: '40.00', maxObserved: '40.00', minDiscountPct: '50' }),
    );

    expect(verdict.notify).toBe(true);
    expect(verdict.discountPct).toBe(50);
  });

  it('el umbral se mide contra el PVP honesto, no contra el tachado inflado', () => {
    // La tienda dice 99,99 € (-80%), pero nunca pasó de 39,99 €: el descuento real es ~50%.
    const verdict = evaluateDeal(deal({ listPrice: '99.99', minDiscountPct: '70' }));

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('descuento-insuficiente');
    expect(verdict.discountPct).toBe(50.01);
  });

  it('sin rebaja real -> silencio', () => {
    const verdict = evaluateDeal(
      deal({ price: '19.99', listPrice: '19.99', maxObserved: '19.99', recentMin: '24.00' }),
    );

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('sin-rebaja');
  });
});

describe('compare_base = list_price', () => {
  it('ignora la condición A: avisa aunque no sea mínimo nuevo', () => {
    const verdict = evaluateDeal(deal({ price: '30.00', compareBase: 'list_price' }));

    expect(verdict.notify).toBe(true);
    expect(verdict.discountPct).toBe(24.98);
  });

  it('mantiene la guarda del PVP honesto', () => {
    const verdict = evaluateDeal(
      deal({ price: '30.00', listPrice: '99.99', compareBase: 'list_price', minDiscountPct: '50' }),
    );

    expect(verdict.notify).toBe(false);
    expect(verdict.honestListPrice).toBe(39.99);
  });

  it('sigue sin avisar en arranque en frío', () => {
    const verdict = evaluateDeal(
      deal({ priorPoints: 0, maxObserved: null, recentMin: null, compareBase: 'list_price' }),
    );

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('sin-historico');
  });
});

describe('classifyHonesty · veredicto del catálogo (misma regla que el aviso)', () => {
  it('mínimo reciente con rebaja honesta -> real', () => {
    expect(classifyHonesty(deal())).toBe('real');
  });

  it('arranque en frío (sin observaciones previas) -> none, aunque el PVP grite -60%', () => {
    expect(
      classifyHonesty(deal({ priorPoints: 0, recentMin: null, maxObserved: null, listPrice: '49.99' })),
    ).toBe('none');
  });

  it('tachado inflado sobre el máximo observado, con histórico que lo sostiene -> suspicious', () => {
    // Está a 30 € (no es mínimo nuevo: llegó a 24 €) y la tienda enseña un tachado de 99,99 €.
    // Llevamos 120 días mirándola, así que "nunca ha costado 99,99 €" es una afirmación nuestra.
    expect(
      classifyHonesty(deal({ price: '30.00', listPrice: '99.99', trackedDays: 120 })),
    ).toBe('suspicious');
  });

  it('rebaja permanente con tachado creíble (no es mínimo reciente) -> unverified, no suspicious', () => {
    // CAMBIO DELIBERADO DE EXPECTATIVA (#332). Antes esto se etiquetaba `suspicious` y la ficha
    // decía «el precio tachado está inflado respecto a su historial». Es falso: el tachado de
    // 39,99 € coincide con el máximo que hemos observado, o sea que es CREÍBLE. Lo único cierto es
    // que 30 € no es un mínimo nuevo — y eso no es un precio inflado.
    expect(classifyHonesty(deal({ price: '30.00', trackedDays: 400 }))).toBe('unverified');
  });

  it('sin tachado y sin rebaja real -> none', () => {
    expect(
      classifyHonesty(
        deal({ price: '30.00', listPrice: '30.00', maxObserved: '39.99', recentMin: '24.00' }),
      ),
    ).toBe('none');
  });

  it('el umbral del catálogo es 0%: cualquier mínimo nuevo honesto cuenta como real', () => {
    // 23,99 € sobre 39,99 € es solo un ~40%, insuficiente para un interés que pida 45%,
    // pero para el badge del catálogo (umbral 0) es una oferta real.
    expect(classifyHonesty(deal({ price: '23.99', minDiscountPct: '45' }))).toBe('real');
  });
});

/**
 * #332. `max_observed` no es "lo que la prenda ha costado jamás", es "lo más caro que la hemos
 * visto desde que la descubrimos". En una prenda descubierta **ya rebajada** las dos cosas no
 * coinciden, y la regla anterior las confundía: bastaba una segunda pasada para acusar a la tienda
 * de inflar el tachado. Medido en prod el 13/08/2026: 15.928 acusaciones sobre una media de 2,27
 * días de observación.
 */
describe('classifyHonesty · no acusar sin poder desmentir (#332)', () => {
  /** Prenda descubierta ya rebajada: lo más caro que la hemos visto es su propio precio actual. */
  function descubiertaRebajada(overrides: Partial<DealInput> = {}): DealInput {
    return deal({
      price: '3.99',
      listPrice: '17.99',
      recentMin: '3.99',
      maxObserved: '3.99',
      priorPoints: 1,
      trackedDays: 0,
      ...overrides,
    });
  }

  it('max_observed == price con UNA sola observación previa -> unverified', () => {
    // El caso real de la issue: Sfera 224, «Top cruzado», descubierto el 21/07 ya a 3,99 € con un
    // tachado de 17,99 €. Dos pasadas del mismo día bastaban para llamarlo fraude.
    expect(classifyHonesty(descubiertaRebajada())).toBe('unverified');
  });

  it('max_observed == price con MUCHAS observaciones previas -> unverified', () => {
    // Los puntos no son evidencia por sí solos: mil pasadas en una semana siguen sin decirnos qué
    // costaba la prenda antes de la rebaja. Por eso el umbral es de días cubiertos, no de puntos.
    expect(classifyHonesty(descubiertaRebajada({ priorPoints: 500, trackedDays: 7 }))).toBe(
      'unverified',
    );
  });

  it('tachado por encima del máximo pero con poco recorrido -> unverified', () => {
    // Aquí SÍ la hemos visto más cara (5 € > 3,99 €) y el tachado los supera, pero llevamos 30
    // días: no cubre una temporada de rebajas, así que "nunca costó 17,99 €" aún no es nuestro.
    expect(
      classifyHonesty(descubiertaRebajada({ maxObserved: '5.00', trackedDays: 30 })),
    ).toBe('unverified');
  });

  it('el mismo caso con histórico suficiente -> suspicious', () => {
    expect(
      classifyHonesty(descubiertaRebajada({ maxObserved: '5.00', trackedDays: 90 })),
    ).toBe('suspicious');
  });

  it('justo por debajo del umbral no acusa, y justo en el umbral sí', () => {
    const caso = (dias: number) =>
      classifyHonesty(descubiertaRebajada({ maxObserved: '5.00', trackedDays: dias }));

    expect(caso(HONESTY_EVIDENCE_DAYS - 1)).toBe('unverified');
    expect(caso(HONESTY_EVIDENCE_DAYS)).toBe('suspicious');
  });

  it('sin trackedDays cuenta como cero evidencia: nunca acusa', () => {
    // El defecto conservador. Si un llamante nuevo olvida pasar el dato, el fallo es callarse.
    const sinDato = descubiertaRebajada({ maxObserved: '5.00' });
    delete sinDato.trackedDays;

    expect(classifyHonesty(sinDato)).toBe('unverified');
    expect(classifyHonesty({ ...sinDato, trackedDays: null })).toBe('unverified');
  });

  it('el margen del 3% se respeta igual al acusar', () => {
    // Un tachado dentro del margen de tolerancia sobre el máximo no es un tachado inflado, por
    // mucho histórico que haya: misma INFLATED_LIST_MARGIN que honestListPrice.
    const conMargen = (list: string) =>
      classifyHonesty(descubiertaRebajada({ price: '4.00', maxObserved: '10.00', listPrice: list, trackedDays: 400 }));

    expect(conMargen('10.30')).toBe('unverified'); // exactamente 10 * 1,03
    expect(conMargen('10.31')).toBe('suspicious');
  });

  it('el veredicto `real` no se mueve por el umbral, ni siquiera con cero días', () => {
    // La garantía que hace seguro este cambio: `real` ya implica max_observed > price, así que el
    // umbral no puede tocarlo. Si esto rompe, es que alguien movió la regla del aviso.
    expect(classifyHonesty(deal({ trackedDays: 0 }))).toBe('real');
    expect(classifyHonesty(deal({ trackedDays: 1000 }))).toBe('real');
  });

  it('no acusar NO afloja el aviso de Telegram: evaluateDeal sigue callándose igual', () => {
    // La asimetría del producto: "ante la duda, callar" para el aviso; "ante la duda, NO acusar"
    // para el catálogo. Son la misma prudencia, no dos reglas distintas.
    const verdict = evaluateDeal({ ...descubiertaRebajada(), minDiscountPct: '20' });

    expect(verdict.notify).toBe(false);
    expect(verdict.reason).toBe('no-es-minimo');
  });
});

describe('robustez de tipos', () => {
  it('acepta números además de los strings de Postgres', () => {
    const verdict = evaluateDeal(
      deal({ price: 19.99, listPrice: 39.99, recentMin: 24, maxObserved: 39.99, minDiscountPct: 20 }),
    );

    expect(verdict.notify).toBe(true);
  });

  it('precio nulo o ilegible -> silencio, sin lanzar', () => {
    expect(evaluateDeal(deal({ price: null })).notify).toBe(false);
    expect(evaluateDeal(deal({ price: 'no-es-un-numero' })).notify).toBe(false);
  });
});
