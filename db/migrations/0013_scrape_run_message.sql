-- Motivo por el que una pasada terminó como terminó. Hasta ahora una pasada que fallaba hacía
-- rollback y NO dejaba fila en `scrape_run`: el único rastro estaba en los logs del pod, que es
-- justo cómo Zara pudo pasarse cuatro días sin poder ingerir sin que nadie se enterara (#41).
-- Ahora el fallo se registra con `status = 'failed'` y este mensaje dice qué pasó, para que la
-- pregunta "¿cuándo dejó de funcionar esta tienda?" se responda con una consulta y no leyendo
-- logs que además rotan.
ALTER TABLE scrape_run ADD COLUMN message TEXT;

-- La consulta que esto habilita es "últimas pasadas de esta tienda, y cuáles fallaron".
CREATE INDEX ix_scrape_run_retailer_time ON scrape_run (retailer_id, started_at DESC);
