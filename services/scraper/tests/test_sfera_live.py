"""Smoke en vivo de Sfera (opta por `SFERA_LIVE=1`). Requiere navegador y red reales.

Fuera del camino por defecto de CI para no meter flakiness/dependencia de Chromium. Valida
la ruta completa del store: navegador -> firefly -> parseo -> caché (dos fases), sobre una
categoría pequeña. Necesita `playwright install chromium` (o `PLAYWRIGHT_BROWSERS_PATH`).
"""

from __future__ import annotations

import os

import pytest

from scraper.config import Config
from scraper.stores.base import DelistCandidate
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

    assert verdicts.get(live.retailer_product_id) is True
    assert verdicts.get("A999999999") is False
