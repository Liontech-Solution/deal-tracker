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
    expect(res.body.images).toEqual([
      { color: 'Negro', url: NEGRO_0 },
      { color: 'Rosa', url: ROSA_0 },
      { color: 'Rosa', url: ROSA_1 },
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
 * Talla canónica en el catálogo (#43).
 *
 * El escenario es el medido en `dev`: la talla 26 de un niño existe como '26' en Sfera y como
 * '26 (16,3 cm)' en Zara. Antes de 0014 la faceta ofrecía las dos y cada filtro enseñaba media
 * zapatería.
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

    await seedTalla(zara.id, 'Bota Zara', 'zapateria', 'barefoot', 'si', '26 (16,3 cm)');
    await seedTalla(sfera.id, 'Bota Sfera', 'zapateria', 'barefoot', 'si', '26');
    await seedTalla(zara.id, 'Camiseta Zara', 'ropa', 'camisetas', null, '11-12 años (152 cm)');
    await seedTalla(sfera.id, 'Camiseta Sfera', 'ropa', 'camisetas', null, '11-12');

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
    expect((await facetas('?section=zapateria')).sizes).toEqual(['26']);
    expect((await facetas('?section=ropa')).sizes).toEqual(['11-12 años']);
  });

  it('acota las tallas a la sección que se está mirando', async () => {
    // Sin acotar, la lista es la unión de números de pie y rangos de edad, y ninguna de las dos
    // mitades sirve para la vista en la que está el usuario.
    // Y el orden lo demuestra: sin sección, un rango de edad se cuela delante de un número de pie
    // porque numéricamente le toca ahí.
    const sinAcotar = await facetas();
    expect(sinAcotar.sizes).toEqual(['11-12 años', '26']);
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

  it('la ficha sigue enseñando la talla tal como la escribe la tienda', async () => {
    const [row] = await sql<{ id: number }[]>`SELECT id FROM product WHERE name = 'Bota Zara'`;
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${row.id}`)
      .expect(200);
    expect(res.body.variants[0].size).toBe('26 (16,3 cm)');
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
