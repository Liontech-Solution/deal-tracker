-- Talla canónica, cuarta pasada: un rango cuyos dos extremos coinciden no es un rango (issue #89).
--
-- `size_canon('0-0 meses (50 cm)')` devolvía **'0-0 meses'**, que es el chip que se lee en la
-- faceta de tallas y en el alta de un interés. Hallazgo lateral de #73, y NO es el mismo defecto:
-- allí era la unidad ('1 meses'), aquí es el rango.
--
-- ALCANCE MEDIDO, no supuesto (02/08/2026). **4 variantes vivas, 2 productos, solo Zara**, y es el
-- único caso del catálogo: barrido de `dev` y `qa` buscando cualquier canónica con los dos extremos
-- iguales, y no hay más ni en años ni en rangos de número de pie. La talla la sirve la tienda hoy y
-- con stock —comprobado contra su API, no solo contra la base de datos—: es
-- `PACK TRES BODIES POINTELLE CUELLO CRUZADO` (niña / ropa-interior), cuyo resto de escalado (0-1,
-- 1-3, 3-6… meses) es normal; solo la talla de recién nacido se escribe así.
--
--   entorno   última pasada de Zara   variantes con '0-0 meses'
--   qa        02/08/2026 00:34        4
--   dev       28/07/2026 11:33        0
--
-- El 0 de `dev` no desmiente nada: su pasada es cinco días más vieja. Se deja escrito porque la
-- tentación natural es mirar `dev` primero y concluir que no existe.
--
-- Es cosmético y se declara como tal, igual que la 0019: la función sigue siendo idempotente y
-- determinista y se aplica a los DOS lados de cada comparación, así que ni el filtro del catálogo
-- ni el JOIN del matching fallaban. Solo se leía mal.
--
-- ── Las tres decisiones, tomadas a propósito y no por omisión ────────────────────────────────
--
-- 1. **Se enseña '0 meses'**, no una etiqueta propia tipo 'recién nacido'. La 0014 fijó que la
--    canónica es una etiqueta DERIVABLE del texto que sirve la tienda, no vocabulario nuestro;
--    inventar uno rompería esa propiedad y abriría la puerta a que dos tiendas dejaran de casar.
--
-- 2. **La regla es general, no solo para meses.** Hoy el único dato está en meses, pero un colapso
--    que solo mirase esa rama dejaría '4-4 años' y '20-20' mal el día que aparezcan, y habría que
--    volver con otra migración. Se decide como se decidió el rango mixto '14-16' en la 0017: a
--    propósito, no por omisión.
--
-- 3. **El singular de la 0019 se reusa, no se reimplementa.** Es lo que fija la forma de la
--    solución: en vez de tocar las tres ramas de rango (2, 3 y 5) —tres copias de la misma regla—,
--    el rango se **colapsa antes**, en el CTE `base`, y el valor cae por las ramas de número suelto
--    que ya saben decidir singular. Así '1-1 meses' sale '1 mes' sin escribir una línea nueva.
--
-- ── La retro-referencia y sus guardas ────────────────────────────────────────────────────────
--
-- El colapso es `\2` (retro-referencia: el mismo TEXTO a los dos lados) con dos guardas que no son
-- decorativas: sin ellas, '11-110' encajaría como '11-11' y quedaría un '10' suelto. Verificado en
-- Postgres 18.4 (local) y, en CI, sobre `postgres:16-alpine`, que es la versión del cluster:
--
--   0-0 meses (50 cm) -> 0 meses (50 cm)      11-110  -> intacto      20-21   -> intacto
--   1-1 meses         -> 1 meses (-> '1 mes') 110-11  -> intacto      110-116 -> intacto
--   1.5-1.5 años      -> 1.5 años             6/6 años -> 6 años      15-15   -> 15
--
-- Sin la bandera `g` a propósito: una talla trae como mucho un rango, y un reemplazo global con
-- guardas que consumen carácter es más difícil de razonar que de necesitar.
--
-- LÍMITE DECLARADO: la regla 7 (irreconocible) devuelve `btrim(size)`, el texto ORIGINAL —así es
-- como 'XL' y 'Talla única' conservan sus mayúsculas—, así que un hipotético '20-20 cm', que
-- ninguna regla reconoce, seguiría saliendo sin colapsar. Hoy no hay ningún dato así en el
-- catálogo; si lo hubiera, el arreglo no es este sino darle una regla.
CREATE OR REPLACE FUNCTION size_canon(size text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    WITH base AS (
        -- '½' es la fracción que Zara usa en el rango mini ('1½ años', 1781 variantes) y tiene que
        -- sobrevivir: '1.5 años' NO es lo mismo que '2 años' (son 86 cm y 92 cm).
        --
        -- Y aquí, antes que ninguna regla, el colapso de #89: 'N-N' -> 'N'. Va en su propio CTE
        -- para que las siete reglas de abajo sigan siendo LAS MISMAS que en la 0019.
        SELECT regexp_replace(lower(btrim(replace(size, '½', '.5'))),
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
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados).
        ELSE btrim(size)
    END
    FROM prep;
$$;

-- OBLIGATORIO al cambiar el cuerpo de `size_canon`, y la 0014 lo dejó avisado en mayúsculas: el
-- índice guarda los valores YA calculados con la definición vieja. Sin esto el filtro por talla no
-- da un error — da FILAS EQUIVOCADAS, que es mucho peor. Mismo patrón que la 0016, la 0017 y la
-- 0019. Aquí además se ha ejercido el escenario real antes de darlo por bueno: poblar el índice con
-- la definición vieja, aplicar esta migración encima y comprobar que el filtro por '0 meses'
-- devuelve la fila (sin el REINDEX no la devuelve, y sin error).
REINDEX INDEX ix_variant_size_canon;

-- LOS INTERESES YA GUARDADOS. `interest.size` se canonicaliza al dar de alta y **no se recalcula**,
-- así que un interés escrito como '0-0 meses' dejaría de casar con nada a partir de aquí.
--
-- Medido antes de aplicar, igual que la 0015, la 0016, la 0017 y la 0019: **0 intereses en `dev` y
-- 0 en `qa`** (02/08/2026). Va de serie de todas formas, porque desde la 0017 QA es público y manda
-- Telegram de verdad: el próximo que aplique esto puede encontrarse otro número, y un UPDATE sobre
-- 0 filas no cuesta nada.
--
-- El recanonicalizado genérico vale aquí, como en la 0019 y a diferencia de #64: la salida vieja
-- ('0-0 meses') vuelve a entrar, colapsa y sale '0 meses'. Si algún día se cambia una regla que NO
-- cumpla esa propiedad, esto no sirve y hace falta un UPDATE dirigido.
UPDATE interest
   SET size = size_canon(size)
 WHERE size IS NOT NULL
   AND size IS DISTINCT FROM size_canon(size);
