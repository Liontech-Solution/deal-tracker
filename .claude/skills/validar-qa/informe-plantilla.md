# Plantilla del informe

Se copia a `.claude/qa-reports/<version>.md`. El informe se versiona en git a propósito: es el
registro de por qué se promovió (o no) cada versión, y su bloque `## Cifras` es la línea base contra
la que la siguiente validación detecta regresiones. Sin ese bloque, la próxima pasada es ciega.

Sustituye todo lo que va entre `<>`. Borra esta cabecera al copiar.

---

```markdown
# Validación QA — <vX.Y.Z>

**Veredicto: <APTO | NO APTO | NO CONCLUYENTE>**

| | |
|---|---|
| Validado el | <fecha> |
| Imágenes | web `<tag>` · scraper `<tag>` · matching `<tag>` |
| Procedencia del dato | <«las 9 tiendas y el matching, escritos por `<tag>`» \| «n tiendas con dato de `<tag anterior>`: …» — salida de `qa-procedencia.sh`. Nunca «se supone»> |
| ArgoCD | `<sync>` / `<health>`, revisión `<sha>` |
| Versión anterior validada | <vX.Y.Z o «ninguna»> |
| Modo | <completa \| rapida \| frente único> |

<Una frase que justifique el veredicto. Si es NO APTO, cuál es el bloqueante. Si es NO CONCLUYENTE,
qué frente falta y por qué.>

## Frentes

| Frente | Casos | ✔ | ✖ | No ejercidos |
|--------|------:|--:|--:|-------------:|
| Interfaz (U1–U50)  | | | | |
| API (A1–A54)       | | | | |
| Datos (D1–D15)     | | | | |
| Vigía              | | | | |
| Telegram (manual)  | | | | |

## Bloqueantes (P0)

<Uno por bloque. Sin ninguno: «Ninguno.»>

### <título en una frase>
- **Qué falla:** <qué le pasa al usuario, no el síntoma técnico>
- **Evidencia:** <comando y salida, o captura>
- **Desde:** <esta versión | ya estaba en vX.Y.Z>
- **Issue:** <#n>

## Hallazgos (P1)

| Qué | Evidencia | Nuevo | Issue |
|---|---|---|---|

## Observaciones (P2) y notas

<Cosmético, dato ausente que no es fallo de código, y cualquier cosa que el siguiente validador
agradezca saber. Una línea cada una.>

## Cifras

Salida de D14. **No la borres**: es la línea base de la siguiente validación.

| tienda | productos | variantes | puntos 30d |
|---|---:|---:|---:|

<Si no había informe anterior, dilo aquí explícitamente: esta pasada no ha podido detectar
regresiones.>

## Qué no se validó

<Lo que quedó fuera y por qué. Esta sección nunca va vacía en modo `rapida`, y es la que impide que
un informe parezca más completo de lo que fue.>
```
