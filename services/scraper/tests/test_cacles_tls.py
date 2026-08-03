"""La huella TLS con la que Cacles nos deja entrar (ver `scraper/tls.py`).

Cloudflare devolvía 429 `local_rate_limited` a TODA petición de httpx —también desde el cluster—
mientras curl y urllib pasaban desde la misma IP con las mismas cabeceras. La única diferencia
medida fue la extensión ALPN del ClientHello. Estos tests fijan las dos mitades del arreglo: que el
contexto ignora el ALPN sin bajar la verificación, y que el cliente de la tienda lo usa.

La tercera parte del fichero es **cómo se clasifica un 429**, y ahí es donde estos tests estaban
equivocados hasta #120: daban por hecho que la marca `local_rate_limited` sólo la traía el rechazo
por huella y que el 429 por presupuesto de Shopify decía otra cosa. Medido el 03/08/2026, los dos
son la misma respuesta byte a byte, así que la marca no decide nada por sí sola y el discriminante
pasó a ser el historial de la ejecución.
"""

from __future__ import annotations

import os
import ssl

import certifi
import httpx
import pytest

from scraper.config import Config
from scraper.stores.cacles import CaclesStore
from scraper.tls import (
    ContextoSinALPN,
    HuellaTLSRechazada,
    contexto_sin_alpn,
    tiene_marca_de_cloudflare,
)

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


def test_la_marca_es_necesaria_pero_no_suficiente() -> None:
    """La marca no dice la causa, sólo que el 429 lo sirvió Cloudflare (#120).

    Este test afirmaba lo contrario: que un 429 con la marca era el de la huella y que el del
    presupuesto de Shopify decía `"Too Many Requests"`. Medido el 03/08/2026 desde el cluster, el
    429 por presupuesto trae **la misma marca**, el mismo `Retry-After: 60` y el mismo cuerpo de 18
    bytes. Por eso la función ya no se llama `es_429_de_huella`: no puede responder esa pregunta.
    """
    assert tiene_marca_de_cloudflare(_respuesta(429, '{"message":"local_rate_limited"}'))
    # Un 429 sin la marca es de otra capa, y ese sí queda descartado.
    assert not tiene_marca_de_cloudflare(_respuesta(429, "Too Many Requests"))
    # Un cuerpo con la marca pero otro status no es esto: no queremos que la marca sola decida.
    assert not tiene_marca_de_cloudflare(_respuesta(503, "local_rate_limited"))


def test_el_429_con_marca_se_reintenta_en_vez_de_tirar_la_pasada() -> None:
    """Regresión de #120: el incidente exacto del 03/08/2026, que hoy debe sobrevivir.

    La primera ejecución programada de Cacles en QA hizo `200` en la página 1 y se comió un `429
    local_rate_limited` en la página 2 —el presupuesto de complejidad de Shopify, que se agota en
    dos páginas—. Como el código elevaba a la primera ante la marca, la pasada murió sin gastar ni
    uno de sus 6 reintentos y, siendo la ingesta atómica, no quedó nada.
    """
    config = Config(database_url="postgresql://unused", request_retries=6, request_delay=0)
    store = CaclesStore(config)
    esperas: list[int] = []
    store._backoff = lambda attempt, retry_after=None: esperas.append(attempt)  # type: ignore[method-assign]

    respuestas = [
        httpx.Response(429, headers={"Retry-After": "60"}, text="local_rate_limited"),
        httpx.Response(200, json={"products": [{"id": 1}]}),
    ]
    transport = httpx.MockTransport(lambda request: respuestas.pop(0))
    with httpx.Client(transport=transport) as client:
        store._huella_aceptada = True  # la página 1 ya había entrado
        payload = store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert payload == {"products": [{"id": 1}]}, "debe esperar y devolver, no elevar"
    assert esperas == [0], "y esperar exactamente una vez, honrando el Retry-After"


