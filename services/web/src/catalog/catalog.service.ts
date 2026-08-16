import { Inject, Injectable, NotFoundException } from '@nestjs/common';
import { sql, type SQL } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { variantLabel } from '../interests/interests.service';
import {
  classifyHonesty,
  honestDiscountPct,
  honestListPrice,
  honestyBasis,
  HONESTY_WINDOW_DAYS,
} from '../matching/deal-rule';
import { honestDiscountSql, isRealDealSql, type DealSqlColumns } from '../matching/deal-rule.sql';
import { GENERO_UNISEX, generoCondition } from './gender.sql';
import type {
  Facets,
  PricePoint,
  ProductDetail,
  ProductImageRef,
  ProductListItem,
  ProductListResult,
  RetailerFacet,
  VariantWithPrice,
} from './catalog.types';
import type { FacetQueryDto } from './dto/facet-query.dto';
import type { BarefootFilter, ProductQueryDto } from './dto/product-query.dto';

/** Sección donde la marca barefoot aplica. En el resto (`ropa`) la columna es NULL. */
const SECCION_CALZADO = 'zapateria';

/** La sección cuyas tallas se pliegan a bandas de edad (#325). Ver `plegadoTalla`. */
const SECCION_ROPA = 'ropa';

/**
 * Condición SQL del filtro barefoot (#30), con `alias` como alias de la tabla `product`.
 *
 * El caso por defecto (`si`) NO es "barefoot = 'si'" a secas: es **toda la ropa más el calzado
 * respetuoso**. La ropa lleva NULL porque la pregunta no le aplica, y `NULL = 'si'` es NULL, o sea
 * falso, así que un filtro ingenuo escondería el catálogo entero de ropa. `IS DISTINCT FROM` en vez
 * de `<>` por la misma razón: un producto con `section` NULL debe pasar, no evaporarse.
 */
export function barefootCondition(filter: BarefootFilter, alias: string): SQL {
  const seccion = sql.raw(`${alias}.section`);
  const marca = sql.raw(`${alias}.barefoot`);
  if (filter === 'all') return sql`true`;
  if (filter === 'si') {
    return sql`(${seccion} IS DISTINCT FROM ${SECCION_CALZADO} OR ${marca} = 'si')`;
  }
  return sql`${marca} = ${filter}`;
}

/**
 * Condición SQL de un eje transversal de `product_tag` (#180), con `alias` como alias de `product`.
 *
 * `EXISTS` y no un `JOIN`: la tabla tiene una fila por (producto, eje), así que unirla duplicaría
 * las filas del listado el día que un producto lleve dos ejes — y el listado ya agrega por producto
 * para elegir la variante representativa.
 *
 * Apagado devuelve `true` en vez de omitirse en quien llama, para que el filtro se componga igual
 * que `barefootCondition` y no haya dos formas de montar el `WHERE`.
 */
export function tagCondition(tag: string | undefined, alias: string): SQL {
  if (!tag) return sql`true`;
  const id = sql.raw(`${alias}.id`);
  return sql`EXISTS (SELECT 1 FROM product_tag pt WHERE pt.product_id = ${id} AND pt.tag = ${tag})`;
}

/** Eje que la SPA ofrece como interruptor. El vocabulario lo fija `scraper/tags.py`. */
export const TAG_DEPORTIVA = 'deportiva';

/**
 * Condición de un eje **multiseleccionable** (#329): `columna = ANY(<lo pedido>)`, o sea unión.
 *
 * `plegado` es la función de la base con la que hay que normalizar lo que llega por la query string
 * —`size_band` o `color_family`— para que siga comparándose contra lo mismo que la columna, y para
 * que los enlaces guardados con el texto crudo sigan encontrando. `null` cuando no hay que plegar
 * nada, que es el caso del slug de tienda.
 *
 * Que el plegado se aplique TAMBIÉN a lo que llega por la URL es lo que hace que un enlace viejo
 * siga funcionando: `?size=4-5 años` se pliega a la banda `4 años` y encuentra, en vez de devolver
 * vacío porque ya nadie guarda esa cadena.
 *
 * **La forma importa, no es indiferente.** El `ARRAY(SELECT ...)` es una subconsulta NO correlada:
 * Postgres la resuelve una vez como InitPlan y deja delante un `= ANY(<array constante>)`, que sigue
 * apoyándose en los índices por expresión `ix_variant_size_band` (0033) e `ix_variant_color_family`
 * (0029). Plegar dentro del `ANY` fila a fila los perdería, y lo que eso cuesta ya está medido en
 * #307: la misma consulta pasó de 1,4 ms a 1 s.
 *
 * Sin valores devuelve `true` en vez de omitirse en quien llama, para componer igual que
 * `barefootCondition` y `tagCondition` y que no haya dos formas de montar el `WHERE`.
 */
export function ejeMultiple(
  valores: string[] | null | undefined,
  columna: SQL,
  plegado: 'size_canon' | 'size_band' | 'color_family' | null,
): SQL {
  if (!valores?.length) return sql`true`;
  // `ARRAY[$1, $2, ...]` y no un solo parámetro de tipo array: en una plantilla `sql` de Drizzle un
  // array de JS se aplana en parámetros sueltos, así que `${valores}::text[]` le pasaba a Postgres
  // un escalar y reventaba con «malformed array literal».
  const lista = sql`ARRAY[${sql.join(
    valores.map((v) => sql`${v}`),
    sql`, `,
  )}]::text[]`;
  const buscados = plegado
    ? sql`ARRAY(SELECT ${sql.raw(plegado)}(x) FROM unnest(${lista}) AS x)`
    : lista;
  return sql`${columna} = ANY(${buscados})`;
}

/**
 * Con qué se pliega la talla, según la sección que se esté mirando (#325).
 *
 * **Las bandas de edad son de `ropa` y solo de `ropa`.** En `zapateria` la talla es un número de
 * pie: plegar un 26 a una banda de edad no ofrecería un filtro más corto sino uno que no filtra lo
 * que dice —y de paso resucitaría el chip «48-51 años» que #64 vino a quitar—. Ahí se sigue
 * ofreciendo la canónica, que ya es corta (76 etiquetas, y son todas del mismo vocabulario).
 *
 * Sin sección elegida también se queda en la canónica: la lista es la unión de dos vocabularios que
 * no se pueden comparar, y plegar la mitad haría el revoltijo peor, no mejor. La SPA no ofrece
 * tallas en ese estado (ver `FacetQueryDto`), así que es un caso de API, no de pantalla.
 */
function plegadoTalla(section: string | null): 'size_band' | 'size_canon' {
  return section === SECCION_ROPA ? 'size_band' : 'size_canon';
}

/**
 * Columnas de la variante "mejor oferta" ya agregada, contra las que se evalúa la honestidad en
 * SQL. Son exactamente las mismas que se le pasan a `classifyHonesty` más abajo (`list_from`
 * incluido): si aquí se colara otra columna, el filtro y la etiqueta hablarían de precios distintos.
 */
const DEAL_COLUMNS: DealSqlColumns = {
  price: sql`price_repr`,
  listPrice: sql`list_from`,
  recentMin: sql`recent_min_repr`,
  maxObserved: sql`max_observed_repr`,
  priorPoints: sql`prior_points_repr`,
  retailerMin30d: sql`retailer_min_30d_repr`,
  // Sin migración: `tracked_days_repr` existe en `product_agg` desde la 0035 y ya viajaba en la CTE
  // `agg`, solo que hasta #436 no la miraba nadie más que el TypeScript.
  trackedDays: sql`tracked_days_repr`,
};

