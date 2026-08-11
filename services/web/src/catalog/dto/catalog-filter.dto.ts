import { Transform } from 'class-transformer';
import { IsBoolean, IsIn, IsOptional, IsString, MaxLength } from 'class-validator';

/** Tope del término de búsqueda: nadie busca frases, y acota el coste del `LIKE` sin índice. */
export const MAX_SEARCH_LENGTH = 80;

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

/**
 * Ejes de filtrado que comparten el **listado** (`ProductQueryDto`) y las **facetas**
 * (`FacetQueryDto`).
 *
 * Existe por #292: hasta la v0.3.0 las facetas solo se acotaban por `barefoot`, `section` y
 * `deportiva`, así que el panel ofrecía tallas y colores que no existían dentro de la categoría ya
 * elegida — se pinchaba un chip y el catálogo salía vacío. Para que la faceta describa la vista de
 * verdad tiene que recibir los MISMOS filtros que el listado, y compartir la clase es lo que evita
 * que las dos listas se separen la próxima vez que se añada un filtro.
 *
 * **La frontera de esta clase no es estética, es de coste.** Aquí viven solo los ejes que se
 * resuelven con `product` + `variant`. Los que necesitan el precio —`inStock`, `onlyDeals` y el
 * rango `minPrice`/`maxPrice`— se quedan en `ProductQueryDto` a propósito: obligarían a la faceta a
 * montar el CTE `latest` sobre `price_history`, y las facetas se piden ahora en CADA cambio de
 * filtro. Medido sobre la copia de dev (127.567 variantes): cruzar solo estos ejes cuesta **63 ms**;
 * darle recuento a cada chip —que era la alternativa que #292 planteaba— se fue a **6,8-19,3 s** en
 * las cuatro formas que se probaron, y bajarlo de ahí exige materializar un agregado por producto.
 *
 * Consecuencia asumida y visible: con "solo en stock" u "ofertas reales" puestos, el panel sigue
 * ofreciendo chips que esos dos interruptores pueden dejar en nada. Es el único hueco que queda del
 * "0 productos" original, y sale mucho más barato que el segundo de espera.
 */
export class CatalogFilterDto {
  /** Búsqueda libre sobre nombre, categoría y género. Insensible a mayúsculas y acentos. */
  @IsOptional()
  @Transform(({ value }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MaxLength(MAX_SEARCH_LENGTH)
  q?: string;

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

  /**
   * Por defecto `si`: el catálogo esconde el calzado no respetuoso salvo que se pida lo contrario.
   * No afecta a la ropa, donde la marca es NULL ("no aplica") y siempre pasa el filtro.
   */
  @IsOptional()
  @IsIn(BAREFOOT_FILTERS)
  barefoot: BarefootFilter = 'si';

  /**
   * Solo ropa que la tienda publica en su cajón de deporte (#180).
   *
   * **Apagado por defecto**, al revés que `barefoot`: aquel esconde lo que contradice al producto
   * —calzado no respetuoso—, y este solo acota una búsqueda concreta. Encenderlo por defecto
   * escondería casi todo el catálogo.
   *
   * Solo aplica a `ropa`: el calzado deportivo ya se encuentra por la categoría `zapatillas`. Y
   * solo lo alimentan Sfera, Lefties y C&A, así que enciende un filtro que **excluye enteras** a
   * las demás tiendas; la SPA lo dice junto al interruptor.
   */
  @IsOptional()
  @Transform(({ value }) => (value === undefined ? undefined : value === 'true' || value === true))
  @IsBoolean()
  deportiva?: boolean;
}
