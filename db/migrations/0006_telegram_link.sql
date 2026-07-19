-- Vínculo usuario ↔ Telegram (dominio: servicio web).
-- Amplía `app_user` con el chat de Telegram vinculado y el flujo de enlace por token.
-- El usuario, autenticado en la web, pide un enlace: se genera un `telegram_link_token`
-- de un solo uso y corta caducidad, con el que se arma un deep-link `t.me/<bot>?start=<token>`.
-- El bot (Fase 2, más adelante) recibe `/start <token>`, resuelve el usuario por el token,
-- fija `telegram_chat_id` y limpia el token. El `scraper` no toca estas columnas.
ALTER TABLE app_user
    ADD COLUMN telegram_chat_id                BIGINT      UNIQUE,  -- chat vinculado (nulo = sin vincular)
    ADD COLUMN telegram_username               TEXT,                -- @usuario, solo para mostrar
    ADD COLUMN telegram_linked_at              TIMESTAMPTZ,
    ADD COLUMN telegram_link_token             TEXT        UNIQUE,  -- token de un solo uso del deep-link
    ADD COLUMN telegram_link_token_expires_at  TIMESTAMPTZ;

-- Búsqueda por token al procesar `/start` (solo sobre tokens vivos).
CREATE INDEX ix_app_user_link_token ON app_user (telegram_link_token)
    WHERE telegram_link_token IS NOT NULL;
