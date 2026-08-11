import { describe, expect, it } from 'vitest';

import { colorHex } from './colors';

/**
 * El espejo de #291. `color_family` (migración 0029) decide en SQL qué familias existen, y este
 * fichero decide en TS de qué color se pinta la muestra de cada chip. Son dos sitios para una misma
 * lista, que es exactamente el problema que #228 describe en otro sitio del repo, y aquí se rompe
 * en silencio: una familia sin entrada en `SWATCHES` no da error, solo sale con la muestra neutra.
 *
 * Esta lista es la de la migración 0029, copiada a mano a propósito. Si una migración futura añade
 * una familia y no la añade aquí, este test falla y esa es toda su razón de ser.
 */
const FAMILIAS = [
  'negro',
  'gris',
  'marino',
  'turquesa',
  'celeste',
  'azul',
  'salvia',
  'verde',
  'teja',
  'marrón',
  'rojo',
  'rosa',
  'naranja',
  'amarillo',
  'morado',
  'beige',
  'crema',
  'blanco',
];

describe('colorHex · familias de #291', () => {
  it.each(FAMILIAS)('la familia %s resuelve a un hex', (familia) => {
    expect(colorHex(familia)).toMatch(/^#[0-9a-f]{6}$/);
  });

  /**
   * La excepción, y es deliberada: 'estampado' agrupa lo que no nombra ningún color ('rayas',
   * 'multicolor', 'leopardo'), así que cualquier hex mentiría. La muestra neutra es lo honesto.
   */
  it('estampado no tiene hex a propósito', () => {
    expect(colorHex('estampado')).toBeNull();
  });

  /**
   * Las dos que motivaron tocar este fichero en #291: ninguna de las dos casaba con los 17 SWATCHES
   * originales, así que sus chips salían con la muestra neutra siendo colores de verdad.
   * 'plateado' es el caso traicionero — NO contiene 'plata'.
   */
  it('turquesa y plateado, que antes caían en la muestra neutra', () => {
    expect(colorHex('turquesa')).not.toBeNull();
    expect(colorHex('plateado')).not.toBeNull();
  });

  /**
   * El color específico de la variante sigue pasando por aquí (`ProductPage`), y no está plegado:
   * es el texto libre de la tienda. Las reglas tienen que seguir siendo amplias.
   */
  it('sigue resolviendo el color específico, que no se pliega', () => {
    expect(colorHex('verde salvia')).toBe(colorHex('salvia'));
    expect(colorHex('AZUL MARINO')).toBe(colorHex('marino'));
    expect(colorHex('nombre que no existe')).toBeNull();
  });
});
