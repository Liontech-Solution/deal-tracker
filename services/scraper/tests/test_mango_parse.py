"""Tests de parsing de Mango con fixtures reales (golden-file).

Capturas del 03/08/2026 de los tres endpoints que usa `stores/mango.py`, elegidas para cubrir lo
que su cabecera documenta:

- `mango_list_nina_pantalones.json` — hoja real `prendas_nina.pantalones_nina`. Trae **55 filas para
  46 productos**, que es la propiedad que obliga a agrupar por `productId` en vez de deduplicar con
  «gana la primera»: una fila del listado es un producto+color.
- `mango_list_bebe_nina_sudaderas.json` / `mango_list_bebe_nino_sudaderas.json` — la misma sudadera
  (`27062900`) publicada en la rama de niña y en la de niño. Es el cruce que la convierte en
  `unisex` (#98), y las dos hojas más pequeñas del catálogo que lo demuestran.
- `mango_list_hoja_muerta.json` — un `catalogId` inventado. A diferencia de las otras seis tiendas
  aquí la hoja muerta **da 404**, así que la fixture es el cuerpo del error y el veredicto sale del
  status.
- `mango_ficha_pantalon.html` — la ficha de `37051350`, con el payload RSC del que salen nombre,
  URL canónica, colores, tallas, stock y tachado en una sola petición.
- `mango_ficha_zapato.html` — la ficha de `37053308`, capturada el 04/08/2026 para #150: es uno de
  los **5 zapatos de los 137** cuya descripción trae una señal débil de `barefoot.py` («Puntera
  redondeada») y ninguna más. Ejerce por primera vez el camino barefoot de esta tienda, que las seis
  fixturas anteriores no tocaban por ser todas de ropa.
- `mango_menu.json` — el menú público del que sale `category_tree()`, y con él los `catalogId` de
  `CATEGORIES`: no están adivinados.

Las categorías se seleccionan **por atributos, no por índice**, para que reordenar `CATEGORIES` no
rompa los tests.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from scraper.config import Config
from scraper.stores.base import ListingEntry, ScrapeScope
from scraper.stores.mango import (
    _MAX_FICHAS_FALLIDAS,
    CATEGORIES,
    CategoryConfig,
    DetailUnavailable,
    FichaIlegible,
    MangoStore,
    _ambito,
    _descripcion,
    es_listado,
    firma_listado,
    foto_de_listado,
    ids_de_hoja,
    parse_ficha,
    parse_filas,
    producto,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CFG = Config(database_url="postgresql://unused")


def _json(nombre: str) -> object:
    return json.loads((_FIXTURES / nombre).read_text(encoding="utf-8"))


def _html(nombre: str) -> str:
    return (_FIXTURES / nombre).read_text(encoding="utf-8")


def _html_ficha(cual: str) -> Mapping[str, Any]:
    return parse_ficha(_html(f"mango_ficha_{cual}.html"))


def _hoja(gender: str, section: str, category: str, contiene: str) -> CategoryConfig:
    """Una hoja de `CATEGORIES` elegida por atributos (nunca por índice)."""
    hojas = [
        c
        for c in CATEGORIES
        if c.gender == gender
        and c.section == section
        and c.category == category
        and contiene in c.catalog_id
    ]
    assert hojas, f"no hay hoja {gender}/{section}/{category} que contenga {contiene!r}"
    return hojas[0]


# --- el listado ----------------------------------------------------------------------------


def test_una_fila_del_listado_es_producto_mas_color_no_producto() -> None:
    """La propiedad que obliga a agrupar: hay más filas que productos en la MISMA hoja.

    Si se dedujera «gana la primera» como en Cacles o C&A se tirarían colores reales del catálogo.
    """
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    productos = {f.product_id for f in filas}
    assert len(filas) > len(productos), "la fixture ya no ejerce el caso que motiva el agrupado"
    repetidos = [p for p in productos if sum(1 for f in filas if f.product_id == p) > 1]
    assert repetidos, "ningún producto aparece con dos colores en esta hoja"


def test_el_precio_no_pasa_por_binario() -> None:
    """Mango sirve `price` como float JSON; convertirlo mal mete 7.990000000000000213 en la BD."""
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    precios = [f.price for f in filas if f.price is not None]
    assert precios, "la fixture no trae un solo precio"
    for p in precios:
        assert isinstance(p, Decimal)
        assert p == Decimal(str(p)), f"{p!r} no es exacto"
        assert p.as_tuple().exponent >= -2, f"{p!r} tiene más de dos decimales"


def test_las_tallas_se_ordenan_para_que_la_huella_sea_estable() -> None:
    """El listado las sirve en orden alfabético; la huella no puede depender de ese orden."""
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    con_varias = [f for f in filas if len(f.sizes) > 1]
    assert con_varias, "la fixture no trae ninguna fila con más de una talla"
    for f in con_varias:
        assert list(f.sizes) == sorted(f.sizes)


def test_la_huella_cambia_con_el_precio_y_con_las_tallas() -> None:
    """Es lo que decide si se pide el detalle: si no distingue, la tienda deja de refrescarse."""
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    uno = [f for f in filas if f.price is not None and f.sizes][:1]
    assert uno
    base = firma_listado(uno)
    otro_precio = [type(uno[0])(**{**uno[0].__dict__, "price": uno[0].price + Decimal("1")})]
    sin_talla = [type(uno[0])(**{**uno[0].__dict__, "sizes": uno[0].sizes[:-1]})]
    assert firma_listado(otro_precio) != base
    assert firma_listado(sin_talla) != base
    assert firma_listado(uno) == base, "la huella no es estable entre llamadas"


def test_la_huella_no_depende_del_orden_de_los_colores() -> None:
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    pid = next(
        p for p in {f.product_id for f in filas} if sum(1 for f in filas if f.product_id == p) > 1
    )
    delmismo = [f for f in filas if f.product_id == pid]
    assert firma_listado(delmismo) == firma_listado(list(reversed(delmismo)))


def test_una_fila_sin_identidad_no_tumba_la_hoja() -> None:
    """Una fila rara se ignora: perder la hoja entera por ella sería mucho peor."""
    payload = {
        "items": [
            {"productId": "1", "colorId": "01", "sizes": ["6"], "price": 9.99},
            {},
            {"colorId": "02"},
        ]
    }
    filas = parse_filas(payload)
    assert [f.product_id for f in filas] == ["1"]


def test_un_payload_que_no_es_del_listado_da_lista_vacia() -> None:
    assert parse_filas({}) == []
    assert parse_filas(None) == []
    assert parse_filas({"items": "no es una lista"}) == []


def test_la_foto_se_construye_sin_pedir_nada() -> None:
    filas = parse_filas(_json("mango_list_nina_pantalones.json"))
    con_foto = [f for f in filas if f.portrait_id]
    assert con_foto, "la fixture no trae `portraitId`"
    url = foto_de_listado(con_foto[0])
    assert url is not None
    assert url.startswith("https://media.mango.com/is/image/punto/")
    assert con_foto[0].product_id in url and con_foto[0].color_id in url


# --- el cruce de géneros -------------------------------------------------------------------


def test_un_producto_en_las_dos_ramas_es_unisex() -> None:
    """La misma sudadera publicada en niña y en niño. Sin esto se quedaría en el género de la
    primera hoja leída, que es el fallo que #98 destapó en Hipercor."""
    nina = parse_filas(_json("mango_list_bebe_nina_sudaderas.json"))
    nino = parse_filas(_json("mango_list_bebe_nino_sudaderas.json"))
    comunes = {f.product_id for f in nina} & {f.product_id for f in nino}
    assert comunes, "las fixturas ya no comparten producto; el cruce no se está ejerciendo"

    hoja_nina = _hoja("niña", "ropa", "sudaderas", "babyNina")
    hoja_nino = _hoja("niño", "ropa", "sudaderas", "babyNino")
    assert _ambito([hoja_nina, hoja_nino]).gender == "unisex"
    assert _ambito([hoja_nina]).gender == "niña"
    assert _ambito([hoja_nino]).gender == "niño"


