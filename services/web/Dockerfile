# syntax=docker/dockerfile:1
#
# Contexto de build = RAÍZ del repo (necesita services/web + db/migrations):
#   docker build -f services/web/Dockerfile -t deal-tracker-web .
#
# Imagen única: API NestJS + SPA React (servida como estáticos por Nest).
# pnpm vía Corepack, siempre con --frozen-lockfile (cadena de suministro endurecida, ver .npmrc).
# Es un workspace pnpm (raíz = servicio web, `frontend` = SPA); por eso se copian los manifiestos
# del workspace antes de instalar.

# --- deps: TODAS las dependencias del workspace (incl. dev) para compilar API y SPA ---
FROM node:22-slim AS deps
ENV PNPM_HOME=/pnpm CI=1
WORKDIR /app
RUN corepack enable
COPY services/web/pnpm-workspace.yaml services/web/package.json services/web/pnpm-lock.yaml services/web/.npmrc ./
COPY services/web/frontend/package.json ./frontend/
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

# --- build-api: compila el backend TS -> dist ---
FROM deps AS build-api
COPY services/web/tsconfig.json services/web/tsconfig.build.json services/web/nest-cli.json ./
COPY services/web/src ./src
RUN pnpm build

# --- build-frontend: compila la SPA -> frontend/dist ---
FROM deps AS build-frontend
COPY services/web/frontend ./frontend
RUN pnpm --filter @deal-tracker/frontend build

# --- prod-deps: solo dependencias de producción del servicio web (imagen final mínima) ---
FROM node:22-slim AS prod-deps
ENV PNPM_HOME=/pnpm CI=1
WORKDIR /app
RUN corepack enable
COPY services/web/pnpm-workspace.yaml services/web/package.json services/web/pnpm-lock.yaml services/web/.npmrc ./
COPY services/web/frontend/package.json ./frontend/
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm --filter @deal-tracker/web install --prod --frozen-lockfile --store-dir /pnpm/store

# --- runtime: imagen mínima, usuario no-root ---
FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    WEB_MIGRATIONS_DIR=/app/db/migrations \
    WEB_STATIC_DIR=/app/frontend/dist
WORKDIR /app

COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=build-api /app/dist ./dist
COPY --from=build-frontend /app/frontend/dist ./frontend/dist
COPY services/web/package.json ./
# Migraciones (contrato de esquema) para poder aplicarlas con `node dist/database/migrate.js`.
COPY db/migrations /app/db/migrations

RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
    node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/main.js"]
