import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { sql, type SQL } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { classifyHonesty, HONESTY_WINDOW_DAYS } from '../matching/deal-rule';
import { honestDiscountSql, isRealDealSql, type DealSqlColumns } from '../matching/deal-rule.sql';
import type {
  Facets,
  PricePoint,
  ProductDetail,
  ProductImageRef,
  ProductListItem,
  ProductListResult,
  RetailerFacet,
  VariantWithPrice,
} from './catalog.types';
import type { ProductQueryDto } from './dto/product-query.dto';

/**
 * Columnas de la variante "mejor oferta" ya agregada, contra las que se evalúa la honestidad en
 * SQL. Son exactamente las mismas que se le pasan a `classifyHonesty` más abajo (`list_from`
 * incluido): si aquí se colara otra columna, el filtro y la etiqueta hablarían de precios distintos.
 */
const DEAL_COLUMNS: DealSqlColumns = {
  price: sql`price_repr`,
  listPrice: sql`list_from`,
  recentMin: sql`recent_min_repr`,
  maxObserved: sql`max_observed_repr`,
  priorPoints: sql`prior_points_repr`,
};

/**
 * Plegado de texto para buscar sin distinguir mayúsculas ni acentos.
 *
 * A propósito **sin `unaccent` ni `pg_trgm`**: ambas exigen `CREATE EXTENSION`, que en la Postgres
 * HA del cluster no está garantizado para el usuario de la aplicación, y no merece la pena atar el
 * arranque del servicio a un privilegio que puede no estar. `translate()` es estándar, `IMMUTABLE`
 * y cubre el castellano, que es todo el idioma del catálogo.
 *
 * Sin índice: el catálogo son unos pocos miles de productos y la consulta ya recorre `price_history`
 * entero en la CTE `latest`, así que el plegado no es el cuello de botella. Si algún día lo fuera,
 * la salida es `pg_trgm` + índice GIN sobre esta misma expresión.
 */
