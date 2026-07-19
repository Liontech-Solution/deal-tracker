/**
 * Tipos espejo del contrato de `services/web/src/catalog/catalog.types.ts`.
 * Los precios llegan como string (dinero exacto, sin float): se formatean/parsean en `lib/format`.
 */

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
  priceFrom: string | null;
  listFrom: string | null;
  discountFrom: string | null;
  maxDiscount: string | null;
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
}

export interface ProductDetail {
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

export type ProductSort = 'ofertas' | 'precio-asc' | 'precio-desc' | 'descuento';

export interface ProductQuery {
  gender?: string;
  section?: string;
  category?: string;
  size?: string;
  color?: string;
  retailer?: string;
  inStock?: boolean;
  sort?: ProductSort;
  limit?: number;
  offset?: number;
}
