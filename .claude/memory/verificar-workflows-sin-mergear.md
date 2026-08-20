---
name: verificar-workflows-sin-mergear
description: no hay linter de workflows en CI; actionlint+shellcheck se bajan al scratchpad, y el bash de los `run:` se ejecuta con stubs y un $GITHUB_STEP_SUMMARY de pega
metadata:
  type: feedback
---

Ningún CI de este repo valida los workflows, y `release-qa` / `release-prod` / `prune-prereleases`
no los dispara ningún PR (no están en el `paths` de nada), así que un error suyo solo aparece el día
de la promoción. Dos herramientas cubren eso sin mergear:

1. **actionlint + shellcheck**, que no están instalados pero se bajan como binario estático al
   scratchpad (`actionlint_*_linux_amd64.tar.gz`, `shellcheck-*.linux.x86_64.tar.xz`). actionlint
   **solo revisa el bash si encuentra shellcheck en el PATH** — sin él pasa limpio y no has probado
   los `run:`. Comprueba que de verdad mira metiéndole un error a una copia (`if: always(`).
2. **Ejecutar los `run:` de verdad**: parsear el YAML, sustituir los `${{ }}` como hace Actions
   (antes de que bash vea nada), y correr cada bloque con `bash -e -o pipefail`, un
   `$GITHUB_STEP_SUMMARY`/`$GITHUB_ENV` de fichero temporal y stubs de `gh`/`yq`/`docker`/`git` en
   el PATH. Encadenando los pasos de un job y propagando `$GITHUB_ENV` se simula la promoción
   entera, incluidos los caminos de corte. Eso encontró en #526 lo que el linter no ve.

**Why:** los dos releases mueven QA y producción, y su primer ensayo real es la promoción misma; un
`::error::` mal escrito o un paso que muere por cwd inexistente se paga allí.

**How to apply:** ver `.github/workflows/` y los scripts de la sesión de #526. Ojo con dos trampas
medidas: `defaults.run.working-directory` de los dos CI apunta a `services/<x>`, que **no existe en
el job `bump`**, así que un `run:` suelto ahí muere; y SC2016 salta con los backticks de markdown
dentro de comillas simples — se evita escribiendo el resumen con `echo "…\`x\`…"` como el resto.

Relacionado: [[saber-que-dispara-un-merge]], [[gh-pr-merge-auto-no-espera]].
