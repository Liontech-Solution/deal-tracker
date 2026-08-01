"""La huella TLS con la que Cacles nos deja entrar (ver `scraper/tls.py`).

Cloudflare devolvía 429 `local_rate_limited` a TODA petición de httpx —también desde el cluster—
mientras curl y urllib pasaban desde la misma IP con las mismas cabeceras. La única diferencia
medida fue la extensión ALPN del ClientHello. Estos tests fijan las dos mitades del arreglo: que el
contexto ignora el ALPN sin bajar la verificación, y que el cliente de la tienda lo usa.
"""

from __future__ import annotations

import os
import ssl

import pytest

from scraper.config import Config
from scraper.stores.cacles import CaclesStore
from scraper.tls import ContextoSinALPN, contexto_sin_alpn

_CFG = Config(database_url="postgresql://unused")


def test_el_contexto_ignora_el_alpn_que_impone_httpcore() -> None:
    ctx = contexto_sin_alpn()
    # httpcore llama a esto sobre el contexto recibido, justo antes del handshake y sin
    # preguntar: si dejase de ser inocuo, volveríamos a anunciar ALPN y a comer 429.
    assert ctx.set_alpn_protocols(["http/1.1", "h2"]) is None


def test_quitar_el_alpn_no_baja_la_verificacion() -> None:
    # Lo que se quita es lo que delata al cliente, no la seguridad: un contexto que no valide
    # certificado o nombre sería un arreglo mucho peor que el problema.
    ctx = contexto_sin_alpn()
    assert ctx.check_hostname is True
    assert ctx.verify_mode is ssl.CERT_REQUIRED


def test_el_cliente_de_la_tienda_usa_ese_contexto() -> None:
    # Se mira dónde ACABA el contexto (el pool de httpcore) y no solo que `_client()` no reviente:
    # el fallo que esto evita es silencioso —peticiones que salen con ALPN— y no da error, da 429.
    store = CaclesStore(_CFG)
    with store._client() as client:
        pool = client._transport._pool  # type: ignore[attr-defined]
        assert isinstance(pool._ssl_context, ContextoSinALPN)


@pytest.mark.skipif(
    os.environ.get("CACLES_LIVE") != "1",
    reason="smoke en vivo; define CACLES_LIVE=1 para ejecutarlo",
)
def test_cacles_live_acepta_nuestra_huella() -> None:
    """Una petición mínima (`limit=1`) contra la tienda real: barata y concluyente.

    Fuera del camino por defecto para no depender de la red en CI. Si esto empieza a dar 429 con
    `local_rate_limited`, Cloudflare habrá afinado la regla y el arreglo de la huella se quedó
    corto: es la señal para ir a por impersonación de navegador, no para subir el backoff.
    """
    store = CaclesStore(_CFG)
    with store._client() as client:
        resp = client.get(
            "https://www.caclesbarefoot.com/collections/infantil/products.json?limit=1&page=1"
        )
    assert resp.status_code == 200, resp.text[:80]
    assert resp.json().get("products"), "la colección infantil debería traer productos"
