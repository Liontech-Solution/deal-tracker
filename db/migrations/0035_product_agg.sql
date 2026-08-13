-- El agregado por producto, ya calculado: `product_agg` (issue #314).
--
-- La 0030 y la 0031 arreglaron el **filtro** y la **faceta** de color. Esta arregla el **listado**,
-- que es el tercer problema y el único que no tiene que ver con `color_family`.
--
--
-- ── EL SÍNTOMA ──
--
-- `listProducts()` ordena por `price_from`, `is_real_deal` y `honest_discount`, que son valores
-- **por producto** calculados a partir de todas sus variantes. No se puede recortar la página antes
-- de tenerlos, así que **cada petición del catálogo agrega el catálogo entero aunque devuelva 20
-- filas**. En el plan se ve como un `Sort` de las variantes vivas por `product_id` que se derrama a
-- disco, seguido de un `GroupAggregate` que las vuelve a ordenar dentro de cada grupo.
--
-- Medido el 13/08/2026 con el SQL sacado del propio servicio (no reescrito a mano) y
-- `EXPLAIN (ANALYZE, BUFFERS)`, sin filtros, contra las dos bases del cluster:
--
--                                      deal_tracker_qa        deal_tracker_prod
--     control                          2.205 / 2.170 / 2.302   1.912 / 1.811 / 1.888 ms
--     Sort Method                      external merge 11208kB  external merge 9888kB
--
-- Conviene saber que **el cuerpo de #314 se quedó corto**: escribía «el suelo de 1,2 s», medido
-- cuando prod tenía 16.010 productos. Hoy son ~1,9 s. El hueco hasta el criterio de hecho —«el
-- catálogo sin filtros por debajo de 1 s en prod»— no era de 250 ms sino de unos 900.
--
--
-- ── LO BARATO, DESCARTADO PRIMERO Y CON DATO ──
--
-- **`work_mem` no llega.** Es `context=user`, así que se puede subir solo para las conexiones del
-- web sin tocar el servidor que comparten otros cuatro proyectos. Quita el derrame entero, y aun
-- así se queda muy lejos:
--
--                                      deal_tracker_qa        deal_tracker_prod
--     work_mem = 32MB                  1.814 / 1.735 / 1.797   1.513 / 1.500 / 1.526 ms
--     Sort Method                      quicksort 19855kB       quicksort 18427kB
--     lo que gana                      ~370 ms (17 %)          ~380 ms (20 %)
--
-- O sea que **el derrame nunca fue el problema**: es CPU de ordenar y agregar 163.509 filas, y el
-- disco solo era la parte de esa CPU que además pasaba por disco. Los controles antes y después
-- coincidieron en las dos bases, así que no es ruido.
--
-- **El índice que #314 proponía crear ya existía.** `ix_variant_product ON variant (product_id)
-- WHERE delisted_at IS NULL`, desde la 0008, y encima `variant_product_id_retailer_variant_id_key`
-- también encabeza por `product_id`. El planificador los ignora, y el plan dice por qué: el `Sort`
-- no es sobre `variant`, es sobre el resultado del `Hash Right Join` de variante ⋈ latest ⋈ stats.
-- Ningún índice de tabla puede pre-ordenar el resultado de un join.
--
--
-- ── POR QUÉ UNA TABLA Y UNA FUNCIÓN, Y NO LO QUE PROPONÍA LA ISSUE ──
--
-- El cuerpo de #314 daba por hecho que el agregado «lo escribiría el scraper y lo leería el web».
-- Aquí no, y el motivo es el que dejó escrito la 0031: **cruzar el contrato scraper→web obliga a
-- `ingest.py` a saber lo que hoy solo sabe el esquema**. En este caso sería peor que en aquel,
-- porque lo que tendría que aprender es la **ventana de honestidad de 90 días** — o sea un tercer
-- sitio donde vive una regla que #228 está peleando justo por no duplicar.
--
-- La 0031 lo resolvió con una columna `GENERATED ALWAYS AS ... STORED`, que **aquí no vale**: una
-- columna generada solo ve su propia fila, y este agregado cruza filas (variantes × price_history).
-- Lo más cerca que se puede estar de aquello es dejar la lógica en el esquema como función y que el
-- scraper solo la invoque:
--
--     `ingest.py` -> SELECT refresh_product_agg(<retailer_id>)   -- una línea, dentro de SU
--                                                               -- transacción, ya atómica
--
-- El scraper no conoce las columnas, ni la ventana, ni la regla. Si el agregado cambia, cambia
-- aquí y nada más.
--
--
-- ── POR QUÉ ES CORRECTO PRECOMPUTARLO (Y POR QUÉ NO SE QUEDA RANCIO SOLO) ──
--
-- `recent_min` se calcula contra `l.scraped_at` —la última observación **de la propia variante**—
-- y NO contra `now()`. Así que este agregado **no deriva con el reloj**: solo cambia cuando cambia
-- `price_history`.
--
-- Y `price_history` tiene **un único escritor**: `ingest.py`. Comprobado, no supuesto — ni el web
-- ni el job de matching insertan ahí. Por eso basta con refrescar al final de cada pasada.
--
-- ⚠️ **Esa es la obligación que esta migración deja atrás, y hay que decirla donde se va a leer:**
-- el día que aparezca un segundo escritor de `price_history` —una corrección a mano, un backfill,
-- un segundo servicio— ese escritor tiene que llamar a `refresh_product_agg()` o el catálogo
-- servirá precios viejos **sin dar ningún síntoma**. No hay trigger que lo vigile a propósito: uno
-- por fila sobre la tabla más escrita del esquema (374.525 filas en QA) costaría en cada pasada
-- mucho más de lo que ahorra en las lecturas.
--
--
-- ── EL CORTE QUE PRESERVA LA SEMÁNTICA, QUE ES LO DELICADO ──
--
-- El agregado del listado se calcula sobre el conjunto **ya filtrado**, así que un agregado
-- precomputado no puede servir a cualquier filtro. De los filtros del catálogo solo tres son **de
-- variante**: `size`, `color` e `inStock`. Los demás —género, sección, categoría, tienda, búsqueda,
-- barefoot, deportiva, activeOnly— son **de producto**, y ésos se aplican igual de bien sobre el
-- agregado ya hecho.
--
-- Por eso `listProducts()` lee esta tabla **sólo cuando los tres son nulos**, y cae al camino de
-- siempre cuando alguno está puesto. No es una limitación que duela: es justo el caso ancho el que
-- tarda, porque con un filtro de variante puesto el conjunto colapsa a unos cientos de filas.
--
-- Aquí NO se guarda `is_real_deal` ni `honest_discount`, y no es un olvido: materializar el
-- veredicto metería la regla de honestidad en el esquema, que es el tercer espejo que #228 quiere
-- evitar. Se guardan los **estadísticos** y el veredicto lo sigue calculando `deal-rule.sql.ts`
-- sobre ~16.000 filas ya agregadas en vez de sobre 163.509 sin agregar.

CREATE TABLE product_agg (
    product_id        bigint PRIMARY KEY REFERENCES product (id) ON DELETE CASCADE,
    -- Redundante con `product.retailer_id`, y a propósito: es lo que permite refrescar una tienda
    -- sin tocar las otras ocho, que es como se ingiere (un CronJob por tienda).
    retailer_id       bigint NOT NULL REFERENCES retailer (id),

    price_from        numeric(10, 2),
    list_from         numeric(10, 2),
    discount_from     numeric(5, 2),
    max_discount      numeric(5, 2),
    any_in_stock      boolean,

    -- Estadísticos de la MISMA variante "mejor oferta" (`in_stock DESC, price ASC`) con la que se
    -- clasifica la honestidad de lo que enseña la tarjeta.
    price_repr        numeric(10, 2),
    recent_min_repr   numeric(10, 2),
    max_observed_repr numeric(10, 2),
    prior_points_repr bigint,
    tracked_days_repr numeric,
    color_repr        text,

    refreshed_at      timestamptz NOT NULL DEFAULT now()
);

-- El refresco borra e inserta por tienda; sin este índice el DELETE sería un seq scan por pasada.
CREATE INDEX ix_product_agg_retailer ON product_agg (retailer_id);

/**
 * Repuebla `product_agg` para una tienda, o para todas si se le pasa NULL.
 *
 * Devuelve cuántas filas ha dejado, para que la pasada pueda decirlo en su resumen: un agregado
 * que deja de poblarse en silencio es exactamente el fallo que #358 describe en otro sitio.
 *
 * VOLATILE y con DML, así que no se hace *inline* en ninguna consulta: la lección de la 0033 sobre
 * `LANGUAGE sql` re-evaluando cada referencia textual no aplica aquí.
 */
CREATE OR REPLACE FUNCTION refresh_product_agg(p_retailer_id bigint DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
AS $fn$
DECLARE
    filas bigint;
BEGIN
    DELETE FROM product_agg pa
     WHERE p_retailer_id IS NULL OR pa.retailer_id = p_retailer_id;

    -- Espejo EXACTO de las CTE `latest`, `stats` y `agg` de `listProducts()`, sin los filtros de
    -- producto (que se aplican al leer) y sin los de variante (que mandan al camino de siempre).
    -- Si tocas una, toca la otra: `catalog-agregado-paridad.spec.ts` ejecuta los dos caminos sobre
    -- el mismo seed y exige el mismo resultado fila a fila.
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
        product_id, retailer_id, price_from, list_from, discount_from, max_discount, any_in_stock,
        price_repr, recent_min_repr, max_observed_repr, prior_points_repr, tracked_days_repr,
        color_repr
    )
    SELECT product_id,
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
     GROUP BY product_id, retailer_id;

    GET DIAGNOSTICS filas = ROW_COUNT;
    RETURN filas;
END;
$fn$;

-- Relleno inicial. Es una tabla nueva, así que NO reescribe `variant` ni toma `ACCESS EXCLUSIVE`
-- sobre ella: sale mucho más barata que la 0031, que sí reescribió las 166.655 variantes.
SELECT refresh_product_agg();

ANALYZE product_agg;
