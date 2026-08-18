"""Tests del árbol de categorías de H&M (`--tree`, #179), sobre una captura real del escaparate.

Herméticos: no necesitan navegador ni red, aunque el dato solo se pueda conseguir con Chromium
(el escaparate es Akamai y `api.hm.com` no publica el árbol).

La fixture está recortada y su cabecera dice a qué: las entradas de menú de `/kids/boys` y
`/baby/newborn` van literales, y se han dejado a propósito tres trozos que **no** son el menú —el
`<meta name="contentPath">`, un `praData` y una entrada de `/men`— porque el fallo que estos tests
vigilan es justo recoger rutas de más.
"""

from __future__ import annotations

from pathlib import Path

from scraper.config import Config
from scraper.stores.hm import CATEGORIES, HOJAS_ETIQUETA, HMStore, parse_category_tree

FIXTURES = Path(__file__).parent / "fixtures"
_CFG = Config(database_url="postgresql://unused", request_delay=0.0)


def _menu() -> str:
    return (FIXTURES / "hm_menu_escaparate.html").read_text(encoding="utf-8")


def _por_ruta(html: str, raiz: str) -> dict[str, object]:
    return {n.path: n for n in parse_category_tree(html, raiz)}


# --- Lo que el menú publica -----------------------------------------------------------------


def test_el_arbol_sale_del_menu_embebido_en_una_pagina_cualquiera() -> None:
    """La rama de niño 2-8 con sus secciones y sus hojas, leída de una página de vestidos de niña.

    Es la propiedad que hace que esto cueste UNA petición: el menú no depende de qué página se
    pida, así que el árbol entero sale de cualquiera.
    """
    rutas = _por_ruta(_menu(), "/kids/boys")

    assert "/kids/boys/clothing" in rutas
    assert "/kids/boys/clothing/trousers" in rutas
    assert "/kids/boys/shoes" in rutas


def test_la_ruta_es_el_vocabulario_de_page_id() -> None:
    """Sin esto, `mapped_leaves()` tendría que resolver contra el árbol como hace Zara.

    Las hojas de `CATEGORIES` de esta rama tienen que salir tal cual: `path` es `/es_es/…​.html` y
    lo que se emite es lo de en medio.
    """
    rutas = _por_ruta(_menu(), "/kids/boys")
    mapeadas = {c.page_id for c in CATEGORIES if c.page_id.startswith("/kids/boys/")}

    assert mapeadas, "la fixture cubre la rama de niño 2-8, que sí está en CATEGORIES"
    assert mapeadas <= rutas.keys()


def test_el_rotulo_es_el_de_la_tienda_y_viene_sin_escapes() -> None:
    """`title` es «cómo la llama la tienda» (ver `CategoryNode`), y llega escapado como JSON."""
    rutas = _por_ruta(_menu(), "/kids/boys")

    assert rutas["/kids/boys/clothing/nightwear"].title == "Pijamas"
    # `H&M Adorables` en el crudo: si no se deshace el escape, el rótulo sale ilegible.
    assert rutas["/kids/boys/h-m-adorables"].title == "H&M Adorables"


def test_una_hoja_no_tiene_hijas_y_una_seccion_si() -> None:
    rutas = _por_ruta(_menu(), "/kids/boys")

    assert rutas["/kids/boys/clothing"].has_children is True
    assert rutas["/kids/boys/clothing/trousers"].has_children is False


def test_la_profundidad_se_cuenta_desde_la_raiz_pedida() -> None:
    rutas = _por_ruta(_menu(), "/kids/boys")

    assert rutas["/kids/boys/clothing"].depth == 1
    assert rutas["/kids/boys/clothing/trousers"].depth == 2


def test_el_menu_no_declara_cuantos_productos_hay() -> None:
    """`None` es «no lo dice», que no es 0: el menú publica navegación, no inventario."""
    assert all(n.count is None for n in parse_category_tree(_menu(), "/kids/boys"))


# --- Lo que NO se recoge, que es la mitad del trabajo ----------------------------------------


def test_no_se_recogen_las_rutas_que_no_son_del_menu() -> None:
    """El regex suelto de la receta de #77 sacaba 690 rutas donde el menú publica 651.

    Las de más venían del `<meta name="contentPath">` de la propia página y de los `praData`, que
    nombran las mismas categorías con otro vocabulario. Las tres están en la fixture.
    """
    todas = {n.path for r in ("/kids", "/baby") for n in parse_category_tree(_menu(), r)}

    assert not any("kids_girls" in r for r in todas), "praData no es el menú"
    # El `contentPath` de la captura es `/es_es/kids/girls/clothing/dresses`, y esa rama NO está
    # en la fixture: si aparece, se está leyendo de donde no toca.
    assert "/kids/girls/clothing/dresses" not in todas


def test_solo_se_enumera_lo_infantil() -> None:
    """La fixture lleva una entrada de `/men` a propósito: el menú es el del sitio entero."""
    todas = {n.path for r in ("/kids", "/baby") for n in parse_category_tree(_menu(), r)}

    assert all(r.startswith(("/kids/", "/baby/")) for r in todas)


def test_la_raiz_no_se_emite_a_si_misma() -> None:
    """Solo descendientes, que es lo que dice el protocolo. El vigía además lo da por hecho."""
    assert "/kids/boys" not in {n.path for n in parse_category_tree(_menu(), "/kids/boys")}


def test_una_raiz_que_el_menu_no_publica_da_lista_vacia() -> None:
    assert parse_category_tree(_menu(), "/kids/no-existe") == []


# --- El contrato de la tienda ---------------------------------------------------------------


def test_mapped_leaves_son_las_hojas_de_categories_y_las_de_etiqueta() -> None:
    """Sin red: no resuelve contra el árbol, al revés que Zara.

    Las de etiqueta cuentan aunque no ingieran nada (#208): la pregunta de esta capa es «¿qué
    publica la tienda que no estemos mirando?», y de esas cuatro ramas sí sacamos algo —el eje
    `deportiva`—. Es además lo que las retira de `vigia.COBERTURA_DECLARADA` sin dejar hueco.
    """
    store = HMStore(_CFG)

    assert list(store.mapped_leaves()) == [c.page_id for c in CATEGORIES] + [
        h.page_id for h in HOJAS_ETIQUETA
    ]
    assert all(h.page_id.endswith("/sportswear") for h in HOJAS_ETIQUETA)


def test_las_raices_del_vigia_son_ramas_de_categories() -> None:
    """Una raíz que no fuese rama de género dejaría hojas ingeridas fuera del barrido."""
    store = HMStore(_CFG)
    raices = list(store.tree_roots())
    # Las de saldo NO cuentan, y es a propósito (#468): `/kids/last-chance/*` y
    # `/baby/last-chance/*` cuelgan de campañas que se apagan solas, así que barrerlas cada semana
    # cantaría un hueco cada vez que acaba la campaña — el falso positivo que `estacional` existe
    # para no tener. Quedan fuera del barrido y siguen en `mapped_leaves()`, que es lo que evita
    # que se señalen como no cubiertas si algún día entran por otra raíz.
    mapeadas = [c.page_id for c in CATEGORIES if not c.por_familia]

    assert all(any(m.startswith(r + "/") for m in mapeadas) for r in raices)
    # Y al revés: ninguna hoja se queda sin raíz que la cubra.
    assert all(any(m.startswith(r + "/") for r in raices) for m in mapeadas)
