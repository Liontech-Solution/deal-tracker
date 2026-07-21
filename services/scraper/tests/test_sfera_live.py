"""Smoke en vivo de Sfera (opta por `SFERA_LIVE=1`). Requiere navegador y red reales.

Fuera del camino por defecto de CI para no meter flakiness/dependencia de Chromium. Valida
la ruta completa del store: navegador -> firefly -> parseo -> caché (dos fases), sobre una
categoría pequeña. Necesita `playwright install chromium` (o `PLAYWRIGHT_BROWSERS_PATH`).
"""

from __future__ import annotations

import os

import pytest

from scraper.config import Config
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
