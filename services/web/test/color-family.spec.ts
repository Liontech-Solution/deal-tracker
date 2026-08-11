import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { BASES_CANON, saltarSiNoHayBase, makeSqlAt } from './helpers';

/**
 * `color_family` (migración 0029; issue #291).
 *
 * Los casos NO son inventados: salen de los **2.940 colores canónicos** que tenía `deal_tracker_qa`
 * el 11/08/2026, y en particular de los 258 que no encajaban en ninguna de las 17 familias que ya
 * existían en `frontend/src/lib/colors.ts`. Si una tienda futura trae una forma nueva, se añade
 * aquí antes de tocar la función.
 *
 * Se ejecuta contra **todas las bases configuradas** (ver `BASES_CANON`), igual que su vecino
 * `color-canon.spec.ts` y por el mismo motivo (#105): el veredicto dependía del ctype y salía
 * distinto en CI que en el cluster. Aquí la dependencia es indirecta —`color_family` se apoya en
 * `color_canon`, que es quien pliega la caja acentuada desde la 0021— y precisamente por eso hay
 * que fijarla: si alguien rehace el plegado sin pasar por `color_canon`, estos casos lo cazan.
 */
saltarSiNoHayBase('familia de color');

describe.each(BASES_CANON)('familia de color · $nombre', ({ url }) => {
  let sql: postgres.Sql;

  const familia = async (value: string): Promise<string | null> => {
    const [row] = await sql<{ v: string | null }[]>`SELECT color_family(${value}) AS v`;
    return row.v;
  };

  beforeAll(async () => {
    sql = makeSqlAt(url);
    await runMigrations(sql);
  });

  afterAll(async () => {
    await sql.end();
  });

  /**
   * Lo que motiva la issue: el 85,2 % de los 2.859 chips eran compuestos, y detrás de la barra va
   * el nombre del dibujo o de la licencia, no un color.
   *
   * SE PLIEGA POR EL SEGMENTO ANTERIOR A LA BARRA, y está medido: 385 colores (13,5 %) caerían en
   * la familia equivocada mirando la cadena entera. Los dos primeros casos son exactamente eso.
   */
  describe('el compuesto con barra', () => {
    it.each([
      ['amarillo claro/bluey', 'amarillo'], // «blue» dentro del nombre del dibujo
      ['amarillo claro/blanco', 'amarillo'],
      ['óxido/lunares azules', 'teja'], // H&M, el mismo valor que fija color-canon
      ['azul / índigo', 'azul'],
    ])('«%s» es de la familia %s, no de la del dibujo', async (valor, esperada) => {
      expect(await familia(valor)).toBe(esperada);
    });
  });

  /**
   * El orden de las reglas es parte del contrato: casi todas son subcadenas de alguna otra. Cada
   * caso de aquí es un par que se pisaría si se cambiara el orden del CASE.
   */
  describe('el orden de las reglas', () => {
    it.each([
      ['azul marino', 'marino'], // 'marino' antes que 'azul'
      ['azul cielo', 'celeste'], // 'celeste' antes que 'azul'
      ['gris topo', 'gris'], // 'gris' antes que 'beige' ('topo' suelto es beige)
      ['topo', 'beige'],
      ['perla vigoré', 'gris'], // 'vigoré' antes que 'perla'
      ['marrón rojizo', 'marrón'], // 'marrón' antes que 'rojo'
      ['rojizo', 'rojo'],
      ['blanco rayas', 'blanco'], // 'estampado' va la ÚLTIMA: el color gana al dibujo
    ])('«%s» es %s', async (valor, esperada) => {
      expect(await familia(valor)).toBe(esperada);
    });
  });

  /**
   * Los huecos de vocabulario que encontró la medición, que eran la mayor parte del problema: no
   * eran estampados, eran nombres de color que la tabla de 17 no conocía. El número es la cuenta
   * de variantes vivas que tenía cada uno en QA.
   */
  describe('el vocabulario que faltaba', () => {
    it.each([
      ['kaki', 'verde'], // 701 — había 'khaki' y 'caqui', no la grafía castellana
      ['beis', 'beige'], // 324 — había 'beige'
      ['plateado', 'gris'], // 58 — NO contiene 'plata'
      ['fucsia', 'rosa'], // 506
      ['berenjena', 'morado'], // 370
      ['índigo', 'azul'], // 300
      ['indigo', 'azul'], // 158 — la misma tienda lo escribe de las dos formas
      ['lima', 'verde'], // 272
      ['limón', 'amarillo'], // 24 — se parece a 'lima' y no es lo mismo
      ['cuero', 'marrón'], // 246
      ['burgundy', 'rojo'], // 236
      ['turquesa', 'turquesa'], // 845 — familia propia
      ['turquesa empolvado', 'turquesa'], // 113
      ['dark turquoise', 'turquesa'], // 12 — H&M en inglés
      ['aceite', 'verde'], // 18
      ['petróleo', 'azul'], // 64
      ['petroleo', 'azul'], // 10 — sin tilde, la misma idea
      ['carbón', 'gris'], // 6
      ['carbon', 'gris'], // 24
    ])('«%s» es %s', async (valor, esperada) => {
      expect(await familia(valor)).toBe(esperada);
    });
  });

  /**
   * 'estampado' es una FAMILIA, no un cajón 'otros': se ofrece como chip a propósito, porque hoy
   * 'rayas' (526), 'multicolor' (841), 'estampado' (559) y 'leopardo' (114) SON chips —perdidos
   * entre 2.859, pero están— y el buscador libre no los repesca: `fold()` cubre nombre, categoría
   * y género, y el color no entra ahí.
   */
  describe('estampado', () => {
    it.each([
      'multicolor',
      'estampado',
      'rayas',
      'leopardo',
      'animal print',
      'bicolor',
      'combinado',
      'varios colores',
    ])('«%s» es estampado', async (valor) => {
      expect(await familia(valor)).toBe('estampado');
    });
  });

  /**
   * Lo que se queda sin familia, que en QA eran 7 valores y 74 variantes (0,04 %). Ninguno nombra
   * un color, y `pickColors()` los filtra fuera de la faceta con el mismo `IS NOT NULL` que ya
   * usaba para lo que niega `color_canon` desde #51.
   */
  describe('lo que no nombra ningún color', () => {
    it.each([
      '1-114', // código interno que la 0016 no atrapa: NO son solo dígitos, llevan el guion
      '1-905',
      'default',
      'único',
      'béisbol',
    ])('«%s» no tiene familia', async (valor) => {
      expect(await familia(valor)).toBeNull();
    });

    it('hereda el NULL de color_canon para el nombre que son solo dígitos (#51)', async () => {
      expect(await familia('771')).toBeNull();
    });
  });

  /**
   * Idempotencia. Es lo que permite aplicarla a los dos lados de la comparación del filtro sin
   * razonar sobre cuál venía ya plegado, igual que `color_canon`, y es lo que hace que un chip
   * pinchado en el panel (que ya es una familia) siga filtrando lo mismo.
   */
  it('es idempotente sobre su propia salida', async () => {
    const familias = [
      'negro',
      'gris',
      'marino',
      'turquesa',
      'celeste',
      'azul',
      'salvia',
      'verde',
      'teja',
      'marrón',
      'rojo',
      'rosa',
      'naranja',
      'amarillo',
      'morado',
      'beige',
      'crema',
      'blanco',
      'estampado',
    ];
    for (const f of familias) {
      expect(await familia(f), `«${f}»`).toBe(f);
    }
  });

  /**
   * La caja y el acento los pliega `color_canon` (0021), y esta función se apoya en ella. Lefties
   * escribe todos sus colores en MAYÚSCULAS, así que sin ese plegado estos casos se irían a NULL
   * bajo el ctype `C` del cluster y el chip saldría partido — que es el fallo de #105.
   */
  it('no depende de la caja ni del acento, tampoco bajo ctype C', async () => {
    expect(await familia('MARRÓN')).toBe('marrón');
    expect(await familia('ÍNDIGO')).toBe('azul');
    expect(await familia('AÑIL DELAVADO')).toBe('azul');
    expect(await familia('GRIS VIGORÉ')).toBe('gris');
    expect(await familia('VERDE QUIRÓFANO')).toBe('verde');
  });
});
