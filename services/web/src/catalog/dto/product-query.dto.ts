import { Transform } from 'class-transformer';
import { IsBoolean, IsIn, IsInt, IsOptional, IsString, Max, Min } from 'class-validator';

/** Criterios de ordenación admitidos por el catálogo. */
export const PRODUCT_SORTS = ['ofertas', 'precio-asc', 'precio-desc', 'descuento'] as const;
export type ProductSort = (typeof PRODUCT_SORTS)[number];

/**
 * Filtro de calzado respetuoso. `si` es el DEFECTO del producto entero: es una plataforma de ropa
 * y calzado barefoot, así que enseñar calzado convencional sin pedirlo sería contar otra cosa.
 *
 * - `si` (defecto): toda la ropa + solo el calzado marcado como respetuoso.
 * - `no` / `desconocido`: solo calzado con esa marca. Sirven para auditar la clasificación.
 * - `all`: sin filtro — el escape explícito para la futura vista de "ver también el no respetuoso".
 */
export const BAREFOOT_FILTERS = ['si', 'no', 'desconocido', 'all'] as const;
export type BarefootFilter = (typeof BAREFOOT_FILTERS)[number];

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

  /**
   * Por defecto `si`: el catálogo esconde el calzado no respetuoso salvo que se pida lo contrario.
   * No afecta a la ropa, donde la marca es NULL ("no aplica") y siempre pasa el filtro.
   */
  @IsOptional()
  @IsIn(BAREFOOT_FILTERS)
  barefoot: BarefootFilter = 'si';

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
