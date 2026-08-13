import { describe, expect, it } from 'vitest';

import {
  available,
  countAvailableSizes,
  distinctSizes,
  otherMeasures,
  sizeAvailable,
  sizeLabels,
} from './variants';
import type { Disponibilidad, MedidaHermana } from './variants';

function v(over: Partial<Disponibilidad> = {}): Disponibilidad {
  return { size: '30', delisted: false, inStock: true, ...over };
}

describe('available (#224)', () => {
  it('una variante a la venta está disponible', () => {
    expect(available(v())).toBe(true);
  });

  it('una variante descatalogada no lo está', () => {
    expect(available(v({ delisted: true }))).toBe(false);
  });

  it('una variante AGOTADA tampoco: es el caso que la ficha dibujaba como comprable', () => {
    expect(available(v({ inStock: false }))).toBe(false);
  });

  it('sin dato de stock (`null`) NO se da por agotada: desconocido no es lo mismo que agotado', () => {
    // Pasa cuando la variante no tiene ninguna fila en `price_history` y el LEFT JOIN del detalle
    // deja el `in_stock` a null. Tacharla sería afirmar algo que nadie ha medido.
    expect(available(v({ inStock: null }))).toBe(true);
  });
});

/**
 * El caso real de la validación de QA: `/producto/4597` (Lefties, "Zapatilla Barefoot Purpurina"),
 * 13 tallas de las que las 31 y 32 vienen `inStock: false` y ninguna descatalogada. La ficha las
 * pintaba clicables y rotulaba «13 disponibles».
 */
const FICHA_4597: Disponibilidad[] = [27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39].map((n) =>
  v({ size: String(n), inStock: n !== 31 && n !== 32 }),
);

describe('tallas de la ficha (#224)', () => {
  it('la talla agotada no cuenta como disponible', () => {
    expect(sizeAvailable(FICHA_4597, '30')).toBe(true);
    expect(sizeAvailable(FICHA_4597, '31')).toBe(false);
    expect(sizeAvailable(FICHA_4597, '32')).toBe(false);
  });

  it('el rótulo dice 11, no 13', () => {
    expect(distinctSizes(FICHA_4597)).toHaveLength(13);
    expect(countAvailableSizes(FICHA_4597)).toBe(11);
  });

  it('una talla sigue disponible si le queda UN color con stock', () => {
    // Una talla son varias variantes (un color cada una): basta que una se pueda comprar.
    const ficha = [
      v({ size: '30', inStock: false }),
      v({ size: '30', inStock: true }),
      v({ size: '31', inStock: false }),
      v({ size: '31', delisted: true }),
    ];
    expect(sizeAvailable(ficha, '30')).toBe(true);
    expect(sizeAvailable(ficha, '31')).toBe(false);
    expect(countAvailableSizes(ficha)).toBe(1);
  });

  it('las tallas repetidas por color no se cuentan dos veces', () => {
    const ficha = [v({ size: '30' }), v({ size: '30' }), v({ size: '31' })];
    expect(distinctSizes(ficha)).toEqual(['30', '31']);
    expect(countAvailableSizes(ficha)).toBe(2);
  });

  it('sin ninguna talla comprable el rótulo dice 0 y no revienta', () => {
    const ficha = [v({ size: '30', inStock: false }), v({ size: '31', delisted: true })];
    expect(countAvailableSizes(ficha)).toBe(0);
  });
});

/**
 * El rótulo del selector de tallas (#331).
 *
 * Los casos son los tres que publican las tiendas de verdad, medidos contra `deal_tracker_qa` el
 * 13/08/2026: la medida que DISCRIMINA (H&M, 20 productos; Hipercor, 2), la que solo REPITE
 * (Hipercor, 28 grupos) y la diferencia de pura GRAFÍA.
 *
 * Quién decide cuál es cuál no es esto: lo resuelve la base y llega en `sizeLabel`. Lo que se
 * prueba aquí es que la ficha lo indexe por la clave correcta —la talla CRUDA, que es con la que
 * selecciona— y que no invente etiqueta donde no la hay.
 */
function m(over: Partial<MedidaHermana> = {}): MedidaHermana {
  return { size: '30', sizeLabel: '30', sizeCanon: '30', ...over };
}

