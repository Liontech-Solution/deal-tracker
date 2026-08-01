"""Tests del árbol de categorías de Sfera (`--tree`, #56), con fixtures reales.

Herméticos: no necesitan navegador ni red. El árbol es la respuesta a «qué hojas existen de
verdad», que en esta tienda no se puede contestar probando rutas — una que no existe devuelve
200 con el catálogo del padre (#54), no un 404.

Los dos fixtures de `_menubar` están capturados con `showDimensions=all` el 02/08/2026 y
recortados: la faceta de Categorías va entera y las otras cinco se quedan sin `values`, que es
lo que prueba que la búsqueda por `type` acierta sin cargar aquí cientos de tallas y colores.
"""

from __future__ import annotations

from typing import Any

import pytest

from scraper.config import Config
from scraper.stores.base import ScrapeScope
from scraper.stores.sfera import (
    CATEGORIES,
    SferaStore,
    parent_path,
    parse_category_tree,
)

from .conftest import load_fixture

_CFG = Config(database_url="postgresql://unused", request_delay=0.0)


def _por_ruta(nodos: list[Any]) -> dict[str, Any]:
    return {n.path: n for n in nodos}


# --- La faceta leída sobre capturas reales --------------------------------------------------


def test_el_arbol_publica_las_hojas_que_adivinar_rutas_no_encuentra() -> None:
    """Las 10 hojas reales de `ninos/bebe-nino`, que es el porqué de toda esta issue.

    El recon de #33 encontró solo tres de ropa (`camisetas`, `camisas`, `punto-y-jerseis`)
    porque las buscó copiando nombres de la rama 6-14. Las que más productos tienen usan otro
    nombre —`pantalones-y-monos`, `bermudas-y-petos`— y por eso se cayeron del inventario.
    """
    payload = load_fixture("sfera_menubar_ninos_bebe_nino.json")
    nodos = parse_category_tree(payload, "ninos/bebe-nino")

    assert len(nodos) == 10
    assert all(n.depth == 1 for n in nodos), "todas cuelgan directamente de la raíz pedida"
    assert not any(n.has_children for n in nodos), "el rango bebé no anida más"

    rutas = _por_ruta(nodos)
    # Las que el recon a ojo SÍ encontró...
    assert rutas["ninos/bebe-nino/camisetas"].count == 26
    assert rutas["ninos/bebe-nino/camisas"].count == 9
    assert rutas["ninos/bebe-nino/punto-y-jerseis"].count == 1
    # ...y las que se le escaparon, que son justo las gordas.
    assert rutas["ninos/bebe-nino/pantalones-y-monos"].count == 24
    assert rutas["ninos/bebe-nino/bermudas-y-petos"].count == 12
    assert rutas["ninos/bebe-nino/accesorios-y-pijamas"].count == 3

    assert rutas["ninos/bebe-nino/camisetas"].title == "Camisetas"


def test_una_hoja_sin_descendencia_no_devuelve_el_rastro_hacia_arriba() -> None:
    """`ninos/mini` no tiene hijas, y la faceta contesta con sus ANCESTROS.

    Es el modo de fallo silencioso de este endpoint: si no se filtrara, `--tree ninos/mini`
    imprimiría «Sfera España» y «Niños» como si colgaran de mini. La respuesta honesta a «qué
    hay debajo» es que no hay nada.
    """
    payload = load_fixture("sfera_menubar_ninos_mini.json")
    assert parse_category_tree(payload, "ninos/mini") == []


def test_el_nodo_raiz_de_la_tienda_no_tiene_ruta_y_no_revienta() -> None:
    """El nodo «Sfera España» viene sin `slugs` ni `link`. Indexar a ciegas ahí es un KeyError."""
    payload = load_fixture("sfera_menubar_ninos_mini.json")
    facetas = payload["data"]["filters"]["_menubar"]
    valores = next(f for f in facetas if f["type"] == "categories")["values"]
    assert any("slugs" not in v for v in valores), "el fixture debe conservar ese nodo"
    # Pedido desde la raíz de la tienda, el único con ruta ('ninos') sí sale.
    assert [n.path for n in parse_category_tree(payload, "")] == ["ninos"]


