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
 * `reciente` SÍ lleva, y con eso se estrenó la guarda (#436): es una afirmación que sí podemos
 * sostener —la prenda ha bajado— con un rótulo que no dice ni «real» ni «honesta», porque eso es lo
 * que no sabemos todavía. Es la simétrica de `unverified`: allí callamos una acusación, aquí
 * rebajamos un elogio.
 *
 * Es una guarda de tipo para que `HonestyBadge` siga aceptando solo los veredictos que sabe pintar:
 * así, si mañana aparece uno nuevo, el compilador obliga a decidir de qué lado cae en vez de
 * dejarlo colarse en el badge.
 */
export function llevaBadge(honesty: Honesty): honesty is 'real' | 'reciente' | 'suspicious' {
  return honesty === 'real' || honesty === 'reciente' || honesty === 'suspicious';
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
/** Los tres tonos con los que se puede pintar un porcentaje de descuento. */
export type TonoDescuento = 'good' | 'warn' | 'neutro';

/**
 * De qué color se pinta el `-X %`, para la tarjeta y para la ficha (#473).
 *
 * **El color es la afirmación**, y esta es la única condición que la decide. Vivía duplicada en las
 * dos superficies y llevaba **tres** divergencias, dos de ellas invisibles hasta que se leyeron los
 * dos ficheros a la vez (medido en QA el 17/08/2026 sobre 800 productos):
 *
 * | veredicto | tarjeta | ficha | y ahora |
 * |---|---|---|---|
 * | `real` (7) | verde | verde | **verde** |
 * | `reciente` (553) | verde | verde | **neutro** |
 * | `unverified` (228) | verde | neutro | **neutro** |
 * | `suspicious` sin PVP creíble | ámbar | gris | **ámbar** |
 *
 * La última fila no lleva cifra porque no está medida aparte: lo que se midió en QA el 14/08/2026 son
 * los 89 productos acusables (291 variantes) y que **un tercio se acusa en la primera pasada**, que
 * es exactamente cuando no hay PVP creíble. O sea del orden de treinta, no 89.
 *
 * La de `unverified` es #473: la tarjeta celebraba en verde un descuento que la ficha del mismo
 * producto declaraba sin confirmar. La de `reciente` no la vio ninguna issue —`ProductCard`
 * *afirmaba en un comentario* que el verde se le retiraba, y no se le retiraba— y es la mayoritaria:
 * `sostenido` es siempre cierto cuando hay bajada, así que no puede ser lo único que decida el verde.
 * La de `suspicious` va al revés y nace del orden de los ternarios: la ficha resolvía `!sostenido`
 * **antes** que la acusación, así que a un «Precio inflado» de la primera pasada (la vía declarada
 * de #354, la de C&A y Springfield) le pintaba el porcentaje en gris justo debajo de su propio badge
 * ámbar. Acusar con el rótulo y desdecirse con el color es peor que no acusar.
 *
 * La regla, en una línea: **el verde es solo para `real`**. Es lo que #332 hizo con la acusación y
 * #436 con el elogio, aplicado a lo que quedaba suelto — el color. `reciente` y `unverified` son las
 * dos formas de «no lo podemos sostener», y las dos van en neutro aunque una sea buena noticia.
 */
/**
 * Si el precio grande va con el acento de la marca o apagado, para las mismas dos superficies.
 *
 * Está aquí por lo mismo que `tonoDelDescuento()` y no porque hoy discrepe: la condición era
 * `honesty === 'suspicious'` **escrita a mano en cada componente**, que es la forma exacta que tenían
 * las tres divergencias de #473 antes de ser divergencias. Hasta este cambio la ficha se lo quitaba
 * además a `unverified` y la tarjeta no.
 *
 * El acento es el color **por defecto** de un precio, no una afirmación: `none` —donde no decimos
 * nada de nada— lo lleva en las dos superficies, así que apagarlo en un caso que tampoco afirma nada
 * no distingue nada. Lo pierde solo la acusación, que es la única que quiere que el precio no se lea
 * como buena noticia.
 */
export function tonoDelPrecio(honesty: Honesty): 'accent' | 'plano' {
  return honesty === 'suspicious' ? 'plano' : 'accent';
}

export function tonoDelDescuento(honesty: Honesty, sostenido: boolean): TonoDescuento {
  // La acusación manda sobre todo lo demás, con PVP creíble o sin él: si el badge dice «Precio
  // inflado», el porcentaje que lo acompaña no puede pintarse como si no dijéramos nada.
  if (honesty === 'suspicious') return 'warn';
  // El `&& sostenido` no es redundante aunque hoy `real` lo implique (una bajada real necesita un
  // PVP creíble contra el que medirla): es la guarda que impide que un veredicto futuro se lleve el
  // verde sin una referencia que lo sostenga.
  return honesty === 'real' && sostenido ? 'good' : 'neutro';
}

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
