-- La unidad abreviada a UNA LETRA: `4A` son años y `12-18M` son meses (issue #135).
--
-- Springfield escribe la EDAD de tres maneras dentro del mismo catálogo, y la tercera no se
-- canonicaliza. Visto de camino al implementar la tienda (#81) y MEDIDO el 03/08/2026 sobre su
-- primera pasada en `dev` (1112 productos, 8620 variantes), que confirma exacta la cuenta de #135:
--
--     grafía            ejemplo   size_canon    variantes
--     rango             '5-6'     '5-6 años'      3899   ✅
--     número suelto     '8'       '8 años'        4538   ✅
--     con sufijo 'A'    '4A'      '4A'              89   ❌ sale CRUDA  (10 valores, '3A'…'14A')
--     con sufijo 'M'    '12-18M'  '12-18M'          75   ❌ sale CRUDA  (6 valores, meses)
--
-- La causa es la misma para las dos: ninguna de las siete reglas las recoge. Las 1-4 buscan la
-- unidad ESCRITA ('mes', 'a[nñ]o') y '4a' no la lleva; las 5 y 6 exigen SOLO dígitos ('^[0-9]+$'),
-- y '4a' tampoco casa. Así que caen hasta la 7 —«irreconocible: devuelve el texto original»—.
--
-- LO QUE ROMPE es lo mismo que la 0015, la 0017 y la 0021, por un camino nuevo: **el chip partido
-- en dos**. El filtro por talla de la SPA y el `interest` agrupan por `size_canon`, así que un padre
-- que sigue «6 años» NO ve las prendas que Springfield publica como '6A', y al revés. No da error:
-- el filtro devuelve resultados, solo que menos.
--
-- Y LA DE MESES ES PEOR, porque no parte el catálogo de una tienda consigo mismo sino CONTRA LAS
-- OTRAS DOS MÁS GRANDES del proyecto. Medido en `dev` el 03/08/2026:
--
--     chip de springfield        el mismo chip en las demás
--     '6-12M'   19 variantes     '6-12 meses'    hm 42,   zara 9
--     '9-12M'    1               '9-12 meses'    hm 1388, zara 9
--     '12-18M'   1               '12-18 meses'   hm 1389, zara 18
--     '3-6M'    18               '3-6 meses'     zara 17
--
-- O sea que un interés dado de alta sobre el chip '12-18 meses' —el que ve quien navega el catálogo,
-- con 1407 variantes detrás— no casa nunca con la prenda de Springfield, y nadie se entera.
--
-- ── La decisión: cada sufijo se lee como su unidad SOLO dentro del rango en que esa unidad existe ─
--
-- La pregunta que la issue dejó abierta es si 'A' significa siempre «años». En el dominio de esta
-- tienda sí, pero la letra pegada a un número es un sitio muy concurrido: es también la COPA de un
-- sujetador ('80A', '85B') y en otras tallas una HORMA o un ancho. Etiquetar '80A' como «80 años»
-- sería exactamente el fallo silencioso que esta familia de issues (#43, #64, #73, #89, #103, #105)
-- lleva seis migraciones persiguiendo, solo que al revés.
--
-- Con la 'M' la ambigüedad no es hipotética, está EN LA MISMA TIENDA: 'M' a secas es **Medium**, y
-- Springfield publica 'XS', 'S', 'M', 'L', 'XL' y 'XXL' en el mismo catálogo (18 variantes). Por eso
-- los patrones exigen dígitos delante: la talla por letra no los tiene y no entra en la regla.
--
-- MEDIDO el 03/08/2026 contra `dev`. Esta consulta es la señal barata que hay que repetir el día que
-- una tienda nueva estrene una letra, y la que dice si un tope se ha quedado corto:
--
--     SELECT r.slug, v.size, count(*) FROM variant v
--       JOIN product p ON p.id = v.product_id JOIN retailer r ON r.id = p.retailer_id
--      WHERE v.size ~ '[0-9][A-Za-z]' GROUP BY 1, 2;
--
-- Antes de la pasada de Springfield devolvía **0 filas en las siete tiendas**: nadie más abrevia la
-- unidad, así que estas reglas no pueden chocar hoy con nada existente. Después devuelve solo
-- Springfield: 10 valores de años ('3A' … '14A', ninguno llega a 15) y 6 de meses ('0-3M' … '12-24M',
-- ninguno pasa de 24).
--
-- Cada sufijo lleva un tope, y no son el mismo número porque no miden lo mismo:
--
--   * **Años: por debajo de 15**, EL MISMO umbral que las reglas 5 y 6 usan para decidir
--     edad-vs-número-de-pie, con el mismo hueco medido detrás (los rangos de edad acaban en '13-14'
--     y los de pie empiezan en '20 /21', #64). Por encima, la 'A' es más probablemente una copa.
--   * **Meses: hasta 36**, que son 3 años — el mayor mes real del catálogo ('36 meses', 372
--     variantes de Hipercor) y el techo natural de la ropa de bebé. Por encima, una 'M' pegada a un
--     número es más probablemente una horma o un ancho que una edad de 4 años dicha en meses.
--
-- El tope no está para distinguir dos unidades entre sí —un pie no se escribe con 'A' ni con 'M'—
-- sino para que lo dudoso caiga a la regla 7 y salga CRUDO, que es el comportamiento de hoy: un chip
-- feo, nunca una etiqueta equivocada. Es el modo de fallo que esta familia de issues persigue.
--
-- La propiedad, enunciada para que se pueda testear de una vez: **el sufijo se lee como su unidad
-- solo cuando TODOS sus números están dentro del tope de esa unidad.**
--
--     '4A'    -> '4 años'       '15A'    -> '15A'      (crudo, como hoy)
--     '1A'    -> '1 año'        '80A'    -> '80A'      (la copa, intacta)
--     '14A'   -> '14 años'      '14-16A' -> '14-16A'   (un extremo llega al tope: crudo)
--     '8-9A'  -> '8-9 años'     '4-4A'   -> '4 años'   (el colapso de la 0020 actúa ANTES)
--     '3M'    -> '3 meses'      '38M'    -> '38M'      (pasa de 36: crudo)
--     '1M'    -> '1 mes'        'M'      -> 'M'        (Medium: sin dígitos, no entra)
--     '12-18M'-> '12-18 meses'  '24-38M' -> '24-38M'
--
-- EL RANGO CON SUFIJO existe y lo destapó esta misma pasada: '8-9A', una variante. #135 lo daba por
-- no visto y proponía decidirlo igualmente «por si aparece» — apareció el mismo día. En meses el
-- rango es además la forma MAYORITARIA (74 de las 75).
--
-- ── Lo que NO cambia ──────────────────────────────────────────────────────────────────────────
--
-- **Las siete reglas de la 0021 son literalmente las mismas.** Lo único que hay son CUATRO reglas
-- nuevas entre la 6 y la 7, que es el hueco donde caían '4a' y '12-18m'. La función se reproduce
-- entera porque `CREATE OR REPLACE FUNCTION` no admite parches, y la firma sigue siendo
-- `text -> text` de UN SOLO argumento: un índice por expresión solo puede referenciar columnas de su
-- propia tabla (0021).
--
-- Los patrones nuevos buscan la letra en MINÚSCULA porque la entrada llega ya plegada por el
-- `translate()` del CTE `base` — que es justo lo que arregló la 0021 para el ctype `C` del cluster,
-- donde `lower('4A')` sí baja la A (no es acentuada) pero `lower('AÑOS')` no bajaba la Ñ.
--
-- `size_sort` tampoco se toca: recibe la canónica ya calculada, y '4 años' es una forma que ya
-- ordena bien desde la 0014.

CREATE OR REPLACE FUNCTION size_canon(size text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    WITH base AS (
        -- '½' es la fracción que Zara usa en el rango mini ('1½ años', 1781 variantes) y tiene que
        -- sobrevivir: '1.5 años' NO es lo mismo que '2 años' (son 86 cm y 92 cm).
        --
        -- Y aquí, antes que ninguna regla, el colapso de #89: 'N-N' -> 'N'. Va en su propio CTE
        -- para que las reglas de abajo sigan siendo LAS MISMAS que en la 0019.
        --
        -- El `lower` de la 0020 pasa a plegar también la caja acentuada (#105): con ctype `C` una
        -- talla en mayúsculas con acento ('11/12 AÑOS', Hipercor) no casaba con el patrón `a[nñ]o`
        -- de la regla 3 y caía hasta la regla 7, que devuelve el texto crudo.
        SELECT regexp_replace(lower(translate(btrim(replace(size, '½', '.5')),
                                              'ÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ',
                                              'áàäâãéèëêíìïîóòöôõúùüûñç')),
                              '(^|[^0-9.])([0-9]+(\.[0-9]+)?)\s*[-/]\s*\2([^0-9.]|$)',
                              '\1\2\4')                            AS b
    ), prep AS (
        SELECT b                                                   AS x,
               btrim(regexp_replace(b, '\([^)]*\)', '', 'g'))      AS y
        FROM base
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
        -- ── LAS CUATRO NUEVAS (#135): la unidad abreviada a una letra ──────────────────────────
        --
        -- Van todas DESPUÉS de la 6 porque no compiten con ninguna de las siete: '4a' y '12-18m' no
        -- casan ni con '[0-9]+\s*mes' ni con 'a[nñ]o' ni con '^[0-9]+$'. Dentro del bloque, los meses
        -- primero, por el mismo motivo que la regla 1 va antes que la 3.
        --
        -- Los patrones exigen DÍGITOS delante de la letra, y eso es lo que deja fuera a la talla por
        -- letra: 'M' a secas es Medium y sale por la regla 7, como 'S', 'L' o 'XL'.

        -- 1b-2b. Meses con el sufijo 'M', hasta 36 (= 3 años, el mayor mes real del catálogo).
        WHEN y ~ '^[0-9]+\s*[-/]\s*[0-9]+\s*m$'
            THEN CASE
                WHEN (regexp_match(y, '^([0-9]+)'))[1]::int > 36
                  OR (regexp_match(y, '([0-9]+)\s*m$'))[1]::int > 36
                    THEN btrim(size)
                ELSE regexp_replace(y, '^([0-9]+)\s*[-/]\s*([0-9]+)\s*m$', '\1-\2 meses')
            END
        WHEN y ~ '^[0-9]+\s*m$'
            THEN (
                -- El singular de la 0019, igual que en la regla 2: '1M' es '1 mes'.
                SELECT CASE
                    WHEN n::int > 36 THEN btrim(size)
                    WHEN n::int = 1  THEN '1 mes'
                    ELSE n || ' meses'
                END
                FROM (SELECT regexp_replace(y, '^([0-9]+)\s*m$', '\1')) AS s(n)
            )

        -- 5b-6b. Años con el sufijo 'A', por debajo de 15 — el mismo umbral de las reglas 5 y 6, y
        -- por eso se leen aquí al lado. Por encima NO se canonicalizan: caen a la 7 y salen crudas,
        -- que es el comportamiento de hoy y el que protege a la copa de sujetador.
        WHEN y ~ '^[0-9]+\s*[-/]\s*[0-9]+\s*a$'
            THEN CASE
                WHEN (regexp_match(y, '^([0-9]+)'))[1]::int >= 15
                  OR (regexp_match(y, '([0-9]+)\s*a$'))[1]::int >= 15
                    THEN btrim(size)
                ELSE regexp_replace(y, '^([0-9]+)\s*[-/]\s*([0-9]+)\s*a$', '\1-\2 años')
            END
        WHEN y ~ '^[0-9]+\s*a$'
            THEN (
                -- El singular de la 0019 se reusa aquí igual que en las reglas 2, 4 y 6: '1A' es
                -- '1 año', no '1 años'.
                SELECT CASE
                    WHEN n::int >= 15 THEN btrim(size)
                    WHEN n::int = 1   THEN '1 año'
                    ELSE n || ' años'
                END
                FROM (SELECT regexp_replace(y, '^([0-9]+)\s*a$', '\1')) AS s(n)
            )
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados). Sigue devolviendo el
        -- texto ORIGINAL, que es como 'XL' y 'Talla única' conservan sus mayúsculas — plegar aquí
        -- cambiaría esos chips sin arreglar nada.
        ELSE btrim(size)
    END
    FROM prep;
$$;

-- ── El índice por expresión ───────────────────────────────────────────────────────────────────
--
-- OBLIGATORIO al cambiar el cuerpo, y la 0014 lo dejó avisado en mayúsculas: el índice guarda los
-- valores YA calculados con la definición vieja, así que sin esto el filtro por talla no da un error
-- — da FILAS EQUIVOCADAS, que es mucho peor. Mismo patrón que la 0016, la 0017, la 0019, la 0020 y
-- la 0021. Y aquí es literal: una variante con '4A' quedaría indexada como '4A' y el filtro por
-- '4 años' seguiría sin devolverla, o sea que la migración no arreglaría nada.
--
-- EJERCIDO antes de darlo por bueno, como hizo la 0020: base migrada hasta la 0023, 10 000 variantes
-- de las que 1 000 llevan '4A', esta migración aplicada encima SIN el REINDEX y `enable_seqscan =
-- off` para obligar al plan a usar el índice (con 10 000 filas el planificador prefiere el barrido
-- y el defecto no se ve):
--
--     ->  Bitmap Index Scan on ix_variant_size_canon
--           Index Cond: (size_canon(size) = '4 años'::text)
--
--     WHERE size_canon(size) = '4 años'  ->  0 filas     <- y la función dice '4 años'
--     WHERE size_canon(size) = '4A'      ->  1 000 filas <- el índice sigue con la canónica vieja
--
-- Sin error y con las filas equivocadas, que es el modo de fallo del que avisa la 0014. Con el
-- REINDEX los dos números se intercambian, que es lo correcto.
REINDEX INDEX ix_variant_size_canon;

-- ── Los intereses ya guardados ────────────────────────────────────────────────────────────────
--
-- `interest.size` se canonicaliza al dar de alta y **no se recalcula**, así que un interés escrito
-- con la canónica vieja ('4A') dejaría de casar con nada a partir de aquí, en silencio.
--
-- Medido antes de aplicar, igual que la 0015, la 0016, la 0017, la 0019, la 0020 y la 0021: **0
-- intereses en `dev` y 0 en `qa`** (03/08/2026). Va de serie de todas formas, porque desde la 0017
-- QA es público y manda Telegram de verdad: el próximo que aplique esto puede encontrarse otro
-- número, y un UPDATE sobre 0 filas no cuesta nada.
--
-- El recanonicalizado genérico vale aquí, que es la condición que dejó escrita la 0019: la salida
-- vieja, al reentrar en la función nueva, produce la nueva ('4A' -> '4 años', '12-18M' ->
-- '12-18 meses'). Las reglas nuevas no tocan ninguna salida anterior —comprobado sobre las 343
-- tallas distintas de `dev`: cero diferencias entre la canónica vieja y la nueva—, así que ningún
-- interés ya canónico se mueve.
UPDATE interest
   SET size = size_canon(size)
 WHERE size IS NOT NULL
   AND size IS DISTINCT FROM size_canon(size);
