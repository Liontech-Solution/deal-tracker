import type { Interest } from '../database/schema';

/**
 * Interés enriquecido para la UI de "Mis seguimientos". Sobre la fila cruda de `interest`
 * añade los nombres resueltos (producto/variante/tienda) cuando el interés apunta a un
 * objetivo concreto. Se resuelven por id lógico con LEFT JOIN (sin FK dura: un interés puede
 * sobrevivir a una baja temporal del producto), así que pueden venir `null`.
 */
export interface InterestView extends Interest {
  /** Nombre de la tienda (por `retailer_id`, o la del producto/variante apuntados). */
  retailerName: string | null;
  /** Nombre del producto apuntado (directo por `product_id` o vía la variante). */
  productName: string | null;
  /** Etiqueta legible de la variante apuntada, p.ej. "Talla 24 · rojo". */
  variantLabel: string | null;
  /**
   * Producto al que enlazar la tarjeta (#302): el apuntado, o el de la variante apuntada. `null`
   * en un interés por filtros, que no apunta a ninguna prenda — y esa es la razón de que este
   * campo exista en vez de reutilizar `productId`, que es el ALCANCE declarado del interés y
   * significa otra cosa: un interés de variante lo trae `null` y aun así tiene ficha que enseñar.
   */
  targetProductId: number | null;
  /** Foto de la prenda seguida: la del color de la variante si la hay, o la del producto (#302). */
  imageUrl: string | null;
  /** Sección del producto apuntado, para el fondo del hueco de la foto. NO es `section`, que es la del filtro. */
  productSection: string | null;
}
