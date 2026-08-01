# Comandos de desarrollo del deal-tracker.
# Requiere `just` (https://github.com/casey/just). Alternativa: copia el comando a mano.

scraper_dir := "services/scraper"

# Crea el venv e instala el servicio scraper con sus dependencias de desarrollo.
setup:
    python -m venv {{scraper_dir}}/.venv
    {{scraper_dir}}/.venv/bin/pip install --upgrade pip
    {{scraper_dir}}/.venv/bin/pip install -e "{{scraper_dir}}[dev]"

# Aplica las migraciones pendientes (usa DATABASE_URL del entorno / .env).
migrate:
    cd {{scraper_dir}} && .venv/bin/python -c "from scraper import db, config; from scraper.migrate import apply_migrations; c=config.Config.from_env(); print(apply_migrations(db.connect(c)))"

# Ejecuta una pasada de scraping (por defecto Zara), aplicando migraciones antes.
run retailer="zara":
    cd {{scraper_dir}} && .venv/bin/python -m scraper.run --retailer {{retailer}} --migrate

# Recorre el scraper sin escribir en base de datos.
dry-run retailer="zara":
    cd {{scraper_dir}} && .venv/bin/python -m scraper.run --retailer {{retailer}} --dry-run

# Sondea las hojas de categoría: falla si alguna ha caducado (no ingiere).
check-categories retailer="zara":
    cd {{scraper_dir}} && .venv/bin/python -m scraper.run --retailer {{retailer}} --check-categories

# Enumera el árbol de categorías que publica la tienda y marca cuáles ingerimos (no ingiere).
tree retailer="sfera" root="ninos":
    cd {{scraper_dir}} && .venv/bin/python -m scraper.run --retailer {{retailer}} --tree {{root}}

# Lint + formato + tipos + tests.
check:
    cd {{scraper_dir}} && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest

test:
    cd {{scraper_dir}} && .venv/bin/pytest

# Construye la imagen del scraper (contexto = raíz del repo).
docker-build:
    docker build -f {{scraper_dir}}/Dockerfile -t deal-tracker-scraper .
