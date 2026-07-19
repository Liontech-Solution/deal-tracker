-- Usuarios de la aplicación (dominio: servicio web).
-- SQL neutro de Postgres: parte del contrato compartido. El `scraper` no toca esta tabla;
-- es propiedad del servicio `web`, que la puebla por aprovisionamiento JIT en la primera
-- petición autenticada. El vínculo con Keycloak es el `sub` del token (estable por usuario).
CREATE TABLE app_user (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    keycloak_sub TEXT        NOT NULL UNIQUE,  -- `sub` del token OIDC de Keycloak
    email        TEXT,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
