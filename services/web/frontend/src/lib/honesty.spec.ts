import { describe, expect, it } from 'vitest';

import type { Honesty } from '../api/types';
import { cifrasDeRebaja, llevaBadge, tonoDelDescuento, tonoDelPrecio } from './honesty';

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

/**
 * De qué color sale el `-X %`, que es donde vivía la divergencia de #473.
 *
 * La tabla se recorre entera y a propósito: la condición estaba duplicada en la tarjeta y en la
 * ficha, y de las diez combinaciones las dos superficies discrepaban en tres. Un test por caso
 * suelto no habría cazado ninguna de las dos que nadie estaba buscando.
 */
describe('tonoDelDescuento', () => {
  const CASOS: [Honesty, boolean, ReturnType<typeof tonoDelDescuento>][] = [
    // El único verde del catálogo: bajada con cobertura y con PVP creíble detrás.
    ['real', true, 'good'],
    ['real', false, 'neutro'],
    // La acusación no depende de tener PVP creíble propio: la vía declarada de #354 acusa en la
    // primera pasada, cuando `sostenido` es todavía falso.
    ['suspicious', true, 'warn'],
    ['suspicious', false, 'warn'],
    // Las dos formas de «no lo podemos sostener», una por el lado del elogio y otra por el de la
    // acusación (#436 y #332). Las dos en neutro, aunque `reciente` sea una buena noticia.
    ['reciente', true, 'neutro'],
    ['reciente', false, 'neutro'],
    ['unverified', true, 'neutro'],
    ['unverified', false, 'neutro'],
    ['none', true, 'neutro'],
    ['none', false, 'neutro'],
  ];

  it.each(CASOS)('%s con sostenido=%s se pinta %s', (honesty, sostenido, esperado) => {
    expect(tonoDelDescuento(honesty, sostenido)).toBe(esperado);
  });

  it('el verde es SOLO de `real`, y `sostenido` no basta para ganarlo (#473)', () => {
    // La regresión exacta: `sostenido` es cierto en toda bajada, así que decidir el verde con él
    // pintaba de verde a `reciente` —553 de los 800 productos de QA— y a un `unverified` cuyo
    // tachado no habíamos podido ni confirmar ni desmentir (otros 228).
    const verdes = (['real', 'reciente', 'suspicious', 'unverified', 'none'] as Honesty[]).filter(
      (h) => tonoDelDescuento(h, true) === 'good',
    );
    expect(verdes).toEqual(['real']);
  });

  it('un `suspicious` sin PVP creíble sigue en ámbar, no en gris (#354)', () => {
    // La divergencia que iba al revés: la ficha resolvía `!sostenido` antes que la acusación y le
    // pintaba el porcentaje en gris debajo de su propio badge «Precio inflado».
    expect(tonoDelDescuento('suspicious', false)).toBe('warn');
  });
});

describe('tonoDelPrecio', () => {
  it('el acento es el color por defecto y solo lo pierde la acusación', () => {
    // No es una afirmación, es el color de un precio: `none` —donde no decimos nada— lo lleva. Por
    // eso la ficha apagándoselo solo a `unverified` no distinguía nada, y la tarjeta no lo hacía.
    expect(tonoDelPrecio('suspicious')).toBe('plano');
    for (const h of ['real', 'reciente', 'unverified', 'none'] as Honesty[]) {
      expect(tonoDelPrecio(h)).toBe('accent');
    }
  });
});

describe('llevaBadge', () => {
  it('solo los veredictos que afirman algo llevan badge', () => {
    expect(llevaBadge('real')).toBe(true);
    expect(llevaBadge('reciente')).toBe(true);
    expect(llevaBadge('suspicious')).toBe(true);
    // `unverified` es ausencia de prueba, y `none` que no hay nada que decir (#332).
    expect(llevaBadge('unverified')).toBe(false);
    expect(llevaBadge('none')).toBe(false);
  });
});
