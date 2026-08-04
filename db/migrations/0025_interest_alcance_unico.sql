-- Un usuario no puede tener dos intereses con el mismo alcance (#149).
--
-- Sale de una pérdida de datos silenciosa: la API solo sabía BORRAR un interés, y
-- `notification.interest_id` es `ON DELETE CASCADE` (0005), así que dejar de seguir una prenda se
-- llevaba por delante todas las filas de aviso que colgaban de ella — y con ellas la protección del
-- `UNIQUE (interest_id, variant_id, price_event_key)` que impide repetir el aviso del mismo evento
-- de precio. Reproducido en QA el 04/08/2026: el interés #1 de Zara tenía 13 avisos con `sent_at`.
--
-- El arreglo tiene dos mitades y esta restricción es la que sostiene la segunda: la baja pasa a ser
-- lógica (`active = false`) y volver a seguir el mismo alcance REACTIVA la fila en vez de crear otra.
-- Conservar el historial sin conservar el `interest_id` no arreglaría nada: el aviso del mismo
-- evento volvería a salir por un id nuevo. Por eso hace falta poder identificar «el mismo interés»,
-- y eso es exactamente lo que nombra esta clave.
--
-- Se declara como RESTRICCIÓN DE TABLA y no como índice suelto porque el alta la usa por nombre
-- (`INSERT ... ON CONFLICT ON CONSTRAINT interest_alcance_uniq DO UPDATE`), sin depender de que
-- Postgres infiera el índice correcto a partir de la lista de columnas.
--
-- `NULLS NOT DISTINCT` (PostgreSQL 15+; el cluster corre 16.4) no es un detalle: aquí un NULL
-- significa «cualquiera», no «desconocido». Con la semántica por defecto —cada NULL distinto de
-- todos los demás— dos intereses de «cualquier talla» nunca colisionarían, el ON CONFLICT no
-- dispararía jamás y la reactivación no llegaría a ocurrir nunca.
--
-- Las columnas son las nueve del ALCANCE (a qué prenda sigue), no las de la REGLA de aviso
-- (`min_discount_pct`, `compare_base`, `window_days`): volver a seguir lo mismo con otro umbral es
-- cambiar de opinión sobre el mismo seguimiento, no abrir uno nuevo. `active` tampoco entra, o la
-- clave dejaría de impedir el duplicado justo en el caso que existe para cubrir.
--
-- Comprobado antes de escribirla que no hay filas que la violen: 0 grupos duplicados en `dev`
-- (0 intereses) y en `qa` (3 intereses), el 04/08/2026. Importa porque el migrador del web corre
-- como initContainer: una restricción que no se puede aplicar deja el despliegue sin arrancar.
ALTER TABLE interest
    ADD CONSTRAINT interest_alcance_uniq
    UNIQUE NULLS NOT DISTINCT (user_id, retailer_id, product_id, variant_id,
                               gender, section, category, size, color);
