"""Tests de parsing de Hipercor con fixtures reales (golden-file), sin red ni navegador.

Las fixtures son **marcado real recortado**: de cada página capturada el 02/08/2026 se conservó
literalmente lo que el scraper lee —el `<script>` del `dataLayer`, los bloques `ld+json` y los
enlaces a ficha— y se tiró el resto (medio megabyte de plantilla). Se capturaron con `/api`
**abortado en el navegador**, que es la comprobación que de verdad importa en esta tienda: su
`robots.txt` veta esa ruta, así que si algo del scraper dependiera de ella, la captura habría
salido vacía en vez de colarse sin que nadie lo notara.

Qué cubre cada una:

- `hipercor_rejilla_nina_vestidos.html` — hoja normal, con productos rebajados y sin rebajar.
- `hipercor_rejilla_ultima_pagina.html` — última página (7 de 12): fin de paginación normal.
- `hipercor_rejilla_fin_paginacion.html` — una página MÁS ALLÁ del final: 0 productos con
  `total_pages` intacto. Es lo que distingue "se acabó" de "esta hoja ha muerto".
- `hipercor_rejilla_espejismo.html` — ruta inventada: 200 con el catálogo del padre (#54). La
  trampa que hizo que seis rutas falsas parecieran vivas en el recon de #70.
- `hipercor_rejilla_zapatos_nina.html` — zapatería, que es de donde sale la clasificación barefoot.
- `hipercor_ficha_zapato.html` — ficha con tallas comprables: `ProductGroup` con `hasVariant`,
  12 tallas y stock mixto.
- `hipercor_ficha_agotada.html` — el **segundo esquema** de la tienda, que solo aparece cuando el
  producto se agota entero: sin `ProductGroup`, un `Product` suelto cuyo `sku` es el gtin, y las
  tallas únicamente en el selector. Está rebajada, así que cubre además el tachado.
- `hipercor_ficha_talla_unica.html` — el **tercer esquema**: producto de talla única
  (`group_by: "None"`), sin `ProductGroup` y sin selector. Su sku de variante solo está en el
  `dataLayer`; el del `ld+json` es el gtin.
- `hipercor_ficha_retirada.html` — id inventado: 404 sin `ld+json` de producto.

Las categorías se eligen **por atributos, no por índice**, para que reordenar `CATEGORIES` no
rompa los tests.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from scraper import progreso
from scraper.config import Config
from scraper.stores.base import DelistCandidate, ScrapeScope
from scraper.stores.browser import BrowserUnreachable
from scraper.stores.hipercor import (
    _MAX_FICHAS_FALLIDAS,
    BASE_URL,
    CATEGORIES,
    CategoryConfig,
    DetailUnavailable,
    HipercorStore,
    agrupa_por_talla,
    es_espejismo,
    extraer_data_layer,
    extraer_enlaces,
    extraer_ld_json,
    pagina_de,
    parse_listing,
    parse_pdp,
    product_group,
    product_signature,
    productos_de,
    ruta_resuelta,
    total_paginas,
)

FIXTURES = Path(__file__).parent / "fixtures"
_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0)


class _RelojQueAvanza:
    """Reloj que avanza solo al leerlo, para que una hoja «cueste» sin dormir de verdad.

    Aquí sirve la variante barata: lo que el test fija es que cada hoja cruza la ventana, no una
    duración concreta, y el latido lee el reloj una vez por hoja.
    """

    def __init__(self) -> None:
        self.ahora = 0.0
        self.por_lectura = 0.0

    def __call__(self) -> float:
        actual = self.ahora
        self.ahora += self.por_lectura
        return actual


_INFANTIL = "moda-y-accesorios/moda-infantil"
_VESTIDOS = CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/vestidos", "niña", "ropa", "vestidos")
_ZAPATOS = CategoryConfig(f"{_INFANTIL}/zapatos-infantiles/nina", "niña", "zapateria", "zapatos")


def load_html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


class SesionFalsa:
    """Doble de `BrowserSession`: sirve HTML por URL, sin navegador ni red.

    `respuestas` mapea URL -> (status, html), o -> una excepción a elevar, que es como se simula
    lo que `BrowserSession` hace cuando la navegación no llega a completarse. Una URL no
    registrada responde 404, que es lo que la tienda hace de verdad con una ficha retirada.

    **Distingue pedir de navegar**, porque desde #160 la tienda distingue las dos cosas y son la
    misma URL con distinto contenido: `pedir_html()` da el documento servido y `get_html()` el DOM
    ya hidratado. `renderizadas` sirve para registrar el segundo cuando el caso lo necesita —la
    ficha agotada, cuyas tallas solo las pinta el JS—; si una URL no está ahí, navegar devuelve lo
    mismo que pedir, que es lo que pasa en la inmensa mayoría de las páginas de esta tienda.

    `pedidas` conserva el nombre y el significado de siempre (todo lo que se ha ido a buscar, en
    orden) y `navegadas` es el subconjunto que ha necesitado navegador. Un test que quiera afirmar
    que NO se renderiza mira la segunda.
    """

    def __init__(
        self,
        respuestas: dict[str, tuple[int, str] | BaseException],
        renderizadas: dict[str, tuple[int, str] | BaseException] | None = None,
    ) -> None:
        self.respuestas = respuestas
        self.renderizadas = renderizadas or {}
        self.pedidas: list[str] = []
        self.navegadas: list[str] = []
        self.bloqueados: list[str] = []
        self.descartados: list[str] = []
        self.sembrada = False

    def __enter__(self) -> SesionFalsa:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def bloquear(self, patron: str) -> None:
        self.bloqueados.append(patron)

    def descartar_recursos(self, tipos: tuple[str, ...]) -> None:
        self.descartados.extend(tipos)

    def goto(self, url: str) -> int:
        self.navegadas.append(url)
        self.sembrada = True
        return 200

    def _responder(
        self, tabla: dict[str, tuple[int, str] | BaseException], url: str
    ) -> tuple[int, str]:
        self.pedidas.append(url)
        respuesta = tabla.get(url, (404, ""))
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta

    def pedir_html(self, url: str) -> tuple[int, str]:
        # La tienda contesta 403 a la ficha si no se han sembrado las cookies del origen. Que el
        # doble lo imite es lo que convierte ese fallo —que en el cluster aborta la pasada a la
        # sexta ficha— en un test que se cae aquí.
        if not self.sembrada:
            return 403, ""
        return self._responder(self.respuestas, url)

    def get_html(self, url: str, espera_selector: str | None = None) -> tuple[int, str]:
        self.navegadas.append(url)
        # Por URL y no por tabla: registrar la versión renderizada de UNA ficha no puede convertir
        # en 404 a todas las demás.
        tabla = self.renderizadas if url in self.renderizadas else self.respuestas
        return self._responder(tabla, url)


def _tienda(
    respuestas: dict[str, tuple[int, str] | BaseException],
    cats: list[CategoryConfig],
    renderizadas: dict[str, tuple[int, str] | BaseException] | None = None,
) -> HipercorStore:
    sesion = SesionFalsa(respuestas, renderizadas)
    return HipercorStore(_CFG, categories=cats, session_factory=lambda: sesion)


def _rehojar(html: str, category_path: str) -> str:
    """La misma rejilla publicada bajo otra hoja, reescribiendo la jerarquía de sus productos.

    No vale con servir la fixture tal cual bajo otra ruta: `es_espejismo()` compara la ruta pedida
    contra `products[].hierarchy` y la trataría —con razón— como hoja muerta. Esto es lo que la
    tienda hace de verdad con los 161 productos de la #98: los publica en las dos ramas, y cada
    rejilla los devuelve con la jerarquía de la suya.
    """
    actual = ruta_resuelta(extraer_data_layer(html) or {})
    assert actual is not None, "la fixture ha cambiado de forma: sin jerarquía de producto"
    original = '"hierarchy":' + json.dumps(actual, ensure_ascii=False).replace(", ", ",")
    nueva = '"hierarchy":' + json.dumps(category_path.split("/"), ensure_ascii=False).replace(
        ", ", ","
    )
    assert original in html, "la fixture ha cambiado de forma: revisa la jerarquía"
    return html.replace(original, nueva)


def _con_unisex(scope: ScrapeScope) -> set[ScrapeScope]:
    """El ámbito caído y su equivalente `unisex`, que es lo que hay que proteger junto a él (#98).

    Un producto que salía en las dos ramas de género deja de verse en las dos en cuanto cae una,
    así que se emitiría con el género de la superviviente; sin sacar también el `unisex` de las
    bajas, la hoja caída descatalogaría producto vivo.
    """
    return {scope, ScrapeScope("unisex", scope.section, scope.category)}


# --- extracción: el dato viaja dentro de la página, no en una API -----------------------------


def test_extraer_data_layer_de_marcado_real() -> None:
    dl = extraer_data_layer(load_html("hipercor_rejilla_nina_vestidos.html"))
    assert dl is not None
    assert len(productos_de(dl)) == 12, "el fixture ya no trae la página completa"
    assert total_paginas(dl) == 3
    assert pagina_de(dl)["total_products"] == 31


def test_extraer_data_layer_tolera_paginas_que_no_son_las_nuestras() -> None:
    # Una página sin dataLayer no es "catálogo vacío": es una página que no reconozco, y quien
    # llama tiene que poder distinguirlo para no disparar bajas masivas.
    assert extraer_data_layer("<html><body>hola</body></html>") is None
    assert extraer_data_layer("<script>dataLayer = [no-es-json];</script>") is None
    assert extraer_data_layer("<script>dataLayer = [];</script>") is None


def test_extraer_enlaces_da_la_url_de_ficha_de_cada_producto() -> None:
    html = load_html("hipercor_rejilla_nina_vestidos.html")
    enlaces = extraer_enlaces(html)
    ids = {p["code_a"] for p in productos_de(extraer_data_layer(html) or {})}
    assert ids <= set(enlaces), "todo producto de la rejilla necesita URL para pedir su ficha"
    for url in enlaces.values():
        assert url.startswith("https://www.hipercor.es/moda-y-accesorios/")


# --- huella y listado -------------------------------------------------------------------------


def test_signature_distingue_precio_tachado_y_disponibilidad() -> None:
    base = {"price": {"f_price": 21}, "status": "ADD"}
    rebajado = {"price": {"f_price": 14.7, "o_price": 21}, "status": "ADD"}
    agotado = {"price": {"f_price": 21}, "status": "SOLD_OUT"}
    assert product_signature(base) != product_signature(rebajado)
    assert product_signature(base) != product_signature(agotado)
    # La tienda escribe el estado en las dos cajas (`ADD` y `add` conviven en la misma rejilla):
    # si la huella no lo normalizara, medio catálogo pediría ficha en cada pasada sin haber
    # cambiado nada.
    assert product_signature(base) == product_signature({**base, "status": "add"})


def test_parse_listing_lleva_id_estable_y_ambito_de_la_hoja() -> None:
    dl = extraer_data_layer(load_html("hipercor_rejilla_nina_vestidos.html"))
    assert dl is not None
    entradas = parse_listing(dl, _VESTIDOS)
    assert len(entradas) == 12
    for entrada, crudo in entradas:
        assert entrada.retailer_product_id.startswith("A"), "el id estable es `code_a`"
        assert entrada.retailer_product_id == crudo["code_a"]
        assert entrada.scope == ScrapeScope("niña", "ropa", "vestidos")
        assert entrada.signature


def test_parse_listing_ignora_productos_sin_id() -> None:
    dl = {"products": [{"name": "sin code_a", "price": {"f_price": 3}}, {"code_a": "A1"}]}
    assert [e.retailer_product_id for e, _ in parse_listing(dl, _VESTIDOS)] == ["A1"]


# --- espejismo: la hoja muerta que responde 200 con el catálogo del padre (#54) ---------------


def test_ruta_resuelta_sale_en_slugs_y_no_en_etiquetas() -> None:
    dl = extraer_data_layer(load_html("hipercor_rejilla_nina_vestidos.html"))
    assert dl is not None
    # `page.hierarchy` trae las etiquetas localizadas ("Niña  4-16 años", con doble espacio);
    # `products[].hierarchy` trae los slugs, que es el mismo vocabulario que `category_path`.
    assert ruta_resuelta(dl) == [
        "moda-y-accesorios",
        "moda-infantil",
        "nina-4-16-anos",
        "vestidos",
    ]


def test_espejismo_se_detecta_y_la_hoja_viva_no_es_falso_positivo() -> None:
    viva = extraer_data_layer(load_html("hipercor_rejilla_nina_vestidos.html"))
    falsa = extraer_data_layer(load_html("hipercor_rejilla_espejismo.html"))
    assert viva is not None and falsa is not None
    assert es_espejismo(viva, _VESTIDOS.category_path) is False
    # La ruta pedida tenía cuatro segmentos y la tienda ha resuelto tres: el catálogo del padre.
    ruta_falsa = f"{_INFANTIL}/nina-4-16-anos/no-existe-abc"
    assert es_espejismo(falsa, ruta_falsa) is True
    assert ruta_resuelta(falsa) == ["moda-y-accesorios", "moda-infantil", "nina-4-16-anos"]


def test_espejismo_admite_que_una_rejilla_liste_sus_subcategorias() -> None:
    # La comprobación es de PREFIJO, no de igualdad: pedir `zapatos-infantiles` y recibir
    # productos de `zapatos-infantiles/nina/sandalias` es correcto, no un espejismo.
    dl = {"products": [{"hierarchy": ["a", "b", "c", "d"]}]}
    assert es_espejismo(dl, "a/b") is False
    assert es_espejismo(dl, "a/x") is True


def test_espejismo_sin_productos_cae_al_largo_de_la_jerarquia_de_pagina() -> None:
    # Una página vacía no trae `products[].hierarchy`, pero la tienda sigue rellenando `page`.
    dl = extraer_data_layer(load_html("hipercor_rejilla_fin_paginacion.html"))
    assert dl is not None and productos_de(dl) == []
    assert es_espejismo(dl, _VESTIDOS.category_path) is False
    assert es_espejismo(dl, f"{_VESTIDOS.category_path}/inventada") is True


# --- ficha: tallas, stock, precio tachado, color y foto ---------------------------------------


def test_parse_pdp_extrae_tallas_con_su_stock() -> None:
    producto = parse_pdp(load_html("hipercor_ficha_zapato.html"), _ZAPATOS)
    assert producto is not None
    assert producto.retailer_product_id == "A56615356"
    assert len(producto.variants) == 12, "el fixture ya no trae las 12 tallas"
    tallas = [v.size for v in producto.variants]
    assert tallas[:3] == ["28", "29", "30"]
    # El stock es POR TALLA: es el dato que el brief pide seguir y el que la rejilla no da.
    assert [v.in_stock for v in producto.variants].count(True) == 4
    assert all(v.retailer_variant_id.startswith("00108") for v in producto.variants)
    assert len({v.retailer_variant_id for v in producto.variants}) == 12


def test_parse_pdp_toma_el_tachado_del_datalayer_que_es_donde_esta() -> None:
    producto = parse_pdp(load_html("hipercor_ficha_agotada.html"), _VESTIDOS)
    assert producto is not None
    assert {v.price for v in producto.variants} == {Decimal("14.70")}
    # `ld+json` no expresa el precio anterior en la forma normal; sin leer el `dataLayer` este
    # producto entraría como si 14,70 € fuera su precio de siempre, y el detector de descuentos
    # perdería la referencia que la tienda sí publica.
    assert {v.list_price for v in producto.variants} == {Decimal("21.00")}


def test_parse_pdp_de_ficha_agotada_conserva_las_tallas_y_las_marca_sin_stock() -> None:
    """El esquema que solo se ve cuando el producto se agota del todo.

    La tienda deja de publicar `ProductGroup` y emite un `Product` suelto cuyo `sku` es el
    **gtin**, no nuestro id de variante. Si esta forma se tratara como ficha ilegible, el
    producto se quedaría con el stock de la última pasada: enseñando tallas disponibles de algo
    que ya no se puede comprar.
    """
    producto = parse_pdp(load_html("hipercor_ficha_agotada.html"), _VESTIDOS)
    assert producto is not None
    assert producto.retailer_product_id == "A56369559", "el id sale del dataLayer, no del ld+json"
    assert [v.size for v in producto.variants][:3] == ["4 años", "5 años", "6 años"]
    assert all(not v.in_stock for v in producto.variants)
    # Los ids de variante son los del selector (los mismos que en la forma con ProductGroup),
    # no el gtin: si se colara el gtin, cada agotamiento crearía una variante fantasma nueva.
    assert all(v.retailer_variant_id.startswith("001003688103732") for v in producto.variants)
    assert "2102665336444" not in {v.retailer_variant_id for v in producto.variants}
    # La galería de esta forma vive en `subjectOf.ImageGallery`, no en las variantes.
    assert len(producto.images) == 3


def test_parse_pdp_no_inventa_descuentos() -> None:
    producto = parse_pdp(load_html("hipercor_ficha_zapato.html"), _ZAPATOS)
    assert producto is not None
    # Sin rebaja no hay `o_price`. Un tachado igual al precio (la mentira que Cacles traía en
    # 248 de 428) tampoco debe registrarse: sería un descuento del 0 % inventado por nosotros.
    assert all(v.list_price is None for v in producto.variants)


def test_parse_pdp_empareja_foto_y_variante_por_el_mismo_color() -> None:
    producto = parse_pdp(load_html("hipercor_ficha_zapato.html"), _ZAPATOS)
    assert producto is not None
    colores_variante = {v.color for v in producto.variants}
    colores_foto = {i.color for i in producto.images}
    assert colores_variante == colores_foto == {"250 Nude"}
    assert producto.images, "sin fotos la ficha pinta el placeholder"
    assert producto.image_url == producto.images[0].url
    assert all(i.url.startswith("https://") for i in producto.images)


def test_parse_pdp_clasifica_barefoot_por_el_nombre_y_solo_en_zapateria() -> None:
    zapato = parse_pdp(load_html("hipercor_ficha_zapato.html"), _ZAPATOS)
    vestido = parse_pdp(load_html("hipercor_ficha_agotada.html"), _VESTIDOS)
    assert zapato is not None and vestido is not None
    # Hipercor no etiqueta el calzado respetuoso: lo que no se puede afirmar queda en
    # `desconocido`, que es un estado y no una carencia que haya que tapar con un `si`.
    assert zapato.barefoot == "desconocido"
    assert vestido.barefoot is None, "en ropa la pregunta no aplica"


def test_parse_pdp_pliega_la_talla_escrita_dos_veces() -> None:
    """Visto en una pasada real: `"11-12 años/11 - 12 Años"` en el mismo campo.

    Son la misma talla con otra caja y otros espacios. Sin plegarla, el chip del filtro enseña
    las dos y `size_canon` recibe una forma que no existe en ninguna otra tienda.
    """
    import json

    grupo = {
        "@type": "ProductGroup",
        "name": "Prenda",
        "productGroupID": "A1",
        "color": "Azul",
        "hasVariant": [
            {
                "sku": "s1",
                "size": "11-12 años/11 - 12 Años",
                "offers": {"price": 10, "availability": "InStock"},
            }
        ],
    }
    producto = parse_pdp(
        f'<script type="application/ld+json">{json.dumps(grupo)}</script>', _VESTIDOS
    )
    assert producto is not None
    assert [v.size for v in producto.variants] == ["11-12 años"]


def test_parse_pdp_de_talla_unica_no_se_queda_fuera_del_catalogo() -> None:
    """Patucos de recién nacido: ni `ProductGroup` ni selector, porque no hay nada que elegir.

    Medido en una pasada real: eran 12 de los 289 zapatos, y sin este caso desaparecían del
    catálogo en silencio (el listado los ve, la ficha no producía producto). El sku sale del
    `dataLayer`; usar el del `ld+json` metería el gtin como id de variante.
    """
    producto = parse_pdp(load_html("hipercor_ficha_talla_unica.html"), _ZAPATOS)
    assert producto is not None
    assert producto.retailer_product_id == "A57503828"
    assert len(producto.variants) == 1
    variante = producto.variants[0]
    assert variante.size is None, "talla única: no inventamos una talla que la tienda no da"
    assert variante.retailer_variant_id == "001003671000994666"
    assert variante.retailer_variant_id != "2102676556114", "eso es el gtin, no el sku"
    assert variante.in_stock and variante.price == Decimal("10.00")


def test_parse_pdp_devuelve_none_ante_una_ficha_retirada() -> None:
    # 404 con `dataLayer` de error y sin `ld+json` de producto: no hay nada que ingerir.
    html = load_html("hipercor_ficha_retirada.html")
    assert product_group(extraer_ld_json(html)) is None
    assert parse_pdp(html, _VESTIDOS) is None


def test_parse_pdp_descarta_variantes_sin_precio_o_sin_sku() -> None:
    grupo: dict[str, Any] = {
        "@type": "ProductGroup",
        "name": "Prenda",
        "productGroupID": "A1",
        "color": "Azul",
        "hasVariant": [
            {"sku": "s1", "size": "4 años", "offers": {"price": 10, "availability": "InStock"}},
            {"size": "5 años", "offers": {"price": 10}},  # sin sku
            {"sku": "s3", "size": "6 años"},  # sin precio
        ],
    }
    import json

    html = f'<script type="application/ld+json">{json.dumps(grupo)}</script>'
    producto = parse_pdp(html, _VESTIDOS)
    assert producto is not None
    assert [v.retailer_variant_id for v in producto.variants] == ["s1"]


# --- recorrido completo -----------------------------------------------------------------------


def _respuestas_de_hoja(
    cat: CategoryConfig, paginas: list[str]
) -> dict[str, tuple[int, str] | BaseException]:
    return {
        HipercorStore.grid_url(cat.category_path, i + 1): (200, load_html(nombre))
        for i, nombre in enumerate(paginas)
    }


def test_list_catalog_pagina_hasta_el_final_y_deduplica() -> None:
    cat = _VESTIDOS
    respuestas = _respuestas_de_hoja(
        cat,
        [
            "hipercor_rejilla_nina_vestidos.html",
            "hipercor_rejilla_nina_vestidos.html",  # misma página repetida: prueba el dedup
            "hipercor_rejilla_ultima_pagina.html",
        ],
    )
    tienda = _tienda(respuestas, [cat])
    entradas = list(tienda.list_catalog())
    ids = [e.retailer_product_id for e in entradas]
    assert len(ids) == len(set(ids)), "gana la primera: un id no puede entrar dos veces"
    # 12 de la primera página + los de la última que no estuvieran ya. No se fija el número
    # exacto: la tienda reordena su rejilla entre peticiones y algún id se repite entre páginas,
    # que es justo el motivo por el que el dedup existe.
    assert 12 < len(ids) <= 12 + 7
    assert tienda.scan_report().leaves_failed == 0


# El cruce de géneros (#98): publicado en las dos ramas = unisex


def test_el_mismo_producto_en_dos_hojas_de_genero_distinto_se_emite_una_vez_y_unisex() -> None:
    """El caso de la #98 de punta a punta: 161 productos (13 %) de esta tienda están así.

    La misma rejilla servida bajo la rama de niña y bajo la de niño: la tienda está diciendo que
    esos productos son de los dos, y hasta ahora se ingerían como `niña` porque su hoja iba antes.
    """
    nina = _VESTIDOS
    nino = CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/camisetas", "niño", "ropa", "camisetas")
    html = load_html("hipercor_rejilla_nina_vestidos.html")
    tienda = _tienda(
        {
            HipercorStore.grid_url(nina.category_path, 1): (200, html),
            HipercorStore.grid_url(nino.category_path, 1): (
                200,
                _rehojar(html, nino.category_path),
            ),
        },
        [nina, nino],
    )

    entradas = list(tienda.list_catalog())

    ids = [e.retailer_product_id for e in entradas]
    assert len(ids) == len(set(ids)), "un producto en dos hojas se emite UNA vez"
    assert {e.gender for e in entradas} == {"unisex"}
    # Sección y categoría siguen saliendo de la primera hoja: cruzar géneros tiene vocabulario
    # (`unisex`), cruzar categorías no lo tiene.
    assert {(e.section, e.category) for e in entradas} == {("ropa", "vestidos")}


def test_un_producto_de_una_hoja_con_genero_y_la_de_bebe_conserva_su_genero() -> None:
    """El caso propio de Hipercor, y el que hay que no romper.

    `zapatos-infantiles/{nina,nino}` van antes que `zapatos-infantiles/bebe` —que ya está
    declarada `unisex`— precisamente para que lo que tenga género declarado se lo quede. Una hoja
    `unisex` no cuenta como un género distinto, así que el cruce no se dispara aquí.
    """
    bebe = CategoryConfig(f"{_INFANTIL}/zapatos-infantiles/bebe", "unisex", "zapateria", "zapatos")
    html = load_html("hipercor_rejilla_zapatos_nina.html")
    tienda = _tienda(
        {
            HipercorStore.grid_url(_ZAPATOS.category_path, 1): (200, html),
            HipercorStore.grid_url(bebe.category_path, 1): (
                200,
                _rehojar(html, bebe.category_path),
            ),
        },
        [_ZAPATOS, bebe],
    )

    entradas = list(tienda.list_catalog())

    assert entradas
    assert {e.gender for e in entradas} == {"niña"}


def test_scopes_declara_tambien_los_ambitos_unisex_que_el_parser_puede_emitir() -> None:
    """Un ámbito no declarado no cuenta como escaneado, y sus productos no se dan de baja NUNCA.

    Es el mismo motivo por el que `cacles.py` declara el producto cartesiano de lo que su parser
    puede emitir en vez de lo que dicen sus hojas.
    """
    scopes = list(HipercorStore(_CFG).scopes())

    assert len(scopes) == len(set(scopes)), "sin duplicados"
    for cat in CATEGORIES:
        assert ScrapeScope(cat.gender, cat.section, cat.category) in scopes
        assert ScrapeScope("unisex", cat.section, cat.category) in scopes


def test_list_catalog_cuenta_el_espejismo_como_hoja_caida() -> None:
    cat = CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/no-existe-abc", "niña", "ropa", "vestidos")
    tienda = _tienda(
        {
            HipercorStore.grid_url(cat.category_path, 1): (
                200,
                load_html("hipercor_rejilla_espejismo.html"),
            )
        },
        [cat],
    )
    assert list(tienda.list_catalog()) == [], (
        "ingerir el catálogo del padre sería peor que no ingerir"
    )
    informe = tienda.scan_report()
    assert informe.leaves_failed == 1
    assert informe.failed_scopes == _con_unisex(ScrapeScope("niña", "ropa", "vestidos"))
    # Con su ruta (#155). El ámbito no la identifica: a `niña/ropa/vestidos` lo alimenta más de
    # una hoja, así que sin esto la siguiente sesión tiene que volver a sondear la tienda entera.
    assert informe.failed_leaves == [cat.category_path]


def test_list_catalog_no_da_por_vacio_lo_que_no_ha_podido_leer() -> None:
    # Una hoja que responde 403 (bloqueo) o con una plantilla desconocida NO es un ámbito
    # vaciado: su ámbito queda fuera de las bajas en vez de descatalogar producto vivo.
    for respuesta in ((403, "<html></html>"), (200, "<html>otra plantilla</html>")):
        tienda = _tienda(
            {HipercorStore.grid_url(_VESTIDOS.category_path, 1): respuesta}, [_VESTIDOS]
        )
        assert list(tienda.list_catalog()) == []
        assert tienda.scan_report().failed_scopes == _con_unisex(
            ScrapeScope("niña", "ropa", "vestidos")
        )


def test_list_catalog_sobrevive_a_un_timeout_de_navegacion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#107: la hoja 32 tardó y se llevó por delante las 31 ya leídas. Nunca más.

    `BrowserUnreachable` no la conoce `_iter_category` —solo convierte `LeafGone` y
    `LeafUnreadable`—, así que subía hasta `ingest` y abortaba la pasada entera. Ahora la hoja
    cuenta como caída, que es lo que de verdad ha pasado: no la hemos podido ver.
    """
    respuestas = _respuestas_de_hoja(
        _VESTIDOS,
        [
            "hipercor_rejilla_nina_vestidos.html",
            "hipercor_rejilla_nina_vestidos.html",
            "hipercor_rejilla_ultima_pagina.html",
        ],
    )
    url_caida = HipercorStore.grid_url(_ZAPATOS.category_path, 1)
    respuestas[url_caida] = BrowserUnreachable(
        url_caida, TimeoutError("Page.goto: Timeout 45000ms exceeded")
    )
    tienda = _tienda(respuestas, [_VESTIDOS, _ZAPATOS])

    with caplog.at_level(logging.WARNING):
        entradas = list(tienda.list_catalog())
    informe = tienda.scan_report()

    assert entradas, "la hoja que sí respondió tiene que ingerirse igual"
    assert {e.section for e in entradas} == {"ropa"}
    assert (informe.leaves_total, informe.leaves_failed) == (2, 1)
    # Fuera de las bajas: lo que no se ha visto en esa hoja no está retirado.
    assert informe.failed_scopes == _con_unisex(ScrapeScope("niña", "zapateria", "zapatos"))
    assert "zapatos" in caplog.text, "perder una hoja no puede ser silencioso"


def test_fetch_details_aguanta_timeouts_sueltos_y_aborta_ante_una_racha() -> None:
    """El contador es de fichas SEGUIDAS, y la diferencia no es cosmética (#107).

    Una pasada en frío son 1.224 navegaciones en 3 h 27 min: a esa escala unos cuantos timeouts
    dispersos son ruido normal, y contarlos acumulados tiraba una pasada entera que iba bien. Una
    racha, en cambio, es la tienda cerrándonos la puerta, y esa sí tiene que abortar.
    """
    base = _respuestas_de_hoja(_ZAPATOS, ["hipercor_rejilla_zapatos_nina.html"])
    tienda = _tienda(dict(base), [_ZAPATOS])
    entradas = list(tienda.list_catalog())
    urls = [
        tienda._urls[e.retailer_product_id]
        for e in entradas
        if e.retailer_product_id in tienda._urls
    ]
    ficha = load_html("hipercor_ficha_zapato.html")
    assert len(urls) >= 2 * _MAX_FICHAS_FALLIDAS + 1, "el fixture ya no trae fichas para este caso"

    def _timeout(url: str) -> BrowserUnreachable:
        return BrowserUnreachable(url, TimeoutError("Page.goto: Timeout 45000ms exceeded"))

    # Alternando desde la primera: nunca dos seguidos, pero MÁS en total que el tope. Con el
    # contador acumulado de antes esto abortaba; es exactamente el caso que #107 quiere permitir.
    alternas = dict(base)
    for i, url in enumerate(urls):
        alternas[url] = _timeout(url) if i % 2 == 0 else (200, ficha)
    dispersos = sum(1 for i in range(len(urls)) if i % 2 == 0)
    assert dispersos > _MAX_FICHAS_FALLIDAS, "sin pasar del tope el caso no probaría nada"
    intercalada = _tienda(alternas, [_ZAPATOS])
    productos = list(intercalada.fetch_details(list(intercalada.list_catalog())))
    assert productos, "unos timeouts dispersos no pueden costar la pasada"

    # Seguidos: la puerta está cerrada y guardar este catálogo sería guardar uno mutilado.
    seguidas = dict(base)
    for url in urls[: _MAX_FICHAS_FALLIDAS + 1]:
        seguidas[url] = _timeout(url)
    for url in urls[_MAX_FICHAS_FALLIDAS + 1 :]:
        seguidas[url] = (200, ficha)
    racha = _tienda(seguidas, [_ZAPATOS])
    with pytest.raises(DetailUnavailable):
        list(racha.fetch_details(list(racha.list_catalog())))


def test_fetch_details_pide_la_ficha_de_lo_que_salio_en_el_listado() -> None:
    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    ficha = load_html("hipercor_ficha_zapato.html")
    url_ficha = (
        "https://www.hipercor.es/moda-y-accesorios/A56615356-sandalia-infantil-bio-cruzada-de-piel/"
    )
    respuestas[url_ficha] = (200, ficha)
    tienda = _tienda(respuestas, [cat])

    entradas = list(tienda.list_catalog())
    objetivo = [e for e in entradas if e.retailer_product_id == "A56615356"]
    assert objetivo, "el fixture de zapatería ya no trae la sandalia"
    productos = list(tienda.fetch_details(objetivo))
    assert [p.retailer_product_id for p in productos] == ["A56615356"]
    assert productos[0].section == "zapateria" and productos[0].gender == "niña"
    # El detalle cuesta UNA petición por producto: es lo que hace imprescindible la huella.
    assert url_ficha in tienda._urls.values()


def test_fetch_details_se_salta_lo_que_ya_no_esta() -> None:
    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    tienda = _tienda(respuestas, [cat])
    entradas = list(tienda.list_catalog())
    # Ninguna ficha registrada -> la sesión falsa responde 404 a todas: 0 productos, sin reventar.
    assert list(tienda.fetch_details(entradas)) == []


# --- pedir en vez de navegar (#160) -----------------------------------------------------------


def _servido(html: str) -> str:
    """El mismo marcado tal y como lo sirve el servidor: sin el selector que pinta el JS.

    Medido en vivo el 04/08/2026: el documento de una ficha trae su `ld+json` y su `dataLayer`,
    pero **no** los `size_option_`. Es la diferencia que decide si `pedir_html()` basta.
    """
    return re.sub(r'id="size_option_[^"]*"', 'id="ya_no_es_el_selector"', html)


def test_la_ficha_normal_no_se_renderiza() -> None:
    """El caso común: el documento servido ya trae las tallas, así que no se navega.

    Es el ahorro entero de #160 —1,14-1,41 s de navegación por ficha frente a 0,08-0,42 s de
    petición—, y lo que hace que la pasada en frío quepa en su deadline. Se afirma sobre
    `navegadas` porque es lo único que distingue las dos rutas: las dos devuelven el mismo dato.
    """
    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    url_ficha = (
        "https://www.hipercor.es/moda-y-accesorios/A56615356-sandalia-infantil-bio-cruzada-de-piel/"
    )
    respuestas[url_ficha] = (200, _servido(load_html("hipercor_ficha_zapato.html")))
    tienda = _tienda(respuestas, [cat])
    sesion = tienda._session_factory()

    entradas = [e for e in tienda.list_catalog() if e.retailer_product_id == "A56615356"]
    productos = list(tienda.fetch_details(entradas))

    assert [p.retailer_product_id for p in productos] == ["A56615356"]
    assert len(productos[0].variants) == 12, "sin renderizar se leen las mismas tallas"
    assert url_ficha in sesion.pedidas
    assert url_ficha not in sesion.navegadas, "la ficha normal no necesita navegador"


def test_la_ficha_agotada_cae_al_respaldo_y_no_pierde_sus_tallas() -> None:
    """El caso en que lo servido NO basta: agotada del todo, las tallas solo las pinta el JS.

    Sin el respaldo esto no sería un ahorro sino una pérdida de dato: el producto entraría con una
    sola variante sin talla en vez de con sus ocho, y el catálogo seguiría ofreciendo tallas de
    algo que ya no se puede comprar. La primera afirmación mide justo eso —lo que daría el
    documento servido a solas— para que el test explique por qué existe el respaldo.
    """
    agotada = load_html("hipercor_ficha_agotada.html")
    cat = _VESTIDOS
    url = "https://www.hipercor.es/moda-y-accesorios/A56369559-vestido-nina-schiffli/"

    a_pelo = parse_pdp(_servido(agotada), cat, url)
    assert a_pelo is not None and [v.size for v in a_pelo.variants] == [None], (
        "si lo servido bastara, este respaldo sobraría"
    )

    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_nina_vestidos.html"])
    respuestas[url] = (200, _servido(agotada))
    tienda = _tienda(respuestas, [cat], renderizadas={url: (200, agotada)})
    sesion = tienda._session_factory()

    entrada = next(e for e in tienda.list_catalog() if e.retailer_product_id == "A56369559")
    producto = next(iter(tienda.fetch_details([entrada])))

    assert len(producto.variants) == 8, "el respaldo recupera las tallas del selector"
    assert all(v.size is not None for v in producto.variants)
    assert url in sesion.navegadas, "una ficha agotada sí necesita navegador"


def test_la_ficha_de_talla_unica_no_paga_el_respaldo() -> None:
    """Tampoco trae `ProductGroup`, pero renderizarla no añadiría nada y cuesta el doble.

    Su selector no existe —`group_by: "None"`—, así que navegar significaría esperar el
    `browser_hydrate_timeout` **entero** por cada uno de estos productos para acabar leyendo el
    mismo `dataLayer` que ya estaba servido. Distinguirla de la agotada es lo que evita cambiar un
    cuello de botella por otro.
    """
    unica = load_html("hipercor_ficha_talla_unica.html")
    assert not agrupa_por_talla(unica), "la fixture ya no es de talla única"
    assert agrupa_por_talla(load_html("hipercor_ficha_agotada.html")), (
        "la agotada sí agrupa por talla: es lo que la separa de esta"
    )

    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    tienda = _tienda(respuestas, [cat])
    entradas = list(tienda.list_catalog())
    pid = next(iter(tienda._urls))
    url = tienda._urls[pid]
    respuestas[url] = (200, _servido(unica))
    sesion = tienda._session_factory()

    list(tienda.fetch_details([e for e in entradas if e.retailer_product_id == pid]))
    assert url not in sesion.navegadas, "la talla única se resuelve con lo servido"


def test_se_siembran_las_cookies_antes_de_pedir_la_primera_ficha() -> None:
    """Sin cookies del origen, la tienda contesta 403 a la ficha aunque la rejilla sí entre.

    Medido en vivo: bote vacío -> ficha 403, rejilla 200. Como `fetch_details` abre su propia
    sesión y desde #160 ya no navega a ningún sitio, sin la siembra explícita de `_preparar` la
    pasada moriría por `DetailUnavailable` a la sexta ficha, con un error que parece un bloqueo de
    Akamai y no un fallo nuestro.
    """
    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    url_ficha = (
        "https://www.hipercor.es/moda-y-accesorios/A56615356-sandalia-infantil-bio-cruzada-de-piel/"
    )
    respuestas[url_ficha] = (200, _servido(load_html("hipercor_ficha_zapato.html")))
    tienda = _tienda(respuestas, [cat])
    sesion = tienda._session_factory()

    entradas = [e for e in tienda.list_catalog() if e.retailer_product_id == "A56615356"]
    assert sesion.sembrada, "la siembra va al abrir la sesión, antes de pedir nada"
    assert list(tienda.fetch_details(entradas)), "con la siembra hecha, la ficha entra"


def test_la_rejilla_tampoco_se_renderiza() -> None:
    """La fase de listado eran 30 minutos y también es documento servido (0,21 s vs 1,34 s)."""
    cat = _VESTIDOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_nina_vestidos.html"])
    tienda = _tienda(respuestas, [cat])
    sesion = tienda._session_factory()

    assert list(tienda.list_catalog()), "el listado sigue emitiendo entradas"
    rejilla = HipercorStore.grid_url(cat.category_path, 1)
    assert rejilla in sesion.pedidas
    assert sesion.navegadas == [BASE_URL], "solo la siembra; ninguna rejilla se navega"


# --- vigía y bajas ----------------------------------------------------------------------------


def test_fetch_details_distingue_retirado_de_bloqueo() -> None:
    """Un 404 es una baja; un 403 es que no nos dejan mirar, y confundirlos descataloga vivos.

    El producto sigue saliendo en el listado, así que las redes de `ingest.py` no ven caer nada:
    simplemente su ficha no llega, no se le toca `last_seen_at` y a las dos pasadas cae por
    histéresis. Por eso un bloqueo repetido tiene que abortar la pasada, no ir en silencio.
    """
    cat = _ZAPATOS
    respuestas = _respuestas_de_hoja(cat, ["hipercor_rejilla_zapatos_nina.html"])
    tienda = _tienda(respuestas, [cat])
    entradas = list(tienda.list_catalog())

    # 404: se salta sin ruido (la sesión falsa responde 404 a lo no registrado).
    assert list(tienda.fetch_details(entradas[:1])) == []

    # 403 repetido: la pasada se aborta en vez de guardar un catálogo mutilado.
    bloqueadas = dict(respuestas)
    for url in tienda._urls.values():
        bloqueadas[url] = (403, "")
    bloqueada = _tienda(bloqueadas, [cat])
    entradas = list(bloqueada.list_catalog())
    with pytest.raises(DetailUnavailable):
        list(bloqueada.fetch_details(entradas))


def test_list_catalog_no_se_traga_una_rejilla_sin_enlaces_a_ficha() -> None:
    """Productos sí, enlaces no: han cambiado la forma de las URLs.

    Sin URL no se puede pedir el detalle de NINGUNO, y esos productos se quedarían sin refrescar
    hasta caer por histéresis. Es fallo nuestro de parseo, así que la hoja se cuenta como no
    leída (su ámbito sale de las bajas) en vez de ingerirse a medias.
    """
    html = load_html("hipercor_rejilla_nina_vestidos.html")
    sin_enlaces = html[: html.index("</script>") + len("</script>")] + "</body></html>"
    tienda = _tienda(
        {HipercorStore.grid_url(_VESTIDOS.category_path, 1): (200, sin_enlaces)}, [_VESTIDOS]
    )
    assert list(tienda.list_catalog()) == []
    assert tienda.scan_report().failed_scopes == _con_unisex(
        ScrapeScope("niña", "ropa", "vestidos")
    )


def test_el_id_de_producto_lo_manda_el_datalayer() -> None:
    """`code_a` gana a `productGroupID`, que es el respaldo.

    Los dos campos los genera la plantilla por su cuenta. Si divergieran y mandara el del
    `ld+json`, el producto entraría como uno nuevo —sin huella, pidiendo ficha cada día— mientras
    el viejo dejaba de verse hasta que la histéresis lo descatalogara, partiendo su histórico.
    """
    import json

    grupo = {
        "@type": "ProductGroup",
        "name": "Prenda",
        "productGroupID": "A-DEL-LDJSON",
        "hasVariant": [{"sku": "s1", "size": "4 años", "offers": {"price": 10}}],
    }
    html = (
        '<script>dataLayer = [{"product":{"code_a":"A-DEL-DATALAYER","name":"Prenda"}}];</script>'
        f'<script type="application/ld+json">{json.dumps(grupo)}</script>'
    )
    producto = parse_pdp(html, _VESTIDOS)
    assert producto is not None
    assert producto.retailer_product_id == "A-DEL-DATALAYER"


def test_check_leaves_distingue_viva_de_espejismo_de_bloqueo() -> None:
    viva = CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/vestidos", "niña", "ropa", "vestidos")
    falsa = CategoryConfig(f"{_INFANTIL}/nina-4-16-anos/no-existe-abc", "niña", "ropa", "vestidos")
    bloqueada = CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/camisetas", "niño", "ropa", "camisetas")
    respuestas = {
        HipercorStore.grid_url(viva.category_path, 1): (
            200,
            load_html("hipercor_rejilla_nina_vestidos.html"),
        ),
        HipercorStore.grid_url(falsa.category_path, 1): (
            200,
            load_html("hipercor_rejilla_espejismo.html"),
        ),
        HipercorStore.grid_url(bloqueada.category_path, 1): (403, ""),
    }
    salud = list(_tienda(respuestas, [viva, falsa, bloqueada]).check_leaves())
    assert [s.alive for s in salud] == [True, False, None]
    assert "12 productos" in salud[0].detail
    assert "espejismo" in salud[1].detail
    # El 403 de Akamai es problema nuestro, no de la hoja: avisa, no dictamina retirada.
    assert "403" in salud[2].detail


def test_toda_hoja_configurada_declara_ambito_del_brief() -> None:
    categorias = {c.category for c in CATEGORIES}
    assert {"pantalones", "camisetas", "sudaderas", "vestidos", "ropa-interior"} <= categorias
    assert {c.section for c in CATEGORIES} == {"ropa", "zapateria"}
    assert {c.gender for c in CATEGORIES} == {"niña", "niño", "unisex"}
    # `vestidos` solo en niña: es lo que publica la tienda, no una decisión nuestra.
    assert {c.gender for c in CATEGORIES if c.category == "vestidos"} == {"niña"}
    # Las rutas no se repiten (una duplicada sería una hoja pedida dos veces por pasada).
    rutas = [c.category_path for c in CATEGORIES]
    assert len(rutas) == len(set(rutas))


def test_probe_alive_solo_sentencia_lo_que_la_tienda_confirma() -> None:
    vivo = DelistCandidate("A56615356", "https://www.hipercor.es/moda-y-accesorios/A56615356-x/")
    muerto = DelistCandidate("A99999999", "https://www.hipercor.es/moda-y-accesorios/A99999999-x/")
    bloqueado = DelistCandidate(
        "A55555555", "https://www.hipercor.es/moda-y-accesorios/A55555555-x/"
    )
    sin_url = DelistCandidate("A44444444", None)
    respuestas = {
        vivo.url or "": (200, load_html("hipercor_ficha_zapato.html")),
        muerto.url or "": (404, load_html("hipercor_ficha_retirada.html")),
        bloqueado.url or "": (403, ""),
    }
    veredictos = _tienda(respuestas, []).probe_alive([vivo, muerto, bloqueado, sin_url])
    assert veredictos == {"A56615356": True, "A99999999": False}
    # Ausente del mapa = no concluyente. Devolver False ante un 403 daría bajas masivas falsas.
    assert "A55555555" not in veredictos and "A44444444" not in veredictos


@pytest.mark.parametrize("pagina,esperado", [(1, ""), (2, "2/"), (7, "7/")])
def test_grid_url_pagina_uno_no_lleva_numero(pagina: int, esperado: str) -> None:
    url = HipercorStore.grid_url("moda-y-accesorios/moda-infantil/nina-4-16-anos/vestidos", pagina)
    assert url == (
        "https://www.hipercor.es/moda-y-accesorios/moda-infantil/nina-4-16-anos/vestidos/"
        + esperado
    )


# El latido del listado (#146): esta tienda acumula, así que `ingest.py` no la puede ver


def test_el_listado_late_por_hoja_diciendo_en_cual_va(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """La fase 1 de esta tienda salía muda, y no un rato: 45+ min medidos en dev el 04/08/2026.

    `ingest.py` late al recibir entradas del generador, y `list_catalog()` acumula la pasada entera
    antes de emitir ninguna (lo exige el cruce de géneros de #98). O sea que para la tienda que
    motivó #146 —la de la pasada en frío de 5 horas— el instrumento no llegaba justo donde hacía
    falta. El latido tiene que salir de aquí dentro, que es el único sitio donde se sabe por qué
    hoja va.
    """
    reloj = _RelojQueAvanza()
    monkeypatch.setattr(progreso, "_reloj", reloj)

    nina = _VESTIDOS
    nino = CategoryConfig(f"{_INFANTIL}/nino-4-16-anos/camisetas", "niño", "ropa", "camisetas")
    html = load_html("hipercor_rejilla_nina_vestidos.html")
    tienda = _tienda(
        {
            HipercorStore.grid_url(nina.category_path, 1): (200, html),
            HipercorStore.grid_url(nino.category_path, 1): (
                200,
                _rehojar(html, nino.category_path),
            ),
        },
        [nina, nino],
    )
    # Cada hoja cuesta más que la ventana, así que las dos laten.
    reloj.por_lectura = 400.0
    cfg = Config(database_url="x", request_delay=0.0, retry_backoff=0.0, progress_every_seconds=300)
    tienda._config = cfg

    with caplog.at_level(logging.INFO, logger="scraper.stores.hipercor"):
        list(tienda.list_catalog())

    latidos = [r.getMessage() for r in caplog.records if "listando" in r.getMessage()]
    assert len(latidos) == 2, latidos
    assert "hoja 1/2" in latidos[0] and nina.category_path in latidos[0]
    assert "hoja 2/2" in latidos[1] and nino.category_path in latidos[1]


def test_el_listado_no_late_si_el_progreso_esta_apagado(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`0` lo apaga aquí igual que en `ingest.py`: un solo knob para toda la pasada."""
    reloj = _RelojQueAvanza()
    reloj.por_lectura = 9000.0
    monkeypatch.setattr(progreso, "_reloj", reloj)

    tienda = _tienda(
        {
            HipercorStore.grid_url(_VESTIDOS.category_path, 1): (
                200,
                load_html("hipercor_rejilla_nina_vestidos.html"),
            )
        },
        [_VESTIDOS],
    )
    tienda._config = Config(
        database_url="x", request_delay=0.0, retry_backoff=0.0, progress_every_seconds=0
    )

    with caplog.at_level(logging.INFO, logger="scraper.stores.hipercor"):
        list(tienda.list_catalog())

    assert [r for r in caplog.records if "listando" in r.getMessage()] == []


def test_conjuntos_va_detras_de_las_hojas_del_brief_de_su_genero() -> None:
    """El invariante de orden del que depende que #192 sea correcto.

    `conjuntos` es la categoría de la prenda que no tiene ninguna de las cinco del brief como casa
    natural. Con «gana la primera» —el dedup por id de `list_catalog()`—, ir DETRÁS significa
    que un conjunto que la tienda además
    publica bajo una de las cinco conserva esa categoría, y solo se etiqueta `conjuntos` el que no
    sale en ninguna otra hoja — o sea que quien decide es la taxonomía de la tienda, no quien mapea.

    Si alguien reordenara `CATEGORIES`, las prendas que hoy entran bien como `pantalones` o
    `sudaderas` pasarían a `conjuntos` **en silencio**, partiendo su histórico de precio en dos
    categorías. Sin este test lo único que lo impedía era un comentario.
    """
    for genero in {c.gender for c in CATEGORIES}:
        conjuntos = [
            i for i, c in enumerate(CATEGORIES) if c.gender == genero and c.category == "conjuntos"
        ]
        if not conjuntos:
            continue
        del_brief = [
            i
            for i, c in enumerate(CATEGORIES)
            if c.gender == genero and c.section == "ropa" and c.category != "conjuntos"
        ]
        assert min(conjuntos) > max(del_brief), (
            f"en {genero!r} una hoja de `conjuntos` va por delante de una del brief: se quedaría "
            "con prendas que la tienda publica además como pantalones/camisetas/sudaderas/..."
        )
