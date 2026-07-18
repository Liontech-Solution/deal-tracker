import { BadRequestException, Inject, Injectable, NotFoundException } from '@nestjs/common';
import { and, desc, eq } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { interest, type Interest } from '../database/schema';
import type { CreateInterestDto } from './dto/create-interest.dto';

@Injectable()
export class InterestsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  list(userId: number): Promise<Interest[]> {
    return this.db
      .select()
      .from(interest)
      .where(and(eq(interest.userId, userId), eq(interest.active, true)))
      .orderBy(desc(interest.createdAt));
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
        size: dto.size ?? null,
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
