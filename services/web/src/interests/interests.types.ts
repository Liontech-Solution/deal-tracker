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
  /**
   * Etiqueta legible de la variante apuntada, p.ej. "Talla 24 · rojo".
   *
   * **Es la del BOT**, y por eso lleva el color tal cual lo escribe la tienda: la misma cadena que
   * sale en el aviso de Telegram (#223). La SPA no la pinta desde #297 — se compone allí a partir
   * de las dos piezas de abajo, para poder capitalizar el color sin tocar lo que se envía.
   */
  variantLabel: string | null;
  /**
   * Las dos piezas con las que se arma `variantLabel`, expuestas desde #297 para que la SPA pueda
   * rotular sin **parsear una cadena ya montada** ni rehacer `size_canon` en TypeScript.
   *
   * `variantSize` es la talla CANÓNICA (la misma que guarda `interest.size` y que ofrecen las
   * facetas, #223); `variantColor` es el color CRUDO, porque `color_canon` devuelve NULL para lo
   * que no reconoce (#51) y canonizarlo aquí lo borraría en vez de normalizarlo.
   */
  variantSize: string | null;
  variantColor: string | null;
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
  /**
   * **La prenda seguida ya no aparece en las pasadas** (`product.delisted_at IS NOT NULL`), para
   * poder pintar la fila apagada. `false` también en un interés por filtros, que no apunta a
   * ninguna prenda y por tanto no puede estar de baja.
   *
   * Se añadió con #435 (favoritos): `/seguimientos` arrastraba el mismo defecto que la lista nueva
   * venía a evitar —enseñar como viva una prenda que lleva N pasadas sin aparecer— y dejar a las
   * dos listas hermanas diciendo cosas distintas de la misma prenda era peor que arreglarlo.
   *
   * No significa «ya no existe»: la baja es conservadora y **se deshace sola** en cuanto una pasada
   * vuelve a ver el producto (`ingest.py`, `ON CONFLICT ... delisted_at = NULL`). Por eso el
   * seguimiento no se cancela ni se borra: `interest.product_id` no tiene FK dura justamente para
   * que pueda sobrevivir a esto.
   */
  delisted: boolean;
}
