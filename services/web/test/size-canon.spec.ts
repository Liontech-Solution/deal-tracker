import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { BASES_CANON, saltarSiNoHayBase, makeSqlAt } from './helpers';

/**
 * `size_canon` / `size_sort` (migraciones 0014, 0017, 0021 y 0024; issues #43, #64, #105 y #135).
 *
 * Los casos NO son inventados: son los valores distintos que había en `dev` —los 121 del 30/07/2026
 * con Zara y Sfera, más los que estrenó Cacles el 01/08/2026, los que trajo Hipercor el 02/08/2026
 * y los que estrenó Springfield el 03/08/2026— reducidos a los que documentan una regla. Si una
 * tienda futura trae una forma nueva, se añade aquí antes de tocar la función.
 *
 * Se ejecuta contra **todas las bases configuradas** (ver `BASES_CANON`), y eso es parte del test:
 * la canónica dependía del ctype de la base (#105) y el veredicto salía distinto en CI que en el
 * cluster. Las mismas aserciones tienen que valer en los dos sitios.
 */
saltarSiNoHayBase('talla canónica');

describe.each(BASES_CANON)('talla canónica · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  const canon = async (value: string): Promise<string> => {
    const [row] = await sql<{ v: string }[]>`SELECT size_canon(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
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
      formas: ['11-12', '11-12 años', '11-12 años (148 cm)', '11-12 años (152 cm)', '11/12 AÑOS'],
    },
    { canonica: '12-13 años', formas: ['12-13 Años', '12-13 años (156 cm)', '12-13 años (158 cm)'] },
    { canonica: '8-9 años', formas: ['8-9 años (130 cm)', '8-9 años (131 cm)', '8-9 años (134 cm)'] },
    // La barra es separador de rango en Zara.
    { canonica: '5-6 años', formas: ['5-6', '5-6 años', '5-6 años (115 cm)', '5/6 años (116 cm)'] },
    { canonica: '1-2 años', formas: ['1-2 años (20-22)', '1-2 años (92 cm)', '1/2 años (89 cm)'] },
    // Talla por letra: manda el rango de edad que lleva dentro, no la letra.
    { canonica: '6-9 años', formas: ['S (6-9 años) (100 cm)'] },
    { canonica: '12-14 años', formas: ['12-14 años (164 cm)', 'L (12-14 años) (140 cm)'] },
    // Meses, que no pueden confundirse con años. La forma abreviada de Springfield entra aquí y no
    // en un grupo propio a propósito: lo que hay que fijar es que cae en el MISMO chip que la de
    // Zara y la de H&M, que es justo lo que #135 tenía partido (1407 variantes contra 1).
    { canonica: '12-18 meses', formas: ['12-18 meses (86 cm)', '12-18M'] },
    { canonica: '6-12 meses', formas: ['6-12M'] },
    { canonica: '9-12 meses', formas: ['9-12M'] },
    { canonica: '3-6 meses', formas: ['3-6M'] },
    { canonica: '1-3 meses', formas: ['1-3 meses (62 cm)'] },
    // El rango que colapsa en sí mismo (#89): la talla de recién nacido del pack de bodies de Zara,
    // 4 variantes vivas en `qa` el 02/08/2026 y el único caso del catálogo.
    { canonica: '0 meses', formas: ['0-0 meses (50 cm)', '0-0 meses'] },
    // Y el singular (#73): las dos formas reales que había en `qa` el 02/08/2026, una por regla.
    { canonica: '1 mes', formas: ['1 mes', '1 Mes'] },
    { canonica: '1 año', formas: ['1 año', '1 años (17-19)', '1'] },
    // Número suelto: por debajo de 15 es edad (ropa de Sfera), por encima es pie.
    // Y con el sufijo 'A' de años (#135): Springfield escribe las tres formas en el mismo catálogo,
    // así que las tres tienen que caer en la misma etiqueta o el filtro por talla se parte.
    { canonica: '4 años', formas: ['4', '4 años (104 cm)', '4A'] },
    { canonica: '6 años', formas: ['6', '6A'] },
    { canonica: '14 años', formas: ['14', '14A'] },
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
    // estaban en `ropa`. Algunos calcetines traen además el cm entre paréntesis, como el calzado de
    // Zara, y se descarta igual.
    { canonica: '36-38', formas: ['36-38'] },
    { canonica: '20-24', formas: ['20-24', '20-24 (14 a 16 cm)'] },
    { canonica: '30-34', formas: ['30-34 (20 a 22 cm)'] },
    // La escalera de ropa de Hipercor, en MAYÚSCULAS (#105). Con ctype `C` —el de la base del
    // cluster— `lower('11/12 AÑOS')` daba '11/12 aÑos', el patrón `a[nñ]o` no casaba y la talla caía
    // hasta la regla 7, que devuelve el texto crudo: 5 variantes con el chip sin canonicalizar.
    { canonica: '7-8 años', formas: ['7/8 AÑOS', '7/8 años'] },
    { canonica: '9-10 años', formas: ['9/10 AÑOS'] },
    { canonica: '13-14 años', formas: ['13/14 AÑOS'] },
    { canonica: '15-16 años', formas: ['15/16 AÑOS'] }, // lleva unidad: edad, no número de pie
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

  /**
   * La unidad abreviada a UNA LETRA (#135): Springfield escribe la edad de tres maneras en el mismo
   * catálogo ('5-6', '8' y '4A') y los meses con 'M' ('12-18M'). La propiedad, y no el caso: **el
   * sufijo se lee como su unidad solo cuando TODOS sus números están dentro del tope de esa
   * unidad**, y los topes son distintos porque no miden lo mismo — 15 para años (el umbral de la
   * 0017, que separa edad de número de pie) y 36 para meses (3 años, el mayor mes real del catálogo).
   *
   * El tope no está para distinguir dos unidades entre sí —un pie no se escribe con 'A' ni con 'M'—
   * sino porque la letra pegada a un número es un sitio concurrido: es también la COPA de un
   * sujetador ('80A') y en otras tallas una horma o un ancho. Lo que se sale del tope cae a la regla
   * 7 y sale crudo, que es el comportamiento de antes de la 0024: un chip feo, nunca una etiqueta
   * equivocada.
   *
   * Medido el 03/08/2026: antes de la primera pasada de Springfield NINGUNA de las siete tiendas
   * tenía una sola talla con letra pegada a un dígito, así que estas reglas no chocan con nada
   * existente (comprobado sobre las 384 tallas distintas de `dev`: los únicos 17 valores que cambian
   * son estos). El día que una tienda escriba la copa así, esto es lo que hay que releer.
   */
  it('lee el sufijo de unidad solo dentro del tope de esa unidad', async () => {
    // Años, tope 15. Las formas son las 10 reales de Springfield, de '3A' a '14A'.
    expect(await canon('4A')).toBe('4 años');
    expect(await canon('1A')).toBe('1 año'); // el singular de la 0019 también aquí
    expect(await canon('14A')).toBe('14 años'); // el mayor real de la tienda: sigue siendo edad
    expect(await canon('15A')).toBe('15A'); // llega al tope: crudo, como antes de la 0024
    expect(await canon('80A')).toBe('80A'); // la copa de sujetador, intacta
    expect(await canon('4a')).toBe('4 años'); // la caja da igual: entra plegada (0021)

    // Meses, tope 36. '12-18M' es el que funde con las 1407 variantes de Zara y H&M.
    expect(await canon('12-18M')).toBe('12-18 meses');
    expect(await canon('3M')).toBe('3 meses');
    expect(await canon('1M')).toBe('1 mes'); // el singular, por la regla 2
    expect(await canon('36M')).toBe('36 meses'); // 3 años: el mayor mes real ('36 meses', Hipercor)
    expect(await canon('38M')).toBe('38M'); // pasa del tope: crudo

    // El rango con sufijo es real y lo destapó la primera pasada de Springfield: '8-9A', una
    // variante. #135 lo daba por no visto. En meses es además la forma mayoritaria (74 de 75).
    expect(await canon('8-9A')).toBe('8-9 años');
    expect(await canon('14-16A')).toBe('14-16A'); // un extremo llega al tope: crudo
    expect(await canon('24-38M')).toBe('24-38M');
    expect(await canon('4-4A')).toBe('4 años'); // el colapso de la 0020 actúa antes que la regla
    expect(await canon('3-3M')).toBe('3 meses');

    // Y lo que las deja fuera: la talla por letra no lleva dígitos, así que no entra en la regla.
    // Springfield publica las dos cosas en el mismo catálogo, 'M' de Medium incluida.
    for (const letra of ['XS', 'S', 'M', 'L', 'XL', 'XXL']) {
      expect(await canon(letra)).toBe(letra);
    }
  });

  /**
   * El singular (#73). El defecto venía de la 0014, que escribió las unidades como literales en
   * plural, y no se veía hasta que la ropa de bebé de Sfera trajo meses sueltos.
   *
   * Las tres reglas que pueden emitir un número solo están cubiertas, incluida la 4 —que la issue
   * daba por sospecha y resultó tener dato real en `qa`: `1 años (17-19)`—. Los rangos no pueden
   * fallar aquí porque su salida lleva siempre dos números.
   */
  it('usa el singular cuando el número es 1, y solo entonces', async () => {
    expect(await canon('1 mes')).toBe('1 mes'); // regla 2
    expect(await canon('1 año')).toBe('1 año'); // regla 4
    expect(await canon('1')).toBe('1 año'); // regla 6

    // La frontera: todo lo demás sigue en plural, y '1.5' NO es '1'.
    expect(await canon('2 meses')).toBe('2 meses');
    expect(await canon('2 años')).toBe('2 años');
    expect(await canon('1.5 años')).toBe('1.5 años');
    expect(await canon('1½ años (86 cm)')).toBe('1.5 años');
    // Y un 1 que forma parte de otro número no cuenta como singular.
    expect(await canon('21 meses')).toBe('21 meses');
    expect(await canon('11 años')).toBe('11 años');
    expect(await canon('1-2 meses')).toBe('1-2 meses');
    expect(await canon('1-2 años (92 cm)')).toBe('1-2 años');
  });

  /**
   * El rango que colapsa en sí mismo (#89). El único dato real era en meses ('0-0 meses', 4
   * variantes de Zara), pero la regla se escribió GENERAL a propósito: el rango se colapsa antes de
   * las siete reglas, así que vale igual para años y para número de pie, y el singular de la 0019
   * se reusa en vez de reescribirse — por eso '1-1 meses' sale '1 mes' y no '1 meses'.
   */
  it('colapsa el rango cuyos dos extremos coinciden, en cualquier unidad', async () => {
    expect(await canon('0-0 meses (50 cm)')).toBe('0 meses'); // la forma real medida
    expect(await canon('1-1 meses')).toBe('1 mes'); // el singular de la 0019, gratis
    expect(await canon('22-22 meses')).toBe('22 meses');
    expect(await canon('4-4 años')).toBe('4 años');
    expect(await canon('1-1 años')).toBe('1 año');
    expect(await canon('6/6 años')).toBe('6 años'); // la barra también es separador
    expect(await canon('20-20')).toBe('20'); // sin unidad y por encima del umbral 15: pie
    expect(await canon('4-4')).toBe('4 años'); // sin unidad y por debajo: edad
  });

  /**
   * La guarda del colapso, que no es decorativa. Se implementa con una retro-referencia («el mismo
   * texto a los dos lados»), y sin delimitar los extremos '11-110' encajaría como '11-11' dejando un
   * '10' suelto. Las formas de aquí son reales: las tallas en cm de C&A son justo las que comparten
   * principio ('110-116') o final.
   */
  it('no colapsa un rango porque un extremo empiece igual que el otro', async () => {
    expect(await canon('110-116')).toBe('110-116');
    expect(await canon('98-104')).toBe('98-104');
    expect(await canon('11-110')).toBe('11-110 años');
    expect(await canon('110-11')).toBe('110-11 años');
    expect(await canon('0-1 meses')).toBe('0-1 meses'); // el hermano real del '0-0' del mismo pack
    expect(await canon('1-11 meses')).toBe('1-11 meses');
  });

  /**
   * La unidad en MAYÚSCULAS (#105). Todas las reglas buscan la unidad en minúsculas ('mes',
   * 'a[nñ]o') sobre la entrada ya plegada, así que si el plegado no baja la vocal acentuada la
   * talla no cae en ninguna regla y sale cruda. Hipercor es la única tienda que hoy escribe así,
   * pero la que viene puede hacerlo igual: esto fija la propiedad, no el caso.
   */
  it('reconoce la unidad escrita en mayúsculas, con acento y sin él', async () => {
    expect(await canon('6 AÑOS')).toBe('6 años');
    expect(await canon('1 AÑO')).toBe('1 año'); // y el singular de la 0019 sigue en pie
    expect(await canon('3 MESES')).toBe('3 meses');
    expect(await canon('12-18 MESES (86 CM)')).toBe('12-18 meses');
    expect(await canon('L (12-14 AÑOS) (140 CM)')).toBe('12-14 años');
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
      '1 mes', // #73: el singular no cambia `size_sort`, que busca 'mes' y lo encuentra igual
      '0 meses', // #89: el rango colapsado tampoco lo cambia — ya ordenaba por el primer extremo
    ];
    const [{ v: ordenada }] = await sql<{ v: string[] }[]>`
      SELECT array_agg(t ORDER BY size_sort(t), t) AS v
      FROM unnest(${sql.array(ropa)}::text[]) AS t`;
    expect(ordenada).toEqual([
      '0 meses', // recién nacido: el primero de la lista, como debe
      '1 mes', // 1/12 de año: delante de '1-3 meses', que empieza igual pero abarca más
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
