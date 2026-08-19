import type { Honesty, HonestyBasis } from '../api/types';
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
export function tonoDelDescuento(honesty: Honesty, sostenido: boolean): TonoDescuento {
  switch (honesty) {
    // La acusación manda sobre todo lo demás, con PVP creíble o sin él: si el badge dice «Precio
    // inflado», el porcentaje que lo acompaña no puede pintarse como si no dijéramos nada.
    case 'suspicious':
      return 'warn';
    // El `sostenido` no es redundante aunque hoy `real` lo implique (una bajada real necesita un
    // PVP creíble contra el que medirla): es la guarda que impide que un veredicto futuro se lleve
    // el verde sin una referencia que lo sostenga.
    case 'real':
      return sostenido ? 'good' : 'neutro';
    case 'reciente':
    case 'unverified':
    case 'none':
      return 'neutro';
  }
}

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

/**
 * De qué color va la **caja explicativa** de la ficha: fondo, borde, icono y texto (#489).
 *
 * Es el último sitio donde la condición del color vivía fuera de aquí. `PriceBlock.tsx` mantenía su
 * propio `const neutro = unverified || reciente` y con él pintaba las cuatro cosas, que es
 * exactamente la forma que tenían las tres divergencias de #473 antes de serlo. **No había ninguna
 * prenda mal pintada** —el reparto coincidía con `tonoDelDescuento()` en los cuatro veredictos que
 * la caja llega a pintar— pero la coincidencia era accidental y no la vigilaba nada: ni un test
 * comparaba las dos, y las tres divergencias de #473 tampoco daban un test rojo.
 *
 * **Por qué es hermana de `tonoDelDescuento()` y no la misma función**, que es la decisión que la
 * issue pedía escribir: las dos afirman cosas distintas sobre entradas distintas. `tonoDelDescuento`
 * colorea **una cifra**, así que depende de `sostenido` — no podemos avalar en verde un número que
 * no tenemos contra qué medir. La caja no habla de cifras sino de **lo que sabemos de la prenda**,
 * y eso no depende de que haya PVP creíble. Hoy la diferencia es inalcanzable (un `real` implica
 * `honestListPrice` no nulo, así que `sostenido` es cierto siempre que el veredicto sea `real`), y
 * ese único caso divergente —caja verde con porcentaje neutro— está fijado en el spec para que se
 * vea por qué son dos. Reutilizar una sola ataría la caja a una entrada que no le corresponde, que
 * es la clase de acoplamiento del que sale la siguiente divergencia.
 *
 * El `switch` sin `default` es deliberado, como la guarda de tipo de `llevaBadge()`: si mañana
 * aparece un veredicto nuevo, el compilador obliga a decidir de qué lado cae. Antes caía por
 * descarte en la rama verde, que es el peor sitio posible para que aterrice algo que nadie ha
 * mirado. **`tonoDelDescuento()` se reescribió igual en este mismo cambio, y no por simetría**:
 * hacer exhaustiva solo una de las dos es peor que no hacer ninguna, porque el `tsc` rojo se
 * arregla en la caja, la hermana sigue compilando devolviendo `neutro` en silencio y el resultado
 * es la ficha pintando la caja de un color con el `-X %` de otro justo encima — que es la tercera
 * divergencia de #473 reintroducida por la puerta de atrás.
 *
 * Y `none` está **fuera del tipo** en vez de tener rama propia porque la caja no se pinta ahí: el
 * llamante tiene que guardarlo, no recibir un color que no va a usar.
 */
export function tonoDeLaCaja(honesty: Exclude<Honesty, 'none'>): TonoDescuento {
  switch (honesty) {
    // La acusación manda, con PVP creíble o sin él: mismo criterio que el porcentaje (#354).
    case 'suspicious':
      return 'warn';
    // Lo único que podemos sostener, y por eso lo único verde (#436).
    case 'real':
      return 'good';
    // Las dos formas de «no lo podemos sostener»: `unverified` calla una acusación y `reciente`
    // rebaja un elogio. Una es mala noticia y la otra buena, y las dos van igual de neutras.
    case 'unverified':
    case 'reciente':
      return 'neutro';
  }
}

/** Lo que el texto de la caja necesita saber de la prenda para no afirmar de más. */
export interface DatosDelTexto {
  /** Días que llevamos observándola. Habla de COBERTURA: «llevamos N días siguiéndola». */
  trackedDays: number;
  /**
   * Tramo que una afirmación de MÍNIMO puede citar. Lo acota el backend con la ventana de
   * honestidad, que aquí no se conoce ni debe conocerse (#517). Habla de ALCANCE.
   */
  claimDays: number;
  /** En qué se apoya una acusación (#354). Solo se mira cuando el veredicto es `suspicious`. */
  honestyBasis: HonestyBasis | null;
  /** El mínimo de 30 días que declara la tienda, ya formateado, o `null` si no lo publica. */
  min30: string | null;
}

