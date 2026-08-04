---
name: validar-qa
description: Validación profunda del entorno QA antes de promover una versión a producción — recorre la interfaz con un navegador real, el contrato de la API, el estado de los datos y de la ingesta, y el cluster, y emite un veredicto APTO / NO APTO / NO CONCLUYENTE con evidencia e issues. Úsala cuando se vaya a promocionar o publicar una versión, ante cualquier cambio mayor o menor que no sea un parche, y siempre que se pregunte "¿esto está listo para prod?", "valida QA", "revisa que QA funciona", "damos el visto bueno a la release".
---

# Validar QA a fondo

Esta skill decide si una versión puede salir a producción. Es lo único que hay entre un merge y el
usuario final, así que su valor entero está en que el listón sea **el mismo cada vez** y en que no
apruebe nada por omisión.

El hueco que tapa es real y se mide: `release-qa.yml` promueve por digest y el «gate humano» es
lanzar el workflow; el CI del web valida lint, typecheck y vitest, y del frontend solo que compila;
no hay ni un test de navegador en el repo; y los e2e corren contra una Postgres sembrada a mano, con
el guard de autenticación falseado y el locale de CI, no contra el `UTF8 | C | C` del cluster. Nada
de eso mira lo que de verdad está desplegado.

Cuando se escribió, QA corría v0.1.5 y bastó con mirar para encontrar: la última pasada de Cacles en
`failed` con un 429 de huella TLS, Hipercor **sin una sola fila** en `scrape_run` pese a que el ADR
afirmaba que las nueve tiendas tenían catálogo ingerido en QA, dos de nueve tiendas invisibles en las
facetas del catálogo, y pasadas en `success` con 69 y 15 errores. Ninguna alarma se disparó.

## Invocación

| Forma | Qué hace |
|---|---|
| `/validar-qa` | Completa: las cinco fases, ~60-90 min por el vigía |
| `/validar-qa rapida` | Sin jobs en el cluster ni vigía. ~20 min. **Nunca da APTO**, solo detecta lo obvio |
| `/validar-qa --frente ui\|api\|datos` | Un solo frente, para depurar o repetir |
| `/validar-qa v0.1.6` | Declara qué versión esperas encontrar desplegada; si no coincide, para |

---

## Fase 0 · Identidad de la versión

Sin esto el informe no vale nada: un «APTO» que no dice sobre qué artefacto se dio no es un
veredicto, es una opinión.

```bash
.claude/skills/validar-qa/scripts/qa-estado.sh
```

Recoge tag de imagen del web, del scraper y del matching, estado de ArgoCD, reinicios de pods, jobs
fallados, `/api/health` y `/api/config`. Además:

- **Los tres tags deben coincidir.** Si no, el `release-qa` quedó a medias: **P0** y se para.
- **Si se pidió una versión concreta** y la desplegada es otra, para y dilo. Validar una versión
  creyendo que es otra es peor que no validar.
- **`/api/config` con los tres campos nulos** en QA es **P0**: sin Keycloak la mitad de los frentes
  no aplica y el resultado sería un falso verde.
- **Delta desde la validación anterior.** Mira el informe más reciente de `.claude/qa-reports/` y
  saca `git log <tag-anterior>..<tag-actual> --oneline`. Eso dirige el esfuerzo: lo que cambió se
  mira con lupa, lo demás se cubre por catálogo.

## Fase 1 · Disparar el vigía (solo en modo completo)

```bash
kubectl -n deal-tracker-qa create job validacion-vigia-<version> --from=cronjob/deal-tracker-vigia
```

Tarda 25-40 minutos, por eso arranca al principio y se recoge en la Fase 5. Corre **desde el
cluster** a propósito: la pregunta es si las tiendas nos dejan entrar a *nosotros*, y esta máquina
sale por otra IP con otra reputación. Escribe filas en `vigia_run` y puede abrir o comentar una
issue `[vigía]` — es su comportamiento normal, no hay que evitarlo.

