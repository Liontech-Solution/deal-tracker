---
name: saber-que-dispara-un-merge
description: Antes de mergear, comprobar qué CI dispara el cambio — los workflows filtran por paths y un PR de solo documentación sale con 0 checks
metadata:
  type: feedback
---

Antes de mergear un PR, comprobar **qué dispara** ese merge, y decirlo. `web-ci` y `scraper-ci`
filtran por `paths` (`services/{web,scraper}/**`, `db/migrations/**`, su propio workflow), así que un
cambio de solo documentación —`README.md`, `CLAUDE.md`, `.claude/**`— sale con **0 checks**: ni
imagen publicada ni bump de GitOps. Se confirma con
`gh pr view <n> --json files,statusCheckRollup`.

**Why:** mergear a `main` despliega en dev automáticamente, así que el radio de impacto de un merge no
es evidente desde el diff. Y «0 checks» se parece peligrosamente a «los checks aún no han salido»: sin
saber que el workflow ni siquiera aplica, se espera un verde que no va a llegar nunca, o se lee la
ausencia como un problema.

**How to apply:** contrastar los `paths` de `.github/workflows/*.yml` con los ficheros del PR antes de
mergear, y confirmarlo con un PR equivalente anterior (el #337, de solo `CLAUDE.md`, salió con 0
checks). Distinto de [[gh-pr-merge-auto-no-espera]], que va de que los checks **existen pero no son
obligatorios**; esto va de que **no existen**. Ver también
[[escrituras-contra-prod-las-pide-el-usuario]].