function dias(n: number): string {
  return `${n} ${n === 1 ? 'día' : 'días'}`;
}

/**
 * El texto que explica el veredicto en la ficha. Una función, y no cinco cadenas sueltas dentro
 * del JSX, y el porqué es #517.
 *
 * Ese texto vivía en un ternario anidado en `PriceBlock.tsx` y decía, para `reciente`, que
 * «todavía no podemos decir si es una rebaja de verdad **o su precio de siempre**». Eso es **falso
 * por construcción en el 100 % de los `reciente`**: a ese veredicto solo se llega porque
 * `evaluateDeal` con `compareBase: 'recent_min'` devolvió `notify`, y eso exige `price < recentMin`
 * con al menos un punto previo. O sea que **siempre** existe una observación anterior más cara —y
 * la ficha la está dibujando en la gráfica justo encima del párrafo—. Lo que de verdad no sabemos
 * no es si ha bajado, sino si esa bajada es **significativa**: con poca cobertura no podemos
 * afirmar que el precio anterior fuera el normal de la prenda y no un vaivén.
 *
 * Que nadie lo viera importa tanto como el defecto. `PriceBlock.tsx` no tiene un solo test de
 * componente, así que estas cadenas se podían reescribir sin poner nada en rojo; y ni el bloque A
 * de #479, ni `revisor-espejo-honestidad`, ni los casos U26–U26d podían cazarlo, porque todos
 * comprueban que las superficies **coincidan** entre sí y que el texto **cite** su cifra, y ninguno
 * que lo que afirma sea **verdad**. Es el patrón de #473 un piso más abajo: la fuente única
 * impecable y el resultado falso igual. Traerlo aquí es lo que lo pone bajo un test y bajo el
 * subagente.
 *
 * La distinción entre `claimDays` y `trackedDays` es la otra mitad de la issue, y no es cosmética:
 * `recent_min` lleva el techo de la ventana de honestidad y `trackedDays` no, así que una prenda
 * con más días que la ventana haría que «lo más barato que la hemos visto en N días» citara un
 * tramo que el mínimo no cubre. **Hoy los dos números coinciden en todas partes** —la serie va por
 * 26 días en QA y 12 en prod, medido el 19/08/2026—, así que esto se sostiene por construcción y
 * no por observación: el primer caso real llega hacia el ~05/11/2026 en prod.
 *
 * `none` queda fuera del tipo por lo mismo que en `tonoDeLaCaja()`: ahí no se pinta caja.
 */
export function textoDeLaCaja(honesty: Exclude<Honesty, 'none'>, datos: DatosDelTexto): string {
  switch (honesty) {
    // Ausencia de prueba, y se dice como tal: ni confirmamos ni desmentimos el tachado. La cifra
    // que toca aquí es la COBERTURA, porque la frase habla de lo poco que llevamos mirando.
    case 'unverified':
      return `Descuento sin confirmar: ${
        datos.trackedDays === 0
          ? 'acabamos de empezar a seguir esta prenda'
          : `llevamos ${dias(datos.trackedDays)} siguiéndola`
      } y su historial todavía no da para saber si el precio tachado es el que costaba de verdad.`;
    // La bajada SÍ la afirmamos —es lo que define el veredicto— y lo que se rebaja es el elogio.
    // Decir «o su precio de siempre» negaba la bajada que la gráfica de al lado dibuja (#517).
    case 'reciente':
      return (
        `Ha bajado de precio: es lo más barato que la hemos visto en los ${dias(datos.claimDays)} ` +
        'que llevamos siguiéndola. Todavía no podemos decir si es una rebaja de las buenas o un ' +
        'vaivén de precio, porque aún no la hemos visto fuera de esta bajada.'
      );
    // Una acusación tiene que ser comprobable, y por eso cita su base (#354). La `declarado` se
    // apoya en lo que la tienda publica de sí misma y no espera a que tengamos histórico.
    case 'suspicious':
      return datos.honestyBasis === 'declarado' && datos.min30 !== null
        ? `Descuento no real: la propia tienda declara haber vendido esta prenda a ${datos.min30} en los últimos 30 días, por debajo de lo que pides ahora. No ha bajado de verdad.`
        : 'Descuento no real: el precio tachado está inflado respecto a su historial. No ha bajado de verdad.';
    // El único elogio, y también acotado con `claimDays`: este texto arrastraba el mismo defecto
    // de ventana que `reciente`, y la issue solo señalaba al otro.
    case 'real':
      return (
        `Rebaja honesta: es el precio más bajo en los ${dias(datos.claimDays)} que llevamos ` +
        'siguiéndola. Buen momento para comprar.'
      );
  }
}
