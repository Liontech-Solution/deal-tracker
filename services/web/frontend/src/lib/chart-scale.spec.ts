import { describe, expect, it } from 'vitest';

import { marcasRedondas } from './chart-scale';

/**
 * Las marcas del eje de la gráfica de histórico (#236).
 *
 * Los dominios de estos casos son los que calcula `PriceHistoryChart`: `ymin = min × 0,94` y
 * `ymax = max × 1,04`, o sea el dominio RECORTADO. Por eso ninguna marca puede caer fuera: si se
 * saliera, se dibujaría una línea de referencia flotando por encima o por debajo de la curva.
 */
describe('marcasRedondas', () => {
  it('no se sale del dominio por ningún extremo', () => {
    for (const [min, max] of [
      [12.21, 41.59],
      [28.15, 33.23],
      [1.88, 10.4],
      [93.86, 208.0],
    ] as [number, number][]) {
      const marcas = marcasRedondas(min, max);
      expect(marcas.length).toBeGreaterThanOrEqual(2);
      expect(Math.min(...marcas)).toBeGreaterThanOrEqual(min);
      expect(Math.max(...marcas)).toBeLessThanOrEqual(max);
    }
  });

  it('da marcas redondas en un recorrido amplio (40 € -> 12 €)', () => {
    // El caso que la issue contrapone al estrecho: una bajada de verdad. Con este dominio caben
    // seis marcas de 5 en 5, pero gana el paso de 10: tres cifras redondas se leen de un vistazo y
    // seis apretadas en 200 px de alto, no.
    expect(marcasRedondas(11.28, 41.6)).toEqual([20, 30, 40]);
  });

  /**
   * El caso que motiva la issue: 29,95 € -> 31,95 €. La curva ocupa toda la altura igual que en el
   * caso de arriba, y sin cifras en el eje las dos se leen como el mismo desplome. Con marcas, el
   * eje delata que el recorrido real son dos euros.
   */
  it('etiqueta también un recorrido estrecho, que es donde la escala recortada engaña', () => {
    const marcas = marcasRedondas(28.15, 33.23);
    expect(marcas.length).toBeGreaterThanOrEqual(3);
    expect(marcas).toEqual([29, 30, 31, 32, 33]);
  });

  it('mantiene las marcas en céntimos exactos, sin arrastre de coma flotante', () => {
    for (const marca of marcasRedondas(1.88, 10.4)) {
      expect(Math.round(marca * 100)).toBe(marca * 100);
    }
  });

  it('devuelve vacío para un dominio degenerado, en vez de un eje inventado', () => {
    expect(marcasRedondas(10, 10)).toEqual([]);
    expect(marcasRedondas(10, 5)).toEqual([]);
    expect(marcasRedondas(Number.NaN, 5)).toEqual([]);
  });
});
