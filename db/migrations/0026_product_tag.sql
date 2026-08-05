-- Ejes transversales del catálogo: marcas que atraviesan las categorías del brief.
--
-- La primera es `deportiva` (#180). Un padre que busca el equipo de educación física tiene que
-- recorrer `pantalones`, `camisetas` y `sudaderas` y decidir prenda a prenda, porque dentro de esas
-- categorías nada distingue el jogger del pantalón de vestir.
--
-- **No puede ser una categoría**, y eso está medido en cuatro tiendas de cuatro (#175, #180): la
-- prenda deportiva existe pero vive repartida entre las categorías del brief, que es donde le toca
-- estar. En Lefties 130 de las 146 prendas de su rama `Ropa Deportiva` ya entran por otra hoja, y en
-- C&A 42 de 45. Una categoría `ropa-deportiva` solo podría llenarse robándole prendas a `camisetas`
-- o a `pantalones`, y entonces el mismo pantalón caería en una u otra según qué hoja lo listó
-- primero — sin criterio visible para quien busca.
--
-- ── Por qué una tabla y no una columna, como `barefoot` ────────────────────────────────────────
--
-- `barefoot` (migración 0012) es el precedente estructural: una marca ORTOGONAL a la categoría, que
-- escribe el scraper y filtra el catálogo. Pero es una sola, y aquí ya hay un segundo eje con la
-- misma forma esperando (#189: la rama de uniforme escolar de H&M, con pantalón, vestido y zapato).
-- Una columna por eje obliga a repetir migración + ingesta + espejo Drizzle + faceta + SPA cada vez;
-- la tabla se paga una sola vez y el eje siguiente es una fila con otro `tag`.
--
-- El coste es un `EXISTS` en el listado, la ficha y las facetas en lugar de una condición sobre la
-- fila. Aceptable: el índice de abajo lo resuelve por (tag, product_id) sin tocar `product`.
--
-- ── El calzado queda FUERA a propósito ────────────────────────────────────────────────────────
--
-- `deportiva` solo se pone en `section = 'ropa'`. El zapato deportivo ya se encuentra hoy sin nada
-- nuevo: la categoría `zapatillas` cruzada con el filtro barefoot por defecto da exactamente
-- «zapatillas deportivas respetuosas», y esa categoría está poblada por Cacles (que mapea ahí
-- `deportivas`, `zapatillas de fútbol`, `de running` y `de gimnasia y baile`), Zara y Lefties.
-- Marcar también el calzado crearía dos formas de pedir lo mismo con resultados distintos según la
-- tienda. Y encaja con lo que las tiendas publican: sus cajones de deporte son ropa — los hijos de
-- `sportswear` en H&M son tops, shorts, joggers, leggings, sudaderas y faldas.
--
-- ── Quién lo rellena, y por qué NO es una heurística de texto ──────────────────────────────────
--
-- Lo escribe el scraper desde la HOJA DE ORIGEN: es la propia tienda quien dice que esa prenda es
-- deportiva, que es el dato más honesto que hay y sale gratis del listado. Al contrario que
-- `barefoot`, aquí no hace falta clasificar por texto y por eso no hay estado `desconocido`: o la
-- tienda lo publica en su cajón de deporte o no lo dice.
--
-- La marca es del PRODUCTO, no de la hoja que ganó el listado. Importa: en Lefties 130 de 146
-- aparecen en la rama de deporte Y en su categoría, y `list_catalog()` se queda con la primera hoja
-- que ve cada producto, así que marcar por hoja ganadora haría que el 89 % se marcara o no según el
-- orden de `CATEGORIES`.
--
-- Cobertura, que es la limitación honesta de este eje: solo cinco de las nueve tiendas publican un
-- cajón de deporte identificable. Zara, Hipercor, Springfield y Cacles no, así que filtrar por
-- `deportiva` las excluye enteras. La SPA lo dice en el propio filtro.
CREATE TABLE product_tag (
    product_id BIGINT NOT NULL REFERENCES product (id) ON DELETE CASCADE,
    tag        TEXT   NOT NULL,
    PRIMARY KEY (product_id, tag)
);

COMMENT ON TABLE product_tag IS
    'Ejes transversales a la categoría, escritos por el scraper desde la hoja de origen de cada '
    'tienda. Vocabulario cerrado en scraper/tags.py; hoy solo `deportiva` (#180).';

-- La lectura del catálogo es «dame los productos con este tag», así que el tag va PRIMERO: con la
-- PK sola (product_id, tag) esa consulta no tiene índice utilizable y acabaría en seq scan sobre la
-- tabla entera. Con las dos columnas basta para resolver el EXISTS por index-only scan.
CREATE INDEX ix_product_tag_tag ON product_tag (tag, product_id);

-- Sin backfill: la marca no existía en ninguna parte de donde deducirla. Se puebla en la siguiente
-- pasada de cada tienda, y a diferencia de `barefoot` **no depende del detalle condicional** — se
-- escribe desde el listado, que se recorre entero en cada pasada, así que una sola pasada por
-- tienda la deja completa sin esperar al refresco forzado.