def test_un_200_previo_descarta_la_huella_aunque_luego_lleguen_solo_429() -> None:
    """Si nos han dejado entrar, el rechazo por huella queda descartado: se decide en el handshake.

    Aquí se agotan los reintentos igual y el error sale, pero **no** como `HuellaTLSRechazada`:
    mandar a mirar `tls.py` cuando la huella está probada es exactamente la pista falsa que costó
    la sesión del 03/08.
    """
    config = Config(database_url="postgresql://unused", request_retries=2, request_delay=0)
    store = CaclesStore(config)
    store._backoff = lambda attempt, retry_after=None: None  # type: ignore[method-assign]
    store._huella_aceptada = True

    transport = httpx.MockTransport(lambda request: httpx.Response(429, text="local_rate_limited"))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError) as exc:
        store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert not isinstance(exc.value, HuellaTLSRechazada)


def test_sin_un_solo_200_y_tras_agotar_los_reintentos_si_es_la_huella() -> None:
    """Lo que el diagnóstico del 01/08 sí quería cubrir, conservado con el disparador corregido.

    Nunca entrar es la firma del rechazo por huella, y ahí el mensaje que manda a `tls.py` vale su
    peso en oro. Lo que cambia respecto a #67 es *cuándo*: al agotar el presupuesto, no a la
    primera. Con los ajustes del cluster son ~10,5 min de los 1800 s del CronJob, y son baratos
    comparados con perder la pasada cuando la causa era otra.
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

    assert esperas == [0, 1, 2, 3, 4, 5], "primero se agota el presupuesto de reintentos"
    # El mensaje es la mitad del arreglo: manda a `tls.py`, no a subir el backoff.
    assert "tls.py" in str(exc.value)


def test_el_429_sin_marca_si_se_reintenta() -> None:
    """El control: un 429 de otra capa conserva el comportamiento de siempre."""
    config = Config(database_url="postgresql://unused", request_retries=2, request_delay=0)
    store = CaclesStore(config)
    esperas: list[int] = []
    store._backoff = lambda attempt, retry_after=None: esperas.append(attempt)  # type: ignore[method-assign]

    transport = httpx.MockTransport(lambda request: httpx.Response(429, text="Too Many Requests"))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError) as exc:
        store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert not isinstance(exc.value, HuellaTLSRechazada)
    assert esperas == [0, 1], "debe agotar los reintentos antes de rendirse"


def test_un_200_marca_la_huella_como_aceptada() -> None:
    """El estado que hace posible todo lo anterior, afirmado por separado."""
    config = Config(database_url="postgresql://unused", request_delay=0)
    store = CaclesStore(config)
    assert store._huella_aceptada is False, "se arranca sin pruebas de que nos dejen entrar"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"products": []}))
    with httpx.Client(transport=transport) as client:
        store._get_json(client, "https://www.caclesbarefoot.com/x.json")

    assert store._huella_aceptada is True


@pytest.mark.skipif(
    os.environ.get("CACLES_LIVE") != "1",
    reason="smoke en vivo; define CACLES_LIVE=1 para ejecutarlo",
)
def test_cacles_live_acepta_nuestra_huella() -> None:
    """Una petición mínima (`limit=1`) contra la tienda real: barata y concluyente.

    Fuera del camino por defecto para no depender de la red en CI. Si esto empieza a dar 429 con
    `local_rate_limited`, Cloudflare habrá afinado la regla y el arreglo de la huella se quedó
    corto: es la señal para ir a por impersonación de navegador, no para subir el backoff.

    **Concluyente sobre la huella, ciego sobre el presupuesto** (#120): `limit=1` no se acerca al
    presupuesto de complejidad ni de lejos, así que este test seguirá verde mientras la pasada real
    muere en la segunda página. Es el mismo punto ciego que el vigía —`check_leaves()` sondea igual—
    y no se arregla subiendo el `limit`, que sólo convertiría el smoke en un generador de 429.
    """
    store = CaclesStore(_CFG)
    with store._client() as client:
        resp = client.get(
            "https://www.caclesbarefoot.com/collections/infantil/products.json?limit=1&page=1"
        )
    assert resp.status_code == 200, resp.text[:80]
    assert resp.json().get("products"), "la colección infantil debería traer productos"
