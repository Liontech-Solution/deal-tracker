import { sql } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/postgres-js';
import type postgres from 'postgres';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { classifyHonesty, honestDiscountPct, honestListPrice } from '../src/matching/deal-rule';
import type { DealInput } from '../src/matching/deal-rule';
import {
  honestDiscountSql,
  honestListPriceSql,
  isRealDealSql,
  type DealSqlColumns,
} from '../src/matching/deal-rule.sql';
import { makeSql, TEST_DB } from './helpers';

/**
 * Paridad entre la regla de honestidad y su espejo SQL (#228).
 *
 * La regla vive en dos sitios a propósito: `classifyHonesty()` es TypeScript y se evalúa sobre las
 * filas **ya paginadas**, lo que sirve para etiquetar una tarjeta pero no para filtrar
 * («Solo ofertas») ni para ordenar (`sort=ofertas`), que tienen que decidirse antes del `LIMIT`.
 * De ahí `isRealDealSql()`.
 *
 * La red que había era un test extremo a extremo sobre **cuatro productos sembrados a mano**
 * (`catalog.e2e.spec.ts`), y esa es la duda que #228 levantaba: cuatro casos no cubren los bordes
 * de una regla con cinco entradas, tres de ellas nulables. Aquí se comparan los dos lados fila a
 * fila sobre el **producto cartesiano** de los valores interesantes de cada entrada — 1.440 casos,
 * incluidos todos los nulos y los dos lados del margen del 3 %.
 *
 * Cartesiano y no aleatorio a posta: es determinista (un fallo se reproduce siempre igual), no
 * necesita semilla ni biblioteca, y sobre un dominio tan pequeño cubre más que un muestreo.
 *
 * **Se comparan tres cosas, y el veredicto `real` es la menos sensible de las tres** (#375). El
 * margen del PVP inflado no puede mover `real` sobre ninguna fila que la base sea capaz de
 * producir —el razonamiento está en el test del PVP creíble—, así que comparar solo el veredicto
 * dejaba pasar una desalineación de `INFLATED_LIST_MARGIN` entera. Lo que sí la ve es el PVP
 * creíble, y lo que la convierte en daño visible es el descuento honesto, que ordena «Ofertas».
 */

/** Las mismas cinco entradas de `DEAL_COLUMNS`, aquí como columnas de un `VALUES`. */
const COLUMNAS: DealSqlColumns = {
  price: sql`price`,
  listPrice: sql`list_price`,
  recentMin: sql`recent_min`,
  maxObserved: sql`max_observed`,
  priorPoints: sql`prior_points`,
};

interface Caso {
  price: string | null;
  listPrice: string | null;
  recentMin: string | null;
  maxObserved: string | null;
  priorPoints: number;
}

/**
 * Los valores no son redondos por casualidad:
 *  - `30.90` es exactamente `30.00 * 1.03`, el borde del margen de tolerancia del PVP inflado;
 *  - `31.00` cae **por encima** de ese borde y por debajo de `30.00 * 1.05`, o sea dentro de la
 *    franja que el cartesiano original no pisaba: sin él, subir `INFLATED_LIST_MARGIN` en un solo
 *    lado no lo veía nadie (#375);
 *  - `3.99` repetido en varias entradas fuerza los empates (`price == max_observed`,
 *    `price == recent_min`), que es donde vive el fallo de #332;
 *  - `null` está en las cuatro entradas nulables, que es lo que el seed de 4 productos no cubría.
 */
const PRECIOS = [null, '3.99', '19.99', '30.00'];
const TACHADOS = [null, '3.99', '30.90', '31.00', '39.99', '99.99'];
const MAXIMOS = [null, '3.99', '19.99', '30.00', '39.99'];
const MINIMOS = [null, '3.99', '24.00', '39.99'];
const PUNTOS = [0, 1, 5];

