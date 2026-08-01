-- Talla canónica, segunda pasada: un rango de dos números también puede ser de PIE (issue #64).
--
-- La 0014 escribió la regla 5 como una certeza: «Rango sin unidad: solo puede ser edad. Ninguna
-- talla de calzado se escribe '11-12'». Cacles la desmiente. Medido en `dev` el 01/08/2026, sobre
-- las tres tiendas con catálogo vivo (zara 2778 productos, cacles 426, sfera 387), hay **33 valores
-- crudos** que son rangos de dos números sin unidad, y se parten en dos familias limpias — 7 de
-- edad y **26 de pie**:
--
--   EDAD (Sfera, ropa)      '4-5' '5-6' '6-7' '7-8' '9-10' '11-12' '13-14'
--   PIE  (Cacles)           '20 /21' '22 /23' '24 / 25' '26 / 27' '28 / 29' '30 / 31'
--                           '25-34' '35-41' '42-47' '48-51'
--                           '20-24' '21-23' '23-26' '24-26' '25-27' '27-29' '27-30' '30-32'
--                           '30-34' '31-34' '33-35' '36-38' '39-41' '42-44' '45-47' '48-50'
--
-- Son **201 variantes vivas**, y el síntoma es un chip de talla «48-51 años» en la faceta, que no
-- filtra lo que dice filtrar. Peor que el chip: el JOIN del matching compara por canónica, así que
-- un interés de ropa por rango de edad y un zapato por rango de número pueden acabar iguales y el
-- aviso llegar mal **sin que falle nada ruidosamente** — el modo de fallo de siempre.
--
-- LA SECCIÓN NO ES EL DISCRIMINADOR, aunque lo parezca y aunque así estuviera escrito en el ADR y
-- en la propia issue («en `zapateria` es número de pie, en `ropa` es edad»). El desglose lo tumba:
--
--   zapateria  plantillas       48 variantes      ← rango de número, como se esperaba
--   zapateria  zapatillas       30                ← ídem, talla doble de primeros pasos
--   ropa       ropa-interior   123                ← **calcetines**, tallados por número de pie
--
-- Las 123 son calcetines barefoot de Be Lenka Kids y Plus12: '21-23', '24-26', '36-38', '48-50'.
-- Son ropa Y son números de pie a la vez, así que una función que mirase `section` seguiría dando
-- «36-38 años» a la mayoría de las filas afectadas. Es el tercer escarmiento de la misma serie que
-- el ADR ya recoge (#49 y #51): una cautela escrita en el contrato que resultó falsa al medirla.
-- Nadie había mirado de qué CATEGORÍA eran las filas.
--
-- Y hay un segundo motivo, este insalvable: `size_canon(size, section)` haría **imposible** el
-- índice por expresión `ix_variant_size_canon`, porque un índice solo puede referirse a columnas de
-- su propia tabla y `variant` no tiene `section`. El filtro de talla volvería de 1,4 ms a ~1 s.
--
-- Lo que sí discrimina es EL PROPIO NÚMERO, y la 0014 ya tenía ese umbral medido para el número
-- suelto de la regla 6: **15**. Los datos de arriba lo confirman con holgura — las edades acaban en
-- '13-14' y los pies empiezan en '20 /21', **seis puntos de hueco**. Así que la regla 5 pasa a
-- aplicar el mismo umbral, exigido en LOS DOS extremos del rango, y la firma no cambia: un solo
-- argumento, índice intacto, y sigue sirviendo para el alta de interés (donde la sección puede no
-- venir, que es justo la razón por la que la 0014 la dejó con un argumento).
--
-- Se exigen los dos extremos, no solo el primero, porque un rango mixto ('14-16') es ambiguo de
-- verdad: no existe hoy en ninguna tienda y ante la duda se queda como edad, que es el
-- comportamiento actual. Si algún día aparece, la decisión se toma aquí y no por omisión.
--
-- La salida del rango de pie va **sin unidad** ('25-34'), igual que la regla 6 devuelve el número
-- pelado ('26') para el calzado. Y normaliza el separador, así que '24 / 25' y '24-25' son la misma
-- talla, como ya pasaba con '5/6 años' y '5-6 años'.
--
-- LOS INTERESES YA GUARDADOS. `interest.size` se canonicaliza al dar de alta y **no se recalcula**,
-- así que un interés creado antes de esta migración con '25-34' quedó escrito como '25-34 años' y a
-- partir de aquí no casaría con nada. Medido antes de aplicarla, igual que hicieron la 0015 y la
-- 0016: **0 intereses en `dev` y 0 en `qa`** (`SELECT count(*) FROM interest`, 01/08/2026, 0 de
-- ellos con talla). No hay nada que backfillear. Si algún día hubiera intereses vivos, no bastaría
-- con recanonicalizar a secas —'25-34 años' ya lleva la unidad, así que la regla 3 lo dejaría
-- igual—: haría falta un UPDATE dirigido que quite el ' años' cuando los dos extremos sean >= 15.
--
-- Sigue siendo IDEMPOTENTE: size_canon('25-34') = '25-34'.
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
            THEN regexp_replace(x, '^.*?([0-9]+)\s*mes.*$', '\1 meses')
        -- 3-4. Años. El separador puede ser '-' o '/': Zara sirve '5-6 años (116 cm)' y
        -- '5/6 años (116 cm)' a la vez, que son la misma talla.
        WHEN x ~ '[0-9]+(\.[0-9]+)?\s*[-/]\s*[0-9]+(\.[0-9]+)?\s*a[nñ]o'
            THEN regexp_replace(x,
                     '^.*?([0-9]+(\.[0-9]+)?)\s*[-/]\s*([0-9]+(\.[0-9]+)?)\s*a[nñ]o.*$',
                     '\1-\3 años')
        WHEN x ~ '[0-9]+(\.[0-9]+)?\s*a[nñ]o'
            THEN regexp_replace(x, '^.*?([0-9]+(\.[0-9]+)?)\s*a[nñ]o.*$', '\1 años')
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
            THEN CASE WHEN y::int >= 15 THEN y ELSE y || ' años' END
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados).
        ELSE btrim(size)
    END
    FROM prep;
$$;

-- OBLIGATORIO al cambiar el cuerpo de `size_canon`, y la 0014 lo dejó avisado en mayúsculas: el
-- índice guarda los valores YA calculados con la definición vieja. Sin esto el filtro por talla no
-- da un error — da FILAS EQUIVOCADAS, que es mucho peor. Mismo patrón que la 0016 con el color.
REINDEX INDEX ix_variant_size_canon;
