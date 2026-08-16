import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, desc, eq, sql } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { favorite, interest, product, productAgg, retailer } from '../database/schema';
import type { Favorite } from '../database/schema';
import type { CreateFavoriteDto } from './dto/create-favorite.dto';
import type { FavoriteView } from './favorites.types';

/**
 * Prendas guardadas sin pedir aviso (#435).
 *
 * Lo que este servicio NO hace es tan importante como lo que hace: **no escribe en `interest`**. La
 * única condición de notificabilidad del sistema es el `JOIN interest i ON i.active` de
 * `matching.service.ts`, así que mientras los favoritos vivan solo aquí, marcar un corazón no puede
 * generar un aviso de Telegram. Eso está cubierto por un test que ejecuta el job de verdad.
 */
@Injectable()
export class FavoritesService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  /**
   * Los favoritos del usuario, los últimos primero, enriquecidos con la prenda.
   *
   * **No filtra por `delisted_at` a propósito**: un favorito puede sobrevivir al producto, y los de
   * baja tienen que salir justamente para poder pintarlos apagados. Borrarlos —o esconderlos— sería
   * perder algo que puede volver: la baja se deshace sola en cuanto una pasada vuelve a ver el
   * producto (`ingest.py`, `ON CONFLICT ... delisted_at = NULL`).
   */
  async list(userId: number): Promise<FavoriteView[]> {
    const rows = await this.db
      .select({
        favorite,
        productName: product.name,
        productSection: product.section,
        imageUrl: product.imageUrl,
        delistedAt: product.delistedAt,
        retailerName: retailer.name,
        // `price_repr` y no `price_from`, igual que la tarjeta desde #402: es lo más barato
        // COMPRABLE, con respaldo al mínimo a secas cuando no hay nada con stock. Leer aquí la otra
        // columna volvería a separar las dos pantallas, que es lo que el comentario del JOIN de
        // abajo lleva pidiendo desde #435.
        priceFrom: productAgg.priceRepr,
        // ¿Hay ya un seguimiento ACTIVO de este mismo producto? Subconsulta y no un LEFT JOIN a
        // `interest` porque un join multiplicaría la fila del favorito por cada interés que case, y
        // esta lista tiene que devolver exactamente un favorito por corazón.
        seguido: sql<boolean>`EXISTS (
          SELECT 1 FROM ${interest} i
           WHERE i.user_id = ${favorite.userId}
             AND i.product_id = ${favorite.productId}
             AND i.active
        )`,
      })
      .from(favorite)
      .leftJoin(product, eq(product.id, favorite.productId))
      .leftJoin(retailer, eq(retailer.id, product.retailerId))
      // `product_agg` tiene DOS filas por producto desde la 0038 ('todas' y 'con_stock'), así que
      // leerla sin fijar el ámbito duplicaría cada favorito sin decir nada. Se fija 'todas', que es
      // lo que el catálogo enseña por defecto: la fila de `/favoritos` tiene que decir el mismo
      // «desde» que la tarjeta desde la que se guardó.
      .leftJoin(
        productAgg,
        and(eq(productAgg.productId, favorite.productId), eq(productAgg.scope, 'todas')),
      )
      .where(eq(favorite.userId, userId))
      .orderBy(desc(favorite.createdAt));

    return rows.map((r) => ({
      ...r.favorite,
      productName: r.productName ?? null,
      retailerName: r.retailerName ?? null,
      imageUrl: r.imageUrl ?? null,
      productSection: r.productSection ?? null,
      priceFrom: r.priceFrom ?? null,
      delisted: r.delistedAt !== null,
      seguido: r.seguido,
    }));
  }

  /**
   * Marca el corazón. **Idempotente**: volver a marcar lo ya marcado devuelve la misma fila y no
   * falla, que es lo que se espera de un botón que se pulsa dos veces sin querer. Por eso el
   * `ON CONFLICT DO NOTHING` va acompañado del `SELECT` de respaldo: `DO NOTHING` no devuelve fila.
   *
   * No se comprueba que el producto exista, igual que `interest` no lo comprueba: no hay FK dura, y
   * un favorito de un producto que se acaba de dar de baja es un caso legítimo, no un error.
   */
  async create(userId: number, dto: CreateFavoriteDto): Promise<Favorite> {
    const [row] = await this.db
      .insert(favorite)
      .values({ userId, productId: dto.productId })
      .onConflictDoNothing({ target: [favorite.userId, favorite.productId] })
      .returning();
    if (row) return row;

    const [existente] = await this.db
      .select()
      .from(favorite)
      .where(and(eq(favorite.userId, userId), eq(favorite.productId, dto.productId)));
    return existente;
  }

  /**
   * Quita el corazón. Borrado **físico**, al revés que `interest.remove()` (#149): allí la baja es
   * lógica porque de la fila cuelgan las `notification` ya entregadas y con ellas la protección
   * contra el aviso repetido. De un favorito no cuelga nada, así que aquí no hay historial que
   * proteger y una fila muerta solo sería ruido.
   *
   * Va por `productId` y no por el `id` de la fila a propósito: el corazón de la tarjeta sabe qué
   * producto pinta, no qué fila de favorito le corresponde.
   */
  async remove(userId: number, productId: number): Promise<void> {
    const borradas = await this.db
      .delete(favorite)
      .where(and(eq(favorite.userId, userId), eq(favorite.productId, productId)))
      .returning({ id: favorite.id });
    if (borradas.length === 0) {
      throw new NotFoundException(`El producto ${productId} no está en favoritos`);
    }
  }
}
