# Casos del frente de API

Base: `https://dealtracker-qa.liontechsolution.com/api`. Token del usuario `test-qa` con
`scripts/qa-token.sh` (dura 300 s: ante un 401 vuelve a pedirlo, no des el caso por roto).

```bash
API=https://dealtracker-qa.liontechsolution.com/api
TOKEN=$(.claude/skills/validar-qa/scripts/qa-token.sh)
aut=(-H "Authorization: Bearer $TOKEN")                  # atajo: petición firmada
cod() { curl -s -o /dev/null -w '%{http_code}' "$@"; }   # solo el código
```

> **Desde v0.3.0 el catálogo pide sesión (#309).** Salvo que un caso diga explícitamente lo
> contrario, **todas** las peticiones de este fichero van firmadas con `"${aut[@]}"` — incluidas
> las del catálogo, que hasta v0.2.0 se hacían a pelo. Solo tres endpoints siguen siendo públicos,
> y hay casos que lo comprueban: `/health`, `/config` y el 404 de A3.
>
> Un 401 inesperado en el bloque de catálogo casi siempre es el token caducado, no una regresión:
> vuelve a pedirlo antes de escribir nada.

Estos casos no repiten lo que ya cubren los e2e de `services/web/test/`. Están para lo que aquellos
no pueden ver: que el **contrato desplegado en QA**, contra el **dato real de nueve tiendas** y con
Keycloak de por medio, se comporta como dice el código.

---

## Salud y configuración

| # | Petición | Se espera |
|---|---|---|
| A1 | `GET /health` | 200 y `{"status":"ok","db":"up"}`. Un 503 es **P0** y aborta el resto del frente |
| A2 | `GET /config` | 200 con `url`, `realm` y `clientId` **no nulos**. En QA hay Keycloak; tres nulos significan que el despliegue perdió la configuración y media SPA queda en modo placeholder — **P0** |
| A3 | `GET /noexiste` | 404, y **no** el `index.html` de la SPA: el `ServeStaticModule` excluye `/api/*` |

## Catálogo (con sesión)

| # | Petición | Se espera |
|---|---|---|
| A4 | `GET /catalog/products` | 200, exactamente **20** ítems (el límite por defecto), más `limit` y `offset`. **No hay `total`, y no es un fallo**: `ProductListResult` (`src/catalog/catalog.types.ts`) nunca lo ha declarado y la SPA no lo consume — este caso lo exigió hasta v0.3.0 y produjo un hallazgo fantasma en cuatro validaciones seguidas. Si algún día hace falta paginar de verdad, será contrato nuevo y se pide entonces |
| A5 | Cada ítem de A4 | `id`, `name`, `retailer`, `priceFrom` > 0 e `imageUrl` no nula. Un catálogo sin fotos es **P0** |
| A6 | `GET /catalog/products?limit=1&offset=0` vs `offset=1` | ids distintos: la paginación pagina de verdad |
| A7 | `GET /catalog/products?limit=0` y `?limit=101` | **400** las dos (rango 1–100) |
| A8 | `GET /catalog/products?sort=inventado` | **400** |
| A9 | `GET /catalog/products?sort=precio-asc` | `priceFrom` no decreciente a lo largo de la página |
| A10 | `GET /catalog/products?sort=descuento` | `maxDiscount` no creciente |
| A11 | `GET /catalog/products?onlyDeals=true` | todos con `honesty = 'real'` y descuento > 0. El filtro va en SQL **antes** del LIMIT: una página con menos de 20 ítems teniendo más ofertas en la base delataría lo contrario |
| A12 | `GET /catalog/products?inStock=true` | todos con `anyInStock = true` |
| A13 | `GET /catalog/products?parametroInventado=1` | **400** (`forbidNonWhitelisted`). Que devuelva 200 ignorándolo es **P1**: los filtros mal escritos pasarían inadvertidos |
| A54 | **Tiempos.** `sin filtros` · `color=<familia>` · `retailer=<la tienda más grande>` · y las **dos combinadas**, cada una ×3 | ninguna por encima de **10 s** (**P0**) ni entre 3 y 10 s (**P1**) — ver «La latencia del catálogo» en `SKILL.md`. Va **en ventana tranquila**, con un `sin filtros` de control antes y después: si el control se mueve, la medida no vale. Con los otros frentes en marcha la lectura se dispara —en la validación de v0.3.0 salieron **45 s donde luego había 23 s**— y sobre eso se escribe un P0 falso |

## Las reglas que solo se ven desde fuera

| # | Petición | Se espera |
|---|---|---|
| A14 | `GET /catalog/products` (sin `barefoot`) | por defecto `barefoot=si`: **toda** la ropa más **solo** el calzado respetuoso. Contrasta contra D8: si aparece un producto de `section=zapateria` con `barefoot` distinto de `si`, es **P0** |
| A15 | `GET /catalog/products?barefoot=all&section=zapateria` | aparece calzado que A14 no traía. Si sale lo mismo, el filtro por defecto no está aplicándose |
| A16 | `GET /catalog/products?gender=niño` y `?gender=niña` | los productos `unisex` salen en **las dos**. Es igualdad de conjunto, no de etiqueta |
| A17 | `GET /catalog/facets` | `genders` **no** contiene `unisex` (es una regla de presentación: el usuario elige niño o niña) |
| A18 | `GET /catalog/facets?section=ropa` vs `?section=zapateria` | `sizes` distintos y sin mezclarse; `sections` y `genders` idénticos en ambas |
| A19 | `sizes` de A18 | ordenados por `size_sort()`, es decir 2, 4, 6, 8, 10 y no 10, 2, 4. Un orden alfabético es **P1** de UX |
| A20 | `colors` de A17 | ningún valor puramente numérico y ninguna cadena `"null"` |
| A21 | `GET /catalog/products?category=<uno de facets>` | ≥ 1 resultado. Una faceta que no devuelve nada es **P1**: se ofrece un filtro que vacía la pantalla |
| A22 | Ídem para un `size`, un `color` y un `retailer` de las facetas | ≥ 1 resultado cada uno |

## Búsqueda

| # | Petición | Se espera |
|---|---|---|
| A23 | `GET /catalog/products?q=pantalon` y `?q=pantalón` | mismo número de resultados: el plegado ignora acentos. **Aquí es donde muerde el ctype C del cluster**, así que este caso no es redundante con CI |
| A24 | `?q=PANTALON` | igual que `?q=pantalon` |
| A25 | `?q=pantalon+azul` | todas las palabras (AND), no cualquiera |
| A26 | `?q=%` y `?q=_` | 0 o pocos resultados, sin error: no se interpretan como comodines |
| A27 | `q` de 81 caracteres | **400** |

## Ficha, histórico y galería

| # | Petición | Se espera |
|---|---|---|
| A28 | `GET /catalog/products/<id de A4>` | 200 con `variants[]` no vacío e `images[]` |
| A29 | `GET /catalog/products/abc` | **400** (`ParseIntPipe`) |
| A30 | `GET /catalog/products/999999999` | **404** |
| A31 | `variants[]` de A28 | ninguna repetida por `(size, color, url)`: la ficha colapsa lo que la tienda duplica (#108). Contrasta con D6 |
| A32 | `GET /catalog/variants/<id>/price-history` | 200 y `scraped_at` **ascendente** |
| A33 | `GET /catalog/variants/999999999/price-history` | **404** |
| A34 | Un producto con varias referencias del mismo color | cada entrada de `images[]` trae su `color` y su `variantUrl`, y el `colorRepr` del listado se corresponde con la foto de `imageUrl` |
| A35 | Ficha de un calzado **no** barefoot (sácalo con `barefoot=all`) | 200: la ficha directa sí lo enseña, el filtro solo acota el catálogo. Un 404 aquí sería **P1** |

## Autenticación

Desde v0.3.0 este bloque es el que sostiene la promesa central de la versión: **sin cuenta no se ve
ni un producto ni una tienda** (#309). Los cuatro del catálogo se suman aquí a los seis de usuario.

| # | Petición | Se espera |
|---|---|---|
| A36 | Los **diez** endpoints protegidos **sin** `Authorization` — los seis de usuario (`/interests` ×3, `/settings/telegram` ×3) y los **cuatro del catálogo** (`/catalog/products`, `/catalog/products/:id`, `/catalog/variants/:id/price-history`, `/catalog/facets`) | **401** los diez. Un 500 significa que la estrategia JWT no se registró — **P0**. Un **200 en cualquiera de los cuatro del catálogo es P0 y hunde la versión**: es exactamente lo que #309 existe para impedir |
| A37 | Con `Authorization: Bearer basura` | **401**, no 500 |
| A38 | `GET /interests` con token válido | 200 y un array (vacío o no) |
| A52 | `GET /health` y `GET /config` **sin** `Authorization` | **200** los dos. Si el candado se los hubiera llevado por delante, la SPA no podría ni ofrecer login: la página de acceso quedaría muerta y nadie podría entrar — **P0** |
| A53 | `GET /catalog/products` **con** token válido | 200 y el mismo cuerpo que A4. Contrasta con A36: prueba que el candado deja pasar a quien tiene sesión, no que el catálogo esté roto |

## Intereses

Este bloque **escribe**. Reglas de convivencia: comparte el usuario `test-qa` con el frente de UI,
así que nunca afirmes sobre el total de la lista — solo sobre los ids que hayas creado tú — y
bórralos todos al terminar, incluso si el frente aborta a medias.

| # | Petición | Se espera |
|---|---|---|
| A39 | `POST /interests` con `{}` | **400**: exige al menos una señal (producto, variante, tienda o filtro) |
| A40 | `POST /interests` con un `color` sin canónica: **`"999"`** | **400**: suscribirse a un color que no canoniza equivale a suscribirse a todos. **Ojo al ejemplo, que hasta v0.3.0 estaba mal elegido**: `color_canon` solo devuelve NULL para cadenas **puramente numéricas** (migración `0016`, #51), así que un `"nosoyuncolor"` canoniza a sí mismo y responde 201 — la regla se cumple, pero ese valor no la ejercita. Si lo pruebas, bórralo |
| A41 | `POST /interests` con `minDiscountPct: 101` / `windowDays: 0` / `compareBase: "otra"` | **400** los tres |
| A42 | `POST /interests` con `{"variantId": <id>, "minDiscountPct": 20}` | 201 con id. Guárdalo |
| A43 | `GET /interests` | contiene el de A42, enriquecido con `retailerName`, `productName` y `variantLabel` (`"Talla 24 · rojo"`). Un `variantLabel` con la talla sin canonizar es **P1** |
| A44 | `POST /interests` con `size` y `color` en mayúsculas y con acentos | se guardan **canonicalizados** (compruébalo en `GET`), no como los mandaste |
| A45 | `DELETE /interests/<id de A42>` | **204**, y desaparece del `GET` |
| A46 | `DELETE /interests/999999999` | **404**, no 204: el borrado tiene ámbito de usuario |

## Telegram

| # | Petición | Se espera |
|---|---|---|
| A47 | `GET /settings/telegram` | 200 con `linked`, `telegramUsername`, `linkedAt`, `pendingLink` |
| A48 | `POST /settings/telegram/link` | **201** (no 200: el endpoint no anota `@HttpCode` y crear un token es una creación, igual que A42) con un deep-link `https://t.me/<bot>?start=<token>`. Un **503** significa que a QA le falta `TELEGRAM_BOT_USERNAME` — **P0**, porque nadie puede vincularse |
| A49 | `GET /settings/telegram` justo después | `pendingLink` a true |
| A50 | `POST /settings/telegram/link` otra vez | token **distinto**: re-pedir sobrescribe |
| A51 | `DELETE /settings/telegram` dos veces seguidas | **204** las dos: es idempotente |

⚠️ A51 **desvincula la cuenta de `test-qa`**. Si el checkpoint manual de Telegram va a ejercerse en
esta pasada, ejecuta A51 **antes** que el checkpoint, nunca después, o dejas la cuenta desvinculada
y el siguiente que valide se encontrará el aviso que no llega.

---

## Cómo reportar cada caso

Un caso fallado necesita las tres cosas o no es accionable: **el `curl` exacto**, **lo que
devolvió** (código y cuerpo recortado) y **lo que se esperaba, con el fichero del repo que lo
promete** — `catalog.service.ts`, el DTO correspondiente, `gender.sql.ts`. Un «falla el filtro de
género» sin eso no se puede arreglar ni reproducir.

Antes de escribir un P0, repite la petición una segunda vez. Un token caducado a mitad de tanda
produce una ristra de 401 que parece una regresión de autenticación y no lo es.
