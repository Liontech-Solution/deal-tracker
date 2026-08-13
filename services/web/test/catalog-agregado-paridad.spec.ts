import { drizzle } from 'drizzle-orm/postgres-js';
import type postgres from 'postgres';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { CatalogService } from '../src/catalog/catalog.service';
import { ProductQueryDto } from '../src/catalog/dto/product-query.dto';
import { schema } from '../src/database/schema';
import { HONESTY_WINDOW_DAYS } from '../src/matching/deal-rule';
import { makeSql, resetSchema, TEST_DB } from './helpers';

/**
 * Paridad entre los DOS caminos del listado (#314, migración 0035).
 *
 * Desde la 0035 `listProducts()` tiene dos formas de conseguir el agregado por producto:
 *
 *  - **precomputado** — leer `product_agg`, que dejó hecho `refresh_product_agg()` en la ingesta.
 *    Es el que se usa mientras no haya filtro de variante, o sea el caso ancho y el que tardaba.
 *  - **vivo** — agregar las variantes en tiempo de consulta, como se ha hecho siempre. Se usa en
 *    cuanto entra un filtro de `size`, `color` o `inStock`.
 *
 * Los dos tienen que devolver **exactamente lo mismo**, y nada en el compilador lo garantiza: son
 * dos SQL en dos ficheros distintos, uno en `catalog.service.ts` y otro dentro de la función de
 * `db/migrations/0035_product_agg.sql`. Si alguien toca uno y no el otro, el catálogo empieza a
 * contestar cosas distintas según qué filtro lleves puesto — y solo en producción, donde hay datos
 * suficientes para que se note.
 *
 * Es la misma medicina que `deal-rule-paridad.spec.ts` aplica al espejo SQL de la honestidad
 * (#228): un test que ejecuta los dos lados sobre los mismos datos y los compara.
 *
 * El seed está hecho para pisar justo donde duele, no para ser bonito:
 *
 *  - **empates** — dos variantes con el mismo precio y el mismo stock. Antes de #314 el
 *    `array_agg(...)[1]` las desempataba con lo que el ejecutor entregara primero, así que los dos
 *    caminos elegían colores distintos: 2.393 discrepancias sobre los 16.517 productos de QA.
 *  - **el borde de la ventana de 90 días** — una observación justo fuera y otra justo dentro, que
 *    es el único valor que esta migración duplica en TypeScript y en SQL.
 *  - **una variante sin histórico** y **una variante dada de baja**, que los dos caminos tienen que
 *    dejar fuera por igual.
 *  - **un producto sin ninguna variante viva con precio**, que no debe aparecer en ninguno de los
 *    dos (en el vivo lo tiran los JOIN; en el precomputado, no tener fila en `product_agg`).
 */
