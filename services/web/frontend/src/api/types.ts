/**
 * Tipos espejo del contrato de `services/web/src/catalog/catalog.types.ts`.
 * Los precios llegan como string (dinero exacto, sin float): se formatean/parsean en `lib/format`.
 */

/** Veredicto de descuento honesto (espejo de `HonestyVerdict` del backend). Lo calcula el catálogo. */
export type Honesty = 'real' | 'suspicious' | 'none';

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
  /**
   * Calzado respetuoso: `si` | `no` | `desconocido`; `null` en ropa, donde no aplica. La API ya
   * filtra por esto (solo `si` salvo `?barefoot=all`), así que en el catálogo normal siempre
   * llega `si` o `null`.
   */
  barefoot: string | null;
  /**
   * Ejes transversales a la categoría (#180). Hoy solo puede traer `deportiva`.
   *
   * Vacío **no** es «no es deportiva»: es «su tienda no lo dice». Solo lo declaran Sfera, Lefties
   * y C&A, así que un chándal de Zara llega sin marca. No pintar nunca una negación con esto.
   */
  tags: string[];
  url: string | null;
  imageUrl: string | null;
  /** Color de la variante cuyo precio muestra la tarjeta: `imageUrl` ya viene resuelta a ese color. */
  colorRepr: string | null;
  priceFrom: string | null;
  listFrom: string | null;
  discountFrom: string | null;
  maxDiscount: string | null;
  honesty: Honesty;
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
  honesty: Honesty;
}

/** Una foto de la galería, atribuida al color que retrata (`null` = sin color atribuible). */
export interface ProductImageRef {
  color: string | null;
  url: string;
  /**
   * Ficha de la tienda de la que sale la foto (= `VariantWithPrice.url`). Solo la rellena H&M,
   * donde dos artículos distintos pueden compartir nombre de color (#123); `null` en las demás
   * tiendas y en lo ingerido antes de la 0023.
   */
  variantUrl: string | null;
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
  /** Igual que en la tarjeta, pero aquí SÍ puede llegar `no`/`desconocido`: la ficha directa no
   * se filtra, solo se filtra lo que el catálogo ofrece. */
  barefoot: string | null;
  /** Igual que en la tarjeta: vacío es «su tienda no lo dice», no «no lo es». */
  tags: string[];
  url: string | null;
  imageUrl: string | null;
  variants: VariantWithPrice[];
  /** Galería ordenada por color y posición. La ficha la filtra por el color seleccionado. */
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

/** Estado del vínculo de Telegram (espejo de `TelegramSettingsView` del backend). */
export interface TelegramSettingsView {
  linked: boolean;
  telegramUsername: string | null;
  linkedAt: string | null;
  pendingLink: boolean;
}

/** Resultado de iniciar un vínculo de Telegram (espejo de `TelegramLinkResult`). */
export interface TelegramLinkResult {
  deepLink: string;
  expiresAt: string;
}

export type ProductSort = 'ofertas' | 'precio-asc' | 'precio-desc' | 'descuento';

export interface ProductQuery {
  /** Búsqueda libre sobre nombre, categoría y género. */
  q?: string;
  gender?: string;
  section?: string;
  category?: string;
  size?: string;
  color?: string;
  retailer?: string;
  inStock?: boolean;
  /** Solo ofertas reales (mínimo nuevo con rebaja honesta), no cualquier rebaja declarada. */
  onlyDeals?: boolean;
  /** Solo lo que la tienda publica como ropa de deporte (#180). Deja fuera a seis de las nueve. */
  deportiva?: boolean;
  sort?: ProductSort;
  limit?: number;
  offset?: number;
}
