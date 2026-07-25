-- Galería de fotos por color.
--
-- `product.image_url` (migración 0010) guarda UNA foto: la del primer color. Eso arrastra un
-- problema que no es estético sino de coherencia: el precio cuelga de `variant`, y la variante es
-- (talla, color), así que **el precio ya varía por color** en las tiendas que scrapeamos (en Zara
-- la huella del listado es literalmente `colorId:precio`). Con una sola foto, la ficha deja elegir
-- color y cambia el precio mientras la imagen se queda quieta, y la tarjeta del catálogo puede
-- enseñar la foto de un color con el precio de otro. Esta tabla permite que la foto que se ve sea
-- siempre la del color cuyo precio se ve.
--
-- El dato ya viaja en payloads que la ingesta YA cachea (Zara: `detail.colors[].xmedia[]`, hasta 11
-- por color; Sfera: `_my_colors[].all_images[]`), así que poblarla no cuesta ni una petición extra.
--
-- Se clava por el TEXTO del color, el mismo que ya guarda `variant.color`, en vez de introducir una
-- entidad `product_color` con clave estable: eso obligaría a migrar `variant`, la ingesta y el
-- matching para lo que aquí resuelve el join por nombre. A cambio, el scraper tiene la obligación
-- de sacar el nombre de color de la imagen y el de la variante del MISMO campo (si se desalinean,
-- el join falla en silencio); los tests de parseo fijan esa invariante por tienda.
CREATE TABLE product_image (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES product (id) ON DELETE CASCADE,
    color      TEXT,          -- mismo valor que variant.color; NULL = foto sin color atribuible
    position   INT    NOT NULL,  -- orden dentro del color (0 = la que representa a ese color)
    url        TEXT   NOT NULL,
    UNIQUE (product_id, color, position)
);

-- La ficha pide la galería entera de un producto. El UNIQUE de arriba ya sirve para la otra
-- lectura, la de la tarjeta: (product_id, color, position = 0).
CREATE INDEX ix_product_image_product ON product_image (product_id);

-- Sin backfill posible: las fotos secundarias nunca se guardaron. Las fichas existentes arrancan
-- sin galería (la SPA cae a `product.image_url`, como hasta ahora) y la estrenan según el refresco
-- forzado del detalle (`last_detail_at`, migración 0009) les vuelva a pedir la ficha.