describe.skipIf(!TEST_DB)('paridad entre el agregado precomputado y el vivo (#314)', () => {
  let sql: postgres.Sql;
  let servicio: CatalogService;

  beforeAll(async () => {
    sql = makeSql();
    await resetSchema(sql);
    await sembrar(sql);
    await sql`SELECT refresh_product_agg()`;
    servicio = new CatalogService(drizzle(sql, { schema }));
  });

  afterAll(async () => {
    await sql.end();
  });

  /** Los dos caminos, sobre la misma consulta. */
  async function ambos(ajusta: (q: ProductQueryDto) => void = () => {}) {
    const hacerQuery = () => {
      const q = new ProductQueryDto();
      q.sort = 'ofertas';
      q.activeOnly = true;
      q.limit = 100;
      q.offset = 0;
      q.barefoot = 'si';
      ajusta(q);
      return q;
    };
    const precomputado = await servicio.listProducts(hacerQuery());
    const vivo = await servicio.listProducts(hacerQuery(), { forzarAgregadoVivo: true });
    return { precomputado, vivo };
  }

  it('el seed llega a los dos caminos (si no, esto no probaría nada)', async () => {
    const { precomputado, vivo } = await ambos();
    expect(precomputado.items.length).toBeGreaterThan(1);
    expect(vivo.items.length).toBe(precomputado.items.length);
  });

  // Cada caso mueve un filtro DE PRODUCTO, que son los que el camino precomputado tiene que saber
  // aplicar sobre el agregado ya hecho.
  const casos: Array<[string, (q: ProductQueryDto) => void]> = [
    ['sin filtros', () => {}],
    ['por género', (q) => (q.gender = 'niña')],
    ['por sección', (q) => (q.section = 'zapateria')],
    ['por categoría', (q) => (q.category = 'zapatos')],
    ['por tienda', (q) => (q.retailer = ['zara'])],
    ['por búsqueda', (q) => (q.q = 'botas')],
    ['barefoot=all', (q) => (q.barefoot = 'all')],
    ['solo ofertas reales', (q) => (q.onlyDeals = true)],
    ['rango de precio', (q) => ((q.minPrice = 5), (q.maxPrice = 30))],
    ['orden precio-asc', (q) => (q.sort = 'precio-asc')],
    ['orden precio-desc', (q) => (q.sort = 'precio-desc')],
    ['orden descuento', (q) => (q.sort = 'descuento')],
    ['segunda página', (q) => ((q.limit = 2), (q.offset = 2))],
    ['incluyendo retirados', (q) => (q.activeOnly = false)],
  ];

  it.each(casos)('devuelve lo mismo por los dos caminos: %s', async (_nombre, ajusta) => {
    const { precomputado, vivo } = await ambos(ajusta);
    // El objeto entero, no unos campos elegidos: lo que se vigila es que no se bifurquen, y
    // comparar solo lo que se me ocurra hoy dejaría fuera justo lo que alguien añada mañana.
    expect(precomputado.items).toEqual(vivo.items);
    expect(precomputado.limit).toBe(vivo.limit);
    expect(precomputado.offset).toBe(vivo.offset);
  });

  it('el empate se desempata igual en los dos caminos, y de forma estable', async () => {
    const { precomputado, vivo } = await ambos((q) => (q.q = 'empate'));
    expect(precomputado.items).toHaveLength(1);
    expect(precomputado.items[0].colorRepr).toBe(vivo.items[0].colorRepr);

    // Y no solo coinciden entre sí: repetir la consulta da lo mismo. Sin el desempate por
    // variant_id esto podía cambiar entre dos peticiones idénticas.
    const otraVez = await ambos((q) => (q.q = 'empate'));
    expect(otraVez.precomputado.items[0].colorRepr).toBe(precomputado.items[0].colorRepr);
  });

  it('la ventana de honestidad es la misma en el SQL de la 0035 que en deal-rule.ts', async () => {
    // El seed pone una observación DENTRO de la ventana y otra FUERA, con precios distintos, así
    // que `recent_min` solo coincide si las dos ventanas son la misma. Es el único valor que la
    // migración duplica en TypeScript, y este es el test que lo sujeta.
    expect(HONESTY_WINDOW_DAYS).toBe(90);
    const { precomputado, vivo } = await ambos((q) => (q.q = 'ventana'));
    expect(precomputado.items).toHaveLength(1);
    expect(precomputado.items[0].honesty).toEqual(vivo.items[0].honesty);
    expect(precomputado.items[0]).toEqual(vivo.items[0]);
  });

  /**
   * Este caso NO se puede comprobar con datos, y conviene saber por qué antes de "simplificarlo".
   *
   * Comparar los resultados de los dos caminos sobre el seed **no** caza que a uno se le caiga el
   * desempate: con pocas filas Postgres elige el mismo plan en los dos y devuelve el mismo orden
   * por casualidad. Está medido — quitando el `variant_id` del servicio, los 19 casos de arriba
   * seguían en verde. Lo que sí lo cazó fue un `EXCEPT` de los dos caminos sobre los 16.517
   * productos de QA: 2.393 `color_repr` distintos.
   *
   * Como el seed no puede reproducir eso, se vigila la **forma** de los dos SQL, que es lo único
   * que aquí es determinista. Es un test de texto a sabiendas.
   */
  it('los dos caminos llevan el desempate por variant_id', async () => {
    const [{ def }] = await sql<{ def: string }[]>`
      SELECT pg_get_functiondef('refresh_product_agg(bigint)'::regprocedure) AS def`;
    const enLaFuncion = def.match(/ORDER BY in_stock DESC, price ASC, variant_id\)\)\[1\]/g) ?? [];
    expect(enLaFuncion).toHaveLength(8);

    // Y el mismo recuento en el camino vivo del servicio.
    const capturadas: string[] = [];
    const espia = {
      execute: (q: { queryChunks?: unknown[] }) => {
        capturadas.push(JSON.stringify(q));
        return Promise.resolve([]);
      },
    };
    const q = new ProductQueryDto();
    q.barefoot = 'si';
    q.sort = 'ofertas';
    await new CatalogService(espia as never).listProducts(q, { forzarAgregadoVivo: true });
    const enElServicio =
      capturadas[0].match(/ORDER BY in_stock DESC, price ASC, variant_id\)\)\[1\]/g) ?? [];
    expect(enElServicio).toHaveLength(8);
  });

  it('un producto sin variantes vivas con precio no sale por ninguno de los dos', async () => {
    const { precomputado, vivo } = await ambos((q) => (q.q = 'fantasma'));
    expect(precomputado.items).toHaveLength(0);
    expect(vivo.items).toHaveLength(0);
  });

  it('refrescar una sola tienda no toca las filas de las demás', async () => {
    const antes = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM product_agg`;
    const [{ id: otra }] = await sql<{ id: number }[]>`
      SELECT id FROM retailer WHERE slug = 'sfera'`;
    await sql`SELECT refresh_product_agg(${otra})`;
    const despues = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM product_agg`;
    expect(despues[0].n).toBe(antes[0].n);

    // Y sigue dando lo mismo que el camino vivo después de un refresco parcial.
    const { precomputado, vivo } = await ambos();
    expect(precomputado.items).toEqual(vivo.items);
  });
});

