import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { BASES_CANON, saltarSiNoHayBase, makeSqlAt } from './helpers';

/**
 * `color_canon` (migraciones 0015, 0016 y 0021; issues #49, #51 y #105).
 *
 * Los casos NO son inventados: son los 220 valores distintos que había en `dev` el 31/07/2026 con
 * Zara, Sfera y Lefties ingeridas —más los que estrenó la primera ingesta de Lefties el
 * 02/08/2026—, reducidos a los que documentan una regla o un límite. Si una tienda futura trae una
 * forma nueva, se añade aquí antes de tocar la función.
 *
 * Se ejecuta contra **todas las bases configuradas** (ver `BASES_CANON`), y eso es parte del test:
 * la canónica dependía del ctype de la base (#105) y el veredicto salía distinto en CI que en el
 * cluster. Las mismas aserciones tienen que valer en los dos sitios.
 */
saltarSiNoHayBase('color canónico');

describe.each(BASES_CANON)('color canónico · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  const canon = async (value: string): Promise<string | null> => {
    const [row] = await sql<{ v: string | null }[]>`SELECT color_canon(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  // Los 11 pares medidos: mismo color, distinta caja. Es la equivalencia que hace que un interés
  // guardado con el chip del filtro case con la variante de cualquier tienda.
  const equivalencias: Array<{ canonica: string; formas: string[] }> = [
    { canonica: 'azul marino', formas: ['Azul Marino', 'Azul marino'] },
    { canonica: 'chocolate', formas: ['Chocolate', 'chocolate'] },
    { canonica: 'frambuesa', formas: ['Frambuesa', 'frambuesa'] },
    { canonica: 'fucsia oscuro', formas: ['Fucsia Oscuro', 'Fucsia oscuro'] },
    { canonica: 'gris topo', formas: ['Gris Topo', 'Gris topo'] },
    { canonica: 'marino', formas: ['Marino', 'marino'] },
    { canonica: 'rayas', formas: ['Rayas', 'rayas'] },
    { canonica: 'rosa claro', formas: ['Rosa claro', 'rosa claro'] },
    { canonica: 'tostado', formas: ['Tostado', 'tostado'] },
    { canonica: 'verde', formas: ['VERDE', 'Verde', 'verde'] },
    { canonica: 'verde pato', formas: ['Verde Pato', 'Verde pato'] },
    // Los acentuados (#105). Con ctype `C` —el de la base del cluster— `lower()` no los bajaba, así
    // que estos NO se fundían: son 743 variantes y los dos chips que salían partidos en la faceta.
    // Lefties escribe todos sus colores en MAYÚSCULAS y por eso aporta 463 ella sola.
    { canonica: 'marrón', formas: ['MARRÓN', 'Marrón', 'marrón'] }, // chip partido nº 1
    { canonica: 'índigo', formas: ['ÍNDIGO', 'Índigo', 'índigo'] }, // chip partido nº 2
    { canonica: 'gris vigoré', formas: ['GRIS VIGORÉ'] }, // 196 variantes, el peor de Lefties
    { canonica: 'añil delavado', formas: ['AÑIL DELAVADO'] }, // la Ñ tampoco bajaba
    { canonica: 'visón', formas: ['VISÓN'] },
    { canonica: 'verde quirófano', formas: ['VERDE QUIRÓFANO'] },
    { canonica: 'óxido/lunares azules', formas: ['Óxido/lunares azules'] }, // H&M
    { canonica: 'azul / índigo', formas: ['Azul / Índigo'] }, // Zara, 12 variantes
  ];

  for (const { canonica, formas } of equivalencias) {
    it(`funde ${formas.map((f) => `«${f}»`).join(' = ')} en «${canonica}»`, async () => {
      for (const forma of formas) {
        expect(await canon(forma)).toBe(canonica);
      }
    });
  }

  it('pliega los espacios de sobra, dentro y fuera', async () => {
    expect(await canon('  Azul   Marino ')).toBe('azul marino');
    expect(await canon('Azul  /  Índigo')).toBe('azul / índigo');
  });

  it('es idempotente sobre su propia salida', async () => {
    for (const { canonica } of equivalencias) {
      expect(await canon(canonica)).toBe(canonica);
    }
  });

  it('devuelve el texto cuando no hay nada que plegar', async () => {
    // Preferimos un chip raro en la faceta a una variante que desaparece del filtro.
    expect(await canon('Estampado flores')).toBe('estampado flores');
  });

  /**
   * El código interno que Sfera antepone al nombre. Los 20 valores medidos en dev son suyos, y en 9
   * de los 11 que colisionan con un nombre suelto la colisión es con la propia Sfera: el código no
   * distingue dos colores, parte en dos el catálogo de una misma tienda.
   */
  describe('el código de Sfera', () => {
    it('se quita cuando queda nombre detrás', async () => {
      for (const [conCodigo, esperado] of [
        ['120 Crudo', 'crudo'],
        ['430 Azul oscuro', 'azul oscuro'],
        ['850 Piedra', 'piedra'],
        ['582 Verde agua', 'verde agua'],
      ]) {
        expect(await canon(conCodigo), `«${conCodigo}»`).toBe(esperado);
      }
    });

    it('hace que el valor con código y el suelto sean el mismo color', async () => {
      expect(await canon('120 Crudo')).toBe(await canon('Crudo'));
      expect(await canon('400 Azul')).toBe(await canon('azul'));
    });

    it('sigue siendo idempotente', async () => {
      const [row] = await sql<
        { v: string | null }[]
      >`SELECT color_canon(color_canon('120 Crudo')) AS v`;
      expect(row.v).toBe('crudo');
    });
  });

  /**
   * Los límites declarados de esta normalización, fijados a propósito (ver 0015 y la issue #49).
   * Si algún día se amplía la función, estos tests son los que hay que reescribir, y así el cambio
   * de criterio queda a la vista en vez de colarse.
   */
  describe('un nombre que son solo dígitos no es un nombre (#51)', () => {
    it('lo niega devolviendo NULL, que es como esta función dice «no hay etiqueta»', async () => {
      // '107', '140' y '771' son 10 productos de ZARA, que escribe el id del color en el campo del
      // nombre (verificado contra su API: {"id":"771","name":"771"}). No es el código de Sfera al
      // que le falta el nombre —eso lo resuelve la 0015—, aquí el número ES el nombre entero.
      // Nadie puede pinchar un chip '771', así que no pertenece a la faceta ni al aviso.
      for (const pelado of ['107', '140', '771']) {
        expect(await canon(pelado)).toBeNull();
      }
    });

    it('sigue siendo idempotente: color_canon(NULL) = NULL porque es STRICT', async () => {
      const [row] = await sql<{ v: string | null }[]>`SELECT color_canon(color_canon('771')) AS v`;
      expect(row.v).toBeNull();
    });

    it('compone con la regla del código de Sfera', async () => {
      // '120 456' -> se quita el prefijo -> '456' -> solo dígitos -> NULL.
      expect(await canon('120 456')).toBeNull();
    });

    it('no se lleva por delante un color que solo EMPIECE por número', async () => {
      // Llevan letras, así que no son "solo dígitos" y siguen intactos. Son los mismos casos que
      // protegía la 0015 al exigir tres dígitos exactos para el prefijo.
      expect(await canon('2 tonos')).toBe('2 tonos');
      expect(await canon('12 rayas')).toBe('12 rayas');
      expect(await canon('1200 Crudo')).toBe('1200 crudo');
    });

    it('no agrupa familias de color', async () => {
      // Para quien compra son colores distintos, y elegir cuáles se funden es producto, no formato.
      const azules = await Promise.all(['Azul claro', 'Azul medio', 'Azul oscuro'].map(canon));
      expect(new Set(azules).size).toBe(3);
      expect(await canon('Kaki')).not.toBe(await canon('Khaki'));
    });

    it('no pliega los acentos', async () => {
      // Ningún par medido lo necesita, y plegarlos degradaría el chip ('índigo' -> 'indigo').
      expect(await canon('Índigo')).toBe('índigo');
      expect(await canon('Índigo')).not.toBe(await canon('Indigo'));
    });
  });

  /**
   * El daño visible de #105, escrito como lo que se rompía de verdad: **un chip partido en dos**.
   * Un padre que filtraba por «marrón» veía una parte del catálogo, y un interés dado de alta sobre
   * un chip no casaba nunca con las prendas del otro — el fallo silencioso que motivó la 0015,
   * reproducido por el ctype de la base en vez de por la caja que escribe la tienda.
   *
   * Se comprueba como equivalencia entre las formas reales medidas en `dev` el 02/08/2026, no
   * contra un literal: lo que importa es que las dos caras acaben en el MISMO chip.
   */
  it('funde la caja acentuada, que es lo que partía el chip en dos (#105)', async () => {
    expect(await canon('MARRÓN')).toBe(await canon('marrón'));
    expect(await canon('ÍNDIGO')).toBe(await canon('índigo'));
    expect(await canon('TONO MARRÓN')).toBe(await canon('tono marrón'));

    // Y el plegado de caja NO se lleva por delante el acento, que es la decisión de la 0015: se
    // pliega la CAJA, no el acento, así que 'marrón' y 'marron' siguen siendo dos colores.
    expect(await canon('MARRÓN')).toBe('marrón');
    expect(await canon('MARRÓN')).not.toBe(await canon('MARRON'));
  });
});

/**
 * El índice que se retiró, y la función que NO (migración 0034; issue #317).
 *
 * `ix_variant_color_canon` (0015) era parcial por `delisted_at IS NULL` sobre `color_canon(color)`,
 * así que solo podía servir a una igualdad sobre todas las variantes vivas — el filtro de color del
 * catálogo, que #291 se llevó a `color_family`. Comprobado con `EXPLAIN` contra prod: los tres
 * llamantes que quedan (matching, alta de intereses y la ficha) no piden ese patrón, y un control
 * positivo demuestra que el índice se elegía en cuanto alguien sí lo pedía.
 *
 * Este spec fija la mitad que importa para no romper nada: **lo que muere es el índice, no la
 * función**. Si alguien confundiera las dos cosas, el matching dejaría de casar colores y el aviso
 * de Telegram se rompería en silencio.
 */
describe.each(BASES_CANON)('índice retirado de color_canon · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  it('ix_variant_color_canon ya no existe', async () => {
    const filas = await sql<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes WHERE indexname = 'ix_variant_color_canon'`;
    expect(filas).toHaveLength(0);
  });

  it('pero el de familias SÍ, que es el que usa el filtro de hoy', async () => {
    // Si esto se cayera junto con el otro, el filtro de color del catálogo pasaría de 3,4 ms a
    // 14 s (medido en la 0029) sin que ningún test lo dijera.
    const filas = await sql<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes WHERE indexname = 'ix_variant_color_family'`;
    expect(filas).toHaveLength(1);
  });

  it('y color_canon sigue viva: la usan el matching, los intereses y la ficha', async () => {
    const [row] = await sql<{ v: string }[]>`SELECT color_canon('AZUL MARINO') AS v`;
    expect(row.v).toBe('azul marino');
  });
});
