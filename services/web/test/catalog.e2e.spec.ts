import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import {
  makeApp,
  makeSql,
  resetSchema,
  seedCatalog,
  SEED_IMAGE_URL,
  TEST_DB,
  type SeedIds,
} from './helpers';

describe.skipIf(!TEST_DB)('catálogo (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;
  let ids: SeedIds;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    ids = await seedCatalog(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  it('lista productos con el último precio (priceFrom = 19.99)', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    expect(res.body.items).toHaveLength(1);
    const item = res.body.items[0];
    expect(item.name).toBe('Botas niña');
    expect(item.priceFrom).toBe('19.99');
    expect(item.listFrom).toBe('39.99');
    expect(item.discountFrom).toBe('50.00');
    expect(item.maxDiscount).toBe('50.00');
    // 19,99 € es un mínimo nuevo real sobre un PVP creíble de 39,99 € -> oferta real.
    expect(item.honesty).toBe('real');
    expect(item.anyInStock).toBe(true);
    expect(item.variantCount).toBe(1);
  });

  it('sirve la foto del producto en la lista y en el detalle', async () => {
    const list = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    expect(list.body.items[0].imageUrl).toBe(SEED_IMAGE_URL);

    const detail = await request(app.getHttpServer())
      .get(`/api/catalog/products/${ids.productId}`)
      .expect(200);
    expect(detail.body.imageUrl).toBe(SEED_IMAGE_URL);
  });

  it('devuelve imageUrl null cuando el producto aún no tiene foto', async () => {
    await sql`UPDATE product SET image_url = NULL WHERE id = ${ids.productId}`;
    try {
      const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
      expect(res.body.items[0].imageUrl).toBeNull();
    } finally {
      await sql`UPDATE product SET image_url = ${SEED_IMAGE_URL} WHERE id = ${ids.productId}`;
    }
  });

  it('acepta orden por descuento y rechaza un sort inválido', async () => {
    await request(app.getHttpServer())
      .get('/api/catalog/products?sort=descuento')
      .expect(200)
      .expect((r) => expect(r.body.items).toHaveLength(1));
    await request(app.getHttpServer()).get('/api/catalog/products?sort=nope').expect(400);
  });

  it('filtra por sección y respeta filtros que no casan', async () => {
    await request(app.getHttpServer())
      .get('/api/catalog/products?section=zapateria')
      .expect(200)
      .expect((r) => expect(r.body.items).toHaveLength(1));
    await request(app.getHttpServer())
      .get('/api/catalog/products?section=ropa')
      .expect(200)
      .expect((r) => expect(r.body.items).toHaveLength(0));
  });

  it('devuelve el detalle con variantes y su precio', async () => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${ids.productId}`)
      .expect(200);
    expect(res.body.variants).toHaveLength(1);
    expect(res.body.variants[0].size).toBe('24');
    expect(res.body.variants[0].price).toBe('19.99');
    expect(res.body.variants[0].honesty).toBe('real');
  });

  it('devuelve el histórico de precios ordenado', async () => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/variants/${ids.variantId}/price-history`)
      .expect(200);
    expect(res.body).toHaveLength(2);
    expect(res.body[0].price).toBe('39.99');
    expect(res.body[1].price).toBe('19.99');
  });

  it('expone facets con los valores sembrados', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/facets').expect(200);
    expect(res.body.genders).toContain('niña');
    expect(res.body.sections).toContain('zapateria');
    expect(res.body.categories).toContain('zapatos');
    expect(res.body.sizes).toContain('24');
    expect(res.body.colors).toContain('rojo');
    expect(res.body.retailers).toContainEqual({ slug: 'zara', name: 'Zara' });
  });

  it('404 para producto inexistente', async () => {
    await request(app.getHttpServer()).get('/api/catalog/products/999999').expect(404);
  });

  // Búsqueda por texto (#28). El producto sembrado es 'Botas niña' con categoría 'zapatos', así que
  // sirve tal cual para el caso que importa en castellano: encontrarlo tecleando sin acentos.
  const search = async (q: string) => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products?q=${encodeURIComponent(q)}`)
      .expect(200);
    return res.body.items.map((i: { name: string }) => i.name);
  };

  it('busca por nombre sin distinguir mayúsculas ni acentos', async () => {
    expect(await search('botas')).toEqual(['Botas niña']);
    expect(await search('BOTAS')).toEqual(['Botas niña']);
    expect(await search('niña')).toEqual(['Botas niña']);
    expect(await search('nina')).toEqual(['Botas niña']); // tecleado sin la ñ
  });

  it('exige todas las palabras, en cualquier orden, y busca en categoría y género', async () => {
    expect(await search('botas nina')).toEqual(['Botas niña']);
    expect(await search('nina botas')).toEqual(['Botas niña']);
    expect(await search('zapatos')).toEqual(['Botas niña']); // categoría
    // El género no está en el nombre que da la tienda, pero es como la gente teclea.
    expect(await search('zapatos nina')).toEqual(['Botas niña']);
    expect(await search('zapatos nino')).toEqual([]);
    expect(await search('botas vestido')).toEqual([]); // una palabra no casa -> nada
  });

  it('no encuentra lo que no hay y no interpreta comodines', async () => {
    expect(await search('vestido')).toEqual([]);
    // Con LIKE, un '%' suelto devolvería el catálogo entero; con position() es texto y ya está.
    expect(await search('%')).toEqual([]);
    expect(await search('_')).toEqual([]);
    // Cadena vacía = sin filtro, no "sin resultados".
    expect(await search('   ')).toEqual(['Botas niña']);
  });

  it('rechaza un término de búsqueda desmedido', async () => {
    await request(app.getHttpServer())
      .get(`/api/catalog/products?q=${'a'.repeat(81)}`)
      .expect(400);
  });
});

describe.skipIf(!TEST_DB)('descuento honesto · veredicto del catálogo (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Un producto con una variante y su histórico de precios (`daysAgo` = antigüedad del punto). */
  async function seedProduct(
    retailerId: number,
    name: string,
    history: { price: number; list: number | null; daysAgo: number; discount?: number }[],
  ): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', 'zapateria', 'zapatos', 'si', 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '24', 'rojo', ${name + '-sku'})
      RETURNING id`;
    for (const h of history) {
      await sql`
        INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
        VALUES (${v.id}, ${h.price}, ${h.list}, ${h.discount ?? null}, true,
                now() - make_interval(days => ${h.daysAgo}))`;
    }
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;

    // Oferta real: mínimo nuevo sobre un PVP creíble. La tienda declara un 50 %.
    await seedProduct(r.id, 'Oferta real', [
      { price: 40, list: 39.99, daysAgo: 2 },
      { price: 19.99, list: 39.99, daysAgo: 0, discount: 50 },
    ]);
    // Precio inflado: el precio actual NO es un mínimo reciente (ya estuvo a 15 €) y la tienda
    // enseña un tachado de 49,99 € con un 64 % -> descuento que no podemos corroborar, y a la vez
    // el mayor `discount_pct` declarado del catálogo: el caso que el orden tiene que degradar.
    await seedProduct(r.id, 'Precio inflado', [
      { price: 15, list: 15, daysAgo: 3 },
      { price: 18, list: 49.99, daysAgo: 0, discount: 64 },
    ]);
    // Recién visto: una sola observación, ya rebajada -> sin histórico no afirmamos nada.
    await seedProduct(r.id, 'Recién visto', [{ price: 12, list: 30, daysAgo: 0, discount: 60 }]);
    // Sin rebaja: histórico plano y sin tachado. Ni oferta ni engaño.
    await seedProduct(r.id, 'Sin rebaja', [
      { price: 25, list: null, daysAgo: 4 },
      { price: 25, list: null, daysAgo: 0 },
    ]);

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  it('clasifica cada producto con la misma regla que el aviso', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    const byName = new Map<string, string>(
      res.body.items.map((i: { name: string; honesty: string }) => [i.name, i.honesty]),
    );
    expect(byName.get('Oferta real')).toBe('real');
    expect(byName.get('Precio inflado')).toBe('suspicious');
    expect(byName.get('Recién visto')).toBe('none');
    expect(byName.get('Sin rebaja')).toBe('none');
  });

  /**
   * El test que vigila el espejo: `onlyDeals` y el orden se deciden en SQL
   * (`matching/deal-rule.sql.ts`), pero la etiqueta `honesty` la sigue calculando `classifyHonesty`
   * en TypeScript. Si los dos lados se separan, esto rompe — que es justo para lo que está.
   */
  it('«solo ofertas» devuelve exactamente los productos etiquetados como oferta real', async () => {
    const todos = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    const realesSegunTs = todos.body.items
      .filter((i: { honesty: string }) => i.honesty === 'real')
      .map((i: { name: string }) => i.name)
      .sort();

    const soloOfertas = await request(app.getHttpServer())
      .get('/api/catalog/products?onlyDeals=true')
      .expect(200);
    const realesSegunSql = soloOfertas.body.items.map((i: { name: string }) => i.name).sort();

    expect(realesSegunSql).toEqual(realesSegunTs);
    expect(realesSegunSql).toEqual(['Oferta real']); // y no es una comparación de dos listas vacías
    expect(
      soloOfertas.body.items.every((i: { honesty: string }) => i.honesty === 'real'),
    ).toBe(true);
  });

  it('sin «solo ofertas» el catálogo sigue mostrándolo todo', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?onlyDeals=false')
      .expect(200);
    expect(res.body.items).toHaveLength(4);
  });

  it('sort=ofertas antepone la oferta real al mayor descuento declarado por la tienda', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?sort=ofertas')
      .expect(200);
    const nombres = res.body.items.map((i: { name: string }) => i.name);
    // 'Precio inflado' declara un 64 % frente al 50 % de 'Oferta real': con el orden viejo
    // (max_discount) iría primero. Lo honesto manda.
    expect(nombres[0]).toBe('Oferta real');
    expect(nombres.indexOf('Oferta real')).toBeLessThan(nombres.indexOf('Precio inflado'));
    // 'descuento' sigue siendo el orden por el % que declara la tienda, para quien lo pida explícito.
    const porDescuento = await request(app.getHttpServer())
      .get('/api/catalog/products?sort=descuento')
      .expect(200);
    expect(porDescuento.body.items[0].name).toBe('Precio inflado');
  });
});

