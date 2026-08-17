"""Smoke en vivo de Sfera (opta por `SFERA_LIVE=1`). Requiere navegador y red reales.

Fuera del camino por defecto de CI para no meter flakiness/dependencia de Chromium. Valida
la ruta completa del store: navegador -> firefly -> parseo -> caché (dos fases), sobre una
categoría pequeña. Necesita `playwright install chromium` (o `PLAYWRIGHT_BROWSERS_PATH`).
"""

from __future__ import annotations

import os

import pytest

from scraper.config import Config
from scraper.stores.base import DelistCandidate, ProbeVerdict
from scraper.stores.sfera import CategoryConfig, SferaStore

_LIVE = os.environ.get("SFERA_LIVE") == "1"


@pytest.mark.skipif(not _LIVE, reason="smoke en vivo; define SFERA_LIVE=1 para ejecutarlo")
def test_sfera_live_smoke() -> None:
    config = Config(database_url="postgresql://unused")  # el smoke no toca la BD
    # Una sola categoría pequeña para que sea rápido y estable.
    store = SferaStore(
        config,
        categories=[CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos")],
    )
    entries = list(store.list_catalog())
    assert entries, "el listado en vivo debería traer productos"

    products = list(store.fetch_details(entries))  # se sirven desde caché
    assert len(products) == len(entries)
    product = products[0]
    assert product.retailer_product_id.startswith("A")
    assert product.gender == "niña" and product.section == "zapateria"
    assert product.variants, "cada producto debería tener variantes talla/color"
    assert all(v.price > 0 for v in product.variants)

    # La foto llega en el mismo payload del listado (sin peticiones extra). Es un campo
    # opcional de la tienda, así que se comprueba la forma de lo que llegue, no que llegue:
    # si Sfera vuelve a dejar de servirlo, el aviso debe ser este test, no un catálogo vacío.
    con_foto = [p for p in products if p.image_url]
    assert con_foto, "hoy Sfera sirve la foto en el listado; si esto falla, revisar #19"
    assert all(p.image_url and p.image_url.startswith("https://") for p in con_foto)
    assert all(p.image_url and "no-image" not in p.image_url for p in con_foto)


@pytest.mark.skipif(not _LIVE, reason="smoke en vivo; define SFERA_LIVE=1 para ejecutarlo")
def test_sfera_live_probe_alive() -> None:
    """#4: la señal de confirmación activa sigue distinguiendo vivo de retirado.

    Contrato observado en la web real: Sfera enruta la PDP por id (el slug da igual, redirige
    al canónico) y devuelve 404 con un id que no existe. Este test es la alarma si cambia.

    El canario es `A999999999` y **no un id mutado**, que es la lección de la medición de #454:
    al mutar el último dígito de tres ids vivos, dos cayeron en ids reales de OTROS productos y
    Sfera devolvió 200 sirviendo esos productos. Un canario así no prueba nada — solo vale un id
    que la tienda no pueda conocer.

    Las aserciones son sobre `ProbeVerdict` desde #197/#426: este test comparaba contra `True` y
    `False`, o sea contra la API booleana que `probe_alive` dejó de tener, y como solo corre con
    `SFERA_LIVE=1` nadie lo vio fallar.
    """
    config = Config(database_url="postgresql://unused")
    store = SferaStore(
        config,
        categories=[CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos")],
    )
    entries = list(store.list_catalog())
    assert entries, "el listado en vivo debería traer productos"
    live = next(store.fetch_details(entries[:1]))

    verdicts = store.probe_alive(
        [
            DelistCandidate(live.retailer_product_id, live.url),
            # Mismo formato de id y slug real, pero un id que Sfera no conoce.
            DelistCandidate("A999999999", "https://www.sfera.com/es/ninos/A999999999-x/"),
        ]
    )

    # Vivo o agotado, pero NUNCA retirado: es un producto que el listado acaba de servir.
    assert verdicts.get(live.retailer_product_id) in (
        ProbeVerdict.ALIVE,
        ProbeVerdict.UNBUYABLE,
    )
    assert verdicts.get("A999999999") is ProbeVerdict.DEAD


@pytest.mark.skipif(not _LIVE, reason="smoke en vivo; define SFERA_LIVE=1 para ejecutarlo")
def test_sfera_live_el_arbol_sigue_publicando_las_hojas_que_ingerimos() -> None:
    """#56: el detector de que Sfera ha reestructurado su árbol de categorías.

    Las hojas del rango bebé no se pueden adivinar copiando rutas de la rama 6-14 —usan otros
    nombres— y una ruta que deja de existir no da 404, devuelve el catálogo del padre. Así que
    el aviso de que hay que volver a mirar el árbol tiene que salir de la propia faceta.

    Solo se comprueba el rango bebé para que el smoke sea corto: son 3 peticiones (`ninos` no
    hace falta) frente a las 5 del árbol entero.
    """
    config = Config(database_url="postgresql://unused")  # el smoke no toca la BD
    store = SferaStore(config)
    mapeadas = set(store.mapped_leaves())

    for rama in ("ninos/bebe-nina", "ninos/bebe-nino"):
        publicadas = {n.path for n in store.category_tree(rama)}
        assert publicadas, f"{rama} debería publicar sus categorías"
        nuestras = {r for r in mapeadas if r.startswith(rama + "/")}
        assert nuestras <= publicadas, (
            f"hojas configuradas que {rama} ya no publica: {sorted(nuestras - publicadas)} "
            "— busca su nombre nuevo con `--tree` antes de que dejen de ingerirse en silencio"
        )
