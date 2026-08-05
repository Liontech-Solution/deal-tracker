import { Transform } from 'class-transformer';
import { IsBoolean, IsIn, IsOptional, IsString } from 'class-validator';

import { BAREFOOT_FILTERS, type BarefootFilter } from './product-query.dto';

/**
 * Parámetros de `GET /api/catalog/facets`.
 *
 * Comparte `barefoot` con el listado a propósito: las facetas describen los filtros de una vista
 * concreta, así que si esa vista esconde el calzado no respetuoso, sus chips también deben hacerlo.
 *
 * `section` es el mismo criterio llevado a su conclusión: las tallas de la ropa (rangos de edad) y
 * las del calzado (números de pie) no son el mismo vocabulario, y ofrecer las dos listas juntas hace
 * inútiles a las dos. Las categorías igual. El género y la sección misma NO se acotan: son los ejes
 * de navegación con los que se sale de la vista.
 */
export class FacetQueryDto {
  @IsOptional()
  @IsIn(BAREFOOT_FILTERS)
  barefoot: BarefootFilter = 'si';

  @IsOptional()
  @IsString()
  section?: string;

  /**
   * Mismo criterio que `barefoot` llevado al eje transversal (#180): con el interruptor encendido,
   * las facetas describen ESA vista. Si no, el panel ofrecería categorías y tallas que la vista
   * filtrada no devuelve — y aquí se notaría más que en ningún otro filtro, porque el eje solo lo
   * alimentan tres tiendas y deja fuera categorías enteras.
   */
  @IsOptional()
  @Transform(({ value }) => (value === undefined ? undefined : value === 'true' || value === true))
  @IsBoolean()
  deportiva?: boolean;
}
