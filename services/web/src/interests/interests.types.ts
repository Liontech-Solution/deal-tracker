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
}
