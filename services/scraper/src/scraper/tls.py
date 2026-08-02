"""Contexto TLS para tiendas que ficharon la huella del cliente, no la IP ni el ritmo.

Medido contra Cacles el 01/08/2026: Cloudflare devolvía **429 `local_rate_limited`** a todas las
peticiones de `httpx` —también desde un pod del cluster— mientras `curl`, `wget` y `urllib` pasaban
con 200 desde la misma IP, con las mismas cabeceras byte a byte y contra el mismo edge
(`23.227.38.74`, TLS 1.3, mismo cifrado). No era el presupuesto de complejidad de Shopify: los
`shopify-complexity-score` medidos con curl eran de 230-650, nada.

La diferencia estaba en el **ClientHello**, en una sola extensión:

| cliente | JA4 | extensiones | Cacles |
|---|---|---|---|
| httpx | `t13d1713h1_ab0a1bf427ad_ecd0401ec68b` | 13 | **429** |
| urllib | `t13d1712_ab0a1bf427ad_ecd0401ec68b` | 12 | 200 |

`t13d17**13**h1` contra `t13d17**12**`: httpx anuncia ALPN y urllib no. Y **no se quita por
configuración** — `httpcore` llama a `set_alpn_protocols()` sobre el contexto que le pases, sea cual
sea (`httpcore/_sync/connection.py`), así que pasarle a httpx el contexto por defecto de urllib
seguía dando 429. De ahí la subclase: es el único punto donde se puede desactivar.

Con esto la huella pasa a ser exactamente la de urllib y la tienda responde 200. Verificado con
control emparejado, mismo proceso y 8 s entre peticiones: con ALPN 429, sin ALPN 200, dos veces
cada uno.

**Coste**: sin ALPN no se puede negociar HTTP/2, así que la conexión es HTTP/1.1. Es lo que el
scraper ya hablaba (`h2` no está instalado), o sea que no se pierde nada.

Vive aquí y no en `stores/cacles.py` porque el problema no es de esa tienda: **cualquier tienda
detrás de Cloudflare puede fichar la huella de httpx**, que es de las más conocidas. Hoy solo lo usa
`cacles`; las demás (`zara`, `lefties`) entran sin esto y no se les toca sin una medida que lo pida.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterable

import certifi
import httpx

# Marca del 429 que devuelve Cloudflare cuando rechaza al cliente por su huella, y no la tienda por
# haber pedido demasiado. Es literal del cuerpo medido el 01/08/2026; no viene en ninguna cabecera.
_MARCA_HUELLA = "local_rate_limited"


class ContextoSinALPN(ssl.SSLContext):
    """`SSLContext` que ignora el ALPN que httpcore impone en cada conexión.

    Sobrescribir el método es la única vía: httpcore lo llama después de recibir el contexto y
    antes del handshake, así que cualquier ajuste hecho al construirlo se pierde. Comprobado sobre
    httpcore 1.0.9, y en sus tres transportes (directo, proxy HTTP y SOCKS). Es un detalle interno,
    no API pública: **al subir de versión httpx/httpcore hay que volver a comprobarlo**, y la señal
    de que ha dejado de valer es el 429 con `local_rate_limited`, no un error.
    """

    def set_alpn_protocols(self, alpn_protocols: Iterable[str]) -> None:
        return None


def contexto_sin_alpn() -> ssl.SSLContext:
    """Contexto de cliente idéntico al de httpx salvo por el ALPN.

    Lo que se quita es la extensión que delata al cliente, no la seguridad — pero eso hay que
    construirlo, no darlo por hecho: `ssl.create_default_context()` (lo que usa httpx con
    `verify=True`) **endurece el contexto DESPUÉS de crearlo**, y partir del `SSLContext` pelado se
    dejaba fuera `VERIFY_X509_STRICT | VERIFY_X509_PARTIAL_CHAIN`, es decir se verificaba menos que
    el resto de tiendas. `test_verifica_igual_que_el_contexto_por_defecto_de_httpx` compara los dos
    contextos atributo a atributo para que la próxima diferencia no pase inadvertida.
    """
    ctx = ContextoSinALPN(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


class HuellaTLSRechazada(httpx.HTTPStatusError):
    """El 429 que NO se arregla esperando: la tienda ha rechazado nuestra huella TLS.

    Subclase de `HTTPStatusError` a propósito, para que todo el manejo que ya existe —los
    `except httpx.HTTPStatusError` de los stores, que convierten el fallo en «hoja sin veredicto»—
    siga valiendo sin tocarlo. Lo que aporta es el nombre y el mensaje: el diagnóstico natural ante
    un 429 («me he pasado pidiendo») es falso en este caso y encaja con los hechos, que es
    justamente lo que costó una tarde el 01/08/2026.
    """


def es_429_de_huella(response: httpx.Response) -> bool:
    """¿Es el 429 de la huella TLS, o el del presupuesto de la tienda?

    Los dos llegan como 429 y son indistinguibles por status. El de Cloudflare por huella trae
    `local_rate_limited` en el cuerpo; el de Shopify por presupuesto de complejidad, no. La
    diferencia importa porque **solo uno se arregla esperando**: ante este hay que ir a mirar
    `ContextoSinALPN` contra la versión de httpcore instalada, no a subir el backoff.
    """
    if response.status_code != 429:
        return False
    try:
        return _MARCA_HUELLA in response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        # Un cuerpo ilegible no prueba nada: ante la duda, es el 429 corriente (que sí se
        # reintenta). Equivocarse hacia el reintento cuesta minutos; hacia el otro lado, una pasada.
        return False


def error_de_huella(response: httpx.Response) -> HuellaTLSRechazada:
    """Construye el error con el mensaje que dice qué mirar, para no repetir el texto por ahí."""
    return HuellaTLSRechazada(
        f"429 {_MARCA_HUELLA} en {response.request.url}: la tienda ha rechazado la huella TLS del "
        "cliente, NO es el presupuesto de peticiones y esperar no lo arregla. Comprueba que "
        "`ContextoSinALPN` (scraper/tls.py) sigue surtiendo efecto con la versión de httpcore "
        "instalada; si httpcore ha dejado de llamar a `set_alpn_protocols()`, hay que buscar otro "
        "punto donde desactivar el ALPN.",
        request=response.request,
        response=response,
    )
