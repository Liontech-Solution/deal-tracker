import { Transform } from 'class-transformer';
import {
  ArrayMaxSize,
  IsArray,
  IsBoolean,
  IsIn,
  IsOptional,
  IsString,
  MaxLength,
} from 'class-validator';

/** Tope del término de búsqueda: nadie busca frases, y acota el coste del `LIKE` sin índice. */
export const MAX_SEARCH_LENGTH = 80;

/**
 * Cuántos valores admite un eje multiseleccionable, y cuánto puede medir cada uno.
 *
 * No son números redondos: la faceta más grande del catálogo son las **165 tallas** de `ropa` sin
 * nada más filtrado (medido en dev), y el panel deja marcar todos sus chips, así que un tope por
 * debajo de eso convertiría en un 400 algo que la interfaz permite hacer. El de longitud sale de la
 * talla cruda más larga que hay en la base (32 caracteres) con margen: por la query string puede
 * llegar la cruda y no la canónica, porque los enlaces viejos siguen vivos.
 *
 * Están para acotar el abuso —una URL con diez mil valores—, no para disciplinar al usuario.
 */
export const MAX_VALORES_POR_EJE = 200;
export const MAX_LONGITUD_VALOR = 80;

/**
 * Normaliza a lista lo que llega por la query string en los ejes multiseleccionables (#329).
 *
 * Express entrega **`string` con un valor y `string[]` con dos o más**, así que sin esto el filtro
 * cambiaría de tipo según cuántos chips haya marcados. Y es lo que mantiene vivos los **enlaces de
 * un solo valor**: los marcadores de antes de la multiselección y los que genera el propio
 * catálogo siguen filtrando igual.
 *
 * Se separan por parámetro repetido y **no por comas**, y eso lo decide el dato: hay tallas que
 * llevan una coma dentro (`26 (16,3 cm)`), así que un separador por comas partiría un valor
 * legítimo en dos que no existen.
 *
 * Deduplica y descarta vacíos porque los dos se cuelan solos al construir URLs a mano y no
 * significan nada: `?size=&size=26` es pedir la 26.
 */
function aLista({ value }: { value: unknown }): string[] | undefined {
  if (value === undefined || value === null) return undefined;
  const bruto = Array.isArray(value) ? value : [value];
  const limpio = bruto
    .filter((v): v is string => typeof v === 'string')
    .map((v) => v.trim())
    .filter((v) => v !== '');
  return limpio.length ? [...new Set(limpio)] : undefined;
}

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

  /**
   * Los tres ejes **multiseleccionables** (#329). Viajan como parámetro repetido
   * (`?size=4 años&size=104`) y se resuelven con `= ANY(...)`, o sea unión: pedir dos tallas
   * devuelve lo que devolvía cada una por separado.
   *
   * Que sean estos tres y no todos tiene una razón medida: **el vocabulario de talla lo fija la
   * tienda, no la prenda** — Sfera solo publica años, C&A solo alturas en cm—, así que con
   * selección única quien pincha `4 años` excluye a C&A sin que nada se lo diga, aunque su `104`
   * sea esa misma talla. Color y tienda van con ella porque combinarlos es igual de natural
   * (dos familias, dos tiendas que comparar).
   *
   * `category` y `gender` se quedan simples a propósito: en género, la regla `unisex` de
   * `gender.sql.ts` hace que marcar niño+niña devuelva casi el catálogo entero, y ese fichero lo
   * comparte el job de matching. Y `section` es el eje de navegación, con el que además las
   * pestañas del panel cortan la ambigüedad de las tallas (#292): ahí marcar las dos sería volver
   * a mezclar dos vocabularios que se solapan.
   */
  @IsOptional()
  @Transform(aLista)
  @IsArray()
  @ArrayMaxSize(MAX_VALORES_POR_EJE)
  @IsString({ each: true })
  @MaxLength(MAX_LONGITUD_VALOR, { each: true })
  size?: string[];

  @IsOptional()
  @Transform(aLista)
  @IsArray()
  @ArrayMaxSize(MAX_VALORES_POR_EJE)
  @IsString({ each: true })
  @MaxLength(MAX_LONGITUD_VALOR, { each: true })
  color?: string[];

  @IsOptional()
  @Transform(aLista)
  @IsArray()
  @ArrayMaxSize(MAX_VALORES_POR_EJE)
  @IsString({ each: true })
  @MaxLength(MAX_LONGITUD_VALOR, { each: true })
  retailer?: string[]; // slugs de tienda

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