const CASOS: Caso[] = PRECIOS.flatMap((price) =>
  TACHADOS.flatMap((listPrice) =>
    MAXIMOS.flatMap((maxObserved) =>
      MINIMOS.flatMap((recentMin) =>
        PUNTOS.map((priorPoints) => ({ price, listPrice, recentMin, maxObserved, priorPoints })),
      ),
    ),
  ),
);

/** El mismo caso, como entrada de `classifyHonesty` con los parámetros que usa el catálogo. */
function comoDealInput(c: Caso, trackedDays: number): DealInput {
  return { ...c, trackedDays, minDiscountPct: 0, compareBase: 'recent_min' };
}

describe.skipIf(!TEST_DB)('paridad de la regla de honestidad con su espejo SQL (#228)', () => {
  let client: postgres.Sql;
  let veredictosSql: boolean[];
  let pvpsSql: (number | null)[];
  let descuentosSql: number[];

  beforeAll(async () => {
    client = makeSql();
    const db = drizzle(client);

    // Un solo viaje a la base con los 1.200 casos como filas de un VALUES: la expresión que se
    // ejercita es LA MISMA que compone `catalog.service.ts`, solo que apuntando a otras columnas.
    const filas = sql.join(
      CASOS.map(
        (c, i) =>
          sql`(${i}::int, ${c.price}::numeric, ${c.listPrice}::numeric, ${c.recentMin}::numeric,
               ${c.maxObserved}::numeric, ${c.priorPoints}::int)`,
      ),
      sql`, `,
    );
    const rows = (await db.execute(sql`
      SELECT idx,
             ${isRealDealSql(COLUMNAS)}      AS is_real_deal,
             ${honestListPriceSql(COLUMNAS.listPrice, COLUMNAS.maxObserved)} AS honest_list_price,
             ${honestDiscountSql(COLUMNAS)}  AS honest_discount
      FROM (VALUES ${filas})
        AS t(idx, price, list_price, recent_min, max_observed, prior_points)
      ORDER BY idx
    `)) as unknown as {
      idx: number;
      is_real_deal: boolean;
      honest_list_price: string | null;
      honest_discount: string;
    }[];

    veredictosSql = rows.map((r) => Boolean(r.is_real_deal));
    // `numeric` viaja como string; el espejo TS devuelve números.
    pvpsSql = rows.map((r) => (r.honest_list_price === null ? null : Number(r.honest_list_price)));
    descuentosSql = rows.map((r) => Number(r.honest_discount));
  });

  afterAll(async () => {
    await client.end();
  });

  it('el corpus cubre los dos veredictos, y no compara dos listas vacías', () => {
    const reales = veredictosSql.filter(Boolean).length;

    expect(veredictosSql).toHaveLength(CASOS.length);
    expect(reales).toBeGreaterThan(0);
    expect(reales).toBeLessThan(CASOS.length);
  });

  it('SQL y TypeScript dan el mismo veredicto en los 1.440 casos', () => {
    const discrepancias = CASOS.map((c, i) => ({ caso: c, sql: veredictosSql[i] }))
      .filter(({ caso, sql: enSql }) => enSql !== (classifyHonesty(comoDealInput(caso, 0)) === 'real'))
      .slice(0, 5);

    // La lista de discrepancias sale en el mensaje del fallo: si esto rompe, lo primero que hace
    // falta es saber QUÉ combinación se separó, no cuántas.
    expect(discrepancias).toEqual([]);
  });

  it('el PVP creíble es el mismo en los dos lados — ahí es donde vive el margen', () => {
    // Por qué esto no lo cubría ya el veredicto `real` (#375): para que `INFLATED_LIST_MARGIN`
    // mueva `real` hace falta que `honest > price` cambie de valor, o sea `price` entre el máximo
    // observado y el tachado; y la condición A exige `price < recent_min`. Como en la base
    // `recent_min <= max_observed` siempre, las dos cosas se contradicen: **sobre filas que la CTE
    // `stats` pueda producir, la paridad de `isRealDealSql` es insensible al margen**. Medido: con
    // el SQL en 1.05 y el TS en 1.03, los 725 tests del servicio pasaban.
    //
    // Aquí no hay condición A que valga: el PVP creíble se compara crudo, así que cualquier
    // tachado de la franja separa los dos lados.
    const discrepancias = CASOS.map((c, i) => ({ caso: c, sql: pvpsSql[i] }))
      .filter(({ caso, sql: enSql }) => enSql !== honestListPrice(caso.listPrice, caso.maxObserved))
      .slice(0, 5);

    expect(discrepancias).toEqual([]);
  });

  it('el descuento honesto —el que ORDENA «Ofertas»— es el mismo en los dos lados', () => {
    // `honestDiscountSql` alimenta el `ORDER BY` de `sort=ofertas` (`catalog.service.ts`), y ese
    // orden se aplica a TODAS las filas, no solo a las `real`. Una divergencia aquí no se ve como
    // una etiqueta equivocada sino como un adelantamiento: la prenda cuyo tachado un lado
    // considera inflado y el otro no, encabezando la lista que existe para castigarlo.
    const discrepancias = CASOS.map((c, i) => ({ caso: c, sql: descuentosSql[i] }))
      .filter(
        ({ caso, sql: enSql }) =>
          enSql !== honestDiscountPct(caso.price, honestListPrice(caso.listPrice, caso.maxObserved)),
      )
      .slice(0, 5);

    expect(discrepancias).toEqual([]);
  });

  it('el umbral de evidencia de #332 no puede mover el veredicto `real`', () => {
    // La invariante que hace seguro el cambio de #332: el umbral solo condiciona `suspicious`, y
    // el espejo SQL no calcula `suspicious`. Si alguien mete los días de cobertura en la regla del
    // aviso, los dos lados se separan y esto lo caza antes de que el catálogo y el aviso se
    // contradigan delante del usuario.
    //
    // Revisado en #375 por el mismo sesgo de borde que tenía el margen: estos cinco valores sí
    // caen a los dos lados de `HONESTY_EVIDENCE_DAYS` (89 y 90), así que aquí no hay punto ciego.
    // Las otras dos constantes de la regla no lo tienen porque no entran en el espejo:
    // `minDiscountPct` es 0 por contrato en todo el catálogo, y `EPSILON` solo actúa cuando ese
    // umbral es mayor que 0, o sea nunca por este camino.
    for (const dias of [0, 1, 89, 90, 10_000]) {
      const enTs = CASOS.map((c) => classifyHonesty(comoDealInput(c, dias)) === 'real');
      expect(enTs).toEqual(veredictosSql);
    }
  });

  it('`real` implica haber visto la prenda MÁS CARA que ahora', () => {
    // El otro lado de la misma invariante, y la razón por la que el umbral de #332 no necesita
    // tocar el espejo: si `max_observed <= price`, entonces `recent_min <= price` y la condición A
    // ("solo mínimos nuevos") cae antes de llegar a ninguna acusación.
    //
    // Ese "entonces" se apoya en un invariante de los DATOS, no de la regla: `recent_min` es un
    // MIN sobre las observaciones anteriores dentro de la ventana y `max_observed` un MAX sobre
    // todas, así que en la base `recent_min <= max_observed` siempre. El cartesiano de arriba no lo
    // respeta —genera `recent_min` 39,99 con `max_observed` 30,00, que la CTE `stats` no puede
    // producir— y por eso aquí se filtra: comprobarlo sobre filas imposibles no probaría nada del
    // catálogo, solo del generador. Queda escrito porque es la premisa de la que cuelga el cambio
    // de #332: si algún día `stats` dejara de cumplirla, esta implicación se cae con ella.
    const realizables = CASOS.filter(
      (c, i) =>
        veredictosSql[i] &&
        c.recentMin !== null &&
        c.maxObserved !== null &&
        Number(c.recentMin) <= Number(c.maxObserved),
    );

    expect(realizables.length).toBeGreaterThan(0);
    for (const c of realizables) {
      expect(Number(c.maxObserved)).toBeGreaterThan(Number(c.price));
    }
  });
});
