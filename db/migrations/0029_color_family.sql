-- Familia de color: `color_family(text)`.
--
-- Tercer piso del plegado del color, y el que la 0015 dejó declarado FUERA a propósito (issue #291):
--
--     «No agrupa familias. 'Azul claro', 'Azul medio' y 'Azul oscuro' son colores distintos para
--      quien compra, y 'Kaki' / 'Khaki' es una decisión de vocabulario. Agrupar por familia es
--      producto, no formato.»
--
-- Sigue siendo producto y no formato. La diferencia es que ahora el producto lo pide: el panel de
-- filtros ofrecía **2.859 chips de color** en `ropa` (63 KB de payload), y el reporte de #291 es
-- literal — «la navegación vía móvil se hace muy cansada con tantos colores».
--
-- LO QUE ESTA FUNCIÓN NO ES. No sustituye a `color_canon` ni la toca. El color específico se sigue
-- guardando en `variant.color`, se sigue enseñando en la tarjeta y en la ficha, y **el matching
-- sigue casando por `color_canon`** (`matching.service.ts`), que es lo que hace que un aviso de
-- 'azul marino' no salte por un 'azul cielo'. Lo único que se pliega aquí es **lo que ofrece el
-- panel de filtros**, y en consecuencia el parámetro `color` del listado del catálogo.
--
-- ⚠️ CONSECUENCIA QUE HAY QUE TENER PRESENTE: desde esta migración, `color` significa **cosas
-- distintas** en dos sitios que se parecen mucho:
--
--     GET /api/catalog/products?color=azul   -> FAMILIA (color_family)
--     interest.color = 'azul marino'         -> COLOR CANÓNICO EXACTO (color_canon)
--
-- Es deliberado: el filtro existe para encontrar, y el aviso para no mentir. Un interés plegado a
-- familia dispararía por cualquier azul, que es exactamente el fallo silencioso que la 0015 vino a
-- arreglar en sentido contrario.
--
-- POR QUÉ NO SE TOCA `color_canon`, aunque «plegar más» pareciera su trabajo: la 0015 escribió la
-- obligación de terminar con `REINDEX INDEX ix_variant_color_canon` a cualquiera que cambie su
-- cuerpo, porque el índice guarda los valores YA calculados y un cambio lo deja obsoleto en
-- silencio — y entonces el filtro devuelve **filas equivocadas, no un error**. Construyendo encima
-- en vez de dentro, ese índice sigue siendo válido y no hace falta reindexar nada.
--
-- PERO SE QUEDA SIN CONSUMIDOR, y conviene decirlo aquí en vez de que alguien lo descubra dentro de
-- un año. `ix_variant_color_canon` es parcial por `delisted_at IS NULL` y solo sirve a una igualdad
-- `color_canon(v.color) = <expr>` evaluada sobre TODAS las variantes vivas. Ese query era
-- exactamente el filtro del catálogo, y es el que esta migración se lleva a `color_family`. Lo que
-- queda llamando a `color_canon` **no calza con ese patrón**: el matching la evalúa sobre la CTE
-- `batch` (las filas de la pasada, sin `delisted_at` por ningún lado), el alta de intereses hace un
-- `SELECT color_canon($1)` sin tabla, y la ficha agrupa dentro de un solo `product_id`.
--
-- No se borra aquí a propósito: la función sigue viva y muy usada, el índice sigue siendo válido, y
-- tirar un índice es una decisión con su propio riesgo que no pinta nada en la issue del panel de
-- filtros. Queda anotado como lo que es —mantenimiento muerto— para decidirlo aparte.
--
-- Y hay un segundo regalo por construir encima: **el ctype**. La 0021 ya pliega los acentos con
-- `translate()` ANTES del `lower()` (#105), así que a esta función el texto le llega siempre en
-- minúsculas y con el acento intacto, tanto bajo el locale de CI como bajo el `UTF8 | C | C` del
-- cluster, donde `lower()` no baja las acentuadas. Las reglas de abajo pueden escribir 'marrón' o
-- 'añil' sin repetir ese trabajo.
--
--
-- ── LA MEDIDA (11/08/2026, `deal_tracker_qa`: 16.517 productos vivos, 2.940 colores canónicos) ──
--
-- Emulando esta función con los 17 `SWATCHES` que ya existían en `frontend/src/lib/colors.ts`:
--
--                                        solo los 17 SWATCHES     con el vocabulario de abajo
--     familias                                 17                           19
--     variantes sin familia                 12.433 (7,7 %)                 74 (0,04 %)
--     valores distintos sin familia            258                           7
--     PRODUCTOS sin ningún color filtrable   1.093 (6,6 %)                  11 (0,07 %)
--
-- La última fila es la que importa, y es la pregunta que abrió esta migración: *¿alguien puede
-- perder una prenda por buscar un color que la prenda sí tiene?* Solo desaparecen de una vista con
-- filtro de color puesto los productos cuyos colores caen TODOS en `NULL`. Con los 17 originales
-- eran 1.093; con el vocabulario medido son 146.
--
-- Importa porque **el buscador libre no los repesca**: `fold()` en `catalog.service.ts` cubre
-- `p.name || category || gender` y **el color no entra ahí**. Lo que no tenga familia no es
-- alcanzable por ningún camino de color.
--
-- EL HALLAZGO QUE DECIDIÓ EL ALCANCE: de los 258 valores huérfanos, la inmensa mayoría **no eran
-- estampados, eran huecos de vocabulario**. 'turquesa' y sus tonos (1.768 variantes), 'kaki' (855 —
-- la tabla tenía `khaki|caqui` pero no la grafía castellana), 'fucsia' (613), 'índigo'/'indigo'
-- (464), 'berenjena' (370), 'beis' (324 — la tabla tenía 'beige'). O sea que el trabajo de verdad
-- era escribir vocabulario, no inventar un mecanismo.
--
--
-- ── DECISIONES ──
--
-- 1. SE PLIEGA POR EL SEGMENTO ANTERIOR A LA '/', no por la cadena entera. Medido: **385 colores
--    (13,5 %) caen en la familia equivocada** mirando la cadena completa, porque lo que va detrás
--    de la barra es el nombre del dibujo o de la licencia: 'amarillo claro/bluey' se archivaría
--    como AZUL, y 'amarillo claro/blanco' como BLANCO.
--
-- 2. EL ORDEN DE LAS REGLAS ES PARTE DEL CONTRATO, porque casi todas son subcadenas de alguna otra.
--    Las que se ganan el sitio, con el valor real que las obliga:
--      * 'marino' y 'celeste' ANTES que 'azul'      ('azul marino' es marino, no azul)
--      * 'gris' ANTES que 'beige'                   ('gris topo' es gris; 'topo' suelto es beige)
--      * 'gris' (por 'vigoré') ANTES que 'blanco'   ('perla vigoré' es gris; 'perla' suelto, blanco)
--      * 'marrón' ANTES que 'rojo'                  ('marrón rojizo' es marrón)
--
-- 3. 'estampado' VA LA ÚLTIMA, y es lo contrario de lo que parece natural. Es el cajón de lo que no
--    nombra ningún color ('rayas', 'multicolor', 'leopardo', 'bicolor'), pero tiene que ceder ante
--    cualquier color que aparezca antes: 'blanco rayas' es BLANCO con rayas, no un estampado sin
--    color. Poniéndola la primera, todo compuesto de color + dibujo se archivaría como estampado.
--
-- 4. ES UNA FAMILIA, NO UN CAJÓN 'otros'. Se ofrece como chip a propósito: hoy 'rayas' (526),
--    'multicolor' (841), 'estampado' (559) y 'leopardo' (114) SON chips —perdidos entre 2.859, pero
--    están—, y mandarlos a `NULL` habría quitado una capacidad que existe, sin que el buscador
--    pudiera compensarlo. Son 2.040 variantes.
--
-- 5. 'turquesa' ES FAMILIA PROPIA. 1.768 variantes es demasiado para esconderlas dentro de 'azul' o
--    'celeste', y es un color que se busca por su nombre.
--
-- 6. LOS AMBIGUOS, resueltos por lo que esperaría quien compra, y escritos aquí para que se puedan
--    discutir con un nombre y no de memoria:
--      * 'topo' (306) -> beige. Es el *taupe*: pardo grisáceo. Pero 'gris topo' se lo queda 'gris'.
--      * 'visón' (194), 'natural' (94), 'nude' (156), 'tierra' (36) -> beige.
--      * 'perla vigoré' (162) -> gris. 'vigoré' es el jaspeado, y el jaspeado de Lefties es gris.
--      * 'jabón' (146) -> crema. Es el blanco roto amarillento, no un verde.
--      * 'hielo' (196) -> blanco. Azulado, sí, pero lo que se ve es blanco.
--      * 'petróleo' (126) -> azul. Es el *petrol blue*, aunque tenga verde dentro.
--      * 'cerúleo' (96) y 'añil' (68) -> azul.
--      * 'óxido' (94), 'caldero' (30) y 'cobre' (6) -> teja, con 'rust', que es su misma idea.
--      * 'limón' (24) -> amarillo, NO verde. 'lima' (308) sí es verde. Se parecen y no son lo mismo.
--      * 'cuero' (246), 'caramelo' (61), 'toffee' (36), 'café' (27), 'tabaco' (23) -> marrón.
--      * 'burgundy' (236), 'vino' (81), 'carmín' (61), 'fresa' (279), 'frambuesa' (92) -> rojo.
--      * 'buganvilla' (124) -> rosa; 'berenjena' (370) y 'ciruela' (279) -> morado.
--
-- 7. LO QUE SE QUEDA EN `NULL`, y por qué no es un descuido. Medido sobre QA, son **7 valores y 74
--    variantes (0,04 %)**, y ninguno nombra un color:
--
--        1-114 (13)   1-251 (11)   1-905 (10)   1-126 (10)   default (20)   único (5)   béisbol (5)
--
--    Los cuatro primeros son códigos internos que la 0016 no atrapa porque **no son SOLO dígitos**
--    (llevan el guion), así que llegan aquí como texto. `NULL` es como estas funciones dicen «no hay
--    etiqueta», y `pickColors()` ya lo filtra fuera de la faceta desde #51.
--
--    El efecto sobre lo que de verdad importa: **11 productos de 16.517 (0,07 %)** se quedan sin
--    ningún color filtrable, frente a los 1.093 (6,6 %) que dejaban los 17 SWATCHES originales.
--
-- 8. LAS GRAFÍAS ALTERNATIVAS VAN EN LAS REGLAS ('índigo'/'indigo', 'carbón'/'carbon',
--    'petróleo'/'petroleo'). No es por la caja —de eso ya se encargó la 0021— sino porque las
--    tiendas escriben el mismo color con acento y sin él, y las dos formas están en los datos.
--
-- Es IDEMPOTENTE: color_family(color_family(x)) = color_family(x), porque el nombre de cada familia
-- pertenece a su propia familia ('azul' -> 'azul'). Eso es lo que permite aplicarla a los dos lados
-- de la comparación del filtro sin razonar sobre cuál venía ya plegado, igual que `color_canon`.
CREATE OR REPLACE FUNCTION color_family(color text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN seg ~ 'negro|black'                                             THEN 'negro'
        -- 'platead' va aparte de 'plata' y no es redundante: 'plateado' NO contiene 'plata'.
        WHEN seg ~ 'gris|grey|gray|piedra|antracita|plata|platead|plomo|marengo|ceniza|carb[oó]n|vigor[eé]'
                                                                             THEN 'gris'
        WHEN seg ~ 'marino|navy'                                             THEN 'marino'
        WHEN seg ~ 'turquesa|turquoise'                                      THEN 'turquesa'
        WHEN seg ~ 'celeste|cielo'                                           THEN 'celeste'
        WHEN seg ~ 'azul|blue|niebla|denim|[ií]ndigo|a[ñn]il|cer[uú]leo|petr[oó]leo'
                                                                             THEN 'azul'
        WHEN seg ~ 'salvia|sage'                                             THEN 'salvia'
        WHEN seg ~ 'verd|green|oliva|kh?aki|caqui|lima|pistacho|menta|musgo|kiwi|esmeralda|aceite'
                                                                             THEN 'verde'
        WHEN seg ~ 'teja|terracota|ladrillo|rust|[oó]xido|caldero|cobre'     THEN 'teja'
        -- Con tilde: el nombre de la familia ES el chip, y la 0015 conserva el acento a propósito
        -- para no degradarlo. La SPA lo pinta con `capitalize()`, así que se ve «Marrón».
        WHEN seg ~ 'marr[oó]n|brown|chocolate|tostado|cuero|caramelo|toffee|caf[eé]|tabaco'
                                                                             THEN 'marrón'
        WHEN seg ~ 'rojo|rojizo|red|granate|burdeos|burgundy|vino|cereza|carm[ií]n|fresa|frambuesa|geranio'
                                                                             THEN 'rojo'
        WHEN seg ~ 'rosa|pink|coral|fucsia|chicle|buganvilla'                THEN 'rosa'
        WHEN seg ~ 'naranja|orange|melocot[oó]n|salm[oó]n|mandarina|calabaza|albaricoque|papaya|pomelo'
                                                                             THEN 'naranja'
        WHEN seg ~ 'mostaza|amarill|yellow|dorado|ocre|oro|lim[oó]n|paja|mantequilla'
                                                                             THEN 'amarillo'
        WHEN seg ~ 'morado|lila|violeta|purple|malva|p[uú]rpura|berenjena|ciruela|mora|lavanda'
                                                                             THEN 'morado'
        WHEN seg ~ 'beige|beis|arena|camel|sand|nude|topo|vis[oó]n|natural|tierra'
                                                                             THEN 'beige'
        WHEN seg ~ 'crema|cream|hueso|vainilla|jab[oó]n'                     THEN 'crema'
        WHEN seg ~ 'blanc|white|crudo|marfil|hielo|perla'                    THEN 'blanco'
        -- La última, y a propósito: lo que no nombra ningún color. Ver la decisión 3.
        WHEN seg ~ 'multicolor|estampad|raya|leopardo|animal print|bicolor|combinado|varios'
                                                                             THEN 'estampado'
    END
    FROM (SELECT btrim(split_part(color_canon(color), '/', 1)) AS seg) t;
$$;

-- El filtro del catálogo pasa a comparar `color_family(v.color) = color_family(<color>)`, y sin
-- índice eso evalúa la función una vez por variante viva. Es el mismo caso que resolvieron el
-- índice de la 0014 para la talla y el de la 0015 para el color, y con MUCHO más motivo: son ~20
-- regexes encadenados sobre el resultado de `color_canon`, así que cada evaluación cuesta lo que
-- costaba `color_canon` entera, multiplicado.
--
-- Medido en local sobre 150.000 variantes con los nombres de color reales de QA, Postgres 16:
--
--     sin índice   14.075 ms (Parallel Seq Scan)        con índice   3,4 ms (Bitmap Index Scan)
--
-- Cuatro órdenes de magnitud, contra los dos que medía la 0015 para `color_canon` (14,6 ms -> 0,11
-- ms). En el cluster —que son Raspberry Pi— esto no es una optimización, es la diferencia entre que
-- el filtro responda y que se quede pensando.
--
-- Parcial por `delisted_at IS NULL` porque ese es el otro filtro que llevan todas las lecturas del
-- catálogo, igual que en la 0012, la 0014 y la 0015.
--
-- ⚠️ MISMA OBLIGACIÓN QUE LA 0015, y aquí es más probable que toque: el índice almacena los valores
-- YA calculados, así que **cualquier migración futura que añada vocabulario a `color_family`**
-- —y va a haberla, cada tienda nueva trae nombres nuevos— tiene que terminar con:
--
--     REINDEX INDEX ix_variant_color_family;
--
-- Sin eso el panel sigue ofreciendo los chips viejos y el filtro devuelve resultados mal, sin error.
CREATE INDEX ix_variant_color_family ON variant (color_family(color))
    WHERE delisted_at IS NULL;
