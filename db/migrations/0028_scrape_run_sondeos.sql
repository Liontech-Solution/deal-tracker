-- Los sondeos de la confirmación activa dejan de esconderse dentro de `errors` (#261).
--
-- Hasta ahora `errors` sumaba tres cosas distintas —ámbitos sospechosos, hojas caídas y sondeos de
-- baja sin resolver— y el detalle solo se decía por stdout. Eso hizo que tres validaciones seguidas
-- de QA leyeran el `errors = 60` de Zara como "algo va mal en la ingesta" cuando lo que decía era
-- otra cosa: que 60 productos llevaban pasadas sin aparecer en ningún listado y no se les había
-- podido preguntar. Mismo problema que arregló la 0013 para el camino de fallo, un nivel más abajo.
--
-- Medido al abrir estas columnas (QA, 10/08/2026): de 40 productos de Zara ausentes 14+ días, el
-- sondeo llamaba vivos a los 40 y **39 tenían stock de verdad**. O sea que estos números NO son
-- errores ni prendas retiradas atrapadas: son prendas a la venta que el listado ha dejado de ver, y
-- meterlas en `errors` convertía una cobertura incompleta en una alarma de ingesta.
--
-- La distinción que faltaba, y que es toda la diferencia entre una alarma útil y uná que nadie
-- mira: **no cupo en el tope** (`probes_over_cap`) es la rutina de una tienda con muchos
-- candidatos, mientras que **sondeado y sin veredicto** (`probes_unresolved`) es la tienda
-- negándose a contestar. Antes eran el mismo número. Ahora `errors` se queda con lo que sí es un
-- error —sospechosos + hojas caídas + sondeos sin veredicto— y el resto vive aquí.
--
-- La consulta que habilitan es "¿el pool de candidatos a baja crece o se drena?":
-- `probes_sent + probes_over_cap` es el pool de esa pasada, y `probes_dead` el drenaje real.
ALTER TABLE scrape_run
    ADD COLUMN probes_sent       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN probes_alive      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN probes_dead       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN probes_over_cap   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN probes_unresolved INTEGER NOT NULL DEFAULT 0;

-- Las pasadas anteriores a esta migración se quedan a 0 y NO se rellenan hacia atrás: el dato es
-- reconstruible pero solo para las limpias (con `message IS NULL`, `errors` era exactamente
-- `probes_unresolved`), y rellenar solo esas dejaría una columna que unas veces significa el
-- desglose y otras un cero que quiere decir "no se sabe". Un 0 uniforme antes de la 0028 es más
-- fácil de leer: la serie empieza aquí.
COMMENT ON COLUMN scrape_run.probes_over_cap IS
    'Candidatos a baja que no cupieron en el tope de sondeos de la pasada. Rutina: entran los '
    'primeros en la siguiente (se ordena por racha descendente). NO cuentan como error.';
COMMENT ON COLUMN scrape_run.probes_unresolved IS
    'Candidatos sondeados que no dieron veredicto: fallo de red, bloqueo de la tienda o respuesta '
    'ambigua. SÍ cuentan como error: es la tienda negándose a contestar.';
