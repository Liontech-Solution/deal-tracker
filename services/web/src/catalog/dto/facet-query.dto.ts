import { IsIn, IsOptional, IsString } from 'class-validator';

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
}
