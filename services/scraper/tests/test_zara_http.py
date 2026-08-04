"""Tests de la capa HTTP del scraper: reintentos + backoff ante throttling/errores.

También el sondeo de vida (`_probe_one`) que usa la confirmación activa de bajas, y la
tolerancia a hojas de categoría retiradas (#41).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.base import ScrapeScope
from scraper.stores.zara import CategoryConfig, ZaraStore

# Sin esperas reales: delay y backoff a 0 -> el test es instantáneo.
_CFG = Config(database_url="x", request_delay=0.0, request_retries=3, retry_backoff=0.0)


def _store_with(responses: list[int]) -> tuple[ZaraStore, httpx.Client, dict[str, int]]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        status = responses[min(i, len(responses) - 1)]
        if status == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(status, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ZaraStore(_CFG), client, calls


def test_reintenta_ante_503_y_acaba_con_exito() -> None:
    store, client, calls = _store_with([503, 503, 200])
    assert store._get_json(client, "https://x/y") == {"ok": True}
    assert calls["n"] == 3  # 2 fallos + 1 éxito


def test_no_reintenta_ante_404() -> None:
    store, client, calls = _store_with([404])
    with pytest.raises(httpx.HTTPStatusError):
        store._get_json(client, "https://x/y")
    assert calls["n"] == 1  # 404 no es reintentable: falla a la primera


def test_agota_reintentos_y_propaga() -> None:
    store, client, calls = _store_with([503])
    with pytest.raises(httpx.HTTPStatusError):
        store._get_json(client, "https://x/y")
    assert calls["n"] == 4  # 1 intento + 3 reintentos


# --- #4 Confirmación activa: el endpoint de detalle como sondeo de vida ------------------


def _probe_store(handler: Any) -> tuple[ZaraStore, httpx.Client]:
    return ZaraStore(_CFG), httpx.Client(transport=httpx.MockTransport(handler))


def test_sondeo_lista_con_producto_es_vivo() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(200, json=[{"seo": {"id": 1}}]))
    assert store._probe_one(client, "123") is True


def test_sondeo_lista_vacia_es_retirado() -> None:
    """Zara responde 200 con [] cuando ya no conoce el id: es el veredicto que buscamos."""
    store, client = _probe_store(lambda _r: httpx.Response(200, json=[]))
    assert store._probe_one(client, "123") is False


def test_sondeo_404_es_retirado() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(404, text="nope"))
    assert store._probe_one(client, "123") is False


def test_sondeo_5xx_agotado_no_da_veredicto() -> None:
    """Un fallo nuestro no es prueba de retirada: sin veredicto, la ingesta no da de baja."""
    store, client = _probe_store(lambda _r: httpx.Response(503, text="nope"))
    assert store._probe_one(client, "123") is None


def test_sondeo_respuesta_inesperada_no_da_veredicto() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(200, json={"no": "es una lista"}))
    assert store._probe_one(client, "123") is None


# --- #41 Hojas de categoría retiradas: la pasada sigue, el ámbito sale de las bajas -------

# Tres hojas de ámbitos distintos, para poder comprobar cuál cae y cuál no.
_CATS = [
    CategoryConfig(111, "niña", "zapateria", "zapatos"),
    CategoryConfig(222, "niña", "ropa", "camisetas"),
    CategoryConfig(333, "niño", "ropa", "pantalones"),
]


def _listing(product_id: str) -> dict[str, Any]:
    """Payload de listado mínimo con un producto (lo que `_iter_product_nodes` busca)."""
    return {
        "productGroups": [
            {
                "elements": [
                    {
                        "seo": {"discernProductId": product_id},
                        "detail": {"colors": [{"id": "c1", "price": 1995}]},
                    }
                ]
            }
        ]
    }


def _catalog_store(status_by_cat: dict[int, int]) -> ZaraStore:
    """Tienda cuyo listado responde por categoría según `status_by_cat` (id -> status)."""

    def handler(request: httpx.Request) -> httpx.Response:
        cat_id = int(request.url.path.split("/category/")[1].split("/")[0])
        status = status_by_cat[cat_id]
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, json=_listing(f"p{cat_id}"))

    store = ZaraStore(_CFG, categories=list(_CATS))
    # El cliente lo abre `list_catalog`; se lo cambiamos por uno con transporte simulado.
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_una_hoja_404_no_impide_ingerir_las_demas() -> None:
    """El fallo que motivó #41: una hoja de 47 tumbaba las 47."""
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    ids = [e.retailer_product_id for e in store.list_catalog()]

    assert ids == ["p111", "p333"]  # la hoja muerta se salta, las vivas se ingieren


