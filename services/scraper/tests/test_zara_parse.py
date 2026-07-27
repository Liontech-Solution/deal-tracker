"""Tests de parsing de Zara con fixtures reales capturados de la web (golden-file)."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

import httpx

from scraper.config import Config
from scraper.ingest import _discount_pct
from scraper.stores.base import ScrapedImage
from scraper.stores.zara import (
    CATEGORIES,
    CategoryConfig,
    ZaraStore,
    parse_detail_product,
    parse_listing_entries,
)

from .conftest import load_fixture

# Seleccionado por atributos, no por índice: así ampliar/reordenar CATEGORIES no rompe el test.
# `zapatos` (y no cualquier hoja de zapatería) porque es la hoja de la que salió el fixture.
_CAT = next(
    c
    for c in CATEGORIES
    if c.section == "zapateria" and c.gender == "niña" and c.category == "zapatos"
)
_DOMAIN = {"gender": _CAT.gender, "section": _CAT.section, "category": _CAT.category}
# Hoja de ropa (niña / pantalones) para comprobar que el parsing común también la cubre.
_CAT_ROPA = next(
    c
    for c in CATEGORIES
    if c.section == "ropa" and c.gender == "niña" and c.category == "pantalones"
)


def test_parse_listing_entries_extrae_id_y_huella() -> None:
    listing = load_fixture("zara_category_2427610.json")
    entries = parse_listing_entries(listing, _CAT)
    assert entries, "el listado debería contener productos"

    ids = [e.retailer_product_id for e in entries]
    assert "545453620" in ids  # bailarina barefoot vista en el probe
    assert len(ids) == len(set(ids)), "no debe haber ids duplicados"

    entry = next(e for e in entries if e.retailer_product_id == "545453620")
    assert entry.signature, "la huella no debería estar vacía (hay precio por color)"
    assert entry.gender == "niña"
    # La huella es determinista: reparsear el mismo listado da la misma huella.
    assert entry.signature == parse_listing_entries(listing, _CAT)[ids.index("545453620")].signature


def test_parse_listing_entries_ropa_extrae_seccion_y_categoria() -> None:
    """El mismo parser sirve para ropa: una hoja de ropa produce entradas con su sección/slug."""
    listing = load_fixture("zara_category_2427327.json")  # niña / ropa / pantalones
    entries = parse_listing_entries(listing, _CAT_ROPA)
    assert entries, "el listado de ropa debería contener productos"

    ids = [e.retailer_product_id for e in entries]
    assert len(ids) == len(set(ids)), "no debe haber ids duplicados"

    entry = entries[0]
    assert entry.gender == "niña"
    assert entry.section == "ropa"
    assert entry.category == "pantalones"
    assert entry.signature, "la huella no debería estar vacía (hay precio por color)"
    # Determinista: reparsear el mismo listado da la misma huella.
    assert entry.signature == parse_listing_entries(listing, _CAT_ROPA)[0].signature


def test_parse_detail_product_construye_producto_con_variantes() -> None:
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)

    assert product is not None
    assert product.retailer_product_id == "545453620"
    assert "BAREFOOT" in product.name.upper()
    assert product.gender == "niña"
    assert product.section == "zapateria"
    assert product.url and product.url.endswith(".html")
    assert product.variants, "debería tener variantes talla/color"

    v = product.variants[0]
    assert v.retailer_variant_id.startswith("545453620-")  # {pid}-{color}-{size}
    assert v.price == Decimal("39.95")  # 3995 céntimos
    assert v.color == "Negro"
    assert v.size is not None
    assert v.sku is not None
    assert isinstance(v.in_stock, bool)


def test_parse_detail_product_extrae_foto_primaria() -> None:
    """La foto solo está en el detalle (el `xmedia` del listado viene vacío)."""
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)

    assert product is not None
    assert product.image_url is not None
    # `deliveryUrl` (jpg plano), no el hermano `url` con la plantilla `&w={width}`.
    assert product.image_url.startswith("https://static.zara.net/")
    assert "{width}" not in product.image_url
    assert ".jpg" in product.image_url


def test_parse_detail_product_sin_xmedia_no_revienta() -> None:
    """Un producto sin imágenes es un producto válido sin foto, no un fallo de parseo."""
    details = load_fixture("zara_products_details_545453620.json")
    entry = deepcopy(details[0])
    for color in entry["detail"]["colors"]:
        color["xmedia"] = []

    product = parse_detail_product(entry, **_DOMAIN)
    assert product is not None
    assert product.image_url is None
    assert product.images == []
    assert product.variants, "quitar las fotos no debe afectar a las variantes"


def test_parse_detail_product_construye_galeria_por_color() -> None:
    """La galería sale del MISMO recorrido de colores que las variantes."""
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)
    assert product is not None
    assert product.images, "el detalle trae xmedia: debería haber galería"

    # El fixture da once imágenes para su único color; se recorta al tope de galería.
    por_color: dict[str | None, list[ScrapedImage]] = defaultdict(list)
    for img in product.images:
        por_color[img.color].append(img)
    assert len(por_color) == 1
    (fotos,) = por_color.values()
    assert len(fotos) == 8  # once en el payload, recortadas a _MAX_IMAGES_PER_COLOR

    # El orden de la lista ES el de la galería (la posición la numera la ingesta).
    assert all(u.startswith("https://static.zara.net/") for u in (img.url for img in fotos))
    assert all("{width}" not in img.url for img in fotos)
    # La foto de tarjeta sale de la galería: una sola fuente de verdad.
    assert product.image_url == product.images[0].url


def test_galeria_y_variantes_comparten_el_nombre_de_color() -> None:
    """Invariante que sostiene el emparejamiento foto<->precio de la ficha.

    Las fotos se clavan por el TEXTO del color contra `variant.color`. Si el parseo sacara ese
    nombre de dos sitios distintos, el emparejamiento fallaría en silencio: la ficha enseñaría la
    foto de un color con el precio de otro. Aquí se fija que no puede pasar.
    """
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)
    assert product is not None

    colores_variante = {v.color for v in product.variants}
    colores_foto = {img.color for img in product.images}
    assert colores_foto <= colores_variante


def test_color_sin_tallas_con_precio_no_aporta_fotos() -> None:
    """Un color sin variantes utilizables dejaría fotos huérfanas: no se registran.

    Es el otro lado de la invariante anterior. Se sintetiza un 2º color (el fixture solo trae
    uno) con todas sus tallas a `priceUnavailable`: aporta cero variantes, luego debe aportar
    cero fotos, o la ficha ofrecería un color que no tiene precio que enseñar.
    """
    details = load_fixture("zara_products_details_545453620.json")
    entry = deepcopy(details[0])
    (vivo,) = entry["detail"]["colors"]
    muerto = deepcopy(vivo)
    muerto["id"], muerto["name"] = "999", "Fantasma"
    for size in muerto["sizes"]:
        size["price"] = None
    entry["detail"]["colors"] = [vivo, muerto]

    product = parse_detail_product(entry, **_DOMAIN)
    assert product is not None
    assert "Fantasma" not in {v.color for v in product.variants}
    assert "Fantasma" not in {img.color for img in product.images}
    assert {img.color for img in product.images} <= {v.color for v in product.variants}


def test_precios_en_euros_y_variant_id_unico() -> None:
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)
    assert product is not None
    ids = [v.retailer_variant_id for v in product.variants]
    assert len(ids) == len(set(ids)), "cada talla/color debe tener id único"
    assert all(v.price > 0 for v in product.variants)


# --- #33 Barefoot: la señal que Zara ya etiqueta y el orden que la conserva ----------------


def test_barefoot_va_antes_que_el_resto_del_calzado() -> None:
    """El orden de CATEGORIES no es cosmético: decide con qué categoría se queda un modelo.

    `list_catalog()` deduplica por id y gana la primera hoja que lo ve. Las 86 referencias
    barefoot de Zara solapan en 8 con `zapatos`/`zapatillas`; si estas hojas fuesen al final,
    esas 8 se guardarían como calzado genérico. Barefoot es el nicho del producto: gana ella.
    """
    calzado = [i for i, c in enumerate(CATEGORIES) if c.section == "zapateria"]
    barefoot = [i for i, c in enumerate(CATEGORIES) if c.category == "barefoot"]
    assert barefoot, "debe haber hojas barefoot"
    assert max(barefoot) < min(set(calzado) - set(barefoot))

    # Ids de categoría únicos: un duplicado listaría la misma hoja dos veces por pasada.
    ids = [c.category_id for c in CATEGORIES]
    assert len(ids) == len(set(ids))


def _listado_con(*product_ids: str) -> dict[str, object]:
    """Listado mínimo con la forma que recorre `_iter_product_nodes` (seo.discernProductId)."""
    return {
        "productGroups": [
            {
                "elements": [
                    {
                        "commercialComponents": [
                            {
                                "seo": {"discernProductId": pid},
                                "detail": {"colors": [{"id": "1", "price": 2599}]},
                            }
                            for pid in product_ids
                        ]
                    }
                ]
            }
        ]
    }


def _store_sirviendo(
    categories: list[CategoryConfig], por_categoria: dict[int, tuple[str, ...]]
) -> ZaraStore:
    """ZaraStore cuyo cliente HTTP devuelve un listado sintético por id de categoría."""

    def handler(request: httpx.Request) -> httpx.Response:
        cat_id = int(request.url.path.rstrip("/").split("/")[-2])
        return httpx.Response(200, json=_listado_con(*por_categoria[cat_id]))

    store = ZaraStore(Config(database_url="x", request_delay=0.0), categories)
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_el_dedup_de_list_catalog_respeta_el_orden_de_categories() -> None:
    """Lo anterior, comprobado sobre el comportamiento real y no sobre la posición en la lista.

    Un modelo que cuelga de la hoja barefoot y de la de zapatos debe salir como `barefoot`
    mientras barefoot vaya primero; invertir el orden lo degrada a `zapatos`. Ese es justo el
    fallo que el orden de CATEGORIES previene.
    """
    barefoot = CategoryConfig(2596605, "niña", "zapateria", "barefoot")
    zapatos = CategoryConfig(2427610, "niña", "zapateria", "zapatos")
    # 545453620 (la bailarina barefoot del fixture) cuelga de las dos; 111 solo de zapatos.
    servido = {2596605: ("545453620",), 2427610: ("545453620", "111")}

    entries = list(_store_sirviendo([barefoot, zapatos], servido).list_catalog())
    por_id = {e.retailer_product_id: e.category for e in entries}
    assert por_id == {"545453620": "barefoot", "111": "zapatos"}, "sin duplicar el solapado"

    al_reves = list(_store_sirviendo([zapatos, barefoot], servido).list_catalog())
    assert {e.retailer_product_id: e.category for e in al_reves}["545453620"] == "zapatos"


def test_discount_pct() -> None:
    assert _discount_pct(Decimal("39.95"), None) is None
    assert _discount_pct(Decimal("40"), Decimal("40")) is None  # sin rebaja real
    assert _discount_pct(Decimal("30"), Decimal("60")) == Decimal("50.00")
    assert _discount_pct(Decimal("50"), Decimal("40")) is None  # precio > original: no es descuento