/**
 * Catálogo pequeño pero con todos los bordes que separan los dos caminos.
 *
 * Dos tiendas, para poder comprobar que el refresco por tienda no arrastra a la otra.
 */
async function sembrar(sql: postgres.Sql): Promise<void> {
  const [zara] = await sql<{ id: number }[]>`
    INSERT INTO retailer (slug, name, base_url)
    VALUES ('zara', 'Zara', 'https://www.zara.com') RETURNING id`;
  const [sfera] = await sql<{ id: number }[]>`
    INSERT INTO retailer (slug, name, base_url)
    VALUES ('sfera', 'Sfera', 'https://www.sfera.com') RETURNING id`;

  const producto = async (
    retailerId: number,
    rpid: string,
    nombre: string,
    extra: { delisted?: boolean } = {},
  ) => {
    const [p] = await sql<{ id: number }[]>`
      INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                           barefoot, url, image_url, delisted_at)
      VALUES (${retailerId}, ${rpid}, ${nombre}, 'niña', 'zapateria', 'zapatos', 'si',
              ${'https://x/' + rpid}, ${'https://img/' + rpid},
              ${extra.delisted ? sql`now()` : null})
      RETURNING id`;
    return p.id;
  };

  const variante = async (
    productId: number,
    rvid: string,
    talla: string,
    color: string,
    baja = false,
  ) => {
    const [v] = await sql<{ id: number }[]>`
      INSERT INTO variant (product_id, retailer_variant_id, size, color, delisted_at)
      VALUES (${productId}, ${rvid}, ${talla}, ${color}, ${baja ? sql`now()` : null})
      RETURNING id`;
    return v.id;
  };

  const precio = async (
    variantId: number,
    price: string,
    listPrice: string | null,
    diasAtras: number,
    inStock = true,
  ) => {
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
      VALUES (${variantId}, ${price}, ${listPrice},
              ${listPrice ? sql`round((1 - ${price}::numeric / ${listPrice}::numeric) * 100, 2)` : null},
              ${inStock}, now() - make_interval(days => ${diasAtras}))`;
  };

  // 1. EMPATE: dos variantes al mismo precio y mismo stock. Solo el desempate por variant_id
  //    hace que los dos caminos elijan el mismo color.
  const empate = await producto(zara.id, 'Z-EMPATE', 'Botas empate');
  const e1 = await variante(empate, 'Z-EMPATE-24-rojo', '24', 'rojo');
  const e2 = await variante(empate, 'Z-EMPATE-24-azul', '24', 'azul');
  await precio(e1, '30.00', '40.00', 3);
  await precio(e1, '20.00', '40.00', 0);
  await precio(e2, '30.00', '40.00', 3);
  await precio(e2, '20.00', '40.00', 0);

  // 2. VENTANA: una observación justo DENTRO de los 90 días y otra bastante FUERA, con precios
  //    distintos, para que `recent_min` distinga las dos ventanas si alguna vez dejan de coincidir.
  const ventana = await producto(zara.id, 'Z-VENTANA', 'Botas ventana');
  const v1 = await variante(ventana, 'Z-VENTANA-25-verde', '25', 'verde');
  await precio(v1, '12.00', '50.00', 200); // fuera de la ventana: barato y viejo
  await precio(v1, '45.00', '50.00', 30); // dentro
  await precio(v1, '25.00', '50.00', 0); // el último

  // 3. Producto normal, con precios distintos por variante y una variante DADA DE BAJA que los
  //    dos caminos tienen que ignorar.
  const normal = await producto(zara.id, 'Z-NORMAL', 'Botas normales');
  const n1 = await variante(normal, 'Z-NORMAL-26-negro', '26', 'negro');
  const n2 = await variante(normal, 'Z-NORMAL-27-blanco', '27', 'blanco');
  const nBaja = await variante(normal, 'Z-NORMAL-28-gris', '28', 'gris', true);
  await precio(n1, '60.00', '60.00', 5);
  await precio(n1, '55.00', '60.00', 0);
  await precio(n2, '70.00', null, 0, false);
  await precio(nBaja, '1.00', '99.00', 0); // baratísima, pero está de baja: no debe asomar

  // 4. FANTASMA: producto cuyas variantes no tienen histórico ninguno.
  const fantasma = await producto(zara.id, 'Z-FANTASMA', 'Botas fantasma');
  await variante(fantasma, 'Z-FANTASMA-24-rosa', '24', 'rosa');

  // 5. Producto RETIRADO, para el caso `activeOnly = false`.
  const retirado = await producto(zara.id, 'Z-RETIRADO', 'Botas retiradas', { delisted: true });
  const r1 = await variante(retirado, 'Z-RETIRADO-24-lila', '24', 'lila');
  await precio(r1, '15.00', '30.00', 0);

  // 6. Otra tienda, para el refresco parcial.
  const otra = await producto(sfera.id, 'S-1', 'Botas de Sfera');
  const s1 = await variante(otra, 'S-1-24-marron', '24', 'marrón');
  await precio(s1, '40.00', '80.00', 4);
  await precio(s1, '35.00', '80.00', 0);
}
