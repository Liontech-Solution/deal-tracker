-- Talla canónica: `size_canon(text)` + `size_sort(text)`.
--
-- `variant.size` guarda la talla TAL COMO la escribe cada tienda, y eso rompía dos cosas (issue #43).
-- Medido en `dev` el 30/07/2026 con las dos tiendas ingeridas: **121 valores distintos** entre
-- variantes vivas, con la misma talla física escrita hasta de cuatro formas:
--
--   zapateria '26' ← sfera:'26' | zara:'26' | zara:'26 (16,3 cm)' | zara:'26 (16.3 cm)'
--   ropa '11-12 años' ← sfera:'11-12' | sfera:'11-12 años' | zara:'11-12 años (148 cm)' | '(152 cm)'
--
--   1. **El aviso por talla fallaba en silencio.** El job de matching casa por igualdad exacta de
--      texto, así que un interés guardado con '26' —la talla que ofrecía el filtro mirando Sfera—
--      NUNCA disparaba para un zapato de Zara almacenado como '26 (16,3 cm)', aunque sea el mismo
--      pie. No hay error, no llega el aviso, y nadie se entera. Es el corazón del producto.
--   2. **El filtro escondía catálogo**, y la faceta ofrecía la misma talla varias veces.
--
-- Se hace ahora porque había **0 intereses en dev y 0 en qa**: en cuanto haya seguimientos reales,
-- cambiar la representación obliga a migrarlos.
--
-- ¿Por qué una función SQL y no una columna que escriba el scraper?
--   * Los dos consumidores —el filtro del catálogo y el JOIN del matching— son SQL, así que la
--     canónica tiene que existir *dentro* de la consulta. Una sola implementación, y vive en el
--     contrato (`db/migrations`), que es la fuente de verdad del esquema.
--   * Una columna escrita por el scraper se quedaría NULL hasta que el refresco forzado del detalle
--     (`last_detail_at`, migración 0009) volviera a tocar cada producto — una semana con el catálogo
--     a medio arreglar — y sería un tercer sitio que puede divergir en silencio.
--   * `size` NO se toca: sigue guardando el texto de la tienda, y la ficha lo sigue enseñando. Perder
--     el texto original impediría enseñarle al usuario lo que la tienda dice de verdad.
--
-- Cambiar las reglas = migración nueva con CREATE OR REPLACE, efectiva al instante en todas las
-- lecturas: no hay ningún valor materializado que backfillear.

-- Etiqueta canónica de una talla. Devuelve el número de pie ('26') para el calzado y el rango de
-- edad ('11-12 años', '1.5 años', '12-18 meses') para la ropa; el cm y la altura de referencia son
-- información útil en la ficha y ruido en el filtro, así que se descartan aquí.
--
-- Las reglas se aplican EN ORDEN, y las cuatro primeras buscan la unidad en cualquier posición de la
-- cadena: eso resuelve de una vez los paréntesis de Zara ('11-12 años (152 cm)'), el rango de
-- calzado que trae la ropa de bebé ('1-2 años (20-22)') y las tallas por letra
-- ('L (12-14 años) (140 cm)' → '12-14 años'), sin una excepción por caso.
--
-- La regla 6 es la única que necesita adivinar: un número suelto sin unidad puede ser un pie (Sfera
-- sirve el calzado limpio: '26', '27'...) o una edad (Sfera también sirve ropa como '4' u '11-12').
-- El umbral **15** es el hueco medido entre los dos dominios —el calzado infantil empieza en 19 y
-- las edades acaban en 14—, así que hay cuatro puntos de holgura a cada lado. Mantener la función
-- con UN solo argumento (sin pasarle la sección) es lo que permite usarla también al dar de alta un
-- interés, donde la sección puede no venir.
--
-- La regla 7 devuelve el texto original: ante algo que no se reconoce, mejor un chip raro en la
-- faceta que una variante que desaparece del filtro.
--
-- Es IDEMPOTENTE —size_canon(size_canon(x)) = size_canon(x)— y por eso se aplica a los DOS lados de
-- cada comparación, sin tener que razonar sobre cuál venía ya normalizado.
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
        -- 5. Rango sin unidad: solo puede ser edad. Ninguna talla de calzado se escribe '11-12'.
        WHEN y ~ '^[0-9]+\s*[-/]\s*[0-9]+$'
            THEN regexp_replace(y, '^([0-9]+)\s*[-/]\s*([0-9]+)$', '\1-\2 años')
        -- 6. Número suelto: pie o edad, según el umbral explicado arriba.
        WHEN y ~ '^[0-9]+$'
            THEN CASE WHEN y::int >= 15 THEN y ELSE y || ' años' END
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados).
        ELSE btrim(size)
    END
    FROM prep;
