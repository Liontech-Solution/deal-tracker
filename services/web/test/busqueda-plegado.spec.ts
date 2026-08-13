import { drizzle } from 'drizzle-orm/postgres-js';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { CatalogService } from '../src/catalog/catalog.service';
import { ProductQueryDto } from '../src/catalog/dto/product-query.dto';
import { schema } from '../src/database/schema';
import {
  BASES_CANON,
  saltarSiNoHayBase,
  makeSqlAt,
  refrescarAgregado,
  resetSchema,
} from './helpers';

/**
 * El plegado de la búsqueda por texto (`fold()` en `catalog.service.ts`), contra las dos bases.
 *
 * Existe por el mismo motivo que el arnés de las canónicas (#105): `fold()` hacía
 * `translate(lower(x), 'áàä…', 'aaa…')`, y con el ctype `C` de la base del cluster `lower()` no baja
 * las mayúsculas acentuadas — 'PANTALÓN' se quedaba en 'pantalÓn', la 'Ó' no estaba en la tabla y el
 * producto era **invisible** al buscar «pantalon». En `dev` el 02/08/2026 eso eran 694 productos
 * vivos con mayúscula acentuada en el nombre, casi todos de Zara.
 *
 * Los `catalog.e2e.spec.ts` ya cubren la búsqueda, pero corren solo contra `TEST_DATABASE_URL`, que
 * es la base con el locale bueno: ahí esto pasaba en verde. El caso solo se ve contra ctype `C`.
 *
 * Se ejercita el servicio de verdad (no una copia de la expresión SQL en el test), que es lo único
 * que garantiza que el arreglo siga puesto si alguien reescribe `fold()`.
 */
saltarSiNoHayBase('plegado de la búsqueda');

describe.each(BASES_CANON)('plegado de la búsqueda · $nombre', ({ url }) => {
  let sql: postgres.Sql;
  let service: CatalogService;

  /**
   * Nombres como los escribe cada tienda, con su categoría real: el pajar de la búsqueda es
   * `nombre + categoría + género`, así que sembrarlos todos en la misma categoría haría casar
   * cualquier término con todo.
   */
  const PRODUCTOS: Array<[string, string]> = [
    ['PANTALÓN VAQUERO', 'pantalones'], // zara, mayúscula acentuada — el caso de #105
    ['Pantalón chino', 'pantalones'], // la misma prenda escrita en minúsculas
    ['CAMISETA ALGODÓN', 'camisetas'], // otra mayúscula acentuada, para que no case por casualidad
    ['Vestido liso', 'vestidos'], // sin acento: el control
  ];

  const buscar = async (q: string): Promise<string[]> => {
    const query = Object.assign(new ProductQueryDto(), { q, barefoot: 'all' as const });
    const { items } = await service.listProducts(query);
    return items.map((i) => i.name).sort();
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await resetSchema(sql);
    service = new CatalogService(drizzle(sql, { schema }) as never);

    const [r] = await sql<{ id: number }[]>`
      INSERT INTO retailer (slug, name, base_url)
      VALUES ('zara', 'Zara', 'https://www.zara.com')
      RETURNING id`;
    for (const [i, [name, categoria]] of PRODUCTOS.entries()) {
      const [p] = await sql<{ id: number }[]>`
        INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category, url)
        VALUES (${r.id}, ${`ZARA-${i}`}, ${name}, 'niña', 'ropa', ${categoria}, ${`https://x/${i}`})
        RETURNING id`;
      const [v] = await sql<{ id: number }[]>`
        INSERT INTO variant (product_id, retailer_variant_id, size, color)
        VALUES (${p.id}, ${`ZARA-${i}-6`}, '6 años', 'azul')
        RETURNING id`;
      // El catálogo solo devuelve variantes con precio: la CTE `latest` hace JOIN con price_history.
      await sql`
        INSERT INTO price_history (variant_id, price, in_stock, scraped_at)
        VALUES (${v.id}, 19.99, true, now())`;
    }
    await refrescarAgregado(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  it('encuentra un nombre en MAYÚSCULAS con acento tecleando sin acento', async () => {
    expect(await buscar('pantalon')).toEqual(['PANTALÓN VAQUERO', 'Pantalón chino']);
    expect(await buscar('algodon')).toEqual(['CAMISETA ALGODÓN']);
  });

  it('lo encuentra igual tecleando el acento, y en cualquier caja', async () => {
    expect(await buscar('pantalón')).toEqual(['PANTALÓN VAQUERO', 'Pantalón chino']);
    expect(await buscar('PANTALÓN')).toEqual(['PANTALÓN VAQUERO', 'Pantalón chino']);
    expect(await buscar('VAQUERO')).toEqual(['PANTALÓN VAQUERO']);
  });

  it('sigue exigiendo todas las palabras y sin colar lo que no es', async () => {
    expect(await buscar('pantalon vaquero')).toEqual(['PANTALÓN VAQUERO']);
    expect(await buscar('pantalon algodon')).toEqual([]);
    expect(await buscar('sudadera')).toEqual([]);
  });
});
