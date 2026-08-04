---
name: adr-update-por-cli
description: "manage_adr en modo update REEMPLAZA el ADR entero; publicarlo siempre con el CLI pasando el fichero, nunca escribiendo contenido a mano"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 31501bb9-0b33-4106-9061-4e31b18a2197
  modified: 2026-08-03T17:42:48.330Z
---

`manage_adr(mode='update')` **reemplaza el ADR completo** con lo que le pases en `content`: no
hace merge ni parchea secciones. Llamarlo con un texto corto (o un placeholder) borra en silencio
los ~21k del ADR real en el grafo.

Publicarlo siempre desde el fichero versionado, con el CLI:

```bash
codebase-memory-mcp cli manage_adr --project home-juanjocop-Proyectos-deal-tracker \
  --mode update --content "$(cat .claude/adr/deal-tracker.md)"
```

**Why:** el fichero de `.claude/adr/` es la fuente de verdad y el grafo solo la copia consultable
(ver [[adr-contexto-compartido]]); el CLI es la única forma cómoda de pasar el fichero entero sin
transcribirlo. Con la herramienta MCP directa es fácil mandar contenido parcial sin darse cuenta.

**How to apply:** editar el fichero `.claude/adr/<proyecto>.md`, y solo entonces republicar con el
CLI. Comprobar después con `manage_adr(mode='sections')` que salen todas las secciones esperadas —
si sale una lista corta, el ADR del grafo se ha perdido y hay que republicarlo.

**Y esa comprobación sirve para algo más que contar secciones: el parser trata CUALQUIER línea que
empiece por `#` como encabezado.** En este ADR el texto va justificado a ~100 columnas y las
referencias a issues (`#135`, `#64`) son constantes, así que es fácil que el ajuste de línea deje
una al principio de una línea — y entonces sale como sección falsa en medio del índice, que es lo
primero que se consulta para orientarse. Pasó el 03/08/2026 con dos. Se detecta con
`grep -n "^#" .claude/adr/<proyecto>.md` y se arregla reajustando la línea, sin tocar el contenido.
