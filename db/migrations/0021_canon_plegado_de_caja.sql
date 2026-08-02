-- Las canónicas dependían del ctype de la base, y la del cluster es `C` (issue #105).
--
-- `size_canon` y `color_canon` empiezan las dos por `lower(btrim(...))`. Con el ctype `C`,
-- `lower()` **no baja las letras acentuadas**:
--
--     lower('ÍNDIGO')      -->  'Índigo'
--     lower('11/12 AÑOS')  -->  '11/12 aÑos'
--
-- Y la base del cluster tiene ese ctype. Comprobado el 02/08/2026 contra la CNPG `platform-postgres-dev`:
--
--     datname              | enc  | datcollate | datctype
--     deal_tracker         | UTF8 | C          | C
--     deal_tracker_qa      | UTF8 | C          | C
--
-- En `color_canon` el plegado simplemente no ocurría. En `size_canon` es peor de lo que parece: el
-- `lower` alimenta a la regla de años, cuyo patrón es `a[nñ]o`, y contra 'aÑos' no casa; la talla cae
-- hasta la regla 7 —«irreconocible: devuelve el texto original»— y sale CRUDA.
--
-- ALCANCE MEDIDO en `dev` el 02/08/2026, con las siete tiendas ingeridas (lefties entró ese día):
--
--     tienda     color a medias   talla a medias
--     lefties         463               0
--     zara            240               0
--     hm               40               0
--     hipercor          0               5
--     cacles · c-and-a · sfera          0
--     TOTAL           743               5      -> 748 variantes
--
-- Lefties sola aporta más que las otras tres juntas porque **escribe los colores enteros en
-- MAYÚSCULAS**: no se le rompen los que por casualidad vienen capitalizados, se le rompen TODOS los
-- que llevan acento ('GRIS VIGORÉ' 196 variantes, 'TONO MARRÓN' 33, 'AÑIL DELAVADO' 24…). Nótese
-- que la 'Ñ' tampoco baja: esto no es solo cosa de las vocales.
--
-- LO QUE SE ROMPÍA DE VERDAD: **el chip partido en dos**, que es el fallo silencioso que motivó la
-- 0015 reproducido por otro camino. El 02/08/2026 había dos en la faceta de color:
--
--     indigo  ->  ['indigo', 'Índigo']        marron  ->  ['marron', 'marrÓn']
--
-- O sea que un padre que filtraba por «marrón» veía UNA PARTE del catálogo, y un interés dado de
-- alta sobre un chip no casaba nunca con las prendas del otro. Y empeora de forma no lineal: basta
-- con que una tienda escriba en mayúsculas para partir sus colores contra los de todas las demás
-- (van dos de siete).
--
-- POR QUÉ NO LO VIO NADIE, que es la parte que hubo que arreglar además del SQL. CI levanta
-- `postgres:16-alpine` con su locale por defecto, donde `lower('ÍNDIGO')` sí devuelve 'índigo', así
-- que `size-canon.spec.ts` y `color-canon.spec.ts` estaban en verde mientras el cluster hacía otra
-- cosa. Esto no es una regresión: lleva desde la 0014 y la 0015. Desde esta migración los dos specs
-- se ejecutan contra **las dos bases** —la del locale por defecto y una con ctype `C`—, y CI crea la
-- segunda (`TEST_DATABASE_URL_CTYPE_C` en `web-ci.yml`). Un test que solo corre con el locale bueno
-- no prueba lo que hace el sitio donde esto se ejecuta de verdad.
--
-- ── Por qué `translate()` y no un COLLATE ────────────────────────────────────────────────────
--
-- Las tres opciones que planteaba #105 no son equivalentes:
--
--   1. `lower(x COLLATE "es-ES-x-icu")` corrige la causa y es menos código, pero **ata el esquema a
--      que ese collation exista en el servidor**. Si un día no está, la función no se puede crear y
--      la migración revienta el arranque del servicio (el migrador del web corre como initContainer).
--   2. Recrear la base con otro ctype no es un cambio de aplicación sino de la CNPG, y no se puede
--      hacer en caliente.
--   3. `translate()` explícito: estándar, `IMMUTABLE`, sin extensiones ni privilegios. Es feo y es
--      el que ya eligió `catalog.service.ts` para la búsqueda acento-insensible, **precisamente por
--      no depender de `unaccent`** (#39.2). Hay precedente en el repo y se elige el mismo.
--
-- `translate()` opera por CARÁCTER y no por byte en una base UTF8, que es justo lo que lo hace
-- portable entre locales — no depende del ctype para nada.
--
-- El plegado, entonces, es este, y aparece DOS VECES en este fichero, una por función:
--
--     lower(translate(x, 'ÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ', 'áàäâãéèëêíìïîóòöôõúùüûñç'))
--
-- Se pliega la CAJA, no el acento. Es deliberado y sostiene la decisión que la 0015 tomó midiendo:
-- 'Índigo' -> 'índigo', NO 'indigo'. De los 220 valores de color medidos, 26 llevaban acento y
-- ninguno colisionaba con su versión sin acentuar, así que plegarlos no fundiría ni un par y a
-- cambio degradaría el chip. El alfabeto es el mismo que usa `fold()` en `catalog.service.ts` para
-- la búsqueda, en mayúsculas.
--
-- ⚠️ **POR QUÉ ESTÁ DUPLICADO Y NO EN UNA FUNCIÓN AUXILIAR.** Lo primero que se escribió fue un
-- `lower_es(text)` llamado desde las dos, que es lo obvio. **No funciona**, y falla de una forma que
-- conviene no volver a descubrir: desde PostgreSQL 15 las operaciones de mantenimiento
-- (`CREATE INDEX`, `REINDEX`, `VACUUM FULL`, `CLUSTER`…) corren con un `search_path` restringido a
-- `pg_catalog, pg_temp`. El nombre de la función indexada se resuelve por OID y sobrevive, pero un
-- nombre escrito DENTRO de su cuerpo se resuelve al ejecutar, y ahí ya no hay `public`:
--
--     CREATE INDEX ix ON t (size_canon(x));
--     ERROR:  function lower_es(text) does not exist
--     CONTEXTO:  SQL function "size_canon" during inlining
--
-- Medido en local (PG 18.4) con las tres variantes: sin cualificar **ni siquiera deja crear el
-- índice**; `public.lower_es(...)` funciona; el `translate()` en línea funciona (`translate` vive en
-- `pg_catalog`, que sí está en ese search_path). Se elige el tercero porque el segundo obliga a
-- clavar el nombre del esquema en el contrato SQL —y sería el ÚNICO sitio del repo que lo hace—,
-- para romperse en silencio y solo al reindexar el día que alguien despliegue en otro esquema.
-- Dos literales de 24 caracteres en el mismo fichero son más baratos que eso.

-- ── Las dos funciones ─────────────────────────────────────────────────────────────────────────
--
-- **No cambia ninguna regla.** Las siete de la talla son literalmente las de la 0020 y las tres del
-- color las de la 0015/0016; lo único que cambia es que la entrada llega plegada de verdad. Se
-- reproducen enteras porque `CREATE OR REPLACE FUNCTION` no admite parches: se reemplaza el cuerpo
-- completo o nada, y la firma tiene que seguir siendo la misma (`text -> text`, un solo argumento)
-- porque un índice por expresión solo puede referenciar columnas de su propia tabla.

CREATE OR REPLACE FUNCTION size_canon(size text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    WITH base AS (
        -- '½' es la fracción que Zara usa en el rango mini ('1½ años', 1781 variantes) y tiene que
        -- sobrevivir: '1.5 años' NO es lo mismo que '2 años' (son 86 cm y 92 cm).
        --
        -- Y aquí, antes que ninguna regla, el colapso de #89: 'N-N' -> 'N'. Va en su propio CTE
        -- para que las siete reglas de abajo sigan siendo LAS MISMAS que en la 0019.
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
        -- 7. Irreconocible: se devuelve tal cual (con los espacios recortados). Sigue devolviendo el
        -- texto ORIGINAL, que es como 'XL' y 'Talla única' conservan sus mayúsculas — plegar aquí
        -- cambiaría esos chips sin arreglar nada.
        ELSE btrim(size)
    END
    FROM prep;
$$;

CREATE OR REPLACE FUNCTION color_canon(color text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE WHEN canon ~ '^[0-9]+$' THEN NULL ELSE canon END
    FROM (
        -- Mismo plegado que en `size_canon`, y por el mismo motivo (#105): con ctype `C` el `lower`
        -- de la 0015 dejaba 'ÍNDIGO' en 'Índigo' y 'MARRÓN' en 'marrÓn', que son 743 variantes y
        -- dos chips partidos en la faceta.
        SELECT regexp_replace(
                   lower(translate(btrim(regexp_replace(color, '\s+', ' ', 'g')),
                                   'ÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇ',
                                   'áàäâãéèëêíìïîóòöôõúùüûñç')),
                   '^[0-9]{3} (.+)$', '\1') AS canon
    ) t;
$$;

-- ── Los índices ───────────────────────────────────────────────────────────────────────────────
--
-- OBLIGATORIO al cambiar el cuerpo de cualquiera de las dos, y la 0014 y la 0015 lo dejaron avisado
-- en mayúsculas: el índice guarda los valores YA calculados con la definición vieja. Sin esto el
-- filtro no da un error — da FILAS EQUIVOCADAS, que es mucho peor. Mismo patrón que la 0016, la
-- 0017, la 0019 y la 0020, y aquí por partida doble porque cambian las dos funciones.
--
-- Estos dos REINDEX son además la prueba de la nota de arriba: con el `lower_es` sin cualificar
-- fallaban en el acto, y por eso el plegado acabó en línea.
REINDEX INDEX ix_variant_size_canon;
REINDEX INDEX ix_variant_color_canon;

-- ── Los intereses ya guardados ────────────────────────────────────────────────────────────────
--
-- `interest.size` e `interest.color` se canonicalizan al dar de alta y **no se recalculan**, así que
-- un interés escrito con la canónica vieja ('marrÓn', '11/12 AÑOS') dejaría de casar con nada a
-- partir de aquí, en silencio.
--
-- Medido antes de aplicar, igual que la 0015, la 0016, la 0017, la 0019 y la 0020: **0 intereses en
-- `dev` y 0 en `qa`** (02/08/2026). Va de serie de todas formas, porque desde la 0017 QA es público
-- y manda Telegram de verdad: el próximo que aplique esto puede encontrarse otro número, y un UPDATE
-- sobre 0 filas no cuesta nada.
--
-- El recanonicalizado genérico vale aquí, que es la condición que dejó escrita la 0019: la salida
-- vieja, al reentrar en la función nueva, produce la nueva ('marrÓn' -> 'marrón',
-- '11/12 AÑOS' -> '11-12 años'). Es la primera vez que se recanonicaliza `interest.color`; la 0016
-- dejó el suyo solo en comentario porque no hacía falta.
UPDATE interest
   SET size = size_canon(size)
 WHERE size IS NOT NULL
   AND size IS DISTINCT FROM size_canon(size);

-- La guarda `color_canon(color) IS NOT NULL` NO es decorativa: un `interest.color` NULL significa
-- «cualquier color» en el JOIN del matching, así que sin ella un interés cuyo color colapsara a NULL
-- (la regla de solo-dígitos de la 0016) se convertiría en silencio en un interés mucho más ancho, y
-- empezaría a avisar de prendas que nadie pidió. Qué hacer con esos intereses es una decisión de
-- producto —la 0016 ya lo dejó escrito—, no algo que deba pasar de refilón en un plegado de caja.
UPDATE interest
   SET color = color_canon(color)
 WHERE color IS NOT NULL
   AND color_canon(color) IS NOT NULL
   AND color IS DISTINCT FROM color_canon(color);
