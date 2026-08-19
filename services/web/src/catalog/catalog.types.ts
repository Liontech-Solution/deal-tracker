/** Formas de respuesta del catálogo (precios como string: dinero exacto, sin float). */

import type { HonestyBasis, HonestyVerdict } from '../matching/deal-rule';

export interface ProductListItem {
  id: number;
  retailerId: number;
  retailerSlug: string;
  retailerName: string;
  retailerProductId: string;
  name: string;
  gender: string | null;
  section: string | null;
  category: string | null;
  /** Calzado respetuoso: `si` | `no` | `desconocido`; `null` en ropa, donde no aplica. */
  barefoot: string | null;
  /**
   * Ejes transversales a la categoría (`product_tag`, #180). Hoy solo puede traer `deportiva`.
   *
   * Vacío NO significa «no es deportiva»: significa que su tienda no lo declara. Solo Sfera,
   * Lefties y C&A publican un cajón de deporte identificable, así que un producto de Zara nunca
   * llevará la marca aunque sea un chándal. Quien pinte esto no debe leerlo como una negación.
   */
  tags: string[];
  url: string | null;
  /** Foto del producto en el CDN de la tienda (`null` si aún no se conoce). */
  imageUrl: string | null;
  /**
   * Color de la variante "mejor oferta", la MISMA de la que salen `listFrom`/`discountFrom`/
   * `honesty`. La tarjeta lo necesita para que la foto que enseña sea la del color cuyo precio
   * enseña: sin esto puede pintar la foto de un color con el precio de otro.
   */
  colorRepr: string | null;
  /**
   * El **«desde»** de la tarjeta: lo más barato que se puede **comprar** (#402).
   *
   * Sale de la misma variante "mejor oferta" que `colorRepr`, `listFrom`, `discountFrom` y
   * `honesty`, y eso es lo que garantiza que la tarjeta no pinte el precio de una prenda con el
   * tachado de otra. Cuando **ninguna** variante tiene stock cae al mínimo a secas, que es el
   * comportamiento de siempre y va acompañado de `anyInStock: false`.
   *
   * El nombre se conserva por contrato de API; lo que cambió en #402 es lo que significa.
   */
  priceFrom: string | null;
  /** PVP y descuento de la variante "mejor oferta" (en stock, más barata). */
  listFrom: string | null;
  discountFrom: string | null;
  /** Mayor descuento entre las variantes del producto (para orden/badge). */
  maxDiscount: string | null;
  /**
   * PVP **creíble** de la variante "mejor oferta" y el descuento que se sostiene contra él (#436).
   *
   * Hasta ahora la regla los calculaba para decidir el veredicto y para ordenar «Ofertas», pero no
   * salían de la API: la tarjeta pintaba `listFrom`/`discountFrom` —lo que declara la tienda— en
   * verde aunque la regla acabara de descartar ese tachado por inflado. Medido en QA el 16/08/2026,
   * eso pasaba en 88 de los 246 productos con badge, enseñando un 51,7 % medio donde la regla
   * sostiene un 24,4 %.
   *
   * `honestListPrice` es `null` en arranque en frío: sin nada observado antes no hay ninguna
   * referencia creíble, y ahí **no se cae de vuelta al tachado de la tienda** — es justo el caso que
   * este producto delata. Quien lo pinte tiene que tratar ese `null` como «no podemos sostener
   * ninguna cifra», nunca como «usa la de la tienda».
   */
  honestListPrice: string | null;
  /** Descuento en % contra `honestListPrice`; `0` cuando no hay ninguno que sostener. */
  honestDiscountPct: number;
  /** Veredicto de descuento honesto de la variante "mejor oferta" (misma regla que el aviso). */
  honesty: HonestyVerdict;
  anyInStock: boolean;
  variantCount: number;
}

export interface ProductListResult {
  items: ProductListItem[];
  limit: number;
  offset: number;
}

