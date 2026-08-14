---
name: adr-update-por-cli
description: "manage_adr en modo update REEMPLAZA el ADR entero; publicarlo siempre con el CLI pasando el fichero, nunca escribiendo contenido a mano"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 31501bb9-0b33-4106-9061-4e31b18a2197
  modified: 2026-08-14T14:21:00.003Z
---

`manage_adr(mode='update')` **reemplaza el ADR completo** con lo que le pases en `content`: no
hace merge ni parchea secciones. Llamarlo con un texto corto (o un placeholder) borra en silencio
los ~21k del ADR real en el grafo.

Publicarlo siempre desde el fichero versionado, con el CLI. **Y ya no cabe en `--content`**: el
05/08/2026, con el ADR en 128778 bytes, la forma con `$(cat …)` empezó a morir con
`bash: … : La lista de argumentos es demasiado larga`. El ADR solo crece, así que la forma buena es
`--args-file` con un JSON, que no pasa por argv:

```bash
python3 -c "
import json
adr = open('.claude/adr/deal-tracker.md').read()
json.dump({'project':'home-juanjocop-Proyectos-deal-tracker','mode':'update','content':adr},
          open('/tmp/adr_args.json','w'))
"
codebase-memory-mcp cli manage_adr --args-file /tmp/adr_args.json
```

(`codebase-memory-mcp cli manage_adr --help` lista las tres entradas alternativas: `--args-file`,
JSON por stdin y JSON crudo como argumento posicional.)

**Why:** el fichero de `.claude/adr/` es la fuente de verdad y el grafo solo la copia consultable
(ver [[adr-contexto-compartido]]); el CLI es la única forma cómoda de pasar el fichero entero sin
transcribirlo. Con la herramienta MCP directa es fácil mandar contenido parcial sin darse cuenta.

**How to apply:** editar el fichero `.claude/adr/<proyecto>.md`, y solo entonces republicar con el
CLI. Comprobar después con `manage_adr(mode='sections')` que salen todas las secciones esperadas —
si sale una lista corta, el ADR del grafo se ha perdido y hay que republicarlo.

**Pero `sections` NO basta para saber si el grafo tiene TU versión, y `adr_present: true` tampoco.**
`index_repository` excluye `.claude/`, así que reindexar ni lee ni restaura el fichero: deja intacto
lo que hubiera publicado el último que llamó a `manage_adr`, que puede ser otra sesión. El 05/08/2026
un `full` devolvió `adr_present: true` y `sections` completo, y el ADR del grafo era el de otra
sesión, sin mis cambios — porque iban **dentro de una sección que ya existía** y ningún encabezado
cambió. La comprobación buena es por contenido: `mode='get'` y `grep` de una frase que hayas escrito
tú.

**Con otra sesión trabajando a la vez, republicar puede perderse sin error.** El 04/08/2026 el CLI
devolvió `{"status":"updated"}` y acto seguido `mode='get'` dio `no_adr`: entre las dos llamadas,
otra sesión había mergeado su PR y reindexado, y su `index_repository` se llevó el ADR recién
publicado. Peor, mi `cat` era de un `main` anterior al commit de ADR de esa sesión, así que aunque
hubiera sobrevivido habría sido una versión vieja. Se detecta con `list_projects`, que enseña el
`head_sha` indexado: si no es el `main` que acabas de dejar, alguien ha reindexado después. La
salida es `git fetch && git merge --ff-only origin/main` y republicar desde el fichero ya
actualizado (ver [[reindexar-tras-actualizar-main]]). Y `git worktree list` es el aviso barato de
que hay compañía antes de empezar.

**Y esa comprobación sirve para algo más que contar secciones: el parser trata CUALQUIER línea que
empiece por `#` como encabezado.** En este ADR el texto va justificado a ~100 columnas y las
referencias a issues (`#135`, `#64`) son constantes, así que es fácil que el ajuste de línea deje
una al principio de una línea — y entonces sale como sección falsa en medio del índice, que es lo
primero que se consulta para orientarse. Pasó el 03/08/2026 con dos, y **otra vez el 11/08/2026** al añadir una
sección. Se arregla reajustando la línea, sin tocar el contenido, y el grep va **antes** de
republicar y no después — si no, te toca un segundo commit y una segunda publicación para arreglar
lo que costaba un vistazo:

```bash
grep -n "^#[0-9]" .claude/adr/<proyecto>.md   # antes de construir el args-file
```

**Ojo: `sections` no prueba nada en un ADR sin subsecciones.** El de
`k3s-local-apps-manifests` es una lista de viñetas dentro de `## PATTERNS`, así que `--mode sections`
devuelve siempre los mismos seis encabezados y sale idéntico con el ADR viejo o con el nuevo. Para
esos hay que comprobar **contenido**:
`codebase-memory-mcp cli manage_adr --project <p> --mode get | grep -o "<frase>" | sort | uniq -c`, y
comparar los recuentos con los del fichero. Dos trampas medidas el 08/08/2026: el `get` devuelve el
ADR entero **en una sola línea JSON**, así que `grep -c` cuenta 1 pase lo que pase y hay que usar
`grep -o`; y los backticks del markdown están dentro del texto, así que un patrón como
`"OnFailure borra"` no casa con `` `OnFailure` borra ``.

**Y para comprobar que el grafo tiene TU versión, no uses `--mode get` con `sections`.** El filtro no
filtra: el 12/08/2026 pedirle tres secciones del ADR de deal-tracker devolvió el documento entero
(245757 caracteres) y reventó el límite de tokens de la herramienta. La comprobación barata es
volcar `--mode get` a un fichero y buscar dentro **una frase que hayas escrito hoy** y otra que
hayas **quitado** — las dos, porque encontrar la nueva no prueba que la vieja se fuera. Y con
`grep -o … | wc -l`, no `grep -c`: la salida es una única línea JSON gigante y `-c` cuenta líneas,
así que siempre dirá 1.

**Y elige el testigo de una línea.** El JSON conserva los `\n` del markdown, así que una frase que
en el fichero esté **partida por el salto de línea** da 0 aunque el ADR esté entero — un falso
negativo que parece exactamente lo mismo que haber perdido la publicación. Medido el 14/08/2026 con
tres sesiones republicando por turnos. Antes de usar una frase como testigo, compruébala en el
fichero (`grep -c "<frase>" .claude/adr/<proyecto>.md`): si ahí ya da 0, el problema es el testigo y
no el grafo.
