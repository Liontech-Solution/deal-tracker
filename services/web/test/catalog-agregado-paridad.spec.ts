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
 *    cuanto entra un filtro de `size` o de `color`.
 *
 * Desde la 0038 (#371) `inStock=true` **también va por el precomputado**, leyendo el ámbito
 * `'con_stock'` en vez del `'todas'`. Eso mete un segundo agregado en la misma tabla, y con él dos
 * formas nuevas de romper esto que antes no existían: que el ámbito se calcule mal (y entonces el
 * precomputado y el vivo dejan de coincidir **solo** con el filtro puesto) y que alguna lectura se
 * olvide del predicado de `scope` (y entonces cada producto sale dos veces). Los casos de abajo
 * cubren las dos.
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
    // Los de la 0038 (#371): `inStock` ya no es un filtro de variante, así que el precomputado
    // tiene que saber contestarlo desde el ámbito 'con_stock'.
    ['solo en stock', (q) => (q.inStock = true)],
    ['solo en stock + solo ofertas', (q) => ((q.inStock = true), (q.onlyDeals = true))],
    ['solo en stock, orden precio-asc', (q) => ((q.inStock = true), (q.sort = 'precio-asc'))],
    // Justo lo que pide la portada para «las ofertas de hoy» (`HomePage.tsx`), que era quien
    // pagaba los ~2,1 s en cada carga sin que nadie pulsara el interruptor.
    [
      'la consulta de la portada',
      (q) => ((q.inStock = true), (q.onlyDeals = true), (q.sort = 'ofertas')),
    ],
    // `inStock=false` NO tiene ámbito y se queda en el camino vivo a propósito: aquí lo que se
    // comprueba es que sigue contestando lo mismo que antes.
    ['solo agotados', (q) => (q.inStock = false)],
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
    // Nueve desde la 0039 (#354): las ocho de la 0038 más `retailer_min_30d_repr`.
    expect(enLaFuncion).toHaveLength(9);

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
    expect(enElServicio).toHaveLength(9);
  });

  /**
   * **Todo filtro de VARIANTE tiene que mandar al camino vivo**, y esto lo vigila eje por eje.
   *
   * El precomputado lee `product_agg`, cuyas columnas `*_repr` están calculadas sobre TODAS las
   * variantes vivas del producto. Si un filtro de variante no entra en `filtroDeVariante`, el
   * producto sale correctamente filtrado —el WHERE sí se aplica— pero el precio, el tachado y el
   * **veredicto de honestidad** de la tarjeta son los de una variante que el filtro acaba de
   * excluir: «Oferta real» apoyada en una talla que el usuario ha dicho que no quiere ver.
   *
   * No lo caza la paridad de arriba, y conviene entender por qué: `ambos()` compara el camino por
   * defecto contra el vivo forzado, así que en cuanto el eje está bien puesto **los dos lados son
   * el vivo** y la comparación se vuelve tautológica. Lo que hay que afirmar es cuál de los dos se
   * elige, y eso solo se ve en el SQL emitido. Es un test de forma, como el del desempate.
   *
   * `sizeExact` (#367) es el que lo estrena, pero la lista está para que el siguiente eje de
   * variante que se añada tenga dónde caerse.
   *
   * **Esto protege dos cosas, y la segunda no se lee sola:** que el listado no mienta en el precio,
   * y que el **veredicto de honestidad describa la variante que el usuario ha filtrado** y no otra
   * del mismo producto. Lo segundo es lo que hace que un eje de variante mal puesto sea de la misma
   * familia que #436 —elogiar sin que lo elogiado sea lo que se está mirando— con otra causa.
   */
  it('cada filtro de variante manda al camino vivo, y no al precomputado', async () => {
    const ejes: Array<[string, (q: ProductQueryDto) => void]> = [
      ['size', (q) => (q.size = ['4 años'])],
      ['sizeExact', (q) => (q.sizeExact = ['104'])],
      ['color', (q) => (q.color = ['azul'])],
      ['inStock=false', (q) => (q.inStock = false)],
    ];

    for (const [nombre, ajusta] of ejes) {
      const capturadas: string[] = [];
      const espia = {
        execute: (c: unknown) => {
          capturadas.push(JSON.stringify(c));
          return Promise.resolve([]);
        },
      };
      const q = new ProductQueryDto();
      q.barefoot = 'si';
      q.sort = 'ofertas';
      ajusta(q);
      await new CatalogService(espia as never).listProducts(q);
      // El camino vivo agrega variantes en tiempo de consulta; el precomputado lee la tabla.
      expect(capturadas[0], `${nombre} se fue por el precomputado`).not.toContain('product_agg');
    }

    // Y el control: sin ningún filtro de variante SÍ se usa el precomputado, que es lo que hace
    // barata la petición ancha. Sin esto, el test de arriba pasaría con un `filtroDeVariante` que
    // fuera siempre cierto, o sea deshaciendo la 0035 sin que nada chistara.
    const capturadas: string[] = [];
    const espia = {
      execute: (c: unknown) => {
        capturadas.push(JSON.stringify(c));
        return Promise.resolve([]);
      },
    };
    const q = new ProductQueryDto();
    q.barefoot = 'si';
    q.sort = 'ofertas';
    await new CatalogService(espia as never).listProducts(q);
    expect(capturadas[0]).toContain('product_agg');
  });

  it('la CTE `stats` del camino vivo va vallada, o el catálogo se cuelga (#515)', async () => {
    // Una aserción sobre una palabra clave parece poca cosa, y es justo lo que hace falta: el modo
    // de fallo es que alguien la quite por parecer decorativa, y entonces NO se rompe nada visible
    // — el catálogo sigue devolviendo lo mismo, solo que tardando dos órdenes de magnitud más.
    //
    // Sin `MATERIALIZED`, esta CTE se referencia una sola vez, Postgres la inlinea, y en cuanto
    // estima pocas filas arriba la mete como lado interno de un Nested Loop: se ejecuta UNA VEZ POR
    // FILA, recorriendo entera `price_history` cada vez. Medido contra QA el 19/08/2026 con la
    // consulta real del servicio: `loops=90` y **105.978 ms** en niña+zapatería+zapatillas+rosa+
    // cacles; **239.856 ms** en el caso vacío (verde+deditos), que por HTTP moría en 524 a los
    // 125 s y seguía corriendo en la base después. Con la valla: 1.853 ms y 1.827 ms.
    const capturadas: string[] = [];
    const espia = {
      execute: (c: unknown) => {
        capturadas.push(JSON.stringify(c));
        return Promise.resolve([]);
      },
    };
    const q = new ProductQueryDto();
    q.barefoot = 'si';
    q.sort = 'ofertas';
    q.color = ['rosa'];
    await new CatalogService(espia as never).listProducts(q);
    expect(capturadas[0]).toContain('stats AS MATERIALIZED');

    // Y el control, que es lo que impide que esto se cumpla por accidente: la petición ancha no
    // pasa por el camino vivo, así que ahí no puede aparecer la valla. Si apareciera, sería que
    // `filtroDeVariante` se ha vuelto siempre cierto y el precomputado de la 0035 ya no se usa.
    const anchas: string[] = [];
    const espiaAncho = {
      execute: (c: unknown) => {
        anchas.push(JSON.stringify(c));
        return Promise.resolve([]);
      },
    };
    const ancha = new ProductQueryDto();
    ancha.barefoot = 'si';
    ancha.sort = 'ofertas';
    await new CatalogService(espiaAncho as never).listProducts(ancha);
    expect(anchas[0]).not.toContain('stats AS MATERIALIZED');
  });

  it('el mínimo declarado llega igual por los dos caminos, y acusa (#354)', async () => {
    const { precomputado, vivo } = await ambos((q) => (q.q = 'declarado'));

    // Lo primero es la paridad, que es lo que este fichero vigila: si el precomputado se llevara el
    // mínimo de otra variante, el veredicto de la tarjeta se separaría del camino vivo.
    expect(precomputado.items).toEqual(vivo.items);
    expect(precomputado.items).toHaveLength(1);

    // Y el veredicto es la acusación, no un «sin confirmar»: la representativa vale 25,00 con un
    // mínimo declarado de 20,00, o sea que la tienda anuncia rebaja sobre 40,00 habiendo vendido
    // más barato dentro de los 30 días. Sin el dato serían 4 días de histórico y `unverified`.
    expect(precomputado.items[0].honesty).toBe('suspicious');
  });

  it('un producto sin variantes vivas con precio no sale por ninguno de los dos', async () => {
    const { precomputado, vivo } = await ambos((q) => (q.q = 'fantasma'));
    expect(precomputado.items).toHaveLength(0);
    expect(vivo.items).toHaveLength(0);
  });

  // ── Los tres de la 0038 (#371) ──

  it('filtrar por stock cambia la variante representativa, y en los dos caminos igual', async () => {
    // Z-AGOTADA tiene la barata (10,00, verde) agotada y la cara (40,00, negro) comprable. Si el
    // ámbito 'con_stock' fuera el mismo agregado con menos filas, esto pasaría igual; solo pasa si
    // de verdad se ha vuelto a elegir la representativa.
    const sinFiltro = await ambos((q) => (q.q = 'agotada'));
    expect(sinFiltro.precomputado.items).toEqual(sinFiltro.vivo.items);
    // Este `expect` decía 10,00 y pasaba A PROPÓSITO, documentando el defecto de #402: el "desde"
    // era un MIN() ciego al stock, así que la tarjeta anunciaba la verde agotada mientras el
    // tachado, el % y el veredicto hablaban de la negra. Ahora los cuatro salen de la misma prenda.
    expect(sinFiltro.precomputado.items[0].priceFrom).toBe('40.00');
    expect(sinFiltro.precomputado.items[0].colorRepr).toBe('negro');

    const conFiltro = await ambos((q) => ((q.q = 'agotada'), (q.inStock = true)));
    expect(conFiltro.precomputado.items).toEqual(conFiltro.vivo.items);
    // Y con el filtro puesto sale lo mismo, que es lo que tiene que pasar desde #402: el ámbito
    // 'con_stock' ya no es la única forma de no enseñar un precio que no se puede pagar.
    expect(conFiltro.precomputado.items[0].priceFrom).toBe('40.00');
    expect(conFiltro.precomputado.items[0].colorRepr).toBe('negro');
  });

  /**
   * El respaldo de #402, que es la mitad que ninguna de las dos issues escribe.
   *
   * `price_repr` es "la más barata CON stock, y la más barata a secas si no hay ninguna". Esa
   * segunda mitad la ejercita Z-SINSTOCK (18,00, todo agotado): si el "desde" se hubiera hecho con
   * un `MIN(price) FILTER (WHERE in_stock)` —que es la forma en la que uno piensa este arreglo—,
   * aquí saldría `null` y la portada pintaría un hueco en 344 productos de QA.
   */
  it('un producto con TODO agotado conserva su "desde" en vez de quedarse sin él', async () => {
    const { precomputado, vivo } = await ambos((q) => (q.q = 'sin stock'));
    expect(precomputado.items).toEqual(vivo.items);
    expect(precomputado.items).toHaveLength(1);
    expect(precomputado.items[0].priceFrom).toBe('18.00');
    // Y va acompañado de lo que la tarjeta usa para pintar el badge, que es lo que hace honesto el
    // respaldo: se enseña un precio que no se puede pagar, pero diciendo que no se puede.
    expect(precomputado.items[0].anyInStock).toBe(false);
  });

  /**
   * Las dos consecuencias de #402 fuera de la tarjeta. Van juntas porque son el mismo argumento:
   * ordenar o filtrar por una columna mientras se enseña otra es peor que el defecto de partida.
   *
   * Z-AGOTADA es el único del seed donde las dos columnas difieren (10,00 contra 40,00), así que es
   * el único que puede acusar la diferencia.
   */
  it('el orden por precio y el rango filtran por el "desde" que se enseña', async () => {
    const { items } = await servicio.listProducts(
      Object.assign(new ProductQueryDto(), {
        sort: 'precio-asc' as const,
        barefoot: 'all' as const,
        limit: 100,
      }),
    );
    const agotada = items.find((i) => i.name.includes('agotada la barata'))!;
    expect(agotada.priceFrom).toBe('40.00');
    // Ordenado por lo que se ve: nadie con un "desde" mayor puede ir delante.
    const posiciones = items.map((i) => Number(i.priceFrom));
    expect(posiciones).toEqual([...posiciones].sort((a, b) => a - b));

    // Y el rango: con el MIN ciego, "hasta 20 €" devolvía una prenda cuya tarjeta dice 40.
    const { items: baratos } = await servicio.listProducts(
      Object.assign(new ProductQueryDto(), { maxPrice: 20, barefoot: 'all' as const, limit: 100 }),
    );
    expect(baratos.map((i) => i.name)).not.toContain(agotada.name);
    expect(baratos.every((i) => Number(i.priceFrom) <= 20)).toBe(true);
  });

  it('un producto con todo agotado no sale con inStock, y sí sin él', async () => {
    const sinFiltro = await ambos((q) => (q.q = 'sin stock'));
    expect(sinFiltro.precomputado.items).toHaveLength(1);
    expect(sinFiltro.precomputado.items).toEqual(sinFiltro.vivo.items);

    const conFiltro = await ambos((q) => ((q.q = 'sin stock'), (q.inStock = true)));
    expect(conFiltro.precomputado.items).toHaveLength(0);
    expect(conFiltro.vivo.items).toHaveLength(0);
  });

  it('cada producto tiene una sola fila por ámbito, y el catálogo no lo duplica', async () => {
    // La forma de la 0038 tiene un riesgo conocido: una lectura sin el predicado de `scope` saca
    // las dos filas del mismo producto. Se comprueba por los dos lados — la tabla y el resultado.
    const [{ n }] = await sql<{ n: number }[]>`
      SELECT count(*)::int AS n FROM (
        SELECT product_id, scope FROM product_agg GROUP BY product_id, scope HAVING count(*) > 1
      ) d`;
    expect(n).toBe(0);

    const ambosAmbitos = await sql<{ scope: string; n: number }[]>`
      SELECT scope, count(*)::int AS n FROM product_agg GROUP BY scope ORDER BY scope`;
    expect(ambosAmbitos.map((r) => r.scope)).toEqual(['con_stock', 'todas']);
    // 'con_stock' tiene que tener MENOS filas: Z-SINSTOCK no está.
    const todas = ambosAmbitos.find((r) => r.scope === 'todas')!.n;
    const conStock = ambosAmbitos.find((r) => r.scope === 'con_stock')!.n;
    expect(conStock).toBeLessThan(todas);

    const { precomputado } = await ambos((q) => (q.inStock = true));
    const ids = precomputado.items.map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
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
    min30d: string | null = null,
  ) => {
    await sql`
      INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock,
                                 retailer_min_30d, scraped_at)
      VALUES (${variantId}, ${price}, ${listPrice},
              ${listPrice ? sql`round((1 - ${price}::numeric / ${listPrice}::numeric) * 100, 2)` : null},
              ${inStock}, ${min30d}, now() - make_interval(days => ${diasAtras}))`;
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

  // 7. AGOTADA LA BARATA: el caso que separa los dos ámbitos de la 0038 (#371). La variante más
  //    barata está agotada, así que el agregado 'todas' y el 'con_stock' tienen que diferir en
  //    `price_from` Y en la variante representativa (`color_repr`). Sin este producto el seed
  //    pasaría igual con un segundo ámbito mal construido, porque el de arriba (Z-NORMAL) tiene
  //    agotada la CARA, que no cambia quién representa.
  const agotada = await producto(zara.id, 'Z-AGOTADA', 'Botas agotada la barata');
  const a1 = await variante(agotada, 'Z-AGOTADA-24-verde', '24', 'verde');
  const a2 = await variante(agotada, 'Z-AGOTADA-25-negro', '25', 'negro');
  await precio(a1, '30.00', '60.00', 4);
  await precio(a1, '10.00', '60.00', 0, false); // la más barata, y sin stock
  await precio(a2, '50.00', '60.00', 4);
  await precio(a2, '40.00', '60.00', 0); // más cara, pero comprable

  // 9. MÍNIMO DECLARADO (#354): la tienda publica su mínimo de 30 días y el precio actual está POR
  //    ENCIMA de él, o sea que se desmiente sola. Es lo único del seed que ejercita
  //    `retailer_min_30d_repr`, y hace falta que lo ejercite en las DOS variantes con valores
  //    distintos: si el precomputado se llevara el de la variante equivocada, el veredicto de la
  //    tarjeta cambiaría y el `EXCEPT` de los dos caminos es lo único que lo vería.
  const declarado = await producto(zara.id, 'Z-DECLARADO', 'Botas con minimo declarado');
  const d1 = await variante(declarado, 'Z-DECLARADO-24-gris', '24', 'gris');
  const d2 = await variante(declarado, 'Z-DECLARADO-25-lila', '25', 'lila');
  await precio(d1, '30.00', '40.00', 4, true, '20.00');
  await precio(d1, '25.00', '40.00', 0, true, '20.00'); // la representativa: 25 > 20 -> se desmiente
  await precio(d2, '38.00', '40.00', 4, true, '35.00');
  await precio(d2, '36.00', '40.00', 0, true, '35.00');

  // 8. TODO AGOTADO: ninguna variante con stock. No tiene fila en el ámbito 'con_stock', así que
  //    con `inStock=true` no debe salir por ninguno de los dos caminos.
  const sinStock = await producto(zara.id, 'Z-SINSTOCK', 'Botas sin stock');
  const ss1 = await variante(sinStock, 'Z-SINSTOCK-24-coral', '24', 'coral');
  await precio(ss1, '20.00', '40.00', 3);
  await precio(ss1, '18.00', '40.00', 0, false);
}