describe.skipIf(!TEST_DB)('galería por color · coherencia foto↔precio (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;
  let productId: number;

  const ROSA_0 = 'https://static.example/p/rosa-0.jpg';
  const ROSA_1 = 'https://static.example/p/rosa-1.jpg';
  const NEGRO_0 = 'https://static.example/p/negro-0.jpg';

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;

    // Producto de DOS colores a precios distintos. `image_url` (la foto suelta de siempre) apunta
    // al rosa, pero la variante más barata en stock es la negra: es justo el caso en el que la
    // tarjeta enseñaba la foto de un color con el precio de otro.
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url, image_url)
      VALUES (${r.id}, 'ZARA-2', 'Bailarina', 'niña', 'zapateria', 'zapatos', 'si', 'https://x/2',
              ${ROSA_0})
      RETURNING id`;
    productId = p.id;

    for (const [rvid, color, price] of [
      ['ZARA-2-24-rosa', 'Rosa', 39.95],
      ['ZARA-2-24-negro', 'Negro', 24.95],
    ] as const) {
      const [v] = await sql<{ id: number }[]>`
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
        VALUES (${p.id}, ${rvid}, '24', ${color}, ${rvid}) RETURNING id`;
      await sql`
        INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
        VALUES (${v.id}, ${price}, 39.95, 0, true, now())`;
    }

    await sql`
      INSERT INTO product_image (product_id, color, position, url)
      VALUES (${p.id}, 'Rosa', 0, ${ROSA_0}),
             (${p.id}, 'Rosa', 1, ${ROSA_1}),
             (${p.id}, 'Negro', 0, ${NEGRO_0})`;

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  it('la tarjeta sirve la foto del color cuyo precio muestra', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    const item = res.body.items[0];
    // El precio mostrado es el de la variante negra (la más barata en stock)...
    expect(item.priceFrom).toBe('24.95');
    expect(item.colorRepr).toBe('Negro');
    // ...así que la foto tiene que ser la negra, no la del `product.image_url` (rosa).
    expect(item.imageUrl).toBe(NEGRO_0);
  });

  it('el detalle devuelve la galería agrupada por color y ordenada por posición', async () => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${productId}`)
      .expect(200);
    // `variantUrl` a null: Zara no publica dos artículos bajo un mismo nombre de color, así que
    // aquí el color solo ya identifica la galería (ver #123 y la 0023).
    expect(res.body.images).toEqual([
      { color: 'Negro', url: NEGRO_0, variantUrl: null },
      { color: 'Rosa', url: ROSA_0, variantUrl: null },
      { color: 'Rosa', url: ROSA_1, variantUrl: null },
    ]);
  });

  it('cae a product.image_url cuando el color representativo no tiene foto', async () => {
    await sql`DELETE FROM product_image WHERE color = 'Negro'`;
    try {
      const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
      expect(res.body.items[0].colorRepr).toBe('Negro');
      expect(res.body.items[0].imageUrl).toBe(ROSA_0); // el respaldo de siempre, no un hueco
    } finally {
      await sql`
        INSERT INTO product_image (product_id, color, position, url)
        VALUES (${productId}, 'Negro', 0, ${NEGRO_0})`;
    }
  });
});

