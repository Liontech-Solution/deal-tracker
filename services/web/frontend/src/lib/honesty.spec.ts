import { describe, expect, it } from 'vitest';

import type { Honesty } from '../api/types';
import type { TonoDescuento } from './honesty';
import {
  cifrasDeRebaja,
  llevaBadge,
  textoDeLaCaja,
  tonoDeLaCaja,
  tonoDelDescuento,
  tonoDelPrecio,
} from './honesty';

/**
 * Qué cifras pinta la SPA y cuándo se le permite pintarlas en verde (#436).
 *
 * Los casos no son inventados: son los tres que produce `honestListPrice()` del backend, y el
 * segundo es el que la tarjeta estaba pintando mal en 88 de los 246 productos con badge de QA.
 */
describe('cifrasDeRebaja', () => {
  it('pinta lo que declara la tienda cuando su tachado ES el PVP creíble', () => {
    const r = cifrasDeRebaja({
      listPrice: '20.00',
      discountPct: '25.00',
      honestListPrice: '20.00',
      honestDiscountPct: 25,
    });
    expect(r).toEqual({ tachado: '20.00', descuento: 25, sostenido: true });
  });

  it('sustituye el tachado inflado por el creíble, y el % por el que sostiene la regla', () => {
    // El producto 10834 de Springfield, medido en QA el 16/08/2026: la tarjeta enseñaba 53,00 € y
    // -50 % cuando el máximo observado eran 31,80 € y el descuento sostenible un 16,7 %.
    const r = cifrasDeRebaja({
      listPrice: '53.00',
      discountPct: '50.00',
      honestListPrice: '31.80',
      honestDiscountPct: 16.67,
    });
    expect(r.tachado).toBe('31.80');
    expect(r.descuento).toBe(17);
    expect(r.sostenido).toBe(true);
  });

  it('sin PVP creíble enseña lo de la tienda pero NO lo avala', () => {
    // Arranque en frío: no hemos visto nunca la prenda a otro precio. El tachado se sigue
    // enseñando —el usuario lo ve igual en la web de la tienda— pero sin nuestro verde detrás.
    const r = cifrasDeRebaja({
      listPrice: '60.00',
      discountPct: '50.00',
      honestListPrice: null,
      honestDiscountPct: 0,
    });
    expect(r).toEqual({ tachado: '60.00', descuento: 50, sostenido: false });
  });

  it('no inventa porcentaje cuando el PVP creíble no sostiene ninguno', () => {
    // El techo del mínimo declarado (#354) puede dejar el PVP creíble en el precio actual o por
    // debajo: ahí no hay rebaja que pintar, aunque la tienda anuncie una.
    const r = cifrasDeRebaja({
      listPrice: '15.99',
      discountPct: '75.00',
      honestListPrice: '3.99',
      honestDiscountPct: 0,
    });
    expect(r.tachado).toBe('3.99');
    expect(r.descuento).toBeNull();
  });

  it('sin tachado declarado no hay nada que sustituir', () => {
    const r = cifrasDeRebaja({
      listPrice: null,
      discountPct: null,
      honestListPrice: '20.00',
      honestDiscountPct: 0,
    });
    expect(r).toEqual({ tachado: null, descuento: null, sostenido: true });
  });
});

/**
 * De qué color sale el `-X %`, que es donde vivía la divergencia de #473.
 *
 * La tabla se recorre entera y a propósito: la condición estaba duplicada en la tarjeta y en la
 * ficha, y de las diez combinaciones las dos superficies discrepaban en tres. Un test por caso
 * suelto no habría cazado ninguna de las dos que nadie estaba buscando.
 */
