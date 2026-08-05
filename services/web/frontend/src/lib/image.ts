/**
 * Cómo se le pide un ancho a la foto de cada tienda (#207).
 *
 * No hay una forma común: cada CDN tiene la suya y **tres de los nueve rechazan la petición con
 * 4xx** si se les cuela un parámetro que no entienden mal formado. La versión anterior concatenaba
 * `&w=` a pelo dando por hecho que la URL ya traía query —cierto solo en las cuatro tiendas que
 * había entonces— y las otras cinco salían con un `&` sin `?`: C&A 400, Hipercor y Mango 403, H&M
 * y Springfield 404, o sea 8.560 de 12.787 productos con el placeholder de «SIN FOTO».
 *
 * Medido contra los nueve CDN el 05/08/2026, con la foto de una tarjeta (563 px):
 *
 *   host                        cruda     ancho pedido
 *   static.zara.net             124 KB    `w`        -> 9,8 KB
 *   static.lefties.com          —         `w`        (Akamai veta curl; se mide en navegador)
 *   image.hm.com                3,2 MB    `imwidth`  -> 181 KB   (con `w` o `width`: 2,5 MB)
 *   media.mango.com             12,3 KB   `wid`      -> 7,8 KB   (Scene7)
 *   cdn.grupoelcorteingles.es   15,4 KB   `impolicy=Resize&width` -> 13,2 KB
 *   dam.elcorteingles.es        32 KB     ninguno    (ya trae su `impolicy&width` desde el scraper)
 *   cdn.shopify.com             36 KB     ninguno    (`width` da 404; ignora el resto)
 *   www.c-and-a.com             328 KB    ninguno    (Cloudinary veta las transformaciones para
 *                                                     `productimages/`, ver `c_and_a.py`)
 *   myspringfield.com           85 KB     ninguno    (`sw` no es determinista: la misma URL sirve
 *                                                     85 KB o 383 KB según el parámetro)
 *
 * Un host que no esté en la tabla se deja **intacto**, no se le pide ancho. Es el fallo seguro: una
 * foto más pesada de la cuenta se ve; una URL rota, no. Al añadir una tienda (skill `nueva-tienda`)
 * hay que medir su CDN y añadirlo aquí — si no, sus fotos funcionarán, solo que sin optimizar.
 */

/** Nombre del parámetro de ancho de cada CDN, o `null` si no acepta ninguno. */
const ANCHO_POR_HOST: Record<string, string | null> = {
  'static.zara.net': 'w',
  'static.lefties.com': 'w',
  'image.hm.com': 'imwidth',
  'media.mango.com': 'wid',
  'cdn.grupoelcorteingles.es': 'impolicy=Resize&width',
  'dam.elcorteingles.es': null,
  'cdn.shopify.com': null,
  'www.c-and-a.com': null,
  'myspringfield.com': null,
};

/**
 * URL final de la foto con el ancho que su CDN entienda. El `width` es una petición, no una
 * garantía: los CDN sin parámetro devuelven la foto entera y por eso `ProductImage` fija el hueco
 * con `aspectRatio` + `objectFit: cover`.
 */
export function imageSrc(src: string, width: number): string {
  let host: string;
  try {
    host = new URL(src).host;
  } catch {
    return src; // no es una URL absoluta: no hay nada que decidir, se sirve tal cual
  }

  const param = ANCHO_POR_HOST[host];
  if (!param) return src;

  return `${src}${src.includes('?') ? '&' : '?'}${param}=${width}`;
}
