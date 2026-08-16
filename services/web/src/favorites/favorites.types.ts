import type { Favorite } from '../database/schema';

/**
 * Favorito enriquecido para la página `/favoritos`. Sobre la fila cruda de `favorite` añade lo que
 * hace falta para *enseñar la prenda* —nombre, tienda, foto y precio—, resuelto con LEFT JOIN por
 * id lógico: `favorite.product_id` no tiene FK dura, así que todo esto puede venir `null`.
 */
export interface FavoriteView extends Favorite {
  /** Nombre del producto guardado. */
  productName: string | null;
  /** Tienda del producto guardado. */
  retailerName: string | null;
  /** Foto principal del producto (la de `position = 0`, o la del propio `product.image_url`). */
  imageUrl: string | null;
  /** Sección del producto, para el fondo del hueco cuando no hay foto. */
  productSection: string | null;
  /**
   * El «desde» del producto, leído de `product_agg` con **`scope = 'todas'`** — el mismo ámbito
   * que usa el catálogo cuando no se filtra por stock (`catalog.service.ts`), para que la fila no
   * enseñe un precio distinto del de la tarjeta por la que el usuario llegó. Que ese «desde» pueda
   * ser una talla agotada es #402, y se decide allí, no aquí.
   */
  priceFrom: string | null;
  /**
   * **La prenda ya no aparece en las pasadas** (`product.delisted_at IS NOT NULL`).
   *
   * No significa «ya no existe»: la baja es conservadora (`missing_streak` contra los
   * `SCRAPER_DELIST_*`) y **se deshace sola** en cuanto una pasada vuelve a ver el producto, porque
   * el `ON CONFLICT` de `ingest.py` pone `delisted_at = NULL`. Por eso la fila se pinta apagada y
   * NUNCA se borra sola: lo que hoy está de baja puede volver la semana que viene.
   */
  delisted: boolean;
  /**
   * ¿Hay además un seguimiento activo del mismo producto? Para que la fila ofrezca «Avisarme si
   * baja» o lleve a `/seguimientos` en vez de invitar a crear el mismo aviso dos veces.
   *
   * Se mira por `interest.product_id`, que es el alcance que crea `FollowModal` desde aquí (el
   * favorito es de producto entero). Un seguimiento de VARIANTE de la misma prenda no cuenta: es
   * otro alcance, y ofrecer el de producto encima sigue teniendo sentido.
   */
  seguido: boolean;
}
