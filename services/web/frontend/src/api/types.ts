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

/** Base de comparación de la regla de aviso (espejo de `interest.compare_base`). */
export type CompareBase = 'list_price' | 'recent_min';

/** Alta de un interés: espejo de `CreateInterestDto` del backend. */
export interface CreateInterestInput {
  retailerId?: number;
  productId?: number;
  variantId?: number;
  gender?: string;
  section?: string;
  category?: string;
  size?: string;
  color?: string;
  minDiscountPct?: number;
  compareBase?: CompareBase;
  windowDays?: number;
}

/** Interés enriquecido tal y como lo devuelve `GET /interests` (espejo de `InterestView`). */
export interface InterestView {
  id: number;
  userId: number;
  retailerId: number | null;
  productId: number | null;
  variantId: number | null;
  gender: string | null;
  section: string | null;
  category: string | null;
  size: string | null;
  color: string | null;
  minDiscountPct: string;
  compareBase: CompareBase;
  windowDays: number;
  active: boolean;
  createdAt: string;
  retailerName: string | null;
  productName: string | null;
  variantLabel: string | null;
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