/** Las dos prendas de recién nacido de H&M: misma etiqueta de edad, dos alturas. */
const HM_RECIEN_NACIDO: MedidaHermana[] = [
  m({ size: '0-1 meses (44 cm)', sizeLabel: '0-1 meses · 44 cm', sizeCanon: '0-1 meses' }),
  m({ size: '0-1 meses (50 cm)', sizeLabel: '0-1 meses · 50 cm', sizeCanon: '0-1 meses' }),
  m({ size: '1-2 meses (56 cm)', sizeLabel: '1-2 meses', sizeCanon: '1-2 meses' }),
];

describe('sizeLabels (#331)', () => {
  it('indexa por la talla CRUDA, que es la clave con la que la ficha selecciona', () => {
    const etiquetas = sizeLabels(HM_RECIEN_NACIDO);
    expect(etiquetas.get('0-1 meses (44 cm)')).toBe('0-1 meses · 44 cm');
    expect(etiquetas.get('0-1 meses (50 cm)')).toBe('0-1 meses · 50 cm');
  });

  it('donde no hay ambigüedad rotula con la canónica sola, sin el texto de la tienda', () => {
    // Es el caso de los 16.482 productos restantes, y de rebote quita del chip cosas como
    // '3 meses/6 meses - Medida 68 cm', que no caben en un botón de 46 px.
    const etiquetas = sizeLabels([
      m({ size: '9-10 años - Medida 128 cm', sizeLabel: '9-10 años', sizeCanon: '9-10 años' }),
    ]);
    expect(etiquetas.get('9-10 años - Medida 128 cm')).toBe('9-10 años');
  });

  it('si la API no manda etiqueta, cae en la cruda en vez de dejar el chip vacío', () => {
    const etiquetas = sizeLabels([m({ size: '2 años (92 cm)', sizeLabel: null })]);
    expect(etiquetas.get('2 años (92 cm)')).toBe('2 años (92 cm)');
  });

  it('las variantes sin talla no ensucian el mapa', () => {
    expect(sizeLabels([m({ size: null, sizeLabel: null })]).size).toBe(0);
  });
});

describe('otherMeasures (#331)', () => {
  it('dice las OTRAS medidas que el interés va a cubrir', () => {
    // El interés se guarda por la canónica ('0-1 meses'), así que seguir la de 44 avisa también
    // de la de 50. El modal lo dice ANTES de seguirla, en vez de que el usuario lo descubra
    // recibiendo un aviso de una prenda que no es la suya.
    expect(otherMeasures(HM_RECIEN_NACIDO, '0-1 meses (44 cm)')).toEqual(['0-1 meses · 50 cm']);
    expect(otherMeasures(HM_RECIEN_NACIDO, '0-1 meses (50 cm)')).toEqual(['0-1 meses · 44 cm']);
  });

  it('no avisa de nada cuando la talla no tapa ninguna otra', () => {
    expect(otherMeasures(HM_RECIEN_NACIDO, '1-2 meses (56 cm)')).toEqual([]);
  });

  it('el sufijo que solo REPITE no cuenta como otra medida', () => {
    // '9-10 años' y '9-10 años - Medida 128 cm' son la misma talla física, así que la base les
    // da la MISMA etiqueta y aquí no hay NADA que advertir. Si esto devolviera algo, el modal
    // diría «esta tienda publica 2 medidas con esta misma talla (9-10 años y la elegida)» en 28
    // grupos de Hipercor: una ambigüedad inventada, justo lo contrario de lo que busca.
    const hipercor: MedidaHermana[] = [
      m({ size: '9-10 años', sizeLabel: '9-10 años', sizeCanon: '9-10 años' }),
      m({ size: '9-10 años - Medida 128 cm', sizeLabel: '9-10 años', sizeCanon: '9-10 años' }),
    ];
    expect(otherMeasures(hipercor, '9-10 años')).toEqual([]);
    expect(otherMeasures(hipercor, '9-10 años - Medida 128 cm')).toEqual([]);
  });

  it('sin talla seleccionada no dice nada', () => {
    expect(otherMeasures(HM_RECIEN_NACIDO, null)).toEqual([]);
  });
});
