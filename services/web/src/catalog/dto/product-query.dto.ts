import { Transform } from 'class-transformer';
import { IsBoolean, IsIn, IsInt, IsNumber, IsOptional, Max, Min } from 'class-validator';

import { CatalogFilterDto } from './catalog-filter.dto';

/**
 * Reexportados desde `catalog-filter.dto` para no romper a quien ya los importaba de aquí, que es
 * donde vivieron hasta #292.
 */
export {
  BAREFOOT_FILTERS,
  MAX_SEARCH_LENGTH,
  type BarefootFilter,
} from './catalog-filter.dto';

/** Criterios de ordenación admitidos por el catálogo. */
export const PRODUCT_SORTS = ['ofertas', 'precio-asc', 'precio-desc', 'descuento'] as const;
export type ProductSort = (typeof PRODUCT_SORTS)[number];

/**
 * Filtros y paginación de `GET /api/catalog/products`. Todos opcionales.
 *
 * Hereda de `CatalogFilterDto` los ejes que comparte con las facetas; lo que añade aquí es, por un
 * lado, la paginación y el orden, y por otro los tres filtros que **necesitan `price_history`** y
 * que por eso no cruzan a la faceta (la razón, medida, está en la cabecera de la clase base).
 */
export class ProductQueryDto extends CatalogFilterDto {
  @IsOptional()
  @Transform(({ value }) => (value === undefined ? undefined : value === 'true' || value === true))
  @IsBoolean()
  inStock?: boolean;

  /**
   * Deja solo las **ofertas reales** (mínimo nuevo con rebaja contra el PVP creíble), no cualquier
   * rebaja que declare la tienda. Apagado por defecto: el catálogo completo es el valor para quien
   * lo usa para no ir tienda por tienda, y la oferta es secundaria para ese uso.
   */
  @IsOptional()
  @Transform(({ value }) => (value === undefined ? undefined : value === 'true' || value === true))
  @IsBoolean()
  onlyDeals?: boolean;

  /**
   * Rango de precio (#290), sobre `price_from` — el precio de la variante más barata del producto,
   * que es el que la tarjeta enseña. Filtrar por otro haría que el catálogo devolviera prendas cuyo
   * precio visible cae fuera del rango pedido.
   *
   * Ambos extremos **incluyen**, que es lo que espera quien escribe "hasta 20 €".
   *
   * Van aquí y no en `CatalogFilterDto` por la misma razón que `inStock` y `onlyDeals`: el precio
   * sale de `price_history`, así que cruzarlos a la faceta obligaría a montar el CTE `latest` en
   * cada cambio de filtro. Ver la cabecera de la clase base.
   */
  @IsOptional()
  @Transform(({ value }) => Number.parseFloat(value as string))
  @IsNumber()
  @Min(0)
  minPrice?: number;

  @IsOptional()
  @Transform(({ value }) => Number.parseFloat(value as string))
  @IsNumber()
  @Min(0)
  maxPrice?: number;

  @IsOptional()
  @IsIn(PRODUCT_SORTS)
  sort: ProductSort = 'ofertas';

  @IsOptional()
  @Transform(({ value }) => value === undefined || value === 'true' || value === true)
  @IsBoolean()
  activeOnly = true;

  @IsOptional()
  @Transform(({ value }) => Number.parseInt(value as string, 10))
  @IsInt()
  @Min(1)
  @Max(100)
  limit = 20;

  @IsOptional()
  @Transform(({ value }) => Number.parseInt(value as string, 10))
  @IsInt()
  @Min(0)
  offset = 0;
}
