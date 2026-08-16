"""Tests de parsing de Zara con fixtures reales capturados de la web (golden-file)."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

import httpx

from scraper.config import Config
from scraper.ingest import _discount_pct
from scraper.stores.base import ScrapedImage, ScrapeScope
from scraper.stores.zara import (
    _FAMILIA_RESIDUAL,
    CATEGORIES,
    CategoryConfig,
    ZaraStore,
    _comprabilidad,
    _familia_base,
    parse_category_tree,
    parse_detail_product,
    parse_listing_entries,
    parse_listing_leftovers,
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
# Los ámbitos que la tienda declara, que es lo que `list_catalog()` le pasa al residuo.
_DECLARADOS = [ScrapeScope(c.gender, c.section, c.category) for c in CATEGORIES]
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


def test_el_filtro_de_conjuntos_se_queda_solo_con_la_familia_conjunto() -> None:
    """El mecanismo de #200, sobre un listado real y **sin fixture nuevo**.

    La hoja de pantalones ya trae dos productos que la tienda declara `familyName = CONJUNTO`, que
    es exactamente la mezcla que hay dentro de las hojas de conjunto: el filtro se queda con esos
    dos y descarta los otros 69, sin mirar el título.

    Vale como prueba del filtro precisamente porque la hoja NO es de conjuntos: si el criterio
    fuese el nombre de la hoja, o el orden, aquí no saldría nada.
    """
    listing = load_fixture("zara_category_2427327.json")
    cat_conjuntos = next(c for c in CATEGORIES if c.category == "conjuntos")
    hoja = CategoryConfig(2427327, "niña", "ropa", "conjuntos", filtro=cat_conjuntos.filtro)

    sin_filtro = parse_listing_entries(listing, _CAT_ROPA)
    filtradas = parse_listing_entries(listing, hoja)

    esperados = {
        pid
        for pid, (fam, nom) in _senales_por_id(listing).items()
        if fam.upper().startswith("CONJUNTO") or nom.upper().startswith("CONJUNTO")
    }
    assert esperados, "el fixture debería traer algún producto identificado como conjunto"
    assert {e.retailer_product_id for e in filtradas} == esperados
    assert len(filtradas) < len(sin_filtro), "el filtro tiene que descartar el resto de la hoja"
    assert all(e.category == "conjuntos" for e in filtradas)


def test_lo_que_el_filtro_descarta_puede_seguir_entrando_por_su_hoja() -> None:
    """Descartar no es «gastar» el producto: es la condición para que el dedup no lo pierda (#200).

    `list_catalog()` deduplica con «gana la primera» y las hojas de conjunto van DELANTE. Si el
    filtro emitiera lo que no casa —aunque fuese con otra categoría— ocuparía el hueco en `emitted`
    y el pantalón del lookbook no entraría nunca por su hoja de pantalones.
    """
    listing = load_fixture("zara_category_2427327.json")
    cat_conjuntos = next(c for c in CATEGORIES if c.category == "conjuntos")
    hoja = CategoryConfig(2427327, "niña", "ropa", "conjuntos", filtro=cat_conjuntos.filtro)

    filtradas = {e.retailer_product_id for e in parse_listing_entries(listing, hoja)}
    completas = {e.retailer_product_id for e in parse_listing_entries(listing, _CAT_ROPA)}

    assert completas - filtradas, "el resto de la hoja tiene que quedar libre para su categoría"


def test_el_conjunto_se_reconoce_por_la_familia_o_por_el_titulo() -> None:
    """Las dos señales, y por qué ninguna vale sola. Medido en vivo el 06/08/2026 (#200).

    Los cuatro casos son reales, sacados de las hojas que se mapean:

      - la familia sin el título: la tienda titula «PACK BODY …» o «SET PRIMERA PUESTA …» productos
        que archiva como `CONJUNTO`, y hasta escribe «CONJUTO» con una errata suya.
      - el título sin la familia: 40 conjuntos viven en la familia `CHANDAL BEBE`, que no vale como
        señal porque esa misma familia lleva pantalones y camisetas sueltos.
    """
    filtro = next(c for c in CATEGORIES if c.category == "conjuntos").filtro
    assert filtro is not None

    assert filtro.acepta("CONJUNTO BEBE", "PACK BODY CRUZADO Y LEGGING POINTELLE LAZO")
    assert filtro.acepta("CONJUNTO", "CONJUTO CAMISETA Y BERMUDA CUADRO DAMERO")
    assert filtro.acepta("CHANDAL BEBE", "CONJUNTO SUDADERA RAYAS Y LEGGING FLARE")
    # Y lo que NO es conjunto aunque salga en la misma hoja: ni la familia ni el título lo dicen.
    assert not filtro.acepta("CHANDAL BEBE", "PANTALÓN JOGGER FELPA")
    assert not filtro.acepta("PANTALON", "PANTALÓN WIDE LEG EFECTO ARRUGADO")
    # El ancla: la palabra tiene que abrir la etiqueta, no aparecer dentro.
    assert not filtro.acepta("VESTIDO", "VESTIDO CON CONJUNTO DE CHAQUETA")


def _senales_por_id(listing: dict) -> dict[str, tuple[str, str]]:
    """`(familyName, name)` por id, leyendo el listado igual que lo lee el parser."""
    senales: dict[str, tuple[str, str]] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            seo = node.get("seo")
            if isinstance(seo, dict) and seo.get("discernProductId"):
                senales[str(seo["discernProductId"])] = (
                    str(node.get("familyName") or ""),
                    str(node.get("name") or ""),
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(listing)
    return senales


def test_el_residuo_del_lookbook_que_es_prenda_del_brief_se_recoge() -> None:
    """El residuo que ninguna otra hoja publica se rescata con la familia de la tienda (#289).

    La hoja es la real de `CHANDAL` de niño (2622124), que es de donde salieron los 44 productos
    que la pasada tiraba en cada vuelta: Zara solo los publica ahí, así que descartarlos los dejaba
    fuera del catálogo para siempre aunque siguieran a la venta.
    """
    listing = load_fixture("zara_category_2622124_conjuntos.json")
    hoja = next(c for c in CATEGORIES if c.category_id == 2622124)

    conjuntos = {e.retailer_product_id: e for e in parse_listing_entries(listing, hoja)}
    residuo = {
        e.retailer_product_id: e for e in parse_listing_leftovers(listing, hoja, _DECLARADOS)
    }

    # El conjunto de verdad sigue siendo `conjuntos` y NO se duplica en el residuo.
    assert conjuntos["545888980"].category == "conjuntos"
    assert "545888980" not in residuo

    # La prenda suelta entra por la familia que la tienda le pone, no por su título.
    assert residuo["545470437"].category == "pantalones"  # familia PANTALON
    assert residuo["545852572"].category == "sudaderas"  # familia JERSEY, titulada «SUDADERA …»
    assert residuo["545470437"].gender == hoja.gender
    assert residuo["545470437"].section == hoja.section

    # Y lo que no es del brief o no es producto se sigue descartando, que es la mitad conservadora.
    assert "579615420" not in residuo, "CHAQUETA es abrigo: fuera del brief"
    assert "9911278428" not in residuo, "el nodo LOOK del lookbook no es un producto"


def test_el_residuo_no_inventa_un_ambito_que_la_tienda_no_recorre() -> None:
    """La familia decide categoría y la hoja decide género: juntas pueden inventar una pareja.

    `PETO` va a `vestidos` —lo decidió la hoja `PETOS | MONOS`— pero en Zara `vestidos` solo existe
    para niña y unisex, así que un peto en el lookbook de NIÑO daría `niño/ropa/vestidos`, que
    `scopes()` no devuelve nunca. Y un ámbito que no está en `scopes()` no entra en `safe_scopes`:
    el producto no se podría dar de baja jamás, ni desapareciendo de la tienda.
    """
    peto_de_nino = {
        "productGroups": [
            {
                "elements": [
                    {
                        "commercialComponents": [
                            {
                                "seo": {"discernProductId": 999000111},
                                "familyName": "PETO",
                                "name": "PETO SARGA",
                                "detail": {"colors": [{"id": "800", "price": 2595}]},
                            }
                        ]
                    }
                ]
            }
        ]
    }
    lookbook_nino = next(c for c in CATEGORIES if c.gender == "niño" and c.category == "conjuntos")
    assert lookbook_nino.filtro is not None

    assert ScrapeScope("niño", "ropa", "vestidos") not in _DECLARADOS, "premisa del test"
    assert parse_listing_leftovers(peto_de_nino, lookbook_nino, _DECLARADOS) == []

    # Y el mismo peto en una hoja cuyo género SÍ declara `vestidos` sí entra: lo que descarta es la
    # pareja imposible, no la familia.
    lookbook_unisex = next(
        c for c in CATEGORIES if c.gender == "unisex" and c.category == "conjuntos"
    )
    recogido = parse_listing_leftovers(peto_de_nino, lookbook_unisex, _DECLARADOS)
    assert [e.category for e in recogido] == ["vestidos"]


def test_el_residuo_no_se_recoge_en_una_hoja_sin_filtro() -> None:
    """Sin `filtro` no hay lookbook ni residuo: la hoja entera es su categoría."""
    listing = load_fixture("zara_category_2427327.json")
    assert parse_listing_leftovers(listing, _CAT_ROPA, _DECLARADOS) == []


def test_el_residuo_solo_entra_si_ninguna_hoja_lo_reclama() -> None:
    """La demora es lo que impide que el arreglo re-etiquete nada (#289).

    Se recorre el catálogo como `list_catalog()`: hojas primero, residuo al final. Un producto que
    aparece en la hoja de conjuntos Y en la de pantalones tiene que conservar la categoría que le
    dio su hoja, porque para cuando se mira el residuo ya está en `emitted`.
    """
    listing = load_fixture("zara_category_2427327.json")  # trae PANTALON de sobra
    cat_conjuntos = next(c for c in CATEGORIES if c.category == "conjuntos")
    lookbook = CategoryConfig(2427327, "niña", "ropa", "conjuntos", filtro=cat_conjuntos.filtro)

    emitted: dict[str, str] = {}
    for entry in parse_listing_entries(listing, lookbook):  # el lookbook va DELANTE
        emitted.setdefault(entry.retailer_product_id, entry.category)
    for entry in parse_listing_entries(listing, _CAT_ROPA):  # su hoja de verdad
        emitted.setdefault(entry.retailer_product_id, entry.category)
    sobrantes = [
        e
        for e in parse_listing_leftovers(listing, lookbook, _DECLARADOS)
        if e.retailer_product_id not in emitted
    ]

    assert not sobrantes, "aquí toda prenda tiene su hoja: el residuo no debe añadir nada"


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


def test_la_comprabilidad_del_sondeo_sale_del_MISMO_sitio_que_el_stock_del_catalogo() -> None:
    """#426, contra el fixture real: el sondeo y el catálogo no pueden discrepar sobre el stock.

    `_comprabilidad()` es lo que decide `ALIVE` frente a `UNBUYABLE` en `probe_alive()`, y
    `ScrapedVariant.in_stock` es lo que el catálogo enseña. Los dos leen `availability`, así que lo
    que este test fija es que **coincidan sobre un payload de verdad** — si algún día Zara le
    cambia el nombre al campo, lo que no puede pasar es que uno diga que hay stock y el otro que no.

    El fixture trae 10 tallas `in_stock` y una `coming_soon`, o sea que además cubre que un valor
    que NO es `in_stock` exista de verdad en la respuesta de la tienda y no sea una invención mía.
    """
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)

    assert product is not None
    assert _comprabilidad(details) is any(v.in_stock for v in product.variants)
    assert _comprabilidad(details) is True  # este producto sí tiene stock


def test_sin_ninguna_talla_in_stock_el_sondeo_lo_ve_incomprable() -> None:
    """El otro lado del anterior, sobre el mismo payload real con las tallas agotadas.

    Se degrada el fixture en vez de escribir uno a mano: así lo que se prueba es la forma que Zara
    publica de verdad, no la que yo creo que publica.
    """
    details = load_fixture("zara_products_details_545453620.json")
    for color in details[0]["detail"]["colors"]:
        for size in color["sizes"]:
            size["availability"] = "out_of_stock"

    assert _comprabilidad(details) is False


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


def test_bebe_va_despues_de_las_hojas_con_genero() -> None:
    """El mismo invariante de orden, en la dirección contraria (#186).

    La rama de bebé (0-18 meses) no separa niño de niña, así que entra como `unisex`, y su rango
    solapa con el de las hojas mini: medido el 05/08/2026, de los 1157 productos que listan sus
    once hojas, 612 ya entraban por una hoja con género. Aquí gana la hoja CON género —es la que
    la web puede filtrar—, así que bebé va detrás. Invertirlo dejaría esos 612 como `unisex` y,
    de paso, los contaría como mudanza de ámbito en la primera pasada (#174).
    """
    bebe = [i for i, c in enumerate(CATEGORIES) if c.gender == "unisex"]
    con_genero = [i for i, c in enumerate(CATEGORIES) if c.gender != "unisex"]
    assert bebe, "debe haber hojas de bebé"
    assert min(bebe) > max(con_genero)


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


def test_el_detalle_clasifica_el_calzado_como_barefoot() -> None:
    """La marca de #30 sale del propio detalle, sin peticiones nuevas.

    El fixture es la bailarina de la hoja BAREFOOT: su nombre lo dice y su descripción por color
    también («suela de goma flexible con drop 0»), así que cae por las dos vías.
    """
    details = load_fixture("zara_products_details_545453620.json")
    product = parse_detail_product(details[0], **_DOMAIN)  # _DOMAIN es zapatería/zapatos
    assert product is not None
    assert product.barefoot == "si"

    # Y por la vía de la categoría, sin mirar el texto: es la hoja de la tienda quien lo dice.
    barefoot_cat = parse_detail_product(
        details[0], gender="niña", section="zapateria", category="barefoot"
    )
    assert barefoot_cat is not None and barefoot_cat.barefoot == "si"


def test_la_ropa_se_queda_sin_marca_barefoot() -> None:
    """NULL = "no aplica", que no es lo mismo que `desconocido` (ver 0012_add_barefoot.sql)."""
    details = load_fixture("zara_products_details_545453620.json")
    ropa = parse_detail_product(details[0], gender="niña", section="ropa", category="camisetas")
    assert ropa is not None
    assert ropa.barefoot is None


def test_discount_pct() -> None:
    assert _discount_pct(Decimal("39.95"), None) is None
    assert _discount_pct(Decimal("40"), Decimal("40")) is None  # sin rebaja real
    assert _discount_pct(Decimal("30"), Decimal("60")) == Decimal("50.00")
    assert _discount_pct(Decimal("50"), Decimal("40")) is None  # precio > original: no es descuento


# --- el árbol de categorías (#179) -------------------------------------------


def arbol() -> dict:
    return load_fixture("zara_categories_ninos.json")


def tienda_con_arbol() -> ZaraStore:
    """La tienda sirviendo el árbol capturado, sin red."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=arbol())

    store = ZaraStore(Config(database_url="postgresql:///no-usada", request_delay=0.0))
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_la_ruta_es_la_cadena_de_ids_y_no_el_id_suelto() -> None:
    """Es lo que deja saber que un nodo cuelga de una hoja que ya ingerimos.

    Los ids de Zara son opacos: `2427327` no dice de quién es hijo. Sin la cadena, los 183 nodos
    que cuelgan de una hoja mapeada saldrían como huecos.
    """
    nodos = {n.path: n for n in parse_category_tree(arbol(), "2112261")}

    assert "2112261/2425905" in nodos, "la rama de niña cuelga directamente de la raíz"
    ruta_pantalones = "2112261/2425905/2643261/2427327"
    assert ruta_pantalones in nodos
    assert nodos[ruta_pantalones].title == "PANTALONES"


def test_la_profundidad_se_mide_contra_la_raiz_pedida() -> None:
    """Pedir una rama o el catálogo entero no puede dar dos profundidades al mismo nodo."""
    desde_raiz = {n.path: n for n in parse_category_tree(arbol(), "2112261")}
    desde_rama = {n.path: n for n in parse_category_tree(arbol(), "2112261/2425905")}

    ruta = "2112261/2425905/2643261/2427327"
    assert desde_raiz[ruta].depth == 3
    assert desde_rama[ruta].depth == 2, "dos niveles por debajo de la rama de niña"


def test_solo_salen_los_descendientes_de_la_raiz_pedida() -> None:
    """Y la raíz no se emite a sí misma: es lo que se ha preguntado, no un hallazgo."""
    rutas = {n.path for n in parse_category_tree(arbol(), "2112261/2425905")}

    assert rutas
    assert all(r.startswith("2112261/2425905/") for r in rutas)


def test_un_nodo_sin_hijas_no_se_las_inventa() -> None:
    nodos = {n.path: n for n in parse_category_tree(arbol(), "2112261")}

    assert not nodos["2112261/2311136"].has_children, "TIENDAS es una hoja del menú"
    assert nodos["2112261/2425905"].has_children


def test_el_count_es_none_porque_este_endpoint_no_lo_dice() -> None:
    """`None` es «no lo dice» y 0 sería «rama vacía»: confundirlos diría que la tienda se cayó."""
    assert all(n.count is None for n in parse_category_tree(arbol(), "2112261"))


def test_una_raiz_que_el_arbol_ya_no_publica_no_revienta() -> None:
    """Un id retirado devuelve árbol vacío; quien avisa de que la hoja murió es `check_leaves()`."""
    assert parse_category_tree(arbol(), "9999999") == []


def test_un_payload_sin_categorias_no_inventa_arbol() -> None:
    assert parse_category_tree({}, "2112261") == []
    assert parse_category_tree({"categories": []}, "2112261") == []


def test_el_menu_publica_ruido_que_no_es_catalogo() -> None:
    """El porqué de que esta tienda no se vigile cada semana: su árbol no es una taxonomía.

    Si algún día se quisiera vigilar, esto es lo que habría que declarar una por una.
    """
    titulos = {n.title for n in parse_category_tree(arbol(), "2112261")}

    assert "DIVIDER_MENU_KIDS5" in titulos
    assert "TIENDAS" in titulos
    assert "VER TODO" in titulos


def test_mapped_leaves_devuelve_cadenas_y_no_ids_sueltos() -> None:
    """Tienen que hablar el mismo idioma que el árbol o la cobertura no cruza nada."""
    store = tienda_con_arbol()
    hojas = set(store.mapped_leaves())

    assert hojas, "el fixture trae la rama de niña, con 14 hojas mapeadas"
    assert all("/" in h for h in hojas)
    assert "2112261/2425905/2643261/2427327" in hojas


def test_mapped_leaves_omite_la_hoja_cuyo_id_ha_caducado() -> None:
    """Inventarle una cadena la marcaría como ingerida; quien lo canta es `check_leaves()`."""
    store = tienda_con_arbol()
    store._categories = [*store._categories, CategoryConfig(9999999, "niña", "ropa", "vestidos")]

    assert not any(h.endswith("9999999") for h in store.mapped_leaves())


def test_el_arbol_se_pide_una_sola_vez_por_instancia() -> None:
    """`run --tree` llama a `mapped_leaves()` y a `category_tree()`: sin caché serían 2 MB."""
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        return httpx.Response(200, json=arbol())

    store = ZaraStore(Config(database_url="postgresql:///no-usada", request_delay=0.0))
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    list(store.mapped_leaves())
    list(store.mapped_leaves())

    assert llamadas == 1


# --- `_familia_base()`: la red de #358 ----------------------------------------------------------
#
# Todo esto protege una sola cosa: que el residuo de #289 no se caiga a cero en silencio porque
# Zara re-rotule una familia. La lista de sufijos NO se adivina — se midió el 14/08/2026 pidiendo
# las 62 hojas mapeadas (4644 productos, 54 familias distintas) y el único que la tienda usa es
# `BEBE`, sin tilde. Lo que se endurece es el mecanismo, que daba por hecho un literal exacto.


def test_familia_base_sigue_recortando_el_sufijo_de_siempre() -> None:
    assert _familia_base("PANTALON BEBE") == "PANTALON"
    assert _familia_base("PANTALON") == "PANTALON"
    assert _familia_base("  jersey   bebe  ") == "JERSEY"


def test_familia_base_aguanta_la_tilde_que_hoy_no_existe() -> None:
    """El cambio más probable y el más silencioso: `PANTALÓN BEBÉ` devolvía `None` y se perdían 48.

    Hoy Zara no acentúa ninguna familia —medido—, así que esto no arregla nada: es la red. Que el
    test pase antes de que ocurra es justo el punto.
    """
    assert _familia_base("PANTALÓN BEBÉ") == "PANTALON"
    assert _familia_base("JERSEY BEBÉ") == "JERSEY"
    assert _FAMILIA_RESIDUAL[_familia_base("PANTALÓN BEBÉ")] == "pantalones"


def test_familia_base_no_da_por_hecho_el_espacio() -> None:
    """Zara ya rotula sin espacio: `PRENDA EXT.BEBE` y `BRAGA/CALZONC.BEBE` existen hoy.

    Las dos siguen fuera del catálogo porque su base tampoco está en `_FAMILIA_RESIDUAL` —son
    accesorio y ropa interior de bebé, declaradas fuera— pero demuestran que el separador no
    siempre es un espacio, y el literal viejo se lo tragaba entero.
    """
    assert _familia_base("PRENDA EXT.BEBE") == "PRENDA EXT"
    assert _familia_base("BRAGA/CALZONC.BEBE") == "BRAGA/CALZONC"
    assert _familia_base("PANTALON.BEBE") == "PANTALON"
    assert _familia_base("PANTALON-BEBE") == "PANTALON"


def test_familia_base_no_recorta_sin_separador_ni_cuando_la_familia_ES_el_rango() -> None:
    """La mitad conservadora: recortar de más inventaría una categoría, que es peor que perder una.

    `NEWBORN` es un rango de edad que Zara usa como familia entera (`NEWBORN`, `NEWBORN TRICOT`),
    no como sufijo — por eso no está en `_RANGOS_DE_EDAD`. Y una familia acabada en las mismas
    letras sin separador no se toca.
    """
    assert _familia_base("NEWBORN") == "NEWBORN"
    assert _familia_base("NEWBORN TRICOT") == "NEWBORN TRICOT"
    assert _familia_base("BEBE") == "BEBE"
    assert _familia_base("XBEBE") == "XBEBE"


def test_familia_base_no_mueve_ni_un_producto_de_los_de_hoy() -> None:
    """Endurecer no puede recategorizar nada, y esto lo fija contra las familias reales medidas.

    Son las 54 que devolvieron las 62 hojas el 14/08/2026. Lo que se compara es el **destino** en
    el catálogo, no la base: `PRENDA EXT.BEBE` cambia de base y sigue descartándose igual.
    """
    for familia, destino in [
        ("PANTALON", "pantalones"),
        ("PANTALON BEBE", "pantalones"),
        ("LEGGINGS BEBE", "pantalones"),
        ("BERMUDA BEBE", "pantalones"),
        ("JERSEY BEBE", "sudaderas"),
        ("CAMISETA BEBE", "camisetas"),
        ("PELELE BEBE", "vestidos"),
        ("FALDA BEBE", "vestidos"),
        ("PETO BEBE", "vestidos"),
        ("BODY BEBE", "ropa-interior"),
    ]:
        assert _FAMILIA_RESIDUAL.get(_familia_base(familia)) == destino, familia

    for familia in [
        "GORRO BEBE",
        "CHAQUETA BEBE",
        "CAZADORA BEBE",
        "PRENDA EXT.BEBE",
        "BRAGA/CALZONC.BEBE",
        "CHANDAL BEBE",
        "VESTIDO BEBE",
        "CAMISA BEBE",
        "NEWBORN",
        "BAMBAS",
        "",
    ]:
        assert _FAMILIA_RESIDUAL.get(_familia_base(familia)) is None, familia
