import { describe, expect, it } from 'vitest';

import { buildDigestChunks, MAX_DIGEST_MESSAGES, TELEGRAM_MAX_CHARS } from './message';
import type { CandidateRow, Deal } from './matching.types';

/**
 * Oferta mínima: solo las piezas que mira el resumen.
 *
 * La talla va en las DOS formas y distintas a propósito (#223): `size` es el texto de la tienda y
 * `sizeCanon` la que calcula `size_canon` en la base. El resumen tiene que usar la segunda —es la
 * que ve el usuario en la web—, así que con una fixture donde coincidieran, ninguna de las
 * afirmaciones de este fichero podría distinguir cuál de las dos se está imprimiendo.
 */
function deal(over: Partial<CandidateRow> = {}): Deal {
  const row = {
    productName: 'PANTALÓN CULOTTE CUADRO DAMERO',
    productUrl: 'https://www.zara.com/es/pantalon-culotte-p123456.html',
    retailerName: 'Zara',
    size: '2 años (92 cm)',
    sizeCanon: '2 años',
    color: 'Rosa / Blanco',
    price: '6.38',
    ...over,
  } as CandidateRow;
  return {
    row,
    verdict: { notify: true, honestListPrice: 15.95, discountPct: 60 } as Deal['verdict'],
    priceEventKey: '1:6.38',
  };
}

/** Lote de n ofertas distinguibles por nombre. */
function lote(n: number): Deal[] {
  return Array.from({ length: n }, (_, i) => deal({ productName: `PRENDA ${i}` }));
}

/** Bullets detallados en todo el resumen: uno por oferta que sí se enumera. */
function detalladas(textos: string[]): number {
  return textos.join('\n').split('\n').filter((l) => l.startsWith('• ')).length;
}

