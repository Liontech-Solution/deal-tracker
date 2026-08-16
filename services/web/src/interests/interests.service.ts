import { BadRequestException, Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, desc, eq, sql } from 'drizzle-orm';
import { alias } from 'drizzle-orm/pg-core';

import { Database, DRIZZLE } from '../database/database.module';
import {
  interest,
  product,
  productImage,
  retailer,
  variant,
  type Interest,
} from '../database/schema';
import type { CreateInterestDto } from './dto/create-interest.dto';
import type { InterestView } from './interests.types';

@Injectable()
export class InterestsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  /**
   * Lista los intereses activos del usuario, enriquecidos con los nombres del objetivo
   * (producto/variante/tienda) cuando apuntan a uno. Las uniones son por id lógico y LEFT
   * (sin FK dura desde `interest`): si el objetivo ya no existe, los nombres vienen `null`.
   *
   * Desde #302 devuelve además con qué **enseñar** la prenda seguida —foto, y el id con el que
   * enlazar a su ficha—, porque la lista traía la configuración del aviso y no la prenda. Todo
   * eso puede venir `null` y la tarjeta tiene que aguantarlo: **un interés por filtros no apunta
   * a ninguna prenda** ('toda la ropa de niña por debajo de 10 €' no tiene foto que enseñar).
   */
  async list(userId: number): Promise<InterestView[]> {
    // El producto que da nombre a la variante apuntada (product_id de la variante).
    const variantProduct = alias(product, 'variant_product');
    // La foto DEL COLOR de la variante seguida, cuando la galería la tiene. Mismo criterio que la
    // tarjeta del catálogo (`applyReprImages`, catalog.service.ts) y por el mismo motivo: el color
    // cuelga de la variante, así que enseñar la foto de "el primer color" junto a una talla y un
    // color concretos puede mezclar y enseñar una prenda que no es la que se sigue.
    //
    // El join va por el color CRUDO porque así lo guarda `product_image` —igual que el `color_repr`
    // del catálogo, que sale de `v.color` sin canonizar—. Un `variant.color` NULL no casa con nada
    // y cae al respaldo, que es lo que se quiere.
    const colorImage = alias(productImage, 'color_image');

    const rows = await this.db
      .select({
        interest,
        directProductName: product.name,
        variantProductName: variantProduct.name,
        // Para enlazar a la ficha: el producto apuntado, o el de la variante apuntada. Se
        // seleccionan los dos ids por separado y se resuelven en TypeScript en lugar de con un
        // `coalesce` en SQL, para que drizzle siga convirtiendo el `bigint` a número — postgres.js
        // devuelve como string el bigint que sale de una expresión.
        variantProductId: variant.productId,
        directProductImage: product.imageUrl,
        variantProductImage: variantProduct.imageUrl,
        colorImage: colorImage.url,
        // La sección del PRODUCTO, no la del interés: `interest.section` es la del filtro y viene
        // null en un interés que apunta a una prenda. La usa el hueco de la foto para su fondo.
        directProductSection: product.section,
        variantProductSection: variantProduct.section,
        // La baja de la prenda seguida (#435). Se seleccionan las dos y se resuelven abajo, igual
        // que el resto del par directo/vía-variante: un interés apunta a una o a la otra.
        directProductDelistedAt: product.delistedAt,
        variantProductDelistedAt: variantProduct.delistedAt,
        // La talla CANÓNICA, no la de la tienda (#223). `variant.size` guarda el texto crudo
        // ('2 años (92 cm)'), y esta etiqueta la lee el usuario en dos sitios: su lista de
        // seguimientos y —vía `variantLabel`, ver abajo— el aviso de Telegram. Devolverla cruda
        // aquí la enfrentaba con la que dan las facetas y los filtros, que ya van por `size_canon`
        // (`catalog.service.ts`), y con la que el propio `create()` guarda en `interest.size`.
        //
        // El color se queda CRUDO a propósito: `color_canon` devuelve NULL para lo que no
        // reconoce (#51), así que canonizarlo aquí no lo normalizaría — lo borraría de la etiqueta.
        variantSize: sql<string | null>`size_canon(${variant.size})`,
        variantColor: variant.color,
        retailerName: retailer.name,
      })
      .from(interest)
      .leftJoin(product, eq(product.id, interest.productId))
      .leftJoin(variant, eq(variant.id, interest.variantId))
      .leftJoin(variantProduct, eq(variantProduct.id, variant.productId))
      .leftJoin(
        colorImage,
        and(
          eq(colorImage.productId, variant.productId),
          eq(colorImage.color, variant.color),
          eq(colorImage.position, 0),
        ),
      )
      .leftJoin(
        retailer,
        eq(
          retailer.id,
          sql`coalesce(${interest.retailerId}, ${product.retailerId}, ${variantProduct.retailerId})`,
        ),
      )
      .where(and(eq(interest.userId, userId), eq(interest.active, true)))
      .orderBy(desc(interest.createdAt));

    return rows.map((r) => ({
      ...r.interest,
      retailerName: r.retailerName ?? null,
      productName: r.directProductName ?? r.variantProductName ?? null,
      variantLabel: variantLabel(r.variantSize, r.variantColor),
      // Las mismas dos piezas que acaban de armar la etiqueta, sueltas (#297): la SPA capitaliza
      // el color para que la ficha y el modal no digan 'Rosa' y 'rosa' a pocos centímetros, y esto
      // le evita tener que partir `variantLabel` por su separador o rehacer `size_canon` en TS.
      variantSize: r.variantSize,
      variantColor: r.variantColor,
      targetProductId: r.interest.productId ?? r.variantProductId ?? null,
      // La del color seguido si la galería la tiene; si no, la principal del producto. La galería
      // la estrenan las fichas según se les vuelve a pedir el detalle, así que el respaldo no es
      // un caso raro.
      imageUrl: r.colorImage ?? r.directProductImage ?? r.variantProductImage ?? null,
      productSection: r.directProductSection ?? r.variantProductSection ?? null,
      // Se mira la marca de LA MISMA prenda que `targetProductId`, no un `??` entre las dos: aquí
      // un NULL significa «no está de baja», no «no hay dato», así que encadenarlas haría que un
      // interés con producto vivo Y variante de otro producto de baja se pintara apagado. Un
      // interés por filtros no apunta a ninguna prenda y sale `false`, que es lo correcto:
      // 'toda la ropa de niña' no puede estar de baja.
      delisted:
        (r.interest.productId !== null ? r.directProductDelistedAt : r.variantProductDelistedAt) !==
        null,
    }));
  }

  async create(userId: number, dto: CreateInterestDto): Promise<Interest> {
    if (!this.hasSignal(dto)) {
      throw new BadRequestException(
        'El interés necesita al menos un objetivo (producto/variante/tienda) o un filtro (género/sección/categoría/talla/color).',
      );
    }
    // Un color que no tiene etiqueta canónica no puede guardarse tal cual: `color_canon` devuelve
    // NULL para él (#51), y en `matching.service.ts` un `interest.color` NULL significa «cualquier
    // color». O sea, pedir avisos del '771' de Zara habría suscrito al usuario a TODOS los colores,
    // en silencio y más ancho de lo que pidió. La SPA ya no lo ofrece —desaparece de la faceta—,
    // pero esta API acepta texto libre.
    if (dto.color !== undefined && dto.color !== null && !(await this.hasCanonColor(dto.color))) {
      throw new BadRequestException(
        `El color '${dto.color}' no identifica ningún color: no puede usarse como filtro.`,
      );
    }

    // Alta O reactivación, en una sola sentencia (#149). El alcance —las nueve columnas de
    // `interest_alcance_uniq`, migración 0025— identifica al interés, así que volver a seguir algo
    // que ya se seguía recupera LA MISMA FILA. Importa por lo que cuelga de su id: `notification`
    // guarda ahí los avisos entregados, y con ellos el `UNIQUE (interest_id, variant_id,
    // price_event_key)` que impide repetir el aviso del mismo evento de precio. Con una fila nueva
    // el id cambiaría y esa protección se perdería igual que cuando el borrado era físico.
    //
    // En una sentencia y no «buscar y luego insertar» a propósito: dos POST simultáneos del mismo
    // alcance no pueden partir el historial en dos intereses equivalentes.
    //
    // Se actualiza la REGLA (umbral, base de comparación, ventana) porque volver a seguir con otro
    // umbral es cambiar de opinión sobre el mismo seguimiento. No se toca `created_at`: un
    // seguimiento recuperado vuelve a su sitio en la lista (ordenada por fecha) y no al principio.
    const [row] = await this.db
      .insert(interest)
      .values({
        userId,
        retailerId: dto.retailerId ?? null,
        productId: dto.productId ?? null,
        variantId: dto.variantId ?? null,
        gender: dto.gender ?? null,
        section: dto.section ?? null,
        category: dto.category ?? null,
        // La talla se guarda canónica (#43): el chip del filtro ya lo es, pero un alta por API con el
        // texto crudo de la tienda ('26 (16,3 cm)') tiene que seguir a la misma prenda que un '26'.
        size: dto.size ? sql<string>`size_canon(${dto.size})` : null,
        // Y el color igual (#49): 'VERDE' y 'Verde' son el mismo color, y el interés tiene que
        // seguir a la misma prenda venga como venga.
        color: dto.color ? sql<string>`color_canon(${dto.color})` : null,
        // numeric() de Drizzle se envía como string; DEFAULT si no se especifica.
        ...(dto.minDiscountPct !== undefined ? { minDiscountPct: String(dto.minDiscountPct) } : {}),
        ...(dto.compareBase !== undefined ? { compareBase: dto.compareBase } : {}),
        ...(dto.windowDays !== undefined ? { windowDays: dto.windowDays } : {}),
      })
      // El árbitro se infiere por la lista de columnas y no por el nombre de la restricción porque
      // Drizzle no sabe expresar `ON CONFLICT ON CONSTRAINT`, y la respuesta de esta API es la fila
      // que Drizzle mapea a camelCase — con SQL crudo cambiaría el contrato. Comprobado contra
      // Postgres 16 que la inferencia SÍ encuentra un índice `NULLS NOT DISTINCT` a partir de sus
      // columnas, que era la duda: dos filas con NULL en el mismo hueco colisionan como deben.
      .onConflictDoUpdate({
        target: [
          interest.userId,
          interest.retailerId,
          interest.productId,
          interest.variantId,
          interest.gender,
          interest.section,
          interest.category,
          interest.size,
          interest.color,
        ],
        // `excluded` es la fila que se habría insertado, así que la regla queda exactamente igual
        // que si el interés se hubiera creado de cero: lo que el DTO omite recupera el DEFAULT de
        // la 0004. Un POST describe el seguimiento entero, no un parche sobre el anterior.
        set: {
          active: true,
          minDiscountPct: sql`excluded.min_discount_pct`,
          compareBase: sql`excluded.compare_base`,
          windowDays: sql`excluded.window_days`,
        },
      })
      .returning();
    return row;
  }

  /**
   * Baja LÓGICA del interés (#149). No borra la fila: `notification.interest_id` es
   * `ON DELETE CASCADE`, así que un borrado físico se llevaba los avisos ya entregados y, con
   * ellos, el `UNIQUE (interest_id, variant_id, price_event_key)` que impide repetir el aviso del
   * mismo evento de precio. El usuario entiende este clic como «ya no me interesa esto», no como
   * «bórrame el historial».
   *
   * Sigue devolviendo 404 cuando no cambia nada —incluido un interés ya inactivo— porque `list()`
   * solo muestra los activos: lo que no está en la lista del usuario no puede darse de baja.
   */
  async remove(userId: number, id: number): Promise<void> {
    const deactivated = await this.db
      .update(interest)
      .set({ active: false })
      .where(and(eq(interest.id, id), eq(interest.userId, userId), eq(interest.active, true)))
      .returning({ id: interest.id });
    if (deactivated.length === 0) {
      throw new NotFoundException(`Interés ${id} no encontrado`);
    }
  }

  /** ¿Este color tiene etiqueta canónica? (`color_canon` la niega devolviendo NULL — ver #51). */
  private async hasCanonColor(color: string): Promise<boolean> {
    const rows = (await this.db.execute(
      sql`SELECT color_canon(${color}) AS canon`,
    )) as unknown as Record<string, unknown>[];
    return rows[0]?.canon != null;
  }

  private hasSignal(dto: CreateInterestDto): boolean {
    return [
      dto.retailerId,
      dto.productId,
      dto.variantId,
      dto.gender,
      dto.section,
      dto.category,
      dto.size,
      dto.color,
    ].some((v) => v !== undefined && v !== null && v !== '');
  }
}

/**
 * Etiqueta legible de una variante apuntada. `null` si no hay talla ni color (sin objetivo).
 * Exportada para que los avisos de Telegram nombren la variante igual que la web.
 *
 * **La talla se le pasa ya canónica**, y eso es contrato del llamante, no de aquí: esta función
 * solo concatena. Los TRES llamantes la traen de la base con `size_canon` —el SELECT de `list()`,
 * el de `findCandidates` (`matching.service.ts`) y el del detalle de producto
 * (`catalog.service.ts`)— porque canonizar en TypeScript sería una segunda definición de «misma
 * talla». Pasarle la cruda es lo que causó #223.
 *
 * El tercero se sumó en #248: la ficha servía la etiqueta al modal de «Seguir esta variante»
 * rehaciéndola en el frontend con la talla cruda, así que el usuario confirmaba una talla y su
 * lista le enseñaba otra. Que la sirva la API es lo que impide que vuelva a haber un cuarto sitio
 * donde el formato se reinvente.
 */
export function variantLabel(size: string | null, color: string | null): string | null {
  const parts = [size ? `Talla ${size}` : null, color].filter((p): p is string => !!p);
  return parts.length ? parts.join(' · ') : null;
}
