import { describe, expect, it } from 'vitest';

import { capitalize, etiquetaVariante } from './format';

/**
 * #297. La ficha rotulaba el chip de color con `capitalize()` y el modal que se abre encima pintaba
 * la etiqueta que sirve la API, que lleva el color crudo: a pocos centímetros se leía `Rosa` y
 * `rosa`. Lo que estos casos fijan es que las dos salgan de la misma función.
 */
describe('etiquetaVariante (#297)', () => {
  it('capitaliza el color, que es la incoherencia que arregla', () => {
    expect(etiquetaVariante('2 años', 'rosa')).toBe('Talla 2 años · Rosa');
  });

  it('dice lo mismo que el chip del catálogo, que usa `capitalize`', () => {
    const color = 'morado/lila';
    expect(etiquetaVariante('4 años', color)).toBe(`Talla 4 años · ${capitalize(color)}`);
  });

  it('no toca un color que la tienda ya escribe capitalizado', () => {
    // H&M publica así la mayoría de los suyos; subirle la inicial a cada tramo reescribiría los
    // 2.169 colores compuestos para imponer un criterio que ninguna tienda usa.
    expect(etiquetaVariante('6-9 meses', 'Blanco/Floral')).toBe('Talla 6-9 meses · Blanco/Floral');
  });

  it('la talla va tal cual llega: tiene que ser la CANÓNICA, no la cruda', () => {
    // Si el llamante le pasara `size` en vez de `sizeCanon`, el modal confirmaría una talla y
    // `/seguimientos` enseñaría otra — que es el fallo que arregló #248.
    expect(etiquetaVariante('11-12 años', 'rojo')).toBe('Talla 11-12 años · Rojo');
  });

  it('con solo talla o solo color, no deja el separador colgando', () => {
    expect(etiquetaVariante('24', null)).toBe('Talla 24');
    expect(etiquetaVariante(null, 'azul marino')).toBe('Azul marino');
  });

  it('sin talla ni color no hay etiqueta', () => {
    expect(etiquetaVariante(null, null)).toBeNull();
  });
});
