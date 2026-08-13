---
name: subagente-nuevo-no-invocable-en-su-sesion
description: "un .claude/agents/*.md recién creado NO es invocable por subagent_type en la misma sesión: el registro se carga al arrancar"
metadata:
  node_type: memory
  type: feedback
---

Crear `.claude/agents/<nombre>.md` **no** lo registra en la sesión en curso. Llamarlo por
`subagent_type` falla con:

```
Agent type '<nombre>' not found. Available agents: claude, ..., revisor-contrato-esquema, ...
```

La lista de agentes se resuelve al arrancar la sesión, así que el fichero nuevo solo aparece a
partir de la siguiente. (Medido el 13/08/2026 creando `revisor-espejo-honestidad` en #228; el aviso
de que el agente ya está disponible llegó **después**, al empezar el turno siguiente.)

**Why:** el error se lee como que el fichero está mal —frontmatter inválido, nombre que no casa— y
la reacción natural es reescribirlo, que es tiempo perdido sobre algo que ya estaba bien. Y peor: un
agente escrito y no ejercido es exactamente lo que la issue que lo pedía creía haber entregado. Que
exista no prueba que detecte nada.

**How to apply:** para ejercerlo en la misma sesión, extraer el cuerpo del fichero (sin el
frontmatter) y pasárselo como instrucciones a un agente genérico:

```bash
sed -n '/^---$/,/^---$/!p' .claude/agents/<nombre>.md > <scratchpad>/instrucciones.md
```

…y lanzar `claude` / `general-purpose` diciéndole que lea ese fichero y se comporte exactamente como
indica, con las mismas herramientas que declara su frontmatter.

Y ejercerlo **dos veces**: contra el árbol limpio (debe decir que no hay hallazgo, sin inventar ruido
de estilo) y contra una divergencia deliberada que se revierte después. Solo la segunda prueba que
sirve — en #228 fue mover `INFLATED_LIST_MARGIN` en un lado del espejo, y ahí el agente encontró
además un agujero real del test que nadie buscaba ([[buscar-issue-antes-de-abrir]] para lo que vino
luego).
