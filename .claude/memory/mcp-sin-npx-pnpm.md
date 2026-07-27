---
name: mcp-sin-npx-pnpm
description: MCP de Playwright/Context7 montados por binario global de pnpm en vez de npx; el usuario evita npx por seguridad
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 73821265-0f3c-46e5-a426-3b831dab3777
  modified: 2026-07-27T10:27:59.838Z
---

El usuario **evita `npx`** (y paquetes node no auditados) por seguridad; prefiere pnpm porque no ejecuta scripts `postinstall` de dependencias sin aprobación. No propongas `npx ...` para MCP/plugins: pre-instala con `pnpm add -g` y apunta al binario.

**Why:** riesgo de typosquatting / postinstall malicioso en el ecosistema npm; pnpm mitiga y deja el global en `$HOME` (nunca sudo).

**How to apply:** toolchain vía pacman (`nodejs pnpm`), NO `pnpm setup` (peta con EACCES en `/usr/bin` porque pnpm viene de pacman). Bin global en `~/.local/share/pnpm/bin`, añadido al PATH con `fish_add_path` (universal var). Nunca `sudo` con pnpm/npm.

MCP montados así (scope user, en `~/.claude.json`):
- `context7` → `/home/juanjocop/.local/share/pnpm/bin/context7-mcp`
- `playwright` → `/home/juanjocop/.local/share/pnpm/bin/playwright-mcp`
- `postgres` → `/home/juanjocop/.local/share/mcp/postgres-mcp/bin/postgres-mcp`

**MCP en Python (preferido cuando existe alternativa Python al paquete npm):** no hace falta
`pipx` ni `uv` (ninguno instalado, y ambos pedirían sudo/pacman). Un **venv dedicado** hace lo
mismo que pipx por dentro, sin sudo y sin tocar el Python del sistema (Arch tiene PEP 668, así
que `pip install --user` falla):
`python -m venv ~/.local/share/mcp/<nombre> && ~/.local/share/mcp/<nombre>/bin/pip install <pkg>`,
y `claude mcp add <nombre> --scope user -- <ruta-al-bin> <args>`.
Ejemplo aplicado: `postgres-mcp` (crystaldba/postgres-mcp, MIT, verificado contra el
`pyproject.toml` de upstream antes de instalar) con `--access-mode restricted` = read-only.

Playwright: `@playwright/mcp` (v playwright 1.62.0-alpha) usa navegadores bundled en `~/.cache/ms-playwright` (firefox-1534, chromium-1228 descargados). El flag `--browser` solo acepta chrome/firefox/webkit/msedge, y `chrome`/`msedge` son canales del **sistema**; para el chromium bundled se usa `--browser chrome --executable-path ~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome`. Instalar builds con el binario bundled (`.../node_modules/.pnpm/node_modules/.bin/playwright install <navegador>`), NO con `pnpm dlx playwright` (versión distinta → mismatch). Cambios de `claude mcp` requieren reiniciar Claude Code para recargar el server.

Relacionado: [[qa-test-user]]