def test_la_seccion_y_la_categoria_las_fija_la_primera_hoja() -> None:
    """Cruzar géneros sí, cruzar categorías no: no hay forma de decir «las dos»."""
    pantalon = _hoja("niña", "ropa", "pantalones", "prendas_nina")
    vestido = _hoja("niña", "ropa", "vestidos", "prendas_nina")
    assert _ambito([pantalon, vestido]).category == "pantalones"


# --- la ficha ------------------------------------------------------------------------------


def test_la_ficha_da_nombre_url_y_variantes_en_una_peticion() -> None:
    ficha = parse_ficha(_html("mango_ficha_pantalon.html"))
    prod = producto(ficha, ScrapeScope("niña", "ropa", "pantalones"))
    assert prod is not None
    assert prod.retailer_product_id == "37051350"
    assert prod.name and prod.name != prod.retailer_product_id, "no se ha leído el nombre"
    assert prod.url is not None and prod.url.startswith("https://shop.mango.com/")
    assert prod.variants, "la ficha no ha producido variantes"
    for v in prod.variants:
        assert v.size, "una variante sin talla no se puede casar con un interés"
        assert v.color, "sin color no se pueden emparejar foto y precio"
        assert isinstance(v.price, Decimal)
        assert v.retailer_variant_id.startswith(prod.retailer_product_id)