def test_sin_facetas_no_es_un_error_sino_que_no_se_han_pedido() -> None:
    """Con el `showDimensions=none` de la ingesta, `_menubar` viene vacío. Eso no es un fallo."""
    payload = load_fixture("sfera_firefly_ninos_nino.json")
    assert payload["data"]["filters"]["_menubar"] == []
    assert parse_category_tree(payload, "ninos/nino") == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": {"filters": None}},
        {"data": {"filters": {}}},
        {"data": {"filters": {"_menubar": None}}},
        {"data": {"filters": {"_menubar": [{"type": "price", "values": []}]}}},
        {"data": {"filters": {"_menubar": [{"type": "categories", "values": None}]}}},
    ],
)
def test_una_respuesta_recortada_devuelve_arbol_vacio(payload: dict[str, Any]) -> None:
    assert parse_category_tree(payload, "ninos") == []


# --- Los dos campos que engañan -------------------------------------------------------------


def _faceta(*valores: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"filters": {"_menubar": [{"type": "categories", "values": list(valores)}]}}}


def test_un_count_ausente_es_none_y_no_cero() -> None:
    """«No lo dice» y «tiene 0 productos» son decisiones de cobertura distintas."""
    payload = _faceta(
        {"slugs": ["ninos", "x"], "label": "Sin dato"},
        {"slugs": ["ninos", "y"], "label": "Vacía", "count": 0},
    )
    rutas = _por_ruta(parse_category_tree(payload, "ninos"))
    assert rutas["ninos/x"].count is None
    assert rutas["ninos/y"].count == 0


def test_un_count_booleano_no_se_cuela_como_un_uno() -> None:
    """`bool` es subclase de `int` en Python: sin el guarda, `True` valdría 1 productos."""
    payload = _faceta({"slugs": ["ninos", "x"], "label": "X", "count": True})
    assert parse_category_tree(payload, "ninos")[0].count is None


def test_has_children_exige_un_booleano_de_verdad() -> None:
    """El `"False"` en texto es cierto en Python, y bajar por él sería una petición inventada."""
    payload = _faceta(
        {"slugs": ["ninos", "x"], "label": "X", "has_children": "False"},
        {"slugs": ["ninos", "y"], "label": "Y", "has_children": True},
    )
    rutas = _por_ruta(parse_category_tree(payload, "ninos"))
    assert rutas["ninos/x"].has_children is False
    assert rutas["ninos/y"].has_children is True


def test_solo_salen_los_descendientes_de_la_raiz_pedida() -> None:
    payload = _faceta(
        {"slugs": ["ninos"], "label": "la raíz misma"},
        {"slugs": ["mujer", "abrigos"], "label": "otra rama"},
        {"slugs": ["ninos", "nina"], "label": "hija"},
        {"slugs": ["ninos", "nina", "zapatos"], "label": "nieta"},
    )
    nodos = parse_category_tree(payload, "ninos")
    assert [(n.path, n.depth) for n in nodos] == [("ninos/nina", 1), ("ninos/nina/zapatos", 2)]


# --- La bajada por las ramas ----------------------------------------------------------------


class _TreeSession:
    """Sesión falsa que responde la faceta de cada ruta y apunta lo que se le ha pedido."""

    def __init__(self, por_ruta: dict[str, dict[str, Any]]) -> None:
        self._por_ruta = por_ruta
        self.pedidas: list[str] = []

    def __enter__(self) -> _TreeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def goto(self, url: str) -> int:
        return 200

    def get_json(self, url: str) -> Any:
        for ruta, payload in self._por_ruta.items():
            if f"/{ruta}/1/" in url:
                self.pedidas.append(ruta)
                return payload
        raise AssertionError(f"ruta no simulada: {url}")


