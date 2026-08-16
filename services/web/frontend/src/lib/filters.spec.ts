import { describe, expect, it } from 'vitest';

import { alternar, aplicarPatch, parcheBanda, parcheSeccion, patchSeccion } from './filters';

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

describe('patchSeccion (#434)', () => {
  it('conserva TODO lo demás de la búsqueda', () => {
    // El fallo medido: la cabecera construía `/catalogo?section=ropa` y se llevaba por delante
    // género, tienda, color, `q`, `sort` y el rango de precio.
    const previo = new URLSearchParams(
      'gender=niña&section=zapateria&color=azul&retailer=zara&q=botas&sort=descuento&minPrice=5&maxPrice=20&inStock=true',
    );
    const out = patchSeccion(previo, 'ropa');
    expect(out.get('section')).toBe('ropa');
    expect(out.get('gender')).toBe('niña');
    expect(out.getAll('color')).toEqual(['azul']);
    expect(out.getAll('retailer')).toEqual(['zara']);
    expect(out.get('q')).toBe('botas');
    expect(out.get('sort')).toBe('descuento');
    expect(out.get('minPrice')).toBe('5');
    expect(out.get('maxPrice')).toBe('20');
    expect(out.get('inStock')).toBe('true');
  });

  it('limpia talla y categoría, que son los dos que cambian de significado', () => {
    // `36-38` es un calcetín en ropa y un número de pie en zapatería; `pantalones` no existe allí.
    const previo = new URLSearchParams('section=ropa&size=36-38&size=104&category=pantalones');
    const out = patchSeccion(previo, 'zapateria');
    expect(out.getAll('size')).toEqual([]);
    expect(out.get('category')).toBeNull();
  });

  it('la cadena vacía es «sin sección», no un section vacío en la URL', () => {
    const out = patchSeccion(new URLSearchParams('gender=niño&section=ropa&category=camisetas'), '');
    expect(out.has('section')).toBe(false);
    expect(out.get('gender')).toBe('niño');
    // Y tampoco puede dejar una categoría de la sección que se acaba de quitar (síntoma 4 de #434).
    expect(out.has('category')).toBe(false);
  });

  it('no muta los parámetros que recibe', () => {
    const previo = new URLSearchParams('section=ropa&category=camisetas');
    patchSeccion(previo, 'zapateria');
    expect(previo.get('section')).toBe('ropa');
    expect(previo.get('category')).toBe('camisetas');
  });

  it('se lleva también la talla concreta, que vivía dentro de la banda', () => {
    const out = patchSeccion(new URLSearchParams('section=ropa&size=4 años&sizeExact=104'), 'zapateria');
    expect(out.getAll('size')).toEqual([]);
    expect(out.getAll('sizeExact')).toEqual([]);
  });
});

describe('parcheBanda (#367)', () => {
  it('tocar la banda suelta las tallas concretas de dentro', () => {
    // Sin esto, quitar la banda dejaría un `?sizeExact=104` que el panel ya NO pinta —el segundo
    // piso desaparece con la banda— y el catálogo seguiría filtrando por él en silencio.
    expect(parcheBanda([])).toEqual({ size: [], sizeExact: [] });
    expect(parcheBanda(['6 años'])).toEqual({ size: ['6 años'], sizeExact: [] });
  });

  it('también al AÑADIR una banda, no solo al quitarla', () => {
    // El panel no sabe a qué banda pertenece cada concreta: esa correspondencia la calcula
    // `size_band` en la base. Soltarlas todas es predecible; adivinar cuáles sobreviven, no.
    expect(parcheBanda(['4 años', '6 años']).sizeExact).toEqual([]);
  });

  it('parcheSeccion hereda la regla', () => {
    expect(parcheSeccion('zapateria')).toEqual({
      section: 'zapateria',
      size: [],
      sizeExact: [],
      category: '',
    });
  });
});
