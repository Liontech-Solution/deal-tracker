import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { BASES_CANON, saltarSiNoHayBase, makeSqlAt } from './helpers';

/**
 * `size_cm` (migración 0032; issue #331).
 *
 * Extrae la medida en centímetros que algunas tiendas cuelgan de la talla. **No es una canónica**:
 * no pliega ni ordena nada, y `size_canon` no la usa. Existe para que quien SÍ tiene contexto
 * —la ficha, que mira todas las variantes de un producto— pueda decidir si dos formas crudas son
 * dos tallas físicas o la misma escrita de dos maneras.
 *
 * Los valores no son inventados: son los que publican H&M, Hipercor, Zara y Cacles, medidos contra
 * `deal_tracker_qa` el 13/08/2026.
 *
 * Se ejecuta contra **todas las bases configuradas**, como el resto de funciones del esquema: la
 * lección de #105 es que una función que parece independiente del locale puede no serlo, y
 * comprobarlo cuesta un `describe.each`.
 */
saltarSiNoHayBase('medida en cm de la talla');

describe.each(BASES_CANON)('medida en cm de la talla · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  const cm = async (value: string): Promise<number | null> => {
    const [row] = await sql<{ v: number | null }[]>`SELECT size_cm(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  describe('la saca de las dos formas que publican las tiendas', () => {
    // H&M y Zara: entre paréntesis.
    it.each([
      ['0-1 meses (44 cm)', 44],
      ['0-1 meses (50 cm)', 50],
      ['2 años (92 cm)', 92],
      ['11-12 años (152 cm)', 152],
    ])('paréntesis: %s -> %s', async (entrada, esperado) => {
      expect(await cm(entrada as string)).toBe(esperado);
    });

    // Hipercor: sufijo ' - Medida N cm'. Es la forma que la consulta original de #331 no vio, y
    // por eso la issue daba por hecho que el defecto era de una sola tienda.
    it.each([
      ['3 meses - Medida 62 cm', 62],
      ['3 meses/6 meses - Medida 68 cm', 68],
      ['9 meses - Medida 74 cm', 74],
      ['9-10 años - Medida 128 cm', 128],
    ])('sufijo Medida: %s -> %s', async (entrada, esperado) => {
      expect(await cm(entrada as string)).toBe(esperado);
    });
  });

  describe('devuelve NULL cuando la tienda no publica medida', () => {
    // Este NULL no es un hueco: es **la mitad de la regla** de #331. Que una de las dos formas no
    // traiga medida es exactamente lo que dice que no discrimina, y es lo que impide partir
    // '9-10 años' de '9-10 años - Medida 128 cm' en dos chips.
    it.each(['9-10 años', '12 meses', '12 Meses', 'XS', 'Talla única', '36-38', '4A'])(
      '%s',
      async (entrada) => {
        expect(await cm(entrada)).toBeNull();
      },
    );
  });

  it('se queda con la parte entera de una medida decimal', async () => {
    // '26 (16,3 cm)' es un número de pie de Cacles. Aquí basta con distinguir dos medidas entre
    // sí dentro de un producto, así que la parte entera sobra para el trabajo.
    expect(await cm('26 (16,3 cm)')).toBe(16);
    expect(await cm('19 (11.6 cm)')).toBe(11);
  });

  it('no se confunde con un número que no sea la medida', async () => {
    // La talla de Hipercor lleva DOS números y solo el segundo es cm.
    expect(await cm('9 meses/12 meses - Medida 80 cm')).toBe(80);
    expect(await cm('12 meses/18 meses - Medida 86 cm')).toBe(86);
  });

  it('es STRICT: sin talla no hay medida', async () => {
    const [row] = await sql<{ v: number | null }[]>`SELECT size_cm(NULL) AS v`;
    expect(row.v).toBeNull();
  });

  it('NO cambia size_canon, que es lo que #331 preguntaba y la respuesta fue que no', async () => {
    // Si algún día alguien mete la medida en la canónica, esto se cae — y tiene que caerse, porque
    // `interest.size` guarda la canónica y `matching` casa por ella.
    const [row] = await sql<{ a: string; b: string }[]>`
      SELECT size_canon('0-1 meses (44 cm)') AS a, size_canon('0-1 meses (50 cm)') AS b`;
    expect(row.a).toBe('0-1 meses');
    expect(row.b).toBe('0-1 meses');
  });
});