def _store(por_ruta: dict[str, dict[str, Any]]) -> tuple[SferaStore, _TreeSession]:
    session = _TreeSession(por_ruta)
    return SferaStore(_CFG, session_factory=lambda: session), session  # type: ignore[arg-type]


def test_category_tree_baja_por_las_ramas_con_hijas() -> None:
    """La faceta publica UN nivel por respuesta, así que el árbol entero son varias peticiones."""
    store, session = _store(
        {
            "ninos": _faceta(
                {"slugs": ["ninos", "nina"], "label": "Niña", "has_children": True, "count": 328},
                {"slugs": ["ninos", "mini"], "label": "Mini", "count": 28},
            ),
            "ninos/nina": _faceta(
                {"slugs": ["ninos", "nina", "zapatos"], "label": "Zapatos", "count": 29}
            ),
        }
    )
    nodos = list(store.category_tree("ninos"))

    # La profundidad va referida a la raíz que se pidió, no a la de cada petición intermedia.
    assert [(n.path, n.depth) for n in nodos] == [
        ("ninos/nina", 1),
        ("ninos/nina/zapatos", 2),
        ("ninos/mini", 1),
    ]
    # A `mini` no se le pregunta: dijo que no tiene hijas.
    assert session.pedidas == ["ninos", "ninos/nina"]


def test_una_rama_que_falla_no_se_lleva_por_delante_lo_ya_leido() -> None:
    """Un 403 de Akamai a media bajada no puede costar el recon entero.

    `category_tree` deja propagar (no sabría inventarse un nodo «no pude leer esto»), pero lo
    que ya había emitido tiene que llegar a quien tira del generador — que es quien decide, y
    en `run._tree()` lo imprime con su aviso. Es el escenario NORMAL en esta tienda.
    """
    store, _ = _store(
        {
            "ninos": _faceta(
                {"slugs": ["ninos", "nina"], "label": "Niña", "has_children": True},
                {"slugs": ["ninos", "mini"], "label": "Mini"},
            ),
            # `ninos/nina` no está simulada: `_TreeSession` revienta al pedirla.
        }
    )
    leidos: list[str] = []
    with pytest.raises(AssertionError):
        for nodo in store.category_tree("ninos"):
            leidos.append(nodo.path)

    assert leidos == ["ninos/nina"], "lo emitido antes del fallo no se pierde"


def test_un_nodo_repetido_no_sale_dos_veces_en_el_informe() -> None:
    """La faceta publica un nivel por respuesta, pero no lo promete.

    Si una respuesta trajera también nietos, la bajada por el hijo intermedio los volvería a
    encontrar e inflaría el recuento de «N categorías, M sin mapear», que es justo el número
    que se mira para decidir cobertura.
    """
    store, session = _store(
        {
            "ninos": _faceta(
                {"slugs": ["ninos", "nina"], "label": "Niña", "has_children": True},
                {"slugs": ["ninos", "nina", "zapatos"], "label": "Zapatos"},  # un nieto colado
            ),
            "ninos/nina": _faceta(
                {"slugs": ["ninos", "nina", "zapatos"], "label": "Zapatos"},
            ),
        }
    )
    rutas = [n.path for n in store.category_tree("ninos")]

    assert rutas == ["ninos/nina", "ninos/nina/zapatos"]
    assert session.pedidas == ["ninos", "ninos/nina"]


def test_category_tree_no_vuelve_a_pedir_una_ruta_ya_visitada() -> None:
    """Un `has_children` que apunta hacia arriba sería un bucle infinito de peticiones."""
    store, session = _store(
        {
            "ninos": _faceta(
                {"slugs": ["ninos", "nina"], "label": "Niña", "has_children": True},
            ),
            "ninos/nina": _faceta(
                {"slugs": ["ninos", "nina", "x"], "label": "X", "has_children": True},
            ),
            "ninos/nina/x": _faceta(
                {"slugs": ["ninos", "nina"], "label": "vuelta atrás", "has_children": True},
            ),
        }
    )
    rutas = [n.path for n in store.category_tree("ninos")]

    assert rutas == ["ninos/nina", "ninos/nina/x"]
    assert session.pedidas == ["ninos", "ninos/nina", "ninos/nina/x"]


