-- El sondeo de bajas deja de tirar su propio veredicto (#412).
--
-- `_confirm_candidates` pregunta a la tienda por un candidato, obtiene «vivo», y `_rescue()` le
-- pone `missing_streak = 0`. Con eso se pierde la única prueba de que se preguntó: dos pasadas
-- después la misma prenda vuelve a ser candidata y se le vuelve a preguntar lo mismo,
-- indefinidamente. No había ningún sitio donde constara que la tienda ya había contestado que sí.
--
-- Medido en `deal_tracker_qa` el 16/08/2026, última pasada de cada tienda (15/08):
--
--     tienda      sent  alive  dead  over_cap
--     zara          50     50     0       195
--     mango         50     50     0       146
--     sfera         50     50     0        90
--     hipercor      50     50     0        73
--
-- 200 sondeos, 200 vivos, CERO bajas y 504 candidatos que no cupieron en el tope. Y el pool de
-- Zara crece: `over_cap` 134 (10/08) -> 106 (14/08) -> 195 (15/08). O sea que el presupuesto de
-- peticiones se está gastando entero en reconfirmar prendas que ya sabíamos vivas, mientras la
-- cola de las que nunca se han sondeado se alarga sola. La cohorte que lo causa es permanente por
-- decisión: #357 resolvió que las prendas comprables que la tienda deja de listar SE QUEDAN.
--
-- `last_probe_at` es la memoria de lo ya preguntado. Se escribe solo con veredicto CONCLUYENTE
-- (`ALIVE` y `UNBUYABLE`: las dos son la tienda contestando sobre ese producto), nunca cuando el
-- sondeo se queda sin respuesta — si la tienda no contestó, no hay nada que recordar.
ALTER TABLE product
    ADD COLUMN last_probe_at TIMESTAMPTZ;

COMMENT ON COLUMN product.last_probe_at IS
    'Cuándo contestó la tienda por última vez a un sondeo de confirmación sobre este producto. '
    'Solo veredictos concluyentes. La ingesta excluye de un sondeo nuevo a los confirmados hace '
    'menos de SCRAPER_DELIST_PROBE_COOLDOWN_DAYS, pero NO los descataloga por ello.';

-- ── Y el contador que hace visible el ahorro ──
--
-- Sin él, el efecto de la ventana sería invisible: `probes_over_cap` bajaría y no habría forma de
-- saber si es que el pool se ha drenado (bien) o que hemos dejado de mirar (mal). Son preguntas
-- opuestas con el mismo síntoma, que es exactamente el fallo que la 0028 vino a arreglar separando
-- `over_cap` de `unresolved`.
--
-- CUIDADO al leerlo: `probes_skipped_fresh` NO es un error y NO es una baja evitada. Es «no hacía
-- falta volver a preguntar». Esos productos siguen protegidos de `_delist` igual que los
-- `over_cap`: lo único que se les ahorra es la petición de red, no la protección. Un producto
-- excluido por ventana no es un producto descatalogado sin confirmar — eso sería justo la clase
-- de baja falsa masiva que esta issue tiene prohibido producir.
ALTER TABLE scrape_run
    ADD COLUMN probes_skipped_fresh INTEGER NOT NULL DEFAULT 0;

-- Como en la 0028 y la 0040, las pasadas anteriores se quedan a 0 y no se rellenan hacia atrás: el
-- dato no es reconstruible, porque antes de este cambio esos productos se sondeaban y se contaban
-- en `probes_sent`. La serie empieza aquí.
COMMENT ON COLUMN scrape_run.probes_skipped_fresh IS
    'Candidatos a los que NO se sondeó por haber contestado hace poco (ver product.last_probe_at). '
    'Siguen bloqueados frente a las bajas, igual que probes_over_cap. NO cuentan como error.';
