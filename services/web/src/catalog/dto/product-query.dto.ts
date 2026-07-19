import { Transform } from 'class-transformer';
import { IsBoolean, IsIn, IsInt, IsOptional, IsString, Max, Min } from 'class-validator';

/** Criterios de ordenación admitidos por el catálogo. */
export const PRODUCT_SORTS = ['ofertas', 'precio-asc', 'precio-desc', 'descuento'] as const;
export type ProductSort = (typeof PRODUCT_SORTS)[number];

/** Filtros y paginación de `GET /api/catalog/products`. Todos opcionales. */
export class ProductQueryDto {
  @IsOptional()
  @IsString()
  gender?: string; // niño | niña | unisex

  @IsOptional()
  @IsString()
  section?: string; // ropa | zapateria

  @IsOptional()
  @IsString()
  category?: string;

  @IsOptional()
  @IsString()
  size?: string;

  @IsOptional()
  @IsString()
  color?: string;

  @IsOptional()
  @IsString()
  retailer?: string; // slug de la tienda

  @IsOptional()
  @Transform(({ value }) => (value === undefined ? undefined : value === 'true' || value === true))
  @IsBoolean()
  inStock?: boolean;

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