def test_el_id_de_variante_es_producto_color_talla() -> None:
    """Estable y ajeno a la temporada: los tres ids los pone la tienda."""
    ficha = parse_ficha(_html("mango_ficha_pantalon.html"))
    prod = producto(ficha, ScrapeScope("niña", "ropa", "pantalones"))
    assert prod is not None
    assert len({v.retailer_variant_id for v in prod.variants}) == len(prod.variants)
    for v in prod.variants:
        assert v.retailer_variant_id.count("-") == 2


def test_el_tachado_solo_cuenta_si_es_estrictamente_mayor() -> None:
    """La guarda de Cacles, donde el tachado venía igual al precio en 248 de 428."""
    ficha = parse_ficha(_html("mango_ficha_pantalon.html"))
    prod = producto(ficha, ScrapeScope("niña", "ropa", "pantalones"))
    assert prod is not None
    con_tachado = [v for v in prod.variants if v.list_price is not None]
    assert con_tachado, "la fixture ya no trae precio anterior; el camino no se ejerce"
    for v in con_tachado:
        assert v.list_price > v.price


def test_las_fotos_se_atribuyen_a_un_color_con_variantes() -> None:
    ficha = parse_ficha(_html("mango_ficha_pantalon.html"))
    prod = producto(ficha, ScrapeScope("niña", "ropa", "pantalones"))
    assert prod is not None
    assert prod.images, "la ficha no ha producido galería"
    colores = {v.color for v in prod.variants}
    for img in prod.images:
        assert img.color in colores, "foto de un color sin variantes utilizables"
    assert prod.image_url == prod.images[0].url


def test_una_ficha_sin_payload_no_se_confunde_con_un_producto_retirado() -> None:
    """Un 200 sin producto es un cambio de la tienda, no una baja: confundirlos da bajas falsas."""
    with pytest.raises(FichaIlegible):
        parse_ficha("<html><body>nada</body></html>")
    with pytest.raises(FichaIlegible):
        parse_ficha('<script>self.__next_f.push([1,"{\\"otra\\":1}"])</script>')


def test_un_producto_sin_variantes_no_se_emite() -> None:
    assert producto({"id": "1", "colors": []}, ScrapeScope("niña", "ropa", "pantalones")) is None
    assert producto({"colors": [{"id": "01"}]}, ScrapeScope("niña", "ropa", "pantalones")) is None


# --- barefoot ------------------------------------------------------------------------------


def test_la_ropa_no_recibe_marca_barefoot_y_el_calzado_si() -> None:
    """`NULL` (no aplica) y `desconocido` (no se sabe) no son lo mismo: decide `section`."""
    ropa = producto(_html_ficha("pantalon"), ScrapeScope("niña", "ropa", "pantalones"))
    zapato = producto(_html_ficha("zapato"), ScrapeScope("niña", "zapateria", "zapatos"))
    assert ropa is not None and zapato is not None
    assert ropa.barefoot is None
    assert zapato.barefoot is not None


def test_una_sola_senal_debil_deja_el_zapato_de_mango_en_desconocido() -> None:
    """El caso que motivó #150, fijado sobre dato real de la tienda.

    Mango no etiqueta el calzado respetuoso en su árbol ni es barefoot nativa, así que decide el
    texto — y su texto describe estética, no construcción. Esta ficha trae «Puntera redondeada», que
    **sí** es una de las señales débiles de `barefoot.py`, y ninguna más: el veredicto es
    `desconocido`, no `si`. Es el sesgo deliberado del módulo (en la duda nunca `si`) ejercido sobre
    el catálogo que lo puso a prueba, y la razón por la que la zapatería de Mango no sale en el
    catálogo por defecto.
    """
    ficha = _html_ficha("zapato")
    prod = producto(ficha, ScrapeScope("niña", "zapateria", "zapatos"))
    assert prod is not None
    descripcion = _descripcion(ficha)
    assert descripcion is not None, "sin viñetas el veredicto sería `desconocido` por otro motivo"
    assert "Puntera redondeada" in descripcion, "la fixture ya no ejerce la señal débil suelta"
    assert prod.barefoot == "desconocido"


