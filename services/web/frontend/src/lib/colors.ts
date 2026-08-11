/**
 * Aproximación de color → hex para pintar la muestra del selector de color. Los scrapers
 * guardan el color como texto libre (p. ej. "verde salvia"), así que esto es best-effort:
 * si no reconocemos el nombre devolvemos null y la UI pinta una muestra neutra con la etiqueta.
 *
 * DOS CONSUMIDORES CON NECESIDADES DISTINTAS, y por eso la lista se queda con las reglas amplias:
 *   - `ProductPage` la llama con el color ESPECÍFICO de la variante ('verde salvia'), que no se
 *     pliega y sigue siendo texto libre de la tienda.
 *   - `FilterPanel` la llama con la FAMILIA que devuelve la faceta desde #291 ('verde'), que son
 *     los ~19 valores que produce `color_family` (migración 0029).
 *
 * De ahí que toda familia tenga que resolver aquí a un hex: si una migración futura añade una
 * familia y nadie toca este fichero, su chip sale con la muestra neutra y no da ningún error. Lo
 * fija `colors.spec.ts`, que recorre la lista de familias y exige hex para todas menos 'estampado'.
 */
const SWATCHES: Array<[RegExp, string]> = [
  [/negro|black/, '#2e2a24'],
  [/blanc|white|crudo|marfil/, '#f3ede1'],
  [/crema|cream|hueso/, '#ede3d0'],
  [/beige|arena|camel|sand/, '#d8c6ab'],
  [/gris|grey|gray|piedra|antracita|plata|platead/, '#a79e92'],
  [/marino|navy/, '#2f3b52'],
  [/celeste|cielo/, '#9dc0d8'],
  // Familia propia desde #291: 1.768 variantes, demasiadas para esconderlas dentro de 'azul'.
  [/turquesa|turquoise/, '#6fb3ad'],
  [/azul|blue|niebla|denim/, '#6f8aa3'],
  [/salvia|sage/, '#8fa07e'],
  [/verde|green|oliva|khaki|caqui/, '#7e9070'],
  [/teja|terracota|ladrillo|rust/, '#b4674b'],
  [/rojo|red|granate|burdeos/, '#a5473b'],
  [/rosa|pink|coral/, '#d99a97'],
  [/naranja|orange/, '#d68a4a'],
  [/mostaza|amarill|yellow|dorado|ocre/, '#c79a3e'],
  [/morado|lila|violeta|purple|malva/, '#8f7aa3'],
  [/marr[oó]n|brown|chocolate|tostado/, '#7c5c42'],
  // 'estampado' NO lleva entrada, y es una decisión, no un olvido: es la familia de lo que no
  // nombra ningún color (rayas, multicolor, leopardo), así que cualquier hex que le pusiéramos
  // mentiría. Cae en el `null` de abajo y la UI le pinta la muestra neutra, que es lo honesto.
];

export function colorHex(name: string | null | undefined): string | null {
  if (!name) return null;
  const n = name.trim().toLowerCase();
  for (const [re, hex] of SWATCHES) {
    if (re.test(n)) return hex;
  }
  return null;
}
