import { CatalogFilterDto } from './catalog-filter.dto';

/**
 * Parámetros de `GET /api/catalog/facets`.
 *
 * **No añade nada**: desde #292 la faceta recibe exactamente los mismos ejes que el listado, porque
 * describe la vista que el listado va a devolver. Que sea una clase vacía es la intención — el día
 * que se añada un filtro barato, aparece en las dos a la vez y no hay nada que recordar.
 *
 * Lo que decide qué acota a qué **no vive aquí sino en `getFacets`**, y merece decirse porque no es
 * evidente: cada faceta aplica todos los filtros activos MENOS el de su propio eje. Si la lista de
 * tallas se acotara también por la talla elegida, quedaría esa sola talla y no habría forma de
 * cambiar de idea sin limpiar el filtro.
 *
 * La excepción es `section`, que nunca acota a nadie: es el eje de navegación con el que se sale de
 * la vista, y desde #292 también es lo que eligen las pestañas Ropa/Zapatería del grupo de talla.
 * Ropa y zapatería no comparten vocabulario de talla —y peor: 36 de sus formas COINCIDEN
 * significando cosas distintas, `36-38` es calcetín en una y número de pie en la otra—, así que sin
 * sección elegida el panel no ofrece tallas en vez de ofrecer las 205 mezcladas.
 */
export class FacetQueryDto extends CatalogFilterDto {}
