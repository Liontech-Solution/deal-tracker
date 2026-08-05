"""Tests del árbol de categorías de Lefties (`--tree`, #179), sobre una captura real del menú.

Herméticos: no necesitan navegador ni red, aunque el menú solo se pueda conseguir con Chromium
(la tienda está tras Akamai).

La fixture está recortada y su cabecera dice a qué: va entera la rama de **niña** —que es la que
tiene hojas mapeadas, separadores, redirecciones y vistas transversales—, entera `Recién Nacido`
—una rama sin nada mapeado—, recortada `Bebé Niña`, y **una rama de adulto (Mujer)** a propósito,
porque este menú es el del sitio entero y hay que poder comprobar que pedir la raíz infantil no la
arrastra.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scraper.config import Config
from scraper.stores.lefties import CATEGORIES, CategoryConfig, LeftiesStore, parse_category_tree

FIXTURES = Path(__file__).parent / "fixtures"
_CFG = Config(database_url="postgresql://unused", request_delay=0.0)

_NINOS = "1030267671"
_NINA = f"{_NINOS}/1030267672"
_ZAPATOS_NINA = f"{_NINA}/1030267718/1030272335"  # hoja mapeada en CATEGORIES


def _menu() -> dict[str, Any]:
    return json.loads((FIXTURES / "lefties_menu.json").read_text(encoding="utf-8"))


def _por_ruta(raiz: str) -> dict[str, Any]:
    return {n.path: n for n in parse_category_tree(_menu(), raiz)}


class _MenuSession:
    """Sesión falsa que solo sabe servir el menú (es lo único que el árbol pide)."""

    def __enter__(self) -> _MenuSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def goto(self, url: str) -> int:
        return 200

    def get_json(self, url: str) -> Any:
        assert "/menu" in url, f"el árbol no debería pedir nada más: {url}"
        return _menu()


def _store(categories: list[CategoryConfig] | None = None) -> LeftiesStore:
    return LeftiesStore(_CFG, categories=categories, session_factory=_MenuSession)  # type: ignore[arg-type]


# --- Lo que el menú publica -------------------------------------------------------------------


def test_el_arbol_sale_del_menu_que_la_pasada_ya_se_baja() -> None:
    """La rama de niña con sus secciones y sus hojas, del mismo JSON que resuelve los uuid."""
    rutas = _por_ruta(_NINOS)

    assert _NINA in rutas
    assert f"{_NINA}/1030267718" in rutas  # ZAPATOS, la sección
    assert _ZAPATOS_NINA in rutas  # y su hoja


def test_la_ruta_es_la_cadena_de_ids_desde_la_raiz_pedida() -> None:
    """Es lo que deja saber que un nodo cuelga de una hoja que ingerimos (ver Zara).

    A id suelto, los 187 nodos que cuelgan de una hoja mapeada se señalarían como huecos.
    """
    rutas = _por_ruta(_NINOS)

    assert rutas[_ZAPATOS_NINA].path.startswith(f"{_NINA}/")
    # Y pedida desde la rama de niña, la cadena arranca ahí y no en el departamento.
    assert f"{_NINA}/1030267718/1030272335" in _por_ruta(_NINA)


def test_el_rotulo_es_el_de_la_tienda() -> None:
    """`title` es «cómo la llama la tienda» (ver `CategoryNode`)."""
    assert _por_ruta(_NINOS)[_ZAPATOS_NINA].title == "Zapatos"


def test_una_hoja_no_tiene_hijas_y_una_seccion_si() -> None:
    rutas = _por_ruta(_NINOS)

    assert rutas[f"{_NINA}/1030267718"].has_children is True
    assert rutas[_ZAPATOS_NINA].has_children is False


def test_la_profundidad_se_cuenta_desde_la_raiz_pedida() -> None:
    assert _por_ruta(_NINOS)[_NINA].depth == 1
    assert _por_ruta(_NINOS)[_ZAPATOS_NINA].depth == 3
    assert _por_ruta(_NINA)[_ZAPATOS_NINA].depth == 2


def test_el_menu_no_declara_cuantos_productos_hay() -> None:
    """`None` es «no lo dice», que no es 0: el menú publica navegación, no inventario."""
    assert all(n.count is None for n in parse_category_tree(_menu(), _NINOS))


# --- Lo que NO se recoge, que es la mitad del trabajo -----------------------------------------


def test_los_separadores_del_menu_no_son_categorias() -> None:
    """28 de los 301 nodos son rayas de separación (`-`, tipo `marketing`, `key` SEPARACIÓN).

    Emitirlos serían 28 huecos de cobertura que nadie va a ingerir jamás, o 28 declaraciones que
    envejecen con el menú.
    """
    rutas = _por_ruta(_NINOS)

    assert rutas, "premisa del test: el árbol trae algo"
    assert all(n.title != "-" for n in rutas.values())
    # El separador de primer nivel bajo `Niños` está en la fixture a propósito.
    assert f"{_NINOS}/1030283571" not in rutas


def test_pedir_la_raiz_infantil_no_arrastra_el_menu_de_adulto() -> None:
    """El menú es el del sitio entero: `Mujer` está en la fixture justo para esto."""
    rutas = _por_ruta(_NINOS)

    assert all(r.startswith(f"{_NINOS}/") for r in rutas)
    assert not any(r.startswith("1030267502") for r in rutas)


def test_la_raiz_no_se_emite_a_si_misma() -> None:
    """Solo descendientes, que es lo que dice el protocolo. El vigía además lo da por hecho."""
    assert _NINOS not in _por_ruta(_NINOS)
    assert _NINA not in _por_ruta(_NINA)


def test_una_raiz_que_el_menu_no_publica_da_lista_vacia() -> None:
    assert parse_category_tree(_menu(), "1030267671/999999999") == []
    assert parse_category_tree({}, _NINOS) == []


# --- El contrato de la tienda -----------------------------------------------------------------


def test_mapped_leaves_sale_de_categories_y_no_pide_nada() -> None:
    """`parent` + `category_id`, sin tocar el menú.

    Lo comprueba con una sesión que revienta si alguien la usa, porque es la propiedad que hace
    que `test_cobertura_declarada_no_solapa_con_lo_mapeado` siga siendo hermético en CI.
    """

    class _SesionProhibida:
        def __enter__(self) -> _SesionProhibida:
            raise AssertionError("mapped_leaves() no debe abrir el navegador")

        def __exit__(self, *_exc: Any) -> None:
            return None

    store = LeftiesStore(
        _CFG,
        categories=[
            CategoryConfig(1030272335, "niña", "zapateria", "zapatos", f"{_NINA}/1030267718")
        ],
        session_factory=_SesionProhibida,  # type: ignore[arg-type]
    )

    assert list(store.mapped_leaves()) == [_ZAPATOS_NINA]


def test_el_padre_declarado_es_el_que_publica_el_menu() -> None:
    """El precio de escribir el padre en vez de resolverlo: que puede quedarse viejo en silencio.

    Esto lo convierte en ruidoso. La fixture solo trae la rama de niña, así que se comprueban las
    suyas: si una hoja se moviera de sección, su cadena dejaría de existir en el árbol y las
    cifras de `vigia.COBERTURA_DECLARADA` dejarían de significar lo que dicen.
    """
    publicadas = {n.path for n in parse_category_tree(_menu(), _NINOS)}
    de_nina = [c for c in CATEGORIES if c.parent.startswith(_NINA)]

    assert len(de_nina) == 19, "la fixture cubre la rama de niña entera"
    for cat in de_nina:
        assert f"{cat.parent}/{cat.category_id}" in publicadas, (
            f"la hoja {cat.category_id} dice colgar de {cat.parent}, y el menú no lo publica así"
        )


def test_toda_hoja_declara_de_donde_cuelga() -> None:
    """Un `parent` vacío daría una ruta `/id` que no casa con nada y no rompería nada visible."""
    sin_padre = [c.category_id for c in CATEGORIES if not c.parent]

    assert not sin_padre, f"hojas sin `parent`: {sin_padre}"


def test_el_menu_se_pide_una_sola_vez_por_instancia() -> None:
    """Sin caché, el barrido del vigía abriría un Chromium y una siembra de Akamai por raíz."""
    llamadas = 0

    class _Contadora(_MenuSession):
        def get_json(self, url: str) -> Any:
            nonlocal llamadas
            llamadas += 1
            return super().get_json(url)

    store = LeftiesStore(_CFG, session_factory=_Contadora)  # type: ignore[arg-type]
    list(store.category_tree(_NINOS))
    list(store.category_tree(_NINA))

    assert llamadas == 1


def test_la_raiz_del_vigia_cubre_todas_las_hojas_mapeadas() -> None:
    """Una raíz que no cubriera una hoja la dejaría fuera del barrido semanal."""
    store = _store()
    raices = list(store.tree_roots())

    assert raices == [_NINOS]
    for hoja in store.mapped_leaves():
        assert any(hoja.startswith(r + "/") for r in raices)