/**
 * Plegado de texto para buscar sin distinguir mayúsculas ni acentos.
 *
 * A propósito **sin `unaccent` ni `pg_trgm`**: ambas exigen `CREATE EXTENSION`, que en la Postgres
 * HA del cluster no está garantizado para el usuario de la aplicación, y no merece la pena atar el
 * arranque del servicio a un privilegio que puede no estar. `translate()` es estándar, `IMMUTABLE`
 * y cubre el castellano, que es todo el idioma del catálogo.
 *
 * Sin índice: el catálogo son unos pocos miles de productos y la consulta ya recorre `price_history`
 * entero en la CTE `latest`, así que el plegado no es el cuello de botella. Si algún día lo fuera,
 * la salida es `pg_trgm` + índice GIN sobre esta misma expresión.
 *
 * LAS MAYÚSCULAS ACENTUADAS VAN EN LA TABLA, y no basta con el `lower()` de delante (#105). En la
 * base del cluster el ctype es `C`, y con ese ctype `lower()` **no baja las letras acentuadas**:
 * `lower('PANTALÓN')` da 'pantalÓn', la 'Ó' no está en la mitad izquierda de esta tabla y el
 * producto se queda fuera de la búsqueda. No es un caso de borde: el 02/08/2026 había **694
 * productos vivos con mayúscula acentuada en el nombre** en `dev` (zara 679, lefties 11, c-and-a 3,
 * sfera 1), y son justo los de las tiendas que escriben el nombre entero en mayúsculas.
 *
 * La mitad derecha repite el mismo alfabeto sin acentos: aquí se pliegan las dos cosas —caja y
 * acento— porque buscar «pantalon» debe encontrar «PANTALÓN». Es lo contrario de lo que hacen
 * `size_canon` y `color_canon`, que pliegan la caja y **conservan** el acento (0015 y 0021): el
 * chip de la faceta es una etiqueta que se enseña, y esto es un buscador.
 *
 * De ahí que el `translate` vaya DESPUÉS del `lower` aquí y ANTES en las dos funciones canónicas, y
 * no es un descuido: allí el plegado tiene que ocurrir antes para que las reglas de la talla vean
 * ya 'años' y no 'aÑos'; aquí basta con repescar lo que el `lower` no bajó, porque el destino es
 * ASCII de todas formas.
 */
function fold(expr: SQL): SQL {
  return sql`translate(lower((${expr})::text),
    'áàäâãéèëêíìïîóòöôõúùüûñçÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ',
    'aaaaaeeeeiiiiooooouuuuncaaaaaeeeeiiiiooooouuuunc')`;
}

/**
 * Lectura del catálogo (tablas que escribe el scraper). "Último precio" por variante se
 * resuelve con `DISTINCT ON (variant_id) ... ORDER BY scraped_at DESC` sobre `price_history`.
 */
