"""Tests de parsing de Sfera con fixtures reales capturados de la API firefly (golden-file).

Son herméticos: NO necesitan navegador ni red. Ejercitan las funciones puras
(`parse_products`, helpers y huella) sobre respuestas `firefly/products_list` reales.

Hay dos capturas de `ninos/nino` a propósito, y la diferencia entre ellas es el caso de prueba:
`sfera_firefly_ninos_nino.json` es de julio de 2026, cuando el payload **no traía media**, y
`..._media.json` es la captura actual, que sí. La vieja no se sustituye porque documenta un
modo de fallo real —una tienda que deja de dar un campo opcional— y comprueba que el parseo
degrada a `image_url = None` en vez de romperse.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from scraper.config import Config
from scraper.ingest import _discount_pct
from scraper.stores.base import ScrapedImage, ScrapeScope
from scraper.stores.browser import BrowserHTTPError, BrowserUnreachable
from scraper.stores.sfera import (
    CategoryConfig,
    SferaStore,
    _color_image_urls,
    _primary_image,
    is_mirage,
    pagination_of,
    parent_path,
    parse_products,
    product_signature,
    products_of,
)

from .conftest import load_fixture

_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0)
_NINO = CategoryConfig("ninos/nino", "niño", "ropa", "camisetas")
_REBAJAS = CategoryConfig("rebajas/ninos", "niña", "ropa", "camisetas")

# Marcador de "sin foto" de El Corte Inglés, que llega en `default_image` de cada producto.
_NO_IMAGE = "https://www.elcorteingles.es/sgfm/SGFM/contents/images/common/no-image.png"


def test_products_of_y_pagination_of() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    productos = products_of(payload)
    assert productos, "el fixture debería traer productos"
    pag = pagination_of(payload)
    assert pag["_total"] >= 1 and pag["count"] >= 1
    # Robustez ante payloads malformados (modo de fallo => vacío, lo cubre la red de bajas).
    assert products_of({}) == []
    assert pagination_of({"data": None}) == {}


def test_parse_products_extrae_id_estable_tallas_y_stock() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    assert products, "debería parsear productos"

    ids = [p.retailer_product_id for p in products]
    assert "A200974138" in ids  # id estable de producto (también en la URL)
    assert all(pid.startswith("A") for pid in ids)

    product = next(p for p in products if p.retailer_product_id == "A200974138")
    assert product.name
    assert product.gender == "niño"
    assert product.section == "ropa"
    assert product.category == "camisetas"
    assert product.url and product.url.startswith("https://www.sfera.com")
    assert product.variants, "debería tener variantes talla/color"

    v = product.variants[0]
    assert v.retailer_variant_id == "001015811718640004"  # sku estable de la talla
    assert v.sku == v.retailer_variant_id
    assert v.color == "Azul marino"
    assert v.size == "4-5 años"  # normalizada (venía "4-5 años/4-5 años")
    assert v.price == Decimal("5.99")
    assert v.list_price is None  # sin rebaja en esta categoría
    assert isinstance(v.in_stock, bool)

    # Ids de variante únicos dentro del producto.
    vids = [v.retailer_variant_id for v in product.variants]
    assert len(vids) == len(set(vids))


def test_parse_products_rebajas_mapea_precio_y_descuento() -> None:
    payload = load_fixture("sfera_firefly_rebajas_ninos.json")
    products = parse_products(products_of(payload), _REBAJAS)
    assert products

    # Alguna variante rebajada: price=sale_price (actual), list_price=price (tachado).
    rebajada = next(v for p in products for v in p.variants if v.list_price is not None)
    assert rebajada.price < rebajada.list_price
    assert rebajada.price == Decimal("3.99")
    assert rebajada.list_price == Decimal("7.99")
    # El veredicto de descuento honesto (server-side) recibe un descuento real (~50%).
    assert _discount_pct(rebajada.price, rebajada.list_price) == Decimal("50.06")


def test_parse_products_extrae_la_foto_del_listado() -> None:
    """La foto viene en el propio payload de listado: no cuesta ni una petición extra."""
    payload = load_fixture("sfera_firefly_ninos_nino_media.json")
    products = parse_products(products_of(payload), _NINO)
    assert products

    # Todos los productos del fixture traen foto (en la captura real fue 24/24).
    assert all(p.image_url for p in products)

    product = next(p for p in products if p.retailer_product_id == "A200974138")
    assert product.image_url is not None
    assert product.image_url.startswith("https://dam.elcorteingles.es/producto/")
    assert ".jpg" in product.image_url
    # Se guarda la variante de tarjeta, no la original: el CDN de ECI ignora el `&w=` que el
    # frontend añade, así que el ancho que se persiste aquí es el que se acaba sirviendo.
    assert "width=516" in product.image_url
    # Nunca el marcador `no-image.png` de la tienda: para eso está nuestro placeholder.
    assert "no-image" not in product.image_url


def test_primary_image_descarta_lo_que_no_sirve() -> None:
    """Modos de fallo del campo de imagen: marcador de la tienda, ausencia y color oculto."""
    big = "https://dam.elcorteingles.es/producto/www-1-s0.jpg?impolicy=Resize&width=516"

    # `default_image` es el no-image.png de la tienda: se ignora aunque sea lo único que hay.
    assert _primary_image({"default_image": {"default_source": _NO_IMAGE}}) is None
    assert _primary_image({"image": {"sources": {"big": _NO_IMAGE}}}) is None
    # Sin nada utilizable -> None (no revienta el parseo).
    assert _primary_image({}) is None
    assert _primary_image({"image": None, "_my_colors": []}) is None
    # Respaldo por color, saltándose los ocultos (que tampoco generan variantes).
    assert (
        _primary_image({"_my_colors": [{"hideColor": True, "image": _NO_IMAGE}, {"image": big}]})
        == big
    )
    # Preferencia por `big` sobre los tamaños menores.
    assert _primary_image({"image": {"sources": {"small": "https://x/s.jpg", "big": big}}}) == big


def test_parse_products_sin_campo_de_imagen_no_rompe() -> None:
    """El fixture antiguo es de antes de que Sfera sirviera fotos: debe seguir parseando."""
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    assert products, "debería parsear productos aunque no haya media"
    assert all(p.image_url is None for p in products)
    # Galería vacía, que NO es lo mismo que "este producto no tiene fotos": la ingesta la
    # interpreta como "esta pasada no sabe de fotos" y por eso no borra la que hubiera.
    assert all(p.images == [] for p in products)


def test_parse_products_construye_galeria_por_color() -> None:
    """La galería sale del listado firefly (`all_images`): cero peticiones nuevas."""
    payload = load_fixture("sfera_firefly_ninos_nino_media.json")
    products = parse_products(products_of(payload), _NINO)
    assert products

    product = next(p for p in products if p.retailer_product_id == "A200974138")
    assert product.images, "el color trae all_images: debería haber galería"

    por_color: dict[str | None, list[ScrapedImage]] = defaultdict(list)
    for img in product.images:
        por_color[img.color].append(img)

    for fotos in por_color.values():
        for img in fotos:
            assert img.url.startswith("https://dam.elcorteingles.es/producto/")
            # Se guarda `big`: el CDN de ECI ignora el `&w=` del frontend, así que el ancho
            # que se persiste aquí es el que se acaba sirviendo.
            assert "width=516" in img.url
            assert "no-image" not in img.url
            # La muestra de color (`thumbnail_url`, un png de la carta) no es foto de prenda.
            assert not img.url.endswith(".png")

    # La foto de tarjeta sale de la galería: una sola fuente de verdad.
    assert product.image_url == product.images[0].url


def test_galeria_y_variantes_comparten_el_nombre_de_color() -> None:
    """Invariante que sostiene el emparejamiento foto<->precio de la ficha.

    Las fotos se clavan por el TEXTO del color contra `variant.color`. Sacar ese nombre de dos
    sitios distintos haría que la ficha enseñara la foto de un color con el precio de otro.
    """
    payload = load_fixture("sfera_firefly_ninos_nino_media.json")
    for product in parse_products(products_of(payload), _NINO):
        colores_variante = {v.color for v in product.variants}
        assert {img.color for img in product.images} <= colores_variante


def test_color_oculto_no_aporta_fotos() -> None:
    """Un `hideColor` no genera variantes, así que tampoco puede aportar fotos."""
    payload = load_fixture("sfera_firefly_ninos_nino_media.json")
    raw = products_of(payload)
    producto = next(p for p in raw if p["id"] == "A200974138")
    fantasma = deepcopy(producto["_my_colors"][0])
    fantasma["hideColor"] = True
    fantasma["title"] = "Fantasma"
    producto["_my_colors"] = [*producto["_my_colors"], fantasma]

    product = next(p for p in parse_products(raw, _NINO) if p.retailer_product_id == "A200974138")
    assert "Fantasma" not in {img.color for img in product.images}
    assert {img.color for img in product.images} <= {v.color for v in product.variants}


def test_color_image_urls_modos_de_fallo() -> None:
    """`all_images` ausente/inservible: respaldo a `image`, y nunca el marcador de la tienda."""
    big = "https://dam.elcorteingles.es/producto/www-1-s0.jpg?impolicy=Resize&width=516"

    # Sin `all_images` se cae al `image` plano del color.
    assert _color_image_urls({"image": big}) == [big]
    # `all_images` manda sobre `image` cuando lo hay.
    assert _color_image_urls({"all_images": [{"sources": {"big": big}}], "image": _NO_IMAGE}) == [
        big
    ]
    # El marcador `no-image.png` no cuela por ninguna vía.
    assert _color_image_urls({"all_images": [{"sources": {"big": _NO_IMAGE}}]}) == []
    assert _color_image_urls({}) == []
    # Tope por color: la cola son detalles de tejido que no aportan a una galería.
    muchas = {"all_images": [{"sources": {"big": f"{big}&n={i}"}} for i in range(20)]}
    assert len(_color_image_urls(muchas)) == 8


def test_product_signature_determinista_y_sensible_al_precio() -> None:
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    p = products[0]
    # Reparsear el mismo fixture da la misma huella (estable, sin depender del orden).
    again = parse_products(products_of(load_fixture("sfera_firefly_ninos_nino.json")), _NINO)[0]
    assert product_signature(p) == product_signature(again)
    # La huella incluye el precio efectivo: un cambio de precio la cambia.
    assert product_signature(p) != f"{p.variants[0].retailer_variant_id}:0.00"


# --- #33 Sfera no etiqueta el barefoot, pero lo dice en el nombre --------------------------

_BEBE_NINA = CategoryConfig("ninos/bebe-nina/zapatos", "niña", "zapateria", "zapatos")


def test_el_barefoot_de_sfera_sale_del_nombre_del_producto() -> None:
    """La única tienda que se clasifica por la heurística de texto, sobre datos reales.

    Zara y Lefties traen su propia categoría BAREFOOT y se resuelven sin mirar una palabra. Sfera
    no la tiene —ni en el árbol ni en las facetas— pero **escribe «barefoot» en el nombre**. Este
    fixture es la captura real de la hoja donde está el grueso de su calzado respetuoso.
    """
    payload = load_fixture("sfera_firefly_ninos_bebe_nina_zapatos.json")
    products = parse_products(products_of(payload), _BEBE_NINA)
    assert len(products) == 9

    por_marca: dict[str | None, list[str]] = defaultdict(list)
    for p in products:
        por_marca[p.barefoot].append(p.name)

    assert sorted(por_marca["si"]) == [
        "Merceditas basic barefoot",
        "Merceditas basic barefoot",
        "Merceditas basic barefoot",
        "Zapatilla runner barefoot",
    ]
    # El resto NO se inventa un veredicto: sin señal, `desconocido`. El sesgo va en una sola
    # dirección, y un falso `si` es justo la mentira que este producto existe para no contar.
    assert len(por_marca["desconocido"]) == 5
    assert "no" not in por_marca


def test_la_ropa_de_sfera_no_recibe_marca_barefoot() -> None:
    """`barefoot` es None en ropa: la pregunta no aplica y la columna se queda NULL."""
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    products = parse_products(products_of(payload), _NINO)
    assert products and all(p.barefoot is None for p in products)


# --- #56 La ropa del rango bebé, que se talla en meses -------------------------------------

_BEBE_NINO_CAMISETAS = CategoryConfig("ninos/bebe-nino/camisetas", "niño", "ropa", "camisetas")


def test_la_ropa_de_bebe_se_talla_en_meses_y_se_guarda_cruda() -> None:
    """El rango bebé estrena una forma de talla que no había en el catálogo: los meses.

    El scraper **no** canonicaliza —de eso se encarga `size_canon` en SQL— así que lo único que
    tiene que hacer aquí es deshacer el duplicado de `valueMain` ('2-3 Meses/2-3 Meses') y
    guardar el resto tal cual, **incluida la caja**: la tienda escribe 'Meses' en unas tallas y
    'meses' en otras dentro del MISMO producto, y eso es un dato sobre la tienda, no ruido que
    toque limpiar aquí.
    """
    payload = load_fixture("sfera_firefly_ninos_bebe_nino_camisetas.json")
    products = parse_products(products_of(payload), _BEBE_NINO_CAMISETAS)
    assert len(products) == 3

    tallas = {v.size for p in products for v in p.variants}
    assert tallas == {
        "2-3 Meses",
        "3-4 Meses",
        "6-9 meses",
        "9-12 meses",
        "12-18 meses",
        "18-24 meses",
    }
    assert all("/" not in (v.size or "") for p in products for v in p.variants)


def test_la_ropa_de_bebe_hereda_el_ambito_de_su_hoja() -> None:
    """Género y sección salen de `CategoryConfig`, no del slug: `bebe-nino` -> `niño`/`ropa`."""
    payload = load_fixture("sfera_firefly_ninos_bebe_nino_camisetas.json")
    products = parse_products(products_of(payload), _BEBE_NINO_CAMISETAS)

    assert all(p.gender == "niño" and p.section == "ropa" for p in products)
    assert all(p.category == "camisetas" for p in products)
    assert all(p.barefoot is None for p in products), "es ropa: la pregunta barefoot no aplica"
    assert all(p.variants and p.url and p.image_url for p in products)


# --- #41 Una categoría retirada no tumba las demás -----------------------------------------

_CATS_SCAN = [
    CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos"),
    CategoryConfig("ninos/nina/camisetas", "niña", "ropa", "camisetas"),
]


class _ScanSession:
    """Sesión falsa: cada categoría responde su payload firefly, o revienta con un status.

    Las claves se buscan por subcadena de la URL y **gana la primera que case**, así que las
    rutas hoja van antes que la del padre. La del padre (`ninos/nina/1/`) lleva el número de
    página para no comerse también las URLs de sus hojas.
    """

    def __init__(self, por_categoria: dict[str, Any]) -> None:
        self._por_categoria = por_categoria
        self.pedidas: list[str] = []  # para comprobar que el padre se pide UNA vez

    def __enter__(self) -> _ScanSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def goto(self, url: str) -> int:
        return 200

    def get_json(self, url: str) -> Any:
        self.pedidas.append(url)
        for path, respuesta in self._por_categoria.items():
            if path in url:
                if isinstance(respuesta, Exception):
                    raise respuesta
                return respuesta
        raise AssertionError(f"categoría no simulada: {url}")


def _firefly(pid: str) -> dict[str, Any]:
    """Payload firefly mínimo: un producto de un color con una talla con precio."""
    return {
        "success": True,
        "data": {
            "pagination": {"_total": 1},
            "products": [
                {
                    "id": pid,
                    "name": f"Producto {pid}",
                    "_my_colors": [
                        {
                            "title": "Negro",
                            "variants": [{"id": f"{pid}-v", "size": "30", "price": 19.95}],
                        }
                    ],
                }
            ],
        },
    }


def _scan_store(
    por_categoria: dict[str, Any], padre: Any = None
) -> tuple[SferaStore, _ScanSession]:
    """Store con sesión falsa. `padre` responde a `ninos/nina/1/` (ver #54); por defecto, uno
    que no se parece a ninguna hoja, que es el caso sano."""
    respuestas = {**por_categoria, "ninos/nina/1/": padre or _firefly("PADRE")}
    session = _ScanSession(respuestas)
    store = SferaStore(_CFG, categories=_CATS_SCAN, session_factory=lambda: session)  # type: ignore[arg-type]
    return store, session


def test_categoria_retirada_no_impide_listar_las_demas() -> None:
    store, _ = _scan_store(
        {
            "ninos/nina/zapatos": _firefly("Z1"),
            "ninos/nina/camisetas": BrowserHTTPError(404, "https://sfera.example/firefly"),
        }
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["Z1"]
    assert (report.leaves_total, report.leaves_failed) == (2, 1)
    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "camisetas")}


def test_un_bloqueo_de_akamai_no_pasa_por_categoria_retirada() -> None:
    """403 es Akamai cerrando la puerta, no una categoría que Sfera haya quitado."""
    store, _ = _scan_store(
        {
            "ninos/nina/zapatos": _firefly("Z1"),
            "ninos/nina/camisetas": BrowserHTTPError(403, "https://sfera.example/firefly"),
        }
    )

    with pytest.raises(BrowserHTTPError):
        list(store.list_catalog())


def test_un_timeout_en_una_hoja_no_se_lleva_la_pasada(caplog: pytest.LogCaptureFixture) -> None:
    """#107: el fallo transitorio más probable de una tienda por navegador no puede abortarlo todo.

    Antes de #107 esto subía hasta `ingest` y tiraba la pasada entera, incluidas las hojas ya
    leídas. Ahora la hoja cuenta como caída —su ámbito queda fuera de las bajas— y es el
    `dead_ratio` quien decide si han sido demasiadas.
    """
    store, _ = _scan_store(
        {
            "ninos/nina/zapatos": _firefly("Z1"),
            "ninos/nina/camisetas": BrowserUnreachable(
                "https://sfera.example/firefly", TimeoutError("Timeout 45000ms exceeded")
            ),
        }
    )

    with caplog.at_level(logging.WARNING):
        ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["Z1"]
    assert (report.leaves_total, report.leaves_failed) == (2, 1)
    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "camisetas")}
    assert "camisetas" in caplog.text, "una hoja que se pierde tiene que dejar rastro"


# --- #54 La hoja que no existe devuelve el catálogo del padre ------------------------------

# Captura real de `ninos/nina` (página 1, 12 productos, 30 páginas): es a la vez el catálogo del
# padre y —literalmente la misma respuesta— lo que Sfera sirve para una ruta que ya no existe.
_PADRE_REAL = load_fixture("sfera_firefly_ninos_nina_padre.json")


def test_parent_path_recorta_el_ultimo_segmento() -> None:
    assert parent_path("ninos/nina/zapatos") == "ninos/nina"
    assert parent_path("/ninos/bebe-nina/zapatos/") == "ninos/bebe-nina"
    # Menos de tres segmentos no tiene padre útil: sería la tienda entera.
    assert parent_path("ninos/nina") is None
    assert parent_path("ninos") is None


def test_el_espejismo_se_reconoce_por_los_ids_no_por_el_titulo() -> None:
    """El título es texto localizado de presentación; los ids son el contrato."""
    con_otro_titulo = deepcopy(_PADRE_REAL)
    con_otro_titulo["data"]["title"] = "Zapatos (Niños) | Sfera España"

    assert is_mirage(con_otro_titulo, _PADRE_REAL)
    assert not is_mirage(_firefly("Z1"), _PADRE_REAL)


def test_una_pagina_distinta_del_padre_no_es_espejismo() -> None:
    """Sin esto, cualquier hoja con el mismo número de páginas caería en la red."""
    otra = deepcopy(_PADRE_REAL)
    otra["data"]["products"] = otra["data"]["products"][1:]

    assert not is_mirage(otra, _PADRE_REAL)


def test_la_hoja_espejismo_no_se_ingiere_y_cuenta_como_caida() -> None:
    """El caso real: `ninos/nina/camisetas` deja de existir y Sfera sirve el género entero.

    Sin la comprobación se ingerirían 360 productos —ropa incluida— etiquetados como
    `niña/ropa/camisetas`, y el `ScanReport` no vería ninguna hoja caída.
    """
    store, _ = _scan_store(
        {
            "ninos/nina/zapatos": _firefly("Z1"),
            "ninos/nina/camisetas": _PADRE_REAL,
        },
        padre=_PADRE_REAL,
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["Z1"], "la hoja espejismo no debe aportar ni un producto"
    assert (report.leaves_total, report.leaves_failed) == (2, 1)
    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "camisetas")}


def test_el_padre_se_pide_una_sola_vez_para_todas_sus_hojas() -> None:
    """La red cuesta una petición por padre y pasada, no una por hoja."""
    store, session = _scan_store(
        {"ninos/nina/zapatos": _firefly("Z1"), "ninos/nina/camisetas": _firefly("C1")}
    )

    list(store.list_catalog())

    del_padre = [u for u in session.pedidas if "products_list/ninos/nina/1/" in u]
    assert len(del_padre) == 1


def test_check_leaves_marca_el_espejismo_como_muerta() -> None:
    """Antes informaba «12 productos en la 1ª página» de una ruta inventada, o sea: viva."""
    store, _ = _scan_store(
        {
            "ninos/nina/zapatos": _firefly("Z1"),
            "ninos/nina/camisetas": _PADRE_REAL,
        },
        padre=_PADRE_REAL,
    )

    salud = {h.leaf: h for h in store.check_leaves()}

    assert salud["ninos/nina/zapatos"].alive is True
    assert salud["ninos/nina/camisetas"].alive is False
    assert "espejismo" in (salud["ninos/nina/camisetas"].detail or "")
