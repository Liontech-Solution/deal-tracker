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
});

describe.skipIf(!TEST_DB)('descuento honesto · veredicto del catálogo (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Un producto con una variante y su histórico de precios (`daysAgo` = antigüedad del punto). */
  async function seedProduct(
    retailerId: number,
    name: string,
    history: { price: number; list: number | null; daysAgo: number }[],
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
        INSERT INTO price_history (variant_id, price, list_price, in_stock, scraped_at)
        VALUES (${v.id}, ${h.price}, ${h.list}, true, now() - make_interval(days => ${h.daysAgo}))`;
    }
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;

    // Oferta real: mínimo nuevo sobre un PVP creíble.
    await seedProduct(r.id, 'Oferta real', [
      { price: 40, list: 39.99, daysAgo: 2 },
      { price: 19.99, list: 39.99, daysAgo: 0 },
    ]);
    // Precio inflado: el precio actual NO es un mínimo reciente (ya estuvo a 15 €) y la tienda
    // enseña un tachado de 49,99 € -> descuento que no podemos corroborar.
    await seedProduct(r.id, 'Precio inflado', [
      { price: 15, list: 15, daysAgo: 3 },
      { price: 18, list: 49.99, daysAgo: 0 },
    ]);
    // Recién visto: una sola observación, ya rebajada -> sin histórico no afirmamos nada.
    await seedProduct(r.id, 'Recién visto', [{ price: 12, list: 30, daysAgo: 0 }]);

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
