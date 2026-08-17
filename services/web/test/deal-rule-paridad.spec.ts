import { sql } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/postgres-js';
import type postgres from 'postgres';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import {
  classifyHonesty,
  honestDiscountPct,
  honestListPrice,
  honestyBasis,
  REAL_EVIDENCE_DAYS,
} from '../src/matching/deal-rule';
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
 * de una regla con seis entradas, cuatro de ellas nulables. Aquí se comparan los dos lados fila a
 * fila sobre el **producto cartesiano** de los valores interesantes de cada entrada — 23.040 casos,
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

/** Las mismas seis entradas de `DEAL_COLUMNS`, aquí como columnas de un `VALUES`. */
const COLUMNAS: DealSqlColumns = {
  price: sql`price`,
  listPrice: sql`list_price`,
  recentMin: sql`recent_min`,
  maxObserved: sql`max_observed`,
  priorPoints: sql`prior_points`,
  retailerMin30d: sql`retailer_min_30d`,
  trackedDays: sql`tracked_days`,
};

interface Caso {
  price: string | null;
  listPrice: string | null;
  recentMin: string | null;
  maxObserved: string | null;
  priorPoints: number;
  retailerMin30d: string | null;
  trackedDays: number;
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
 *
 * **Ejercido de verdad el 17/08/2026**, que era la última casilla viva de la v0.5.0 (#386): «mover
 * `INFLATED_LIST_MARGIN` en un solo lado hace fallar la suite» llevaba dos versiones siendo una
 * afirmación escrita y nunca comprobada. Los dos experimentos, y el segundo es el que da valor al
 * primero:
 *
 *  - **Un solo lado**: escribir el margen a mano aquí al lado (`sql.raw('1.05')` en lugar de
 *    `sql.raw(String(INFLATED_LIST_MARGIN))` en `deal-rule.sql.ts`) pone en **rojo tres** de los
 *    once tests de este fichero, y son justo los tres ejes que compara: el veredicto de los 23.040
 *    casos, el PVP creíble y el descuento honesto —el que ordena «Ofertas»—. Las filas que delata
 *    llevan todas `listPrice: '31.00'` con `maxObserved: '30.00'`, o sea **exactamente** el valor que
 *    #375 metió en el cartesiano para esto: 31,00 pasa el borde de 1,03 (30,90) y no el de 1,05, así
 *    que el SQL sigue creyéndose el tachado mientras el TypeScript ya lo ha tirado.
 *  - **El control**: mover `INFLATED_LIST_MARGIN` en `deal-rule.ts` —que por el `import` lo mueve en
 *    los dos lados a la vez— deja este fichero en **verde, 11 de 11**, y de las 36 redes del servicio
 *    solo fallan tres unitarios de `deal-rule.spec.ts` que llevan «el 3 %» **en su propio nombre**.
 *    Eso es lo que había que demostrar: este espejo es sensible a la **divergencia**, no al valor.
 *
 * Ojo a la asimetría con el experimento gemelo de `REAL_EVIDENCE_DAYS` (más abajo), porque invita a
 * confundirse: allí el control deja la suite **entera** en verde, aquí no puede, y no es un defecto
 * de ninguno de los dos. La diferencia es que el 3 % es una decisión de política con tres tests que
 * la fijan a propósito, así que cambiarlo de verdad exige tocarlos **a mano y queriendo** — un
 * rojo esperado, no un fallo. Si algún día se mueve el margen, esos tres son la lista.
 */
/**
 * El mínimo declarado de 30 días (#354). Cuatro valores elegidos por lo que hacen contra los
 * precios de arriba, no por parecer variados:
 *  - `null` es el caso de siete de las nueve tiendas, o sea la inmensa mayoría del catálogo;
 *  - `3.99` coincide con un precio y con un máximo, que es donde vive el empate;
 *  - `19.99` cae POR DEBAJO de los tachados grandes y por encima de los precios pequeños, así que
 *    ejercita el techo de `honestListPrice` en las dos direcciones;
 *  - `30.90` es exactamente `30.00 * 1.03`, el borde del margen, para que mover
 *    `INFLATED_LIST_MARGIN` en un solo lado también se vea por esta entrada.
 */
const MINIMOS_DECLARADOS = [null, '3.99', '19.99', '30.90'];

const PRECIOS = [null, '3.99', '19.99', '30.00'];
const TACHADOS = [null, '3.99', '30.90', '31.00', '39.99', '99.99'];
const MAXIMOS = [null, '3.99', '19.99', '30.00', '39.99'];
const MINIMOS = [null, '3.99', '24.00', '39.99'];
const PUNTOS = [0, 1, 5];

/**
 * Días de cobertura, la séptima entrada — nueva con #436, que es la que separa `real` de `reciente`.
 *
 * Los cuatro valores caen **a los dos lados de `REAL_EVIDENCE_DAYS`** y pegados al borde
 * (`13.99` / `14`), que es lo que hace que mover la constante en un solo lado rompa esta suite en
 * vez de cambiar el catálogo en silencio. `0` es el caso mayoritario del catálogo de hoy y `120`
 * cruza además el umbral de #332, para que la independencia entre los dos siga vigilada.
 *
 * `numeric` y no `int` a propósito: `tracked_days` sale de un `EXTRACT(EPOCH …) / 86400` y llega
 * fraccionario, así que el borde real que hay que ejercitar no es entero.
 *
 * **Ejercido de verdad el 17/08/2026**, porque «rompe la suite» era hasta entonces una afirmación
 * sin comprobar y es una casilla de la definición de hecho de la v0.6.0 (#437). Los dos
 * experimentos, y hacen falta los dos:
 *
 *  - **Un solo lado**: escribir el umbral a mano en `deal-rule.sql.ts` (`sql.raw('7')` en lugar de
 *    `sql.raw(String(REAL_EVIDENCE_DAYS))`) pone en **rojo** «SQL y TypeScript dan el mismo
 *    veredicto», y las combinaciones que delata son exactamente las de `trackedDays: 13.99` —el SQL
 *    dice `real`, el TS dice `reciente`—. Ninguna otra red de las 36 se enteró.
 *  - **El control**: mover `REAL_EVIDENCE_DAYS` en `deal-rule.ts` y solo ahí deja la suite entera en
 *    **verde** (821 tests). Sin este segundo experimento el primero no prueba nada: podría estar
 *    fallando cualquier expectativa que tuviera el 14 escrito a mano. Lo que protege el espejo es el
 *    `import`, y esto es lo que lo demuestra.
 */
const COBERTURAS = [0, 13.99, 14, 120];

const CASOS: Caso[] = PRECIOS.flatMap((price) =>
  TACHADOS.flatMap((listPrice) =>
    MAXIMOS.flatMap((maxObserved) =>
      MINIMOS.flatMap((recentMin) =>
        PUNTOS.flatMap((priorPoints) =>
          MINIMOS_DECLARADOS.flatMap((retailerMin30d) =>
            COBERTURAS.map((trackedDays) => ({
              price,
              listPrice,
              recentMin,
              maxObserved,
              priorPoints,
              retailerMin30d,
              trackedDays,
            })),
          ),
        ),
      ),
    ),
  ),
);

/** El mismo caso, como entrada de `classifyHonesty` con los parámetros que usa el catálogo. */
function comoDealInput(c: Caso, trackedDays: number = c.trackedDays): DealInput {
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

    // Los 23.040 casos como filas de un VALUES, **en lotes**: la expresión que se ejercita es LA
    // MISMA que compone `catalog.service.ts`, solo que apuntando a otras columnas.
    //
    // El troceo no es una optimización: `sql.join` construye la consulta recursivamente y con el
    // cartesiano entero en una sola llamada drizzle se queda sin pila (`Maximum call stack size
    // exceeded`) antes de mandar nada. 5.760 por viaje es el tamaño con el que este test vivió
    // hasta #436, o sea el que ya se sabe que aguanta. Cubrir menos casos para ahorrarse esto sería
    // pagar el borde del umbral con cobertura, que es justo lo que el test existe para tener.
    const LOTE = 5_760;
    const rows: {
      idx: number;
      is_real_deal: boolean;
      honest_list_price: string | null;
      honest_discount: string;
    }[] = [];

    for (let desde = 0; desde < CASOS.length; desde += LOTE) {
      const lote = CASOS.slice(desde, desde + LOTE);
      const filas = sql.join(
        lote.map(
          (c, i) =>
            sql`(${desde + i}::int, ${c.price}::numeric, ${c.listPrice}::numeric,
                 ${c.recentMin}::numeric, ${c.maxObserved}::numeric, ${c.priorPoints}::int,
                 ${c.retailerMin30d}::numeric, ${c.trackedDays}::numeric)`,
        ),
        sql`, `,
      );
      rows.push(
        ...((await db.execute(sql`
          SELECT idx,
                 ${isRealDealSql(COLUMNAS)}      AS is_real_deal,
                 ${honestListPriceSql(COLUMNAS.listPrice, COLUMNAS.maxObserved, COLUMNAS.retailerMin30d)}
                   AS honest_list_price,
                 ${honestDiscountSql(COLUMNAS)}  AS honest_discount
          FROM (VALUES ${filas})
            AS t(idx, price, list_price, recent_min, max_observed, prior_points, retailer_min_30d,
                 tracked_days)
          ORDER BY idx
        `)) as unknown as typeof rows),
      );
    }

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

  it('SQL y TypeScript dan el mismo veredicto en los 23.040 casos', () => {
    const discrepancias = CASOS.map((c, i) => ({ caso: c, sql: veredictosSql[i] }))
      .filter(({ caso, sql: enSql }) => enSql !== (classifyHonesty(comoDealInput(caso)) === 'real'))
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
      .filter(
        ({ caso, sql: enSql }) =>
          enSql !== honestListPrice(caso.listPrice, caso.maxObserved, caso.retailerMin30d),
      )
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
          enSql !==
          honestDiscountPct(
            caso.price,
            honestListPrice(caso.listPrice, caso.maxObserved, caso.retailerMin30d),
          ),
      )
      .slice(0, 5);

    expect(discrepancias).toEqual([]);
  });

  it('el umbral de evidencia de #332 sigue sin poder mover el veredicto `real`', () => {
    // La invariante que hacía seguro el cambio de #332: su umbral solo condiciona `suspicious`, y
    // el espejo SQL no calcula `suspicious`. Sigue en pie **por encima de la cobertura de #436**:
    // una vez pasado `REAL_EVIDENCE_DAYS`, seguir sumando días no puede cambiar nada, y los tres
    // valores de abajo cruzan `HONESTY_EVIDENCE_DAYS` (89 y 90) sin que el veredicto se mueva.
    //
    // Las otras dos constantes de la regla no entran en el espejo: `minDiscountPct` es 0 por
    // contrato en todo el catálogo, y `EPSILON` solo actúa cuando ese umbral es mayor que 0.
    const referencia = CASOS.map((c) => classifyHonesty(comoDealInput(c, 14)) === 'real');
    for (const dias of [89, 90, 10_000]) {
      const enTs = CASOS.map((c) => classifyHonesty(comoDealInput(c, dias)) === 'real');
      expect(enTs).toEqual(referencia);
    }
  });

  it('la cobertura de #436 es lo ÚNICO que separa `real` de `reciente`', () => {
    // El corte nuevo, y la red que exige la definición de hecho de la v0.6.0: mover
    // `REAL_EVIDENCE_DAYS` en un solo lado tiene que romper la suite. Como `trackedDays` es ahora
    // una dimensión del cartesiano, el test del veredicto de arriba ya compara los dos lados a
    // ambos lados del borde (13,99 y 14); esto fija además que el corte no arrastra nada más.
    for (const c of CASOS) {
      const conCobertura = classifyHonesty(comoDealInput(c, REAL_EVIDENCE_DAYS));
      const sinCobertura = classifyHonesty(comoDealInput(c, REAL_EVIDENCE_DAYS - 0.01));

      if (conCobertura === 'real') {
        // Lo único que puede pasarle a un `real` al quitarle cobertura es caer a `reciente`.
        expect(sinCobertura).toBe('reciente');
      } else {
        // Y a nada más le afecta: quitar días no puede crear ni una acusación ni un elogio.
        expect(sinCobertura).toBe(conCobertura);
      }
    }
  });

  it('`reciente` no aparece nunca sin una bajada real detrás', () => {
    // El veredicto nuevo no es un cajón de sastre: es exactamente `real` sin cobertura. Si alguien
    // lo usara para etiquetar cualquier otra cosa, el badge «Bajada reciente» empezaría a afirmar
    // bajadas que no han ocurrido, que es el fallo simétrico del que #436 viene a arreglar.
    for (const c of CASOS) {
      if (classifyHonesty(comoDealInput(c)) !== 'reciente') continue;
      expect(classifyHonesty(comoDealInput(c, REAL_EVIDENCE_DAYS))).toBe('real');
    }
  });

  it('el mínimo declarado solo puede BAJAR el PVP creíble, nunca subirlo (#354)', () => {
    // Es la propiedad de la que cuelga todo lo demás: si el techo solo baja, el descuento honesto
    // solo puede encogerse, así que este cambio no puede convertir en «oferta real» nada que no lo
    // fuera ya. Sin esto habría que demostrar caso a caso que `onlyDeals` no se ensancha.
    for (const c of CASOS) {
      const sinTecho = honestListPrice(c.listPrice, c.maxObserved, null);
      const conTecho = honestListPrice(c.listPrice, c.maxObserved, c.retailerMin30d);

      // Y el techo tampoco puede CREAR una referencia donde no la había: sin histórico seguimos
      // callados, que es lo que #332 dejó establecido.
      if (sinTecho === null) {
        expect(conTecho).toBeNull();
        continue;
      }
      expect(conTecho).not.toBeNull();
      expect(conTecho as number).toBeLessThanOrEqual(sinTecho);
    }
  });

  it('la acusación por dato declarado y `real` son excluyentes por construcción (#354)', () => {
    // No es una casualidad de los datos de hoy —medido en QA el 14/08/2026: 0 filas en las dos a la
    // vez— sino una consecuencia del techo: si `price > min30 · margen`, el PVP creíble se queda en
    // `min30 < price`, la condición B cae y `evaluateDeal` no puede avisar. Si alguien cambiara el
    // techo por una vía paralela que no tocara `honestListPrice`, esto se rompería y sería justo lo
    // que hay que enterarse: la tarjeta diría «Oferta real» y la ficha «Precio inflado».
    const enAmbas = CASOS.filter(
      (c, i) => veredictosSql[i] && classifyHonesty(comoDealInput(c, 0)) === 'suspicious',
    );

    expect(enAmbas).toEqual([]);
  });

  it('la acusación por dato declarado NO espera a HONESTY_EVIDENCE_DAYS (#354)', () => {
    // La mitad del valor de #354 es esta: el umbral de 90 días existe porque `max_observed` es
    // NUESTRA observación y necesita madurar. El mínimo declarado no es nuestro, así que la
    // acusación vale desde el primer día — incluido el arranque en frío, que en QA era un tercio de
    // los casos (104 de 291). Si alguien le aplicara el umbral «por coherencia», el apagón hasta
    // noviembre volvería entero y en silencio.
    const caso: Caso = {
      price: '19.99',
      listPrice: '39.99',
      recentMin: null,
      maxObserved: null,
      priorPoints: 0,
      retailerMin30d: '3.99',
      // Cero cobertura: es justo el punto del test — esta acusación no la necesita. Los dos usos de
      // abajo lo pasan explícito a `comoDealInput`, así que este valor no decide nada.
      trackedDays: 0,
    };

    expect(classifyHonesty(comoDealInput(caso, 0))).toBe('suspicious');
    expect(honestyBasis(comoDealInput(caso, 0))).toBe('declarado');
    // Y la misma prenda sin el dato de la tienda se queda callada, que es el estado de antes.
    expect(classifyHonesty(comoDealInput({ ...caso, retailerMin30d: null }, 0))).toBe('none');
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
