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

/**
 * Cómo se nombra una variante en la SPA: «Talla 2 años · Rosa».
 *
 * Existe por #297. La ficha rotulaba el chip de color con `capitalize()` y el modal que se abre
 * encima tomaba la etiqueta ya montada por la API, que emite el color **crudo** — así que a pocos
 * centímetros se leía `Rosa` y `rosa`. Aquí las dos salen de la misma función, que es lo que impide
 * que vuelvan a separarse.
 *
 * **El plegado es de presentación y se queda en la SPA.** `variantLabel()` del backend no se toca,
 * porque es la cadena que va al aviso de Telegram: cambiar la caja de lo que se envía tiene que ser
 * una decisión propia, no un efecto de rebote de arreglar un rótulo.
 *
 * `capitalize` y no una capitalización por segmentos: el 78 % de los colores son compuestos (2.169
 * de 2.782 distintos, medidos en dev) y las tiendas ya los escriben con criterios propios —H&M da
 * `Blanco/Floral` y Zara `blanco / negro`—. Subir la inicial de cada tramo reescribiría 2.169
 * valores para imponer un criterio que ninguna tienda usa; subir solo la primera es lo que el chip
 * del catálogo lleva haciendo desde siempre, y con esto la etiqueta dice exactamente lo mismo.
 *
 * La talla tiene que llegar CANÓNICA (`sizeCanon` / `variantSize`), no la cruda de la tienda: es la
 * que ve el usuario en `/seguimientos` y la que guarda `interest.size`, y rehacerla aquí desde el
 * texto crudo es el fallo que arregló #248.
 */
export function etiquetaVariante(size: string | null, color: string | null): string | null {
  const partes = [size ? `Talla ${size}` : null, color ? capitalize(color) : null].filter(
    (p): p is string => !!p,
  );
  return partes.length ? partes.join(' · ') : null;
}