Anota el nombre del job: lo necesitas al final.

## Fase 2 · Datos y API, en paralelo

Lanza los dos subagentes a la vez; ninguno toca el navegador:

- `validador-qa-datos` — catálogo `casos-datos.md`, D1–D14.
- `validador-qa-api` — catálogo `casos-api.md`, A1–A51.

## Fase 3 · Interfaz

`validador-qa-ui` — catálogo `casos-ui.md`, U1–U50. **Solo**, y después de los otros dos: el
navegador de Playwright es un MCP único y dos agentes usándolo se pisan las pestañas. Además, saber
ya qué tiendas están vacías evita que el frente de UI reporte como roto lo que solo es dato ausente.

### Dependencias entre frentes

Dos casos no se sostienen solos, y hay que saberlo antes de correr un frente aislado:

- **D6** (prendas duplicadas) necesita contrastar la base contra la respuesta de la API. Con
  `--frente datos` el agente lo resuelve con `curl`, pero en la pasada completa la respuesta ya la
  tiene el frente de API: pásale el `product_id` en lugar de que lo repita.
- **D13** (vigía) depende del job de la Fase 1. Con `--frente datos` **no se lanza**: se declara
  fuera de alcance de esa ejecución. Una dependencia no satisfecha no es un fallo, y reportarla como
  tal mete un P0 falso.

## Fase 4 · Checkpoint manual de Telegram

El canje del `/start` y la llegada del aviso necesitan a una persona con la app abierta. El usuario
`test-qa` está vinculado al Telegram del operador precisamente para esto. Para y pregunta así:

```
✋ CHECKPOINT MANUAL — Telegram

1. Abre este enlace desde tu Telegram: https://t.me/<bot>?start=<token>
2. El bot debe responder: "✅ …ya estás vinculado"
3. La pestaña /ajustes debe pasar sola a "@<usuario>" en menos de 4 s, sin recargar

¿Qué ves?
```

Con la respuesta, cierra el caso. **Sin respuesta, el frente queda NO CUBIERTO**, y eso arrastra el
veredicto a NO CONCLUYENTE. No se aprueba por silencio.

## Fase 5 · Consolidar y decidir

1. **Recoge el vigía**: `kubectl -n deal-tracker-qa logs job/validacion-vigia-<version> --tail=200`.
   Su código de salida ya es un veredicto — 0 nada accionable, 1 algo lo es. Cada `✖` es P0, cada
   `⚠` es P1. Si no terminó, el frente queda no cubierto.
2. **Escribe el informe** en `.claude/qa-reports/<version>.md` con `informe-plantilla.md`.
3. **Abre issues** de los P0 y P1 (ver más abajo).
4. **Emite el veredicto** en el terminal, en tres líneas: veredicto, cuántos P0/P1/P2, y la frase
   que lo justifica.

---

## El listón

**P0 — bloquea la promoción.** `/api/health` en 503 · cualquier 5xx · login roto · catálogo vacío o
sin fotos · precios ≤ 0 · talla o color sin canonicalizar en proporción apreciable · calzado no
barefoot colado en el catálogo por defecto · alta o baja de interés que no funciona · migraciones
sin aplicar · los tres tags de imagen descuadrados · una tienda con la última pasada `failed`, en
`running` colgada, o sin ninguna pasada · drift entre la versión pedida y la desplegada · caída de
más del 30 % en las cifras de una tienda respecto al informe anterior · un `✖` del vigía · un
«oferta real» sobre un PVP inflado.

**P1 — no bloquea, pero se abre issue.** `errors > 0` en una pasada `success` · hoja de categoría
retirada · aviso de ritmo del vigía · error en la consola del navegador · faceta que no devuelve
resultados · regresión de UX no crítica · `ValueError: Tienda desconocida` (que es P1 **de proceso**:
la tienda está en `main` pero el `release-qa` aún no la ha promovido, no está rota).

