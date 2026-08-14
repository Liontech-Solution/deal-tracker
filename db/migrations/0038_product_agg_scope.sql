-- `product_agg` gana un eje: el agregado existe en dos ámbitos (issue #371).
--
-- La 0035 precomputó el agregado por producto y `listProducts()` lo lee **mientras no haya filtro de
-- variante**. Su cabecera lo apoyaba en una premisa escrita allí con todas las letras: «con un
-- filtro de variante puesto el conjunto colapsa a unos cientos de filas».
--
-- Para talla y color es cierta. Para `inStock` es falsa, y esta migración es la consecuencia.
--
--
-- ── LO MEDIDO ──
--
-- Contra `deal_tracker_qa`, 14/08/2026:
--
--     variantes vivas ............ 167.377
--       con stock ................ 121.710
--       agotadas .................  45.667   -> 27,28 %
--
-- O sea que `inStock=true` **no colapsa nada**: deja pasar tres de cada cuatro variantes, y ese
-- camino paga la agregación del catálogo casi entera. Medido en la validación de v0.4.0, en ventana
-- tranquila y con control sin filtros antes y después: **~2,1 s**, contra 0,18-1,07 s del resto de
-- combinaciones del panel. Con la 0035 dentro, era **el único camino que no aprovechaba
-- `product_agg`**, y por tanto el techo de latencia del panel.
--
-- El cuerpo de #371 sospechaba lo contrario —«si son el 2 %, el filtro no está filtrando»— y por eso
-- la medida iba primero. No son el 2 %: el interruptor filtra de verdad y hay que conservarlo.
--
-- Conviene saber además que **no lo dispara solo quien lo pulsa**: la portada pide
-- `{ sort: 'ofertas', onlyDeals: true, inStock: true }` para «las ofertas de hoy»
-- (`frontend/src/pages/HomePage.tsx`), así que cada carga autenticada pagaba esos ~2,1 s.
--
--
-- ── POR QUÉ NO BASTA UNA COLUMNA MÁS ──
--
-- Filtrar por stock **cambia cuál es la variante representativa** —la de `in_stock DESC, price ASC,
-- variant_id`— y con ella `price_from`, `list_from`, `discount_from` y todos los `*_repr` con los
-- que `deal-rule.sql.ts` decide la honestidad. El agregado de la 0035 responde a «de todas sus
-- variantes vivas»; este filtro pregunta «de las que además tienen stock». **Son dos agregados
-- distintos**, no un dato más del mismo.
--
--
-- ── POR QUÉ UN EJE Y NO UNA SEGUNDA TABLA ──
--
-- La alternativa era `product_agg_in_stock`, con las mismas 14 columnas. Se descarta porque el
-- coste de dos tablas no es el almacenamiento —33.688 filas donde había 16.844 no es nada— sino
-- **el espejo**: dos definiciones en `schema.ts`, dos ramas en `refresh_product_agg()` y dos juegos
-- de paridad que mantener de acuerdo. Este esquema ya arrastra tres espejos (#228) y no necesita
-- otro.
--
-- Con el eje, los dos ámbitos salen del **mismo** `GROUP BY` sobre la misma CTE `matched`: no hay
-- forma de que uno derive del otro, porque son la misma expresión evaluada dos veces.
--
-- El riesgo de la forma es real y conviene decirlo: **una lectura que olvide el predicado de `scope`
-- duplica filas en silencio**. Lo sujetan dos cosas — que hay un solo lector (`agregadoPrecomputado`
-- en `catalog.service.ts`) y que `catalog-agregado-paridad.spec.ts` contrasta los dos caminos con
-- `inStock` puesto, que antes excluía a propósito.
--
--
-- ── QUÉ NO CUBRE, Y POR QUÉ ──
--
-- Solo dos ámbitos. `inStock=false` («enséñame lo agotado») **se queda en el camino vivo**: es una
-- pregunta que la SPA no hace —`CatalogPage` manda `inStock: filters.inStock || undefined`— y un
-- tercer ámbito costaría otro tercio de refresco por un caso que hoy nadie pide.
--
-- Los 294 productos sin ninguna variante con stock simplemente no tienen fila `con_stock`. La
-- ausencia significa lo mismo que ya significaba en la 0035: fuera del resultado.

-- ── 1. El eje ──
--
-- `DEFAULT 'todas'` para poder añadirla sobre las filas que ya existen, y se retira acto seguido:
-- dejarlo puesto haría que un INSERT que olvide el ámbito acabara silenciosamente en 'todas', que
-- es exactamente el fallo que esta tabla no puede permitirse.
ALTER TABLE product_agg
    ADD COLUMN scope text NOT NULL DEFAULT 'todas'
        CHECK (scope IN ('todas', 'con_stock'));

ALTER TABLE product_agg ALTER COLUMN scope DROP DEFAULT;

ALTER TABLE product_agg DROP CONSTRAINT product_agg_pkey;
ALTER TABLE product_agg ADD PRIMARY KEY (product_id, scope);

-- ── 2. El refresco, que ahora emite los dos ámbitos ──
--
-- Mismo cuerpo que la 0035 hasta `matched`. Lo único que cambia es el `INSERT`, y cambia de la
-- forma más aburrida posible: un `CROSS JOIN` con los dos ámbitos y un `WHERE` que descarta las
-- variantes agotadas del ámbito `con_stock`.
--
-- **El filtro va sobre `matched`, no dentro de `latest`**, y esto es lo único delicado de la
-- migración: `latest` es un `DISTINCT ON (variant_id) ... ORDER BY scraped_at DESC`, o sea *la
-- última lectura* de cada variante. Filtrar por `in_stock` dentro haría que una variante cuya
-- última lectura está agotada cayera a una lectura anterior que sí tenía stock, y entonces el
-- agregado enseñaría un precio que ya no existe. Filtrando después, esa variante queda fuera, que
-- es lo que hace el camino vivo (`AND l.in_stock = ...` sobre el resultado de `latest`).
CREATE OR REPLACE FUNCTION refresh_product_agg(p_retailer_id bigint DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $fn$
DECLARE
    filas bigint;
BEGIN
    DELETE FROM product_agg pa
     WHERE p_retailer_id IS NULL OR pa.retailer_id = p_retailer_id;

    -- Espejo EXACTO de las CTE `latest`, `stats` y `agg` de `listProducts()`, sin los filtros de
    -- producto (que se aplican al leer) y sin los de talla y color (que mandan al camino de
    -- siempre). Si tocas una, toca la otra: `catalog-agregado-paridad.spec.ts` ejecuta los dos
    -- caminos sobre el mismo seed y exige el mismo resultado fila a fila.
    WITH vivas AS (
        SELECT v.id, v.product_id, v.color
          FROM variant v
          JOIN product p ON p.id = v.product_id
         WHERE v.delisted_at IS NULL
           AND (p_retailer_id IS NULL OR p.retailer_id = p_retailer_id)
    ),
    latest AS (
        SELECT DISTINCT ON (ph.variant_id)
               ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock, ph.scraped_at
          FROM price_history ph
          JOIN vivas ON vivas.id = ph.variant_id
         ORDER BY ph.variant_id, ph.scraped_at DESC
    ),
    stats AS (
        SELECT l.variant_id,
               -- 90 = HONESTY_WINDOW_DAYS (`deal-rule.ts`). Es el único valor de esta migración que
               -- también vive en TypeScript; `catalog-agregado-paridad.spec.ts` lo fija.
               MIN(h.price) FILTER (
                 WHERE h.scraped_at >= l.scraped_at - make_interval(days => 90)
               ) AS recent_min,
               MAX(h.price) AS max_observed,
               COUNT(*)     AS prior_points,
               EXTRACT(EPOCH FROM l.scraped_at - MIN(h.scraped_at)) / 86400 AS tracked_days
          FROM latest l
          JOIN price_history h ON h.variant_id = l.variant_id AND h.scraped_at < l.scraped_at
         GROUP BY l.variant_id, l.scraped_at
    ),
    matched AS (
        SELECT v.product_id, p.retailer_id, v.color, v.id AS variant_id,
               l.price, l.list_price, l.discount_pct, l.in_stock,
               s.recent_min, s.max_observed,
               COALESCE(s.prior_points, 0) AS prior_points,
               COALESCE(s.tracked_days, 0) AS tracked_days
          FROM vivas v
          JOIN product p ON p.id = v.product_id
          JOIN latest l ON l.variant_id = v.id
          LEFT JOIN stats s ON s.variant_id = v.id
    )
    INSERT INTO product_agg (
        product_id, scope, retailer_id, price_from, list_from, discount_from, max_discount,
        any_in_stock, price_repr, recent_min_repr, max_observed_repr, prior_points_repr,
        tracked_days_repr, color_repr
    )
    SELECT product_id,
           sc.scope,
           retailer_id,
           MIN(price)         AS price_from,
           (array_agg(list_price   ORDER BY in_stock DESC, price ASC, variant_id))[1] AS list_from,
           (array_agg(discount_pct ORDER BY in_stock DESC, price ASC, variant_id))[1] AS discount_from,
           MAX(discount_pct)  AS max_discount,
           BOOL_OR(in_stock)  AS any_in_stock,
           (array_agg(price         ORDER BY in_stock DESC, price ASC, variant_id))[1] AS price_repr,
           (array_agg(recent_min    ORDER BY in_stock DESC, price ASC, variant_id))[1] AS recent_min_repr,
           (array_agg(max_observed  ORDER BY in_stock DESC, price ASC, variant_id))[1] AS max_observed_repr,
           (array_agg(prior_points  ORDER BY in_stock DESC, price ASC, variant_id))[1] AS prior_points_repr,
           (array_agg(tracked_days  ORDER BY in_stock DESC, price ASC, variant_id))[1] AS tracked_days_repr,
           (array_agg(color         ORDER BY in_stock DESC, price ASC, variant_id))[1] AS color_repr
      FROM matched
     CROSS JOIN (VALUES ('todas'), ('con_stock')) AS sc(scope)
     WHERE sc.scope = 'todas' OR in_stock
     GROUP BY product_id, sc.scope, retailer_id;

    -- Ojo al leerlo: desde esta migración son las filas de los DOS ámbitos, así que el número que
    -- la pasada enseña en su resumen casi se dobla. No es que haya aparecido catálogo.
    GET DIAGNOSTICS filas = ROW_COUNT;
    RETURN filas;
END;
$fn$;

-- ── 3. Repoblar ──
--
-- Las filas que había son válidas y ya quedaron marcadas como 'todas' por el DEFAULT, pero se
-- rehace entero igualmente: es una sola pasada sobre el agregado y evita dejar la tabla a medias,
-- con un ámbito poblado y el otro vacío hasta la siguiente ingesta de cada tienda.
SELECT refresh_product_agg();

ANALYZE product_agg;