def test_el_ambito_de_la_hoja_muerta_sale_del_informe() -> None:
    """Su ámbito no puede contar como escaneado: lo no visto ahí no está retirado."""
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    list(store.list_catalog())
    report = store.scan_report()

    assert (report.leaves_total, report.leaves_failed) == (3, 1)
    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "camisetas")}
    # Y con su nombre (#155): el ámbito no identifica la hoja, así que sin el id no se sabe cuál
    # de las que lo alimentan hay que ir a buscar al árbol de la tienda.
    assert report.failed_leaves == ["222"]


def test_una_hoja_5xx_agotada_sigue_abortando_la_pasada() -> None:
    """La tolerancia es solo para hojas retiradas: un fallo transitorio no se traga."""
    store = _catalog_store({111: 200, 222: 503, 333: 200})

    with pytest.raises(httpx.HTTPStatusError):
        list(store.list_catalog())


def test_403_no_cuenta_como_hoja_retirada() -> None:
    """Un bloqueo no es una categoría que la tienda haya quitado."""
    store = _catalog_store({111: 200, 222: 403, 333: 200})

    with pytest.raises(httpx.HTTPStatusError):
        list(store.list_catalog())


def test_relistar_no_acumula_hojas_del_recorrido_anterior() -> None:
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    list(store.list_catalog())
    list(store.list_catalog())

    assert store.scan_report().leaves_total == 3


# --- #41 Chequeo preventivo de las hojas (`--check-categories`) -----------------------------


def test_check_leaves_distingue_viva_retirada_y_sin_veredicto() -> None:
    """Un 5xx agotado NO es una hoja retirada: no se puede concluir nada de él."""
    store = _catalog_store({111: 200, 222: 404, 333: 503})

    salud = {leaf.leaf: leaf.alive for leaf in store.check_leaves()}

    assert salud == {"111": True, "222": False, "333": None}


def test_check_categories_sale_distinto_de_cero_si_hay_una_hoja_retirada() -> None:
    """Es lo que hace que un CronJob de vigilancia avise en vez de callarse."""
    sana = _catalog_store({111: 200, 222: 200, 333: 200})
    rota = _catalog_store({111: 200, 222: 404, 333: 200})

    assert _check_categories_con(sana) == 0
    assert _check_categories_con(rota) == 1


def test_un_sondeo_sin_veredicto_suelto_no_hace_fallar_el_chequeo() -> None:
    """Medido contra Sfera: un chequeo normal ya trae un 403 suelto de Akamai.

    Si eso rompiera el vigía, la alarma sonaría de rutina y acabaría silenciada.
    """
    store = _catalog_store({111: 200, 222: 503, 333: 200})

    assert _check_categories_con(store) == 0


def test_un_bloqueo_total_si_hace_fallar_el_chequeo() -> None:
    """Ninguna hoja confirmada viva ya no es un blip, y no puede pasar en silencio."""
    store = _catalog_store({111: 503, 222: 503, 333: 503})

    assert _check_categories_con(store) == 1


def _check_categories_con(store: ZaraStore) -> int:
    """Ejecuta el comando con una tienda ya construida (sin pasar por el registry)."""
    import scraper.run as run_mod

    original = run_mod.get_store
    run_mod.get_store = lambda _slug, _cfg: store  # type: ignore[assignment]
    try:
        return run_mod._check_categories(_CFG, "zara")
    finally:
        run_mod.get_store = original  # type: ignore[assignment]