describe.skipIf(!TEST_DB)('foco barefoot · qué enseña el catálogo por defecto (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Producto con una variante y un precio, con la marca barefoot que se le pase. */
  async function seedProduct(
    retailerId: number,
    name: string,
    section: string,
    category: string,
    barefoot: string | null,
  ): Promise<number> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', ${section}, ${category}, ${barefoot},
              'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '24', 'rojo', ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;
    return p.id;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;

    await seedProduct(r.id, 'Bailarina barefoot', 'zapateria', 'barefoot', 'si');
    await seedProduct(r.id, 'Botín de tacón', 'zapateria', 'zapatos', 'no');
    await seedProduct(r.id, 'Zapato sin clasificar', 'zapateria', 'zapatos', 'desconocido');
    // Ropa: la marca es NULL porque la pregunta no aplica. Debe verse SIEMPRE.
    await seedProduct(r.id, 'Camiseta', 'ropa', 'camisetas', null);

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(query).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('por defecto enseña toda la ropa y solo el calzado respetuoso', async () => {
    expect(await nombres('/api/catalog/products')).toEqual(['Bailarina barefoot', 'Camiseta']);
  });

  it('lo NO concluyente se esconde igual que lo descartado', async () => {
    // El sesgo de #30: en la duda no se enseña. Un `desconocido` visible sería prometer barefoot
    // sin saberlo, que es peor que enseñar de menos.
    const visibles = await nombres('/api/catalog/products');
    expect(visibles).not.toContain('Zapato sin clasificar');
    expect(visibles).not.toContain('Botín de tacón');
  });

  it('la ropa nunca se ve afectada por el filtro', async () => {
    for (const filtro of ['si', 'all']) {
      expect(await nombres(`/api/catalog/products?barefoot=${filtro}`)).toContain('Camiseta');
    }
  });

  it('barefoot=all es el escape explícito y devuelve el catálogo entero', async () => {
    expect(await nombres('/api/catalog/products?barefoot=all')).toEqual([
      'Bailarina barefoot',
      'Botín de tacón',
      'Camiseta',
      'Zapato sin clasificar',
    ]);
  });

  it('permite auditar la clasificación pidiendo un estado concreto', async () => {
    expect(await nombres('/api/catalog/products?barefoot=desconocido')).toEqual([
      'Zapato sin clasificar',
    ]);
    expect(await nombres('/api/catalog/products?barefoot=no')).toEqual(['Botín de tacón']);
  });

  it('rechaza un valor de barefoot inválido', async () => {
    await request(app.getHttpServer()).get('/api/catalog/products?barefoot=quizas').expect(400);
  });

  it('la ficha directa SÍ enseña el calzado no respetuoso, con su marca', async () => {
    // El filtro acota lo que el catálogo OFRECE; no censura un enlace que alguien ya tiene.
    const [row] = await sql<{ id: number }[]>`
      SELECT id FROM product WHERE name = 'Botín de tacón'`;
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${row.id}`)
      .expect(200);
    expect(res.body.barefoot).toBe('no');
  });

  it('las facetas no ofrecen filtros que el catálogo por defecto deja vacíos', async () => {
    const porDefecto = await request(app.getHttpServer()).get('/api/catalog/facets').expect(200);
    expect(porDefecto.body.categories).toEqual(['barefoot', 'camisetas']);

    const todo = await request(app.getHttpServer())
      .get('/api/catalog/facets?barefoot=all')
      .expect(200);
    expect(todo.body.categories).toContain('zapatos');
  });
});

/**
 * Talla canónica en el catálogo (#43 y #64).
 *
 * El escenario es el medido en `dev`: la talla 26 de un niño existe como '26' en Sfera y como
 * '26 (16,3 cm)' en Zara. Antes de 0014 la faceta ofrecía las dos y cada filtro enseñaba media
 * zapatería.
 *
 * Cacles añade el segundo escenario: tallas que son RANGOS de número de pie. Antes de 0017 la
 * faceta llegaba a ofrecer un chip «48-51 años», que ni se entiende ni filtra lo que dice filtrar.
 * Y el calcetín está a propósito en `ropa`: es la contraprueba de que la sección no decide esto.
 */
describe.skipIf(!TEST_DB)('talla canónica · faceta y filtro (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Producto de una tienda con UNA variante, cuya talla se escribe como la escribe esa tienda. */
  async function seedTalla(
    retailerId: number,
    name: string,
    section: string,
    category: string,
    barefoot: string | null,
    size: string,
  ): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', ${section}, ${category}, ${barefoot},
              'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, ${size}, 'rojo', ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [zara] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;
    const [sfera] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('sfera', 'Sfera', 'https://www.sfera.com') RETURNING id`;

    const [cacles] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('cacles', 'Cacles', 'https://cacles.com') RETURNING id`;

    await seedTalla(zara.id, 'Bota Zara', 'zapateria', 'barefoot', 'si', '26 (16,3 cm)');
    await seedTalla(sfera.id, 'Bota Sfera', 'zapateria', 'barefoot', 'si', '26');
    await seedTalla(zara.id, 'Camiseta Zara', 'ropa', 'camisetas', null, '11-12 años (152 cm)');
    await seedTalla(sfera.id, 'Camiseta Sfera', 'ropa', 'camisetas', null, '11-12');
    // Rangos de número de pie (#64), tal como los escribe Cacles.
    await seedTalla(cacles.id, 'Plantilla Cacles', 'zapateria', 'plantillas', 'si', '48-51');
    await seedTalla(cacles.id, 'Botita Cacles', 'zapateria', 'barefoot', 'si', '20 /21');
    await seedTalla(cacles.id, 'Calcetín Cacles', 'ropa', 'ropa-interior', 'si', '36-38');

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const facetas = async (query = '') => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/facets${query}`)
      .expect(200);
    return res.body as { sizes: string[]; sections: string[] };
  };

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(query).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('ofrece la misma talla física UNA sola vez', async () => {
    // Los rangos de pie se intercalan con los números sueltos por su extremo inferior.
    expect((await facetas('?section=zapateria')).sizes).toEqual(['20-21', '26', '48-51']);
    expect((await facetas('?section=ropa')).sizes).toEqual(['11-12 años', '36-38']);
  });

  /**
   * El síntoma que abrió la issue #64: un chip «48-51 años» en la faceta de zapatería. Ni se
   * entiende ni filtra lo que dice filtrar.
   */
  it('no etiqueta como edad un rango de número de pie (#64)', async () => {
    const zapateria = (await facetas('?section=zapateria')).sizes;
    expect(zapateria.filter((s) => s.includes('años'))).toEqual([]);
    // Y el calcetín es ROPA: aquí la única etiqueta de edad es la de la camiseta.
    expect((await facetas('?section=ropa')).sizes.filter((s) => s.includes('años'))).toEqual([
      '11-12 años',
    ]);
  });

  it('acota las tallas a la sección que se está mirando', async () => {
    // Sin acotar, la lista es la unión de números de pie y rangos de edad, y ninguna de las dos
    // mitades sirve para la vista en la que está el usuario.
    // Y el orden lo demuestra: sin sección, un rango de edad se cuela delante de un número de pie
    // porque numéricamente le toca ahí.
    const sinAcotar = await facetas();
    expect(sinAcotar.sizes).toEqual(['11-12 años', '20-21', '26', '36-38', '48-51']);
    expect((await facetas('?section=ropa')).sizes).not.toContain('26');
    // La sección misma no se acota: son las pestañas con las que se sale de aquí.
    expect((await facetas('?section=ropa')).sections).toEqual(['ropa', 'zapateria']);
  });

  it('un filtro por la talla del chip encuentra las dos tiendas', async () => {
    expect(await nombres('/api/catalog/products?size=26')).toEqual(['Bota Sfera', 'Bota Zara']);
    expect(await nombres('/api/catalog/products?size=11-12%20a%C3%B1os')).toEqual([
      'Camiseta Sfera',
      'Camiseta Zara',
    ]);
  });

  it('sigue encontrando por la talla cruda de la tienda (enlaces antiguos)', async () => {
    expect(await nombres('/api/catalog/products?size=26%20(16%2C3%20cm)')).toEqual([
      'Bota Sfera',
      'Bota Zara',
    ]);
  });

  it('el chip de un rango de pie devuelve producto, venga con guion o con barra', async () => {
    expect(await nombres('/api/catalog/products?size=48-51')).toEqual(['Plantilla Cacles']);
    expect(await nombres('/api/catalog/products?size=36-38')).toEqual(['Calcetín Cacles']);
    // '20 /21' y '20-21' son la misma talla: el separador se normaliza igual que en los años.
    expect(await nombres('/api/catalog/products?size=20-21')).toEqual(['Botita Cacles']);
    expect(await nombres('/api/catalog/products?size=20%20%2F21')).toEqual(['Botita Cacles']);
  });

  it('la ficha sigue enseñando la talla tal como la escribe la tienda', async () => {
    const [row] = await sql<{ id: number }[]>`SELECT id FROM product WHERE name = 'Bota Zara'`;
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${row.id}`)
      .expect(200);
    expect(res.body.variants[0].size).toBe('26 (16,3 cm)');
  });

  /**
   * #248. Las dos mitades van juntas a propósito, porque la gracia está en que DIFIEREN: la ficha
   * pinta la talla de la tienda en el selector, y la etiqueta con la que el usuario confirma el
   * seguimiento lleva la canónica — la misma que verá luego en `/seguimientos` y en el aviso de
   * Telegram, que se rotulan con esta misma `variantLabel()` desde #223.
   *
   * Antes de #248 el modal rehacía la etiqueta en el frontend con `variants[].size`, así que
   * confirmaba 'Talla 11-12 años (152 cm)' y la lista enseñaba después 'Talla 11-12 años'. Con una
   * fixture cuya canónica coincidiera con la cruda, este test no podría distinguir las dos.
   */
  it('nombra la variante con la talla canónica, aunque el selector la enseñe cruda', async () => {
    const [row] = await sql<{ id: number }[]>`SELECT id FROM product WHERE name = 'Camiseta Zara'`;
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${row.id}`)
      .expect(200);
    const [variante] = res.body.variants;
    expect(variante.size).toBe('11-12 años (152 cm)');
    expect(variante.variantLabel).toBe('Talla 11-12 años · rojo');
  });
});

