import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { makeSql, TEST_DB } from './helpers';

/**
 * `color_canon` (migración 0015, issue #49).
 *
 * Los casos NO son inventados: son los 220 valores distintos que había en `dev` el 31/07/2026 con
 * Zara, Sfera y Lefties ingeridas, reducidos a los que documentan una regla o un límite. Si una
 * tienda futura trae una forma nueva, se añade aquí antes de tocar la función.
 */
describe.skipIf(!TEST_DB)('color canónico', () => {
  let sql: postgres.Sql;

  const canon = async (value: string): Promise<string> => {
    const [row] = await sql<{ v: string }[]>`SELECT color_canon(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSql();
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
      expect(await canon(await canon('120 Crudo'))).toBe('crudo');
    });
  });

  /**
   * Los límites declarados de esta normalización, fijados a propósito (ver 0015 y la issue #49).
   * Si algún día se amplía la función, estos tests son los que hay que reescribir, y así el cambio
   * de criterio queda a la vista en vez de colarse.
   */
  describe('lo que NO funde, a propósito', () => {
    it('no toca los códigos pelados, que no tienen nombre que rescatar', async () => {
      // '107', '140' y '771' son 10 productos cuyo color, tal como lo sirve Sfera, es solo el
      // número. No hay nada que canonicalizar: recuperarlos exige la PDP, tras Akamai.
      for (const pelado of ['107', '140', '771']) {
        expect(await canon(pelado)).toBe(pelado);
      }
    });

    it('exige tres dígitos exactos, para no destrozar un color que empiece por número', async () => {
      // La regla suelta `[0-9]+` se comería el nombre el día que entre una tienda con colores así.
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
});
