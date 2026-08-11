import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { sql, type SQL } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { variantLabel } from '../interests/interests.service';
import { classifyHonesty, HONESTY_WINDOW_DAYS } from '../matching/deal-rule';
import { honestDiscountSql, isRealDealSql, type DealSqlColumns } from '../matching/deal-rule.sql';
import { GENERO_UNISEX, generoCondition } from './gender.sql';
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
import type { BarefootFilter, ProductQueryDto } from './dto/product-query.dto';

/** Sección donde la marca barefoot aplica. En el resto (`ropa`) la columna es NULL. */
const SECCION_CALZADO = 'zapateria';

/**
 * Condición SQL del filtro barefoot (#30), con `alias` como alias de la tabla `product`.
 *
 * El caso por defecto (`si`) NO es "barefoot = 'si'" a secas: es **toda la ropa más el calzado
 * respetuoso**. La ropa lleva NULL porque la pregunta no le aplica, y `NULL = 'si'` es NULL, o sea
 * falso, así que un filtro ingenuo escondería el catálogo entero de ropa. `IS DISTINCT FROM` en vez
 * de `<>` por la misma razón: un producto con `section` NULL debe pasar, no evaporarse.
 */
export function barefootCondition(filter: BarefootFilter, alias: string): SQL {
  const seccion = sql.raw(`${alias}.section`);
  const marca = sql.raw(`${alias}.barefoot`);
  if (filter === 'all') return sql`true`;
  if (filter === 'si') {
    return sql`(${seccion} IS DISTINCT FROM ${SECCION_CALZADO} OR ${marca} = 'si')`;
  }
  return sql`${marca} = ${filter}`;
}

/**
 * Condición SQL de un eje transversal de `product_tag` (#180), con `alias` como alias de `product`.
 *
 * `EXISTS` y no un `JOIN`: la tabla tiene una fila por (producto, eje), así que unirla duplicaría
 * las filas del listado el día que un producto lleve dos ejes — y el listado ya agrega por producto
 * para elegir la variante representativa.
 *
 * Apagado devuelve `true` en vez de omitirse en quien llama, para que el filtro se componga igual
 * que `barefootCondition` y no haya dos formas de montar el `WHERE`.
 */
export function tagCondition(tag: string | undefined, alias: string): SQL {
  if (!tag) return sql`true`;
  const id = sql.raw(`${alias}.id`);
  return sql`EXISTS (SELECT 1 FROM product_tag pt WHERE pt.product_id = ${id} AND pt.tag = ${tag})`;
}

