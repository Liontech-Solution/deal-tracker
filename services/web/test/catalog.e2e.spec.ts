import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import {
  makeApp,
  makeSql,
  refrescarAgregado,
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
    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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
    //
    // El punto de hace 30 días es lo que da derecho a ELOGIAR (#436), y es el simétrico del de 120
    // que más abajo da derecho a acusar: con los 2 días que tenía antes, «el precio más bajo de los
    // últimos meses» se apoyaría en una sola observación.
    await seedProduct(r.id, 'Oferta real', [
      { price: 40, list: 39.99, daysAgo: 30 },
      { price: 19.99, list: 39.99, daysAgo: 0, discount: 50 },
    ]);
    // Bajada reciente: EXACTAMENTE la misma forma que 'Oferta real' —mínimo nuevo, mismo PVP
    // creíble, mismo 50 % declarado— pero descubierta anteayer. Es el 71,5 % del catálogo de QA, y
    // el único eje que la separa de la anterior es la cobertura (#436).
    await seedProduct(r.id, 'Bajada reciente', [
      { price: 40, list: 39.99, daysAgo: 2 },
      { price: 19.99, list: 39.99, daysAgo: 0, discount: 50 },
    ]);
    // Precio inflado: el precio actual NO es un mínimo reciente (ya estuvo a 15 €) y la tienda
    // enseña un tachado de 49,99 € con un 64 % -> descuento que no podemos corroborar, y a la vez
    // el mayor `discount_pct` declarado del catálogo: el caso que el orden tiene que degradar.
    //
    // El punto de hace 120 días es lo que da derecho a ACUSAR (#332): sin él llevaríamos tres días
    // mirando y "nunca ha costado 49,99 €" no sería una afirmación nuestra. El de hace 3 días hace
    // falta aparte, porque `recent_min` solo mira la ventana de 90 días y sin nada dentro de ella
    // el veredicto sería `none` por falta de histórico, no por prudencia.
    await seedProduct(r.id, 'Precio inflado', [
      { price: 15, list: 15, daysAgo: 120 },
      { price: 15, list: 15, daysAgo: 3 },
      { price: 18, list: 49.99, daysAgo: 0, discount: 64 },
    ]);
    // Sin corroborar: descubierta YA rebajada, y desde entonces no se ha movido. Lo más caro que la
    // hemos visto es su propio precio de rebaja, así que el tachado de 17,99 € puede ser verdad o
    // mentira y no tenemos con qué distinguirlo (#332). Antes esto se etiquetaba «Precio inflado».
    // Declara un 50 %, por debajo del 64 % de 'Precio inflado': así el caso de `sort=descuento`
    // sigue midiendo lo que medía —quién declara el mayor descuento— y no lo desplaza este.
    await seedProduct(r.id, 'Sin corroborar', [
      { price: 8.99, list: 17.99, daysAgo: 2, discount: 50 },
      { price: 8.99, list: 17.99, daysAgo: 0, discount: 50 },
    ]);
    // Recién visto: una sola observación, ya rebajada -> sin histórico no afirmamos nada.
    await seedProduct(r.id, 'Recién visto', [{ price: 12, list: 30, daysAgo: 0, discount: 60 }]);
    // Sin rebaja: histórico plano y sin tachado. Ni oferta ni engaño.
    await seedProduct(r.id, 'Sin rebaja', [
      { price: 25, list: null, daysAgo: 4 },
      { price: 25, list: null, daysAgo: 0 },
    ]);

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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
    // La misma bajada sin cobertura para sostenerla (#436): ha bajado, pero no la llamamos honesta.
    expect(byName.get('Bajada reciente')).toBe('reciente');
    expect(byName.get('Precio inflado')).toBe('suspicious');
    // Los dos veredictos que NO acusan, y que son cosas distintas (#332): en «Sin corroborar» hay
    // tachado y no podemos desmentirlo; en «Recién visto» no hay ni con qué empezar.
    expect(byName.get('Sin corroborar')).toBe('unverified');
    expect(byName.get('Recién visto')).toBe('none');
    expect(byName.get('Sin rebaja')).toBe('none');
  });

  /**
   * La regresión de #332 en su forma más directa: durante meses, una prenda descubierta ya
   * rebajada acababa acusada de precio inflado a la segunda pasada. Si alguien afloja el umbral,
   * esto rompe antes de que el catálogo vuelva a afirmar lo que no sabe.
   */
  it('una prenda descubierta ya rebajada nunca se etiqueta «Precio inflado»', async () => {
    const res = await request(app.getHttpServer()).get('/api/catalog/products').expect(200);
    const sinCorroborar = res.body.items.find(
      (i: { name: string }) => i.name === 'Sin corroborar',
    );

    expect(sinCorroborar.honesty).not.toBe('suspicious');
    expect(sinCorroborar.honesty).toBe('unverified');
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
    // Los seis sembrados: `reciente` NO se filtra del catálogo, solo se le retira el elogio (#436).
    expect(res.body.items).toHaveLength(6);
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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
    // ZAPATERÍA NO SE PLIEGA A BANDAS (#325): la talla es un número de pie, y plegarla daría un
    // filtro que no filtra lo que dice. Aquí sigue mandando `size_canon`, igual que antes.
    expect((await facetas('?section=zapateria')).sizes).toEqual(['20-21', '26', '48-51']);
    // ROPA sí: '11-12 años' es la banda de 11 (extremo bajo del rango) y el calcetín '36-38' no
    // tiene edad, así que cae en la banda de los números.
    expect((await facetas('?section=ropa')).sizes).toEqual(['11 años', 'Por número']);
  });

  /**
   * El síntoma que abrió la issue #64: un chip «48-51 años» en la faceta de zapatería. Ni se
   * entiende ni filtra lo que dice filtrar.
   */
  it('no etiqueta como edad un rango de número de pie (#64)', async () => {
    const zapateria = (await facetas('?section=zapateria')).sizes;
    expect(zapateria.filter((s) => s.includes('años'))).toEqual([]);
    // Y el calcetín es ROPA: aquí la única etiqueta de edad es la de la camiseta. Con las bandas
    // de #325 esto se cumple MÁS que antes, no menos: el calcetín ya no es un chip numérico suelto
    // entre edades, es 'Por número' — que es justo lo que #64 pedía, dicho con todas las letras.
    expect((await facetas('?section=ropa')).sizes.filter((s) => s.includes('años'))).toEqual([
      '11 años',
    ]);
    expect((await facetas('?section=ropa')).sizes).toContain('Por número');
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

  /**
   * #329. Los tres ejes multiseleccionables viajan como parámetro REPETIDO y se resuelven con
   * `= ANY(...)`, o sea unión. Lo que hay que sujetar son las dos mitades: que varios valores
   * sumen, y que **uno solo siga funcionando igual** — los marcadores de antes de este cambio y
   * los enlaces que el propio catálogo genera son de un valor.
   */
  it('dos tallas devuelven la UNIÓN de lo que devuelve cada una', async () => {
    const una = await nombres('/api/catalog/products?size=48-51');
    const otra = await nombres('/api/catalog/products?size=36-38');
    const dos = await nombres('/api/catalog/products?size=48-51&size=36-38');
    expect(dos).toEqual([...una, ...otra].sort());
  });

  it('una talla CON COMA sobrevive al viaje, que es lo que descarta separar por comas', async () => {
    // `26 (16,3 cm)` lleva una coma dentro: un separador por comas la partiría en dos tallas que
    // no existen, y el filtro devolvería vacío sin dar ningún error.
    expect(await nombres('/api/catalog/products?size=26%20(16%2C3%20cm)&size=48-51')).toEqual([
      'Bota Sfera',
      'Bota Zara',
      'Plantilla Cacles',
    ]);
  });

  it('dos tiendas devuelven las dos, y una sola sigue devolviendo la suya', async () => {
    const soloZara = await nombres('/api/catalog/products?retailer=zara');
    const dos = await nombres('/api/catalog/products?retailer=zara&retailer=sfera');
    expect(soloZara.every((n) => dos.includes(n))).toBe(true);
    expect(dos.length).toBeGreaterThan(soloZara.length);
  });

  it('un valor repetido no altera el resultado', async () => {
    // El DTO deduplica: `?size=26&size=26` es pedir la 26, no pedirla dos veces.
    expect(await nombres('/api/catalog/products?size=26&size=26')).toEqual([
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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
 * Familias de color (#291, migración 0029).
 *
 * El panel ofrecía 2.859 chips de color en `ropa` —el 85,2 % compuestos tipo 'amarillo claro/bluey',
 * donde lo que va detrás de la barra es el nombre del dibujo— y en un móvil eso no es un filtro.
 * Desde esta versión la faceta ofrece FAMILIAS y el parámetro `color` filtra por familia.
 *
 * Los colores sembrados son reales, de `deal_tracker_qa`, y cada uno está por un motivo distinto.
 */
describe.skipIf(!TEST_DB)('familias de color · catálogo (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  const FOTO_MARINO = 'https://static.example/p/marino-0.jpg';

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

    // 'azul marino' NO es de la familia 'azul': el orden de las reglas de color_family lo decide,
    // y es el par que se rompería primero si alguien lo cambiara.
    const idMarino = await seedColor(zara.id, 'Bota marino', 'AZUL MARINO');
    await seedColor(zara.id, 'Bota azul', 'Azul claro');

    // El compuesto que motiva plegar por el segmento anterior a la barra: mirando la cadena entera,
    // este se archiva como AZUL porque el nombre del dibujo lleva 'blue'. Son 385 colores (13,5 %).
    await seedColor(zara.id, 'Bota amarilla', 'amarillo claro/bluey');

    // 'rayas' no nombra ningún color, y aun así tiene que seguir siendo filtrable: hoy es un chip.
    await seedColor(zara.id, 'Bota rayas', 'RAYAS');

    // La foto se clava por el TEXTO del color (migración 0011), que la familia no toca.
    await sql`
      INSERT INTO product_image (product_id, color, position, url)
      VALUES (${idMarino}, 'AZUL MARINO', 0, ${FOTO_MARINO})`;

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

  it('la faceta ofrece familias, no los colores de la tienda', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/facets?section=zapateria')
      .expect(200);
    expect(res.body.colors).toEqual(['amarillo', 'azul', 'estampado', 'marino']);
  });

  it('el compuesto va a la familia de su primer segmento, no a la del dibujo', async () => {
    expect(await nombres('/api/catalog/products?color=amarillo')).toEqual(['Bota amarilla']);
    expect(await nombres('/api/catalog/products?color=azul')).not.toContain('Bota amarilla');
  });

  it('marino y azul siguen siendo familias distintas', async () => {
    expect(await nombres('/api/catalog/products?color=azul')).toEqual(['Bota azul']);
    expect(await nombres('/api/catalog/products?color=marino')).toEqual(['Bota marino']);
  });

  /**
   * Los enlaces antiguos llevaban un color específico. No rompen, pero se ENSANCHAN a su familia:
   * es la consecuencia aceptada de que el filtro pase a ser por familia.
   */
  it('un enlace antiguo con el color específico ahora devuelve su familia entera', async () => {
    expect(await nombres('/api/catalog/products?color=azul%20claro')).toEqual(['Bota azul']);
    expect(await nombres('/api/catalog/products?color=AZUL%20MARINO')).toEqual(['Bota marino']);
  });

  it('lo que no nombra un color sigue siendo filtrable como estampado', async () => {
    expect(await nombres('/api/catalog/products?color=estampado')).toEqual(['Bota rayas']);
    expect(await nombres('/api/catalog/products?color=rayas')).toEqual(['Bota rayas']);
  });

  /**
   * Lo que la issue prometía no tocar: el color específico se sigue guardando y enseñando, y la
   * foto por color sigue casando porque `variant.color` conserva el texto crudo de la tienda.
   */
  it('la tarjeta sigue enseñando el color de la tienda, no la familia', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?color=marino')
      .expect(200);
    expect(res.body.items[0].colorRepr).toBe('AZUL MARINO');
    expect(res.body.items[0].imageUrl).toBe(FOTO_MARINO);
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
    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

  it('dos artículos distintos con la misma talla y color sí cuentan dos', async () => {
    // El contraste del test anterior: aquí las dos caras tienen URL distinta, así que son dos
    // prendas comprables y no una publicada dos veces. Sujeta que la clave del DISTINCT siga
    // llevando la URL, que es lo que separa los dos casos.
    const res = await request(app.getHttpServer()).get('/api/catalog/products?retailer=hm');
    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
    expect(res.body.items[0].variantCount).toBe(2);
  });

  /**
   * Desde #307 el recuento se calcula fuera de `matched`, después del LIMIT, así que tiene que
   * repetir por su cuenta los filtros DE VARIANTE de la consulta. Estos tres los fijan: si alguno
   * se cayera, el contador diría 2 donde la lista solo enseña una prenda comprable.
   */
  it('variantCount respeta el filtro de stock, que mira la última fila de precio', async () => {
    // De las dos caras de H&M solo una está en stock: la lista sigue trayendo el producto, pero
    // solo cuenta la comprable. Es el discriminante del `ORDER BY scraped_at DESC LIMIT 1`.
    const res = await request(app.getHttpServer()).get('/api/catalog/products?retailer=hm&inStock=true');
    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
    expect(res.body.items[0].variantCount).toBe(1);
  });

  it('variantCount respeta el filtro de talla', async () => {
    const casa = await request(app.getHttpServer()).get('/api/catalog/products?retailer=hm&size=27');
    expect(casa.status).toBe(200);
    expect(casa.body.items).toHaveLength(1);
    expect(casa.body.items[0].variantCount).toBe(2);

    const noCasa = await request(app.getHttpServer()).get('/api/catalog/products?retailer=hm&size=99');
    expect(noCasa.status).toBe(200);
    expect(noCasa.body.items).toHaveLength(0);
  });

  /**
   * #326. `variant_count` se quedó filtrando por `color_canon` cuando #291 movió el filtro del
   * catálogo a `color_family`, así que un producto devuelto POR la familia declaraba **0 prendas
   * comprables** si ninguna de sus variantes se llamaba exactamente como la familia: 2.012 de los
   * 3.063 que devuelve `?color=azul`, medidos sobre una copia de dev.
   *
   * No lo cazó nadie porque los otros casos de `variantCount` usan colores de UNA palabra (`rojo`,
   * `azul`), donde `color_canon` y `color_family` coinciden y las dos semánticas dan lo mismo. Este
   * siembra `azul claro` a propósito —2.724 variantes en dev, la forma más común de la familia—,
   * que es la única manera de distinguirlas. (Ojo: `azul marino` NO vale, porque `color_family` le
   * da familia propia `marino`.)
   */
  it('variantCount cuenta por FAMILIA cuando el filtro es por familia (#326)', async () => {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${hm}, 'sudadera-claro', 'Sudadera azul claro', 'niño', 'ropa', 'sudaderas',
              NULL, 'https://hm/claro.html')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url)
      VALUES (${p.id}, 'claro-0', '104', 'azul claro', 'claro-sku', 'https://hm/claro.html')
      RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 9.99, 19.99, 50, true, now())`;
    try {
      const res = await request(app.getHttpServer())
        .get('/api/catalog/products?color=azul&section=ropa')
        .expect(200);
      const item = res.body.items.find((i: { id: number }) => i.id === Number(p.id));
      // El listado lo devuelve porque `azul claro` es de la familia `azul`...
      expect(item).toBeDefined();
      // ...así que su recuento no puede ser 0: sería declarar que dentro no hay nada que comprar.
      expect(item.variantCount).toBe(1);
    } finally {
      await sql`DELETE FROM price_history WHERE variant_id = ${v.id}`;
      await sql`DELETE FROM variant WHERE id = ${v.id}`;
      await sql`DELETE FROM product WHERE id = ${p.id}`;
    }
  });

  it('activeOnly=false no levanta el filtro de variantes retiradas', async () => {
    // `activeOnly` habla del producto, no de la variante. Con el producto descatalogado y una de
    // sus dos caras retirada, el recuento tiene que bajar de 2 a 1: si el contador de fuera se
    // dejara `delisted_at IS NULL`, seguiría diciendo 2 y prometería una prenda que ya no existe.
    const [agotada] = await sql<{ id: number }[]>`
      SELECT id FROM variant WHERE product_id = ${hmProduct} ORDER BY id LIMIT 1`;
    await sql`UPDATE product SET delisted_at = now() WHERE id = ${hmProduct}`;
    await sql`UPDATE variant SET delisted_at = now() WHERE id = ${agotada.id}`;
    try {
      const res = await request(app.getHttpServer()).get(
        '/api/catalog/products?retailer=hm&activeOnly=false',
      );
      expect(res.status).toBe(200);
      expect(res.body.items).toHaveLength(1);
      expect(res.body.items[0].variantCount).toBe(1);
      expect(res.body.items[0].anyInStock).toBe(true);
    } finally {
      await sql`UPDATE variant SET delisted_at = NULL WHERE id = ${agotada.id}`;
      await sql`UPDATE product SET delisted_at = NULL WHERE id = ${hmProduct}`;
    }
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

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
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

/**
 * Las facetas se cruzan con los filtros activos (#292).
 *
 * El fallo que arregla: el panel ofrecía chips que, con lo ya filtrado, no devuelven ni un
 * producto — se pinchaba una talla y el catálogo salía vacío. Medido sobre la copia de dev antes
 * de esto: de las 165 tallas que ofrecía `ropa`, al elegir una categoría solo 82 devolvían algo.
 *
 * El fixture está montado para que cada aserción tenga un chip que DEBE desaparecer y otro que
 * debe quedarse; con datos "planos" (todo cruzado con todo) estos tests pasarían sin cruzar nada.
 */
describe.skipIf(!TEST_DB)('facetas cruzadas con los filtros activos (#292)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Un producto con UNA variante. Lo mínimo para que un chip exista o deje de existir. */
  async function seed(
    retailerId: number,
    name: string,
    section: string,
    category: string,
    gender: string,
    size: string,
    color: string,
  ): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, ${gender}, ${section}, ${category},
              ${section === 'zapateria' ? 'si' : null}, 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, ${size}, ${color}, ${name + '-sku'}) RETURNING id`;
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

    // Ropa. La talla '2 años' SOLO existe en pantalones, y '8 años' SOLO en camisetas: es lo que
    // hace observable el cruce por categoría.
    await seed(zara.id, 'Pantalón niña', 'ropa', 'pantalones', 'niña', '2 años', 'azul');
    await seed(zara.id, 'Camiseta niña', 'ropa', 'camisetas', 'niña', '8 años', 'rojo');
    // El verde SOLO lo tiene una prenda de niño, y en una talla que nadie más usa.
    await seed(sfera.id, 'Pantalón niño', 'ropa', 'pantalones', 'niño', '6 años', 'verde');
    // Zapatería, para que la sección siga teniendo dos valores que ofrecer.
    await seed(sfera.id, 'Bota niña', 'zapateria', 'barefoot', 'niña', '26', 'negro');

    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const facetas = async (query = '') => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/facets${query}`).expect(200);
    return res.body as {
      sizes: string[];
      colors: string[];
      categories: string[];
      genders: string[];
      sections: string[];
      retailers: { slug: string }[];
    };
  };

  it('la categoría elegida se lleva por delante las tallas que no existen en ella', async () => {
    expect((await facetas('?section=ropa')).sizes).toEqual(['2 años', '6 años', '8 años']);
    // '8 años' es de camisetas: ofrecerlo aquí es prometer un catálogo vacío.
    expect((await facetas('?section=ropa&category=pantalones')).sizes).toEqual([
      '2 años',
      '6 años',
    ]);
  });

  it('el género acota talla y color, y el color acota la talla', async () => {
    expect((await facetas('?section=ropa&gender=ni%C3%B1o')).sizes).toEqual(['6 años']);
    expect((await facetas('?section=ropa&gender=ni%C3%B1o')).colors).toEqual(['verde']);
    // Y al revés: el verde solo vive en la talla del pantalón de niño.
    expect((await facetas('?section=ropa&color=verde')).sizes).toEqual(['6 años']);
  });

  it('la tienda acota, y es lo que hace usable la lista de tallas', async () => {
    // Cada tienda mide a su manera: elegir una es la vía rápida para quedarse con SU vocabulario.
    expect((await facetas('?section=ropa&retailer=zara')).sizes).toEqual(['2 años', '8 años']);
    expect((await facetas('?section=ropa&retailer=sfera')).sizes).toEqual(['6 años']);
  });

  it('la búsqueda libre acota las facetas igual que acota el listado', async () => {
    // Si no, se teclea «camiseta» y el panel sigue ofreciendo las tallas de los pantalones.
    expect((await facetas('?section=ropa&q=camiseta')).sizes).toEqual(['8 años']);
    expect((await facetas('?section=ropa&q=camiseta')).colors).toEqual(['rojo']);
  });

  /**
   * La regla que hace usable el filtrado por facetas, y la más fácil de romper sin darse cuenta:
   * una faceta NO se acota a sí misma. Si lo hiciera, elegir «2 años» dejaría «2 años» como única
   * talla ofrecida y no habría manera de cambiar de idea sin limpiar el filtro.
   */
  it('ninguna faceta se acota a sí misma', async () => {
    const conTalla = await facetas('?section=ropa&size=2%20a%C3%B1os');
    expect(conTalla.sizes).toEqual(['2 años', '6 años', '8 años']);
    // ...pero sí acota a las demás: con esa talla solo queda el pantalón azul de niña.
    expect(conTalla.colors).toEqual(['azul']);
    expect(conTalla.categories).toEqual(['pantalones']);

    const conColor = await facetas('?section=ropa&color=azul');
    expect(conColor.colors).toEqual(['azul', 'rojo', 'verde']);

    const conCategoria = await facetas('?section=ropa&category=pantalones');
    expect(conCategoria.categories).toEqual(['camisetas', 'pantalones']);

    const conTienda = await facetas('?section=ropa&retailer=zara');
    expect(conTienda.retailers.map((r) => r.slug).sort()).toEqual(['sfera', 'zara']);

    const conGenero = await facetas('?section=ropa&gender=ni%C3%B1o');
    expect(conGenero.genders.sort()).toEqual(['niña', 'niño']);
  });

  /**
   * `sections` es la excepción declarada: son las pestañas con las que se sale de la vista, y
   * desde #292 también las del grupo de talla. Unas pestañas que desaparecen según lo filtrado
   * dejarían al usuario encerrado en la sección en la que está.
   */
  it('la sección no la acota nada, ni siquiera un filtro que la deja sin productos', async () => {
    expect((await facetas('?section=ropa&category=pantalones')).sections).toEqual([
      'ropa',
      'zapateria',
    ]);
    // 'pantalones' no existe en zapatería y aun así la pestaña sigue ahí.
    expect((await facetas('?section=zapateria&color=azul')).sections).toEqual([
      'ropa',
      'zapateria',
    ]);
  });

  /**
   * La frontera declarada en `CatalogFilterDto`: los filtros que necesitan `price_history` no
   * cruzan, porque obligarían a la faceta a montar el CTE `latest` y las facetas se piden ahora en
   * cada cambio de filtro. Es una decisión de coste.
   *
   * Y la frontera **se nota**: el `ValidationPipe` global va con `forbidNonWhitelisted`, así que
   * mandarlos aquí no es que se ignoren, es que la petición se cae con 400. Eso es lo que se
   * quiere —una frontera silenciosa se cruza sin enterarse— pero obliga a la SPA a no reenviar el
   * objeto de filtros entero a `/facets`, y por eso está escrito en un test y no solo en el DTO.
   */
  it('inStock y onlyDeals no se aceptan en las facetas: 400, no se ignoran', async () => {
    await request(app.getHttpServer()).get('/api/catalog/facets?inStock=true').expect(400);
    await request(app.getHttpServer()).get('/api/catalog/facets?onlyDeals=true').expect(400);
    // Los que sí cruzan siguen aceptándose, claro.
    await request(app.getHttpServer()).get('/api/catalog/facets?section=ropa&q=camiseta').expect(200);
  });

  /**
   * El cruce solo sirve si promete lo mismo que el listado devuelve. Talla y color se aplican a la
   * MISMA variante, así que un chip ofrecido tiene que dar al menos un producto.
   */
  it('todo chip ofrecido devuelve al menos un producto', async () => {
    const base = '?section=ropa&category=pantalones';
    const f = await facetas(base);
    for (const size of f.sizes) {
      const res = await request(app.getHttpServer())
        .get(`/api/catalog/products${base}&size=${encodeURIComponent(size)}`)
        .expect(200);
      expect(res.body.items.length, `la talla «${size}» no devuelve nada`).toBeGreaterThan(0);
    }
    for (const color of f.colors) {
      const res = await request(app.getHttpServer())
        .get(`/api/catalog/products${base}&color=${encodeURIComponent(color)}`)
        .expect(200);
      expect(res.body.items.length, `el color «${color}» no devuelve nada`).toBeGreaterThan(0);
    }
  });
});

/**
 * Rango de precio (#290).
 *
 * Lo que hay que sujetar no es "filtra", que es evidente, sino **dónde** filtra: sobre `price_from`
 * ya agregado, en el `WHERE` exterior. Si alguien lo moviera al servicio —sobre la página ya
 * recortada— los tests de paginación de aquí abajo se caerían, que es justo para lo que están.
 */
describe.skipIf(!TEST_DB)('rango de precio (#290)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  /** Un producto con una variante a un precio dado. `price_from` acaba siendo ese precio. */
  async function seedPrecio(retailerId: number, name: string, precio: string): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category, url)
      VALUES (${retailerId}, ${name}, ${name}, 'niña', 'ropa', 'camisetas', 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '4 años', 'azul', ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, ${precio}, 99.99, 10, true, now())`;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [zara] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;
    // Los valores están elegidos para caer JUSTO en los bordes de las consultas de abajo.
    await seedPrecio(zara.id, 'A 5', '5.00');
    await seedPrecio(zara.id, 'B 10', '10.00');
    await seedPrecio(zara.id, 'C 15', '15.00');
    await seedPrecio(zara.id, 'D 20', '20.00');
    await seedPrecio(zara.id, 'E 25', '25.00');
    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/products${query}`).expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('los dos extremos INCLUYEN', async () => {
    // Quien pide "de 10 a 20" espera ver el de 10 y el de 20. Un `>`/`<` aquí se nota poco y
    // desconcierta mucho: el producto que se acaba de ver en la tarjeta desaparece al filtrar.
    expect(await nombres('?minPrice=10&maxPrice=20')).toEqual(['B 10', 'C 15', 'D 20']);
  });

  it('cada extremo funciona por su cuenta', async () => {
    expect(await nombres('?maxPrice=10')).toEqual(['A 5', 'B 10']);
    expect(await nombres('?minPrice=20')).toEqual(['D 20', 'E 25']);
  });

  it('un rango invertido devuelve vacío, no un error', async () => {
    // La SPA no lo permite (los topes se bloquean entre sí), pero la URL se puede escribir a mano.
    expect(await nombres('?minPrice=20&maxPrice=10')).toEqual([]);
  });

  it('un precio no numérico es 400, no un filtro silenciosamente ignorado', async () => {
    await request(app.getHttpServer()).get('/api/catalog/products?minPrice=barato').expect(400);
    await request(app.getHttpServer()).get('/api/catalog/products?minPrice=-1').expect(400);
  });

  /**
   * El motivo de que el filtro viva en el `WHERE` exterior y no en el servicio: paginando, la
   * segunda página tiene que continuar donde acabó la primera. Filtrar después del `LIMIT` daría
   * páginas de tamaño irregular y saltos.
   */
  it('la paginación cuenta sobre lo filtrado, no sobre el catálogo entero', async () => {
    const p1 = await request(app.getHttpServer())
      .get('/api/catalog/products?minPrice=10&maxPrice=20&sort=precio-asc&limit=2&offset=0')
      .expect(200);
    const p2 = await request(app.getHttpServer())
      .get('/api/catalog/products?minPrice=10&maxPrice=20&sort=precio-asc&limit=2&offset=2')
      .expect(200);
    expect(p1.body.items.map((i: { name: string }) => i.name)).toEqual(['B 10', 'C 15']);
    expect(p2.body.items.map((i: { name: string }) => i.name)).toEqual(['D 20']);
  });

  it('convive con el resto de filtros del mismo WHERE exterior', async () => {
    // `onlyDeals` vive en ese mismo sitio: los dos tienen que componerse, no pisarse.
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?minPrice=10&maxPrice=20&onlyDeals=true')
      .expect(200);
    // Ninguno es oferta real (un solo punto de precio, sin histórico que lo respalde).
    expect(res.body.items).toEqual([]);
    expect(await nombres('?minPrice=10&maxPrice=20&category=camisetas')).toEqual([
      'B 10',
      'C 15',
      'D 20',
    ]);
  });

  it('el precio no llega a las facetas: son 400 igual que inStock y onlyDeals', async () => {
    // Misma frontera de coste que el resto de filtros de precio (ver `CatalogFilterDto`).
    await request(app.getHttpServer()).get('/api/catalog/facets?minPrice=10').expect(400);
    await request(app.getHttpServer()).get('/api/catalog/facets?maxPrice=10').expect(400);
  });
});

/**
 * Dos medidas bajo la misma etiqueta de talla (#331).
 *
 * El caso real: H&M publica en recien nacido '0-1 meses (44 cm)' y '0-1 meses (50 cm)', que son dos
 * prendas distintas, y `size_canon` las funde. Hipercor tiene el mismo defecto con otra sintaxis
 * (' - Medida N cm') y ADEMAS el caso contrario, en el que el sufijo solo repite la misma talla.
 *
 * Lo que se prueba aqui es la REGLA que los separa, que es la unica parte que no puede vivir en
 * `size_canon`: una funcion escalar ve una cadena suelta, y la diferencia esta en el conjunto de
 * variantes del producto. Medido el 13/08/2026: con la regla, 22 fichas del catalogo crecen y
 * ninguna mas de +2 chips; partiendo por el texto crudo eran 27 fichas y hasta +7.
 */
describe.skipIf(!TEST_DB)('dos medidas bajo una misma talla . ficha (e2e)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  async function seedTallas(
    retailerId: number,
    nombre: string,
    tallas: string[],
  ): Promise<number> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${nombre}, ${nombre}, 'niña', 'ropa', 'camisetas', 'si',
              ${'https://x/' + nombre})
      RETURNING id`;
    for (const [i, talla] of tallas.entries()) {
      // MISMA url en todas: es el caso que hoy las colapsa en un solo chip y esconde una.
      const [v] = await sql<{ id: number }[]>`
        INSERT INTO variant (product_id, retailer_variant_id, size, color, sku, url)
        VALUES (${p.id}, ${nombre + '-' + i}, ${talla}, 'BLANCO', ${nombre + '-sku-' + i},
                ${'https://x/' + nombre})
        RETURNING id`;
      await sql`
        INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
        VALUES (${v.id}, 9.99, 19.99, 50, true, now())`;
    }
    return Number(p.id);
  }

  const etiquetas = async (id: number): Promise<string[]> => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${id}`)
      .expect(200);
    return (res.body.variants as { sizeLabel: string | null }[])
      .map((v) => v.sizeLabel)
      .filter((x): x is string => !!x)
      .sort();
  };

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    // El agregado por producto que lee el catálogo lo puebla la ingesta (0035, #314), y aquí se
    // siembra a mano: sin esto el listado no vería NADA. Ver `refrescarAgregado` en helpers.
    await refrescarAgregado(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  it('DOS chips cuando la tienda publica dos medidas distintas: es el caso de H&M', async () => {
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('hm', 'H&M', 'https://hm.com') RETURNING id`;
    const id = await seedTallas(r.id, 'HM-RN', [
      '0-1 meses (44 cm)',
      '0-1 meses (50 cm)',
      '1-2 meses (56 cm)',
    ]);
    // La medida sale SOLO en las dos que la necesitan para poder elegir.
    expect(await etiquetas(id)).toEqual([
      '0-1 meses \u00b7 44 cm',
      '0-1 meses \u00b7 50 cm',
      '1-2 meses',
    ]);
  });

  it('UN chip cuando el sufijo solo repite la talla: es el caso de Hipercor', async () => {
    // Sin esta mitad, la ficha del «Pack 5 slips» pasaba de 7 chips a 14 (medido en qa).
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('hipercor', 'Hipercor', 'https://hipercor.es') RETURNING id`;
    const id = await seedTallas(r.id, 'HC-SLIPS', [
      '9-10 años',
      '9-10 años - Medida 128 cm',
      '11-12 años',
      '11-12 años - Medida 140 cm',
    ]);
    expect(await etiquetas(id)).toEqual(['11-12 años', '9-10 años']);
  });

  it('UN chip cuando solo cambia la caja de las letras', async () => {
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('hipercor2', 'Hipercor 2', 'https://hipercor.es/2') RETURNING id`;
    const id = await seedTallas(r.id, 'HC-CAJA', ['12 Meses', '12 meses', '18 meses']);
    expect(await etiquetas(id)).toEqual(['12 meses', '18 meses']);
  });

  it('la talla CRUDA y la CANONICA no se mueven: son la clave y lo que se guarda al seguir', async () => {
    // Es lo que impide que este cambio toque `interest.size` ni el casado del aviso: la etiqueta
    // es un rotulo y nada mas. Si algun dia `sizeCanon` empezara a traer los cm, esto se cae.
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('hm2', 'H&M 2', 'https://hm.com/2') RETURNING id`;
    const id = await seedTallas(r.id, 'HM-RN2', ['0-1 meses (44 cm)', '0-1 meses (50 cm)']);
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products/${id}`)
      .expect(200);
    const vs = res.body.variants as { size: string; sizeCanon: string; sizeLabel: string }[];
    expect(vs.map((v) => v.size).sort()).toEqual(['0-1 meses (44 cm)', '0-1 meses (50 cm)']);
    expect(vs.map((v) => v.sizeCanon)).toEqual(['0-1 meses', '0-1 meses']);
  });
});

/**
 * El segundo piso de la talla (#367): la CONCRETA dentro de la banda.
 *
 * `size` pliega a `size_band` en ropa y ese plegado se aplica también a lo que llega por la URL, así
 * que sin este eje **no hay forma de pedir un valor concreto**: `?size=104` devuelve la banda entera.
 * Es la contrapartida que #325 dejó anotada y sin marcar al plegar 181 tallas a 21 bandas.
 *
 * El fixture es el caso real medido en QA: la banda `4 años` la publican cuatro vocabularios
 * distintos —`4 años`, `4-5 años`, `4-6 años` y `104`—, que es exactamente lo que hace que el
 * plegado sea necesario y lo que este eje permite volver a distinguir.
 */
describe.skipIf(!TEST_DB)('talla concreta dentro de la banda · segundo piso (e2e)', () => {
  let app: INestApplication;
  let sql: postgres.Sql;

  async function seed(name: string, size: string, category = 'pantalones'): Promise<void> {
    const [r] = await sql<{ id: number }[]>`
      SELECT id FROM retailer WHERE slug = 'zara'`;
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category, url)
      VALUES (${r.id}, ${name}, ${name}, 'niña', 'ropa', ${category}, 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, ${size}, 'azul', ${name + '-sku'}) RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    await sql`
      INSERT INTO retailer (slug, name, base_url) VALUES ('zara', 'Zara', 'https://www.zara.com')`;
    // Las cuatro formas de decir «4 años», una por producto, más una prenda de otra banda.
    await seed('P-4-anios', '4 años');
    await seed('P-4-5', '4-5 años');
    await seed('P-4-6', '4-6 años');
    await seed('P-104', '104');
    await seed('P-6', '6 años');
    // Una de zapatería, donde el primer piso YA es la talla concreta.
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('cacles', 'Cacles', 'https://cacles.example') RETURNING id`;
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${r.id}, 'Z-26', 'Bota', 'niña', 'zapateria', 'botas', 'si', 'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, 'Z-26-v', '26', 'negro', 'Z-26-sku') RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.99, 39.99, 50, true, now())`;

    await refrescarAgregado(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const nombres = async (query: string): Promise<string[]> => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/products${query}`).expect(200);
    return (res.body.items as { name: string }[]).map((i) => i.name).sort();
  };

  const facetas = async (query: string) => {
    const res = await request(app.getHttpServer()).get(`/api/catalog/facets${query}`).expect(200);
    return res.body as { sizes: string[]; sizeValues: string[] };
  };

  it('la banda sigue devolviendo los cuatro vocabularios: es lo que #325 vino a hacer', async () => {
    expect(await nombres('?section=ropa&size=4%20a%C3%B1os')).toEqual([
      'P-104',
      'P-4-5',
      'P-4-6',
      'P-4-anios',
    ]);
  });

  it('la talla CONCRETA devuelve solo la suya, que hasta ahora era imposible pedir', async () => {
    expect(await nombres('?section=ropa&sizeExact=104')).toEqual(['P-104']);
    expect(await nombres('?section=ropa&sizeExact=4-5%20a%C3%B1os')).toEqual(['P-4-5']);
  });

  it('varias concretas se unen, como todo eje multiseleccionable (#329)', async () => {
    expect(await nombres('?section=ropa&sizeExact=104&sizeExact=4%20a%C3%B1os')).toEqual([
      'P-104',
      'P-4-anios',
    ]);
  });

  it('banda y concreta se CRUZAN, no se sustituyen', async () => {
    // La banda es dónde estás; la concreta, lo que pides dentro.
    expect(await nombres('?section=ropa&size=4%20a%C3%B1os&sizeExact=104')).toEqual(['P-104']);
    // Y una pareja incoherente devuelve vacío, que es lo correcto: el error está en el enlace.
    expect(await nombres('?section=ropa&size=6%20a%C3%B1os&sizeExact=104')).toEqual([]);
  });

  it('la faceta ofrece las concretas de la banda elegida, y NADA sin banda', async () => {
    // Sin banda estaría ofreciendo las 181 tallas de ropa, o sea deshaciendo #325.
    expect((await facetas('?section=ropa')).sizeValues).toEqual([]);
    expect((await facetas('?section=ropa&size=4%20a%C3%B1os')).sizeValues).toEqual([
      '4 años',
      '4-5 años',
      '4-6 años',
      '104',
    ]);
  });

  it('la faceta de concretas NO se acota a sí misma', async () => {
    // Si lo hiciera, marcar `104` dejaría `104` como única opción y no habría forma de añadir otra
    // sin limpiar antes. Es la misma regla que ya sujeta a las demás facetas.
    const f = await facetas('?section=ropa&size=4%20a%C3%B1os&sizeExact=104');
    expect(f.sizeValues).toEqual(['4 años', '4-5 años', '4-6 años', '104']);
  });

  it('en zapatería no hay segundo piso: el primero ya es la talla concreta', async () => {
    const f = await facetas('?section=zapateria&size=26');
    expect(f.sizes).toEqual(['26']);
    expect(f.sizeValues).toEqual([]);
  });

  it('el recuento de variantes que casan respeta la concreta (el fallo de #326, un eje después)', async () => {
    const res = await request(app.getHttpServer())
      .get('/api/catalog/products?section=ropa&size=4%20a%C3%B1os&sizeExact=104')
      .expect(200);
    const items = res.body.items as { name: string; variantCount: number }[];
    expect(items.map((i) => [i.name, i.variantCount])).toEqual([['P-104', 1]]);
  });
});

/**
 * El buscador no casa el género por subcadena (#408).
 *
 * El seed de los otros bloques no sirve para esto y conviene decir por qué: sus prendas se llaman
 * «Botas niña» o «Sandalia de niño», así que el término `ni` casa por el **nombre** y el colapso del
 * género queda tapado. Aquí los tres nombres y las tres categorías están elegidos para **no
 * contener `ni`**, de forma que lo único que puede hacerlos aparecer es el género.
 *
 * Contra `deal_tracker_qa` el 14/08/2026 esto valía 16.844 de 16.844 productos con `ni` y 14.918
 * con `nin`; en un seed de tres, 3 y 2.
 */
describe.skipIf(!TEST_DB)('búsqueda · el género no casa por subcadena (#408)', () => {
  let sql: postgres.Sql;
  let app: INestApplication;

  async function seedPrenda(
    retailerId: number,
    name: string,
    gender: string,
    category: string,
  ): Promise<void> {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url)
      VALUES (${retailerId}, ${name}, ${name}, ${gender}, 'zapateria', ${category}, 'si',
              'https://x')
      RETURNING id`;
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
      VALUES (${p.id}, ${name + '-v'}, '24', 'azul', ${name + '-sku'})
      RETURNING id`;
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${v.id}, 19.90, 19.90, 0, true, now())`;
  }

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;
    // Ni los nombres ni las categorías contienen 'ni'. Es la condición del experimento.
    await seedPrenda(r.id, 'Botas de agua', 'niña', 'botas');
    await seedPrenda(r.id, 'Zapato colegial', 'niño', 'zapatos');
    await seedPrenda(r.id, 'Chanclas de playa', 'unisex', 'sandalias');
    await refrescarAgregado(sql);
    app = await makeApp();
  });

  afterAll(async () => {
    await app.close();
    await sql.end();
  });

  const buscar = async (q: string): Promise<string[]> => {
    const res = await request(app.getHttpServer())
      .get(`/api/catalog/products?q=${encodeURIComponent(q)}`)
      .expect(200);
    return res.body.items.map((i: { name: string }) => i.name).sort();
  };

  it('un prefijo que no es un género completo ya no arrastra el catálogo', async () => {
    // Antes de #408: `ni` devolvía los tres (está dentro de niño, niña Y unisex) y `nin` los dos
    // primeros. La SPA busca según se teclea, así que quien escribe «niña» pasaba por los dos.
    expect(await buscar('ni')).toEqual([]);
    expect(await buscar('nin')).toEqual([]);
    expect(await buscar('uni')).toEqual([]);
    // Y el sufijo tampoco, que es el mismo defecto por el otro lado.
    expect(await buscar('sex')).toEqual([]);
  });

  it('el género entero se sigue buscando, que es lo que #229 midió y hay que preservar', async () => {
    expect(await buscar('niña')).toEqual(['Botas de agua']);
    expect(await buscar('nina')).toEqual(['Botas de agua']); // tecleado sin la ñ
    expect(await buscar('niño')).toEqual(['Zapato colegial']);
    expect(await buscar('nino')).toEqual(['Zapato colegial']);
    expect(await buscar('unisex')).toEqual(['Chanclas de playa']);
  });

  it('cada término se resuelve por su cuenta: uno por texto y otro por género', async () => {
    // El caso que motivó meter el género en la búsqueda (PR #38): «botas niña» = categoría + género.
    expect(await buscar('botas nina')).toEqual(['Botas de agua']);
    expect(await buscar('nina botas')).toEqual(['Botas de agua']);
    expect(await buscar('agua nina')).toEqual(['Botas de agua']); // nombre + género
    expect(await buscar('botas nino')).toEqual([]); // la categoría es de la otra
    // Y un término no puede casar a caballo entre el texto y el género.
    expect(await buscar('aguanina')).toEqual([]);
  });

  it('la faceta entiende lo tecleado igual que el listado', async () => {
    const facetas = async (q: string): Promise<string[]> => {
      const res = await request(app.getHttpServer())
        .get(`/api/catalog/facets?q=${encodeURIComponent(q)}`)
        .expect(200);
      return (res.body.genders as string[]).slice().sort();
    };
    // Si esto devolviera los tres géneros, el panel ofrecería chips que el listado no puede llenar
    // — que es exactamente el fallo que la duplicación de esta condición hacía posible.
    expect(await facetas('ni')).toEqual([]);
    expect(await facetas('nina')).toEqual(['niña']);
  });
});
