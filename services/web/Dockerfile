# syntax=docker/dockerfile:1
#
# Contexto de build = RAÍZ del repo (necesita services/web + db/migrations):
#   docker build -f services/web/Dockerfile -t deal-tracker-web .
#
# pnpm vía Corepack, siempre con --frozen-lockfile (cadena de suministro endurecida, ver .npmrc).

# --- deps: instala TODAS las dependencias (incluidas dev) para poder compilar ---
FROM node:22-slim AS deps
ENV PNPM_HOME=/pnpm CI=1
WORKDIR /app
RUN corepack enable
COPY services/web/package.json services/web/pnpm-lock.yaml services/web/.npmrc ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

# --- build: compila TS -> dist ---
FROM node:22-slim AS build
ENV PNPM_HOME=/pnpm CI=1
WORKDIR /app
RUN corepack enable
COPY services/web/package.json services/web/pnpm-lock.yaml services/web/.npmrc ./
COPY --from=deps /app/node_modules ./node_modules
COPY services/web/tsconfig.json services/web/tsconfig.build.json services/web/nest-cli.json ./
COPY services/web/src ./src
RUN pnpm build

# --- prod-deps: solo dependencias de producción (imagen final mínima) ---
FROM node:22-slim AS prod-deps
ENV PNPM_HOME=/pnpm CI=1
WORKDIR /app
RUN corepack enable
COPY services/web/package.json services/web/pnpm-lock.yaml services/web/.npmrc ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --prod --frozen-lockfile --store-dir /pnpm/store

# --- runtime: imagen mínima, usuario no-root ---
FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    WEB_MIGRATIONS_DIR=/app/db/migrations
WORKDIR /app

COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY services/web/package.json ./
# Migraciones (contrato de esquema) para poder aplicarlas con `node dist/database/migrate.js`.
COPY db/migrations /app/db/migrations

RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD \
    node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/main.js"]