/** Eje que la SPA ofrece como interruptor. El vocabulario lo fija `scraper/tags.py`. */
export const TAG_DEPORTIVA = 'deportiva';

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
 *
 * LAS MAYÚSCULAS ACENTUADAS VAN EN LA TABLA, y no basta con el `lower()` de delante (#105). En la
 * base del cluster el ctype es `C`, y con ese ctype `lower()` **no baja las letras acentuadas**:
 * `lower('PANTALÓN')` da 'pantalÓn', la 'Ó' no está en la mitad izquierda de esta tabla y el
 * producto se queda fuera de la búsqueda. No es un caso de borde: el 02/08/2026 había **694
 * productos vivos con mayúscula acentuada en el nombre** en `dev` (zara 679, lefties 11, c-and-a 3,
 * sfera 1), y son justo los de las tiendas que escriben el nombre entero en mayúsculas.
 *
 * La mitad derecha repite el mismo alfabeto sin acentos: aquí se pliegan las dos cosas —caja y
 * acento— porque buscar «pantalon» debe encontrar «PANTALÓN». Es lo contrario de lo que hacen
 * `size_canon` y `color_canon`, que pliegan la caja y **conservan** el acento (0015 y 0021): el
 * chip de la faceta es una etiqueta que se enseña, y esto es un buscador.
 *
 * De ahí que el `translate` vaya DESPUÉS del `lower` aquí y ANTES en las dos funciones canónicas, y
 * no es un descuido: allí el plegado tiene que ocurrir antes para que las reglas de la talla vean
 * ya 'años' y no 'aÑos'; aquí basta con repescar lo que el `lower` no bajó, porque el destino es
 * ASCII de todas formas.
 */
function fold(expr: SQL): SQL {
  return sql`translate(lower((${expr})::text),
    'áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ',
    'aaaaaeeeeiiiiooooouuuuncaaaaaeeeeiiiiooooouuuunc')`;
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
               p.retailer_product_id, p.name, p.gender, p.section, p.category, p.barefoot, p.url,
               p.image_url,
               v.id AS variant_id, v.color, l.price, l.list_price, l.discount_pct, l.in_stock,
               s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points
        FROM product p
        JOIN retailer r ON r.id = p.retailer_id
        JOIN variant v ON v.product_id = p.id AND v.delisted_at IS NULL
        JOIN latest l ON l.variant_id = v.id
        LEFT JOIN stats s ON s.variant_id = v.id
        WHERE ${generoCondition(sql`${gender}::text`, sql.raw('p.gender'))}
          AND (${section}::text IS NULL OR p.section = ${section})
          AND (${category}::text IS NULL OR p.category = ${category})
          AND (${retailer}::text IS NULL OR r.slug = ${retailer})
          -- Talla canónica (#43): variant.size guarda el texto de la tienda, donde la misma talla
          -- aparece como '26', '26 (16,3 cm)' y '26 (16.3 cm)'. Se canonicaliza también lo que llega
          -- por query string, así que los enlaces antiguos con la talla cruda siguen vivos.
          --
          --
          -- Esta igualdad es la que justifica el índice por expresión de la migración 0014: sin él,
          -- la función se evalúa una vez por variante y esta consulta pasa de 1,4 ms a 1 segundo
          -- (medido sobre una copia de dev con 33.311 variantes).
          AND (${size}::text IS NULL OR size_canon(v.size) = size_canon(${size}))
          -- Color canónico (#49), mismo trato y misma razón que la talla: la tienda escribe 'Verde'
          -- y 'VERDE' para el mismo color. Canonicalizar también lo que llega por query string deja
          -- vivos los enlaces antiguos, y es lo que justifica el índice de la migración 0015.
          AND (${color}::text IS NULL OR color_canon(v.color) = color_canon(${color}))
          AND (${inStock}::boolean IS NULL OR l.in_stock = ${inStock})
          AND (${q.activeOnly} = false OR p.delisted_at IS NULL)
          AND ${search}
          AND ${barefootCondition(q.barefoot, 'p')}
          AND ${tagCondition(q.deportiva ? TAG_DEPORTIVA : undefined, 'p')}
      ),
      agg AS (
        SELECT id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
               name, gender, section, category, barefoot, url, image_url,
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
               BOOL_OR(in_stock) AS any_in_stock
        FROM matched
        GROUP BY id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
                 name, gender, section, category, barefoot, url, image_url
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
      SELECT scored.*,
             -- Los ejes transversales van en el SELECT de fuera, después del LIMIT, para que la
             -- subconsulta se evalúe sobre la página y no sobre el catálogo entero. Un ARRAY vacío
             -- es lo normal: hoy solo hay un eje y solo lo alimentan tres tiendas.
             ARRAY(SELECT pt.tag FROM product_tag pt
                    WHERE pt.product_id = scored.id ORDER BY pt.tag) AS tags,
             -- Prendas comprables, no filas: la misma clave con la que la ficha colapsa las caras
             -- duplicadas (#108). Sin esto, un producto de Lefties con las 22 tallas publicadas dos
             -- veces declara 44 variantes. Los coalesce evitan que una fila con talla, color y URL a
             -- NULL forme una ROW toda nula, que COUNT no contaría.
             --
             -- Vive aquí fuera —después del LIMIT, como los ejes transversales— y no dentro de
             -- agg, por lo que midió #307: ahí este COUNT(DISTINCT ROW(...)) obliga a ordenar TODAS
             -- las variantes vivas por un valor calculado (159.037 en prod, con derrame a disco) y
             -- la petición sin filtros tardaba 24 s en vez de 0,33 s. Con cualquier filtro puesto no
             -- se notaba, porque matched colapsa a unos cientos de filas.
             --
             -- Al salir de matched hay que repetir sus filtros DE VARIANTE, que son los únicos que
             -- cambian el recuento; los de producto (género, sección, categoría, tienda, búsqueda,
             -- barefoot, deportiva) no lo tocan. El delisted_at IS NULL va siempre: activeOnly solo
             -- levanta el filtro del producto, nunca el de la variante.
             --
             -- El "ORDER BY ... LIMIT 1" es el espejo por variante del CTE latest, y la duplicación
             -- es a sabiendas: correlar contra latest cuesta 603 ms por página frente a los 16 ms de
             -- esta forma, porque un CTE materializado no tiene índice y se recorre entero una vez
             -- por fila. Lo que sujeta que las dos digan lo mismo es el test de inStock sobre el
             -- fixture de dos SKU.
             (SELECT COUNT(DISTINCT (coalesce(size_canon(v2.size), ''),
                                     coalesce(color_canon(v2.color), ''),
                                     coalesce(COALESCE(v2.url, scored.url), '')))
                FROM variant v2
               WHERE v2.product_id = scored.id
                 AND v2.delisted_at IS NULL
                 AND EXISTS (SELECT 1 FROM price_history ph WHERE ph.variant_id = v2.id)
                 AND (${size}::text IS NULL OR size_canon(v2.size) = size_canon(${size}))
                 AND (${color}::text IS NULL OR color_canon(v2.color) = color_canon(${color}))
                 AND (${inStock}::boolean IS NULL
                      OR (SELECT ph.in_stock FROM price_history ph
                           WHERE ph.variant_id = v2.id
                           ORDER BY ph.scraped_at DESC LIMIT 1) = ${inStock})
             ) AS variant_count
        FROM scored
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
      barefoot: (row.barefoot as string | null) ?? null,
      tags: (row.tags as string[] | null) ?? [],
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
             p.retailer_product_id, p.name, p.gender, p.section, p.category, p.barefoot, p.url,
             p.image_url,
             ARRAY(SELECT pt.tag FROM product_tag pt
                    WHERE pt.product_id = p.id ORDER BY pt.tag) AS tags
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
      ),
      -- Una fila por PRENDA COMPRABLE, no por variante (#108). Lefties, H&M e Hipercor publican
      -- la misma talla y color con dos SKU distintos: los dos son reales y estables, así que
      -- entran los dos en la base, pero la ficha tiene que enseñar una sola fila o el usuario ve
      -- la misma talla dos veces y el precio que se pinta puede ser el de la cara agotada.
      --
      -- La URL entra en la clave a propósito: es lo que separa las dos caras de Lefties o
      -- Hipercor —que comparten ficha en la tienda, así que colapsarlas no le quita al usuario
      -- ningún sitio al que ir— de los dos ARTÍCULOS distintos que H&M publica con el mismo
      -- modelo y el mismo nombre de color, cada uno con su propia ficha (medido en dev el
      -- 03/08/2026: 803 grupos así). Añadirla solo puede partir grupos, nunca unirlos.
      --
      -- Se agrupa por coalesce(v.url, '') y no por coalesce(v.url, p.url) porque aquí todas las
      -- filas son del
      -- mismo producto: el respaldo sería el mismo para todas y solo hace falta que los NULL
      -- caigan juntos.
      --
      -- La baja también parte el grupo: una cara dada de baja no debe absorber a una viva.
      prenda AS (
        SELECT (array_agg(v.id ORDER BY l.in_stock DESC NULLS LAST, l.price ASC NULLS LAST, v.id))[1]
                 AS variant_id,
               -- La disponibilidad real de la talla es el OR de las dos caras: en 387 grupos de
               -- Lefties una está a la venta y la otra no.
               BOOL_OR(l.in_stock) AS in_stock
        FROM variant v
        LEFT JOIN latest l ON l.variant_id = v.id
        WHERE v.product_id = ${id}
        GROUP BY size_canon(v.size), color_canon(v.color), coalesce(v.url, ''),
                 (v.delisted_at IS NULL)
      )
      SELECT v.id, v.retailer_variant_id, v.size, size_canon(v.size) AS size_canon,
             v.color, v.sku, v.url, v.delisted_at,
             l.price, l.list_price, l.discount_pct, g.in_stock, l.scraped_at,
             s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points
      FROM prenda g
      JOIN variant v ON v.id = g.variant_id
      LEFT JOIN latest l ON l.variant_id = v.id
      LEFT JOIN stats s ON s.variant_id = v.id
      ORDER BY v.id
    `)) as unknown as Record<string, unknown>[];

    const variants: VariantWithPrice[] = variantRows.map((row) => ({
      id: Number(row.id),
      retailerVariantId: String(row.retailer_variant_id),
      // La talla sale CRUDA a propósito, y no es un descuido pendiente de arreglar (#248): es el
      // texto que pinta el selector de tallas de la ficha, y en ropa infantil el paréntesis que
      // `size_canon` borra —'2 años (92 cm)' -> '2 años', ver la 0024— es justo por lo que un padre
      // elige. La canónica no se pierde: viaja en `variantLabel`, aquí abajo.
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
      // La MISMA función que nombra la variante en `/seguimientos` y en el aviso de Telegram, con
      // la talla canónica que calcula la base: es lo que impide que el modal de «Seguir esta
      // variante» confirme una talla y la lista enseñe otra (#248). El color va crudo, como en los
      // otros dos llamantes — `color_canon` devuelve NULL para lo que no reconoce (#51), así que
      // canonizarlo aquí lo borraría de la etiqueta en vez de normalizarlo.
      variantLabel: variantLabel(
        (row.size_canon as string | null) ?? null,
        (row.color as string | null) ?? null,
      ),
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
    //
    // `variant_url` (0023, #123) es el segundo eje del filtro: en H&M el nombre del color no
    // identifica la prenda, porque un producto nuestro junta varios artículos de la tienda y dos
    // pueden compartir `colorName`. Va a NULL en las otras seis tiendas y en todo lo ingerido
    // antes de la 0023, y la ficha tiene una cadena de respaldo para eso.
    const imageRows = (await this.db.execute(sql`
      SELECT color, url, variant_url
      FROM product_image
      WHERE product_id = ${id}
      ORDER BY color NULLS FIRST, position
    `)) as unknown as Record<string, unknown>[];

    const images: ProductImageRef[] = imageRows.map((row) => ({
      color: (row.color as string | null) ?? null,
      url: String(row.url),
      variantUrl: (row.variant_url as string | null) ?? null,
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
      // La ficha SÍ enseña el calzado no respetuoso: el filtro de #30 acota lo que se ofrece en el
      // catálogo, no censura un enlace directo. Devolver la marca deja que la ficha lo advierta.
      barefoot: (head.barefoot as string | null) ?? null,
      tags: (head.tags as string[] | null) ?? [],
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

  /**
   * Valores disponibles para los filtros. Acepta el MISMO `barefoot` que el listado y por la misma
   * razón: unas facetas sin filtrar ofrecerían chips (`zapatos`, `zapatillas`, tallas de calzado
   * convencional) que con el filtro por defecto no devuelven ni un producto.
   *
   * `section` acota por el mismo criterio, y en las tallas no es cosmético: sin él la lista mezcla
   * números de pie con rangos de edad (medido en dev: 121 valores crudos, 60 canónicos), y ninguna
   * de las dos mitades sirve para la sección que el usuario está mirando.
   */
  async getFacets(
    barefoot: BarefootFilter = 'si',
    section: string | null = null,
    deportiva = false,
  ): Promise<Facets> {
    // El eje entra en `visible` y no en `inSection` porque acota igual que el filtro barefoot: es
    // qué productos existen para esta vista, no qué sección se está mirando.
    const visible = sql`(${barefootCondition(barefoot, 'p')} AND ${tagCondition(
      deportiva ? TAG_DEPORTIVA : undefined,
      'p',
    )})`;
    const inSection = sql`(${section}::text IS NULL OR p.section = ${section})`;

    // `gender` y `section` van SIN acotar: son los ejes de navegación de la vista, y devolver solo
    // la sección elegida dejaría a la SPA sin las pestañas con las que se sale de ella.
    const pick = async (
      column: 'gender' | 'section' | 'category',
      scoped = false,
    ): Promise<string[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT ${sql.raw(`p.${column}`)} AS value
        FROM product p
        WHERE ${sql.raw(`p.${column}`)} IS NOT NULL AND p.delisted_at IS NULL
          AND ${visible}
          AND ${scoped ? inSection : sql`TRUE`}
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    /**
     * Tallas: valores CANÓNICOS distintos entre variantes vivas de productos activos, ordenados por
     * talla y no alfabéticamente (así el desplegable no pone '19' entre '11-12 años' y '2 años').
     *
     * Es la mitad visible de #43: la faceta ofrecía la misma talla física hasta cuatro veces, y el
     * chip que se elegía aquí es el que luego se guarda en `interest.size`, así que un chip por talla
     * física es también lo que hace que el aviso pueda casar con cualquier tienda.
     */
    const pickSizes = async (): Promise<string[]> => {
      // Tres niveles, y cada uno se gana el sitio:
      //   `crudas` deduplica el TEXTO de la tienda antes de canonicalizar. Medido sobre la copia de
      //     dev (33.311 variantes): canonicalizar fila a fila tarda 866 ms y así 13 ms, porque la
      //     función pasa de ~32.000 llamadas a las ~70 formas distintas que existen de verdad. En
      //     el cluster, que son Raspberry Pi, esa diferencia es la que decide si el panel de
      //     filtros abre al instante o no.
      //   el DISTINCT de fuera funde las formas equivalentes.
      //   el ORDER BY va en el nivel de arriba porque Postgres exige que sus expresiones estén en
      //     la lista del SELECT DISTINCT, y `size_sort(...)` no pinta como chip.
      const rows = (await this.db.execute(sql`
        SELECT value FROM (
          SELECT DISTINCT size_canon(cruda) AS value FROM (
            SELECT DISTINCT v.size AS cruda
            FROM variant v
            JOIN product p ON p.id = v.product_id
            WHERE v.size IS NOT NULL
              AND v.delisted_at IS NULL AND p.delisted_at IS NULL
              AND ${visible}
              AND ${inSection}
          ) crudas
        ) t
        ORDER BY size_sort(value), value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    /**
     * Colores CANÓNICOS distintos entre variantes vivas de productos activos (#49).
     *
     * Misma estructura de dos niveles que `pickSizes` y por la misma medida: `crudas` deduplica el
     * TEXTO de la tienda ANTES de canonicalizar, así la función se llama una vez por forma distinta
     * (220 en dev) y no una por variante (33.311). El DISTINCT de fuera funde las equivalentes.
     * Medido sobre ese volumen: 32,1 ms canonicalizando fila a fila contra 14,2 ms deduplicando
     * antes. Menos espectacular que en la talla (866 ms → 13 ms) porque `color_canon` es mucho más
     * barata, pero es la mitad del tiempo del panel de filtros por escribir el SELECT de otra forma.
     *
     * El orden alfabético del canónico basta —a diferencia de la talla, es el que se espera de una
     * lista de colores—, así que aquí no hace falta el equivalente de `size_sort`.
     *
     * El `IS NOT NULL` de fuera no es defensivo: `color_canon` devuelve NULL a propósito para un
     * nombre que son solo dígitos (#51, migración 0016 — Zara escribe el id del color como nombre
     * en 10 productos). Sin él, ese NULL llegaría a la SPA como el chip literal `"null"`.
     */
    const pickColors = async (): Promise<string[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT color_canon(cruda) AS value FROM (
          SELECT DISTINCT v.color AS cruda
          FROM variant v
          JOIN product p ON p.id = v.product_id
          WHERE v.color IS NOT NULL
            AND v.delisted_at IS NULL AND p.delisted_at IS NULL
            AND ${visible}
            AND ${inSection}
        ) crudas
        WHERE color_canon(cruda) IS NOT NULL
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    const pickRetailers = async (): Promise<RetailerFacet[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT r.slug, r.name
        FROM retailer r
        JOIN product p ON p.retailer_id = r.id AND p.delisted_at IS NULL
        WHERE ${visible}
        ORDER BY r.name
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => ({ slug: String(r.slug), name: String(r.name) }));
    };

    /**
     * Géneros ofrecibles como chip. `unisex` se cae de la lista a propósito: con
     * `generoCondition()` esos productos ya salen dentro de "Niño" y de "Niña", así que un tercer
     * chip no filtraría nada nuevo — solo sugeriría que hay tres estanterías cuando el brief pide
     * dos y el usuario piensa en dos.
     */
    const pickGenders = async (): Promise<string[]> =>
      (await pick('gender')).filter((g) => g !== GENERO_UNISEX);

    const [genders, sections, categories, sizes, colors, retailers] = await Promise.all([
      pickGenders(),
      pick('section'),
      pick('category', true),
      pickSizes(),
      pickColors(),
      pickRetailers(),
    ]);
    return { genders, sections, categories, sizes, colors, retailers };
  }
}
