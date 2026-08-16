import { describe, expect, it } from 'vitest';

import { cifrasDeRebaja, llevaBadge } from './honesty';

/**
 * Qué cifras pinta la SPA y cuándo se le permite pintarlas en verde (#436).
 *
 * Los casos no son inventados: son los tres que produce `honestListPrice()` del backend, y el
 * segundo es el que la tarjeta estaba pintando mal en 88 de los 246 productos con badge de QA.
 */
describe('cifrasDeRebaja', () => {
  it('pinta lo que declara la tienda cuando su tachado ES el PVP creíble', () => {
    const r = cifrasDeRebaja({
      listPrice: '20.00',
      discountPct: '25.00',
      honestListPrice: '20.00',
      honestDiscountPct: 25,
    });
    expect(r).toEqual({ tachado: '20.00', descuento: 25, sostenido: true });
  });

  it('sustituye el tachado inflado por el creíble, y el % por el que sostiene la regla', () => {
    // El producto 10834 de Springfield, medido en QA el 16/08/2026: la tarjeta enseñaba 53,00 € y
    // -50 % cuando el máximo observado eran 31,80 € y el descuento sostenible un 16,7 %.
    const r = cifrasDeRebaja({
      listPrice: '53.00',
      discountPct: '50.00',
      honestListPrice: '31.80',
      honestDiscountPct: 16.67,
    });
    expect(r.tachado).toBe('31.80');
    expect(r.descuento).toBe(17);
    expect(r.sostenido).toBe(true);
  });

  it('sin PVP creíble enseña lo de la tienda pero NO lo avala', () => {
    // Arranque en frío: no hemos visto nunca la prenda a otro precio. El tachado se sigue
    // enseñando —el usuario lo ve igual en la web de la tienda— pero sin nuestro verde detrás.
    const r = cifrasDeRebaja({
      listPrice: '60.00',
      discountPct: '50.00',
      honestListPrice: null,
      honestDiscountPct: 0,
    });
    expect(r).toEqual({ tachado: '60.00', descuento: 50, sostenido: false });
  });

  it('no inventa porcentaje cuando el PVP creíble no sostiene ninguno', () => {
    // El techo del mínimo declarado (#354) puede dejar el PVP creíble en el precio actual o por
    // debajo: ahí no hay rebaja que pintar, aunque la tienda anuncie una.
    const r = cifrasDeRebaja({
      listPrice: '15.99',
      discountPct: '75.00',
      honestListPrice: '3.99',
      honestDiscountPct: 0,
    });
    expect(r.tachado).toBe('3.99');
    expect(r.descuento).toBeNull();
  });

  it('sin tachado declarado no hay nada que sustituir', () => {
    const r = cifrasDeRebaja({
      listPrice: null,
      discountPct: null,
      honestListPrice: '20.00',
      honestDiscountPct: 0,
    });
    expect(r).toEqual({ tachado: null, descuento: null, sostenido: true });
  });
});

describe('llevaBadge', () => {
  it('solo los veredictos que afirman algo llevan badge', () => {
    expect(llevaBadge('real')).toBe(true);
    expect(llevaBadge('suspicious')).toBe(true);
    // `unverified` es ausencia de prueba, y `none` que no hay nada que decir (#332).
    expect(llevaBadge('unverified')).toBe(false);
    expect(llevaBadge('none')).toBe(false);
  });
});
