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

/** Lo que hace falta para rotular el selector de tallas (#331). Ver `sizeLabels()`. */
export interface EtiquetaDeTalla {
  size: string | null;
  sizeLabel: string | null;
}

/** Lo que hace falta para saber qué otras medidas cubre un interés (#331). Ver `otherMeasures()`. */
export interface MedidaHermana extends EtiquetaDeTalla {
  sizeCanon: string | null;
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
 * Cómo se ROTULA cada talla del selector, indexado por la talla cruda, que es la clave con la que
 * la ficha selecciona (#331).
 *
 * La API sirve `sizeLabel` ya resuelto: la canónica, y la medida en cm detrás **solo** cuando ese
 * producto publica dos tallas físicas bajo la misma etiqueta de edad ('0-1 meses · 44 cm' contra
 * '0-1 meses · 50 cm'). Aquí no se decide nada de eso: se indexa lo que viene.
 *
 * De rebote quita del chip el texto de la tienda, que era lo que sacaba
 * '3 meses/6 meses - Medida 68 cm' en un botón de 46 px.
 */
export function sizeLabels(variants: EtiquetaDeTalla[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const v of variants) {
    if (v.size && !m.has(v.size)) m.set(v.size, v.sizeLabel ?? v.size);
  }
  return m;
}

/**
 * Las OTRAS medidas que comparten talla canónica con la elegida (#331).
 *
 * El interés se guarda por `sizeCanon`, así que seguir '0-1 meses (44 cm)' avisa también de
 * '0-1 meses (50 cm)'. Esto es lo que el modal necesita para **decirlo** en vez de que el usuario
 * lo descubra recibiendo un aviso de una prenda que no es la suya.
 *
 * Devuelve las etiquetas ya rotuladas ('0-1 meses · 50 cm'), no las crudas: es texto para leer.
 * Vacío en los 16.482 productos donde la canónica no tapa nada, que es el caso normal.
 */
export function otherMeasures(variants: MedidaHermana[], size: string | null): string[] {
  if (!size) return [];
  const elegida = variants.find((v) => v.size === size);
  if (!elegida?.sizeCanon) return [];
  // Se comparan las ETIQUETAS, no las tallas crudas, y ese es el detalle que decide si el aviso
  // ayuda o estorba: Hipercor publica '9-10 años' y '9-10 años - Medida 128 cm' como dos filas,
  // pero la base les da la MISMA etiqueta porque son la misma talla física. Comparando por la
  // cruda, el modal habría dicho «esta tienda publica 2 medidas con esta misma talla (9-10 años
  // y la elegida)», que es exactamente la confusión que esto viene a quitar.
  const otras = variants.filter(
    (v) => v.sizeCanon === elegida.sizeCanon && v.sizeLabel && v.sizeLabel !== elegida.sizeLabel,
  );
  return [...new Set(otras.map((v) => v.sizeLabel as string))];
}

/**
 * Cuántas de las tallas de la ficha se pueden comprar. Es lo que rotula el selector, y contarlo
 * sin mirar el stock era el segundo síntoma de #224: la ficha decía «13 disponibles» sobre 13
 * variantes de las que solo 11 lo estaban, contradiciendo al JSON que ella misma había pedido.
 */
export function countAvailableSizes(variants: Disponibilidad[]): number {
  return distinctSizes(variants).filter((s) => sizeAvailable(variants, s)).length;
}
