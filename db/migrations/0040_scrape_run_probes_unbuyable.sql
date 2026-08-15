-- «Existe» y «se puede comprar» dejan de ser lo mismo en el sondeo de bajas (#197).
--
-- `probe_alive()` de Lefties daba por vivo cualquier id que `productsArray` siguiera reconociendo,
-- sin mirar el stock. Y a un producto confirmado vivo la ingesta le pone la racha a cero
-- (`_rescue`), así que un saldo agotado que la tienda siga sirviendo en el detalle se quedaba en el
-- catálogo indefinidamente, con su último precio rebajado y sin que nadie pudiera comprarlo.
--
-- Medido el 15/08/2026 contra el `productsArray` real, sobre los 58 productos de las dos hojas
-- `REBAJAS HASTA -70%` (las únicas del catálogo que no cuelgan de ninguna hoja permanente, así que
-- al apagarse la campaña se quedan sin red): **los 58 volvieron con `id`** —0
-- `_ERR_PRODUCT_NOT_FOUND`— y **33 traían TODAS las tallas `HIDDEN`**, uno de ellos con 66 de 66.
-- Los 58 vinieron además `state: "visible"`, o sea que `state` no sirve como señal; la que sirve es
-- `visibilityValue`, y ya viajaba en la misma respuesta que el sondeo descarga.
--
-- El veredicto nuevo (`ProbeVerdict.UNBUYABLE`) no rescata ni da de baja: bloquea el producto esa
-- pasada y le conserva la racha. Que no quede stock hoy no prueba que la prenda se haya retirado, y
-- aflojar eso es como se producen bajas falsas masivas.
--
-- Por qué columna propia y no reutilizar `probes_unresolved`: son diagnósticos OPUESTOS. Sin
-- veredicto es la tienda negándose a contestar —y suma en `errors`, que es justo lo que hay que
-- cazar—; no comprable es la tienda contestando con claridad. Meterlos juntos repetiría el fallo
-- que arregló la 0028, y peor: son 33 productos de Lefties en TODAS las pasadas, así que dejaría a
-- esa tienda con `errors` permanentemente distinto de cero por algo rutinario. `probes_unbuyable`
-- NO cuenta como error, igual que `probes_over_cap`.
ALTER TABLE scrape_run
    ADD COLUMN probes_unbuyable INTEGER NOT NULL DEFAULT 0;

-- Como en la 0028, las pasadas anteriores se quedan a 0 y no se rellenan hacia atrás: el dato no es
-- reconstruible (antes de este cambio esos productos se respondían «vivo» y se contaban en
-- `probes_alive`, indistinguibles de los que sí tenían stock). La serie empieza aquí.
COMMENT ON COLUMN scrape_run.probes_unbuyable IS
    'Candidatos sondeados que la tienda reconoce pero sin ninguna talla comprable. Ni se rescatan '
    'ni se dan de baja: se bloquean esa pasada conservando la racha. NO cuentan como error.';
