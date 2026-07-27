import { IsIn, IsOptional } from 'class-validator';

import { BAREFOOT_FILTERS, type BarefootFilter } from './product-query.dto';

/**
 * Parámetros de `GET /api/catalog/facets`.
 *
 * Comparte `barefoot` con el listado a propósito: las facetas describen los filtros de una vista
 * concreta, así que si esa vista esconde el calzado no respetuoso, sus chips también deben hacerlo.
 */
export class FacetQueryDto {
  @IsOptional()
  @IsIn(BAREFOOT_FILTERS)
  barefoot: BarefootFilter = 'si';
}
