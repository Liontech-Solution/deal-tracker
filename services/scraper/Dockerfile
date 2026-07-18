# syntax=docker/dockerfile:1
#
# Contexto de build = RAÍZ del repo (necesita services/scraper + db/migrations):
#   docker build -f services/scraper/Dockerfile -t deal-tracker-scraper .

# --- build: instala dependencias en un venv aislado ---
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY services/scraper/pyproject.toml ./
COPY services/scraper/src ./src
RUN pip install .

# --- runtime: imagen mínima, sin toolchain de compilación, usuario no-root ---
FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    SCRAPER_MIGRATIONS_DIR=/app/db/migrations
WORKDIR /app

COPY --from=build /opt/venv /opt/venv
# Migraciones (contrato de esquema) para poder aplicarlas desde el propio job.
COPY db/migrations /app/db/migrations

RUN useradd --create-home --uid 10001 appuser
USER appuser

# Comprobación de sanidad: el paquete importa correctamente.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import scraper" || exit 1

ENTRYPOINT ["python", "-m", "scraper.run"]
CMD ["--help"]
