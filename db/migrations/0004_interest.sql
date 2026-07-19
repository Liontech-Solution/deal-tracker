-- Intereses de seguimiento (dominio: servicio web).
-- Qué prenda quiere seguir un usuario y con qué criterio de aviso. Admite dos formas
-- (combinables): apuntar a un producto/variante concretos y/o expresar un filtro por
-- atributos (género/sección/categoría/talla/color). El job de matching (Fase 2, más
-- adelante) leerá esta tabla contra el `price_history` del scraper.
CREATE TABLE interest (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,

    -- Apuntado directo (opcional). Sin FK dura a product/variant: son propiedad del
    -- scraper y un interés puede sobrevivir a una baja temporal; se resuelve por id lógico.
    retailer_id BIGINT      REFERENCES retailer (id),
    product_id  BIGINT,
    variant_id  BIGINT,

    -- Filtro por atributos (opcional). Nulos = "cualquiera".
    gender      TEXT,   -- niño | niña | unisex
    section     TEXT,   -- ropa | zapateria
    category    TEXT,   -- pantalones | camisetas | ...
    size        TEXT,
    color       TEXT,

    -- Regla de "bajada significativa". Valores por defecto conservadores; el job los usa
    -- para decidir el aviso comparando contra el precio de lista real y/o el mínimo reciente.
    min_discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 20,
    compare_base     TEXT          NOT NULL DEFAULT 'recent_min',  -- list_price | recent_min
    window_days      INTEGER       NOT NULL DEFAULT 30,

    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT interest_compare_base_chk CHECK (compare_base IN ('list_price', 'recent_min')),
    -- Un interés vacío (ni apuntado ni filtro) no tiene sentido: exige al menos una señal.
    CONSTRAINT interest_target_present_chk CHECK (
        product_id IS NOT NULL OR variant_id IS NOT NULL OR retailer_id IS NOT NULL
        OR gender IS NOT NULL OR section IS NOT NULL OR category IS NOT NULL
        OR size IS NOT NULL OR color IS NOT NULL
    )
);

CREATE INDEX ix_interest_user ON interest (user_id) WHERE active;
