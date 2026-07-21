-- Histéresis de la detección de bajas: pasadas consecutivas sin ver la fila.
-- La ingesta lo pone a 0 en cuanto la fila se vuelve a ver, lo incrementa cuando falta
-- (solo en ámbitos escaneados y no sospechosos) y solo marca `delisted_at` al alcanzar
-- `SCRAPER_DELIST_MIN_MISSES`. Así un blip de una pasada no descataloga nada.
-- Las filas existentes arrancan en 0: o se vieron en la última pasada, o ya están de baja.
ALTER TABLE product ADD COLUMN missing_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE variant ADD COLUMN missing_streak INTEGER NOT NULL DEFAULT 0;