$$;

-- Clave de orden para la faceta de tallas. Sin esto el desplegable sale en orden alfabético
-- ('10-11 años', '19', '2 años', '26'), que es ilegible con ~60 chips.
--
-- Devuelve los DOS extremos del rango, no solo el primero: con una sola clave, '8-10 años' se colaba
-- delante de '8-9 años' al desempatar por texto. Los arrays se comparan elemento a elemento, así que
-- ORDER BY size_sort(...) basta.
--
-- Los meses se dividen por 12 para expresarlos en años, así que '18-24 meses' cae justo al lado de
-- '1.5 años' — que es exactamente donde le toca. El calzado no lleva unidad y ordena por su propio
-- número; ropa y calzado nunca comparten lista porque la faceta va acotada por sección.
CREATE OR REPLACE FUNCTION size_sort(size text) RETURNS double precision[]
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    WITH m AS (
        SELECT regexp_match(size, '^[^0-9]*([0-9]+(?:\.[0-9]+)?)(?:\s*-\s*([0-9]+(?:\.[0-9]+)?))?') AS n,
               CASE WHEN size ~ 'mes' THEN 12.0 ELSE 1.0 END AS div
        )
    SELECT CASE
        WHEN n IS NULL THEN ARRAY[9999::double precision]   -- sin número: al final, junto a lo raro
        ELSE ARRAY[n[1]::double precision / div, coalesce(n[2], n[1])::double precision / div]
    END
    FROM m;
$$;

-- El filtro del catálogo compara `size_canon(v.size) = size_canon(<talla>)`, y sin índice eso evalúa
-- la función una vez por variante. Medido sobre una copia de `dev` (33.311 variantes, Postgres 16):
--
--   sin índice   ~1.000 ms      con índice   ~1,4 ms      (igualdad de texto cruda: 1,5 ms)
--
-- No es un lujo: en el cluster, que son Raspberry Pi, es la diferencia entre pinchar un chip de talla
-- y esperar. Se intentó antes evitarlo resolviendo primero qué textos crudos significan esa talla
-- (subconsulta con DISTINCT sobre ~70 formas en vez de 33.311 filas) y **no sirve**: Postgres empuja
-- el filtro por debajo del DISTINCT y vuelve a evaluar la función fila a fila.
--
-- ⚠️ **OBLIGACIÓN AL CAMBIAR `size_canon`.** El índice almacena los valores YA calculados. Una
-- migración futura que reemplace la función deja este índice obsoleto en silencio, y entonces el
-- filtro devuelve filas EQUIVOCADAS (no un error: resultados mal). Cualquier migración que toque el
-- cuerpo de `size_canon` tiene que terminar con:
--
--     REINDEX INDEX ix_variant_size_canon;
--
-- Parcial por `delisted_at IS NULL` porque ese es el otro filtro que llevan todas las lecturas del
-- catálogo, igual que en el índice de barefoot de la 0012.
CREATE INDEX ix_variant_size_canon ON variant (size_canon(size))
    WHERE delisted_at IS NULL;
