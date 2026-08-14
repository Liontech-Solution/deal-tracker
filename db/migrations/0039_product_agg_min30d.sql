-- `product_agg` gana el mínimo de 30 días que declara la tienda (issue #354).
--
-- ── POR QUÉ HACE FALTA AQUÍ ──
--
-- `retailer_min_30d` existe en `price_history` desde la `0018` (#78) y ningún código del web la
-- leía. Desde #354 entra en la regla de honestidad como **techo del PVP creíble** y como segunda vía
-- de acusación. El veredicto del LISTADO se calcula sobre las columnas `*_repr` de esta tabla, así
-- que sin esta columna la tarjeta y la ficha dirían cosas distintas de la misma prenda: la ficha
-- —que agrega al vuelo— vería el dato y la tarjeta no.
--
-- Es dato de la ÚLTIMA observación, no del histórico previo: viaja en `latest` junto a `price` y
-- `list_price`, no en `stats`. Por eso no hay agregado nuevo que calcular, solo una columna más que
-- arrastrar hasta la variante representativa.
--
--
-- ── LO MEDIDO ──
--
-- Contra `deal_tracker_qa` (datos del 10/08/2026, consultado el 14/08), sobre las 17.552 variantes
-- vivas de las dos tiendas que publican el dato:
--
--     con mínimo declarado ................ 10.723
--     acusaciones nuevas .................. 291   (hoy hay 0, y no puede haber ninguna hasta ~22/10)
--     de ellas, que hoy fueran `real` ......... 0
--     variantes cuyo PVP creíble baja ....... 991   (bajada media 1,74 €)
--     de ellas, que hoy sean `real` ........... 0
--
-- O sea que `onlyDeals` no cambia de conjunto. Lo que sí puede reordenarse es `sort=ofertas` entre
-- filas que NO son ofertas reales, porque desde #375 el orden se calcula sobre todas.
--
--
-- ── QUÉ NO CAMBIA ──
--
-- Ni el ámbito (`scope`, 0038) ni el desempate de la variante representativa
-- (`in_stock DESC, price ASC, variant_id`, #314). La columna nueva se agrega con el MISMO
-- `array_agg(...)[1]` que las otras cinco `*_repr`, o sea que describe la misma variante que ellas.
-- Si describiera otra, el filtro y la etiqueta hablarían de precios distintos.

-- ── 1. La columna ──
ALTER TABLE product_agg ADD COLUMN retailer_min_30d_repr NUMERIC(10, 2);

-- ── 2. El refresco ──
--
-- Mismo cuerpo que la 0038. Los tres únicos cambios son la columna en `latest`, en `matched` y su
-- `array_agg` en el INSERT. Sigue siendo el espejo EXACTO de las CTE de `listProducts()`: si tocas
-- una, toca la otra — `catalog-agregado-paridad.spec.ts` ejecuta los dos caminos sobre el mismo seed
-- y exige el mismo resultado fila a fila.
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

    WITH vivas AS (
        SELECT v.id, v.product_id, v.color
          FROM variant v
          JOIN product p ON p.id = v.product_id
         WHERE v.delisted_at IS NULL
           AND (p_retailer_id IS NULL OR p.retailer_id = p_retailer_id)
    ),
    latest AS (
        SELECT DISTINCT ON (ph.variant_id)
               ph.variant_id, ph.price, ph.list_price, ph.discount_pct, ph.in_stock,
               ph.retailer_min_30d, ph.scraped_at
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
               l.price, l.list_price, l.discount_pct, l.in_stock, l.retailer_min_30d,
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
        tracked_days_repr, retailer_min_30d_repr, color_repr
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
           (array_agg(retailer_min_30d ORDER BY in_stock DESC, price ASC, variant_id))[1] AS retailer_min_30d_repr,
           (array_agg(color         ORDER BY in_stock DESC, price ASC, variant_id))[1] AS color_repr
      FROM matched
     CROSS JOIN (VALUES ('todas'), ('con_stock')) AS sc(scope)
     WHERE sc.scope = 'todas' OR in_stock
     GROUP BY product_id, sc.scope, retailer_id;

    GET DIAGNOSTICS filas = ROW_COUNT;
    RETURN filas;
END;
$fn$;

-- ── 3. Repoblar ──
--
-- La columna nueva nace NULL en todas las filas que ya existen, y NULL aquí no significa «la tienda
-- no lo publica» sino «no lo hemos calculado». Son dos cosas distintas que la lectura no puede
-- separar, así que la tabla se rehace entera: sin esto, el catálogo se comportaría como antes hasta
-- la siguiente ingesta de cada tienda.
SELECT refresh_product_agg();

ANALYZE product_agg;
