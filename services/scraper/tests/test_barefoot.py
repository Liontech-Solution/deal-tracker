"""Tests de la clasificación barefoot (#30).

Lo que se fija aquí no es "que funcione el regex", es el **sesgo**: en la duda, `desconocido`.
Un falso positivo promete calzado respetuoso y sirve otra cosa, que es la única mentira que este
producto no se puede permitir.
"""

from __future__ import annotations

import pytest

from scraper.barefoot import DESCONOCIDO, NO, OVERRIDES, SI, classify


def _clasifica(**kwargs: object) -> str | None:
    base: dict[str, object] = {
        "retailer": "tienda",
        "retailer_product_id": "1",
        "section": "zapateria",
        "category": "zapatos",
    }
    return classify(**{**base, **kwargs})  # type: ignore[arg-type]


def test_la_ropa_no_aplica_y_no_es_lo_mismo_que_desconocido() -> None:
    """`None` (ropa) y `desconocido` (calzado sin clasificar) son estados distintos.

    Confundirlos rompe el filtro por defecto del catálogo, que deja pasar toda la ropa y solo el
    calzado `si`: con la ropa en `desconocido` desaparecería medio catálogo.
    """
    assert _clasifica(section="ropa", category="camisetas", texts="Pantalón vaquero") is None
    assert _clasifica(section=None, category=None) is None
    assert _clasifica(texts="") == DESCONOCIDO


def test_la_categoria_de_la_tienda_manda_sobre_el_texto() -> None:
    """Zara y Lefties etiquetan el barefoot en su árbol: eso es un dato, no una suposición."""
    assert _clasifica(category="barefoot", texts="Botas") == SI
    # Ni siquiera hace falta texto: la hoja de la tienda ya lo dice.
    assert _clasifica(category="barefoot") == SI


def test_una_senal_fuerte_basta() -> None:
    for texto in (
        "BAILARINA COLEGIAL BAREFOOT",
        "Zapatilla respetuosa de piel",
        "Sandalia minimalista",
        "Bota con suela de goma flexible con drop 0",
    ):
        assert _clasifica(texts=texto) == SI, texto


def test_una_sola_senal_debil_no_basta_pero_dos_si() -> None:
    """«Suela flexible» lo dice media zapatería infantil: sola no prueba nada."""
    assert _clasifica(texts="Zapatilla deportiva con suela flexible") == DESCONOCIDO
    assert _clasifica(texts="Zapato con puntera ancha y suela flexible") == SI


def test_las_senales_negativas_mandan_sobre_las_positivas() -> None:
    """Un zapato con tacón no es barefoot por mucho que el texto diga «flexible»."""
    assert _clasifica(texts="Sandalia de tacón con suela flexible y puntera ancha") == NO
    assert _clasifica(texts="Botín con plataforma") == NO
    assert _clasifica(texts="Zapato con cuña") == NO


def test_negativas_ancladas_a_inicio_de_palabra() -> None:
    """Sin el ancla, `alza` casa dentro de «c-alza-do» y tumba media zapatería.

    Es el fallo que más silenciosamente vaciaría el catálogo: todo lo que mencione "calzado"
    quedaría marcado como no barefoot.
    """
    assert _clasifica(texts="Calzado infantil de piel") == DESCONOCIDO
    assert _clasifica(texts="Zapato con alza interior") == NO
    # ...y el ancla solo va por delante, para que los plurales sigan cazando.
    assert _clasifica(texts="Sandalias de tacones") == NO
    assert _clasifica(texts="Suela rígida") == NO


def test_acentos_y_mayusculas_dan_igual() -> None:
    assert _clasifica(texts="TACÓN") == _clasifica(texts="tacon") == NO
    assert _clasifica(texts="Zapato RESPETUOSO") == SI


def test_acepta_varios_textos_y_tolera_los_nulos() -> None:
    """Cada tienda expone campos distintos y ninguno está garantizado."""
    assert _clasifica(texts=["Bailarina", None, "descripción con drop 0"]) == SI
    assert _clasifica(texts=[None, None]) == DESCONOCIDO


def test_la_correccion_manual_tiene_la_ultima_palabra() -> None:
    """Incluso sobre la categoría de la tienda: si la hoja BAREFOOT trae un intruso, se arregla."""
    clave = ("zara", "999")
    OVERRIDES[clave] = NO
    try:
        assert (
            classify(
                retailer="zara",
                retailer_product_id="999",
                section="zapateria",
                category="barefoot",
                texts="Zapatilla barefoot",
            )
            == NO
        )
    finally:
        del OVERRIDES[clave]


@pytest.mark.parametrize("valor", [SI, NO, DESCONOCIDO])
def test_los_tres_estados_coinciden_con_el_check_de_la_migracion(valor: str) -> None:
    """Si alguien renombra una constante, el CHECK de `0012_add_barefoot.sql` la rechazaría."""
    assert valor in {"si", "no", "desconocido"}