**P2 — se anota y ya.** Cosmético.

### El veredicto

- **APTO** — cero P0 **y** los cuatro frentes ejecutados enteros.
- **NO APTO** — al menos un P0.
- **NO CONCLUYENTE** — cero P0 pero algún frente sin ejecutar o incompleto. Incluye siempre el modo
  `rapida`.

No hay cuarta opción y no se negocia sobre la marcha. Un frente que no se pudo correr **no cuenta
como aprobado**: el valor entero de esta skill es que nunca aprueba por omisión, y basta con
saltárselo una vez para que deje de servir.

### Las issues

**Buscar antes de abrir es obligatorio, y no basta con mirar los títulos.** Un hallazgo puede estar
ya registrado en una issue que trata de otra cosa y lo menciona de pasada — que es justo donde está
el contexto que evita duplicarlo:

```bash
gh issue list --state open --limit 60
gh issue list --state all --search "<término del hallazgo>"
gh issue view <n> --comments        # el estado real vive en el último comentario
```

Es el mismo patrón que usa `services/scraper/src/scraper/avisos.py` con su marcador `[vigía]`:
**una issue viva por asunto, y comentarios dentro**. Tres desenlaces posibles, y hay que elegir uno
a conciencia:

- **Existe y sigue vigente** → comenta en ella con la versión validada y lo nuevo que aportas. No
  abras otra.
- **Existe y ya está resuelta** → dilo en el informe. Una issue obsoleta detectada es tan útil como
  un hallazgo, y en este repo pasa: el estado real está en los comentarios, no en el título.
- **No existe** → ábrela.

Título: `[validación QA] <qué falla, en una frase>`. Cuerpo: versión validada, evidencia (comando y
salida, o captura), qué le pasa al usuario y severidad.

---

## Qué puede tocar en QA

QA no es producción, pero tiene datos reales y manda mensajes reales.

| Acción | Cuándo |
|---|---|
| Crear y borrar intereses de `test-qa` | Siempre. Limpieza obligatoria, también si un frente aborta |
| Job del vigía | Modo completo, automático. Escribe `vigia_run` y puede abrir issue `[vigía]` |
| Pasada de scraper de una tienda | **Solo si el frente de datos la encuentra rota y el operador lo confirma.** Avisa de la duración: Zara en frío son ~30 min |
| Job de matching | **Nunca sin que el operador lo pida en esta sesión.** En QA envía Telegram **reales** a personas y avanza la marca de agua de `job_state`, así que no es repetible: lo que se envió, enviado está |

Todo lo demás es lectura. El SQL va por `scripts/qa-sql.sh`, que abre transacción `READ ONLY` de
verdad — el motor rechaza la escritura, no un filtro de palabras. En agosto de 2026 un pytest
despistado se llevó por delante el histórico de `vigia_run`; esa valla es de aquello.

Y **nada de `kubectl patch`** sobre el cluster: ArgoCD corre con `selfHeal: true` y lo revierte en
segundos. Los cambios de cluster van por el repo de manifiestos.

## Qué no hacer

**No aprobar por omisión.** Es la única forma real de que esta skill haga daño: dar APTO con un
frente sin correr. Si algo no se pudo ejercer, el veredicto es NO CONCLUYENTE y se dice qué falta.

**No inventar hallazgos de relleno.** Un frente limpio se reporta en dos líneas. Un informe con
P2 decorativos se deja de leer, y entonces el P0 de la semana siguiente tampoco se lee.

**No confundir vacío con roto.** Una tienda que no ha ingerido deja pantallas vacías y filtros sin
resultados: la interfaz está bien, el dato no está. Son dueños distintos y mezclarlos manda a
alguien a depurar el sitio equivocado.

**No arreglar lo que encuentres.** Esto es una validación, no una sesión de trabajo. Se documenta,
se abre issue y se decide después. Un arreglo a mitad de validación invalida lo ya medido y deja QA
en un estado que nadie ha visto entero.
