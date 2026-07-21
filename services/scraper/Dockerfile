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
    SCRAPER_MIGRATIONS_DIR=/app/db/migrations \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
WORKDIR /app

COPY --from=build /opt/venv /opt/venv
# Migraciones (contrato de esquema) para poder aplicarlas desde el propio job.
COPY db/migrations /app/db/migrations

# Navegador headless para tiendas tras Akamai (p.ej. Sfera). `--with-deps` trae las libs
# de sistema que Chromium necesita; solo el navegador, no todos. Engorda la imagen ~+400 MB
# (coste asumido). El binario se instala en PLAYWRIGHT_BROWSERS_PATH, legible por appuser.
RUN playwright install --with-deps chromium

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /opt/pw-browsers
USER appuser

# Comprobación de sanidad: el paquete importa correctamente.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import scraper" || exit 1

ENTRYPOINT ["python", "-m", "scraper.run"]
CMD ["--help"]
