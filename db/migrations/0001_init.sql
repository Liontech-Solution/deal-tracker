-- Esquema base del catálogo y del historial de precios (dominio: servicio scraper).
-- SQL neutro de Postgres: es el contrato compartido con el futuro servicio `web` (Node/TS).
-- Las tablas de usuarios / intereses / notificaciones se difieren a la Fase 2.

-- Tienda objetivo de scraping (Zara, Mango, ...).
CREATE TABLE retailer (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug       TEXT        NOT NULL UNIQUE,
    name       TEXT        NOT NULL,
    base_url   TEXT        NOT NULL,
    active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cada ejecución del scraper para una tienda. Base de la detección de altas/bajas
-- y de la observabilidad del job.
CREATE TABLE scrape_run (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retailer_id   BIGINT      NOT NULL REFERENCES retailer (id),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT        NOT NULL DEFAULT 'running',  -- running | success | failed
    products_seen INTEGER     NOT NULL DEFAULT 0,
    variants_seen INTEGER     NOT NULL DEFAULT 0,
    errors        INTEGER     NOT NULL DEFAULT 0
);

-- Producto/modelo dentro de una tienda. `retailer_product_id` es el identificador
-- único y estable por tienda (requisito clave para altas/bajas).
CREATE TABLE product (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retailer_id         BIGINT      NOT NULL REFERENCES retailer (id),
    retailer_product_id TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    gender              TEXT,        -- niño | niña | unisex
    section             TEXT,        -- ropa | zapateria
    category            TEXT,        -- pantalones | camisetas | sudaderas | vestidos | ropa_interior | zapatos | zapatillas | ...
    url                 TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    delisted_at         TIMESTAMPTZ,
    UNIQUE (retailer_id, retailer_product_id)
);

-- Variante talla/color de un producto. El precio puede variar por variante,
-- por eso el historial cuelga de aquí y no del producto.
CREATE TABLE variant (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id          BIGINT      NOT NULL REFERENCES product (id),
    retailer_variant_id TEXT        NOT NULL,
    size                TEXT,
    color               TEXT,
    sku                 TEXT,
    url                 TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    delisted_at         TIMESTAMPTZ,
    UNIQUE (product_id, retailer_variant_id)
);

-- Serie temporal de precios por variante. Se hace APPEND en cada ejecución
-- (nunca se sobrescribe) para poder graficar la evolución y detectar
-- descuentos engañosos comparando price vs list_price a lo largo del tiempo.
CREATE TABLE price_history (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    variant_id    BIGINT        NOT NULL REFERENCES variant (id),
    price         NUMERIC(10, 2) NOT NULL,
    currency      TEXT          NOT NULL DEFAULT 'EUR',
    list_price    NUMERIC(10, 2),            -- precio original/tachado si lo hay
    discount_pct  NUMERIC(5, 2),             -- % de rebaja calculado (list_price -> price)
    in_stock      BOOLEAN       NOT NULL DEFAULT TRUE,
    scraped_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    scrape_run_id BIGINT        REFERENCES scrape_run (id)
);

CREATE INDEX ix_price_history_variant_time ON price_history (variant_id, scraped_at DESC);
CREATE INDEX ix_product_retailer_active ON product (retailer_id) WHERE delisted_at IS NULL;
CREATE INDEX ix_variant_product ON variant (product_id) WHERE delisted_at IS NULL;
