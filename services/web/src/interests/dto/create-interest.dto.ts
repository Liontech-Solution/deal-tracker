import { Type } from 'class-transformer';
import {
  IsIn,
  IsInt,
  IsNumber,
  IsOptional,
  IsPositive,
  IsString,
  Max,
  Min,
} from 'class-validator';

/**
 * Alta de un interés. Admite apuntar a producto/variante y/o filtrar por atributos.
 * La validación de "al menos una señal" la refuerza también el CHECK de la migración 0004.
 */
export class CreateInterestDto {
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @IsPositive()
  retailerId?: number;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @IsPositive()
  productId?: number;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @IsPositive()
  variantId?: number;

  @IsOptional()
  @IsString()
  gender?: string;

  @IsOptional()
  @IsString()
  section?: string;

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
  @Type(() => Number)
  @IsNumber()
  @Min(0)
  @Max(100)
  minDiscountPct?: number;

  @IsOptional()
  @IsIn(['list_price', 'recent_min'])
  compareBase?: 'list_price' | 'recent_min';

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(365)
  windowDays?: number;
}
