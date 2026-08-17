---
name: gh-graphql-503-usar-rest
description: "cuando la API GraphQL de GitHub da 503, casi todo `gh` de alto nivel falla y la salida es la API REST por `gh api`"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2e464cf3-5771-4306-b51a-5745c1480150
  modified: 2026-08-17T18:37:54.501Z
---

La API GraphQL de GitHub estuvo devolviendo **503 intermitentes durante toda la sesión del
17/08/2026**, y eso tumba casi todo `gh` de alto nivel: `gh pr create`, `gh pr merge`,
`gh pr view --json`, `gh issue view --json`, `gh issue comment` y hasta el `gh issue create` que
resuelve etiquetas. El error es literal: `HTTP 503: No server is currently available to service your
request` sobre `https://api.github.com/graphql`. **La REST seguía perfecta**, así que todo se hizo
por `gh api`. Ya le había pasado a la sesión anterior (#479 lo dejó escrito de pasada).

**Why:** el síntoma parece un fallo del repo o de los permisos y no lo es, y reintentar el mismo
comando «a ver si ahora» gasta minutos: hoy hicieron falta hasta **tres** intentos para un
`POST /issues`. Lo caro no es el 503, es no reconocerlo y empezar a dudar del token, de la rama o de
si el PR se creó a medias.

**How to apply:** en cuanto veas un 503 de `graphql`, cambia de vía en vez de insistir:

```bash
# crear PR / mergear / borrar rama (el cuerpo, en un JSON con --input: ver [[commit-en-fish-se-come-backticks]])
gh api repos/<org>/<repo>/pulls -X POST --input payload.json --jq '.number, .html_url'
gh api repos/<org>/<repo>/pulls/<n>/merge -X PUT -f merge_method=merge --jq '.merged, .sha'
gh api -X DELETE repos/<org>/<repo>/git/refs/heads/<rama>
# comentar, editar el cuerpo de una issue (para marcar casillas) y crear issue con etiquetas
gh api repos/<org>/<repo>/issues/<n>/comments -X POST --input c.json
gh api repos/<org>/<repo>/issues/<n> -X PATCH --input body.json
gh api repos/<org>/<repo>/issues -X POST --input issue.json   # labels dentro del JSON
# el estado del CI, que es lo que [[gh-pr-merge-auto-no-espera]] pide mirar antes de mergear
gh api repos/<org>/<repo>/commits/<sha>/check-runs --jq '.check_runs[] | "\(.name)\t\(.status)\t\(.conclusion)"'
```

**Y no encadenes dos intentos en el mismo comando «por si el primero falla»**: si los dos pasan,
publicas el comentario dos veces. Pasó hoy en #437 y hubo que borrar el duplicado con
`gh api -X DELETE repos/<org>/<repo>/issues/comments/<id>`. Reintenta de uno en uno y comprueba.
