/**
 * Cómo se le pide un ancho a la foto de cada tienda (#207).
 *
 * No hay una forma común: cada CDN tiene la suya y **tres de los nueve rechazan la petición con
 * 4xx** si se les cuela un parámetro que no entienden mal formado. La versión anterior concatenaba
 * `&w=` a pelo dando por hecho que la URL ya traía query —cierto solo en las cuatro tiendas que
 * había entonces— y las otras cinco salían con un `&` sin `?`: C&A 400, Hipercor y Mango 403, H&M
 * y Springfield 404, o sea 8.560 de 12.787 productos con el placeholder de «SIN FOTO».
 *
 * Medido contra los diez CDN el 11/08/2026 (#300), con la foto de una tarjeta (563 px):
 *
 *   host                        cruda     ancho pedido
 *   static.zara.net             124 KB    `w`        -> 9,5 KB
 *   static.lefties.com          —         `w`        (Akamai veta curl; se mide en navegador)
 *   image.hm.com                1,86 MB   `imwidth`  -> 119 KB   (con `w` o `width`: 2,5 MB)
 *   media.arket.com             557 KB    `imwidth`  -> 17 KB    (x32; mismo CDN que H&M)
 *   media.mango.com             48 KB     `wid`      -> 31 KB    (Scene7)
 *   cdn.grupoelcorteingles.es   8,1 KB    `impolicy=Resize&width` -> 7,8 KB
 *   dam.elcorteingles.es        92 KB     `impolicy=Resize&width` -> 29 KB  (x3,2)
 *   cdn.shopify.com             222 KB    `width`    -> 44 KB    (x5,1)
 *   www.c-and-a.com             224 KB    ninguno    (Cloudinary veta las transformaciones para
 *                                                     `productimages/`: `?w=` lo ignora y la
 *                                                     transformación en la ruta da 400. Ver
 *                                                     `c_and_a.py`)
 *   myspringfield.com           74 KB     ninguno    (`sw` no es determinista y además EMPEORA:
 *                                                     `?sw=563` devuelve 387 KB, x5 la cruda)
 *
 * Tres de esas entradas las corrigió #300, y las tres estaban mal por el mismo motivo —una
 * suposición sin volver a medir—, así que conviene desconfiar de esta tabla y recomprobarla:
 *
 *   - `media.arket.com` no estaba. Arket es marca del grupo H&M, así que su API cuela ~187
 *     productos por un décimo CDN que nadie había mirado. Era el peor caso de todo el catálogo.
 *   - `dam.elcorteingles.es` valía `null` «porque ya trae su `impolicy&width` desde el scraper».
 *     **No es cierto**: `hipercor.py` no añade el parámetro en ningún camino (las URL salen
 *     literales del `ld+json`), y ese host lo comparten DOS tiendas — de las 1.376 fotos de QA,
 *     las 512 de Hipercor no lo traen nunca y las de Sfera solo en 396 de 864, según lo que su
 *     JSON de origen publique en `sources`. O sea que 980 salían crudas a ~92 KB.
 *   - `cdn.shopify.com` valía `null` «porque `width` da 404». Hoy responde **200** y sirve 44 KB
 *     en lugar de 222 KB.
 *
 * Dos reglas, y las dos son el mismo fallo seguro —una foto pesada se ve, una URL rota no—:
 *
 *   1. Un host que no esté en la tabla se deja **intacto**. Al añadir una tienda (skill
 *      `nueva-tienda`) hay que medir su CDN y añadirlo aquí, o sus fotos funcionarán sin optimizar,
 *      que es exactamente cómo se colaron los 187 de Arket. Lo vigila un caso de `/validar-qa`
 *      (`casos-datos.md`), que es lo único que ve a la vez esta tabla y los hosts de la base.
 *   2. Si la URL **ya trae pedido el ancho**, se deja intacta: manda el que puso la tienda. Sin esta
 *      regla no se puede tocar `dam.elcorteingles.es`, porque a las 396 de Sfera que llegan con
 *      `?impolicy=Resize&width=516&height=640` se les concatenaría el parámetro por segunda vez.
 *      Medido, el CDN lo aguanta (200, 33 KB), pero deja la precedencia a su criterio y la URL sucia.
 */

/** Nombre del parámetro de ancho de cada CDN, o `null` si no acepta ninguno. */
const ANCHO_POR_HOST: Record<string, string | null> = {
  'static.zara.net': 'w',
  'static.lefties.com': 'w',
  'image.hm.com': 'imwidth',
  'media.arket.com': 'imwidth',
  'media.mango.com': 'wid',
  'cdn.grupoelcorteingles.es': 'impolicy=Resize&width',
  'dam.elcorteingles.es': 'impolicy=Resize&width',
  'cdn.shopify.com': 'width',
  'www.c-and-a.com': null,
  'myspringfield.com': null,
};

/**
 * URL final de la foto con el ancho que su CDN entienda. El `width` es una petición, no una
 * garantía: los CDN sin parámetro devuelven la foto entera y por eso `ProductImage` fija el hueco
 * con `aspectRatio` + `objectFit: cover`.
 */
export function imageSrc(src: string, width: number): string {
  let url: URL;
  try {
    url = new URL(src);
  } catch {
    return src; // no es una URL absoluta: no hay nada que decidir, se sirve tal cual
  }

  const param = ANCHO_POR_HOST[url.host];
  if (!param) return src;

  // El parámetro puede ser compuesto (`impolicy=Resize&width`): el que lleva el ancho es el último,
  // y es el que dice si la tienda ya lo pidió por su cuenta (regla 2 de la cabecera).
  if (url.searchParams.has(param.slice(param.lastIndexOf('&') + 1))) return src;

  return `${src}${src.includes('?') ? '&' : '?'}${param}=${width}`;
}
