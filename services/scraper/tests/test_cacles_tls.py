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
import httpx
import pytest

from scraper.config import Config
from scraper.stores.cacles import CaclesStore
from scraper.tls import ContextoSinALPN, HuellaTLSRechazada, contexto_sin_alpn, es_429_de_huella

_CFG = Config(database_url="postgresql://unused")


def _respuesta(status: int, cuerpo: str) -> httpx.Response:
    """Respuesta con `request` puesto: `raise_for_status()` lo exige y el error lo arrastra."""
    return httpx.Response(
        status,
        text=cuerpo,
        request=httpx.Request("GET", "https://www.caclesbarefoot.com/x.json"),
    )


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


def test_distingue_el_429_de_la_huella_del_429_del_presupuesto() -> None:
    """Los dos llegan como 429 y solo el cuerpo los separa (ver cabecera de `stores/cacles.py`)."""
    assert es_429_de_huella(_respuesta(429, '{"message":"local_rate_limited"}'))
    # El de Shopify por presupuesto de complejidad: mismo status, otra causa, y ese SÍ se espera.
    assert not es_429_de_huella(_respuesta(429, "Too Many Requests"))
    # Un cuerpo con la marca pero otro status no es esto: no queremos que la marca sola decida.
    assert not es_429_de_huella(_respuesta(503, "local_rate_limited"))


def test_el_429_de_la_huella_se_eleva_sin_gastar_un_solo_reintento() -> None:
    """Esperar por este 429 está medido que no arregla nada, y quema la ventana del CronJob.

    Con los ajustes del cluster (6 reintentos, backoff 8 s, `Retry-After: 60`) reintentarlo cuesta
    ~10,5 min para acabar elevando igual. Se cuenta `_backoff` y no el reloj porque lo que se
    afirma es la decisión, no la duración.
    """
    config = Config(database_url="postgresql://unused", request_retries=6, request_delay=0)
    store = CaclesStore(config)
    esperas: list[int] = []
    store._backoff = lambda attempt, retry_after=None: esperas.append(attempt)  # type: ignore[method-assign]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429, headers={"Retry-After": "60"}, text='{"errors":"local_rate_limited"}'
        )
    )
    with httpx.Client(transport=transport) as client, pytest.raises(HuellaTLSRechazada) as exc:
        store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert esperas == [], "el 429 de huella no debe esperar: esperar no lo arregla"
    # El mensaje es la mitad del arreglo: manda a `tls.py`, no a subir el backoff.
    assert "tls.py" in str(exc.value)


def test_el_429_del_presupuesto_si_se_reintenta() -> None:
    """El control del test anterior: el otro 429 conserva el comportamiento de siempre."""
    config = Config(database_url="postgresql://unused", request_retries=2, request_delay=0)
    store = CaclesStore(config)
    esperas: list[int] = []
    store._backoff = lambda attempt, retry_after=None: esperas.append(attempt)  # type: ignore[method-assign]

    transport = httpx.MockTransport(lambda request: httpx.Response(429, text="Too Many Requests"))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError) as exc:
        store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert not isinstance(exc.value, HuellaTLSRechazada)
    assert esperas == [0, 1], "debe agotar los reintentos antes de rendirse"


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