describe.skipIf(!TEST_DB)('color canónico · faceta, filtro y foto (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  const FOTO_VERDE = 'https://static.example/p/verde-0.jpg';
  const FOTO_MUDA = 'https://static.example/p/771-0.jpg';

  /** Producto de una tienda con UNA variante, cuyo color se escribe como lo escribe esa tienda. */
  async function seedColor(retailerId: number, name: string, color: string): Promise<number> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', 'zapateria', 'barefoot', 'si', 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '26', ${color}, ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;
    return p.id;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [zara] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;
    const [sfera] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('sfera', 'Sfera', 'https://www.sfera.com') RETURNING id`;

    // El mismo color escrito de dos maneras por dos tiendas, que es el caso medido en dev.
    const idZara = await seedColor(zara.id, 'Bota Zara', 'VERDE');
    await seedColor(sfera.id, 'Bota Sfera', 'Verde');

    // La foto se clava por el TEXTO del color (migración 0011). Se siembra con el texto crudo de la
    // tienda a propósito: si la canonicalización llegara al dato, este join dejaría de casar.
    await sql`
      INSERT INTO product_image (product_id, color, position, url)
      VALUES (${idZara}, 'VERDE', 0, ${FOTO_VERDE})`;

    // El color mudo de Zara (#51): su `name` es el id del color, así que no hay nombre que ofrecer.
    // Se siembra con foto para comprobar que quitarlo de la faceta NO le quita la galería.
    const idMudo = await seedColor(zara.id, 'Bermuda Zara', '771');
    await sql`
      INSERT INTO product_image (product_id, color, position, url)
      VALUES (${idMudo}, '771', 0, ${FOTO_MUDA})`;

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const facetas = async (query = '') => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/facets${query}`).expect(200);
    return res.body as { colors: string[] };
  };

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(query).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('ofrece el mismo color UNA sola vez, y en canónico', async () => {
    // Que '771' no salga aquí es la mitad de #51: un chip que son solo dígitos no lo pincha nadie.
    expect((await facetas('?section=zapateria')).colors).toEqual(['verde']);
  });

  /**
   * La otra mitad de #51, y el riesgo del enfoque: quitar el color de la faceta NO puede quitarle
   * al producto ni su sitio en el catálogo ni su foto. Como `variant.color` conserva el texto crudo
   * ('771') y solo se canonicaliza la comparación, el join de `product_image` sigue casando.
   */
  it('el producto del color mudo sigue en el catálogo y con su foto', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    const mudo = res.body.items.find((i: { name: string }) => i.name === 'Bermuda Zara');
    expect(mudo, 'el producto no puede desaparecer del catálogo').toBeDefined();
    expect(mudo.colorRepr).toBe('771');
    expect(mudo.imageUrl).toBe(FOTO_MUDA);
  });

  it('un filtro por el color mudo no devuelve el catálogo entero', async () => {
    // `color_canon('771')` es NULL, y `NULL = NULL` es NULL: la fila queda fuera. Lo que NO puede
    // pasar es que un filtro sin sentido se comporte como "sin filtro".
    expect(await nombres('/api/catalog/products?color=771')).toEqual([]);
  });

  it('un filtro por el color del chip encuentra las dos tiendas', async () => {
    expect(await nombres('/api/catalog/products?color=verde')).toEqual(['Bota Sfera', 'Bota Zara']);
  });

  it('sigue encontrando por el color crudo de la tienda (enlaces antiguos)', async () => {
    expect(await nombres('/api/catalog/products?color=VERDE')).toEqual(['Bota Sfera', 'Bota Zara']);
  });

  it('la ficha sigue enseñando el color tal como lo escribe la tienda', async () => {
    const [row] = await sql<{ id: number }[]>`SELECT id FROM product WHERE name = 'Bota Zara'`;
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${row.id}`)
      .expect(200);
    expect(res.body.variants[0].color).toBe('VERDE');
  });

  /**
   * El riesgo concreto que esta issue tenía que comprobar antes de escribir nada (#49, punto 2):
   * `product_image.color` está clavada por el texto de `variant.color`. Como se canonicaliza solo la
   * comparación y nunca el dato, ese join sigue siendo crudo-contra-crudo y la foto sigue saliendo.
   */
  it('la foto por color sigue casando aunque el filtro sea canónico', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?color=verde')
      .expect(200);
    const zara = res.body.items.find((i: { name: string }) => i.name === 'Bota Zara');
    expect(zara.colorRepr).toBe('VERDE');
    expect(zara.imageUrl).toBe(FOTO_VERDE);
  });
});

/**
 * Género unisex (#32): un producto que sirve para niño y para niña tiene que salir en los DOS
 * filtros, no en ninguno.
 *
 * No es un caso de borde inventado para el test. El calzado barefoot infantil se diseña unisex, y
 * Cacles —la primera tienda barefoot nativa del catálogo— publica así 342 de sus 428 referencias,
 * con ninguna marcada solo de niño. Con la igualdad estricta que había antes, filtrar por "Niño"
 * devolvía cero productos suyos: la tienda que entró justo para llenar la zapatería quedaba
 * invisible en media navegación.
 */
describe.skipIf(!TEST_DB)('género unisex · catálogo (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  async function seedGenero(retailerId: number, name: string, gender: string): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, ${gender}, 'zapateria', 'sandalias', 'si',
              'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '24', 'azul', ${name + '-sku'})
      RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 39.90, 39.90, 0, true, now() - interval '2 days'),
             (${v.id}, 19.90, 39.90, 50, true, now())`;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('cacles', 'Cacles Barefoot', 'https://www.caclesbarefoot.com') RETURNING id`;
    await seedGenero(r.id, 'Sandalia unisex', 'unisex');
    await seedGenero(r.id, 'Sandalia de niña', 'niña');
    await seedGenero(r.id, 'Sandalia de niño', 'niño');
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(query).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('el unisex sale al filtrar por niño y también por niña', async () => {
    expect(await nombres('/api/catalog/products?gender=niño')).toEqual([
      'Sandalia de niño',
      'Sandalia unisex',
    ]);
    expect(await nombres('/api/catalog/products?gender=niña')).toEqual([
      'Sandalia de niña',
      'Sandalia unisex',
    ]);
  });

  it('sin filtro de género salen los tres, sin duplicar el unisex', async () => {
    expect(await nombres('/api/catalog/products')).toEqual([
      'Sandalia de niña',
      'Sandalia de niño',
      'Sandalia unisex',
    ]);
  });

  it('el unisex sigue saliendo con el filtro barefoot por defecto', async () => {
    // Los dos filtros son condiciones independientes: que el género sea unisex no puede hacer que
    // el producto se caiga del filtro barefoot, que es el que la SPA lleva puesto siempre.
    expect(await nombres('/api/catalog/products?gender=niño&barefoot=si')).toContain(
      'Sandalia unisex',
    );
  });

  it('no ofrece "unisex" como tercer chip de género', async () => {
    // Ya está incluido en Niño y en Niña, así que un chip propio no filtraría nada nuevo: solo
    // sugeriría que hay tres estanterías cuando el brief pide dos.
    const res = await request(app.getHttpServer()).get('/api/catalog/facets').expect(200);
    expect(res.body.genders.sort()).toEqual(['niña', 'niño']);
  });
});

describe.skipIf(!TEST_DB)('dos SKU para la misma prenda · ficha y recuento (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;
  let lefties: number;
  let hm: number;

  /**
   * Producto con dos variantes que repiten talla y color, como publican Lefties, H&M e Hipercor
   * (#108). `url` es lo que decide si son la misma prenda o dos artículos distintos de la tienda.
   */
  async function seedDosCaras(
    retailerId: number,
    name: string,
    urls: [string, string],
    stock: [boolean, boolean],
  ): Promise<number[]> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', 'zapateria', 'barefoot', 'si', ${urls[0]})
      RETURNING id`;
    const ids: number[] = [];
    for (const [i, url] of urls.entries()) {
      const [v] = await sql<{ id: number }[]>`
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url)
        VALUES (${p.id}, ${`${name}-${i}`}, '27', 'BLANCO', ${`${name}-sku-${i}`}, ${url})
        RETURNING id`;
      ids.push(Number(v.id));
      await sql`
        INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
        VALUES (${v.id}, 19.99, 39.99, 50, ${stock[i]}, now())`;
    }
    return [Number(p.id), ...ids];
  }

  let lefProduct: number;
  let lefMuerta: number;
  let lefViva: number;
  let hmProduct: number;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [l] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('lefties', 'Lefties', 'https://www.lefties.com') RETURNING id`;
    const [h] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('hm', 'H&M', 'https://www2.hm.com') RETURNING id`;
    lefties = l.id;
    hm = h.id;

    // Lefties: las dos caras cuelgan de la MISMA ficha. La primera por id está agotada.
    [lefProduct, lefMuerta, lefViva] = await seedDosCaras(
      lefties,
      'Zapatilla retro barefoot',
      ['https://lefties/zapatilla', 'https://lefties/zapatilla'],
      [false, true],
    );
    // H&M: dos artículos distintos, cada uno con su ficha.
    [hmProduct] = await seedDosCaras(
      hm,
      'Pantalón en mezcla de lino',
      ['https://hm/1315153003.html', 'https://hm/1315153005.html'],
      [false, true],
    );

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const ficha = async (id: number) => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/products/${id}`).expect(200);
    return res.body as {
      variants: { id: number; size: string; inStock: boolean; url: string | null }[];
    };
  };

  it('la misma ficha con dos SKU se enseña una sola vez, y con el stock de la cara viva', async () => {
    const { variants } = await ficha(lefProduct);
    expect(variants).toHaveLength(1);
    expect(variants[0].id).toBe(lefViva);
    expect(variants[0].size).toBe('27');
    // La disponibilidad de la talla es el OR de las dos caras: la talla 27 SÍ se puede comprar.
    expect(variants[0].inStock).toBe(true);
    expect(variants.map((v) => v.id)).not.toContain(lefMuerta);
  });

  it('dos artículos distintos de la tienda siguen siendo dos filas (#108, el caso de H&M)', async () => {
    // Cada uno tiene su propia ficha en la tienda: colapsarlos escondería un destino real.
    const { variants } = await ficha(hmProduct);
    expect(variants).toHaveLength(2);
  });

  it('la galería de esos dos artículos viaja separada por ficha (#123)', async () => {
    // Las dos caras comparten nombre de color ('BLANCO'), que es justo lo que hacía que sus fotos
    // acabaran en el mismo saco: la ficha las pedía por color y le llegaban las de los dos.
    await sql`
      INSERT INTO product_image (product_id, color, position, url, variant_url)
      VALUES (${hmProduct}, 'BLANCO', 0, 'https://img/003-a.jpg', 'https://hm/1315153003.html'),
             (${hmProduct}, 'BLANCO', 1, 'https://img/003-b.jpg', 'https://hm/1315153003.html'),
             (${hmProduct}, 'BLANCO', 2, 'https://img/005-a.jpg', 'https://hm/1315153005.html')`;
    try {
      const res = await request(app.getHttpServer())
        .get(`/api/catalog/products/${hmProduct}`)
        .expect(200);
      const images = res.body.images as { color: string; url: string; variantUrl: string | null }[];

      // El mismo color, pero cada foto sabe de qué artículo sale: con eso la ficha puede filtrar.
      expect(images).toHaveLength(3);
      expect(new Set(images.map((i) => i.color))).toEqual(new Set(['BLANCO']));
      const porFicha = images.reduce<Record<string, number>>((acc, i) => {
        acc[i.variantUrl ?? 'null'] = (acc[i.variantUrl ?? 'null'] ?? 0) + 1;
        return acc;
      }, {});
      expect(porFicha).toEqual({
        'https://hm/1315153003.html': 2,
        'https://hm/1315153005.html': 1,
      });

      // Y la URL de cada foto es una de las que traen las variantes, que es la invariante que
      // permite emparejarlas en la ficha (el equivalente del join por color de la 0011).
      const { variants } = await ficha(hmProduct);
      const urlsDeVariante = new Set(variants.map((v) => v.url));
      for (const i of images) expect(urlsDeVariante.has(i.variantUrl)).toBe(true);
    } finally {
      await sql`DELETE FROM product_image WHERE product_id = ${hmProduct}`;
    }
  });

  it('variantCount cuenta prendas comprables, no filas', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?retailer=lefties')
      .expect(200);
    expect(res.body.items).toHaveLength(1);
    expect(res.body.items[0].variantCount).toBe(1);
    expect(res.body.items[0].anyInStock).toBe(true);
  });
});

describe.skipIf(!TEST_DB)('eje transversal · ropa deportiva (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  async function seedProduct(
    retailerId: number,
    name: string,
    section: string,
    category: string,
    tags: string[],
  ): Promise<number> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', ${section}, ${category},
              ${section === 'zapateria' ? 'si' : null}, 'https://x')
      RETURNING id`;
    for (const tag of tags) {
      await sql`INSERT INTO product_tag (product_id, tag) VALUES (${p.id}, ${tag})`;
    }
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '24', 'rojo', ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;
    return p.id;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('sfera', 'Sfera', 'https://www.sfera.com') RETURNING id`;

    // El reparto que hace que el eje NO pueda ser una categoría: la prenda deportiva vive dentro
    // de `pantalones` y de `sudaderas`, junto a las que no lo son.
    await seedProduct(r.id, 'Jogger', 'ropa', 'pantalones', ['deportiva']);
    await seedProduct(r.id, 'Sudadera técnica', 'ropa', 'sudaderas', ['deportiva']);
    await seedProduct(r.id, 'Pantalón de vestir', 'ropa', 'pantalones', []);
    await seedProduct(r.id, 'Vestido', 'ropa', 'vestidos', []);

    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(query).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('apagado por defecto: el eje no esconde nada', async () => {
    // La diferencia con `barefoot`, que sí filtra por defecto. Aquí encenderlo por defecto
    // escondería casi todo el catálogo, porque solo tres tiendas alimentan el eje.
    expect(await nombres('/api/catalog/products')).toEqual([
      'Jogger',
      'Pantalón de vestir',
      'Sudadera técnica',
      'Vestido',
    ]);
  });

  it('encendido deja solo lo marcado, cruzando categorías', async () => {
    // Lo que una categoría `ropa-deportiva` no podría hacer: el jogger sigue siendo `pantalones`
    // y la sudadera `sudaderas`, y aun así salen juntos.
    expect(await nombres('/api/catalog/products?deportiva=true')).toEqual([
      'Jogger',
      'Sudadera técnica',
    ]);
  });

  it('se combina con la categoría en vez de sustituirla', async () => {
    expect(await nombres('/api/catalog/products?deportiva=true&category=pantalones')).toEqual([
      'Jogger',
    ]);
  });

  it('las etiquetas viajan en la tarjeta y en la ficha', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?deportiva=true&category=pantalones')
      .expect(200);
    expect(res.body.items[0].tags).toEqual(['deportiva']);

    const ficha = await request(app.getHttpServer())
      .get(`/api/catalog/products/${res.body.items[0].id}`)
      .expect(200);
    expect(ficha.body.tags).toEqual(['deportiva']);
  });

  it('un producto sin marcar trae un array vacío, no null', async () => {
    // Vacío es «su tienda no lo dice», no «no lo es». Que sea siempre un array evita que la SPA
    // tenga que distinguir los dos casos con un `?.`.
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?category=vestidos')
      .expect(200);
    expect(res.body.items[0].tags).toEqual([]);
  });

  it('las facetas describen la vista filtrada', async () => {
    // Sin esto el panel ofrecería `vestidos`, que con el interruptor puesto no devuelve nada.
    const res = await request(app.getHttpServer())
      .get('/api/catalog/facets?deportiva=true')
      .expect(200);
    expect(res.body.categories).toEqual(['pantalones', 'sudaderas']);
  });

  it('cualquier valor que no sea `true` se lee como apagado', async () => {
    // Mismo trato que `inStock` y `onlyDeals`: el `@Transform` de los tres solo reconoce `true`.
    // No es un 400 —a diferencia de `barefoot`, que sí valida contra una lista— y conviene que
    // esté escrito: un enlace con `?deportiva=1` enseña el catálogo entero, no da error.
    expect(await nombres('/api/catalog/products?deportiva=quizas')).toHaveLength(4);
    expect(await nombres('/api/catalog/products?deportiva=1')).toHaveLength(4);
  });
});
