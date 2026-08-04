---
name: buscar-issue-antes-de-abrir
description: "Antes de abrir una issue hay que buscar si el hallazgo ya está registrado, incluso mencionado de pasada en otra que trate de otra cosa"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a680605d-33b3-4695-9dab-5cbc453f7962
  modified: 2026-08-04T09:26:30.426Z
---

Antes de crear una issue nueva, comprobar **siempre** si ya existe alguna que mencione ese
hallazgo — no solo por título: `gh issue list --state all --search "<término>"` y mirar cuerpos y
comentarios, porque aquí una issue sobre otro asunto suele mencionar de pasada el problema y llevar
el contexto que evita duplicarlo.

**Why:** en este repo el cuerpo de la issue es el plan y el estado real vive en el último
comentario (ver [[revisar-backlog]] como skill que ya explota eso). Duplicar convierte el backlog en
ruido y hace que se dejen de leer los informes enteros. Además pasa lo contrario: al buscar se
descubren issues obsoletas cuyo trabajo ya está hecho, y eso es tan útil como el hallazgo.

**How to apply:** buscar antes de proponer o abrir. Tres desenlaces: existe y vigente → comentar
aportando solo lo nuevo; existe y ya resuelta → decirlo, es un hallazgo en sí; no existe → abrirla.
Está escrito como regla obligatoria en la skill `/validar-qa` y en los tres agentes
`validador-qa-*`.
