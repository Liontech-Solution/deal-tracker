"""Tests de la capa HTTP del scraper: reintentos + backoff ante throttling/errores.

También el sondeo de vida (`_probe_one`) que usa la confirmación activa de bajas.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.zara import ZaraStore

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
