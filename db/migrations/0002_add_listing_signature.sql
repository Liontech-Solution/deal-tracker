-- Huella del producto tal y como se ve en el listado (p.ej. precio por color).
-- La ingesta la compara con la del scrape anterior para decidir si merece la pena
-- pedir el detalle completo (tallas/stock) o basta con marcar el producto como visto.
ALTER TABLE product ADD COLUMN listing_signature TEXT;
