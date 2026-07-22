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
  url: string | null;
  /** Foto primaria del producto en el CDN de la tienda (`null` si aún no se conoce). */
  imageUrl: string | null;
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

export interface ProductDetail
  extends Omit<
    ProductListItem,
    | 'priceFrom'
    | 'listFrom'
    | 'discountFrom'
    | 'maxDiscount'
    | 'honesty'
    | 'anyInStock'
    | 'variantCount'
  > {
  variants: VariantWithPrice[];
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
