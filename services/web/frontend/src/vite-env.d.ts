/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** URL base de Keycloak (p.ej. https://keycloak.dev.example/). Vacío → auth deshabilitada. */
  readonly VITE_KC_URL?: string;
  /** Realm de Keycloak. */
  readonly VITE_KC_REALM?: string;
  /** Client-id público (SPA) registrado en el realm. */
  readonly VITE_KC_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
