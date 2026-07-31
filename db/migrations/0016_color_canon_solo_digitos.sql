-- Color canónico, segunda pasada: un nombre que son SOLO dígitos no es un nombre (issue #51).
--
-- La 0015 dejó escrito, como límite deliberado, que '107', '140' y '771' —10 productos— no tenían
-- arreglo posible: se creía que eran el código interno de Sfera al que le faltaba el nombre detrás,
-- y que recuperarlo exigía su PDP, tras Akamai. **Esa atribución era falsa**, y por eso vuelve a
-- abrirse aquí. Medido el 31/07/2026 sobre `dev`, los tres valores son de **Zara**:
--
--     zara | 107 | 2 productos      zara | 140 | 2      zara | 771 | 6
--
-- Y contra su API, que es pública y no está tras ningún anti-bot
-- (`GET /es/es/products-details?productIds=545461874&ajax=true`):
--
--     {"id":"700", "name":"Marrón", "hexCode":"#877361", "reference":"C04441700700000-I2026"}
--     {"id":"771", "name":"771",    "hexCode":"#E6E6DF", "reference":"C04441700771000-I2026"}
--
-- O sea: **Zara escribe el id del color en el campo del nombre**. El scraper lee el campo correcto
-- y no se está dejando nada — revisado el objeto de color entero (`attributes`, `extraInfo`,
-- `rawDescription`, `reference`, `relations`), no hay ningún otro campo con un nombre.
--
-- ES OTRO PROBLEMA QUE EL DE LA 0015, y por eso sí tiene solución aquí. Allí el número era un
-- PREFIJO ('120 Crudo') y quitarlo dejaba un nombre; el caso sin nombre parecía el mismo fenómeno
-- llevado al extremo. No lo es: aquí el número **es el nombre entero**, y entonces la pregunta deja
-- de ser "¿cómo lo recupero?" —no se puede— y pasa a ser "¿esto debe ofrecerse como color?".
--
-- La respuesta no depende de la tienda: **un color cuyo nombre entero es un número no lo puede
-- elegir nadie**. Un chip '771' no lo pincha ningún usuario, y un interés guardado con ese valor no
-- describe nada. Así que no pertenece a la faceta ni al emparejamiento del aviso, y `NULL` —que es
-- como esta función dice "no hay etiqueta canónica"— es exactamente lo que significa.
--
-- LO QUE NO SE TOCA, y es la razón de arreglarlo aquí y no en el scraper: `variant.color` sigue
-- guardando '771' crudo. `product_image` está clavada por ese TEXTO (migración 0011), y ese join
-- sostiene la foto de la tarjeta y la galería de la ficha. Canonicalizar solo la comparación deja
-- la galería de esos 10 productos intacta; escribir NULL en la columna la habría roto en silencio.
-- La ficha sigue enseñando lo que la tienda dice, que es lo honesto; lo que desaparece es el chip.
--
-- Se exige `^[0-9]+$` sobre el valor YA plegado, y no hace falta afinar más: los casos que la 0015
-- protegía ('2 tonos', '12 rayas', '1200 Crudo') llevan letras, así que no son "solo dígitos" y
-- siguen intactos. Y compone con la regla anterior: '120 456' -> '456' -> NULL.
--
-- LOS INTERESES YA GUARDADOS. `interest.color` NO se recalcula: se canonicaliza al dar de alta y
-- se queda escrito. Un interés creado mientras la 0015 estaba desplegada pudo guardar '771' —era un
-- chip válido en la faceta—, y tras esta migración ese interés dejaría de casar con NADA, en
-- silencio: `color_canon('771') = color_canon(<color>)` pasa a ser `NULL = ...`, que es falso, y la
-- rama `i.color IS NULL` («cualquier color») no le aplica porque la columna no es NULL.
--
-- Medido antes de aplicarla, igual que hizo la 0015 y por la misma razón: **0 intereses en `dev` y
-- 0 en `qa`** (`SELECT count(*) FROM interest` el 31/07/2026, con 0 de ellos con color). No hay
-- nada que backfillear. Si algún día hubiera intereses vivos, esta migración necesitaría además un
-- `UPDATE interest SET color = NULL WHERE color_canon(color) IS NULL` — que los convierte en «de
-- cualquier color»— o borrarlos, y eso es una decisión de producto, no de formato.
--
-- Sigue siendo IDEMPOTENTE: color_canon(NULL) = NULL porque la función es STRICT.
CREATE OR REPLACE FUNCTION color_canon(color text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE WHEN canon ~ '^[0-9]+$' THEN NULL ELSE canon END
    FROM (
        SELECT regexp_replace(
                   lower(btrim(regexp_replace(color, '\s+', ' ', 'g'))),
                   '^[0-9]{3} (.+)$', '\1') AS canon
    ) t;
$$;

-- OBLIGATORIO al cambiar el cuerpo de `color_canon`, y la 0015 lo dejó avisado: el índice guarda
-- los valores YA calculados con la definición vieja. Sin esto el filtro por color no da un error —
-- da FILAS EQUIVOCADAS, que es mucho peor.
REINDEX INDEX ix_variant_color_canon;
