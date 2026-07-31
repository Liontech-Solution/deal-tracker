import { BadRequestException, Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, desc, eq, sql } from 'drizzle-orm';
import { alias } from 'drizzle-orm/pg-core';

import { Database, DRIZZLE } from '../database/database.module';
import { interest, product, retailer, variant, type Interest } from '../database/schema';
import type { CreateInterestDto } from './dto/create-interest.dto';
import type { InterestView } from './interests.types';

@Injectable()
export class InterestsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  /**
   * Lista los intereses activos del usuario, enriquecidos con los nombres del objetivo
   * (producto/variante/tienda) cuando apuntan a uno. Las uniones son por id lógico y LEFT
   * (sin FK dura desde `interest`): si el objetivo ya no existe, los nombres vienen `null`.
   */
  async list(userId: number): Promise<InterestView[]> {
    // El producto que da nombre a la variante apuntada (product_id de la variante).
    const variantProduct = alias(product, 'variant_product');

    const rows = await this.db
      .select({
        interest,
        directProductName: product.name,
        variantProductName: variantProduct.name,
        variantSize: variant.size,
        variantColor: variant.color,
        retailerName: retailer.name,
      })
      .from(interest)
      .leftJoin(product, eq(product.id, interest.productId))
      .leftJoin(variant, eq(variant.id, interest.variantId))
      .leftJoin(variantProduct, eq(variantProduct.id, variant.productId))
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
    }));
  }

  async create(userId: number, dto: CreateInterestDto): Promise<Interest> {
    if (!this.hasSignal(dto)) {
      throw new BadRequestException(
        'El interés necesita al menos un objetivo (producto/variante/tienda) o un filtro (género/sección/categoría/talla/color).',
      );
    }

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
        color: dto.color ?? null,
        // numeric() de Drizzle se envía como string; DEFAULT si no se especifica.
        ...(dto.minDiscountPct !== undefined ? { minDiscountPct: String(dto.minDiscountPct) } : {}),
        ...(dto.compareBase !== undefined ? { compareBase: dto.compareBase } : {}),
        ...(dto.windowDays !== undefined ? { windowDays: dto.windowDays } : {}),
      })
      .returning();
    return row;
  }

  async remove(userId: number, id: number): Promise<void> {
    const deleted = await this.db
      .delete(interest)
      .where(and(eq(interest.id, id), eq(interest.userId, userId)))
      .returning({ id: interest.id });
    if (deleted.length === 0) {
      throw new NotFoundException(`Interés ${id} no encontrado`);
    }
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
 */
export function variantLabel(size: string | null, color: string | null): string | null {
  const parts = [size ? `Talla ${size}` : null, color].filter((p): p is string => !!p);
  return parts.length ? parts.join(' · ') : null;
}