# --- las hojas -----------------------------------------------------------------------------


def test_la_hoja_muerta_da_404_y_su_cuerpo_no_parece_un_listado() -> None:
    """El veredicto sale del status; el cuerpo se guarda para que se vea que no engaña."""
    assert ids_de_hoja(_json("mango_list_hoja_muerta.json")) == []
    assert not es_listado(_json("mango_list_hoja_muerta.json"))


def test_una_hoja_vacia_sigue_teniendo_forma_de_listado() -> None:
    """55 de las 111 hojas son de rebajas y se vacían al acabar una campaña.

    Contarlas como caídas dispararía `SCRAPER_SCAN_MAX_DEAD_RATIO` (0,34) y abortaría la pasada de
    una tienda sana, además de hacer que el vigía avisara en falso cada semana. La retirada de
    verdad la dice el 404, que esta tienda sí da.
    """
    assert es_listado({"gridSize": "S", "filters": [], "items": []})
    assert es_listado(_json("mango_list_nina_pantalones.json"))
    assert not es_listado({"gridSize": "S", "filters": []})
    assert not es_listado({"items": "no es una lista"})
    assert not es_listado(None)


def test_una_hoja_vacia_no_se_cuenta_como_caida() -> None:
    store = MangoStore(_CFG, categories=[_hoja("niña", "ropa", "pantalones", "rebajas_nina")])
    cat = store._categories[0]

    class _Resp:
        @staticmethod
        def json() -> object:
            return {"gridSize": "S", "filters": [], "items": []}

    store._get = lambda *a, **k: _Resp()  # type: ignore[method-assign, assignment]
    filas = store._leer_hoja(None, cat)  # type: ignore[arg-type]
    assert filas == [], "una hoja vacía no aporta productos..."
    assert store.scan_report().leaves_failed == 0, "...pero tampoco es una hoja caída"
    assert store.scan_report().leaves_total == 1


def test_una_respuesta_sin_forma_de_listado_si_compromete_la_hoja() -> None:
    store = MangoStore(_CFG, categories=[_hoja("niña", "ropa", "pantalones", "rebajas_nina")])
    cat = store._categories[0]

    class _Resp:
        @staticmethod
        def json() -> object:
            return {"mensaje": "la API ha cambiado"}

    store._get = lambda *a, **k: _Resp()  # type: ignore[method-assign, assignment]
    assert store._leer_hoja(None, cat) is None  # type: ignore[arg-type]
    assert store.scan_report().leaves_failed == 1


# --- el circuito de corte de las fichas ----------------------------------------------------


def test_demasiadas_fichas_seguidas_ilegibles_abortan_la_pasada() -> None:
    """La red que evita la baja falsa masiva.

    `parse_ficha()` depende de la plantilla RSC de Next.js, así que un cambio de la tienda las
    rompe TODAS a la vez. Sin abortar, `ingest.py` no tocaría `last_seen_at` de esos productos y a
    las 2 pasadas los descatalogaría — con el listado diciendo que siguen ahí.
    """
    store = MangoStore(_CFG)
    entradas = [
        ListingEntry(str(i), "firma", "niña", "ropa", "pantalones")
        for i in range(_MAX_FICHAS_FALLIDAS + 3)
    ]

    class _Resp:
        text = "<html>sin payload</html>"

    store._get = lambda *a, **k: _Resp()  # type: ignore[method-assign, assignment]
    with pytest.raises(DetailUnavailable):
        list(store.fetch_details(entradas))


def test_una_ficha_ilegible_suelta_no_tumba_la_pasada() -> None:
    """El tope va de fichas SEGUIDAS: una leída reinicia la cuenta."""
    store = MangoStore(_CFG)
    bueno = _html("mango_ficha_pantalon.html")
    llamadas = {"n": 0}

    class _Resp:
        def __init__(self, texto: str) -> None:
            self.text = texto

    def _get(*a: object, **k: object) -> _Resp:
        llamadas["n"] += 1
        # una mala de cada dos: nunca llega a _MAX_FICHAS_FALLIDAS seguidas
        return _Resp("<html>rota</html>" if llamadas["n"] % 2 else bueno)

    store._get = _get  # type: ignore[method-assign, assignment]
    entradas = [ListingEntry("37051350", "f", "niña", "ropa", "pantalones") for _ in range(12)]
    assert len(list(store.fetch_details(entradas))) == 6


