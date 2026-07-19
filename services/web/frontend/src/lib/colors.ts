/**
 * Aproximación de color → hex para pintar la muestra del selector de color. Los scrapers
 * guardan el color como texto libre (p. ej. "verde salvia"), así que esto es best-effort:
 * si no reconocemos el nombre devolvemos null y la UI pinta una muestra neutra con la etiqueta.
 */
const SWATCHES: Array<[RegExp, string]> = [
  [/negro|black/, '#2e2a24'],
  [/blanc|white|crudo|marfil/, '#f3ede1'],
  [/crema|cream|hueso/, '#ede3d0'],
  [/beige|arena|camel|sand/, '#d8c6ab'],
  [/gris|grey|gray|piedra|antracita/, '#a79e92'],
  [/marino|navy/, '#2f3b52'],
  [/celeste|cielo/, '#9dc0d8'],
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
];

export function colorHex(name: string | null | undefined): string | null {
  if (!name) return null;
  const n = name.trim().toLowerCase();
  for (const [re, hex] of SWATCHES) {
    if (re.test(n)) return hex;
  }
  return null;
}
