-- Libro de pasadas ya evaluadas por el job de matching (#240).
--
-- Hasta aquí el lote se acotaba solo con `job_state.last_scrape_run_id`, guardando el **mayor id
-- visto**. Eso da por hecho que los `scrape_run` se completan en orden de id, y nada lo garantiza:
-- la 40 puede seguir en transacción cuando la 41 commitea, el matching marca 41, la 40 commitea
-- después y sus filas nacen **por debajo** de la marca. Ningún lote futuro las mira: las bajadas de
-- esa pasada no se avisan nunca, sin ninguna señal — el Job sale en verde y la marca avanza con
-- normalidad. Medido en QA el 06/08/2026: la pasada 33 (`hm`) terminó 11 s después que la 34
-- (`lefties`), la marca quedó en 34 y sus 25.544 filas quedaron fuera para siempre. No costó ningún
-- aviso de milagro —ninguna de esas filas bajaba de su mínimo de 90 días—, que es justo por lo que
-- nadie se enteró.
--
-- La corrección que la issue proponía —acotar por `min(id)` de las pasadas en curso— NO es
-- implementable: el `INSERT INTO scrape_run` vive dentro de la transacción de la propia pasada
-- (`ingest.py`), así que una pasada en vuelo no deja ningún rastro visible desde otra sesión. No hay
-- forma de preguntar "¿queda alguna abierta?".
--
-- Así que se invierte la pregunta: en vez de "¿hasta dónde he llegado?", **"¿qué he procesado ya?"**.
-- Una pasada rezagada aparece cuando commitea, no está en el libro, y entra sola en el lote
-- siguiente. No hace falta verla mientras está abierta, que era lo imposible.
--
-- `job_state.last_scrape_run_id` no desaparece: pasa a ser el **suelo** —todo id por debajo está
-- resuelto— y el libro solo guarda lo que hay por encima. El suelo avanza por el prefijo contiguo
-- ya resuelto, así que el libro se mantiene en unas pocas filas.
--
-- Sin FK a `scrape_run (id)`, igual que `notification.variant_id` en la 0005: es tabla del servicio
-- **web** y no mete integridad referencial en tablas que son del **scraper**. Aquí además hace
-- falta: el libro puede nombrar una pasada que un `TRUNCATE ... CASCADE` del scraper se llevara por
-- delante, y eso no debe reventar el job.
--
-- Un id quemado por una pasada que hizo rollback (la abortada no deja fila; `_record_failed_run`
-- inserta una **nueva**) es un hueco permanente en la secuencia, y frena el avance del suelo: desde
-- ahí el libro crece una fila por pasada. Se asume a propósito — son ~9 pasadas semanales en QA,
-- unas 470 filas al año sobre una PK — a cambio de no meter ninguna constante de tiempo que
-- decidiera cuándo un hueco "ya" puede saltarse. Con constante, una pasada más lenta que ella se
-- seguiría perdiendo en silencio, que es el fallo que esta tabla viene a quitar.
CREATE TABLE matching_scanned_run (
    scrape_run_id BIGINT      PRIMARY KEY,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
