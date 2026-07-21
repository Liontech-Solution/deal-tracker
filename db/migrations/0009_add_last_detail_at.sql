-- Refresco periódico forzado del detalle: cuándo se pidió por última vez la ficha completa.
-- La ingesta solo pide detalle cuando cambia la huella del listado, así que una prenda de precio
-- estable no se volvía a observar NUNCA: sin re-observaciones no hay serie temporal con la que
-- corroborar un descuento (el veredicto de honestidad y el aviso exigen histórico previo) y el
-- stock por talla se quedaba congelado. Con esta marca, lo más rancio vuelve a pedirse cada
-- `SCRAPER_DETAIL_MAX_AGE_DAYS`, repartido por pasada con `SCRAPER_DETAIL_REFRESH_MAX`.
ALTER TABLE product ADD COLUMN last_detail_at TIMESTAMPTZ;

-- Backfill desde la verdad que ya está en la BD: la última vez que se apiló precio de alguna de
-- sus variantes es, exactamente, la última vez que se pidió el detalle. Sin esto todo el catálogo
-- parecería rancio a la vez en la primera pasada tras migrar.
UPDATE product p
SET last_detail_at = (
    SELECT MAX(h.scraped_at)
    FROM price_history h
    JOIN variant v ON v.id = h.variant_id
    WHERE v.product_id = p.id
);

-- Elegir a quién refrescar es "los más rancios primero" sobre los activos de una tienda.
CREATE INDEX ix_product_detail_age ON product (retailer_id, last_detail_at) WHERE delisted_at IS NULL;