def test_una_ficha_que_dice_ser_otro_producto_no_se_emite() -> None:
    """Emitirla guardaría la huella bajo el id equivocado y forzaría refetch en cada pasada."""
    store = MangoStore(_CFG)

    class _Resp:
        text = _html("mango_ficha_pantalon.html")

    store._get = lambda *a, **k: _Resp()  # type: ignore[method-assign, assignment]
    # la ficha es del 37051350; se pide como si fuera otro
    entradas = [ListingEntry("99999999", "f", "niña", "ropa", "pantalones")]
    assert list(store.fetch_details(entradas)) == []


def test_el_sku_separa_sus_tres_ids() -> None:
    """Sin separador, producto+color+talla distintos podrían dar el mismo string."""
    ficha = parse_ficha(_html("mango_ficha_pantalon.html"))
    prod = producto(ficha, ScrapeScope("niña", "ropa", "pantalones"))
    assert prod is not None
    for v in prod.variants:
        assert v.sku is not None and v.sku.count("-") == 2


def test_toda_hoja_configurada_mapea_al_vocabulario_del_brief() -> None:
    secciones = {"ropa", "zapateria"}
    categorias = {"pantalones", "camisetas", "sudaderas", "vestidos", "ropa-interior", "zapatos"}
    for c in CATEGORIES:
        assert c.gender in {"niño", "niña", "unisex"}, c
        assert c.section in secciones, c
        assert c.category in categorias, c
        assert "." in c.catalog_id, f"{c.catalog_id!r} es una colección, no una hoja"


def test_las_cinco_categorias_del_brief_estan_en_los_dos_generos() -> None:
    """Y `vestidos` solo en niña, como en el resto de tiendas (el niño no lleva vestido)."""
    for categoria in ("pantalones", "camisetas", "sudaderas", "ropa-interior"):
        for genero in ("niña", "niño"):
            assert any(c.category == categoria and c.gender == genero for c in CATEGORIES), (
                f"falta {categoria} en {genero}"
            )
    assert not [c for c in CATEGORIES if c.category == "vestidos" and c.gender == "niño"]
    assert [c for c in CATEGORIES if c.category == "vestidos" and c.gender == "niña"]


def test_cada_categoria_se_recorre_en_permanente_y_en_rebajas() -> None:
    """Las rebajas no son un subconjunto de la colección permanente: 62 productos de niña solo
    están en `rebajas_nina`. Recorrer solo una perdería justo los descuentos."""
    for c in CATEGORIES:
        if c.catalog_id.startswith("prendas_"):
            hermanas = [
                o
                for o in CATEGORIES
                if o.gender == c.gender
                and o.category == c.category
                and o.catalog_id.startswith("rebajas_")
            ]
            assert hermanas, f"{c.catalog_id} no tiene hoja de rebajas equivalente"


def test_no_hay_hojas_duplicadas() -> None:
    ids = [c.catalog_id for c in CATEGORIES]
    assert len(ids) == len(set(ids))


def test_los_ambitos_declaran_su_equivalente_unisex() -> None:
    """Sin el `unisex` declarado, los productos que cruzan géneros no se descatalogan nunca."""
    ambitos = set(MangoStore(_CFG).scopes())
    reales = {(c.section, c.category) for c in CATEGORIES}
    for section, category in reales:
        assert ScrapeScope("unisex", section, category) in ambitos


# --- el árbol que publica la tienda --------------------------------------------------------


def test_las_hojas_configuradas_salen_del_menu_publicado() -> None:
    """La prueba de que `CATEGORIES` no está adivinada: cada `catalogId` está en el menú real."""
    menu = _json("mango_menu.json")
    assert isinstance(menu, dict)
    publicados: set[str] = set()

    def recorrer(nodo: object) -> None:
        if not isinstance(nodo, dict):
            return
        cid = nodo.get("catalogId")
        if isinstance(cid, str):
            publicados.add(cid)
        for hijo in nodo.get("menus") or []:
            recorrer(hijo)

    for m in menu.get("menus") or []:
        recorrer(m)

    faltan = [c.catalog_id for c in CATEGORIES if c.catalog_id not in publicados]
    assert not faltan, f"hojas que la tienda ya no publica: {faltan}"


def test_el_mundo_teen_queda_fuera() -> None:
    """Decisión de #80, no un olvido: `teen` es hermano de `ninos`, no hijo."""
    assert not [c for c in CATEGORIES if "teen" in c.catalog_id.lower().replace("preteen", "")]
