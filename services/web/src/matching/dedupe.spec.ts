import { describe, expect, it } from 'vitest';

import { collapseSameGarment } from './dedupe';
import type { CandidateRow, Deal } from './matching.types';

/** Oferta mínima: solo las piezas que mira `collapseSameGarment`. */
function deal(over: Partial<CandidateRow>): Deal {
  const row = {
    interestId: 1,
    productId: 10,
    variantId: 100,
    price: '19.99',
    sizeCanon: '27',
    colorCanon: 'blanco',
    productUrl: 'https://tienda/ficha',
    ...over,
  } as CandidateRow;
  return { row, verdict: { notify: true } as Deal['verdict'], priceEventKey: '1:19.99' };
}

describe('collapseSameGarment (#108)', () => {
  it('dos SKU de la misma ficha, talla y color son una sola oferta', () => {
    const { kept, collapsed } = collapseSameGarment([
      deal({ variantId: 100 }),
      deal({ variantId: 101 }),
    ]);
    expect(kept.map((d) => d.row.variantId)).toEqual([100]);
    expect(collapsed).toBe(1);
  });

  it('la URL separa dos artículos distintos de la tienda (el caso de H&M)', () => {
    const { kept, collapsed } = collapseSameGarment([
      deal({ variantId: 100, productUrl: 'https://hm/1315153003.html' }),
      deal({ variantId: 101, productUrl: 'https://hm/1315153005.html' }),
    ]);
    expect(kept).toHaveLength(2);
    expect(collapsed).toBe(0);
  });

  it('gana el precio menor, y a igualdad de precio el id menor', () => {
    const barata = collapseSameGarment([
      deal({ variantId: 100, price: '24.99' }),
      deal({ variantId: 101, price: '19.99' }),
    ]);
    expect(barata.kept.map((d) => d.row.variantId)).toEqual([101]);

    const empate = collapseSameGarment([
      deal({ variantId: 101 }),
      deal({ variantId: 100 }),
      deal({ variantId: 102 }),
    ]);
    expect(empate.kept.map((d) => d.row.variantId)).toEqual([100]);
  });

  it('no mezcla intereses ni productos distintos', () => {
    const { kept } = collapseSameGarment([
      deal({ variantId: 100 }),
      deal({ variantId: 101, interestId: 2 }),
      deal({ variantId: 102, productId: 11 }),
    ]);
    expect(kept).toHaveLength(3);
  });

  it('separa talla y color distintos, y un NULL no absorbe a una cadena vacía', () => {
    const { kept } = collapseSameGarment([
      deal({ variantId: 100 }),
      deal({ variantId: 101, sizeCanon: '28' }),
      deal({ variantId: 102, colorCanon: 'negro' }),
      deal({ variantId: 103, colorCanon: null }),
      deal({ variantId: 104, colorCanon: '' }),
    ]);
    expect(kept).toHaveLength(5);
  });
});
