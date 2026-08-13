import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { BASES_CANON, saltarSiNoHayBase, makeSqlAt } from './helpers';

/**
 * `size_band` (migración 0033; issue #325).
 *
 * Las entradas NO son inventadas: son las etiquetas que `size_canon` produce hoy en
 * `deal_tracker_qa`, medidas el 13/08/2026. El panel ofrecía **181** y estas 21 bandas son lo que
 * quedan.
 *
 * Contra **las dos bases**, como el resto de funciones del esquema: la banda se apila sobre
 * `size_canon`, y `size_canon` sí depende del ctype (#105). Si la de abajo se rompe bajo ctype `C`,
 * esta se rompe con ella y hay que verlo aquí y no en el cluster.
 */
saltarSiNoHayBase('banda de edad');

describe.each(BASES_CANON)('banda de edad · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  const band = async (value: string): Promise<string> => {
    const [row] = await sql<{ v: string }[]>`SELECT size_band(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  describe('los tres vocabularios de edad caen en la misma banda', () => {
    // Es todo el objetivo de la issue: que 'meses', 'años' y la altura en cm dejen de ser tres
    // listas distintas para el usuario.
    it.each([
      ['0 meses', '0-3 meses'],
      ['0-3 meses', '0-3 meses'],
      ['1-3 meses', '0-3 meses'],
      ['3-6 meses', '3-6 meses'],
      ['6-9 meses', '6-12 meses'],
      ['9-12 meses', '6-12 meses'],
      ['12-18 meses', '12-18 meses'],
      ['18-24 meses', '18-24 meses'],
      ['1.5 años', '18-24 meses'],
      ['2-3 años', '2 años'],
      ['4-5 años', '4 años'],
      ['11-12 años', '11 años'],
      ['13-14 años', '13 años'],
      ['14-16 años', '14+ años'],
    ])('%s -> %s', async (entrada, esperado) => {
      expect(await band(entrada as string)).toBe(esperado);
    });
  });

  describe('la altura en cm entra por la fórmula (cm-80)/6, exacta de 92 arriba', () => {
    // Los 20 valores que el catálogo publica de verdad, comprobados uno a uno al escribir la
    // migración. Aquí van los extremos y los que marcan un cambio de banda.
    it.each([
      ['92', '2 años'],
      ['98', '3 años'],
      ['104', '4 años'],
      ['116', '6 años'],
      ['128', '8 años'],
      ['140', '10 años'],
      ['152', '12 años'],
      ['158', '13 años'],
      ['164', '14+ años'],
      ['182', '14+ años'],
    ])('%s cm -> %s', async (entrada, esperado) => {
      expect(await band(entrada as string)).toBe(esperado);
    });

    it('por debajo de 92 manda la tabla, no la fórmula (que ahí da cero y negativo)', async () => {
      // Son los tres únicos valores que el catálogo publica ahí, con dos productos cada uno.
      expect(await band('80')).toBe('12-18 meses');
      expect(await band('85')).toBe('18-24 meses');
      expect(await band('90')).toBe('18-24 meses');
    });
  });

  describe('lo que no es edad va a su banda y NO se aproxima', () => {
    // Aproximar una talla infantil es lo que hace que la prenda no le valga a nadie, y el usuario
    // no tendría forma de saber que le hemos aproximado. La talla exacta sigue en la ficha.
    it.each([
      ['22-24', 'Por número'],
      ['36-38', 'Por número'],
      ['35-36', 'Por número'],
      ['20-21', 'Por número'],
      ['26', 'Por número'],
    ])('%s -> %s', async (entrada, esperado) => {
      expect(await band(entrada as string)).toBe(esperado);
    });

    it('el 42 suelto de Lefties tampoco se llama «Calcetines»', async () => {
      // 173 productos de Lefties llevan talla '42' y son bermudas, blusas y chaquetas de punto.
      // Es el motivo por el que la banda se llama «Por número» y no «Calcetines»: el nombre obvio
      // habría sido una etiqueta falsa en 173 prendas visibles.
      expect(await band('42')).toBe('Por número');
    });

    it.each([
      ['XS', 'Por letra'],
      ['S', 'Por letra'],
      ['XXL', 'Por letra'],
    ])('%s -> %s', async (entrada, esperado) => {
      expect(await band(entrada as string)).toBe(esperado);
    });

    it.each(['Talla única', 'ONESIZE', '75 B'])('%s -> Otras', async (entrada) => {
      expect(await band(entrada)).toBe('Otras');
    });
  });

  it('un rango entra por su extremo BAJO', async () => {
    // '4-5 años' va a la banda de 4 y no a la de 5: quien busca para un niño de 4 tiene que
    // encontrar la prenda que le vale. Y con multiselección, quien quiera margen marca las dos.
    expect(await band('4-5 años')).toBe('4 años');
    expect(await band('10-12 años')).toBe('10 años');
  });

  it('son 21 bandas y ni una más', async () => {
    // El número es el criterio de hecho de la issue: el panel tiene que caber en un móvil de
    // 390 px sin acotar por tienda. Si alguien añade una banda, que sea a sabiendas.
    const [row] = await sql<{ n: number }[]>`
      SELECT count(DISTINCT size_band(t)) AS n
        FROM unnest(ARRAY['0 meses','0-3 meses','1-3 meses','3-6 meses','6-9 meses','9-12 meses',
                          '12-18 meses','18-24 meses','2-3 años','3-4 años','4-5 años','5-6 años',
                          '6-7 años','7-8 años','8-9 años','9-10 años','10-12 años','11-12 años',
                          '12-13 años','13-14 años','14-16 años','36-38','XS','Talla única']) t`;
    expect(Number(row.n)).toBe(21);
  });

  it('el orden lo pone size_sort, sin funcion nueva: meses, años, y lo raro al final', async () => {
    // `size_sort` (0014) divide los meses por 12 y manda al 9999 lo que no lleva número. Que las
    // tres bandas sin edad caigan al final es reutilización, no casualidad.
    const filas = await sql<{ v: string }[]>`
      SELECT v FROM (SELECT DISTINCT size_band(t) AS v
        FROM unnest(ARRAY['36-38','XS','Talla única','2-3 años','0-3 meses','14-16 años']) t) x
      ORDER BY size_sort(v), v`;
    expect(filas.map((f) => f.v)).toEqual([
      '0-3 meses',
      '2 años',
      '14+ años',
      'Otras',
      'Por letra',
      'Por número',
    ]);
  });

  it('NO cambia size_canon: el interés sigue guardando la talla exacta', async () => {
    // La asimetría deliberada de la 0029, un piso más arriba: el filtro pliega, el aviso no.
    const [row] = await sql<{ a: string; b: string }[]>`
      SELECT size_canon('4-5 años') AS a, size_canon('11-12 años (152 cm)') AS b`;
    expect(row.a).toBe('4-5 años');
    expect(row.b).toBe('11-12 años');
  });
});
