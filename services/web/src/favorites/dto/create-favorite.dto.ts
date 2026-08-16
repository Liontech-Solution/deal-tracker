import { Type } from 'class-transformer';
import { IsInt, IsPositive } from 'class-validator';

/**
 * Alta de un favorito. Una sola señal y obligatoria: el favorito es del **producto entero**, sin
 * talla ni color. La talla se elige después y solo si se convierte en seguimiento, que es lo que ya
 * sabe hacer `FollowModal` con su `variantId` opcional.
 */
export class CreateFavoriteDto {
  @Type(() => Number)
  @IsInt()
  @IsPositive()
  productId!: number;
}
