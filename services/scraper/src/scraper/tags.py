"""Ejes transversales del catálogo: marcas que atraviesan las categorías del brief.

Aquí vive el **vocabulario** y el criterio de cada eje, no su detección: quien marca es cada tienda
desde su hoja de origen (ver `stores/base.py::SupportsProductTags`). Es el mismo reparto que en
`barefoot.py` —el criterio en un sitio, el dato en las tiendas—, y por la misma razón: si el
vocabulario se escribe en cada scraper, dos tiendas acaban llamando distinto a lo mismo y el filtro
del catálogo devuelve media respuesta sin que nada falle.

**Por qué no es una heurística de texto.** `barefoot` clasifica leyendo el nombre porque hay tiendas
que no dan ninguna señal, y por eso necesita un estado `desconocido`. Aquí no: o la tienda pone la
prenda en su cajón de deporte o no lo dice. La ausencia de marca no es «no se sabe», es
«esta tienda no lo declara», y eso es lo que hace que el filtro sea honesto sin tercer estado.

La contrapartida es la **cobertura**: solo cinco de las nueve tiendas publican un cajón de deporte
identificable (sfera, lefties, c-and-a, hm, mango). Zara, Hipercor, Springfield y Cacles no, así que
filtrar por `deportiva` las excluye enteras. Es una limitación del dato, no del código, y la SPA la
dice en el propio filtro en vez de esconderla.
"""

from typing import Final

# Ropa pensada para hacer deporte, que es lo que un niño necesita para educación física. La marca
# sale de la hoja de origen: es la tienda quien lo dice.
#
# **Solo aplica a `section = 'ropa'`.** El calzado deportivo ya se encuentra por la categoría
# `zapatillas` cruzada con el filtro barefoot, así que marcarlo aquí crearía dos formas de pedir lo
# mismo con resultados distintos según la tienda (ver la cabecera de `0026_product_tag.sql`).
TAG_DEPORTIVA: Final = "deportiva"

# Vocabulario cerrado. La ingesta reconcilia SOLO estos valores, así que una etiqueta que no esté
# aquí no se escribe y —más importante— tampoco se borra: si algún día otra herramienta escribe en
# `product_tag`, una pasada del scraper no se lleva su trabajo por delante.
TAGS_CONOCIDOS: Final = frozenset({TAG_DEPORTIVA})

# La sección en la que cada eje tiene sentido; `None` es «en todas». La comprueba la INGESTA, no
# cada tienda: que el calzado se quede fuera de `deportiva` es decisión del eje, y repartirla por
# los scrapers es cómo se acaba con una tienda que la respeta y otra que no.
SECCION_APLICABLE: Final[dict[str, str | None]] = {TAG_DEPORTIVA: "ropa"}
