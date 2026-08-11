import { describe, expect, it } from 'vitest';

import { imageSrc } from './image';

/**
 * Regresión de #207 y de #300. Las URL son reales, sacadas de la base, porque lo que falla aquí no
 * es la lógica sino el supuesto sobre la forma de cada una: la versión anterior daba por hecho que
 * todas traían query y las cinco que no lo traen salían con un `&` sin `?` y un 4xx del CDN.
 *
 * Lo que añade #300 es el otro supuesto, el de que la tabla estaba completa y al día: se le habían
 * quedado fuera los 187 productos que H&M cuela por `media.arket.com`, y dos hosts valían `null`
 * con un motivo que ya no era cierto. Ojo con `dam.elcorteingles.es`, que sale DOS veces aquí:
 * lo comparten Sfera —cuyo JSON de origen a veces trae ya el ancho pedido— e Hipercor, que nunca.
 */
const URLS = {
  zara: 'https://static.zara.net/assets/public/dee3/8874/4b8b48d39465/2246064fa4ce/12536830800-e1/12536830800-e1.jpg?ts=1782468539072',
  lefties:
    'https://static.lefties.com/assets/public/fb81/0f11/5a6949eca563/cc2f5944a434/13274890102-R/13274890102-R.jpg?ts=1784117385726',
  /** Sfera: llega con el ancho YA pedido por la tienda (396 de sus 864 fotos en QA). */
  sfera: 'https://dam.elcorteingles.es/producto/www-001058570204717-00.jpg?impolicy=Resize&width=516&height=640',
  /** Hipercor, foto de ficha: mismo host que Sfera y SIN ancho pedido, sus 512 en QA. 167 KB. */
  hipercorFicha: 'https://dam.elcorteingles.es/producto/www-001081182601955-00.jpg',
  cacles: 'https://cdn.shopify.com/s/files/1/0613/2360/8282/files/Cacles-Barefoot-JOMA.jpg?v=1783750504',
  hm: 'https://image.hm.com/assets/hm/00/46/0046180b2e3fb29fc80eb39157060905bb0c45cf.jpg',
  arket: 'https://media.arket.com/assets/006/ef/a5/efa58a0c503e61c4f79ffc791db047103bb1b764.jpg',
  mango: 'https://media.mango.com/is/image/punto/17002923-01-900',
  hipercor: 'https://cdn.grupoelcorteingles.es/SGFM/dctm/MEDIA03/202412/04/00198413012570____3__440x546.jpg',
  cAndA: 'https://www.c-and-a.com/image/upload/productimages/v1654847583/2090680-1-08.jpg',
  springfield:
    'https://myspringfield.com/on/demandware.static/-/Sites-gc-spf-master-catalog/default/dw0000d18f/images/hi-res/P_730103698FM.jpg',
};

describe('imageSrc', () => {
  it('usa `?` cuando la URL no trae query — el fallo de #207', () => {
    expect(imageSrc(URLS.hm, 563)).toBe(`${URLS.hm}?imwidth=563`);
    expect(imageSrc(URLS.hm, 563)).not.toContain('.jpg&');
  });

  it('usa `&` cuando la URL ya trae query', () => {
    expect(imageSrc(URLS.zara, 563)).toBe(`${URLS.zara}&w=563`);
    expect(imageSrc(URLS.lefties, 160)).toBe(`${URLS.lefties}&w=160`);
  });

  it('le da a cada CDN el parámetro que entiende, no `w`', () => {
    // H&M con `w` o `width` sirve 2,5 MB; con `imwidth`, 119 KB.
    expect(imageSrc(URLS.hm, 563)).toBe(`${URLS.hm}?imwidth=563`);
    // Arket es el mismo CDN que H&M y entiende lo mismo (#300): 557 KB -> 17 KB.
    expect(imageSrc(URLS.arket, 563)).toBe(`${URLS.arket}?imwidth=563`);
    // Mango es Scene7.
    expect(imageSrc(URLS.mango, 563)).toBe(`${URLS.mango}?wid=563`);
    expect(imageSrc(URLS.hipercor, 563)).toBe(`${URLS.hipercor}?impolicy=Resize&width=563`);
    // Shopify: la tabla decía que `width` daba 404 y hoy da 200 (#300): 222 KB -> 44 KB.
    expect(imageSrc(URLS.cacles, 563)).toBe(`${URLS.cacles}&width=563`);
  });

  it('pide el ancho a la foto de ficha de Hipercor, que salía cruda a 167 KB — #300', () => {
    // El host valía `null` porque «ya trae su impolicy&width desde el scraper». No era cierto:
    // `hipercor.py` no lo añade en ningún camino, y el DAM sí acepta el parámetro (-> 14 KB).
    expect(imageSrc(URLS.hipercorFicha, 563)).toBe(
      `${URLS.hipercorFicha}?impolicy=Resize&width=563`,
    );
  });

  it('no pide el ancho dos veces si la tienda ya lo pidió — #300', () => {
    // Las 396 fotos de Sfera que llegan dimensionadas de origen: manda su ancho, no el nuestro.
    // Sin esta regla saldrían con el parámetro repetido, y la precedencia la decidiría el CDN.
    expect(imageSrc(URLS.sfera, 563)).toBe(URLS.sfera);
    expect(imageSrc(URLS.sfera, 563)).not.toContain('width=563');
    // Y no es un caso especial del host: vale para cualquiera de la tabla.
    const zaraYaPedida = `${URLS.zara}&w=200`;
    expect(imageSrc(zaraYaPedida, 563)).toBe(zaraYaPedida);
  });

  it('deja intactas las URL de los CDN que no aceptan ancho', () => {
    // C&A: Cloudinary con las transformaciones vetadas para `productimages/` — `?w=` lo ignora
    // (mismos 224 KB) y la transformación en la ruta da 400.
    expect(imageSrc(URLS.cAndA, 563)).toBe(URLS.cAndA);
    // Springfield: `sw` no es determinista y además empeora — 74 KB crudos, 387 KB con `?sw=563`.
    expect(imageSrc(URLS.springfield, 563)).toBe(URLS.springfield);
  });

  it('no toca un host desconocido: la foto pesada se ve, la URL rota no', () => {
    const nueva = 'https://cdn.tienda-nueva.example/foto/1234.jpg';
    expect(imageSrc(nueva, 563)).toBe(nueva);
  });

  it('no se deja engañar por un host que solo aparece en la ruta o el query', () => {
    const falso = 'https://cdn.tienda-nueva.example/foto.jpg?ref=static.zara.net';
    expect(imageSrc(falso, 563)).toBe(falso);
    expect(imageSrc('https://otro.example/static.zara.net/foto.jpg', 563)).toBe(
      'https://otro.example/static.zara.net/foto.jpg',
    );
  });

  it('devuelve tal cual lo que no es una URL absoluta', () => {
    expect(imageSrc('/local/foto.jpg', 563)).toBe('/local/foto.jpg');
    expect(imageSrc('', 563)).toBe('');
  });
});
