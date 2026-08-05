---
name: commit-en-fish-se-come-backticks
description: "git commit -m con backticks en fish sustituye el identificador por vacío y commitea igual: los mensajes van por heredoc"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c45d5524-8f07-4135-ba73-a112e84384a1
  modified: 2026-08-05T11:56:49.383Z
---

La shell de este equipo es **fish**, donde los backticks son sustitución de comandos igual que en
bash. Los mensajes de commit de este repo van en español y citan identificadores entre backticks
(`lock_timeout`, `idle_in_transaction_session_timeout`), así que un `git commit -m "…"` los ejecuta:
imprime `command not found` **y commitea igual**, con el identificador sustituido por una cadena
vacía. El mensaje queda con un hueco en mitad de la frase.

**Why:** no falla, avisa de refilón y el commit se hace. Si no se relee el mensaje, el hueco llega a
`main` — y en este repo el porqué vive en el mensaje del commit tanto como en el código, así que se
pierde justo lo que justificaba el cambio.

**How to apply:** los mensajes de commit y los cuerpos de PR/issue, siempre por heredoc con el
delimitador entre comillas simples (que desactiva toda sustitución):

```bash
git commit -F /dev/stdin <<'EOF'
fix(scraper): … `lock_timeout` …
EOF

gh pr create --title "…" --body-file /dev/stdin <<'EOF'
…
EOF
```

Si ya se ha commiteado, `git commit --amend -F /dev/stdin <<'EOF'` lo arregla antes de subir.
Medido el 05/08/2026 cerrando [[verificar-en-cluster-dev]] la sesión de #169.
