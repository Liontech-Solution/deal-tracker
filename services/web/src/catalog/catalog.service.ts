import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { sql } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import type {
  Facets,
  PricePoint,
  ProductDetail,
  ProductListItem,
  ProductListResult,
  VariantWithPrice,
} from './catalog.types';
import type { ProductQueryDto } from './dto/product-query.dto';

/**
 * Lectura del catálogo (tablas que escribe el scraper). "Último precio" por variante se
 * resuelve con `DISTINCT ON (variant_id) ... ORDER BY scraped_at DESC` sobre `price_history`.
 */
@Injectable()
export class CatalogService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  async listProducts(q: ProductQueryDto): Promise<ProductListResult> {
    const gender = q.gender ?? null;
    const section = q.section ?? null;
    const category = q.category ?? null;
    const size = q.size ?? null;
    const color = q.color ?? null;
    const retailer = q.retailer ?? null;
    const inStock = q.inStock ?? null;

    const rows = await this.db.execute(sql`
      WITH latest AS (
        SELECT DISTINCT ON (ph.variant_id)
          ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock
        FROM price_history ph
        ORDER BY ph.variant_id, ph.scraped_at DESC
      )
      SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
             p.retailer_product_id, p.name, p.gender, p.section, p.category, p.url,
             MIN(l.price) AS price_from,
             BOOL_OR(l.in_stock) AS any_in_stock,
             COUNT(v.id) AS variant_count
      FROM product p
      JOIN retailer r ON r.id = p.retailer_id
      JOIN variant v ON v.product_id = p.id AND v.delisted_at IS NULL
      JOIN latest l ON l.variant_id = v.id
      WHERE (${gender}::text IS NULL OR p.gender = ${gender})
        AND (${section}::text IS NULL OR p.section = ${section})
        AND (${category}::text IS NULL OR p.category = ${category})
        AND (${retailer}::text IS NULL OR r.slug = ${retailer})
        AND (${size}::text IS NULL OR v.size = ${size})
        AND (${color}::text IS NULL OR v.color = ${color})
        AND (${inStock}::boolean IS NULL OR l.in_stock = ${inStock})
        AND (${q.activeOnly} = false OR p.delisted_at IS NULL)
      GROUP BY p.id, r.id
      ORDER BY p.id
      LIMIT ${q.limit} OFFSET ${q.offset}
    `);

    const items: ProductListItem[] = (rows as unknown as Record<string, unknown>[]).map((row) => ({
      id: Number(row.id),
      retailerId: Number(row.retailer_id),
      retailerSlug: String(row.retailer_slug),
      retailerName: String(row.retailer_name),
      retailerProductId: String(row.retailer_product_id),
      name: String(row.name),
      gender: (row.gender as string | null) ?? null,
      section: (row.section as string | null) ?? null,
      category: (row.category as string | null) ?? null,
      url: (row.url as string | null) ?? null,
      priceFrom: (row.price_from as string | null) ?? null,
      anyInStock: Boolean(row.any_in_stock),
      variantCount: Number(row.variant_count),
    }));

    return { items, limit: q.limit, offset: q.offset };
  }

  async getProduct(id: number): Promise<ProductDetail> {
    const [head] = (await this.db.execute(sql`
      SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
             p.retailer_product_id, p.name, p.gender, p.section, p.category, p.url
      FROM product p
      JOIN retailer r ON r.id = p.retailer_id
      WHERE p.id = ${id}
    `)) as unknown as Record<string, unknown>[];

    if (!head) {
      throw new NotFoundException(`Producto ${id} no encontrado`);
    }

    const variantRows = (await this.db.execute(sql`
      WITH latest AS (
        SELECT DISTINCT ON (ph.variant_id)
          ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock, ph.scraped_at
        FROM price_history ph
        ORDER BY ph.variant_id, ph.scraped_at DESC
      )
      SELECT v.id, v.retailer_variant_id, v.size, v.color, v.sku, v.url, v.delisted_at,
             l.price, l.list_price, l.discount_pct, l.in_stock, l.scraped_at
      FROM variant v
      LEFT JOIN latest l ON l.variant_id = v.id
      WHERE v.product_id = ${id}
      ORDER BY v.id
    `)) as unknown as Record<string, unknown>[];

    const variants: VariantWithPrice[] = variantRows.map((row) => ({
      id: Number(row.id),
      retailerVariantId: String(row.retailer_variant_id),
      size: (row.size as string | null) ?? null,
      color: (row.color as string | null) ?? null,
      sku: (row.sku as string | null) ?? null,
      url: (row.url as string | null) ?? null,
      delisted: row.delisted_at != null,
      price: (row.price as string | null) ?? null,
      listPrice: (row.list_price as string | null) ?? null,
      discountPct: (row.discount_pct as string | null) ?? null,
      inStock: row.in_stock == null ? null : Boolean(row.in_stock),
      scrapedAt: row.scraped_at ? new Date(row.scraped_at as string).toISOString() : null,
    }));

    return {
      id: Number(head.id),
      retailerId: Number(head.retailer_id),
      retailerSlug: String(head.retailer_slug),
      retailerName: String(head.retailer_name),
      retailerProductId: String(head.retailer_product_id),
      name: String(head.name),
      gender: (head.gender as string | null) ?? null,
      section: (head.section as string | null) ?? null,
      category: (head.category as string | null) ?? null,
      url: (head.url as string | null) ?? null,
      variants,
    };
  }

  async getPriceHistory(variantId: number): Promise<PricePoint[]> {
    const [exists] = (await this.db.execute(
      sql`SELECT 1 AS ok FROM variant WHERE id = ${variantId}`,
    )) as unknown as Record<string, unknown>[];
    if (!exists) {
      throw new NotFoundException(`Variante ${variantId} no encontrada`);
    }

    const rows = (await this.db.execute(sql`
      SELECT price, list_price, discount_pct, in_stock, scraped_at
      FROM price_history
      WHERE variant_id = ${variantId}
      ORDER BY scraped_at ASC
    `)) as unknown as Record<string, unknown>[];

    return rows.map((row) => ({
      price: String(row.price),
      listPrice: (row.list_price as string | null) ?? null,
      discountPct: (row.discount_pct as string | null) ?? null,
      inStock: Boolean(row.in_stock),
      scrapedAt: new Date(row.scraped_at as string).toISOString(),
    }));
  }

  async getFacets(): Promise<Facets> {
    const pick = async (column: 'gender' | 'section' | 'category'): Promise<string[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT ${sql.raw(column)} AS value
        FROM product
        WHERE ${sql.raw(column)} IS NOT NULL AND delisted_at IS NULL
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    const [genders, sections, categories] = await Promise.all([
      pick('gender'),
      pick('section'),
      pick('category'),
    ]);
    return { genders, sections, categories };
  }
}
