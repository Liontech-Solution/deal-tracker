import { Transform } from 'class-transformer';
import { IsBoolean, IsInt, IsOptional, IsString, Max, Min } from 'class-validator';

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
