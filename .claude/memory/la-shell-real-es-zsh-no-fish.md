---
name: la-shell-real-es-zsh-no-fish
description: "el entorno anuncia /bin/fish pero los comandos corren en zsh, en primer plano y en segundo; y zsh no hace word-splitting, así que un bucle sobre $var sin comillas itera una vez y no falla"
metadata: 
  node_type: memory
  type: reference
  originSessionId: e0de5743-20ea-46d8-9939-5d97b7f3ae70
  modified: 2026-08-15T22:46:19.374Z
---

El bloque de entorno dice `Shell: /bin/fish`, y es engañoso: **los comandos de la herramienta Bash
corren en zsh**, tanto en primer plano como con `run_in_background`. Medido el 15/08/2026 en las dos
modalidades:

```
shell efectivo: /usr/bin/zsh
ZSH_VERSION=5.9.2  BASH_VERSION=no
```

Dos consecuencias que muerden de formas distintas:

**1. La sintaxis de fish no vale, y falla ruidosamente.** Un `for i in (seq 1 120)` en un comando de
segundo plano muere con `(eval):4: parse error near ')'`, que es formato de error de zsh. Se pierde
el job entero. La forma segura para cualquier script no trivial —y sobre todo para los de
`run_in_background`— es escribirlo en un fichero del scratchpad con `#!/bin/sh` y lanzarlo con
`sh <ruta>`, en vez de pelearse con el quoting inline.

**2. La sintaxis de bash sí se parsea, pero no siempre significa lo mismo — y eso falla en
silencio, que es peor.** zsh **no** hace word-splitting de una variable sin comillas:

```sh
v="a b c"; for x in $v; do ...; done    # bash: 3 iteraciones · zsh: 1
```

O sea que un bucle escrito con la costumbre de bash recorre un solo elemento, no da error, y el
resultado parece correcto. Si necesitas iterar sobre una lista, usa un array
(`ids=(1 2 3); for id in "${ids[@]}"`) o pásala por `printf '%s\n' … | while read`.

**How to apply:** no asumas bash ni fish al escribir un comando — asume zsh. Antes de dar por bueno
un bucle que itera sobre una variable, comprueba cuántas vueltas dio.

Nota sobre [[commit-en-fish-se-come-backticks]]: su remedio (pasar los cuerpos por `--body-file` o
heredoc `<<'EOF'`) sigue siendo el correcto, pero la causa no es fish — en zsh los backticks dentro
de comillas dobles también hacen sustitución de comandos, y el síntoma es idéntico.
