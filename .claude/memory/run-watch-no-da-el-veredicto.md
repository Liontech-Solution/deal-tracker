---
name: run-watch-no-da-el-veredicto
description: "`gh run watch` imprime anotaciones de pasos con continue-on-error y parece rojo aunque el run acabe en success: el veredicto es `gh run view --json conclusion`"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2e464cf3-5771-4306-b51a-5745c1480150
  modified: 2026-08-17T18:38:12.099Z
---

`gh run watch <id> --exit-status` sobre el `web-ci` del PR #487 terminó imprimiendo
`X Process completed with exit code 1` seguido del aviso de eslint de `FilterPanel.tsx`, y el run
había acabado en **`success`**: los 29 pasos de `lint-type-test` y los de `build & push` estaban
todos en `success`. Lo que imprime ahí son **anotaciones**, y la del exit code 1 la produce el paso
`Auditoría de dependencias`, que lleva `continue-on-error` y no cuenta para el resultado.

**Why:** leído deprisa, eso es exactamente lo que hay que tratar como rojo — y `cerrar-sesion` dice
que en rojo no se mergea. Dar por roto un CI verde te deja el trabajo parado en el escalón 4 (PR
abierto) por nada, y encima te manda a depurar un fallo que no existe. El aviso de `FilterPanel.tsx`
además es **preexistente** en `main`, así que refuerza la lectura equivocada de que lo has roto tú.

**How to apply:** el veredicto se pregunta aparte, nunca se lee de la salida de `watch`:

```bash
gh run view <id> --json status,conclusion --jq '"\(.status) \(.conclusion)"'   # completed success
gh run view <id> --json jobs --jq '.jobs[] | .name, (.steps[] | select(.conclusion!="success") | "  ✗ \(.name)")'
```

Si de verdad hay un paso fallido, el segundo comando lo nombra; si no imprime ningún `✗`, lo que
viste era una anotación. Con la GraphQL caída (ver [[gh-graphql-503-usar-rest]]) el equivalente es
`gh api repos/<org>/<repo>/commits/<sha>/check-runs`. Y para no quedarte mirando,
`gh run watch <id> --exit-status > /dev/null` y después la consulta del veredicto — así el
`watch` solo hace de espera, que es lo único que hace bien. Ver también
[[gh-pr-merge-auto-no-espera]].