export interface VariantWithPrice {
  id: number;
  retailerVariantId: string;
  size: string | null;
  /**
   * La misma talla que `size`, plegada por `size_canon` (0014). Sale aparte desde #297 para que la
   * SPA pueda componer la etiqueta de la variante —la que confirma el modal de seguimiento— sin
   * rehacer la canónica en TypeScript, que es justo lo que #248 condenó.
   *
   * `size` sigue siendo la de la tienda y sigue siendo lo que pinta el selector: en ropa infantil
   * el paréntesis que `size_canon` borra ('2 años (92 cm)' -> '2 años', ver la 0024) es por lo que
   * un padre elige.
   */
  sizeCanon: string | null;
  /**
   * Lo que el selector de tallas **enseña** (#331). Es `sizeCanon`, y solo cuando este producto
   * publica dos tallas físicas distintas bajo la misma etiqueta de edad, con la medida detrás:
   *
   *     0-1 meses · 44 cm      0-1 meses · 50 cm       (H&M, 20 productos)
   *     3 meses · 62 cm        3 meses · 68 cm         (Hipercor, 2 productos)
   *     11-12 años                                     (todos los demás: la canónica sola)
   *
   * No sustituye a `size`, que sigue siendo el texto de la tienda y **la clave** con la que la SPA
   * selecciona una talla; ni a `sizeCanon`, que es lo que se guarda en `interest.size`. Esto es
   * solo el rótulo, y existe porque los dos anteriores fallan al rotular: la canónica sola tapaba
   * una de las dos medidas, y la cruda sacaba `3 meses/6 meses - Medida 68 cm` en un chip.
   */
  sizeLabel: string | null;
  color: string | null;
  sku: string | null;
  url: string | null;
  delisted: boolean;
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  inStock: boolean | null;
  scrapedAt: string | null;
  /**
   * Cómo se nombra esta variante donde el usuario la reconoce como «la prenda que sigo»: la misma
   * `variantLabel()` que rotula `/seguimientos` y el aviso de Telegram (#223, #248). Lleva la talla
   * CANÓNICA, mientras que `size` sigue siendo la de la tienda — que es lo que pinta el selector de
   * tallas de la ficha. `null` cuando la variante no tiene ni talla ni color.
   */
  variantLabel: string | null;
  /**
   * Días enteros que llevamos observando esta variante. Existe para que la ficha de una prenda
   * `unverified` pueda decir «llevamos N días siguiéndola» en vez de acusar a la tienda de inflar
   * un tachado que todavía no podemos desmentir (#332). No sale en el listado: la tarjeta no lo
   * pinta, y la superficie de la API se queda en lo que alguien consume.
   */
  trackedDays: number;
  /**
   * El tramo, en días enteros, que una afirmación de MÍNIMO puede citar sin mentir (#517).
   *
   * No es `trackedDays`, y la diferencia es el defecto que esta issue vino a cerrar: `recent_min`
   * se calcula con el filtro de `HONESTY_WINDOW_DAYS` y `trackedDays` se calcula sobre la serie
   * entera, así que en cuanto una prenda pase de esos días el texto de la ficha estaría diciendo
   * «es lo más barato que la hemos visto en los N días que llevamos siguiéndola» sobre una ventana
   * que no cubre esos N. Aquí ya viene el `LEAST` de los dos hecho.
   *
   * **Hoy coincide siempre con `trackedDays`**, y eso es un dato, no una casualidad que tape el
   * problema: medido el 19/08/2026, la serie de `price_history` abarca 26 días en QA y 12 en prod,
   * con cero filas fuera de la ventana. El primer caso en que los dos números se separan llega
   * hacia el ~22/10/2026 en QA y el ~05/11/2026 en prod. O sea que esto **no se puede verificar
   * observando** todavía: se sostiene por construcción y por su test.
   *
   * Lo resuelve el backend en vez de exportar el 90 porque el SPA es otro paquete y no puede
   * importar la regla: la constante duplicada allí sería el quinto espejo de esta misma regla
   * (#375, #473, #489, #511), que es el mecanismo con el que este repo ya se ha quemado cuatro
   * veces.
   */
  claimDays: number;
  /**
   * Mínimo de los últimos 30 días que declara la tienda por la directiva Ómnibus (#354). `null` en
   * las siete tiendas que no lo publican — solo lo traen C&A y Springfield.
   *
   * Sale en la ficha y no en el listado, con el mismo criterio que `trackedDays`: lo consume el
   * texto de una acusación `declarado`, que cita la cifra en vez de limitarse a etiquetar.
   */
  retailerMin30d: string | null;
  /**
   * PVP creíble de ESTA variante y el descuento que se sostiene contra él (#436). Mismo criterio y
   * mismo `null` que en `ProductListItem`; aquí sí sale en la ficha, que es donde el usuario compara
   * la cifra con el tachado que la tienda le enseña al lado.
   */
  honestListPrice: string | null;
  honestDiscountPct: number;
  /** Veredicto de descuento honesto de esta variante (misma regla que el aviso). */
  honesty: HonestyVerdict;
  /**
   * En qué se apoya una acusación: en nuestro histórico (`observado`) o en lo que la propia tienda
   * declara (`declarado`). `null` en todo lo que no sea `suspicious` (#354).
   *
   * Existe porque la frase de la ficha no puede ser la misma en los dos casos: decir «inflado
   * respecto a su historial» sobre una prenda que acabamos de descubrir sería falso, y afirmar de
   * más es exactamente lo que #332 vino a quitar.
   */
  honestyBasis: HonestyBasis | null;
}

/** Una foto de la galería, atribuida al color que retrata (`null` = sin color atribuible). */
export interface ProductImageRef {
  color: string | null;
  url: string;
  /**
   * Ficha de la tienda de la que sale la foto (= `VariantWithPrice.url`). Solo la rellena H&M,
   * donde dos artículos distintos pueden compartir nombre de color (#123); `null` en las demás
   * tiendas y en lo ingerido antes de la 0023, y ahí el color solo ya identifica la galería.
   */
  variantUrl: string | null;
}

export interface ProductDetail
  extends Omit<
    ProductListItem,
    | 'colorRepr'
    | 'priceFrom'
    | 'listFrom'
    | 'discountFrom'
    | 'maxDiscount'
    | 'honestListPrice'
    | 'honestDiscountPct'
    | 'honesty'
    | 'anyInStock'
    | 'variantCount'
  > {
  variants: VariantWithPrice[];
  /** Galería ordenada por color y posición. La ficha filtra por el color seleccionado. */
  images: ProductImageRef[];
}

export interface PricePoint {
  price: string;
  listPrice: string | null;
  discountPct: string | null;
  inStock: boolean;
  scrapedAt: string;
}

export interface RetailerFacet {
  slug: string;
  name: string;
}

export interface Facets {
  genders: string[];
  sections: string[];
  categories: string[];
  sizes: string[];
  /**
   * Las tallas concretas de la banda ya elegida (#367). **Vacía si no hay banda**, y vacía siempre
   * en `zapateria`, donde el primer piso ya es la canónica.
   */
  sizeValues: string[];
  colors: string[];
  retailers: RetailerFacet[];
}
