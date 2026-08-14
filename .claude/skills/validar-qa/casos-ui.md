# Casos del frente de interfaz

Navegador contra `https://dealtracker-qa.liontechsolution.com`. No hay ni un test de navegador en
el repo —CI del web solo comprueba que la SPA *compila*—, así que **este frente es la única
cobertura que existe** de todo lo que sigue. Trátalo como tal: un caso que no puedas ejercer se
declara no cubierto, no se da por bueno.

Dos herramientas que se usan poco y valen mucho aquí: `browser_console_messages` y
`browser_network_requests`. Un error de consola o un 500 de fondo no rompen la pantalla —el usuario
ve un hueco y se va— pero son la señal más temprana que hay.

---

## U0 · Sesión y el muro del catálogo

Desde v0.3.0 **al catálogo solo se entra con sesión** (#309). Este bloque se ejerce entero
**anónimo** —ventana limpia o de incógnito, sin arrastrar la sesión de una pasada anterior— y es lo
primero que se hace, porque a partir de U3 ya no se puede volver atrás sin cerrar sesión.

| # | Paso | Se espera |
|---|---|---|
| U1 | Cargar `/` sin sesión | la home: hero, «cómo funciona» y botón de login en la cabecera. **Ni una prenda, ni una foto de producto, ni el número de tiendas** — donde iba la tira de ofertas hay una invitación a entrar. Un solo producto visible aquí es **P0**: es la promesa de la versión |
| U1b | Ir a `/catalogo` sin sesión (por la pestaña «Catálogo», el buscador de la cabecera o una tarjeta de sección) | aterriza en `/acceso`: hace falta cuenta · el registro está cerrado por ahora · botón de entrar. Que se vea el catálogo, aunque sea un instante antes de redirigir, es **P0** |
| U1c | Abrir `/producto/<id>` a pelo sin sesión (el enlace compartido) | mismo muro en `/acceso` |
| U1d | Abrir `/acceso` directamente | la página se sostiene sola y ofrece login, sin quedarse en blanco por no traer destino |
| U1e | Con `browser_network_requests`, mirar las llamadas de U1 | las de `/api/catalog/*` o no se hacen, o responden **401**. Un 200 con datos significa que el candado está solo en la interfaz — **P0** |
| U1f | Pulsar «Empieza a seguir prendas», el CTA del hero, **sin sesión** | aterriza en `/acceso`, **no** en el formulario de Keycloak. El CTA es la puerta de entrada del visitante y `/acceso` es lo que le explica que hace falta cuenta y que el registro está cerrado. Volver a saltar a Keycloak es **P1**: la regresión de #383. **Exigible a partir de v0.5.0** — el arreglo (`51241c1`) es posterior al tag `v0.4.0`, así que contra una QA que sirva v0.4.0 o anterior el CTA salta a Keycloak con `redirect_uri=…%2F` y **eso no es un hallazgo**, es la versión sin el arreglo (comprobado en QA el 14/08/2026). Antes de reportarlo: `git merge-base --is-ancestor 51241c1 <tag desplegado>` |
| U2 | Pulsar login desde `/acceso` (viniendo de U1c) | redirección al Keycloak de `keycloak-dev`, formulario real. **Y mira el `redirect_uri`**, que es la mitad que se escapa: debe ser `https%3A%2F%2Fdealtracker-qa.liontechsolution.com%2Fcatalogo`. Si trae `…%2F` —la portada— el destino se perdió por el camino y el visitante acaba donde no pidió ir; era el segundo defecto de #383 y no se ve mirando solo a dónde navega |
| U3 | Entrar con las credenciales de `.claude/qa-test-user.local` | vuelta a la SPA **ya autenticada**, `UserMenu` en lugar del botón, y **de vuelta a la ficha de U1c**, no a la raíz. Volver a `/` es **P1**: rompe compartir enlaces, que es justo lo que U12 protege en el catálogo |
| U4 | Recargar la página | la sesión **sobrevive** (`check-sso` + PKCE). Si obliga a repetir login, es **P0**: nadie usa así una web |

El login va por la interfaz de verdad, no inyectando un token: parte de lo que se valida es que el
`silent-check-sso` funciona en el dominio de QA.

**Este bloque entero solo es observable en QA o en prod**, y conviene saberlo antes de intentar
adelantarlo: todas sus ramas cuelgan de `auth.enabled === true` con la sesión cerrada, y `dev` deja
los `KEYCLOAK_*` sin poner **a propósito**. Allí el CTA del hero se va al catálogo sin pasar por lo
que aquí se comprueba, así que un `dev` verde no dice nada de U0.

**Del resto del fichero en adelante se da por hecha la sesión de U3.** Antes de v0.3.0 daba igual
en casi todos los casos; ahora no, porque sin ella no hay catálogo que recorrer.

## U1 · Home

| # | Paso | Se espera |
|---|---|---|
| U5 | Cargar `/` **con sesión** | hero, buscador y tira de ofertas **con productos y fotos**, y el contador «N tiendas rastreadas» con su número. Contrasta con U1: los mismos elementos que allí no pueden verse, aquí tienen que estar |
| U6 | La tira de ofertas | 8 ítems como mucho; si no hay ninguna oferta real, el estado vacío honesto, nunca una fila rota |
| U7 | Escribir en el buscador y enviar | navega a `/catalogo?q=…` con el término aplicado |
| U8 | Pulsar una sugerencia (`botas`) | misma navegación con ese término |
| U9 | Tarjeta «Ropa» y tarjeta «Zapatería» | `/catalogo?section=ropa` y `?section=zapateria`, con resultados |
| U10b | Panel «Dos etiquetas, y cuándo no ponemos ninguna» | enumera **tres** estados —«Oferta real», «Precio inflado» y «Sin etiqueta»— y son los mismos que el catálogo puede enseñar hoy. Es el caso que faltaba: hasta #332 este panel no lo ejercía nadie, y explicaba dos etiquetas cuando los estados ya eran tres, con el ausente siendo el mayoritario (en prod, 15.968 prendas sin etiqueta frente a 335 ofertas reales). Que la home describa un estado que el producto ya no tiene —o se calle uno que sí— es **P1**: es la promesa de honestidad explicada mal, y se pudre en silencio igual que se pudrió U10 |
| U10 | Botón «Empieza a seguir prendas» | ~~solo lanza un toast, placeholder conocido~~ **ya no lo es**: con sesión lleva a `/catalogo` (`HomePage.tsx`). ~~Sin ella lanza el login de Keycloak~~ — desde #383 el anónimo va a `/acceso`, y eso se ejerce en **U1f**, no aquí. El toast solo es alcanzable con Keycloak **desactivado**, o sea en `dev` y nunca en QA. Aquí, que estamos en el bloque con sesión (ver U5), lo que se exige es la navegación al catálogo; un toast es **P1** de verdad, no un conocido |

## U2 · Catálogo

| # | Paso | Se espera |
|---|---|---|
| U11 | Aplicar género, categoría, talla, color y tienda | la URL recoge **todos** los filtros |
| U12 | **Recargar** esa URL | se reproduce exactamente la misma vista. Este es el caso más valioso del frente: todo el estado vive en la query string y una regresión aquí rompe compartir enlaces |
| U13 | Abrir la URL en pestaña nueva | ídem |
| U14 | Chips de filtros activos | uno por filtro, y quitar uno quita **solo** ese |
| U15 | «Limpiar todo» | vuelve al catálogo sin filtros |
| U16 | Cambiar de sección ropa → zapatería | las **tallas de la faceta cambian** y no se mezclan (meses/años frente a números de pie) |
| U17 | «Cargar más» | añade 12 sin duplicar los ya cargados ni saltarse ninguno |
| U18 | Combinación imposible (talla de bebé + categoría de calzado) | estado vacío con acción de limpiar, nunca una pantalla en blanco |
| U19 | Switch «Solo ofertas reales» | todos los resultados con badge de oferta real |
| U20 | Switch «Solo en stock» | ningún resultado marcado como agotado |
| U21 | Durante la carga | skeleton, no salto de maquetación |

## U3 · Ficha de producto

| # | Paso | Se espera |
|---|---|---|
| U22 | Abrir un producto desde el catálogo | foto, nombre, precio, tienda y enlace externo |
| U23 | Tallas no disponibles | deshabilitadas y tachadas, no ocultas: el usuario tiene que ver que existe pero no está |
| U24 | Cambiar de referencia de color | cambian **foto y precio a la vez**, y la miniatura vuelve a la primera. Que cambie la foto y no el precio es **P0**: enseña un precio que no es el de lo que se ve |
| U25 | Producto con dos referencias del **mismo** nombre de color (caso H&M) | se distinguen con «· 2ª referencia», no aparecen como duplicado indistinguible |
| U26 | Badge de honestidad | coherente con el descuento mostrado; un «oferta real» sobre un PVP inflado es **P0** — es justo el fraude que el producto existe para detectar |
| U26b | Prenda descubierta **ya rebajada** (la tienda enseña tachado y nunca la hemos visto más cara) | **no** lleva badge «Precio inflado», y la ficha dice «Descuento sin confirmar: llevamos N días siguiéndola…» sin acusar a nadie. Un «Precio inflado» aquí es **P0**: es afirmar un fraude que no hemos comprobado (#332) |
| U26c | Ausencia total de badges «Precio inflado» **en las siete tiendas que no publican el mínimo de 30 días** | **es lo esperado, no un hallazgo.** Desde #332 acusar por histórico propio exige 90 días cubiertos, y la serie más antigua de prod arranca el 07/08/2026: hasta **~05/11/2026** (qa ~22/10/2026) no puede haber ni uno por esa vía. Reportarlo como regresión es un P0 inventado. Lo que sí hay que comprobar es que esas prendas salen como «Descuento sin confirmar» y no como si no pasara nada |
| U26d | Badges «Precio inflado» **en C&A y Springfield** | **desde #354 tiene que haberlos, y su ausencia total es un P1.** Esas dos publican el mínimo de 30 días de la Ómnibus, y esa vía no espera a los 90 días: acusa desde la primera pasada. Medido en QA el 14/08/2026 sobre datos del 10/08: **291 variantes de 89 productos**. El texto de la ficha tiene que citar la cifra —«la propia tienda declara haber vendido esta prenda a X»— y **no** decir «respecto a su historial»: eso último sobre una prenda con `trackedDays: 0` es **P0**, es afirmar lo que no sabemos |
| U27 | Badge de stock | `stock` / `agotado` / `descatalogado` según el dato |
| U28 | Gráfica de histórico | línea de precio, PVP discontinuo, punto de mínimo, y tooltip con precio y fecha en `es-ES` al pasar por encima |
| U29 | Variante con menos de dos puntos | mensaje «Aún no hay suficiente histórico», no una gráfica vacía ni un error |
| U30 | «Ver en \<tienda\>» | abre la ficha real de la tienda, `noopener noreferrer`. Una URL rota es **P1** por tienda |
| U31 | Campana de la tarjeta del catálogo | ~~placeholder conocido~~ **cableada desde #301**: abre el mismo `FollowModal` que la ficha, con alcance de **producto entero** (desde la rejilla no hay talla ni color elegidos), y **sin navegar** a la ficha — la tarjeta entera navega, así que el clic de la campana no puede arrastrarte. Que no haga nada, o que te lleve a la ficha, es un fallo **P1 nuevo**, no un conocido: sería la regresión de #301 |
| U31b | Confirmar ese modal con «Crear aviso» | **crea el interés de verdad**: un `POST /api/interests` en `browser_network_requests` (no basta el toast), y la prenda aparece en `/seguimientos` con alcance de producto. Es la mitad que #301 pidió verificar y que ninguna validación había ejercido desde la **tarjeta**. Bórralo después, como todo lo que escribas |

## U4 · Seguir una prenda

| # | Paso | Se espera |
|---|---|---|
| U32 | «Seguir esta variante» **sin** sesión | ~~lanza el login~~ **ya no se puede ejercer desde v0.3.0**: a la ficha no se llega sin sesión (#309), así que la rama anónima solo es alcanzable si el token caduca con la página abierta. Si consigues provocarlo, el botón tiene que llevar al login y no dar un error; si no, decláralo **no aplicable**, no fallado |
| U33 | Con sesión | abre el `FollowModal` |
| U34 | El modal | alcance variante/producto, slider 5–70 en pasos de 5 (20 por defecto), base de comparación y ventana 7/14/30/60/90 (30 por defecto) |
| U35 | Escape y clic en el fondo | cierran el modal |
| U36 | Reabrir | vuelve a los valores por defecto, no recuerda el intento anterior |
| U37 | Confirmar | toast de éxito **y** el interés aparece en `/seguimientos` con sus chips (`−X% mínimo`, base, ventana, tienda) |
| U38 | Borrar el interés desde `/seguimientos` | desaparece con toast |
| U39 | `/seguimientos` sin ninguno | estado vacío con enlace al catálogo. **Ejercitable desde el 14/08/2026**: hasta entonces la lista nunca estaba vacía y el caso se declaraba no ejercido en cada validación (#385) |

U37 y U38 **escriben**: deja la lista como la encontraste. Si el frente aborta a medias, bórralo por
API antes de dar el informe. Y ojo, porque de eso depende U39: si te dejas uno vivo, el siguiente
validador se encuentra el caso bloqueado y sin saber por qué.

> **Los dos intereses que arrastraba `test-qa` ya no están, y no eran «ajenos».** Hasta el
> 14/08/2026 la lista traía dos intereses activos creados el 04/08 (`lefties` y `hm`, tienda entera,
> 0 % de mínimo, con 147 ms entre uno y otro: semilla, no algo curado). Tres validaciones seguidas
> los describieron como *intereses ajenos que no se tocan* y por eso nadie los quitó — pero en QA hay
> **un solo `app_user`**, y `GET /api/interests` solo devuelve los del usuario autenticado, así que
> si el frente de API los ve, son suyos. En diez días no generaron ni un aviso. Se dieron de baja por
> la API, que es lo mismo que hace un usuario, y la baja es **lógica**: las filas siguen ahí con
> `active = false` y se revierten con `UPDATE interest SET active = true WHERE id IN (2,3)`.
> Lo que **no** se hace es borrarlas en la base: `notification.interest_id` es `ON DELETE CASCADE` y
> un `DELETE` se llevaría por delante el histórico de avisos sin decir nada.

## U5 · Ajustes y Telegram

| # | Paso | Se espera |
|---|---|---|
| U40 | `/ajustes` con sesión | tarjeta de Telegram con el estado actual |
| U41 | «Vincular Telegram» | abre el deep-link `https://t.me/…?start=…` y la tarjeta pasa a pendiente |
| U42 | Dejar la pestaña abierta | consulta sola cada ~4 s (obsérvalo en `browser_network_requests`): la confirmación debe entrar **sin recargar** |
| U43 | Canje real del `/start` | ← checkpoint manual, ver la Fase 4 de `SKILL.md` |
| U44 | «Desvincular» | vuelve al estado inicial |

## U6 · Transversal

| # | Paso | Se espera |
|---|---|---|
| U45 | Alternar tema claro/oscuro | cambia toda la interfaz y **sobrevive a la recarga**; ningún texto queda ilegible sobre su fondo |
| U46 | Viewport 390×844 | el panel de filtros es un drawer inferior, aparece la `BottomNav`, y **nada desborda en horizontal** |
| U47 | Volver a escritorio | sidebar sticky de vuelta |
| U48 | `browser_console_messages` al final del recorrido | sin errores. Cada uno es **P1** con su traza |
| U49 | `browser_network_requests` al final | sin 4xx/5xx inesperados. Un 500 es **P0** aunque la pantalla se viera bien |
| U50 | Capturas | una por pantalla visitada, adjuntas al informe como evidencia |

---

## Cómo reportar

Cada fallo con **la URL exacta**, **la captura**, **lo que se esperaba** y **el mensaje de consola
o la petición de red** si los hay. Un «el catálogo se ve raro» no es un hallazgo.

Distingue siempre tres cosas que se confunden con facilidad y tienen dueños distintos:

- **Roto** — la interfaz no hace lo que promete. Va al informe con su severidad.
- **Vacío** — la interfaz funciona pero el dato no está (una tienda que no ingirió, un producto sin
  histórico). **No es un fallo de UI**: apúntalo y remite al frente de datos, que es quien lo
  explica. Confundirlos llena el informe de falsos P0.
- **Feo** — cosmético. **P2**, una línea, sin capturas ni párrafos.

Y no inventes casos de accesibilidad ni de rendimiento que no estén aquí. Si ves algo grave de esos
frentes, dilo en una línea al final como observación, no como caso fallado.
