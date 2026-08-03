/** Formas de respuesta del catálogo (precios como string: dinero exacto, sin float). */

import type { HonestyVerdict } from '../matching/deal-rule';

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
  url: string | null;
  /** Foto del producto en el CDN de la tienda (`null` si aún no se conoce). */
  imageUrl: string | null;
  /**
   * Color de la variante "mejor oferta", la MISMA de la que salen `listFrom`/`discountFrom`/
   * `honesty`. La tarjeta lo necesita para que la foto que enseña sea la del color cuyo precio
   * enseña: sin esto puede pintar la foto de un color con el precio de otro.
   */
  colorRepr: string | null;
  priceFrom: string | null;
  /** PVP y descuento de la variante "mejor oferta" (en stock, más barata). */
  listFrom: string | null;
  discountFrom: string | null;
  /** Mayor descuento entre las variantes del producto (para orden/badge). */
  maxDiscount: string | null;
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
  color: string | null;
  sku: string | null;
  url: string | null;
  delisted: boolean;
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  inStock: boolean | null;
  scrapedAt: string | null;
  /** Veredicto de descuento honesto de esta variante (misma regla que el aviso). */
  honesty: HonestyVerdict;
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
  colors: string[];
  retailers: RetailerFacet[];
}
