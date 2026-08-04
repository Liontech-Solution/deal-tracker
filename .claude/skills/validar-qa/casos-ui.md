# Casos del frente de interfaz

Navegador contra `https://dealtracker-qa.liontechsolution.com`. No hay ni un test de navegador en
el repo —CI del web solo comprueba que la SPA *compila*—, así que **este frente es la única
cobertura que existe** de todo lo que sigue. Trátalo como tal: un caso que no puedas ejercer se
declara no cubierto, no se da por bueno.

Dos herramientas que se usan poco y valen mucho aquí: `browser_console_messages` y
`browser_network_requests`. Un error de consola o un 500 de fondo no rompen la pantalla —el usuario
ve un hueco y se va— pero son la señal más temprana que hay.

---

## U0 · Sesión

| # | Paso | Se espera |
|---|---|---|
| U1 | Cargar `/` sin sesión | catálogo público visible, botón de login en la cabecera |
| U2 | Pulsar login | redirección al Keycloak de `keycloak-dev`, formulario real |
| U3 | Entrar con las credenciales de `.claude/qa-test-user.local` | vuelta a la SPA **ya autenticada**, `UserMenu` en lugar del botón |
| U4 | Recargar la página | la sesión **sobrevive** (`check-sso` + PKCE). Si obliga a repetir login, es **P0**: nadie usa así una web |

El login va por la interfaz de verdad, no inyectando un token: parte de lo que se valida es que el
`silent-check-sso` funciona en el dominio de QA.

## U1 · Home

| # | Paso | Se espera |
|---|---|---|
| U5 | Cargar `/` | hero, buscador y tira de ofertas **con productos y fotos** |
| U6 | La tira de ofertas | 8 ítems como mucho; si no hay ninguna oferta real, el estado vacío honesto, nunca una fila rota |
| U7 | Escribir en el buscador y enviar | navega a `/catalogo?q=…` con el término aplicado |
| U8 | Pulsar una sugerencia (`botas`) | misma navegación con ese término |
| U9 | Tarjeta «Ropa» y tarjeta «Zapatería» | `/catalogo?section=ropa` y `?section=zapateria`, con resultados |
| U10 | Botón «Empieza a seguir prendas» | hoy solo lanza un toast: es un placeholder conocido. Repórtalo **P1** sin dramatizar, y **no** lo cuentes como fallo nuevo cada pasada |

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
| U27 | Badge de stock | `stock` / `agotado` / `descatalogado` según el dato |
| U28 | Gráfica de histórico | línea de precio, PVP discontinuo, punto de mínimo, y tooltip con precio y fecha en `es-ES` al pasar por encima |
| U29 | Variante con menos de dos puntos | mensaje «Aún no hay suficiente histórico», no una gráfica vacía ni un error |
| U30 | «Ver en \<tienda\>» | abre la ficha real de la tienda, `noopener noreferrer`. Una URL rota es **P1** por tienda |
| U31 | Campana de la tarjeta del catálogo | hoy es placeholder incluso con sesión: **P1** conocido, igual que U10 |

## U4 · Seguir una prenda

| # | Paso | Se espera |
|---|---|---|
| U32 | «Seguir esta variante» **sin** sesión | lanza el login, no un error |
| U33 | Con sesión | abre el `FollowModal` |
| U34 | El modal | alcance variante/producto, slider 5–70 en pasos de 5 (20 por defecto), base de comparación y ventana 7/14/30/60/90 (30 por defecto) |
| U35 | Escape y clic en el fondo | cierran el modal |
| U36 | Reabrir | vuelve a los valores por defecto, no recuerda el intento anterior |
| U37 | Confirmar | toast de éxito **y** el interés aparece en `/seguimientos` con sus chips (`−X% mínimo`, base, ventana, tienda) |
| U38 | Borrar el interés desde `/seguimientos` | desaparece con toast |
| U39 | `/seguimientos` sin ninguno | estado vacío con enlace al catálogo |

U37 y U38 **escriben**: deja la lista como la encontraste. Si el frente aborta a medias, bórralo por
API antes de dar el informe.

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
