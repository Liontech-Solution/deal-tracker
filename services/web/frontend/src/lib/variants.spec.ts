import { describe, expect, it } from 'vitest';

import { available, countAvailableSizes, distinctSizes, sizeAvailable } from './variants';
import type { Disponibilidad } from './variants';

function v(over: Partial<Disponibilidad> = {}): Disponibilidad {
  return { size: '30', delisted: false, inStock: true, ...over };
}

describe('available (#224)', () => {
  it('una variante a la venta está disponible', () => {
    expect(available(v())).toBe(true);
  });

  it('una variante descatalogada no lo está', () => {
    expect(available(v({ delisted: true }))).toBe(false);
  });

  it('una variante AGOTADA tampoco: es el caso que la ficha dibujaba como comprable', () => {
    expect(available(v({ inStock: false }))).toBe(false);
  });

  it('sin dato de stock (`null`) NO se da por agotada: desconocido no es lo mismo que agotado', () => {
    // Pasa cuando la variante no tiene ninguna fila en `price_history` y el LEFT JOIN del detalle
    // deja el `in_stock` a null. Tacharla sería afirmar algo que nadie ha medido.
    expect(available(v({ inStock: null }))).toBe(true);
  });
});

/**
 * El caso real de la validación de QA: `/producto/4597` (Lefties, "Zapatilla Barefoot Purpurina"),
 * 13 tallas de las que las 31 y 32 vienen `inStock: false` y ninguna descatalogada. La ficha las
 * pintaba clicables y rotulaba «13 disponibles».
 */
const FICHA_4597: Disponibilidad[] = [27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39].map((n) =>
  v({ size: String(n), inStock: n !== 31 && n !== 32 }),
);

describe('tallas de la ficha (#224)', () => {
  it('la talla agotada no cuenta como disponible', () => {
    expect(sizeAvailable(FICHA_4597, '30')).toBe(true);
    expect(sizeAvailable(FICHA_4597, '31')).toBe(false);
    expect(sizeAvailable(FICHA_4597, '32')).toBe(false);
  });

  it('el rótulo dice 11, no 13', () => {
    expect(distinctSizes(FICHA_4597)).toHaveLength(13);
    expect(countAvailableSizes(FICHA_4597)).toBe(11);
  });

  it('una talla sigue disponible si le queda UN color con stock', () => {
    // Una talla son varias variantes (un color cada una): basta que una se pueda comprar.
    const ficha = [
      v({ size: '30', inStock: false }),
      v({ size: '30', inStock: true }),
      v({ size: '31', inStock: false }),
      v({ size: '31', delisted: true }),
    ];
    expect(sizeAvailable(ficha, '30')).toBe(true);
    expect(sizeAvailable(ficha, '31')).toBe(false);
    expect(countAvailableSizes(ficha)).toBe(1);
  });

  it('las tallas repetidas por color no se cuentan dos veces', () => {
    const ficha = [v({ size: '30' }), v({ size: '30' }), v({ size: '31' })];
    expect(distinctSizes(ficha)).toEqual(['30', '31']);
    expect(countAvailableSizes(ficha)).toBe(2);
  });

  it('sin ninguna talla comprable el rótulo dice 0 y no revienta', () => {
    const ficha = [v({ size: '30', inStock: false }), v({ size: '31', delisted: true })];
    expect(countAvailableSizes(ficha)).toBe(0);
  });
});
