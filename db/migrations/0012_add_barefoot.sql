-- Marca de calzado respetuoso (barefoot) en `product`.
--
-- El producto es ropa y calzado BAREFOOT para peques, pero hasta ahora nada en el sistema
-- distinguía un zapato respetuoso de uno que no lo es: los scrapers ingieren categorías enteras de
-- calzado convencional y el catálogo que veía el usuario era calzado infantil genérico, que es justo
-- lo que el producto dice no ser.
--
-- La decisión (issue #30) es **ingerir todo y clasificar**, no dejar de scrapear:
--   * el histórico de precios de un producto no barefoot vale aunque hoy no se muestre;
--   * si el criterio de clasificación falla o mejora, no hay que re-scrapear nada, solo reclasificar.
--
-- Tres estados y un cuarto implícito, que es el que más cuidado pide:
--
--   'si'          calzado respetuoso confirmado.
--   'no'          calzado que sabemos que NO lo es (tacón, cuña, plataforma...).
--   'desconocido' calzado sin señal concluyente. NO se muestra por defecto: en la duda, fuera.
--   NULL          NO APLICA — es ropa. Distinto de 'desconocido', que significa "es un zapato y no
--                 sabemos qué es". La diferencia importa: el filtro por defecto del catálogo deja
--                 pasar toda la ropa (NULL) y solo el calzado 'si', así que confundir los dos
--                 estados escondería la mitad del catálogo o enseñaría lo que no toca.
--
-- La clasificación la escribe el scraper, que es quien posee esta tabla (ver `scraper/barefoot.py`):
-- por categoría propia de la tienda cuando la hay (Zara y Lefties etiquetan el barefoot en su árbol,
-- coste cero y sin heurística) y por heurística de texto cuando no (Sfera, que no da ninguna señal).
ALTER TABLE product
    ADD COLUMN barefoot TEXT
    CONSTRAINT product_barefoot_estado_valido
    CHECK (barefoot IN ('si', 'no', 'desconocido'));

-- Backfill: todo el calzado ya ingerido arranca sin clasificar, y la ropa se queda en NULL.
-- Consecuencia deliberada: hasta que el scraper vuelva a pedir el detalle de cada producto, la
-- zapatería se ve VACÍA con el filtro por defecto. Es el lado seguro del error — enseñar de más
-- sería prometer barefoot y servir calzado convencional.
UPDATE product SET barefoot = 'desconocido' WHERE section = 'zapateria';

-- El filtro por defecto del catálogo es "toda la ropa + solo el calzado 'si'", o sea que siempre
-- entra por (section, barefoot). Parcial por `delisted_at IS NULL` porque ese es el otro filtro que
-- llevan todas las lecturas del catálogo.
CREATE INDEX ix_product_seccion_barefoot ON product (section, barefoot)
    WHERE delisted_at IS NULL;
