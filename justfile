# Comandos de desarrollo del deal-tracker.
# Requiere `just` (https://github.com/casey/just). Alternativa: copia el comando a mano.

scraper_dir := "services/scraper"

# Crea el venv e instala el servicio scraper con sus dependencias de desarrollo.
# El `playwright install` no es opcional: los navegadores viven en `~/.cache/ms-playwright` y se
# comparten entre venvs, así que darlos por hecho hace que un entorno nuevo dependa de qué hubiera
# antes en esa caché — y cuando no coincide, Sfera e Hipercor mueren al arrancar mientras
# `just check` sigue verde (#91). Cuesta ~150 MB la primera vez por máquina; después no baja nada.
setup:
    python -m venv {{scraper_dir}}/.venv
    {{scraper_dir}}/.venv/bin/pip install --upgrade pip
    {{scraper_dir}}/.venv/bin/pip install -e "{{scraper_dir}}[dev]"
    {{scraper_dir}}/.venv/bin/playwright install chromium

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

# Vigía en vivo de TODAS las tiendas del registro: hojas + smoke de parseo, sin ingerir ni avisar.
# Es la comprobación de "¿nos siguen dejando entrar?" que en el cluster corre semanalmente (#67).
# `just vigia --retailer cacles` para una sola. Necesita DATABASE_URL definida aunque no toque la BD.
vigia *args:
    cd {{scraper_dir}} && .venv/bin/python -m scraper.vigia --dry-run {{args}}

# Lint + formato + tipos + tests.
check:
    cd {{scraper_dir}} && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest

test:
    cd {{scraper_dir}} && .venv/bin/pytest

# Construye la imagen del scraper (contexto = raíz del repo).
docker-build:
    docker build -f {{scraper_dir}}/Dockerfile -t deal-tracker-scraper .