describe('buildDigestChunks (#220)', () => {
  it('sin ofertas no hay mensaje', () => {
    expect(buildDigestChunks([])).toEqual([]);
  });

  it('una sola oferta: un mensaje, con la cabecera en singular y sin numerar', () => {
    const [chunk, ...resto] = buildDigestChunks([deal()]);

    expect(resto).toHaveLength(0);
    expect(chunk.text).toBe(
      '🎉 <b>Ha bajado de precio una prenda que sigues</b>\n' +
        '\n' +
        '• <a href="https://www.zara.com/es/pantalon-culotte-p123456.html">PANTALÓN CULOTTE CUADRO DAMERO</a> — Zara\n' +
        '  Talla 2 años · Rosa / Blanco\n' +
        '  <b>6,38 €</b> (antes 15,95 €) · <b>-60%</b>',
    );
  });

  it('nombra la variante con la talla CANÓNICA, no con la de la tienda (#223)', () => {
    // El aviso y la web tienen que decir la misma talla: la web la pinta por `size_canon`, así que
    // el sufijo de unidad que publica la tienda no puede llegar al mensaje. Es la mitad de #223 que
    // no se ve por la API, porque solo aparece en el Telegram de un usuario real.
    const [chunk] = buildDigestChunks([deal({ size: '5-6 años (116 cm)', sizeCanon: '5-6 años' })]);

    expect(chunk.text).toContain('  Talla 5-6 años · Rosa / Blanco\n');
    expect(chunk.text).not.toContain('116 cm');
  });

  it('un lote pequeño sigue siendo un único mensaje sin numerar', () => {
    const chunks = buildDigestChunks(lote(5));

    expect(chunks).toHaveLength(1);
    expect(chunks[0].text).toContain('🎉 <b>Han bajado de precio 5 prendas que sigues</b>\n');
    expect(chunks[0].text).not.toContain('(1/');
    expect(chunks[0].deals).toHaveLength(5);
  });

  /** El caso medido en QA: 87 prendas eran 17 717 caracteres en un solo mensaje. */
  it('las 87 prendas de QA se trocean y ningún trozo pasa del límite', () => {
    const deals = lote(87);
    const chunks = buildDigestChunks(deals);

    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks) {
      expect(chunk.text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    }
    expect(chunks.flatMap((c) => c.deals)).toEqual(deals);
    expect(detalladas(chunks.map((c) => c.text))).toBe(87);
  });

  it('cada trozo va numerado y la cabecera cuenta el lote entero', () => {
    const chunks = buildDigestChunks(lote(87));

    chunks.forEach((chunk, i) => {
      expect(chunk.text.startsWith(
        `🎉 <b>Han bajado de precio 87 prendas que sigues</b> (${i + 1}/${chunks.length})\n`,
      )).toBe(true);
    });
  });

  it('nunca parte una oferta: sus líneas viajan juntas en el mismo trozo', () => {
    const chunks = buildDigestChunks(lote(87));

    for (const chunk of chunks) {
      const lineas = chunk.text.split('\n');
      // Cada bullet arrastra sus dos líneas sangradas; si un trozo se cortara por la mitad, la
      // primera línea del siguiente no sería ni cabecera ni bullet.
      const bullets = lineas.filter((l) => l.startsWith('• ')).length;
      const sangradas = lineas.filter((l) => l.startsWith('  ')).length;
      expect(sangradas).toBe(bullets * 2);
    }
  });

  it('por encima del tope resume el resto en la cola del último mensaje', () => {
    const deals = lote(300);
    const chunks = buildDigestChunks(deals);

    expect(chunks).toHaveLength(MAX_DIGEST_MESSAGES);
    for (const chunk of chunks) {
      expect(chunk.text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    }

    const enumeradas = detalladas(chunks.map((c) => c.text));
    expect(enumeradas).toBeLessThan(300);
    const ultimo = chunks[chunks.length - 1];
    expect(ultimo.text).toContain(`… y ${300 - enumeradas} prendas más. Puedes verlas todas en la web.`);

    // Las sobrantes NO se pierden: cuelgan del último trozo, así que conservan su fila en
    // `notification` y no se vuelven a evaluar cuando la marca de agua avance.
    expect(chunks.flatMap((c) => c.deals)).toEqual(deals);
  });

  it('la cola va en singular cuando solo sobra una', () => {
    // Ofertas con todos los campos al máximo: caben 6 por mensaje, o sea 60 en el tope de 10, y
    // la 61 es la única que sobra.
    const deals = Array.from({ length: 61 }, (_, i) =>
      deal({
        productName: `PRENDA ${i} `.padEnd(120, 'X'),
        productUrl: `https://tienda.example.com/${i}/`.padEnd(300, 'q'),
        // La CANÓNICA, que es la que se imprime y por tanto la que ocupa sitio en el mensaje.
        sizeCanon: 'talla '.padEnd(60, 'y'),
        color: 'color '.padEnd(50, 'z'),
      }),
    );
    const chunks = buildDigestChunks(deals);

    expect(chunks).toHaveLength(MAX_DIGEST_MESSAGES);
    expect(detalladas(chunks.map((c) => c.text))).toBe(60);
    expect(chunks[chunks.length - 1].text).toContain('… y 1 prenda más.');
  });

  it('un nombre de producto desmesurado no desborda el mensaje', () => {
    const chunks = buildDigestChunks([deal({ productName: 'A'.repeat(10_000) })]);

    expect(chunks).toHaveLength(1);
    expect(chunks[0].text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    expect(chunks[0].text).toContain('…');
  });

  it('una URL desmesurada se emite sin enlace en vez de desbordar', () => {
    const chunks = buildDigestChunks([deal({ productUrl: `https://t/${'q'.repeat(10_000)}` })]);

    expect(chunks).toHaveLength(1);
    expect(chunks[0].text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    expect(chunks[0].text).not.toContain('<a href=');
    expect(chunks[0].text).toContain('• PANTALÓN CULOTTE CUADRO DAMERO — Zara');
  });

  it('el escape de HTML nunca queda partido a mitad de entidad', () => {
    const deals = lote(60).map((_, i) => deal({ productName: `M&M's <${i}> & más & más` }));
    const chunks = buildDigestChunks(deals);

    for (const chunk of chunks) {
      // Ningún `&` suelto: si un trozo cortara dentro de `&amp;` quedaría uno sin cerrar.
      expect(chunk.text).not.toMatch(/&(?!amp;|lt;|gt;)/);
      expect(chunk.text.length).toBeLessThanOrEqual(TELEGRAM_MAX_CHARS);
    }
  });

  it('una oferta sin talla ni color se resume igual, con una línea menos', () => {
    const [chunk] = buildDigestChunks([deal({ size: null, sizeCanon: null, color: null })]);

    expect(chunk.text.split('\n')).toHaveLength(4);
    expect(chunk.text).not.toContain('Talla');
  });
});