describe('tonoDelDescuento', () => {
  const CASOS: [Honesty, boolean, ReturnType<typeof tonoDelDescuento>][] = [
    // El único verde del catálogo: bajada con cobertura y con PVP creíble detrás.
    ['real', true, 'good'],
    ['real', false, 'neutro'],
    // La acusación no depende de tener PVP creíble propio: la vía declarada de #354 acusa en la
    // primera pasada, cuando `sostenido` es todavía falso.
    ['suspicious', true, 'warn'],
    ['suspicious', false, 'warn'],
    // Las dos formas de «no lo podemos sostener», una por el lado del elogio y otra por el de la
    // acusación (#436 y #332). Las dos en neutro, aunque `reciente` sea una buena noticia.
    ['reciente', true, 'neutro'],
    ['reciente', false, 'neutro'],
    ['unverified', true, 'neutro'],
    ['unverified', false, 'neutro'],
    ['none', true, 'neutro'],
    ['none', false, 'neutro'],
  ];

  it.each(CASOS)('%s con sostenido=%s se pinta %s', (honesty, sostenido, esperado) => {
    expect(tonoDelDescuento(honesty, sostenido)).toBe(esperado);
  });

  it('el verde es SOLO de `real`, y `sostenido` no basta para ganarlo (#473)', () => {
    // La regresión exacta: `sostenido` es cierto en toda bajada, así que decidir el verde con él
    // pintaba de verde a `reciente` —553 de los 800 productos de QA— y a un `unverified` cuyo
    // tachado no habíamos podido ni confirmar ni desmentir (otros 228).
    const verdes = (['real', 'reciente', 'suspicious', 'unverified', 'none'] as Honesty[]).filter(
      (h) => tonoDelDescuento(h, true) === 'good',
    );
    expect(verdes).toEqual(['real']);
  });

  it('un `suspicious` sin PVP creíble sigue en ámbar, no en gris (#354)', () => {
    // La divergencia que iba al revés: la ficha resolvía `!sostenido` antes que la acusación y le
    // pintaba el porcentaje en gris debajo de su propio badge «Precio inflado».
    expect(tonoDelDescuento('suspicious', false)).toBe('warn');
  });
});

/**
 * De qué color va la caja explicativa de la ficha (#489).
 *
 * Esto no existía, y ese era el fallo: la caja decidía su tono con un `const neutro` propio dentro
 * de `PriceBlock.tsx` que **coincidía** con `tonoDelDescuento()` de casualidad. Nada comparaba las
 * dos, igual que nada comparaba las dos superficies antes de #473 — y aquellas tres divergencias
 * tampoco daban un test rojo.
 */
describe('tonoDeLaCaja', () => {
  // Un `Record` y no un array de tuplas **a propósito**: con una lista escrita a mano, un veredicto
  // nuevo no entra sola en los casos y el test de paridad de aquí abajo seguiría verde por omisión
  // justo cuando las dos funciones ya discrepan. El `Record` sobre el tipo obliga al compilador.
  const ESPERADO: Record<Exclude<Honesty, 'none'>, ReturnType<typeof tonoDeLaCaja>> = {
    real: 'good',
    suspicious: 'warn',
    reciente: 'neutro',
    unverified: 'neutro',
  };
  const CASOS = Object.entries(ESPERADO) as [Exclude<Honesty, 'none'>, TonoDescuento][];

  it.each(CASOS)('%s pinta la caja en %s', (honesty, esperado) => {
    expect(tonoDeLaCaja(honesty)).toBe(esperado);
  });

  it('coincide con el porcentaje en todo lo que la base puede producir hoy (#489)', () => {
    // ESTE es el test que faltaba. La coincidencia se venía dando por supuesta sin que nada la
    // comprobara: si mañana se mueve una de las dos condiciones y la otra no, la ficha vuelve a
    // afirmar una cosa con el color de la caja y otra con el del porcentaje que tiene encima.
    // `sostenido` va a `true` porque es lo que produce la base en los cuatro casos: una bajada
    // siempre lo tiene, y `real` implica PVP creíble.
    for (const [honesty] of CASOS) {
      expect(tonoDeLaCaja(honesty)).toBe(tonoDelDescuento(honesty, true));
    }
  });

  it('y NO coincide en el único caso que las separa, que es por qué son dos funciones', () => {
    // Un `real` sin PVP creíble. Hoy es inalcanzable —`real` implica `honestListPrice` no nulo— y
    // por eso esto no es un fallo vivo, pero es la razón de que la caja no dependa de `sostenido`:
    // habla de lo que sabemos de la prenda, no de una cifra que haya que avalar.
    expect(tonoDeLaCaja('real')).toBe('good');
    expect(tonoDelDescuento('real', false)).toBe('neutro');
  });
});

