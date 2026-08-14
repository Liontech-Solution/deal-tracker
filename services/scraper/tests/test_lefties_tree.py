"""Tests del árbol de categorías de Lefties (`--tree`, #179), sobre una captura real del menú.

Herméticos: no necesitan navegador ni red, aunque el menú solo se pueda conseguir con Chromium
(la tienda está tras Akamai).

La fixture está recortada y su cabecera dice a qué: va entera la rama de **niña** —que es la que
tiene hojas mapeadas, separadores, redirecciones y vistas transversales—, entera `Recién Nacido`
—una rama sin nada mapeado—, recortada `Bebé Niña`, y **una rama de adulto (Mujer)** a propósito,
porque este menú es el del sitio entero y hay que poder comprobar que pedir la raíz infantil no la
arrastra.

Hay una **segunda** fixture, `lefties_menu_bebe.json`, con las tres ramas de bebé enteras y de una
captura distinta (06/08/2026, #194). Va aparte en vez de injertada en la de arriba justo porque son
dos capturas: mezclarlas dejaría un fichero que dice una fecha y contiene dos, y las cifras de
`vigia.COBERTURA_DECLARADA` se leen contra la fecha que declaran.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scraper.config import Config
from scraper.stores.lefties import (
    CATEGORIES,
    CategoryConfig,
    LeftiesStore,
    TagLeaf,
    grid_ids_by_category,
    parse_category_tree,
)
from scraper.tags import TAG_DEPORTIVA

FIXTURES = Path(__file__).parent / "fixtures"
_CFG = Config(database_url="postgresql://unused", request_delay=0.0)

_NINOS = "1030267671"
_NINA = f"{_NINOS}/1030267672"
_ZAPATOS_NINA = f"{_NINA}/1030267718/1030272335"  # hoja mapeada en CATEGORIES


def _menu() -> dict[str, Any]:
    return json.loads((FIXTURES / "lefties_menu.json").read_text(encoding="utf-8"))


def _menu_bebe() -> dict[str, Any]:
    """Las tres ramas de bebé enteras (captura del 06/08/2026, #194)."""
    return json.loads((FIXTURES / "lefties_menu_bebe.json").read_text(encoding="utf-8"))


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


class _SesionProhibida:
    """Sesión que revienta si alguien la usa: `mapped_leaves()` no puede tocar la red."""

    def __enter__(self) -> _SesionProhibida:
        raise AssertionError("mapped_leaves() no debe abrir el navegador")

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_mapped_leaves_sale_de_categories_y_no_pide_nada() -> None:
    """`parent` + `category_id`, sin tocar el menú.

    Lo comprueba con una sesión que revienta si alguien la usa, porque es la propiedad que hace
    que `test_cobertura_declarada_no_solapa_con_lo_mapeado` siga siendo hermético en CI.
    """
    store = LeftiesStore(
        _CFG,
        categories=[
            CategoryConfig(1030272335, "niña", "zapateria", "zapatos", f"{_NINA}/1030267718")
        ],
        session_factory=_SesionProhibida,  # type: ignore[arg-type]
        tag_leaves=[],
    )

    assert list(store.mapped_leaves()) == [_ZAPATOS_NINA]


def test_las_hojas_de_etiqueta_cuentan_como_mapeadas() -> None:
    """Una rama que solo etiqueta NO es un hueco de cobertura (#180).

    Es la mitad de la pareja que hace que las dos ramas de `Ropa Deportiva` puedan salir de
    `vigia.COBERTURA_DECLARADA`: si no aparecieran aquí, el vigía las cantaría cada jueves como
    catálogo sin mirar — que es exactamente lo que decían las declaraciones que esta issue borra.
    """
    store = LeftiesStore(
        _CFG,
        categories=[],
        session_factory=_SesionProhibida,  # type: ignore[arg-type]
        tag_leaves=[TagLeaf(1030267709, f"{_NINA}/1030267677", TAG_DEPORTIVA, "niña")],
    )

    assert list(store.mapped_leaves()) == ["1030267671/1030267672/1030267677/1030267709"]


def test_el_padre_declarado_es_el_que_publica_el_menu() -> None:
    """El precio de escribir el padre en vez de resolverlo: que puede quedarse viejo en silencio.

    Esto lo convierte en ruidoso. La fixture solo trae la rama de niña, así que se comprueban las
    suyas: si una hoja se moviera de sección, su cadena dejaría de existir en el árbol y las
    cifras de `vigia.COBERTURA_DECLARADA` dejarían de significar lo que dicen.
    """
    publicadas = {n.path for n in parse_category_tree(_menu(), _NINOS)}
    de_nina = [c for c in CATEGORIES if c.parent.startswith(_NINA)]

    # 19 desde #203, que quitó la segunda hoja `barefoot` de la rama (era el alias de la de dentro
    # de `ZAPATOS`, y publicaba lo mismo).
    assert len(de_nina) == 19, "la fixture cubre la rama de niña entera"
    for cat in de_nina:
        assert f"{cat.parent}/{cat.category_id}" in publicadas, (
            f"la hoja {cat.category_id} dice colgar de {cat.parent}, y el menú no lo publica así"
        )


def test_el_padre_declarado_de_bebe_es_el_que_publica_el_menu() -> None:
    """El mismo seguro que el de arriba, para las tres ramas de bebé (#194).

    Son 36 hojas escritas a mano contra un árbol de ids opacos, y el `parent` es lo que hace que
    `mapped_leaves()` no necesite red. Si la tienda mueve una hoja de sección, aquí se entera:
    su cadena deja de existir en el menú, y las cifras de `vigia.COBERTURA_DECLARADA` dejan de
    significar lo que dicen.
    """
    publicadas = {n.path for n in parse_category_tree(_menu_bebe(), _NINOS)}
    ramas = (f"{_NINOS}/1030267674", f"{_NINOS}/1030267675", f"{_NINOS}/1030513546")
    de_bebe = [c for c in CATEGORIES if c.parent.startswith(ramas)]

    assert len(de_bebe) == 36, "las tres ramas de bebé mapeadas"
    for cat in de_bebe:
        assert f"{cat.parent}/{cat.category_id}" in publicadas, (
            f"la hoja {cat.category_id} dice colgar de {cat.parent}, y el menú no lo publica así"
        )


def test_las_hojas_de_bebe_son_hojas_con_grid_y_no_secciones() -> None:
    """Estar en el árbol no basta: una sección también lo está, y no se puede listar.

    Lo pedía la auditoría de #205, y es la mitad que al test de arriba le faltaba. Los 36 ids de
    #194 están escritos a mano contra un árbol de ids opacos, y el que apuntara a un nodo
    intermedio pasaría el test anterior y luego no listaría nada: `list_catalog()` resuelve el
    grid con este mismo `grids.get(cat.category_id)` (`lefties.py`), y `check_leaves()` la cantaría
    como retirada desde el primer día — un diagnóstico equivocado, porque la hoja no ha muerto: es
    que nunca fue una hoja.

    Que esto solo lo demostrara la pasada en vivo era el hueco: aquí cuesta cero peticiones,
    porque la fixture del menú ya trae el `content.id` de cada nodo.
    """
    grids = grid_ids_by_category(_menu_bebe())
    ramas = (f"{_NINOS}/1030267674", f"{_NINOS}/1030267675", f"{_NINOS}/1030513546")
    de_bebe = [c for c in CATEGORIES if c.parent.startswith(ramas)]

    sin_grid = [c.category_id for c in de_bebe if grids.get(c.category_id) is None]

    assert sin_grid == [], (
        f"estas hojas de bebé no tienen grid propio en el menú: {sin_grid} — son secciones, "
        "y una sección no se puede listar"
    )


def test_ninguna_hoja_del_menu_se_pide_por_id_numerico() -> None:
    """Los alias del menú quedan resueltos a uuid en los dos menús capturados (#393).

    Es la comprobación de fondo, y por eso mira **todas** las hojas y no solo las cuatro de
    barefoot: el alias es una convención de este menú —las `_VIEWALL` apuntan a su padre y las
    `_MENU` a la hoja de otra rama—, así que la regla que vale es «ningún grid es un id numérico»,
    no una lista de excepciones que envejece con el menú.
    """
    for nombre, menu in (("niña", _menu()), ("bebé", _menu_bebe())):
        grids = grid_ids_by_category(menu)
        numericos = {cid: g for cid, g in grids.items() if g.isdigit()}

        assert grids, f"premisa: la fixture de {nombre} trae hojas"
        assert numericos == {}, (
            f"en el menú de {nombre} estas hojas se pedirían por id de categoría y no por uuid de "
            f"grid: {numericos}"
        )


def test_las_cuatro_hojas_barefoot_resuelven_al_grid_que_se_midio() -> None:
    """Las tres que hay en fixtures, contra los uuid medidos en la tienda el 14/08/2026 (#393).

    Son alias las cuatro, así que son justamente las que se pedían por el id numérico. La cuarta
    (niño, `1030680206` -> `6a22fc1e-…`) no está aquí: su rama no la trae ninguna de las dos
    capturas.
    """
    assert grid_ids_by_category(_menu())[1030680692] == "d5e0b942-2772-4bdf-b67a-7f8bba20aa9e"

    bebe = grid_ids_by_category(_menu_bebe())
    assert bebe[1030680693] == "5abb612d-2aec-4c08-abb3-e15a205d9369"
    assert bebe[1030680207] == "c87ddae7-9d96-4719-9636-797a2552c7e8"


def test_la_fixture_de_bebe_no_arrastra_las_ramas_que_ya_ingeriamos() -> None:
    """Es una captura de las TRES ramas de bebé, no un segundo menú entero.

    Si algún día se recapturara sin recortar, los dos ficheros dirían cosas distintas sobre la
    rama de niña y el test de arriba pasaría a comprobarse contra la fecha equivocada.
    """
    rutas = {n.path for n in parse_category_tree(_menu_bebe(), _NINOS)}

    assert rutas, "premisa: la fixture trae árbol"
    assert not any(r.startswith(f"{_NINA}/") for r in rutas)


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
