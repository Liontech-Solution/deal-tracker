import type { Honesty } from '../api/types';
import { discountInt, parseMoney } from './format';

/**
 * Qué veredictos de honestidad se pintan como badge.
 *
 * `unverified` **no lleva**, y es el fondo de #332: significa «la tienda enseña un tachado que no
 * podemos corroborar», o sea ausencia de prueba. Un badge ahí acusaría de lo que no sabemos —que
 * es justo lo que el catálogo hacía 15.928 veces en producción, apoyándose en una media de 2,3
 * días de observación—. `none` es que no hay nada que decir.
 *
 * Es una guarda de tipo para que `HonestyBadge` siga aceptando solo los dos veredictos que sabe
 * pintar: así, si mañana aparece un quinto veredicto, el compilador obliga a decidir de qué lado
 * cae en vez de dejarlo colarse en el badge.
 */
export function llevaBadge(honesty: Honesty): honesty is 'real' | 'suspicious' {
  return honesty === 'real' || honesty === 'suspicious';
}

/**
 * Qué tachado y qué porcentaje se pintan, y si el descuento está **sostenido** por la regla (#436).
 *
 * Vive aquí y no en cada componente porque la tarjeta y la ficha tienen que decir lo mismo de la
 * misma prenda: cuando esa decisión estaba duplicada, la tarjeta enseñaba el `-50 %` de la tienda y
 * la regla que la etiquetaba sostenía un `-16,7 %` (producto 10834 de Springfield, medido en QA el
 * 16/08/2026 — 88 productos con badge en el mismo caso, con 51,7 % pintado contra 24,4 % sostenido).
 *
 * Tres casos, y el tercero es el que más importa:
 *
 *  1. **Hay PVP creíble y coincide con el declarado** — se pinta lo de la tienda, como siempre.
 *  2. **Hay PVP creíble por debajo del declarado** (tachado inflado, o techo del mínimo de 30 días
 *     de #354) — se pinta **el creíble**. El tachado de la tienda no desaparece de la ficha, que lo
 *     sigue enseñando aparte como «PVP declarado»; lo que desaparece es que lo avalemos nosotros.
 *  3. **No hay PVP creíble** (arranque en frío: no hemos visto nunca esta prenda a otro precio) —
 *     se sigue enseñando lo que declara la tienda, porque ocultarlo sería esconder información que
 *     el usuario ve igualmente en la web de la tienda, pero **`sostenido` es `false`**: nada de
 *     verde. No sabemos si ese tachado es cierto, y afirmarlo con nuestro color es el elogio sin
 *     pruebas que #436 vino a quitar.
 */
export function cifrasDeRebaja(p: {
  listPrice: string | null;
  discountPct: string | null;
  honestListPrice: string | null;
  honestDiscountPct: number;
}): { tachado: string | null; descuento: number | null; sostenido: boolean } {
  const declarado = parseMoney(p.listPrice);
  const honesto = parseMoney(p.honestListPrice);

  // Sin PVP creíble no hay nada que nosotros podamos sostener (caso 3).
  if (honesto === null) {
    return { tachado: p.listPrice, descuento: discountInt(p.discountPct), sostenido: false };
  }
  // El creíble manda en cuanto es más bajo que el declarado (caso 2). El `<` es estricto a
  // propósito: si coinciden, el tachado de la tienda ya es el creíble y no hay nada que sustituir.
  if (declarado !== null && honesto < declarado) {
    return {
      tachado: honesto.toFixed(2),
      descuento: p.honestDiscountPct > 0 ? Math.round(p.honestDiscountPct) : null,
      sostenido: true,
    };
  }
  return { tachado: p.listPrice, descuento: discountInt(p.discountPct), sostenido: true };
}
