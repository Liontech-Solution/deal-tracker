-- Registro de avisos enviados (dominio: servicio web).
-- Sirve para deduplicar: garantiza idempotencia del job de matching (no repetir el mismo
-- aviso por el mismo evento de precio). Aún no hay runtime que la escriba en esta fase;
-- se crea ahora para cerrar el contrato de esquema. `price_event_key` identifica el evento
-- de precio que disparó el aviso (p.ej. "<scrape_run_id>:<price>"), de modo que un mismo
-- interés puede volver a avisar ante una bajada nueva pero no ante la misma.
CREATE TABLE notification (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    interest_id     BIGINT      NOT NULL REFERENCES interest (id) ON DELETE CASCADE,
    variant_id      BIGINT      NOT NULL,
    price           NUMERIC(10, 2) NOT NULL,   -- snapshot del precio avisado
    list_price      NUMERIC(10, 2),
    discount_pct    NUMERIC(5, 2),
    price_event_key TEXT        NOT NULL,       -- clave del evento de precio (dedupe)
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (interest_id, variant_id, price_event_key)
);

CREATE INDEX ix_notification_user_time ON notification (user_id, sent_at DESC);
