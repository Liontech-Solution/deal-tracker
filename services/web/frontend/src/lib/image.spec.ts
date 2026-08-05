import { describe, expect, it } from 'vitest';

import { imageSrc } from './image';

/**
 * Regresión de #207. Las URL son reales, sacadas de la base de `dev`, porque lo que falla aquí no
 * es la lógica sino el supuesto sobre la forma de cada una: la versión anterior daba por hecho que
 * todas traían query y las cinco que no lo traen salían con un `&` sin `?` y un 4xx del CDN.
 */
const URLS = {
  zara: 'https://static.zara.net/assets/public/dee3/8874/4b8b48d39465/2246064fa4ce/12536830800-e1/12536830800-e1.jpg?ts=1782468539072',
  lefties:
    'https://static.lefties.com/assets/public/fb81/0f11/5a6949eca563/cc2f5944a434/13274890102-R/13274890102-R.jpg?ts=1784117385726',
  sfera: 'https://dam.elcorteingles.es/producto/www-001058570204717-00.jpg?impolicy=Resize&width=516&height=640',
  cacles: 'https://cdn.shopify.com/s/files/1/0613/2360/8282/files/Cacles-Barefoot-JOMA.jpg?v=1783750504',
  hm: 'https://image.hm.com/assets/hm/00/46/0046180b2e3fb29fc80eb39157060905bb0c45cf.jpg',
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
    // H&M con `w` o `width` sirve 2,5 MB; con `imwidth`, 181 KB.
    expect(imageSrc(URLS.hm, 563)).toBe(`${URLS.hm}?imwidth=563`);
    // Mango es Scene7.
    expect(imageSrc(URLS.mango, 563)).toBe(`${URLS.mango}?wid=563`);
    expect(imageSrc(URLS.hipercor, 563)).toBe(`${URLS.hipercor}?impolicy=Resize&width=563`);
  });

  it('deja intactas las URL de los CDN que no aceptan ancho', () => {
    // C&A: Cloudinary con las transformaciones vetadas para `productimages/`.
    expect(imageSrc(URLS.cAndA, 563)).toBe(URLS.cAndA);
    // Springfield: `sw` no es determinista, así que no se le pide nada.
    expect(imageSrc(URLS.springfield, 563)).toBe(URLS.springfield);
    // Cacles: `width` da 404 en el CDN de Shopify.
    expect(imageSrc(URLS.cacles, 563)).toBe(URLS.cacles);
    // Sfera: el scraper ya guarda la URL con su propio `impolicy&width`.
    expect(imageSrc(URLS.sfera, 563)).toBe(URLS.sfera);
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
