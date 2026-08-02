-- Talla canónica, tercera pasada: cuando el número es 1, la unidad va en SINGULAR (issue #73).
--
-- La 0014 escribió las unidades como literales en plural, así que `size_canon('1 mes')` devolvía
-- **'1 meses'**. El defecto es de la 0014 —está ahí desde el principio— pero no se veía porque
-- ninguna tienda del catálogo tallaba en meses sueltos hasta que entró la ropa de bebé de Sfera
-- (#56 / PR #71).
--
-- ALCANCE MEDIDO, no supuesto. La issue solo documentaba la regla 2, y la sospecha de que la 4
-- tuviera lo mismo había que comprobarla: es el mismo tipo de cautela que en #64 resultó falsa al
-- medirla. Comprobado el 02/08/2026 contra `deal_tracker_qa`, que es el entorno con la pasada de
-- bebé hecha (en `dev` todavía no hay ni una variante en meses sueltos):
--
--   cruda             canónica antes    variantes    regla
--   1 mes             1 meses                   1    2 (meses, número suelto)
--   1 años (17-19)    1 años                    1    4 (años, número suelto)   ← la issue no la vio
--   1                 1 años                    0    6 (número pelado < 15)
--
-- Son **2 variantes vivas** de las ~2500 de QA. Es cosmético y se declara como tal: la función
-- seguía siendo idempotente y determinista, y se aplica a los DOS lados de cada comparación, así
-- que ni el filtro del catálogo ni el JOIN del matching fallaban. Lo que hacía era enseñar una
-- talla mal escrita en el chip de la faceta y en el alta de un interés, en un producto que vende
-- precisión.
--
-- Los rangos NO pueden producir singular (reglas 1, 3 y 5): su salida lleva siempre dos números.
-- Y **'1.5 años' se queda en plural**, que es lo correcto en castellano — son 2270 variantes en QA
-- (el rango mini de Zara), la forma más común del catálogo, y tocarla sería el error de verdad.
--
-- Se implementa extrayendo el número UNA vez y decidiendo la unidad sobre él, en vez de dos
-- `regexp_replace` con la misma expresión: la alternativa de mirar si el texto de entrada contiene
-- un '1' pegado a la unidad funciona hoy pero se rompe con cualquier talla que traiga dos números.
CREATE OR REPLACE FUNCTION size_canon(size text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    WITH prep AS (
        -- '½' es la fracción que Zara usa en el rango mini ('1½ años', 1781 variantes) y tiene que
        -- sobrevivir: '1.5 años' NO es lo mismo que '2 años' (son 86 cm y 92 cm).
        SELECT lower(btrim(replace(size, '½', '.5')))              AS x,
               btrim(regexp_replace(lower(btrim(replace(size, '½', '.5'))),
                                    '\([^)]*\)', '', 'g'))        AS y
    )
    SELECT CASE
        -- 1-2. Meses. Antes que los años: los rangos en meses ('12-18 meses (86 cm)') colisionarían
        -- con los de años si solo se mirara el número.
        WHEN x ~ '[0-9]+\s*[-/]\s*[0-9]+\s*mes'
            THEN regexp_replace(x, '^.*?([0-9]+)\s*[-/]\s*([0-9]+)\s*mes.*$', '\1-\2 meses')
        WHEN x ~ '[0-9]+\s*mes'
            THEN (
                SELECT n || CASE WHEN n = '1' THEN ' mes' ELSE ' meses' END
                FROM (SELECT regexp_replace(x, '^.*?([0-9]+)\s*mes.*$', '\1')) AS s(n)
            )
        -- 3-4. Años. El separador puede ser '-' o '/': Zara sirve '5-6 años (116 cm)' y
        -- '5/6 años (116 cm)' a la vez, que son la misma talla.
        WHEN x ~ '[0-9]+(\.[0-9]+)?\s*[-/]\s*[0-9]+(\.[0-9]+)?\s*a[nñ]o'
            THEN regexp_replace(x,
                     '^.*?([0-9]+(\.[0-9]+)?)\s*[-/]\s*([0-9]+(\.[0-9]+)?)\s*a[nñ]o.*$',
                     '\1-\3 años')
        WHEN x ~ '[0-9]+(\.[0-9]+)?\s*a[nñ]o'
            THEN (
                -- '1.5' NO es '1', así que la fracción sale en plural, como debe.
                SELECT n || CASE WHEN n = '1' THEN ' año' ELSE ' años' END
                FROM (SELECT regexp_replace(x, '^.*?([0-9]+(\.[0-9]+)?)\s*a[nñ]o.*$', '\1')) AS s(n)
            )
        -- 5. Rango sin unidad: EDAD o NÚMERO DE PIE, con el mismo umbral 15 de la regla 6 exigido en
        -- los dos extremos (#64: las plantillas y los calcetines por rango de Cacles).
        WHEN y ~ '^[0-9]+\s*[-/]\s*[0-9]+$'
            THEN CASE
                WHEN (regexp_match(y, '^([0-9]+)'))[1]::int >= 15
                 AND (regexp_match(y, '([0-9]+)$'))[1]::int >= 15
                    THEN regexp_replace(y, '^([0-9]+)\s*[-/]\s*([0-9]+)$', '\1-\2')
                ELSE regexp_replace(y, '^([0-9]+)\s*[-/]\s*([0-9]+)$', '\1-\2 años')
            END
        -- 6. Número suelto: pie o edad, según el mismo umbral.
        WHEN y ~ '^[0-9]+$'
            THEN CASE
                WHEN y::int >= 15 THEN y
                WHEN y::int = 1   THEN '1 año'   -- por el valor, no por el texto: '01' también
                ELSE y || ' años'
            END
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados).
        ELSE btrim(size)
    END
    FROM prep;
$$;

-- OBLIGATORIO al cambiar el cuerpo de `size_canon`, y la 0014 lo dejó avisado en mayúsculas: el
-- índice guarda los valores YA calculados con la definición vieja. Sin esto el filtro por talla no
-- da un error — da FILAS EQUIVOCADAS, que es mucho peor. Mismo patrón que la 0016 y la 0017.
REINDEX INDEX ix_variant_size_canon;

-- LOS INTERESES YA GUARDADOS. `interest.size` se canonicaliza al dar de alta y **no se recalcula**,
-- así que un interés escrito como '1 meses' dejaría de casar con nada a partir de aquí.
--
-- Medido antes de aplicar, igual que hicieron la 0015, la 0016 y la 0017: **0 intereses en `dev` y
-- 0 en `qa`** (02/08/2026). Aun así el backfill va de serie y no como nota al pie, porque desde la
-- 0017 QA es público y manda Telegram de verdad: el próximo que aplique esto puede encontrarse un
-- número distinto, y un UPDATE sobre 0 filas no cuesta nada.
--
-- Y aquí el recanonicalizado genérico SÍ vale, a diferencia de #64: la salida vieja ('1 meses')
-- vuelve a entrar por la regla 2 y sale '1 mes'. En la 0017 no valía —'25-34 años' ya llevaba la
-- unidad, así que la regla 3 lo dejaba igual— y por eso allí habría hecho falta un UPDATE dirigido.
-- Si algún día se cambia una regla que NO cumpla esa propiedad, esto no sirve: hay que comprobarlo.
UPDATE interest
   SET size = size_canon(size)
 WHERE size IS NOT NULL
   AND size IS DISTINCT FROM size_canon(size);
