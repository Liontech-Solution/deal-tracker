-- Marca de agua de los jobs del servicio web (dominio: servicio web).
-- El job de matching evalúa solo los precios nuevos: `price_history.scrape_run_id > last_scrape_run_id`.
-- Se guarda el mayor id procesado en vez de mirar "el último scrape_run" porque (a) hay un
-- scrape_run POR TIENDA y pasada, y (b) así una ejecución perdida se recupera sola en la siguiente
-- (procesa el hueco) en lugar de saltárselo.
-- Es seguro leer por id: el scraper ingesta cada pasada en UNA transacción, así que nunca se
-- observa un run a medias.
CREATE TABLE job_state (
    job                TEXT PRIMARY KEY,      -- identificador del job (p.ej. 'matching')
    last_scrape_run_id BIGINT      NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
