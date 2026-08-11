import { describe, expect, it } from 'vitest';

import { alternar, aplicarPatch } from './filters';

describe('alternar (#329)', () => {
  it('añade el valor que no estaba', () => {
    expect(alternar([], '4 años')).toEqual(['4 años']);
    expect(alternar(['4 años'], '104')).toEqual(['4 años', '104']);
  });

  it('quita el que ya estaba, sin tocar los demás', () => {
    expect(alternar(['4 años', '104', '36-38'], '104')).toEqual(['4 años', '36-38']);
  });

  it('quitar el último deja el eje sin filtrar', () => {
    expect(alternar(['4 años'], '4 años')).toEqual([]);
  });

  it('conserva el orden en que se fueron marcando', () => {
    // El orden es el del usuario, no alfabético: los chips activos se leen como se pulsaron.
    expect(alternar(alternar(alternar([], 'c'), 'a'), 'b')).toEqual(['c', 'a', 'b']);
  });
});

describe('aplicarPatch (#329)', () => {
  it('escribe una lista como parámetro REPETIDO, no como uno solo', () => {
    // Con `set` se quedaría solo `104` y las otras dos se perderían sin avisar.
    const out = aplicarPatch(new URLSearchParams(), { size: ['4 años', '104', '36-38'] });
    expect(out.getAll('size')).toEqual(['4 años', '104', '36-38']);
  });

  it('una talla con coma dentro sobrevive entera', () => {
    // Es lo que descarta separar por comas: `26 (16,3 cm)` se partiría en dos tallas inexistentes.
    const out = aplicarPatch(new URLSearchParams(), { size: ['26 (16,3 cm)', '30'] });
    expect(out.getAll('size')).toEqual(['26 (16,3 cm)', '30']);
  });

  it('una lista vacía borra el eje', () => {
    const previo = new URLSearchParams('size=26&size=30');
    expect(aplicarPatch(previo, { size: [] }).getAll('size')).toEqual([]);
  });

  it('no arrastra los valores anteriores al reescribir el eje', () => {
    const previo = new URLSearchParams('size=26&size=30');
    expect(aplicarPatch(previo, { size: ['40'] }).getAll('size')).toEqual(['40']);
  });

  it('deja en paz los ejes que el parche no menciona', () => {
    const previo = new URLSearchParams('size=26&gender=niña&sort=descuento');
    const out = aplicarPatch(previo, { color: ['azul'] });
    expect(out.get('gender')).toBe('niña');
    expect(out.get('sort')).toBe('descuento');
    expect(out.getAll('size')).toEqual(['26']);
  });

  it('el vacío, el false y el undefined apagan el filtro', () => {
    const previo = new URLSearchParams('q=botas&inStock=true&gender=niño');
    const out = aplicarPatch(previo, { q: '', inStock: false, gender: undefined });
    expect(out.toString()).toBe('');
  });

  it('los escalares siguen escribiéndose como uno solo', () => {
    const out = aplicarPatch(new URLSearchParams('gender=niño'), { gender: 'niña' });
    expect(out.getAll('gender')).toEqual(['niña']);
  });
});
