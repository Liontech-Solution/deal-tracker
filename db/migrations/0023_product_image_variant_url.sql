-- La galería necesita distinguir dos ARTÍCULOS que la tienda publica con el mismo nombre de color.
--
-- La 0011 clavó `product_image` por el TEXTO del color, y dejó escrito el porqué: introducir una
-- entidad `product_color` con clave estable obligaba a migrar `variant`, la ingesta y el matching
-- para lo que el join por nombre ya resolvía. Sigue siendo cierto para seis de las siete tiendas.
--
-- H&M rompe el supuesto de fondo —que dentro de un producto el nombre del color identifica al
-- color—. Allí una fila del listado es producto+color y agrupamos por la raíz de 7 dígitos del
-- `articleId`, así que un producto nuestro junta varios artículos de la tienda; y la tienda a veces
-- publica DOS artículos del mismo modelo con el mismo `colorName`, cada uno con su ficha, su URL y
-- sus fotos. Medido en `dev` el 03/08/2026 (#123):
--
--     tienda     grupos (producto, talla_canon, color_canon)   misma URL   URLs distintas
--     lefties                    815                              815            0
--     hipercor                   108                              108            0
--     hm                         854                               51          803   <- 105 productos
--
-- Las 803 son las que aquí importan. Los 51 restantes de H&M, y los de Lefties e Hipercor, son la
-- misma prenda publicada dos veces: comparten URL y se quieren seguir fusionando.
--
-- Así que el discriminador no es el color: es **la ficha de la tienda**, o sea la URL — el mismo
-- criterio que el PR #121 (#108) adoptó para la clave de agrupación de la ficha y del aviso.
-- Añadirla solo puede partir galerías, nunca unirlas.
ALTER TABLE product_image ADD COLUMN variant_url TEXT;

COMMENT ON COLUMN product_image.variant_url IS
    'URL de la ficha de la tienda a la que pertenece la foto (= variant.url). NULL cuando la tienda '
    'no distingue dos artículos bajo el mismo nombre de color, que es el caso de las otras seis.';

-- ── Por qué NO entra en el UNIQUE ─────────────────────────────────────────────────────────────
--
-- El `UNIQUE (product_id, color, position)` de la 0011 se queda como está, y la ingesta sigue
-- numerando `position` por NOMBRE DE COLOR, no por (color, url).
--
-- No es pereza: es lo que sostiene la consulta de la TARJETA del catálogo
-- (`applyReprImages` en `catalog.service.ts`), que hace join por `i.position = 0` y espera
-- exactamente UNA fila por (product_id, color). Si el discriminador entrase en la clave, los dos
-- artículos «Azul marino» tendrían los dos una foto en `position = 0` y la tarjeta escogería una
-- al azar según el orden que devolviese el plan. La tarjeta enseña el color representativo y con
-- una foto cualquiera de ese color le vale; quien necesita separar las dos referencias es la
-- ficha, y para eso le basta esta columna como atributo de filtrado.
--
-- ── Sin backfill, igual que la 0011 ───────────────────────────────────────────────────────────
--
-- La URL por foto nunca se guardó, así que no hay de dónde sacarla para lo ya ingerido: se queda a
-- NULL y se puebla según el detalle condicional y el refresco forzado (`last_detail_at`, 0009)
-- vuelvan a pedir cada producto. Mientras tanto la ficha cae al respaldo «fotos de este color sin
-- URL atribuida», que es exactamente el comportamiento de hoy. Ninguna tienda regresiona por
-- aplicar esta migración sin pasar el scraper detrás.
