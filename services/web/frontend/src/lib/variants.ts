/**
 * Disponibilidad de una variante en la ficha de producto (#224).
 *
 * Vive aquí y no en `ProductPage.tsx` porque es la única forma de cubrirlo con test: de `frontend/`
 * el `vitest.config.ts` solo recoge helpers puros, y sin jsdom ni testing-library un componente no
 * se puede renderizar.
 */

/** Lo que estas funciones necesitan de una variante. Menos que `VariantWithPrice`, a propósito. */
export interface Disponibilidad {
  size: string | null;
  delisted: boolean;
  inStock: boolean | null;
}

/**
 * ¿Se puede comprar esta variante?
 *
 * Son DOS motivos para que no, y hasta #224 solo se miraba el primero: la prenda puede estar
 * descatalogada (la tienda ya no la publica) o simplemente **agotada** en esa talla. El segundo es
 * el habitual, y se dibujaba como disponible: el usuario pulsaba la talla creyendo que podía
 * comprarla y solo entonces el badge del precio decía «Agotado».
 *
 * `inStock: null` NO es agotada, y por eso la comparación es contra `false` y no un `!`: la columna
 * `price_history.in_stock` es NOT NULL, así que el null solo aparece cuando la variante no tiene
 * ninguna fila de precio y el LEFT JOIN del detalle no encuentra nada. Eso es *desconocido*, y no
 * se tacha lo que no se sabe.
 */
export function available(v: Disponibilidad): boolean {
  return !v.delisted && v.inStock !== false;
}

/** ¿Queda alguna variante comprable en esta talla? (una talla son varios colores). */
export function sizeAvailable(variants: Disponibilidad[], size: string): boolean {
  return variants.some((v) => v.size === size && available(v));
}

/** Las tallas distintas de la ficha, en el orden en que las trae la API. */
export function distinctSizes(variants: Disponibilidad[]): string[] {
  return [...new Set(variants.map((v) => v.size).filter((s): s is string => !!s))];
}

/**
 * Cuántas de las tallas de la ficha se pueden comprar. Es lo que rotula el selector, y contarlo
 * sin mirar el stock era el segundo síntoma de #224: la ficha decía «13 disponibles» sobre 13
 * variantes de las que solo 11 lo estaban, contradiciendo al JSON que ella misma había pedido.
 */
export function countAvailableSizes(variants: Disponibilidad[]): number {
  return distinctSizes(variants).filter((s) => sizeAvailable(variants, s)).length;
}
