/// <reference types="vite/client" />

// La config de Keycloak ya no viaja en el build: la SPA la pide en runtime a `GET /api/config`
// (ver `src/auth/keycloak.ts`), para que una sola imagen sirva dev/qa/prod.
