-- Banda de edad: `size_band(text)` (issue #325).
--
-- Tercer piso del plegado de la talla, exactamente el mismo patrón que `color_family` sobre
-- `color_canon` (0029): no sustituye a `size_canon` ni la toca, se apila encima.
--
--
-- ── EL PROBLEMA ──
--
-- El panel de filtros ofrecía **181 tallas** en `ropa` (165 cuando se abrió la issue; el catálogo
-- ha crecido). Y no son una lista larga: son **cinco vocabularios mezclados**, porque cada tienda
-- mide a su manera. Medido contra `deal_tracker_qa` el 13/08/2026, contando PRODUCTOS alcanzados y
-- no etiquetas, que es lo que dice si un vocabulario importa:
--
--     vocabulario          etiquetas   productos
--     años                     48        11.569
--     meses                    28         3.193
--     altura en cm             19         1.060
--     número (calcetín)        71           553      <-- 39 % de los chips, 3,4 % del catálogo
--     letra (XS..XXL)           6            27
--     otras                     9            27
--
-- La fila del número es la que decide el diseño: **es el 39 % del panel para el 3,4 % del
-- catálogo**. Y el problema no lo arregla acotar por categoría: todas las categorías publican 4-6
-- vocabularios. Lo que separa limpio es la TIENDA, y pedirle al usuario que elija tienda para poder
-- usar la talla es el parche que el panel lleva puesto hoy (el aviso de `TALLAS_PARA_SUGERIR_TIENDA`).
--
--
-- ── LAS 18 BANDAS, Y POR QUÉ 18 Y NO 12 ──
--
-- Cinco de meses y trece de años:
--
--     0-3 meses · 3-6 meses · 6-12 meses · 12-18 meses · 18-24 meses
--     2 años · 3 años · ... · 13 años · 14+ años
--
-- Se probó agrupar por pares a partir de los 4 (4-5, 6-7, 8-9...), que deja el panel en ~15 chips.
-- **Se descartó porque el filtro de talla es multiselección** (`size?: string[]`, resuelto con
-- `= ANY(...)`, o sea unión): con grano fino, quien quiera margen marca dos bandas y quien no,
-- no lo hace. Con bandas por pares, a quien busca para un niño de 4 años se le mete ropa de 5 y
-- **no hay forma de decir que no** — y en infantil eso es una talla entera.
--
-- O sea: la multiselección hace que el grano fino salga gratis, y el grueso cueste una elección
-- que el usuario ya no puede tomar.
--
--
-- ── LAS TRES BANDAS QUE NO SON EDAD ──
--
-- `Por número`, `Por letra` y `Otras`. Van al final solas, sin que haga falta ordenarlas a mano:
-- `size_sort` (0014) manda al 9999 lo que no lleva número, así que el `ORDER BY` de la faceta ya
-- las coloca. Es reutilización, no casualidad — la 0014 lo dejó escrito para «lo raro».
--
-- **`Por número` y no `Calcetines`, y esto se midió antes de nombrarlo.** El nombre obvio era
-- «Calcetines», porque 71 de esas etiquetas son rangos de pie (`22-24`, `36-38`). Pero **173
-- productos de Lefties son talla `42`** y son bermudas, blusas, chaquetas de punto y vestidos, no
-- calcetines. Llamarlos así sería una etiqueta falsa en 173 prendas visibles. `Por número` cubre
-- las dos cosas sin afirmar ninguna.
--
-- (Que Lefties venda ropa con una talla `42` suelta entre vocabularios de edad es raro y puede ser
-- talla de adulto colándose en un catálogo infantil. No se toca aquí: es otro asunto.)
--
-- **No se traduce el número a edad**, que era la otra salida. Aproximar una talla infantil es
-- exactamente lo que hace que la prenda no le valga a nadie, y el usuario no tendría forma de
-- saber que le hemos aproximado. La talla exacta sigue en la ficha.
--
--
-- ── LA CONVERSIÓN DESDE ALTURA ──
--
-- `(cm − 80) / 6` da la edad en años, y es **exacta** en todo el rango que el catálogo publica de
-- 92 para arriba — comprobado contra los 20 valores que existen: 92→2, 98→3, 104→4 … 176→16, 182→17.
--
-- Por debajo de 92 la fórmula se va a cero y a negativo, y ahí van los únicos tres valores que el
-- catálogo publica (80, 85 y 90 cm, **dos productos cada uno**), con la tabla estándar de talla
-- infantil: 80 cm ≈ 12-18 meses, 85 y 90 cm ≈ 18-24 meses.
--
-- El umbral que separa altura de número tiene margen de sobra y está medido: **la altura más baja
-- que se publica es 80 y el número más alto es 51**. Se corta en 60.
--
--
-- ── LO QUE ESTO NO ES ──
--
-- **No sustituye a `size_canon` ni cambia su cuerpo**, así que `ix_variant_size_canon` sigue válido
-- y esta migración **no necesita `REINDEX`** de nada existente.
--
-- **No entra en el interés.** `interest.size` sigue guardando la CANÓNICA, igual que `interest.color`
-- guarda el color canónico y no la familia (0029). Es la misma asimetría deliberada: el filtro
-- existe para encontrar y el aviso para no mentir. Un interés plegado a banda avisaría de cualquier
-- talla del año entero.
--
-- ⚠️ **CONSECUENCIA QUE HAY QUE TENER PRESENTE**, la misma que estrenó la 0029 para el color:
-- desde aquí, `size` significa **cosas distintas** en dos sitios que se parecen:
--
--     GET /api/catalog/products?size=4 años   -> BANDA (size_band)
--     interest.size = '4-5 años'              -> TALLA CANÓNICA EXACTA (size_canon)
--
--
-- ⚠️ **OBLIGACIÓN AL CAMBIAR ESTA FUNCIÓN**, la misma que la 0014, la 0015 y la 0029: el índice de
-- abajo guarda los valores YA calculados, así que cualquier migración que toque el cuerpo de
-- `size_band` —o el de `size_canon`, sobre el que se apila— tiene que terminar con:
--
--     REINDEX INDEX ix_variant_size_band;
--
-- Sin eso el panel ofrece las bandas viejas y el filtro devuelve filas equivocadas, **sin error**.
--
-- ⚠️ Y UNA FRAGILIDAD HEREDADA, para quien un día suba el motor: el cuerpo llama a `size_canon`
-- **sin cualificar**. La 0021 dejó escrito que `CREATE INDEX`/`REINDEX` corren con un `search_path`
-- restringido a `pg_catalog, pg_temp` a partir de PostgreSQL 18, y ahí esa llamada falla con
-- «function size_canon(text) does not exist». Hoy NO muerde —el CNPG del cluster es 16.4,
-- comprobado, y ahí el `REINDEX` de este índice funciona— y es el mismo patrón que ya tienen
-- `color_family` (0029) y la columna generada de la 0031, así que no se arregla aquí para no
-- hacerlo a medias en tres sitios. Pero el día del salto a PG18 hay que cualificarlas las tres.
-- ⚠️ **LA FORMA DEL CUERPO NO ES ESTILO: ERA 88× EL COSTE.** Es la única función del esquema que
-- no es `LANGUAGE sql`, y conviene saber por qué antes de "arreglarlo".
--
-- Escrita como las demás —un `SELECT` con el resultado de `size_canon` en un `FROM`— costaba
-- **6,89 ms por llamada**. Medido sobre 20.000 evaluaciones con el método de la 0030:
--
--     size_canon sola                        1.546 ms / 20.000  =  0,077 ms
--     una función SQL con DOS referencias
--       al resultado de size_canon          11.207 ms / 20.000  =  0,56  ms      x7
--     size_band en SQL (~10 referencias)   137.590 ms / 20.000  =  6,89  ms      x90
--     size_band con la valla `OFFSET 0`     6.239 ms / 20.000  =  0,31  ms
--     size_band en plpgsql (esta)           1.884 ms / 20.000  =  0,094 ms       <--
--
-- La causa **no es el `WITH`** —esa fue la primera hipótesis y es falsa: quitarlo no cambió nada—.
-- Es que al hacer *inline* de una función SQL, **cada referencia textual al valor se re-evalúa
-- entera**. `size_band` nombra su `s` unas diez veces (los cinco brazos del CASE de meses, los dos
-- del final, el número...), así que ejecutaba `size_canon` diez veces por talla.
--
-- `plpgsql` lo arregla porque no se inlinea y porque una variable **es** una variable: `size_canon`
-- se evalúa una vez y las diez referencias son diez lecturas de memoria. Sale en **0,094 ms**
-- contra los 0,092 de `size_canon` sola: el suelo teórico, porque lo único que esta función hace
-- de más son comparaciones sobre un texto ya calculado.
--
-- Lo que estaba en juego no era el panel: era el **índice**. A 6,89 ms por llamada,
-- `CREATE INDEX` sobre las 163.143 variantes vivas de prod son **~19 minutos** de migración con la
-- tabla bloqueada. A 0,094 son **15 segundos**.
--
-- La valla `OFFSET 0` también sirve (0,31 ms) y habría dejado la función en SQL. Se descartó porque
-- sigue siendo 4× el suelo y porque es un truco que el siguiente que lo lea no va a entender sin
-- esta nota — y si alguien la quita "limpiando", el coste vuelve en silencio. plpgsql no se puede
-- deshacer por accidente.
--
-- ⚠️ **Y ESTO APLICA A LAS OTRAS.** `color_family` (0029) está en SQL y nombra su `seg` una vez por
-- brazo del CASE, que son ~20. Los 0,50 ms/llamada que midió la 0030 son probablemente esto mismo,
-- no el coste de los regex. No se toca aquí —#327 la materializó en columna y ya no se llama por
-- consulta— pero queda dicho por si alguien la revive.
-- **El `COST` va declarado desde el primer día**, que es la instrucción literal que la 0030 dejó
-- escrita para esta issue. 2.000 sale de la proporción con el único par medido que hay: la 0030 le
-- puso 10.000 a `color_family` con 0,50 ms/llamada, y esta cuesta 0,094. No es el valor "verdadero"
-- —en unidades de `cpu_operator_cost` serían cinco cifras— sino uno del orden correcto, que es lo
-- que evita el mal plan de #342 en cuanto haya un segundo predicado.
CREATE OR REPLACE FUNCTION size_band(size text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE COST 2000
AS $$
DECLARE
    -- UNA sola evaluación de size_canon. Todo el punto de que esto sea plpgsql.
    s     text := size_canon(size);
    -- El PRIMER número de la etiqueta, que en un rango es el extremo BAJO: '4-5 años' entra en la
    -- banda de 4 y no en la de 5. Es lo prudente — quien busca para un niño de 4 tiene que
    -- encontrar la prenda que le vale, y las tiendas rotulan el rango por donde empieza.
    -- Sale de `s`, no de otra llamada a size_canon: declararlo con `size_canon(size)` otra vez
    -- costaba exactamente el doble (0,184 ms contra 0,092). Las declaraciones se evalúan en
    -- orden, así que `s` ya está disponible aquí.
    num   numeric := NULLIF(substring(s FROM '^([0-9]+(?:[.][0-9]+)?)'), '')::numeric;
    meses numeric;
BEGIN
    meses := CASE
        WHEN s ~ 'mes' THEN num
        WHEN s ~ 'año' THEN num * 12
        -- Altura en cm. La fórmula es exacta de 92 arriba; debajo, la tabla.
        WHEN s ~ '^[0-9]+$' AND num >= 92 THEN ((num - 80) / 6.0) * 12
        -- El orden de estas dos importa: con el corte de 78 delante, 90 cm caía en 12 meses.
        WHEN s ~ '^[0-9]+$' AND num >= 83 THEN 18    -- 85 y 90 cm
        WHEN s ~ '^[0-9]+$' AND num >= 60 THEN 12    -- 80 cm
    END;

    RETURN CASE
        WHEN meses IS NOT NULL THEN
            CASE
                WHEN meses <   3 THEN '0-3 meses'
                WHEN meses <   6 THEN '3-6 meses'
                WHEN meses <  12 THEN '6-12 meses'
                WHEN meses <  18 THEN '12-18 meses'
                WHEN meses <  24 THEN '18-24 meses'
                WHEN meses <  36 THEN '2 años'
                WHEN meses <  48 THEN '3 años'
                WHEN meses <  60 THEN '4 años'
                WHEN meses <  72 THEN '5 años'
                WHEN meses <  84 THEN '6 años'
                WHEN meses <  96 THEN '7 años'
                WHEN meses < 108 THEN '8 años'
                WHEN meses < 120 THEN '9 años'
                WHEN meses < 132 THEN '10 años'
                WHEN meses < 144 THEN '11 años'
                WHEN meses < 156 THEN '12 años'
                WHEN meses < 168 THEN '13 años'
                ELSE '14+ años'
            END
        WHEN s ~ '^(XXS|XS|S|M|L|XL|XXL)$' THEN 'Por letra'
        -- Todo lo que es un número (o un rango) y no llegó a altura: pie, calcetín, y el 42 de
        -- Lefties. Ver arriba por qué no se llama «Calcetines».
        WHEN s ~ '^[0-9]+(-[0-9]+)?$' THEN 'Por número'
        ELSE 'Otras'
    END;
END;
$$;

-- Mismo motivo que el índice de la 0014 para `size_canon` y el de la 0029 para `color_family`: el
-- filtro compara `size_band(v.size) = size_band(<banda>)` y sin índice eso evalúa la función una
-- vez por variante viva. Y esta es más cara que `size_canon`, porque la llama por dentro.
--
-- Parcial por `delisted_at IS NULL`, como todos los demás: es el otro filtro que llevan todas las
-- lecturas del catálogo.
CREATE INDEX ix_variant_size_band ON variant (size_band(size))
    WHERE delisted_at IS NULL;
