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

/**
 * Cambiar de sección conservando el resto de la búsqueda (#434).
 *
 * **Existe porque el mismo eje tenía tres controles con tres comportamientos.** El del panel
 * parcheaba y conservaba; los enlaces Ropa/Zapatería de la cabecera (`Layout.tsx`) y el de la home
 * (`HomePage.tsx`) construían la URL entera —`/catalogo?section=<s>`—, y como React Router
 * sustituye el `search` completo se llevaban por delante **género, categoría, talla, color, tienda,
 * `q`, `sort` y el rango de precio**. Medido: desde `?gender=niña&section=zapateria`, pulsar «Ropa»
 * en la cabecera dejaba `?section=ropa` y el género se había ido por el camino.
 *
 * Se limpian `size` y `category`, y **es lo único que se limpia**: ropa y zapatería no comparten
 * vocabulario de talla y encima **se solapan** —`36-38` es un calcetín en una y un número de pie en
 * la otra—, así que arrastrar la talla al saltar de sección le cambiaría el significado sin que
 * nadie lo haya pedido. La categoría por lo mismo: `pantalones` no existe en zapatería y dejaría el
 * catálogo vacío.
 *
 * `section: ''` es «sin sección»: quita ese eje y deja el resto intacto. Desde #434 se alcanza
 * repulsando la pestaña activa, igual que en cualquier otro eje de valor único.
 *
 * Se parte en dos porque los tres controles no hablan el mismo idioma: el panel emite un **parche
 * de filtros** y los dos enlaces de fuera manejan **la query string**. La regla se escribe una vez
 * y cada uno la consume por su lado; que estuviera escrita tres veces es lo que produjo tres
 * comportamientos.
 */
export function parcheSeccion(
  section: string,
): { section: string; size: string[]; sizeExact: string[]; category: string } {
  // `sizeExact` cae con la banda que lo contenía (#367): ver `parcheBanda`.
  return { section, ...parcheBanda([]), category: '' };
}

/** La misma regla, aplicada sobre la query string: es lo que consumen los dos enlaces de fuera del
 *  panel, que no tienen a mano el objeto de filtros sino la URL. */
export function patchSeccion(params: URLSearchParams, section: string): URLSearchParams {
  return aplicarPatch(params, parcheSeccion(section));
}

/**
 * Tocar la BANDA de talla suelta las tallas concretas que había dentro (#367).
 *
 * Es la misma regla que `parcheSeccion` un piso más abajo, y por el mismo motivo: la talla concreta
 * solo significa algo dentro de la banda desde la que se eligió. Sin esto, quitar la banda `4 años`
 * dejaría un `?sizeExact=104` puesto que **el panel ya no pinta** —el segundo piso desaparece con la
 * banda— y el catálogo seguiría filtrando por él sin que nada lo diga. Un filtro invisible es peor
 * que un filtro perdido.
 *
 * Se limpia al tocar la banda, no solo al quitarla, porque el panel no puede saber a qué banda
 * pertenece cada concreta: esa correspondencia la calcula `size_band` en la base y solo llega, para
 * la selección de ese momento, dentro de la faceta `sizeValues`. Elegir a ojo cuáles sobreviven
 * sería adivinar; soltarlas todas es predecible y se ve.
 */
export function parcheBanda(bandas: string[]): { size: string[]; sizeExact: string[] } {
  return { size: bandas, sizeExact: [] };
}
