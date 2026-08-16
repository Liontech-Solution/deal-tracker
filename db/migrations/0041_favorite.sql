-- Guardar una prenda sin pedir un aviso: la lista de favoritos (#435).
--
-- Hasta ahora lo único que un usuario podía guardar era un SEGUIMIENTO, y un seguimiento existe
-- para avisar por Telegram: lleva umbral, base de comparación y ventana de días. No había forma de
-- decir «esta prenda me gusta, guárdamela» sin suscribirse de paso a una notificación.
--
-- Por qué tabla propia y no una bandera en `interest`. Los tres motivos se midieron antes de
-- escribir esto, y el tercero es el que manda:
--
--   1. `interest` no es «una prenda», es un CRITERIO: su alcance son ocho columnas y
--      `interest_target_present_chk` (0004) solo exige que UNA no sea NULL. En QA conviven
--      intereses de una tienda entera, de «género + talla + color» y de producto concreto. Un
--      favorito es siempre una prenda; no es la misma cosa.
--   2. Colisionarían por identidad: `interest_alcance_uniq` (0025) es UNIQUE NULLS NOT DISTINCT
--      sobre usuario + esas ocho columnas, así que «favorito del producto X» y «seguimiento del
--      producto X» tienen alcance IDÉNTICO y serían la MISMA FILA. O sea que un usuario no podría
--      tener las dos cosas sobre la misma prenda, que es justo lo que esta funcionalidad pide.
--   3. Y el modo de fallo sería el peor posible: la ÚNICA condición de notificabilidad de todo el
--      sistema es el `JOIN interest i ON i.active` de `matching.service.ts`. Una fila de favorito
--      viviendo en `interest` DISPARA avisos de Telegram salvo que se parchee ese JOIN, y olvidarlo
--      es silencioso para nosotros y ruidoso en el móvil del usuario. Con tabla aparte es imposible
--      por construcción, y `matching.service.ts` no se toca.
--
-- `active` tampoco servía como «no avisar»: desde #149 el borrado de un seguimiento ya produce
-- `active = false`, así que confundiría «dejé de seguirlo» con «lo tengo guardado».
CREATE TABLE IF NOT EXISTS favorite (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Se va con el usuario, como `interest` y `notification`: lo guardado no sobrevive a la cuenta.
    user_id    BIGINT      NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    -- SIN FK dura, exactamente por el mismo motivo que `interest.product_id` (0004): el producto es
    -- propiedad del scraper y `delisted_at` NO borra la fila, pero un favorito tiene que sobrevivir
    -- igualmente a una baja Y a su resurrección. Con FK, cualquier limpieza futura del catálogo se
    -- llevaría por delante lo que el usuario guardó.
    product_id BIGINT      NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- El favorito es del PRODUCTO entero: un corazón por producto, sin elegir talla. La talla se
    -- elige después y solo si se convierte en seguimiento, que es lo que ya sabe hacer `FollowModal`
    -- con `variantId` opcional. Esta clave es además la que hace idempotente el alta
    -- (`ON CONFLICT DO NOTHING`): marcar dos veces el mismo corazón no falla ni duplica.
    CONSTRAINT favorite_user_product_uniq UNIQUE (user_id, product_id)
);

-- El único acceso que hay: «los favoritos de este usuario, los últimos primero», que es como los
-- pinta `/favoritos`. El UNIQUE de arriba ya indexa (user_id, product_id), pero no sirve para
-- ordenar por fecha sin releer la tabla.
CREATE INDEX IF NOT EXISTS ix_favorite_user ON favorite (user_id, created_at DESC);

COMMENT ON TABLE favorite IS
    'Prendas guardadas por el usuario SIN pedir aviso. Deliberadamente fuera de `interest`: una '
    'fila aquí no puede generar ninguna notificación de Telegram, porque el matching solo mira '
    '`interest`. Ver #435.';
