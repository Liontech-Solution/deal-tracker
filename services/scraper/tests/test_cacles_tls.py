"""La huella TLS con la que Cacles nos deja entrar (ver `scraper/tls.py`).

Cloudflare devolvía 429 `local_rate_limited` a TODA petición de httpx —también desde el cluster—
mientras curl y urllib pasaban desde la misma IP con las mismas cabeceras. La única diferencia
medida fue la extensión ALPN del ClientHello. Estos tests fijan las dos mitades del arreglo: que el
contexto ignora el ALPN sin bajar la verificación, y que el cliente de la tienda lo usa.
"""

from __future__ import annotations

import os
import ssl

import certifi
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


def test_verifica_igual_que_el_contexto_por_defecto_de_httpx() -> None:
    """Un contexto que verifique menos sería un arreglo peor que el problema.

    Se compara atributo a atributo contra `ssl.create_default_context()` —lo que usa httpx con
    `verify=True`— en vez de afirmar dos o tres propiedades sueltas: la primera versión de este
    módulo pasaba un test así y aun así se dejaba fuera `VERIFY_X509_STRICT |
    VERIFY_X509_PARTIAL_CHAIN`, porque `create_default_context()` los añade DESPUÉS de construir el
    contexto y partir del `SSLContext` pelado no los hereda.
    """
    ctx = contexto_sin_alpn()
    referencia = ssl.create_default_context(cafile=certifi.where())
    for atributo in (
        "verify_flags",
        "verify_mode",
        "check_hostname",
        "options",
        "minimum_version",
        "maximum_version",
        "post_handshake_auth",
        "hostname_checks_common_name",
    ):
        assert getattr(ctx, atributo) == getattr(referencia, atributo), atributo


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