# --- El comando `--tree` --------------------------------------------------------------------


def _tree_con(store: SferaStore, root: str) -> tuple[int, str]:
    """Ejecuta el comando con una tienda ya construida (sin pasar por el registry)."""
    import io
    from contextlib import redirect_stdout

    import scraper.run as run_mod

    original = run_mod.get_store
    run_mod.get_store = lambda _slug, _cfg: store  # type: ignore[assignment]
    salida = io.StringIO()
    try:
        with redirect_stdout(salida):
            codigo = run_mod._tree(_CFG, "sfera", root)
    finally:
        run_mod.get_store = original  # type: ignore[assignment]
    return codigo, salida.getvalue()


def test_el_comando_marca_lo_que_ya_ingerimos_y_lo_que_no() -> None:
    """Es la única pregunta que justifica pedir el árbol: qué falta."""
    store, _ = _store(
        {
            "ninos/bebe-nino": _faceta(
                {"slugs": ["ninos", "bebe-nino", "zapatos"], "label": "Zapatos", "count": 6},
                {
                    "slugs": ["ninos", "bebe-nino", "ropa-deportiva"],
                    "label": "Deporte",
                    "count": 16,
                },
            )
        }
    )
    codigo, salida = _tree_con(store, "ninos/bebe-nino")

    assert codigo == 0
    assert "1 sin mapear" in salida
    marcas = {
        linea.split()[1]: linea.split()[0] for linea in salida.splitlines() if "ninos/" in linea
    }
    assert marcas["ninos/bebe-nino/zapatos"] == "✓"
    assert marcas["ninos/bebe-nino/ropa-deportiva"] == "·"


def test_el_comando_imprime_lo_leido_aunque_la_bajada_se_corte() -> None:
    """El fallo de la última rama no puede costar las peticiones que ya se gastaron.

    Es el escenario rutinario de esta tienda (un 403 suelto de Akamai) y el motivo de que este
    comando acumule a mano en vez de hacer `list()` sobre el generador.
    """
    store, _ = _store(
        {
            "ninos": _faceta(
                {"slugs": ["ninos", "nina"], "label": "Niña", "has_children": True, "count": 328},
            )
            # `ninos/nina` sin simular: la bajada revienta al pedirla.
        }
    )
    codigo, salida = _tree_con(store, "ninos")

    assert codigo == 0, "es un informe, no un vigía"
    assert "ninos/nina" in salida, "lo leído antes del corte se imprime"
    assert "se cortó antes de acabar" in salida, "y se dice que está a medias"


def test_una_hoja_sin_hijas_lo_dice_en_vez_de_callarse() -> None:
    store, _ = _store({"ninos/mini": _faceta({"slugs": ["ninos"], "label": "hacia arriba"})})
    codigo, salida = _tree_con(store, "ninos/mini")

    assert codigo == 0
    assert "no publica ninguna categoría por debajo" in salida


# --- Lo que ingerimos, cruzado con lo que existe --------------------------------------------


def test_mapped_leaves_habla_el_mismo_vocabulario_que_el_arbol() -> None:
    """Si no coincidieran, el informe marcaría como «sin mapear» hojas que sí ingerimos."""
    store = SferaStore(_CFG)
    assert set(store.mapped_leaves()) == {cat.category_path for cat in CATEGORIES}
    assert "ninos/bebe-nino/camisetas" in set(store.mapped_leaves())


def test_toda_hoja_configurada_tiene_padre_contra_el_que_compararse() -> None:
    """Sin tres segmentos, `parent_path` devuelve None y la hoja se queda SIN la red del
    espejismo, que en esta tienda es la única que hay: aquí un 404 no llega nunca."""
    for cat in CATEGORIES:
        assert parent_path(cat.category_path) is not None, cat.category_path