@Injectable()
export class CatalogService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  /**
   * `forzarAgregadoVivo` es una **costura de test**, no una opción de producto: obliga a agregar en
   * tiempo de consulta aunque no haya filtro de variante. Existe porque los dos caminos de la 0035
   * tienen que devolver lo mismo, y la única forma de comprobarlo es ejecutarlos los dos sobre los
   * mismos datos — que es lo que hace `catalog-agregado-paridad.spec.ts`. El controlador nunca lo
   * pasa.
   */
  async listProducts(
    q: ProductQueryDto,
    { forzarAgregadoVivo = false }: { forzarAgregadoVivo?: boolean } = {},
  ): Promise<ProductListResult> {
    const gender = q.gender ?? null;
    const section = q.section ?? null;
    const category = q.category ?? null;
    const size = q.size ?? null;
    const sizeExact = q.sizeExact ?? null;
    const color = q.color ?? null;
    const retailer = q.retailer ?? null;
    const inStock = q.inStock ?? null;
    const onlyDeals = q.onlyDeals ?? null;
    const minPrice = q.minPrice ?? null;
    const maxPrice = q.maxPrice ?? null;

    // Búsqueda por texto: cada palabra debe aparecer en el nombre, la categoría o el género, en
    // cualquier orden ("botas niña" y "niña botas" encuentran lo mismo). El género entra porque es
    // como la gente teclea ("botas niña"), y los nombres que dan las tiendas casi nunca lo llevan.
    // `position()` en vez de `LIKE` para no tener que escapar los comodines de lo que se teclee.
    //
    // #229 entró a revisar si mezclar un eje de FILTRADO dentro de la búsqueda por texto valía lo
    // que cuesta, y la respuesta, medida sobre `deal_tracker_qa` el 14/08/2026 (16.844 productos
    // vivos), es que sí: `botas niña` devuelve **60 productos con el género dentro y 0 sin él**, y
    // `vestido niña` pasa de 121 a 1.503. O sea que el caso que lo motivó no era hipotético y sigue
    // sin serlo. Se queda, y con ello la asimetría conocida: teclear un género suelto devuelve ese
    // catálogo entero (8.692 de 16.844) sin que la barra de filtros aplicados lo refleje.
    //
    // Lo que la medida sí destapó, y no estaba escrito en ninguna parte, es que el `position()`
    // casa por SUBCADENA sobre un campo de tres valores: `ni` está dentro de `niño`, `niña` **y**
    // `unisex`, así que buscar `ni` devuelve el catálogo completo (16.844 de 16.844; sin el género
    // serían 2.426) y `nin` devuelve el 88 % (14.918). Eso es #408, y se arregla con la búsqueda
    // facetada o acotando el género a palabra completa, no aquí.
    //
    // Un falso positivo que NO existe, por si alguien viene a buscarlo: un término no puede casar a
    // caballo entre el nombre, la categoría y el género, porque las costuras son espacios y los
    // términos salen de partir por espacios.
    const terms = (q.q ?? '').split(/\s+/).filter(Boolean);
    const haystack = fold(
      sql`p.name || ' ' || coalesce(p.category, '') || ' ' || coalesce(p.gender, '')`,
    );
    const search = terms.length
      ? sql.join(
          terms.map((t) => sql`position(${fold(sql`${t}`)} in ${haystack}) > 0`),
          sql` AND `,
        )
      : sql`TRUE`;

    // Orden traducido a SQL (whitelist en el DTO). "ofertas" = la oferta **real** primero, después
    // stock, y el descuento honesto (contra el PVP creíble) como criterio; el `discount_pct` que
    // declara la tienda queda de mero desempate porque es justo el dato del que desconfiamos.
    // El id, desempate estable para que la paginación por offset no repita ni se salte filas.
    const orderBy = {
      'ofertas': sql`is_real_deal DESC, any_in_stock DESC, honest_discount DESC NULLS LAST,
                     max_discount DESC NULLS LAST, id`,
      'precio-asc': sql`price_from ASC NULLS LAST, id`,
      'precio-desc': sql`price_from DESC NULLS LAST, id`,
      'descuento': sql`max_discount DESC NULLS LAST, id`,
    }[q.sort];

    // Los tres filtros **de variante**. Todo lo demás —género, sección, categoría, tienda,
    // búsqueda, barefoot, deportiva, activeOnly— es **de producto**, y por eso se puede aplicar
    // igual de bien sobre un agregado ya hecho. Esa distinción es la que hace posible la 0035:
    // el agregado por producto solo vale mientras no se filtre por debajo del producto.
    //
    // `length` y no `!== null` para casar con `ejeMultiple`, que trata el array vacío como "sin
    // filtro": si no, un `?size=` vacío mandaría al camino lento a no filtrar nada.
    //
    // `inStock` **ya no manda al camino vivo** (0038, #371): era el único de los tres que no
    // colapsaba el conjunto —el 27,28 % de las variantes vivas están agotadas, así que deja pasar
    // tres de cada cuatro— y por eso pagaba la agregación entera, ~2,1 s contra 0,18-1,07 s del
    // resto del panel. Ahora tiene su propio ámbito precomputado.
    //
    // `inStock === false` («enséñame lo agotado») sí se queda aquí: no hay ámbito para eso y la SPA
    // no lo pide (`CatalogPage` manda `inStock: filters.inStock || undefined`).
    const filtroDeVariante =
      forzarAgregadoVivo ||
      Boolean(size?.length) ||
      Boolean(sizeExact?.length) ||
      Boolean(color?.length) ||
      inStock === false;

    // Los filtros de producto se montan **una sola vez** y los usan los dos caminos. No es estilo:
    // es lo que impide que se bifurquen, que es el riesgo que se asume al tener dos caminos.
    const filtrosDeProducto = sql`
          ${generoCondition(sql`${gender}::text`, sql.raw('p.gender'))}
          AND (${section}::text IS NULL OR p.section = ${section})
          AND (${category}::text IS NULL OR p.category = ${category})
          AND ${ejeMultiple(retailer, sql`r.slug`, null)}
          AND (${q.activeOnly} = false OR p.delisted_at IS NULL)
          AND ${search}
          AND ${barefootCondition(q.barefoot, 'p')}
          AND ${tagCondition(q.deportiva ? TAG_DEPORTIVA : undefined, 'p')}`;

    // Las mismas columnas y los mismos nombres en los dos caminos: `scored` y el SELECT de fuera
    // leen de `agg` sin saber cuál de los dos la ha producido.
    const columnasDeProducto = sql`p.id, p.retailer_id, r.slug AS retailer_slug,
               r.name AS retailer_name, p.retailer_product_id, p.name, p.gender, p.section,
               p.category, p.barefoot, p.url, p.image_url`;

    // ── Camino de siempre: agregar las variantes vivas en tiempo de consulta ──
    // Es el que se usa cuando hay un filtro de variante puesto. Ahí no duele: el filtro colapsa el
    // conjunto a unos cientos de filas antes de agregarlo.
    const agregadoVivo = sql`
      latest AS (
        SELECT DISTINCT ON (ph.variant_id)
          ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock,
          ph.retailer_min_30d, ph.scraped_at
        FROM price_history ph
        ORDER BY ph.variant_id, ph.scraped_at DESC
      ),
      stats AS (
        SELECT l.variant_id,
               MIN(h.price) FILTER (
                 WHERE h.scraped_at >= l.scraped_at - make_interval(days => ${HONESTY_WINDOW_DAYS})
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points,
               -- Días que llevamos observando la variante, de su primera observación a la última.
               -- Es lo que separa "el tachado está inflado" de "no lo puedo corroborar todavía"
               -- (#332): max_observed no es lo que la prenda ha costado jamás, es lo más caro que
               -- la hemos visto DESDE QUE LA DESCUBRIMOS, así que en una prenda descubierta ya
               -- rebajada vale su propio precio de rebaja y acusar con él es afirmar lo que no
               -- sabemos. El umbral y su porqué, en HONESTY_EVIDENCE_DAYS (deal-rule.ts).
               --
               -- l.scraped_at entra en el GROUP BY por esto, no por capricho: es la referencia
               -- contra la que se mide, y ya venía fijada por variante desde la CTE latest.
               EXTRACT(EPOCH FROM l.scraped_at - MIN(h.scraped_at)) / 86400 AS tracked_days
        FROM latest l
        JOIN price_history h ON h.variant_id = l.variant_id AND h.scraped_at < l.scraped_at
        GROUP BY l.variant_id, l.scraped_at
      ),
      matched AS (
        SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
               p.retailer_product_id, p.name, p.gender, p.section, p.category, p.barefoot, p.url,
               p.image_url,
               v.id AS variant_id, v.color, l.price, l.list_price, l.discount_pct, l.in_stock,
               l.retailer_min_30d,
               s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points,
               COALESCE(s.tracked_days, 0) AS tracked_days
        FROM product p
        JOIN retailer r ON r.id = p.retailer_id
        JOIN variant v ON v.product_id = p.id AND v.delisted_at IS NULL
        JOIN latest l ON l.variant_id = v.id
        LEFT JOIN stats s ON s.variant_id = v.id
        WHERE ${filtrosDeProducto}
          -- Talla canónica (#43): variant.size guarda el texto de la tienda, donde la misma talla
          -- aparece como '26', '26 (16,3 cm)' y '26 (16.3 cm)'. Se canonicaliza también lo que llega
          -- por query string, así que los enlaces antiguos con la talla cruda siguen vivos.
          --
          --
          -- Esta igualdad es la que justifica el índice por expresión de la migración 0014: sin él,
          -- la función se evalúa una vez por variante y esta consulta pasa de 1,4 ms a 1 segundo
          -- (medido sobre una copia de dev con 33.311 variantes).
          AND ${ejeMultiple(size, sql`${sql.raw(plegadoTalla(section))}(v.size)`, plegadoTalla(section))}
          -- Segundo piso de la talla (#367): la CONCRETA dentro de la banda. Se cruza con la de
          -- arriba en vez de sustituirla —la banda es dónde estás, esta es lo que pides dentro—, y
          -- se apoya en el mismo índice de la 0014 que la canónica de zapatería, que sigue vivo
          -- porque la 0033 no lo tocó. En zapateria los dos ejes pliegan igual y este sobra, pero
          -- no estorba: la SPA no lo manda ahí.
          AND ${ejeMultiple(sizeExact, sql`size_canon(v.size)`, 'size_canon')}
          -- Color por FAMILIA (#291, migración 0029), no por color canónico. El panel ofrecía 2.859
          -- chips —el 85,2 % compuestos tipo 'amarillo claro/bluey'— y en un móvil eso es
          -- inservible; ahora ofrece las ~19 familias que deja color_family.
          --
          -- Plegar también lo que llega por query string mantiene vivos los enlaces antiguos, igual
          -- que en la talla, con una diferencia que conviene tener presente: aquí no solo siguen
          -- vivos, se ENSANCHAN. Un ?color=azul marino guardado en un marcador pasa a devolver
          -- todos los azules. Es la consecuencia aceptada de que el filtro sea por familia.
          --
          -- El color específico NO se pierde por esto: se sigue guardando en variant.color, la
          -- tarjeta y la ficha lo siguen enseñando, y el aviso lo sigue casando por color_canon
          -- (ver la cabecera de la 0029, que explica por qué los dos "color" significan cosas
          -- distintas). Y es lo que justifica el índice ix_variant_color_family.
          AND ${ejeMultiple(color, sql`color_family(v.color)`, 'color_family')}
          AND (${inStock}::boolean IS NULL OR l.in_stock = ${inStock})
      ),
      agg AS (
        SELECT id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
               name, gender, section, category, barefoot, url, image_url,
               -- El variant_id del final de cada ORDER BY es un DESEMPATE, y no es cosmético
               -- (#314). "in_stock DESC, price ASC" no ordena del todo: un producto con varias
               -- variantes al mismo precio y mismo stock deja el [1] a merced de lo que el
               -- ejecutor entregue primero. O sea que la tarjeta podía enseñar un color y una foto
               -- distintos entre dos peticiones idénticas, sin que nada cambiara en la base.
               --
               -- Se vio al contrastar este camino con el agregado de la 0035 sobre los 16.517
               -- productos de QA: coincidían todos los agregados deterministas (price_from,
               -- list_from, discount_from, price_repr, any_in_stock, variant_count) y discrepaban
               -- 2.393 color_repr, 316 recent_min y 12 is_real_deal — todos empates. Con el
               -- desempate puesto, los dos caminos coinciden fila a fila.
               MIN(price) AS price_from,
               MAX(discount_pct) AS max_discount,
               (array_agg(list_price ORDER BY in_stock DESC, price ASC, variant_id))[1] AS list_from,
               (array_agg(discount_pct ORDER BY in_stock DESC, price ASC, variant_id))[1] AS discount_from,
               -- Estadísticos de la MISMA variante "mejor oferta" que list_from/discount_from,
               -- para clasificar la honestidad de la oferta que se muestra en la tarjeta.
               (array_agg(price ORDER BY in_stock DESC, price ASC, variant_id))[1] AS price_repr,
               (array_agg(recent_min ORDER BY in_stock DESC, price ASC, variant_id))[1] AS recent_min_repr,
               (array_agg(max_observed ORDER BY in_stock DESC, price ASC, variant_id))[1] AS max_observed_repr,
               (array_agg(prior_points ORDER BY in_stock DESC, price ASC, variant_id))[1] AS prior_points_repr,
               (array_agg(tracked_days ORDER BY in_stock DESC, price ASC, variant_id))[1] AS tracked_days_repr,
               (array_agg(retailer_min_30d ORDER BY in_stock DESC, price ASC, variant_id))[1] AS retailer_min_30d_repr,
               -- ...y su COLOR, para que la foto de la tarjeta sea la de ese mismo color y no la de
               -- otro cualquiera: el precio cuelga de la variante (talla+color), así que enseñar la
               -- foto del "primer color" junto al precio de la variante más barata puede mezclar.
               (array_agg(color ORDER BY in_stock DESC, price ASC, variant_id))[1] AS color_repr,
               BOOL_OR(in_stock) AS any_in_stock
        FROM matched
        GROUP BY id, retailer_id, retailer_slug, retailer_name, retailer_product_id,
                 name, gender, section, category, barefoot, url, image_url
      )`;

    // ── Camino precomputado: leer el agregado que ya dejó hecho la ingesta (0035, #314) ──
    //
    // Mismas columnas, mismos nombres, calculadas por `refresh_product_agg()` con el espejo exacto
    // de las cuatro CTE de arriba. Lo que se ahorra es agregar el catálogo entero antes del LIMIT:
    // ~16.000 filas ya hechas en vez de 163.509 por ordenar y agrupar en cada petición.
    //
    // Un producto sin variantes vivas con histórico no tiene fila aquí, que es exactamente lo que
    // le pasaba en el camino de siempre: los JOIN lo dejaban fuera. La ausencia significa lo mismo.
    const agregadoPrecomputado = sql`
      agg AS (
        -- El MISMO orden de columnas que el camino de siempre, no solo los mismos nombres: es lo
        -- que permite contrastar los dos caminos con un EXCEPT, que es como se ha verificado esto
        -- contra los 16.517 productos de QA.
        SELECT ${columnasDeProducto},
               pa.price_from, pa.max_discount, pa.list_from, pa.discount_from,
               pa.price_repr, pa.recent_min_repr, pa.max_observed_repr, pa.prior_points_repr,
               pa.tracked_days_repr, pa.retailer_min_30d_repr, pa.color_repr, pa.any_in_stock
        FROM product p
        JOIN retailer r ON r.id = p.retailer_id
        JOIN product_agg pa ON pa.product_id = p.id
        -- El ámbito NO es opcional: product_agg tiene una fila por producto y ámbito desde la
        -- 0038, así que sin este predicado cada producto sale dos veces. Va en el JOIN y no en el
        -- WHERE para que no haya forma de perderlo al tocar filtrosDeProducto.
        AND pa.scope = ${inStock === true ? 'con_stock' : 'todas'}
        WHERE ${filtrosDeProducto}
      )`;

    const rows = await this.db.execute(sql`
      WITH ${filtroDeVariante ? agregadoVivo : agregadoPrecomputado},
      -- La honestidad se decide sobre las columnas *_repr, que solo existen tras el GROUP BY, así
      -- que va en su propia CTE: desde aquí ya se puede filtrar y ordenar por ella antes del
      -- LIMIT, que es justo lo que el TypeScript, evaluado sobre la página ya recortada, no puede.
      scored AS (
        SELECT agg.*,
               ${isRealDealSql(DEAL_COLUMNS)}   AS is_real_deal,
               ${honestDiscountSql(DEAL_COLUMNS)} AS honest_discount
        FROM agg
      )
      SELECT scored.*,
             -- Los ejes transversales van en el SELECT de fuera, después del LIMIT, para que la
             -- subconsulta se evalúe sobre la página y no sobre el catálogo entero. Un ARRAY vacío
             -- es lo normal: hoy solo hay un eje y solo lo alimentan tres tiendas.
             ARRAY(SELECT pt.tag FROM product_tag pt
                    WHERE pt.product_id = scored.id ORDER BY pt.tag) AS tags,
             -- Prendas comprables, no filas: la misma clave con la que la ficha colapsa las caras
             -- duplicadas (#108). Sin esto, un producto de Lefties con las 22 tallas publicadas dos
             -- veces declara 44 variantes. Los coalesce evitan que una fila con talla, color y URL a
             -- NULL forme una ROW toda nula, que COUNT no contaría.
             --
             -- Vive aquí fuera —después del LIMIT, como los ejes transversales— y no dentro de
             -- agg, por lo que midió #307: ahí este COUNT(DISTINCT ROW(...)) obliga a ordenar TODAS
             -- las variantes vivas por un valor calculado (159.037 en prod, con derrame a disco) y
             -- la petición sin filtros tardaba 24 s en vez de 0,33 s. Con cualquier filtro puesto no
             -- se notaba, porque matched colapsa a unos cientos de filas.
             --
             -- Al salir de matched hay que repetir sus filtros DE VARIANTE, que son los únicos que
             -- cambian el recuento; los de producto (género, sección, categoría, tienda, búsqueda,
             -- barefoot, deportiva) no lo tocan. El delisted_at IS NULL va siempre: activeOnly solo
             -- levanta el filtro del producto, nunca el de la variante.
             --
             -- El "ORDER BY ... LIMIT 1" es el espejo por variante del CTE latest, y la duplicación
             -- es a sabiendas: correlar contra latest cuesta 603 ms por página frente a los 16 ms de
             -- esta forma, porque un CTE materializado no tiene índice y se recorre entero una vez
             -- por fila. Lo que sujeta que las dos digan lo mismo es el test de inStock sobre el
             -- fixture de dos SKU.
             (SELECT COUNT(DISTINCT (coalesce(size_canon(v2.size), ''),
                                     coalesce(color_canon(v2.color), ''),
                                     coalesce(COALESCE(v2.url, scored.url), '')))
                FROM variant v2
               WHERE v2.product_id = scored.id
                 AND v2.delisted_at IS NULL
                 AND EXISTS (SELECT 1 FROM price_history ph WHERE ph.variant_id = v2.id)
                 AND ${ejeMultiple(size, sql`${sql.raw(plegadoTalla(section))}(v2.size)`, plegadoTalla(section))}
                 -- El segundo piso de la talla (#367) también, y por el mismo motivo que la línea
                 -- de abajo: este recuento tiene que repetir TODOS los filtros de variante o
                 -- declara comprables prendas que el filtro puesto ya ha descartado. Es el fallo
                 -- que #326 arregló en el color, un eje más tarde.
                 AND ${ejeMultiple(sizeExact, sql`size_canon(v2.size)`, 'size_canon')}
                 -- Por FAMILIA, igual que el WHERE de matched (#326). Se quedó en color_canon
                 -- cuando #291 movió el filtro a color_family, y eso hacía que un producto que el
                 -- catálogo devuelve por la familia 'azul' declarase CERO prendas comprables si
                 -- ninguna de sus variantes se llamaba exactamente 'azul': 2.012 de los 3.063 que
                 -- devuelve ese filtro, medidos sobre una copia de dev. No se veía porque la SPA no
                 -- pinta variantCount en ninguna parte, y los tests usaban colores de una sola
                 -- palabra, donde las dos funciones coinciden.
                 AND ${ejeMultiple(color, sql`color_family(v2.color)`, 'color_family')}
                 AND (${inStock}::boolean IS NULL
                      OR (SELECT ph.in_stock FROM price_history ph
                           WHERE ph.variant_id = v2.id
                           ORDER BY ph.scraped_at DESC LIMIT 1) = ${inStock})
             ) AS variant_count
        FROM scored
      -- El rango de precio (#290) va AQUÍ, junto a onlyDeals: price_from es un MIN() que solo
      -- existe tras el GROUP BY de agg, y filtrarlo después del LIMIT —o peor, en el servicio
      -- sobre la página ya recortada— rompería la paginación por offset, que es el mismo argumento
      -- ya escrito en #228. Extremos incluidos: quien pide "hasta 20 EUR" espera ver los de 20.
      WHERE (${onlyDeals}::boolean IS NOT TRUE OR is_real_deal)
        AND (${minPrice}::numeric IS NULL OR price_from >= ${minPrice})
        AND (${maxPrice}::numeric IS NULL OR price_from <= ${maxPrice})
      ORDER BY ${orderBy}
      LIMIT ${q.limit} OFFSET ${q.offset}
    `);

    const items: ProductListItem[] = (rows as unknown as Record<string, unknown>[]).map((row) => {
      // Las mismas entradas para las tres preguntas —qué veredicto, contra qué PVP y cuánto
      // descuento se sostiene—, montadas una sola vez, igual que en la ficha: si se construyeran por
      // separado podrían divergir y la tarjeta acabaría etiquetando con unos precios y pintando
      // otros, que es media #436.
      const entrada = {
        price: (row.price_repr as string | null) ?? null,
        listPrice: (row.list_from as string | null) ?? null,
        recentMin: (row.recent_min_repr as string | null) ?? null,
        maxObserved: (row.max_observed_repr as string | null) ?? null,
        retailerMin30d: (row.retailer_min_30d_repr as string | null) ?? null,
        priorPoints: Number(row.prior_points_repr ?? 0),
        trackedDays: Number(row.tracked_days_repr ?? 0),
        minDiscountPct: 0,
        compareBase: 'recent_min' as const,
      };
      const honesto = honestListPrice(entrada.listPrice, entrada.maxObserved, entrada.retailerMin30d);
      return {
      id: Number(row.id),
      retailerId: Number(row.retailer_id),
      retailerSlug: String(row.retailer_slug),
      retailerName: String(row.retailer_name),
      retailerProductId: String(row.retailer_product_id),
      name: String(row.name),
      gender: (row.gender as string | null) ?? null,
      section: (row.section as string | null) ?? null,
      category: (row.category as string | null) ?? null,
      barefoot: (row.barefoot as string | null) ?? null,
      tags: (row.tags as string[] | null) ?? [],
      url: (row.url as string | null) ?? null,
      imageUrl: (row.image_url as string | null) ?? null,
      colorRepr: (row.color_repr as string | null) ?? null,
      priceFrom: (row.price_from as string | null) ?? null,
      listFrom: (row.list_from as string | null) ?? null,
      discountFrom: (row.discount_from as string | null) ?? null,
      maxDiscount: (row.max_discount as string | null) ?? null,
      // Se calcula en TypeScript y no se lee de la CTE `scored` a propósito, aunque `honest_discount`
      // ya esté ahí para ordenar: lo que la tarjeta pinta tiene que salir de la MISMA llamada que la
      // etiqueta, no de un espejo que puede derivar. El espejo SQL sigue siendo el que filtra y
      // ordena, y `deal-rule-paridad.spec.ts` es quien vigila que los dos digan lo mismo.
      honestListPrice: honesto === null ? null : honesto.toFixed(2),
      honestDiscountPct: honestDiscountPct(entrada.price, honesto),
      honesty: classifyHonesty(entrada),
      anyInStock: Boolean(row.any_in_stock),
      variantCount: Number(row.variant_count),
      };
    });

    await this.applyReprImages(items);
    return { items, limit: q.limit, offset: q.offset };
  }

  /**
   * Sustituye `imageUrl` por la foto del color de la variante "mejor oferta", cuando la hay.
   *
   * Va en una segunda consulta y no como JOIN dentro de la query grande a propósito: aquí está
   * acotada a los productos de UNA página (`limit`), mientras que dentro de `matched` se pagaría
   * por cada fila variante×precio de todo el catálogo filtrado. `product.image_url` sigue siendo
   * el respaldo para las fichas que aún no tienen galería (la estrenan con el refresco del detalle).
   */
  private async applyReprImages(items: ProductListItem[]): Promise<void> {
    const wanted = items.filter((it) => it.colorRepr !== null);
    if (wanted.length === 0) return;

    // Dos trampas juntas al pasar arrays: `sql.param()` es obligatorio, porque un array suelto en
    // una plantilla de drizzle se expande a N parámetros sueltos (`$1, $2`) y el cast a `bigint[]`
    // se queja de literal malformado; y los ids van como TEXTO, porque postgres.js no sabe
    // serializar un array de números (falla en el Bind). Postgres los castea sin problema.
    const ids = wanted.map((it) => String(it.id));
    const colors = wanted.map((it) => it.colorRepr as string);
    const rows = (await this.db.execute(sql`
      SELECT i.product_id, i.url
      FROM unnest(${sql.param(ids)}::bigint[], ${sql.param(colors)}::text[])
             AS want(product_id, color)
      JOIN product_image i
        ON i.product_id = want.product_id AND i.color = want.color AND i.position = 0
    `)) as unknown as Record<string, unknown>[];

    const byProduct = new Map(rows.map((r) => [Number(r.product_id), String(r.url)]));
    for (const item of items) {
      const url = byProduct.get(item.id);
      if (url) item.imageUrl = url;
    }
  }

  async getProduct(id: number): Promise<ProductDetail> {
    const [head] = (await this.db.execute(sql`
      SELECT p.id, p.retailer_id, r.slug AS retailer_slug, r.name AS retailer_name,
             p.retailer_product_id, p.name, p.gender, p.section, p.category, p.barefoot, p.url,
             p.image_url,
             ARRAY(SELECT pt.tag FROM product_tag pt
                    WHERE pt.product_id = p.id ORDER BY pt.tag) AS tags
      FROM product p
      JOIN retailer r ON r.id = p.retailer_id
      WHERE p.id = ${id}
    `)) as unknown as Record<string, unknown>[];

    if (!head) {
      throw new NotFoundException(`Producto ${id} no encontrado`);
    }

    const variantRows = (await this.db.execute(sql`
      WITH latest AS (
        SELECT DISTINCT ON (ph.variant_id)
          ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock,
          ph.retailer_min_30d, ph.scraped_at
        FROM price_history ph
        ORDER BY ph.variant_id, ph.scraped_at DESC
      ),
      stats AS (
        SELECT l.variant_id,
               MIN(h.price) FILTER (
                 WHERE h.scraped_at >= l.scraped_at - make_interval(days => ${HONESTY_WINDOW_DAYS})
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points,
               -- Mismo tracked_days que el listado, y por el mismo motivo (#332): aquí además se
               -- devuelve al cliente, porque la ficha de una prenda "unverified" dice cuántos días
               -- llevamos siguiéndola en vez de acusar a la tienda.
               EXTRACT(EPOCH FROM l.scraped_at - MIN(h.scraped_at)) / 86400 AS tracked_days
        FROM latest l
        JOIN price_history h ON h.variant_id = l.variant_id AND h.scraped_at < l.scraped_at
        GROUP BY l.variant_id, l.scraped_at
      ),
      -- Una fila por PRENDA COMPRABLE, no por variante (#108). Lefties, H&M e Hipercor publican
      -- la misma talla y color con dos SKU distintos: los dos son reales y estables, así que
      -- entran los dos en la base, pero la ficha tiene que enseñar una sola fila o el usuario ve
      -- la misma talla dos veces y el precio que se pinta puede ser el de la cara agotada.
      --
      -- La URL entra en la clave a propósito: es lo que separa las dos caras de Lefties o
      -- Hipercor —que comparten ficha en la tienda, así que colapsarlas no le quita al usuario
      -- ningún sitio al que ir— de los dos ARTÍCULOS distintos que H&M publica con el mismo
      -- modelo y el mismo nombre de color, cada uno con su propia ficha (medido en dev el
      -- 03/08/2026: 803 grupos así). Añadirla solo puede partir grupos, nunca unirlos.
      --
      -- Se agrupa por coalesce(v.url, '') y no por coalesce(v.url, p.url) porque aquí todas las
      -- filas son del
      -- mismo producto: el respaldo sería el mismo para todas y solo hace falta que los NULL
      -- caigan juntos.
      --
      -- La baja también parte el grupo: una cara dada de baja no debe absorber a una viva.
      --
      -- Cuántas medidas en cm DISTINTAS publica la tienda bajo cada talla canónica de este
      -- producto (#331). Es la mitad que size_canon no puede saber: la función ve una cadena
      -- suelta, y aquí la diferencia está en el conjunto.
      --
      --     '9-10 años' | '9-10 años - Medida 128 cm'   -> {128}     n=1  -> MISMA talla
      --     '12 Meses'  | '12 meses'                    -> {}        n=0  -> MISMA talla
      --     '3 meses - Medida 62 cm'
      --       | '3 meses/6 meses - Medida 68 cm'        -> {62,68}   n=2  -> DOS tallas
      --
      -- count(DISTINCT ...) ignora los NULL, y ESO es la regla: que una de las dos formas no
      -- traiga medida es justo lo que dice que no discrimina. Sin ese detalle, partir por el texto
      -- crudo llevaba la ficha del «Pack 5 slips» de Hipercor de 7 chips a 14 (medido).
      medidas AS (
        SELECT size_canon(v.size) AS canon, count(DISTINCT size_cm(v.size)) AS n
        FROM variant v
        WHERE v.product_id = ${id} AND v.delisted_at IS NULL
        GROUP BY size_canon(v.size)
      ),
      prenda AS (
        SELECT (array_agg(v.id ORDER BY l.in_stock DESC NULLS LAST, l.price ASC NULLS LAST, v.id))[1]
                 AS variant_id,
               -- La disponibilidad real de la talla es el OR de las dos caras: en 387 grupos de
               -- Lefties una está a la venta y la otra no.
               BOOL_OR(l.in_stock) AS in_stock
        FROM variant v
        LEFT JOIN latest l ON l.variant_id = v.id
        LEFT JOIN medidas m ON m.canon IS NOT DISTINCT FROM size_canon(v.size)
        WHERE v.product_id = ${id}
        GROUP BY size_canon(v.size),
                 -- Solo cuando de verdad hay dos medidas: si no, el CASE da NULL para todas las
                 -- filas del grupo y la clave queda exactamente como estaba.
                 CASE WHEN m.n > 1 THEN size_cm(v.size) END,
                 color_canon(v.color), coalesce(v.url, ''),
                 (v.delisted_at IS NULL)
      )
      SELECT v.id, v.retailer_variant_id, v.size, size_canon(v.size) AS size_canon,
             -- Lo que rotula el chip (#331). La canónica, y la medida SOLO cuando este producto
             -- publica dos bajo la misma etiqueta — que es cuando el padre la necesita para
             -- elegir. En los otros 16.482 productos del catálogo sale la canónica sola.
             CASE WHEN m.n > 1 AND size_cm(v.size) IS NOT NULL
                  THEN size_canon(v.size) || ' · ' || size_cm(v.size) || ' cm'
                  ELSE size_canon(v.size)
             END AS size_label,
             v.color, v.sku, v.url, v.delisted_at,
             l.price, l.list_price, l.discount_pct, g.in_stock, l.scraped_at,
             l.retailer_min_30d,
             s.recent_min, s.max_observed, COALESCE(s.prior_points, 0) AS prior_points,
             COALESCE(s.tracked_days, 0) AS tracked_days
      FROM prenda g
      JOIN variant v ON v.id = g.variant_id
      LEFT JOIN latest l ON l.variant_id = v.id
      LEFT JOIN stats s ON s.variant_id = v.id
      LEFT JOIN medidas m ON m.canon IS NOT DISTINCT FROM size_canon(v.size)
      ORDER BY v.id
    `)) as unknown as Record<string, unknown>[];

    const variants: VariantWithPrice[] = variantRows.map((row) => {
      // Las mismas entradas para las tres preguntas —qué veredicto, en qué se apoya y contra qué PVP
      // se mide—, montadas una sola vez: si se construyeran por separado podrían divergir sin que
      // nada lo dijera.
      const entrada = {
        price: (row.price as string | null) ?? null,
        listPrice: (row.list_price as string | null) ?? null,
        recentMin: (row.recent_min as string | null) ?? null,
        maxObserved: (row.max_observed as string | null) ?? null,
        retailerMin30d: (row.retailer_min_30d as string | null) ?? null,
        priorPoints: Number(row.prior_points ?? 0),
        trackedDays: Number(row.tracked_days ?? 0),
        minDiscountPct: 0,
        compareBase: 'recent_min' as const,
      };
      const honesto = honestListPrice(entrada.listPrice, entrada.maxObserved, entrada.retailerMin30d);
      return {
      id: Number(row.id),
      retailerVariantId: String(row.retailer_variant_id),
      // La talla sale CRUDA a propósito, y no es un descuido pendiente de arreglar (#248): es el
      // texto que pinta el selector de tallas de la ficha, y en ropa infantil el paréntesis que
      // `size_canon` borra —'2 años (92 cm)' -> '2 años', ver la 0024— es justo por lo que un padre
      // elige. La canónica no se pierde: viaja en `variantLabel`, aquí abajo.
      size: (row.size as string | null) ?? null,
      // Ya se calculaba aquí abajo para `variantLabel`; desde #297 sale también como campo propio,
      // porque la SPA compone la etiqueta por su cuenta para capitalizar el color.
      sizeCanon: (row.size_canon as string | null) ?? null,
      // Lo que se ENSEÑA (#331): la canónica, más la medida en cm solo si este producto publica
      // dos tallas físicas bajo la misma etiqueta. `size` sigue siendo la clave con la que la SPA
      // selecciona, y `sizeCanon` lo que se guarda en el interés; esto es solo el rótulo.
      sizeLabel: (row.size_label as string | null) ?? null,
      color: (row.color as string | null) ?? null,
      sku: (row.sku as string | null) ?? null,
      url: (row.url as string | null) ?? null,
      delisted: row.delisted_at != null,
      price: (row.price as string | null) ?? null,
      listPrice: (row.list_price as string | null) ?? null,
      discountPct: (row.discount_pct as string | null) ?? null,
      inStock: row.in_stock == null ? null : Boolean(row.in_stock),
      scrapedAt: row.scraped_at ? new Date(row.scraped_at as string).toISOString() : null,
      // La MISMA función que nombra la variante en `/seguimientos` y en el aviso de Telegram, con
      // la talla canónica que calcula la base: es lo que impide que el modal de «Seguir esta
      // variante» confirme una talla y la lista enseñe otra (#248). El color va crudo, como en los
      // otros dos llamantes — `color_canon` devuelve NULL para lo que no reconoce (#51), así que
      // canonizarlo aquí lo borraría de la etiqueta en vez de normalizarlo.
      variantLabel: variantLabel(
        (row.size_canon as string | null) ?? null,
        (row.color as string | null) ?? null,
      ),
      // Días que llevamos siguiendo esta prenda. Solo sale en la ficha —la tarjeta no lo pinta—, y
      // está para que el texto de una `unverified` diga lo que sabemos en vez de acusar (#332).
      trackedDays: Math.floor(Number(row.tracked_days ?? 0)),
      // El mínimo de 30 días que declara la tienda (#354). Sale a la ficha porque el texto de una
      // acusación `declarado` lo cita —«la propia tienda dice haberla vendido a 4,24 €»—, que es lo
      // que la convierte en una afirmación comprobable en vez de una etiqueta.
      retailerMin30d: (row.retailer_min_30d as string | null) ?? null,
      // El PVP que sí podemos sostener, y el descuento contra él (#436). La ficha los enseña en
      // lugar del tachado de la tienda cuando la regla ha descartado ese tachado.
      honestListPrice: honesto === null ? null : honesto.toFixed(2),
      honestDiscountPct: honestDiscountPct(entrada.price, honesto),
      honesty: classifyHonesty(entrada),
      honestyBasis: honestyBasis(entrada),
      };
    });

    // Galería completa: la ficha la filtra por el color seleccionado, para que la foto cambie a
    // la vez que el precio. `color NULLS FIRST` deja delante las fotos sin color atribuible, que
    // son las que sirven de respaldo cuando el color elegido no tiene ninguna.
    //
    // `variant_url` (0023, #123) es el segundo eje del filtro: en H&M el nombre del color no
    // identifica la prenda, porque un producto nuestro junta varios artículos de la tienda y dos
    // pueden compartir `colorName`. Va a NULL en las otras seis tiendas y en todo lo ingerido
    // antes de la 0023, y la ficha tiene una cadena de respaldo para eso.
    const imageRows = (await this.db.execute(sql`
      SELECT color, url, variant_url
      FROM product_image
      WHERE product_id = ${id}
      ORDER BY color NULLS FIRST, position
    `)) as unknown as Record<string, unknown>[];

    const images: ProductImageRef[] = imageRows.map((row) => ({
      color: (row.color as string | null) ?? null,
      url: String(row.url),
      variantUrl: (row.variant_url as string | null) ?? null,
    }));

    return {
      id: Number(head.id),
      retailerId: Number(head.retailer_id),
      retailerSlug: String(head.retailer_slug),
      retailerName: String(head.retailer_name),
      retailerProductId: String(head.retailer_product_id),
      name: String(head.name),
      gender: (head.gender as string | null) ?? null,
      section: (head.section as string | null) ?? null,
      category: (head.category as string | null) ?? null,
      // La ficha SÍ enseña el calzado no respetuoso: el filtro de #30 acota lo que se ofrece en el
      // catálogo, no censura un enlace directo. Devolver la marca deja que la ficha lo advierta.
      barefoot: (head.barefoot as string | null) ?? null,
      tags: (head.tags as string[] | null) ?? [],
      url: (head.url as string | null) ?? null,
      imageUrl: (head.image_url as string | null) ?? null,
      variants,
      images,
    };
  }

  async getPriceHistory(variantId: number): Promise<PricePoint[]> {
    const [exists] = (await this.db.execute(
      sql`SELECT 1 AS ok FROM variant WHERE id = ${variantId}`,
    )) as unknown as Record<string, unknown>[];
    if (!exists) {
      throw new NotFoundException(`Variante ${variantId} no encontrada`);
    }

    const rows = (await this.db.execute(sql`
      SELECT price, list_price, discount_pct, in_stock, scraped_at
      FROM price_history
      WHERE variant_id = ${variantId}
      ORDER BY scraped_at ASC
    `)) as unknown as Record<string, unknown>[];

    return rows.map((row) => ({
      price: String(row.price),
      listPrice: (row.list_price as string | null) ?? null,
      discountPct: (row.discount_pct as string | null) ?? null,
      inStock: Boolean(row.in_stock),
      scrapedAt: new Date(row.scraped_at as string).toISOString(),
    }));
  }

  /**
   * Valores disponibles para los filtros: los chips que el panel puede ofrecer sin mentir.
   *
   * Desde #292 **se cruzan con los filtros activos**. Antes solo se acotaban por `barefoot`,
   * `section` y el eje `deportiva`, así que el panel ofrecía tallas y colores que no existen dentro
   * de la categoría ya elegida: se pinchaba un chip y el catálogo salía vacío, que es literalmente
   * lo que reportó la issue. Medido sobre la copia de dev, en `ropa`: de las **165** tallas que
   * ofrecía, al elegir una categoría solo **82** devuelven algo, y con género y color puestos quedan
   * **65**. La mitad larga de los chips era una promesa falsa.
   *
   * **Cada faceta omite su propio eje.** La lista de tallas se acota por categoría, color, tienda,
   * género y búsqueda, pero NO por la talla ya elegida — si lo hiciera quedaría esa sola talla y no
   * habría manera de cambiar de idea sin limpiar el filtro. Es la regla clásica del filtrado por
   * facetas, y es lo único que lo hace usable.
   *
   * **`sections` es la excepción: no la acota nada.** Es el eje de navegación con el que se sale de
   * la vista, y desde #292 también lo que eligen las pestañas Ropa/Zapatería del grupo de talla:
   * unas pestañas que desaparecen según lo que haya filtrado serían una trampa.
   *
   * Los filtros de VARIANTE (talla y color) se aplican **a la misma fila de variante**, no por
   * separado: una prenda cuenta como "azul en 4 años" si tiene una variante que es las dos cosas, no
   * si tiene una azul y otra de 4 años. Es la misma semántica que `matched` en `listProducts`, y
   * cualquier otra haría que la faceta prometiera lo que el listado luego no devuelve.
   *
   * Lo que NO cruza —`inStock`, `onlyDeals` y el rango de precio— y por qué, en la cabecera de
   * `CatalogFilterDto`: es una frontera de coste medida, no un olvido.
   */
  async getFacets(q: FacetQueryDto): Promise<Facets> {
    const gender = q.gender ?? null;
    const section = q.section ?? null;
    const category = q.category ?? null;
    const size = q.size ?? null;
    const sizeExact = q.sizeExact ?? null;
    const color = q.color ?? null;
    const retailer = q.retailer ?? null;

    // Mismo plegado y misma forma que la búsqueda del listado, a propósito: si el buscador y la
    // faceta no entendieran lo tecleado igual, el panel volvería a ofrecer chips vacíos. Por eso el
    // género también entra aquí, y por eso el porqué —con lo que se midió en #229— está escrito una
    // sola vez, en `listProducts`: son el mismo criterio y tienen que moverse juntos.
    const terms = (q.q ?? '').split(/\s+/).filter(Boolean);
    const haystack = fold(
      sql`p.name || ' ' || coalesce(p.category, '') || ' ' || coalesce(p.gender, '')`,
    );
    const search = terms.length
      ? sql.join(
          terms.map((t) => sql`position(${fold(sql`${t}`)} in ${haystack}) > 0`),
          sql` AND `,
        )
      : sql`TRUE`;

    /** El eje que la faceta que se está pidiendo NO debe acotarse a sí misma. */
    type Eje = 'gender' | 'category' | 'size' | 'sizeExact' | 'color' | 'retailer';

    /** Filtros de VARIANTE sobre el alias que se pase, omitiendo el eje pedido. */
    const deVariante = (alias: string, excepto: Eje | null): SQL[] => {
      const a = sql.raw(alias);
      const cs: SQL[] = [];
      if (size?.length && excepto !== 'size') {
        const pliegue = plegadoTalla(section);
        cs.push(ejeMultiple(size, sql`${sql.raw(pliegue)}(${a}.size)`, pliegue));
      }
      // La banda SÍ acota la lista de tallas concretas —es de lo que va el segundo piso—, pero la
      // concreta no se acota a sí misma: si lo hiciera, marcar `104` dejaría la lista en `104` y no
      // habría forma de añadir `4-5 años` sin quitar antes lo puesto. Es el mismo motivo por el que
      // `pickSizes` se pide con `excepto: 'size'`.
      if (sizeExact?.length && excepto !== 'sizeExact') {
        cs.push(ejeMultiple(sizeExact, sql`size_canon(${a}.size)`, 'size_canon'));
      }
      if (color?.length && excepto !== 'color') {
        cs.push(ejeMultiple(color, sql`color_family(${a}.color)`, 'color_family'));
      }
      return cs;
    };

    /** Filtros de PRODUCTO, omitiendo el eje pedido. `p` y `r` tienen que estar en la consulta. */
    const deProducto = (excepto: Eje | null): SQL[] => {
      const cs: SQL[] = [
        barefootCondition(q.barefoot, 'p'),
        tagCondition(q.deportiva ? TAG_DEPORTIVA : undefined, 'p'),
        search,
        // La sección acota SIEMPRE (nunca es el eje omitido): es la que separa dos vocabularios de
        // talla que además se solapan —36 formas coinciden significando cosas distintas—, así que
        // mezclarlas no es ruido, es error.
        sql`(${section}::text IS NULL OR p.section = ${section})`,
      ];
      if (excepto !== 'gender') cs.push(generoCondition(sql`${gender}::text`, sql.raw('p.gender')));
      if (excepto !== 'category') {
        cs.push(sql`(${category}::text IS NULL OR p.category = ${category})`);
      }
      if (excepto !== 'retailer') cs.push(ejeMultiple(retailer, sql`r.slug`, null));
      return cs;
    };

    /**
     * El `WHERE` completo de una faceta.
     *
     * `aliasVariante` distingue los dos casos: si la consulta ya recorre variantes (tallas, colores)
     * las condiciones de variante van en línea sobre esa fila; si solo mira productos (género,
     * categoría, tienda) van dentro de un `EXISTS`, que es lo que las mantiene en la MISMA variante.
     * El `EXISTS` solo se emite si hay algo que meterle: sin filtro de talla ni de color sería una
     * subconsulta por producto a cambio de nada.
     */
    const donde = (excepto: Eje | null, aliasVariante?: string): SQL => {
      const cs = deProducto(excepto);
      if (aliasVariante) {
        cs.push(...deVariante(aliasVariante, excepto));
      } else {
        const vs = deVariante('vx', excepto);
        if (vs.length) {
          cs.push(sql`EXISTS (SELECT 1 FROM variant vx
                               WHERE vx.product_id = p.id AND vx.delisted_at IS NULL
                                 AND ${sql.join(vs, sql` AND `)})`);
        }
      }
      return sql.join(cs, sql` AND `);
    };

    /**
     * Columnas de `product` que se ofrecen como chip.
     *
     * `sinAcotar` es solo para `sections`, el eje de navegación: devolver únicamente la sección
     * elegida dejaría a la SPA sin las pestañas con las que se sale de ella, y sin las del grupo de
     * talla.
     */
    const pick = async (
      column: 'gender' | 'section' | 'category',
      excepto: Eje | null,
      sinAcotar = false,
    ): Promise<string[]> => {
      const scope = sinAcotar
        ? sql`(${barefootCondition(q.barefoot, 'p')} AND ${tagCondition(
            q.deportiva ? TAG_DEPORTIVA : undefined,
            'p',
          )})`
        : donde(excepto);
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT ${sql.raw(`p.${column}`)} AS value
        FROM product p
        JOIN retailer r ON r.id = p.retailer_id
        WHERE ${sql.raw(`p.${column}`)} IS NOT NULL AND p.delisted_at IS NULL
          AND ${scope}
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    /**
     * Tallas: **BANDAS DE EDAD** distintas entre variantes vivas de productos activos (#325),
     * ordenadas por talla y no alfabéticamente (así el desplegable no pone '19' entre '11-12 años'
     * y '2 años').
     *
     * Devolvía la talla CANÓNICA, y eran **181 chips** en `ropa` —cinco vocabularios mezclados,
     * porque cada tienda mide a su manera—. `size_band` (migración 0033) los pliega a **21**:
     * 18 bandas de edad más `Por número`, `Por letra` y `Otras`.
     *
     * Es el mismo movimiento que #291 hizo con el color, y por el mismo motivo: el filtro tiene que
     * **filtrar de verdad**, así que la banda vive en la base y no en el frontend.
     *
     * Las tres que no son edad salen al final **sin ordenarlas a mano**: `size_sort` (0014) manda al
     * 9999 lo que no lleva número, que es exactamente para lo que lo dejó escrito.
     *
     * ⚠️ Lo que el chip significa YA NO es lo que se guarda al seguir una prenda. El chip es una
     * banda; `interest.size` sigue siendo la talla canónica exacta, igual que `interest.color` se
     * quedó en el color canónico cuando el filtro pasó a familias (0029). Es la misma asimetría
     * deliberada: el filtro existe para encontrar, el aviso para no mentir.
     */
    const pickSizes = async (): Promise<string[]> => {
      // `crudas` deduplica el TEXTO de la tienda antes de plegar. Medido sobre la copia de
      // dev (33.311 variantes): plegar fila a fila tarda 866 ms y así 13 ms, porque la función
      // pasa de ~32.000 llamadas a las ~70 formas distintas que existen de verdad. En el cluster,
      // que son Raspberry Pi, esa diferencia es la que decide si el panel de filtros abre al
      // instante o no.
      //
      // Importa MÁS desde #325, no menos: `size_band` llama a `size_canon` por dentro, así que
      // cada evaluación cuesta las dos. Por eso la CTE se queda donde estaba en vez de plegar
      // sobre la columna — que es lo contrario de lo que hizo `pickColors` en #327, y la
      // diferencia es que allí la familia está materializada y aquí la banda no.
      //
      // El ORDER BY va fuera porque Postgres exige que sus expresiones estén en la lista del
      // SELECT DISTINCT, y `size_sort(...)` no pinta como chip.
      //
      // Sobre la tentación de poner aquí un `AS MATERIALIZED`, ver la nota de `pickColors`: se
      // probó, y es una optimización que depende de la máquina.
      const rows = (await this.db.execute(sql`
        WITH crudas AS (
          SELECT DISTINCT v.size AS cruda
          FROM variant v
          JOIN product p ON p.id = v.product_id
          JOIN retailer r ON r.id = p.retailer_id
          WHERE v.size IS NOT NULL
            AND v.delisted_at IS NULL AND p.delisted_at IS NULL
            AND ${donde('size', 'v')}
        )
        SELECT value FROM (SELECT DISTINCT ${sql.raw(plegadoTalla(section))}(cruda) AS value FROM crudas) t
        ORDER BY size_sort(value), value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    /**
     * Tallas CONCRETAS dentro de la banda ya elegida — el segundo piso del filtro (#367).
     *
     * Es la contrapartida que #325 dejó anotada al plegar la talla a 21 bandas: quien quiere
     * `4-5 años` de una tienda concreta, y no toda la banda de 4, no lo podía pedir. Medido contra
     * `deal_tracker_qa` el 16/08/2026, la banda `4 años` contiene exactamente cuatro valores —
     * `4-5 años` (2.407 productos), `4 años` (2.233), `4-6 años` (1.031) y `104` (428)—, así que
     * desplegarla es una lista corta y no otro panel.
     *
     * **Vacía mientras no haya banda elegida, y eso es el diseño, no una optimización.** Es lo que
     * hace que el filtro sea de dos pasos: sin banda, `ropa` tiene 181 tallas concretas y ofrecerlas
     * todas sería deshacer #325. El color no tiene equivalente por esto mismo llevado al extremo: la
     * familia «azul» contiene 466 concretos incluso después de elegirla (#444).
     *
     * **Vacía también en `zapateria`**, donde `plegadoTalla` ya devuelve la canónica: allí el primer
     * piso ES el concreto y un segundo piso repetiría la misma lista.
     *
     * Se pide con `excepto: 'sizeExact'` para que marcar un valor no colapse la lista a ese valor.
     */
    const pickSizeValues = async (): Promise<string[]> => {
      if (plegadoTalla(section) !== 'size_band' || !size?.length) return [];
      const rows = (await this.db.execute(sql`
        WITH crudas AS (
          SELECT DISTINCT v.size AS cruda
          FROM variant v
          JOIN product p ON p.id = v.product_id
          JOIN retailer r ON r.id = p.retailer_id
          WHERE v.size IS NOT NULL
            AND v.delisted_at IS NULL AND p.delisted_at IS NULL
            AND ${donde('sizeExact', 'v')}
        )
        SELECT value FROM (SELECT DISTINCT size_canon(cruda) AS value FROM crudas) t
        ORDER BY size_sort(value), value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    /**
     * FAMILIAS de color distintas entre variantes vivas de productos activos (#49 y #291).
     *
     * Devolvía el color canónico, y eran **2.859 chips** en `ropa` —63 KB de payload, el 85,2 %
     * compuestos tipo 'amarillo claro/bluey'—. En un móvil eso no es un filtro, que es literalmente
     * lo que reportó #291. `color_family` (migración 0029) los pliega a **19 familias**.
     *
     * **No llama a `color_family`: lee `variant.color_family_cache`** (migración 0031, #327), que
     * es una columna generada que escribe Postgres. Esta consulta era la cara del panel —las seis
     * facetas van en `Promise.all`, así que el endpoint cuesta lo que la más lenta, y desde #292 se
     * pide en cada cambio de filtro—. Medido contra `deal_tracker_qa` el 13/08/2026 con el SQL de
     * este mismo servicio:
     *
     *     calculando la familia (3.312 formas crudas a ~0,5 ms)     1.667 ms
     *     leyendo la columna generada                                 140 ms      <-- x12
     *
     * Y desapareció con ello la CTE `crudas`, que existía para deduplicar el texto de la tienda
     * **antes** de plegar y así llamar a la función una vez por forma distinta y no una por
     * variante. `pickSizes` la conserva porque allí sigue haciendo falta: `size_canon` no está
     * materializada (todavía — ver #325, que apila `size_band` encima y hereda este problema).
     *
     * Con la CTE se fue también la nota sobre `AS MATERIALIZED`, que ya no aplica aquí porque no
     * hay CTE que vallar. La lección sigue viva en la 0029 y en el ADR, y en `pickSizes`, que sí
     * tiene la forma que la provocaba: **la valla arregla una máquina y estropea la otra**, porque
     * el push-down depende del plan que elija cada planificador. No se reintenta.
     *
     * El `IS NOT NULL` va sobre la columna y no es defensivo: tapa dos casos reales: el nombre que
     * son solo dígitos, que ya negaba `color_canon` (#51, migración 0016), y lo que no encaja en
     * ninguna familia —7 valores en QA, entre ellos códigos como '1-114' y literales como
     * 'default'—. Sin él, ese NULL llegaría a la SPA como el chip literal `"null"`.
     *
     * El orden alfabético basta —a diferencia de la talla, es el que se espera de una lista de
     * colores—, así que aquí no hace falta el equivalente de `size_sort`.
     */
    const pickColors = async (): Promise<string[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT v.color_family_cache AS value
        FROM variant v
        JOIN product p ON p.id = v.product_id
        JOIN retailer r ON r.id = p.retailer_id
        WHERE v.color_family_cache IS NOT NULL
          AND v.delisted_at IS NULL AND p.delisted_at IS NULL
          AND ${donde('color', 'v')}
        ORDER BY value
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => String(r.value));
    };

    const pickRetailers = async (): Promise<RetailerFacet[]> => {
      const rows = (await this.db.execute(sql`
        SELECT DISTINCT r.slug, r.name
        FROM retailer r
        JOIN product p ON p.retailer_id = r.id AND p.delisted_at IS NULL
        WHERE ${donde('retailer')}
        ORDER BY r.name
      `)) as unknown as Record<string, unknown>[];
      return rows.map((r) => ({ slug: String(r.slug), name: String(r.name) }));
    };

    /**
     * Géneros ofrecibles como chip. `unisex` se cae de la lista a propósito: con
     * `generoCondition()` esos productos ya salen dentro de "Niño" y de "Niña", así que un tercer
     * chip no filtraría nada nuevo — solo sugeriría que hay tres estanterías cuando el brief pide
     * dos y el usuario piensa en dos.
     */
    const pickGenders = async (): Promise<string[]> =>
      (await pick('gender', 'gender')).filter((g) => g !== GENERO_UNISEX);

    const [genders, sections, categories, sizes, sizeValues, colors, retailers] = await Promise.all([
      pickGenders(),
      pick('section', null, true),
      pick('category', 'category'),
      pickSizes(),
      // Entra en el mismo `Promise.all` y no en una petición aparte: el endpoint cuesta lo que la
      // más lenta, y esta se va en 0 sin tocar la base mientras no haya banda elegida.
      pickSizeValues(),
      pickColors(),
      pickRetailers(),
    ]);
    return { genders, sections, categories, sizes, sizeValues, colors, retailers };
  }
}