describe('tonoDelPrecio', () => {
  it('el acento es el color por defecto y solo lo pierde la acusación', () => {
    // No es una afirmación, es el color de un precio: `none` —donde no decimos nada— lo lleva. Por
    // eso la ficha apagándoselo solo a `unverified` no distinguía nada, y la tarjeta no lo hacía.
    expect(tonoDelPrecio('suspicious')).toBe('plano');
    for (const h of ['real', 'reciente', 'unverified', 'none'] as Honesty[]) {
      expect(tonoDelPrecio(h)).toBe('accent');
    }
  });
});

describe('llevaBadge', () => {
  it('solo los veredictos que afirman algo llevan badge', () => {
    expect(llevaBadge('real')).toBe(true);
    expect(llevaBadge('reciente')).toBe(true);
    expect(llevaBadge('suspicious')).toBe(true);
    // `unverified` es ausencia de prueba, y `none` que no hay nada que decir (#332).
    expect(llevaBadge('unverified')).toBe(false);
    expect(llevaBadge('none')).toBe(false);
  });
});

/**
 * Qué se le PERMITE afirmar al texto de la ficha (#517).
 *
 * Esto tampoco existía, y su ausencia es la mitad de por qué la frase falsa llegó a producción:
 * el texto vivía dentro del JSX de `PriceBlock.tsx`, que no tiene un solo test de componente, así
 * que se podía escribir cualquier cosa sin poner nada en rojo.
 *
 * Lo que se fija aquí es la **propiedad, no la redacción**. Un test que comparase la cadena entera
 * solo diría que el texto es el que alguien escribió, que es justo lo que ya se creía de la frase
 * anterior; lo que hay que impedir es que vuelva a afirmar algo que el veredicto desmiente.
 */