def test_la_ropa_de_bebe_no_estrena_ningun_ambito() -> None:
    """Las 12 hojas nuevas reutilizan ámbitos que ya existían por la rama 6-14.

    Importa porque el ámbito es la unidad con la que se acotan las BAJAS: una hoja que
    estrenara ámbito abriría superficie nueva para descatalogar, y eso no es lo que #56 pide.
    """
    de_bebe = [c for c in CATEGORIES if "bebe-" in c.category_path]
    del_resto = [c for c in CATEGORIES if "bebe-" not in c.category_path]
    ambitos_previos = {ScrapeScope(c.gender, c.section, c.category) for c in del_resto}

    assert len(de_bebe) == 14  # 12 de ropa (#56) + las 2 de calzado (#33)
    for cat in de_bebe:
        assert ScrapeScope(cat.gender, cat.section, cat.category) in ambitos_previos


def test_la_ropa_de_bebe_se_mapea_como_su_equivalente_de_6_14() -> None:
    """El vocabulario del catálogo no se parte por rango de edad: `punto-y-jerseis` es
    `sudaderas` tanto en niña de 6-14 como en bebé niña."""
    por_ruta = {c.category_path: c for c in CATEGORIES}
    esperado = {
        "ninos/bebe-nina/pantalones-y-leggings": ("niña", "ropa", "pantalones"),
        "ninos/bebe-nina/shorts-y-bermudas": ("niña", "ropa", "pantalones"),
        "ninos/bebe-nina/blusas-y-camisetas": ("niña", "ropa", "camisetas"),
        "ninos/bebe-nina/punto-y-jerseis": ("niña", "ropa", "sudaderas"),
        "ninos/bebe-nina/vestidos-y-faldas": ("niña", "ropa", "vestidos"),
        "ninos/bebe-nina/accesorios-y-pijamas": ("niña", "ropa", "ropa-interior"),
        "ninos/bebe-nino/pantalones-y-monos": ("niño", "ropa", "pantalones"),
        "ninos/bebe-nino/bermudas-y-petos": ("niño", "ropa", "pantalones"),
        "ninos/bebe-nino/camisetas": ("niño", "ropa", "camisetas"),
        "ninos/bebe-nino/camisas": ("niño", "ropa", "camisetas"),
        "ninos/bebe-nino/punto-y-jerseis": ("niño", "ropa", "sudaderas"),
        "ninos/bebe-nino/accesorios-y-pijamas": ("niño", "ropa", "ropa-interior"),
    }
    for ruta, (genero, seccion, categoria) in esperado.items():
        cat = por_ruta[ruta]
        assert (cat.gender, cat.section, cat.category) == (genero, seccion, categoria), ruta


def test_ninguna_hoja_esta_configurada_dos_veces() -> None:
    rutas = [c.category_path for c in CATEGORIES]
    assert len(rutas) == len(set(rutas))


def test_el_genero_de_una_hoja_sale_de_su_rama_no_del_slug() -> None:
    """`bebe-nina` mapea a `niña` aunque el slug no lleve la ñ; es el detalle que se cuela."""
    por_ruta = {c.category_path: c for c in CATEGORIES}
    assert por_ruta["ninos/bebe-nina/vestidos-y-faldas"].gender == "niña"
    assert por_ruta["ninos/bebe-nino/bermudas-y-petos"].gender == "niño"


def test_las_hojas_fuera_del_brief_siguen_fuera() -> None:
    """Baño, ropa deportiva y abrigos existen en el árbol y NO se ingieren, igual que en 6-14.

    Es una decisión de cobertura, no un olvido: si alguna entrara sin querer, aparecerían
    productos en una categoría del brief que no les corresponde.
    """
    configuradas = {c.category_path for c in CATEGORIES}
    for fuera in (
        "ninos/bebe-nina/bano",
        "ninos/bebe-nino/banadores-bebe",
        "ninos/bebe-nina/ropa-deportiva",
        "ninos/bebe-nino/ropa-deportiva",
        "ninos/bebe-nina/abrigos-y-cazadoras",
        "ninos/bebe-nino/abrigos-y-cazadoras",
    ):
        assert fuera not in configuradas
