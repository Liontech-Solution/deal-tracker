import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { makeApp, makeSql, resetSchema, seedCatalog, TEST_DB, type SeedIds } from './helpers';

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
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', 'zapateria', 'zapatos', 'https://x')
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
