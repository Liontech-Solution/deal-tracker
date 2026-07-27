"""Clasificación de calzado respetuoso (barefoot), compartida por todas las tiendas.

Contexto (#30): ingerimos todo el calzado, pero el catálogo solo enseña por defecto el que es
barefoot. Esa marca la escribe el scraper en `product.barefoot` con tres estados —`si` / `no` /
`desconocido`— más `NULL` para la ropa, donde la pregunta no aplica (ver `0012_add_barefoot.sql`).

**La vía preferente no es la heurística, es la propia tienda.** Zara y Lefties etiquetan el calzado
barefoot en su árbol de categorías, así que sus scrapers ya traen `category="barefoot"` y aquí sale
`si` sin mirar una sola palabra. La heurística de texto es el plan B para las tiendas que no dan esa
señal (hoy Sfera, cuyo payload no contiene ni una vez `barefoot|respetuos|descalz|minimalist`).

El sesgo es deliberado y va en una sola dirección: **en la duda, `desconocido`, nunca `si`**. Un
falso negativo esconde un zapato bueno; un falso positivo promete barefoot y sirve calzado
convencional, que es exactamente la mentira que este producto existe para no contar.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final, Literal

Barefoot = Literal["si", "no", "desconocido"]

SI: Final[Barefoot] = "si"
NO: Final[Barefoot] = "no"
DESCONOCIDO: Final[Barefoot] = "desconocido"

# Sección sobre la que la pregunta tiene sentido. La ropa se queda en NULL ("no aplica").
_SECCION_CALZADO: Final = "zapateria"

# Slug de categoría con el que un scraper declara que la tienda YA etiqueta esa hoja como barefoot.
CATEGORIA_BAREFOOT: Final = "barefoot"

# Señales FUERTES: nombran el concepto. Con una basta (si no hay ninguna negativa).
_FUERTES: Final[tuple[str, ...]] = (
    "barefoot",
    "respetuos",  # respetuoso | respetuosa | respetuosos
    "descalz",  # descalzo | descalza | descalcito
    "minimalista",
    "drop 0",
    "sin drop",
    "cero drop",
)

# Señales DÉBILES: describen rasgos de un barefoot, pero también los tiene calzado convencional
# ("suela flexible" lo dice media zapatería infantil). Hacen falta DOS para dar el `si`; una sola
# deja el producto en `desconocido`, que es donde debe quedarse lo que no está claro.
_DEBILES: Final[tuple[str, ...]] = (
    "suela flexible",
    "suela de goma flexible",
    "puntera ancha",
    "puntera redondeada",
    "horma ancha",
    "libre movimiento de los dedos",
    "plantilla extraible",
    "contrafuerte flexible",
    "contrafuerte muy flexible",
    "ligera y flexible",
)

# Señales NEGATIVAS: estructuralmente incompatibles con un zapato respetuoso. Mandan sobre todo lo
# demás — un zapato con tacón no es barefoot por mucho que el texto diga "flexible".
_NEGATIVAS: Final[tuple[str, ...]] = (
    "tacon",  # tacón | tacones
    "cuna",  # cuña | cuñas
    "plataforma",
    "alza",
    "puntera estrecha",
    "punta fina",
    "rigid",  # rígido | rígida
)

# Correcciones manuales, versionadas: (tienda, id de producto en la tienda) -> valor forzado.
#
# Es la válvula de escape para el falso positivo/negativo concreto, sin tocar la heurística ni
# esperar a un despliegue de lógica. Manda sobre todo, incluida la categoría de la tienda: si Zara
# cuelga por error un zapato convencional de su hoja BAREFOOT, aquí se arregla.
#
# Cada entrada debe llevar comentario con el porqué y la fecha, o dentro de seis meses nadie sabrá
# si sigue haciendo falta.
OVERRIDES: Final[dict[tuple[str, str], Barefoot]] = {
    # ("zara", "123456789"): NO,  # colgado de la hoja BAREFOOT pero lleva alza (27/07/2026)
}


def _patron(marcas: tuple[str, ...]) -> re.Pattern[str]:
    """Une las marcas en una alternancia anclada al INICIO de palabra.

    El ancla no es cosmética: sin ella, `alza` casa dentro de «c*alza*do» y bastaría la palabra
    "calzado" para marcar como no-barefoot media zapatería. Solo se ancla por delante, para que
    `tacon` siga cazando "tacones" y `rigid` cace "rígido"/"rígida".
    """
    return re.compile(r"\b(?:" + "|".join(re.escape(m) for m in marcas) + r")")


_RE_FUERTES: Final = _patron(_FUERTES)
_RE_DEBILES: Final = _patron(_DEBILES)
_RE_NEGATIVAS: Final = _patron(_NEGATIVAS)


def _normaliza(texto: str) -> str:
    """Minúsculas y sin acentos: `Tacón` y `tacon` son la misma señal."""
    sin_tildes = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sin_tildes if not unicodedata.combining(c))


def classify(
    *,
    retailer: str,
    retailer_product_id: str,
    section: str | None,
    category: str | None,
    texts: str | Iterable[str | None] = (),
) -> Barefoot | None:
    """Decide el valor de `product.barefoot`. `None` = no aplica (no es calzado).

    `texts` son los textos que la tienda ofrezca del producto (nombre, descripción, composición);
    se aceptan `None` y se ignoran, porque cada tienda tiene unos campos distintos y ninguno
    garantizado. El orden de decisión es de más fiable a menos:

      1. corrección manual (`OVERRIDES`) — la última palabra;
      2. categoría barefoot de la propia tienda — su etiqueta, no nuestra suposición;
      3. señal negativa en el texto — incompatible con barefoot;
      4. una señal fuerte, o dos débiles;
      5. `desconocido`.
    """
    if section != _SECCION_CALZADO:
        return None  # ropa (o sección desconocida): la pregunta no aplica

    forzado = OVERRIDES.get((retailer, retailer_product_id))
    if forzado is not None:
        return forzado

    if category == CATEGORIA_BAREFOOT:
        return SI

    partes: list[str | None] = [texts] if isinstance(texts, str) else list(texts)
    texto = _normaliza(" ".join(t for t in partes if t))
    if not texto:
        return DESCONOCIDO

    if _RE_NEGATIVAS.search(texto):
        return NO
    if _RE_FUERTES.search(texto):
        return SI
    if len(set(_RE_DEBILES.findall(texto))) >= 2:
        return SI
    return DESCONOCIDO
