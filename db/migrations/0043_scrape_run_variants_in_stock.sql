-- Cuánto stock vio la pasada en el listado, que es el discriminador que le faltaba a la alarma
-- del sondeo (#427).
--
-- ── EL AGUJERO ──
--
-- La 0040 introdujo `ProbeVerdict.UNBUYABLE` y su contador, y ese veredicto **no suma en
-- `errors`** a propósito: son 33 productos de Lefties en todas las pasadas, así que contarlos
-- dejaría a esa tienda con `errors` permanentemente distinto de cero por algo rutinario (el fallo
-- que arregló la 0028).
--
-- La contrapartida es que si la señal de stock se rompiera —Lefties renombra `visibilityValue`,
-- Zara renombra `availability`— TODO candidato saldría `UNBUYABLE`, nadie se rescataría, el
-- mecanismo de confirmación quedaría inoperante, y `errors` seguiría a 0 con `message` a NULL.
-- Un fallo silencioso de los que este repo paga caros: el precedente exacto es #370, meses
-- invisible porque nada lo decía en voz alta.
--
-- ── POR QUÉ EL ARREGLO OBVIO NO VALE, Y ESTÁ MEDIDO ──
--
-- Lo natural era un umbral sobre `unbuyable == sent`, copiando el que ya existe para `unresolved`.
-- Lo tumbó el test de ingesta con el caso más pequeño posible: con UN solo candidato legítimamente
-- agotado ya se cumple. Y ahí está la asimetría — que la tienda no conteste a NADIE solo pasa si
-- algo está roto, mientras que «todos los sondeados están agotados» es un estado perfectamente
-- sano, porque la cohorte son prendas ausentes del listado **y** sin stock, las dos cosas a la vez.
--
-- ── EL DISCRIMINADOR, Y POR QUÉ ÉSTE SÍ ──
--
-- No está dentro del sondeo: está fuera. Si el parser de stock se rompe, no es que los candidatos
-- salgan agotados — es que **el catálogo entero** se queda sin una sola variante con stock, y eso
-- sí es inequívoco. Un producto agotado de verdad convive con miles que no lo están.
--
-- ── LO MEDIDO, Y POR QUÉ NO HAY UMBRAL QUE ELEGIR ──
--
-- La issue pedía elegir el umbral con dato y no a ojo. El dato dice que **no hace falta umbral**:
-- el único valor que significa algo es el cero.
--
-- Medido en `deal_tracker_qa` el 16/08/2026 sobre las ~60 pasadas con éxito que hay registradas,
-- contando las filas de `price_history` que escribió cada una. Las nueve tiendas pueblan
-- `in_stock` y **ninguna deja NULLs**, así que el contador es fiable en todas. Las cinco pasadas
-- con MENOS stock de la historia del proyecto:
--
--     tienda      escritas  con stock     %
--     hipercor          55          7  12,7   <- el mínimo absoluto
--     lefties          180         24  13,3
--     hipercor         131         40  30,5
--     hipercor         151         50  33,1
--     cacles           179         68  38,0
--
-- O sea: **ninguna pasada ha bajado nunca de 7 variantes con stock**, y la peor proporción vista es
-- un 12,7 %. Ni siquiera las pasadas pequeñas se acercan al cero. Por eso la condición es
-- `= 0` y no `< N`: un cero no tiene lectura benigna en el rango observado, y cualquier N que
-- eligiera por encima sería un número inventado con falsos positivos garantizados.
--
-- Ojo al denominador al leerla: cuenta las variantes que la pasada ESCRIBIÓ, o sea las de los
-- productos a los que se les pidió detalle. Una pasada sin ningún cambio de huella no escribe
-- ninguna —está registrada al menos una, de Cacles— y por eso quien la consume exige además
-- `variants_seen > 0`. Sin ese denominador, la pasada más tranquila posible se leería igual que un
-- parser roto.
ALTER TABLE scrape_run
    ADD COLUMN variants_in_stock INTEGER NOT NULL DEFAULT 0;

-- Como en la 0028 y la 0040: las pasadas anteriores se quedan a 0 y no se rellenan hacia atrás. Es
-- reconstruible con un `count(*) FILTER (WHERE in_stock)` sobre `price_history`, pero no se hace
-- aquí — sobre la serie entera es un barrido caro, y el valor de la columna es la pasada nueva.
-- La serie empieza aquí.
COMMENT ON COLUMN scrape_run.variants_in_stock IS
    'Variantes con stock entre las que ESTA pasada escribió (denominador: variants_seen). A 0 con '
    'variants_seen > 0 significa que el parser de stock de la tienda se ha roto, no que no haya '
    'existencias: la pasada con menos stock jamás registrada trae 7 (de 55 escritas).';
