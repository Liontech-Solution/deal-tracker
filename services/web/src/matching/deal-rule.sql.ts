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
 * dos tests lo vigilan: el de paridad extremo a extremo de `test/catalog.e2e.spec.ts` y el de
 * propiedades de `test/deal-rule-paridad.spec.ts`, que compara los dos lados fila a fila sobre
 * histórico generado en vez de sobre cuatro productos sembrados a mano (#228). Mismo trato que
 * `database/schema.ts` frente a `db/migrations`: espejo declarado, con una prueba que lo vigila.
 *
 * **El umbral de evidencia de #332 no vive aquí, y no es un olvido.** Solo condiciona el veredicto
 * `suspicious`, que este fichero no calcula: aquí únicamente se decide `real` (para `onlyDeals` y
 * `sort=ofertas`) y el descuento honesto con el que se ordena. Y `real` no puede verse afectado por
 * ese umbral, porque ya implica `max_observed > price`: si el máximo observado no supera al precio
 * actual, tampoco lo supera `recent_min` —que es un mínimo sobre las mismas observaciones— y la
 * condición A cae antes. `deal-rule-paridad.spec.ts` fija esa implicación como invariante, para que
 * el día que alguien mueva `real` se entere de que arrastra la acusación.
 *
 * **Y el veredicto no es lo único que hay que comparar.** `honestDiscountSql` alimenta el `ORDER BY`
 * de `sort=ofertas` sobre TODAS las filas, no solo las `real`, así que su espejo es
 * `honestDiscountPct()` y no `DealVerdict.discountPct` —que se pone a 0 en cuanto falla la
 * condición A, condición que el orden no aplica—. Mirar solo `real` dejaba pasar una desalineación
 * del margen entera: no puede moverlo sobre ninguna fila que la base pueda producir (#375).
 *
 * **El mínimo declarado de 30 días sí vive aquí, y esa es la diferencia con el umbral de arriba**
 * (#354). Tiene dos mitades y solo una es de este fichero: la *vía de acusación* condiciona
 * `suspicious` y se queda en el TS, pero el *techo del PVP creíble* entra en `honestListPriceSql` y
 * por tanto en `real` y en el orden de «Ofertas». La dirección es lo que lo hace seguro: el techo
 * solo puede **bajar** el PVP, así que solo puede quitar una oferta real, nunca inventarla.
 *
 * Ojo a los dos umbrales, que no son el mismo y conviene no leerlos como si lo fueran: el techo
 * retira `real` en cuanto `min30 <= price`, mientras que la acusación exige el margen
 * (`price > min30 · 1,03`). Entre los dos queda una banda estrecha —`min30 <= price <= min30·1,03`—
 * donde la oferta deja de ser real y **no** se acusa a nadie: cae a `unverified`. Es el
 * comportamiento que se quiere, no un hueco — dentro del margen no afirmamos nada— pero significa
 * que quitar `real` y etiquetar `suspicious` no son equivalentes, solo van en la misma dirección.
 *
 * Si tocas `deal-rule.ts`, toca este fichero en el mismo commit.
 */

import { sql, type SQL } from 'drizzle-orm';

import { INFLATED_LIST_MARGIN } from './deal-rule';

/**
 * El margen del PVP inflado, como **literal SQL** y no como parámetro ligado.
 *
 * `sql.raw` y no `sql`${...}`` a propósito: un número de JavaScript ligado viaja como `float8`, y
 * `numeric * float8` no redondea igual que `numeric * numeric`. El literal preserva la aritmética
 * exacta que la comparación tenía cuando el 1.03 estaba escrito a mano aquí.
 */
const MARGEN = sql.raw(String(INFLATED_LIST_MARGIN));

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
  /**
   * Mínimo de 30 días que declara la tienda (`retailer_min_30d`), o `NULL` en las siete tiendas que
   * no lo publican. Entra como techo del PVP creíble (#354).
   */
  retailerMin30d: SQL;
}

/**
 * PVP creíble — espejo de `honestListPrice()` (`deal-rule.ts`), con el mismo
 * `INFLATED_LIST_MARGIN`, que ya no se repite aquí sino que se importa.
 *
 * `NULL` cuando no hay ninguno: sin histórico no se cae de vuelta al precio tachado de la tienda.
 */
export function honestListPriceSql(listPrice: SQL, maxObserved: SQL, retailerMin30d: SQL): SQL {
  const base = sql`(CASE
    WHEN ${maxObserved} IS NULL THEN NULL
    WHEN ${listPrice} IS NULL THEN ${maxObserved}
    WHEN ${listPrice} > ${maxObserved} * ${MARGEN} THEN ${maxObserved}
    ELSE ${listPrice}
  END)`;
  // El tercer parámetro es OBLIGATORIO a propósito, aunque la columna sea NULL en siete de las nueve
  // tiendas: un argumento opcional aquí es exactamente el vector de deriva que este fichero existe
  // para cerrar — quien lo olvidara se llevaría la regla vieja, compilando y sin decir nada.
  //
  // `LEAST` ignora los NULL, que es lo que hace falta cuando la tienda no publica el mínimo. Ojo,
  // no es intercambiable con un `LEAST` a secas: si `base` fuese NULL (arranque en frío) `LEAST`
  // devolvería el mínimo declarado y **crearía** una referencia donde el TS devuelve `null`.
  return sql`(CASE WHEN ${base} IS NULL THEN NULL ELSE LEAST(${base}, ${retailerMin30d}) END)`;
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
  const honest = honestListPriceSql(c.listPrice, c.maxObserved, c.retailerMin30d);
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
  const honest = honestListPriceSql(c.listPrice, c.maxObserved, c.retailerMin30d);
  return sql`(CASE
    WHEN ${honest} IS NOT NULL AND ${honest} > 0 AND ${honest} > ${c.price}
      THEN round((1 - ${c.price} / ${honest}) * 100, 2)
    ELSE 0
  END)`;
}
