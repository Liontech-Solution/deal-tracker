---
name: scraper-sin-just-ni-env
description: "En esta máquina no está instalado `just` y el repo no tiene `.env`; cómo correr las recetas del scraper a mano y qué necesita `--check-categories`"
metadata: 
  node_type: memory
  type: project
  originSessionId: 032ca09b-7791-49e7-b965-283c3277b5e4
  modified: 2026-08-03T11:00:16.336Z
---

El `justfile` es la interfaz documentada del scraper, pero **`just` no está instalado en esta
máquina** (ni en PATH, ni por mise/asdf/cargo). Y **no existe ningún `.env`** en el repo — solo
`.env.example` —: el proyecto corre con las variables del entorno.

Las recetas a mano, desde `services/scraper`:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # = just setup
.venv/bin/playwright install chromium                        # solo la 1ª vez por máquina
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest
```

Tres detalles que cuestan tiempo si no se saben:

- **`mypy` sin argumentos**, que es como lo invoca el justfile: usa la config del proyecto. Un
  `mypy src tests` a mano da ~41 errores que no son reales.
- **Un worktree nuevo necesita su propio venv**: el install es editable y apunta al directorio de
  origen, así que reutilizar el venv del checkout original testea el código equivocado. Los
  navegadores de playwright sí se comparten (`~/.cache/ms-playwright`), así que eso no se rebaja.
- **`--check-categories` y `--tree` exigen `DATABASE_URL`** solo porque lo valida
  `Config.from_env()`; nunca llaman a `db.connect`. Una URL de pega vale y no se marca nunca —
  comprobado leyendo `run.py` antes de usarla.

Relacionado: [[verificar-en-cluster-dev]] (la Postgres de usuario, que sí hace falta para ingesta).
