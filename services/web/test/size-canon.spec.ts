import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { makeSql, TEST_DB } from './helpers';

/**
 * `size_canon` / `size_sort` (migraciones 0014 y 0017; issues #43 y #64).
 *
 * Los casos NO son inventados: son los valores distintos que había en `dev` —los 121 del 30/07/2026
 * con Zara y Sfera, más los que estrenó Cacles el 01/08/2026— reducidos a los que documentan una
 * regla. Si una tienda futura trae una forma nueva, se añade aquí antes de tocar la función.
 */
describe.skipIf(!TEST_DB)('talla canónica', () => {
  let sql: postgres.Sql;

  const canon = async (value: string): Promise<string> => {
    const [row] = await sql<{ v: string }[]>`SELECT size_canon(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSql();
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  // Cada grupo son formas que DEBEN acabar en la misma etiqueta: es la equivalencia que hace que un
  // interés guardado con la talla del filtro case con la variante de cualquier tienda.
  const equivalencias: Array<{ canonica: string; formas: string[] }> = [
    // Calzado. Zara sirve el rango 19-26 de tres maneras a la vez; Sfera lo da limpio.
    { canonica: '26', formas: ['26', '26 (16,3 cm)', '26 (16.3 cm)'] },
    { canonica: '19', formas: ['19', '19 (11.6 cm)'] },
    { canonica: '30', formas: ['30', '30 (18,9 cm)'] },
    { canonica: '41', formas: ['41 (26,3 cm)'] },
    // Ropa: la altura de referencia cambia entre prendas reales, y la palabra "años" va y viene.
    {
      canonica: '11-12 años',
      formas: ['11-12', '11-12 años', '11-12 años (148 cm)', '11-12 años (152 cm)'],
    },
    { canonica: '12-13 años', formas: ['12-13 Años', '12-13 años (156 cm)', '12-13 años (158 cm)'] },
    { canonica: '8-9 años', formas: ['8-9 años (130 cm)', '8-9 años (131 cm)', '8-9 años (134 cm)'] },
    // La barra es separador de rango en Zara.
    { canonica: '5-6 años', formas: ['5-6', '5-6 años', '5-6 años (115 cm)', '5/6 años (116 cm)'] },
    { canonica: '1-2 años', formas: ['1-2 años (20-22)', '1-2 años (92 cm)', '1/2 años (89 cm)'] },
    // Talla por letra: manda el rango de edad que lleva dentro, no la letra.
    { canonica: '6-9 años', formas: ['S (6-9 años) (100 cm)'] },
    { canonica: '12-14 años', formas: ['12-14 años (164 cm)', 'L (12-14 años) (140 cm)'] },
    // Meses, que no pueden confundirse con años.
    { canonica: '12-18 meses', formas: ['12-18 meses (86 cm)'] },
    { canonica: '1-3 meses', formas: ['1-3 meses (62 cm)'] },
    // Número suelto: por debajo de 15 es edad (ropa de Sfera), por encima es pie.
    { canonica: '4 años', formas: ['4', '4 años (104 cm)'] },
    { canonica: '11 años', formas: ['11'] },
    // Rango de NÚMERO DE PIE (#64). Cacles es la primera tienda que lo trae, y desmiente la premisa
    // de la 0014 («un rango sin unidad solo puede ser edad»). Sale sin unidad, igual que el número
    // suelto del calzado, y el separador se normaliza como en los rangos de edad.
    { canonica: '25-34', formas: ['25-34'] }, // plantillas vendidas por rango
    { canonica: '48-51', formas: ['48-51'] }, // el chip «48-51 años» que motivó la issue
    { canonica: '20-21', formas: ['20 /21', '20-21'] }, // calzado de primeros pasos, talla doble
    { canonica: '24-25', formas: ['24 / 25'] },
    // Y ojo: esto es ROPA (calcetines barefoot de Plus12, categoría ropa-interior) tallada por
    // número de pie. Por eso la sección NO sirve para decidirlo: 123 de las 201 variantes afectadas
    // estaban en `ropa`.
    { canonica: '36-38', formas: ['36-38'] },
  ];

  for (const { canonica, formas } of equivalencias) {
    it(`funde ${formas.map((f) => `«${f}»`).join(' = ')} en «${canonica}»`, async () => {
      for (const forma of formas) {
        expect(await canon(forma)).toBe(canonica);
      }
    });
  }

  it('conserva la fracción: 1½ años (86 cm) no es 2 años (92 cm)', async () => {
    expect(await canon('1½ años (86 cm)')).toBe('1.5 años');
    expect(await canon('1½-2 años (92 cm)')).toBe('1.5-2 años');
    expect(await canon('2 años (92 cm)')).toBe('2 años');
  });

  /**
   * El límite declarado de esta normalización, fijado a propósito (ver 0014 y la issue #43): las tres
   * son 92 cm —la misma talla física— con tres etiquetas de edad distintas, y siguen separadas.
   * Fundirlas exige casar por intervalos, que es otro cambio. Si algún día se hace, este test es el
   * que hay que reescribir, y así el cambio de criterio queda a la vista.
   */
  it('NO funde rangos de edad que se solapan, aunque compartan el cm', async () => {
    const tres = await Promise.all(
      ['2 años (92 cm)', '1-2 años (92 cm)', '1½-2 años (92 cm)'].map(canon),
    );
    expect(new Set(tres).size).toBe(3);
    expect(tres).toEqual(['2 años', '1-2 años', '1.5-2 años']);
  });

  /**
   * El otro límite declarado, y el que la 0017 fija a propósito (issue #64): un rango de dos números
   * sin unidad es de PIE solo si LOS DOS extremos llegan al umbral 15, y si no se queda como edad.
   *
   * El umbral no es una intuición, es el hueco medido entre los dos dominios: en `dev` los rangos de
   * edad acaban en '13-14' (Sfera) y los de pie empiezan en '20 /21' (Cacles) — seis puntos de
   * holgura. El rango mixto ('14-16') es el único ambiguo de verdad, no existe hoy en ninguna
   * tienda, y se decide como edad, que era el comportamiento anterior.
   *
   * La sección NO interviene: los calcetines de Cacles son `ropa` y van por número de pie. Si algún
   * día se quisiera mover el umbral o meter la sección en la decisión, es este test el que hay que
   * reescribir, y así el cambio de criterio queda a la vista.
   */
  it('decide rango de pie vs. rango de edad por el umbral 15, en los DOS extremos', async () => {
    expect(await canon('13-14')).toBe('13-14 años'); // el mayor rango de edad real
    expect(await canon('14-15')).toBe('14-15 años'); // un extremo por debajo: sigue siendo edad
    expect(await canon('14-16')).toBe('14-16 años'); // mixto: ante la duda, edad
    expect(await canon('15-16')).toBe('15-16'); // los dos llegan: pie
    expect(await canon('20-21')).toBe('20-21'); // el menor rango de pie real
  });

  it('es idempotente sobre su propia salida', async () => {
    for (const { canonica } of equivalencias) {
      expect(await canon(canonica)).toBe(canonica);
    }
    expect(await canon('1.5 años')).toBe('1.5 años');
    expect(await canon('12-18 meses')).toBe('12-18 meses');
  });

  it('devuelve el texto original cuando no reconoce nada', async () => {
    // Preferimos un chip raro en la faceta a una variante que desaparece del filtro.
    expect(await canon('  Talla única  ')).toBe('Talla única');
    expect(await canon('XL')).toBe('XL');
  });

  it('ordena por talla y no alfabéticamente', async () => {
    const ropa = [
      '11-12 años',
      '1-3 meses',
      '2 años',
      '8-10 años',
      '8-9 años',
      '1.5 años',
      '18-24 meses',
    ];
    const [{ v: ordenada }] = await sql<{ v: string[] }[]>`
      SELECT array_agg(t ORDER BY size_sort(t), t) AS v
      FROM unnest(${sql.array(ropa)}::text[]) AS t`;
    expect(ordenada).toEqual([
      '1-3 meses',
      '1.5 años',
      '18-24 meses', // 1,5-2 años: cae donde le toca, no al final por empezar por "1"
      '2 años',
      '8-9 años',
      '8-10 años', // el desempate mira el segundo extremo del rango, no el texto
      '11-12 años',
    ]);

    const calzado = ['26', '19', '41', '30', '9'];
    const [{ v: pies }] = await sql<{ v: string[] }[]>`
      SELECT array_agg(t ORDER BY size_sort(t), t) AS v
      FROM unnest(${sql.array(calzado)}::text[]) AS t`;
    // El '9' no es un pie real (el calzado infantil empieza en 19), pero fija que ordena por número
    // y no por texto, que es donde '9' se colaba detrás de '41'.
    expect(pies).toEqual(['9', '19', '26', '30', '41']);

    // Los rangos de pie de la 0017 se intercalan con los números sueltos por su extremo inferior,
    // sin necesidad de tocar `size_sort`: la faceta de zapatería los mezcla en la misma lista.
    const conRangos = ['26', '19', '48-51', '25-34', '41', '20-21'];
    const [{ v: mezclados }] = await sql<{ v: string[] }[]>`
      SELECT array_agg(t ORDER BY size_sort(t), t) AS v
      FROM unnest(${sql.array(conRangos)}::text[]) AS t`;
    expect(mezclados).toEqual(['19', '20-21', '25-34', '26', '41', '48-51']);
  });
});
