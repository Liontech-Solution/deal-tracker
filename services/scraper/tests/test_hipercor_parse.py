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

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from scraper.config import Config
from scraper.stores.base import DelistCandidate, ScrapeScope
from scraper.stores.browser import BrowserUnreachable
from scraper.stores.hipercor import (
    _MAX_FICHAS_FALLIDAS,
    CATEGORIES,
    CategoryConfig,
    DetailUnavailable,
    HipercorStore,
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
    """

    def __init__(self, respuestas: dict[str, tuple[int, str] | BaseException]) -> None:
        self.respuestas = respuestas
        self.pedidas: list[str] = []
        self.bloqueados: list[str] = []
        self.descartados: list[str] = []

    def __enter__(self) -> SesionFalsa:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def bloquear(self, patron: str) -> None:
        self.bloqueados.append(patron)

    def descartar_recursos(self, tipos: tuple[str, ...]) -> None:
        self.descartados.extend(tipos)

    def get_html(self, url: str, espera_selector: str | None = None) -> tuple[int, str]:
        self.pedidas.append(url)
        respuesta = self.respuestas.get(url, (404, ""))
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta


def _tienda(
    respuestas: dict[str, tuple[int, str] | BaseException], cats: list[CategoryConfig]
) -> HipercorStore:
    sesion = SesionFalsa(respuestas)
    return HipercorStore(_CFG, categories=cats, session_factory=lambda: sesion)


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
    assert informe.failed_scopes == {ScrapeScope("niña", "ropa", "vestidos")}


def test_list_catalog_no_da_por_vacio_lo_que_no_ha_podido_leer() -> None:
    # Una hoja que responde 403 (bloqueo) o con una plantilla desconocida NO es un ámbito
    # vaciado: su ámbito queda fuera de las bajas en vez de descatalogar producto vivo.
    for respuesta in ((403, "<html></html>"), (200, "<html>otra plantilla</html>")):
        tienda = _tienda(
            {HipercorStore.grid_url(_VESTIDOS.category_path, 1): respuesta}, [_VESTIDOS]
        )
        assert list(tienda.list_catalog()) == []
        assert tienda.scan_report().failed_scopes == {ScrapeScope("niña", "ropa", "vestidos")}


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
    assert informe.failed_scopes == {ScrapeScope("niña", "zapateria", "zapatos")}
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
    assert tienda.scan_report().failed_scopes == {ScrapeScope("niña", "ropa", "vestidos")}


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