function fold(expr: SQL): SQL {
  return sql`translate(lower((${expr})::text),
    'áàäâãéèëêíìïîóòöôõúùüûñç', 'aaaaaeeeeiiiiooooouuuunc')`;
}

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
    const onlyDeals = q.onlyDeals ?? null;

    // Búsqueda por texto: cada palabra debe aparecer en el nombre, la categoría o el género, en
    // cualquier orden ("botas niña" y "niña botas" encuentran lo mismo). El género entra porque es
    // como la gente teclea ("botas niña"), y los nombres que dan las tiendas casi nunca lo llevan.
    // `position()` en vez de `LIKE` para no tener que escapar los comodines de lo que se teclee.
    const terms = (q.q ?? '').split(/\s+/).filter(Boolean);
    const haystack = fold(
      sql`p.name || ' ' || coalesce(p.category, '') || ' ' || coalesce(p.gender, '')`,
    );
    const search = terms.length
      ? sql.join(
          terms.map((t) => sql`position(${fold(sql`${t}`)} in ${haystack}) > 0`),
          sql` AND `,
        )
      : sql`TRUE`;

    // Orden traducido a SQL (whitelist en el DTO). "ofertas" = la oferta **real** primero, después
    // stock, y el descuento honesto (contra el PVP creíble) como criterio; el `discount_pct` que
    // declara la tienda queda de mero desempate porque es justo el dato del que desconfiamos.
    // El id, desempate estable para que la paginación por offset no repita ni se salte filas.
    const orderBy = {
      'ofertas': sql`is_real_deal DESC, any_in_stock DESC, honest_discount DESC NULLS LAST,
                     max_discount DESC NULLS LAST, id`,
      'precio-asc': sql`price_from ASC NULLS LAST, id`,
      'precio-desc': sql`price_from DESC NULLS LAST, id`,
      'descuento': sql`max_discount DESC NULLS LAST, id`,
    }[q.sort];

    const rows = await this.db.execute(sql`
      WITH latest AS (
        SELECT DISTINCT ON (ph.variant_id)
          ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock, ph.scraped_at
        FROM price_history ph
        ORDER BY ph.variant_id, ph.scraped_at DESC
      ),
      stats AS (
        SELECT l.variant_id,
               MIN(h.price) FILTER (
                 WHERE h.scraped_at >= l.scraped_at - make_interval(days => ${HONESTY_WINDOW_DAYS})
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points
        FROM latest l
        JOIN price_history h ON h.variant_id = l.variant_id AND h.scraped_at < l.scraped_at
        GROUP BY l.variant_id
      ),
      matched AS (
        SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
               p.retailer_product_id, p.name, p.gender, p.section, p.category, p.url,
               p.image_url,
               v.id AS variant_id, v.color, l.price, l.list_price, l.discount_pct, l.in_stock,
               s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points
        FROM product p
        JOIN retailer r ON r.id = p.retailer_id
        JOIN variant v ON v.product_id = p.id AND v.delisted_at IS NULL
        JOIN latest l ON l.variant_id = v.id
        LEFT JOIN stats s ON s.variant_id = v.id
        WHERE (${gender}::text IS NULL OR p.gender = ${gender})
          AND (${section}::text IS NULL OR p.section = ${section})
          AND (${category}::text IS NULL OR p.category = ${category})
          AND (${retailer}::text IS NULL OR r.slug = ${retailer})
          AND (${size}::text IS NULL OR v.size = ${size})
          AND (${color}::text IS NULL OR v.color = ${color})
          AND (${inStock}::boolean IS NULL OR l.in_stock = ${inStock})
          AND (${q.activeOnly} = false OR p.delisted_at IS NULL)
          AND ${search}
      ),
      agg AS (
        SELECT id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
               name, gender, section, category, url, image_url,
               MIN(price) AS price_from,
               MAX(discount_pct) AS max_discount,
               (array_agg(list_price ORDER BY in_stock DESC, price ASC))[1] AS list_from,
               (array_agg(discount_pct ORDER BY in_stock DESC, price ASC))[1] AS discount_from,
               -- Estadísticos de la MISMA variante "mejor oferta" que list_from/discount_from,
               -- para clasificar la honestidad de la oferta que se muestra en la tarjeta.
               (array_agg(price ORDER BY in_stock DESC, price ASC))[1] AS price_repr,
               (array_agg(recent_min ORDER BY in_stock DESC, price ASC))[1] AS recent_min_repr,
               (array_agg(max_observed ORDER BY in_stock DESC, price ASC))[1] AS max_observed_repr,
               (array_agg(prior_points ORDER BY in_stock DESC, price ASC))[1] AS prior_points_repr,
               -- ...y su COLOR, para que la foto de la tarjeta sea la de ese mismo color y no la de
               -- otro cualquiera: el precio cuelga de la variante (talla+color), así que enseñar la
               -- foto del "primer color" junto al precio de la variante más barata puede mezclar.
               (array_agg(color ORDER BY in_stock DESC, price ASC))[1] AS color_repr,
               BOOL_OR(in_stock) AS any_in_stock,
               COUNT(variant_id) AS variant_count
        FROM matched
        GROUP BY id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
                 name, gender, section, category, url, image_url
      ),
      -- La honestidad se decide sobre las columnas *_repr, que solo existen tras el GROUP BY, así
      -- que va en su propia CTE: desde aquí ya se puede filtrar y ordenar por ella antes del
      -- LIMIT, que es justo lo que el TypeScript, evaluado sobre la página ya recortada, no puede.
      scored AS (
        SELECT agg.*,
               ${isRealDealSql(DEAL_COLUMNS)}   AS is_real_deal,
               ${honestDiscountSql(DEAL_COLUMNS)} AS honest_discount
        FROM agg
      )
      SELECT * FROM scored
      WHERE (${onlyDeals}::boolean IS NOT TRUE OR is_real_deal)
      ORDER BY ${orderBy}
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
      imageUrl: (row.image_url as string | null) ?? null,
      colorRepr: (row.color_repr as string | null) ?? null,
      priceFrom: (row.price_from as string | null) ?? null,
      listFrom: (row.list_from as string | null) ?? null,
      discountFrom: (row.discount_from as string | null) ?? null,
      maxDiscount: (row.max_discount as string | null) ?? null,
      honesty: classifyHonesty({
        price: (row.price_repr as string | null) ?? null,
        listPrice: (row.list_from as string | null) ?? null,
        recentMin: (row.recent_min_repr as string | null) ?? null,
        maxObserved: (row.max_observed_repr as string | null) ?? null,
        priorPoints: Number(row.prior_points_repr ?? 0),
        minDiscountPct: 0,
        compareBase: 'recent_min',
      }),
      anyInStock: Boolean(row.any_in_stock),
      variantCount: Number(row.variant_count),
    }));

    await this.applyReprImages(items);
    return { items, limit: q.limit, offset: q.offset };
  }

  /**
   * Sustituye `imageUrl` por la foto del color de la variante "mejor oferta", cuando la hay.
   *
   * Va en una segunda consulta y no como JOIN dentro de la query grande a propósito: aquí está
   * acotada a los productos de UNA página (`limit`), mientras que dentro de `matched` se pagaría
   * por cada fila variante×precio de todo el catálogo filtrado. `product.image_url` sigue siendo
   * el respaldo para las fichas que aún no tienen galería (la estrenan con el refresco del detalle).
   */
  private async applyReprImages(items: ProductListItem[]): Promise<void> {
    const wanted = items.filter((it) => it.colorRepr !== null);
    if (wanted.length === 0) return;

    // Dos trampas juntas al pasar arrays: `sql.param()` es obligatorio, porque un array suelto en
    // una plantilla de drizzle se expande a N parámetros sueltos (`$1, $2`) y el cast a `bigint[]`
    // se queja de literal malformado; y los ids van como TEXTO, porque postgres.js no sabe
    // serializar un array de números (falla en el Bind). Postgres los castea sin problema.
    const ids = wanted.map((it) => String(it.id));
    const colors = wanted.map((it) => it.colorRepr as string);
    const rows = (await this.db.execute(sql`
      SELECT i.product_id, i.url
      FROM unnest(${sql.param(ids)}::bigint[], ${sql.param(colors)}::text[])
             AS want(product_id, color)
      JOIN product_image i
        ON i.product_id = want.product_id AND i.color = want.color AND i.position = 0
    `)) as unknown as Record<string, unknown>[];

    const byProduct = new Map(rows.map((r) => [Number(r.product_id), String(r.url)]));
    for (const item of items) {
      const url = byProduct.get(item.id);
      if (url) item.imageUrl = url;
    }
  }

  async getProduct(id: number): Promise<ProductDetail> {
    const [head] = (await this.db.execute(sql`
      SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
             p.retailer_product_id, p.name, p.gender, p.section, p.category, p.url,
             p.image_url
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
      ),
      stats AS (
        SELECT l.variant_id,
               MIN(h.price) FILTER (
                 WHERE h.scraped_at >= l.scraped_at - make_interval(days => ${HONESTY_WINDOW_DAYS})
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points
        FROM latest l
        JOIN price_history h ON h.variant_id = l.variant_id AND h.scraped_at < l.scraped_at
        GROUP BY l.variant_id
      )
      SELECT v.id, v.retailer_variant_id, v.size, v.color, v.sku, v.url, v.delisted_at,
             l.price, l.list_price, l.discount_pct, l.in_stock, l.scraped_at,
             s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points
      FROM variant v
      LEFT JOIN latest l ON l.variant_id = v.id
      LEFT JOIN stats s ON s.variant_id = v.id
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
      honesty: classifyHonesty({
        price: (row.price as string | null) ?? null,
        listPrice: (row.list_price as string | null) ?? null,
        recentMin: (row.recent_min as string | null) ?? null,
        maxObserved: (row.max_observed as string | null) ?? null,
        priorPoints: Number(row.prior_points ?? 0),
        minDiscountPct: 0,
        compareBase: 'recent_min',
      }),
    }));

    // Galería completa: la ficha la filtra por el color seleccionado, para que la foto cambie a
    // la vez que el precio. `color NULLS FIRST` deja delante las fotos sin color atribuible, que
    // son las que sirven de respaldo cuando el color elegido no tiene ninguna.
    const imageRows = (await this.db.execute(sql`
      SELECT color, url
      FROM product_image
      WHERE product_id = ${id}
      ORDER BY color NULLS FIRST, position
    `)) as unknown as Record<string, unknown>[];

    const images: ProductImageRef[] = imageRows.map((row) => ({
      color: (row.color as string | null) ?? null,
      url: String(row.url),
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
      imageUrl: (head.image_url as string | null) ?? null,
      variants,
      images,
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

    // Tallas/colores: valores distintos entre variantes vivas de productos activos.
    const pickVariant = async (column: 'size' | 'color'): Promise<string[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT ${sql.raw(`v.${column}`)} AS value
        FROM variant v
        JOIN product p ON p.id = v.product_id
        WHERE ${sql.raw(`v.${column}`)} IS NOT NULL
          AND v.delisted_at IS NULL AND p.delisted_at IS NULL
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    const pickRetailers = async (): Promise<RetailerFacet[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT r.slug, r.name
        FROM retailer r
        JOIN product p ON p.retailer_id = r.id AND p.delisted_at IS NULL
        ORDER BY r.name
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => ({ slug: String(r.slug), name: String(r.name) }));
    };

    const [genders, sections, categories, sizes, colors, retailers] = await Promise.all([
      pick('gender'),
      pick('section'),
      pick('category'),
      pickVariant('size'),
      pickVariant('color'),
      pickRetailers(),
    ]);
    return { genders, sections, categories, sizes, colors, retailers };
  }
}
