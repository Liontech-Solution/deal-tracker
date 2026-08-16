import { describe, expect, it } from 'vitest';

import { SECCIONES, etiquetaSeccion } from './section';

describe('etiquetaSeccion (#434)', () => {
  it('escribe «Zapatería» con tilde, que el slug no lleva', () => {
    // El chip activo del catálogo capitalizaba el slug crudo y pintaba «Zapateria». Anotado en el
    // informe de QA de la v0.1.9 y seguía ahí.
    expect(etiquetaSeccion('zapateria')).toBe('Zapatería');
    expect(etiquetaSeccion('ropa')).toBe('Ropa');
  });

  it('con un slug desconocido capitaliza, en vez de quedarse en blanco', () => {
    // La sección viaja por la URL y la teclea cualquiera: un valor que no existe tiene que salir
    // legible, no vacío.
    expect(etiquetaSeccion('complementos')).toBe('Complementos');
    expect(etiquetaSeccion('')).toBe('');
  });

  it('son las dos secciones del brief, y en ese orden', () => {
    expect(SECCIONES.map((s) => s.value)).toEqual(['ropa', 'zapateria']);
  });
});
