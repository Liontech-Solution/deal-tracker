/**
 * Espejo **en SQL** de la regla de honestidad de `deal-rule.ts`.
 *
 * Por qué existe: `classifyHonesty()` es TypeScript y se evalúa *después* de la consulta, sobre las
 * filas ya paginadas. Eso vale para *etiquetar* una tarjeta, pero no para **filtrar** («Solo ofertas
 * reales») ni para **ordenar** el catálogo: ambas cosas tienen que ocurrir dentro del `ORDER BY` /
 * `WHERE`, antes del `LIMIT`.
 *
 * Contrato: `isRealDealSql(...)` debe ser cierto exactamente cuando
 * `classifyHonesty({ ...mismos valores, minDiscountPct: 0, compareBase: 'recent_min' }) === 'real'`.
 * La etiqueta que llega al usuario la sigue calculando el TS — aquí solo se filtra y se ordena — y
 * un test de paridad en `test/catalog.e2e.spec.ts` compara ambos lados sobre el mismo catálogo para
 * que no se separen sin que nadie se entere. Mismo trato que `database/schema.ts` frente a
 * `db/migrations`: espejo declarado, con una prueba que lo vigila.
 *
 * Si tocas `deal-rule.ts`, toca este fichero en el mismo commit.
 */

import { sql, type SQL } from 'drizzle-orm';

/**
 * Columnas (o expresiones) de la variante "mejor oferta" contra las que se evalúa la regla. Son las
 * mismas que `catalog.service.ts` pasa a `classifyHonesty`.
 */
export interface DealSqlColumns {
  /** Último precio observado. */
  price: SQL;
  /** Precio tachado que declara la tienda; puede faltar y puede estar inflado. */
  listPrice: SQL;
  /** Mínimo de las observaciones anteriores dentro de `HONESTY_WINDOW_DAYS`. */
  recentMin: SQL;
  /** Máximo de las observaciones anteriores (todo el histórico de la variante). */
  maxObserved: SQL;
  /** Cuántas observaciones anteriores hay. 0 = arranque en frío. */
  priorPoints: SQL;
}

/**
 * PVP creíble — espejo de `honestListPrice()` (`deal-rule.ts:64-77`), incluido su
 * `INFLATED_LIST_MARGIN = 1.03`.
 *
 * `NULL` cuando no hay ninguno: sin histórico no se cae de vuelta al precio tachado de la tienda.
 */
export function honestListPriceSql(listPrice: SQL, maxObserved: SQL): SQL {
  return sql`(CASE
    WHEN ${maxObserved} IS NULL THEN NULL
    WHEN ${listPrice} IS NULL THEN ${maxObserved}
    WHEN ${listPrice} > ${maxObserved} * 1.03 THEN ${maxObserved}
    ELSE ${listPrice}
  END)`;
}

/**
 * Booleano equivalente a `classifyHonesty(...) === 'real'`.
 *
 * Desglose de `evaluateDeal` con `minDiscountPct: 0` y `compareBase: 'recent_min'`:
 *  - `prior_points > 0`                → si no, `'sin-historico'` (arranque en frío).
 *  - `price IS NOT NULL`               → si no, `'sin-rebaja'`.
 *  - PVP honesto no nulo               → si no, `'sin-historico'`.
 *  - `recent_min` no nulo y `price <`  → condición A: solo mínimos nuevos, no rebajas permanentes.
 *  - `honest > 0 AND honest > price`   → condición B: descuento > 0 contra el PVP creíble.
 */
export function isRealDealSql(c: DealSqlColumns): SQL {
  const honest = honestListPriceSql(c.listPrice, c.maxObserved);
  return sql`(
    COALESCE(${c.priorPoints}, 0) > 0
    AND ${c.price} IS NOT NULL
    AND ${c.recentMin} IS NOT NULL
    AND ${c.price} < ${c.recentMin}
    AND ${honest} IS NOT NULL
    AND ${honest} > 0
    AND ${honest} > ${c.price}
  )`;
}

/**
 * Descuento real en % contra el PVP creíble — espejo de `DealVerdict.discountPct`.
 *
 * Solo se usa para **ordenar**, nunca se devuelve al cliente: es el criterio honesto con el que
 * `sort=ofertas` desempata, en lugar del `discount_pct` que declara la tienda.
 */
export function honestDiscountSql(c: DealSqlColumns): SQL {
  const honest = honestListPriceSql(c.listPrice, c.maxObserved);
  return sql`(CASE
    WHEN ${honest} IS NOT NULL AND ${honest} > 0 AND ${honest} > ${c.price}
      THEN round((1 - ${c.price} / ${honest}) * 100, 2)
    ELSE 0
  END)`;
}
