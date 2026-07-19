/** Formato de dinero. Los precios llegan como string exacto desde la API. */

export function parseMoney(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 12.99 -> "12,99 €" (formato del diseño). */
export function eur(value: number): string {
  return value.toFixed(2).replace('.', ',') + ' €';
}

/** Igual que `eur` pero tolerando el string de la API (o null). */
export function eurStr(value: string | null | undefined): string | null {
  const n = parseMoney(value);
  return n === null ? null : eur(n);
}

/** Descuento entero a partir del string de discount_pct de la API. */
export function discountInt(value: string | null | undefined): number | null {
  const n = parseMoney(value);
  return n === null ? null : Math.round(n);
}

/** Primera letra en mayúscula (para categorías/colores). */
export function capitalize(value: string): string {
  return value.length ? value[0].toUpperCase() + value.slice(1) : value;
}
