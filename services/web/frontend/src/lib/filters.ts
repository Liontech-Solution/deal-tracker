/**
 * Los ejes del catálogo que admiten **varios valores a la vez** (#329), y cómo viajan por la URL.
 *
 * Vive en `lib/` y no dentro de `FilterPanel`/`CatalogPage` porque es la única forma de cubrirlo con
 * test: el `vitest.config.ts` de `frontend/` solo recoge helpers puros y no hay jsdom, así que un
 * componente no se puede renderizar (mismo motivo que `variants.ts`).
 */

/**
 * Añade o quita un valor de un eje multiseleccionable.
 *
 * Es lo que sustituye al `value[key] === v ? '' : v` de la selección única. La diferencia no es de
 * comodidad: como **el vocabulario de talla lo fija la tienda y no la prenda** —Sfera solo publica
 * años, C&A solo alturas en cm—, con un solo valor quien pincha `4 años` excluye a C&A sin que nada
 * se lo diga, aunque su `104` sea esa misma talla física.
 *
 * Quitar el último valor deja la lista vacía, que es «sin filtrar por este eje».
 */
export function alternar(lista: string[], valor: string): string[] {
  return lista.includes(valor) ? lista.filter((v) => v !== valor) : [...lista, valor];
}

/**
 * Aplica un parche de filtros sobre la query string.
 *
 * **La distinción que importa es `set` contra `append`.** Los ejes multiseleccionables viajan como
 * parámetro repetido (`?size=4 años&size=104`), así que escribirlos con `set` —como se hacía cuando
 * todos eran de un valor— se quedaría solo con el último y perdería el resto en silencio.
 *
 * Se separan por parámetro repetido y **no por comas** porque hay tallas que llevan una coma dentro
 * (`26 (16,3 cm)`): un separador por comas partiría un valor legítimo en dos que no existen.
 *
 * Un valor vacío, `false`, `undefined` o una lista vacía **borran** la clave, que es como se apaga
 * un filtro. `false` entra ahí porque los interruptores (`inStock`, `onlyDeals`, `deportiva`)
 * apagados no tienen que ensuciar la URL.
 */
export function aplicarPatch(
  params: URLSearchParams,
  patch: Record<string, string | string[] | boolean | number | undefined>,
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const [clave, valor] of Object.entries(patch)) {
    next.delete(clave);
    if (Array.isArray(valor)) {
      for (const v of valor) next.append(clave, v);
      continue;
    }
    if (valor === '' || valor === false || valor === undefined) continue;
    next.set(clave, String(valor));
  }
  return next;
}