describe('textoDeLaCaja', () => {
  const DATOS = { trackedDays: 12, claimDays: 12, honestyBasis: null, min30: null };

  // Mismo `Record` sobre el tipo y por el mismo motivo que en `tonoDeLaCaja`: un veredicto nuevo
  // tiene que entrar solo en los casos, o el test se queda verde por omisión justo cuando aparece
  // el texto que nadie ha revisado.
  const TEXTOS: Record<Exclude<Honesty, 'none'>, string> = {
    real: textoDeLaCaja('real', DATOS),
    reciente: textoDeLaCaja('reciente', DATOS),
    suspicious: textoDeLaCaja('suspicious', DATOS),
    unverified: textoDeLaCaja('unverified', DATOS),
  };

  it('un `reciente` NUNCA dice que el precio pueda ser «su precio de siempre»', () => {
    // El fallo exacto de #517, clavado. A `reciente` solo se llega porque `evaluateDeal` con
    // `compareBase: 'recent_min'` dio `notify`, y eso exige `price < recentMin` con un punto
    // previo: existe SIEMPRE una observación anterior más cara, y la ficha la dibuja encima.
    // Afirmar que quizá sea su precio de siempre es falso en el 100 % de los casos.
    expect(TEXTOS.reciente).not.toMatch(/precio de siempre/i);
  });

  it('un `reciente` afirma la bajada en vez de ponerla en duda', () => {
    // La otra mitad: no basta con quitar la mentira, el texto tiene que decir lo que sí sabemos.
    expect(TEXTOS.reciente).toMatch(/ha bajado/i);
  });

  it('ningún texto pone en duda la bajada de un veredicto que la afirma', () => {
    // `real` y `reciente` son los dos que se alcanzan por la rama de `notify`, o sea los dos que
    // tienen una observación previa más cara garantizada. Ninguno puede dudar de que haya bajado.
    for (const honesty of ['real', 'reciente'] as const) {
      expect(TEXTOS[honesty]).not.toMatch(/precio de siempre|precio habitual|no sabemos si ha bajado/i);
    }
  });

  it('ningún texto niega haber visto la prenda fuera de la bajada', () => {
    // #530, y la razón de que este test exista aparte del de arriba: aquel enumeraba las tres
    // redacciones falsas que ya se conocían, y la de #530 —«porque aún no la hemos visto fuera de
    // esta bajada»— no encajaba en ninguna. **Pasó en verde.** Es la misma caducidad por enumerar
    // que se llevó por delante el caso U26e de `/validar-qa` (#532), un piso más abajo.
    //
    // Lo que se fija aquí no es una frase sino la forma del error: una negación y un «visto» en la
    // misma oración. `real` y `reciente` se alcanzan por `notify`, que exige `price < recentMin`
    // con un punto anterior, así que **existe siempre** una observación previa más cara y negarla
    // es falso en el 100 % de los casos (medido: 120 de 120 variantes `reciente` de QA el
    // 20/08/2026 tienen al menos un punto por encima del precio actual).
    //
    // No pretende ser exhaustivo —un texto puede negar la observación previa sin usar el verbo
    // «ver»— y por eso la red de verdad es U26e, que traduce cada oración del párrafo desplegado a
    // una comprobación sobre la serie. Este test es el que impide que vuelva a entrar la forma que
    // YA ha entrado dos veces.
    for (const honesty of ['real', 'reciente'] as const) {
      expect(TEXTOS[honesty]).not.toMatch(/\b(no|nunca|jamás)\b[^.]{0,40}\bvist[oa]\b/i);
    }
  });

  it('las afirmaciones de mínimo citan `claimDays`, no `trackedDays`', () => {
    // La segunda mitad de #517, y la que hoy NO se puede ver en QA ni en prod: `recent_min` lleva
    // el techo de la ventana de honestidad y `trackedDays` no, así que una prenda más vieja que la
    // ventana haría que «lo más barato en N días» citara un tramo que el mínimo no cubre. Con las
    // series de hoy (26 días en QA, 12 en prod) los dos números coinciden y esto es inobservable;
    // por eso se fija aquí, con los dos deliberadamente distintos.
    const viejo = { trackedDays: 200, claimDays: 90, honestyBasis: null, min30: null };
    for (const honesty of ['real', 'reciente'] as const) {
      const texto = textoDeLaCaja(honesty, viejo);
      expect(texto).toContain('90 días');
      expect(texto).not.toContain('200 días');
    }
  });

  it('`unverified` sí cita la cobertura, porque habla de cuánto llevamos mirando', () => {
    // La excepción, y es deliberada: esa frase no afirma ningún mínimo, dice cuánto hace que la
    // seguimos. Ahí el número que toca es `trackedDays`, y confundirlos sería el defecto simétrico.
    const viejo = { trackedDays: 200, claimDays: 90, honestyBasis: null, min30: null };
    expect(textoDeLaCaja('unverified', viejo)).toContain('200 días');
  });

  it('`unverified` recién descubierta no dice «llevamos 0 días»', () => {
    expect(textoDeLaCaja('unverified', { ...DATOS, trackedDays: 0 })).toContain(
      'acabamos de empezar',
    );
  });

  it('una acusación `declarado` cita la cifra que la hace comprobable (#354)', () => {
    const texto = textoDeLaCaja('suspicious', {
      ...DATOS,
      honestyBasis: 'declarado',
      min30: '4,24 €',
    });
    expect(texto).toContain('4,24 €');
    // Y no dice «respecto a su historial», que sobre una prenda recién descubierta sería falso.
    expect(texto).not.toMatch(/historial/i);
  });

  it('una acusación sin cifra declarada cae al texto de histórico y no enseña un hueco', () => {
    // La vía declarada no puede dispararse sin `min30`, pero si faltara el texto no puede quedar
    // con un agujero donde iba el número.
    const texto = textoDeLaCaja('suspicious', { ...DATOS, honestyBasis: 'declarado', min30: null });
    expect(texto).toMatch(/historial/i);
    expect(texto).not.toContain('null');
  });

  it('declina el plural en singular', () => {
    expect(textoDeLaCaja('real', { ...DATOS, claimDays: 1 })).toContain('1 día ');
  });
});
