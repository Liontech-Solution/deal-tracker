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

**Re-verificado el 03/08/2026** (#120), y conviene decirlo porque esa issue podría leerse como «el
arreglo de la huella sobraba»: no sobra. Con httpcore 1.0.9 el control emparejado sigue dando lo
mismo —`contexto_sin_alpn()` → 200, `ssl.create_default_context()` → 429— así que quitar el ALPN
sigue siendo necesario. Lo que #120 corrigió es otra cosa: **la marca del cuerpo no dice por qué
nos rechazan**, y este módulo llegó a afirmar que sí. Ver `tiene_marca_de_cloudflare()`.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterable

import certifi
import httpx

# Marca literal del cuerpo de los 429 que sirve Cloudflare; no viene en ninguna cabecera. OJO: la
# emite por MÁS DE UNA causa (#120), así que por sí sola no dice por qué nos rechazan.
_MARCA_CLOUDFLARE = "local_rate_limited"


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
    """El 429 que no se arregla esperando porque nunca nos dejaron entrar.

    Subclase de `HTTPStatusError` a propósito, para que todo el manejo que ya existe —los
    `except httpx.HTTPStatusError` de los stores, que convierten el fallo en «hoja sin veredicto»—
    siga valiendo sin tocarlo. Lo que aporta es el nombre y el mensaje: el diagnóstico natural ante
    un 429 («me he pasado pidiendo») es falso en este caso y encaja con los hechos, que es
    justamente lo que costó una tarde el 01/08/2026.

    **Cuándo se eleva, tras #120**: no a la primera. Solo cuando se han agotado los reintentos *y*
    el proceso no ha conseguido ni un solo 200 de ese host. Ver `tiene_marca_de_cloudflare()`.
    """


def tiene_marca_de_cloudflare(response: httpx.Response) -> bool:
    """¿Trae este 429 la marca `local_rate_limited` de Cloudflare?

    **Esto NO dice por qué nos rechazan**, y creer que sí costó la pasada de Cacles del 03/08/2026
    (#120). Antes esta función se llamaba `es_429_de_huella()` y afirmaba que la marca separaba el
    rechazo por huella TLS del 429 por presupuesto de Shopify. Medido, no lo separa:

    | | 429 de la huella | 429 del presupuesto |
    |---|---|---|
    | cuerpo | `local_rate_limited` (18 bytes) | `local_rate_limited` (18 bytes) |
    | `Retry-After` | `60` | `60` |
    | `server` | `cloudflare` | `cloudflare` |

    Son la misma respuesta byte a byte. La marca es **necesaria pero no suficiente**: descarta el
    429 genérico de otra capa, y nada más.

    Quien decide la causa es el llamante, y con el único dato que de verdad las separa: **su
    historial**. El rechazo por huella se decide en el handshake, así que si ya hubo un 200 de ese
    host, la huella está demostrablemente aceptada y un 429 posterior no puede ser eso. Ver
    `stores/cacles.py::_get_json`.
    """
    if response.status_code != 429:
        return False
    try:
        return _MARCA_CLOUDFLARE in response.text
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        # Un cuerpo ilegible no prueba nada: ante la duda, es el 429 corriente (que sí se
        # reintenta). Equivocarse hacia el reintento cuesta minutos; hacia el otro lado, una pasada.
        return False


def error_de_huella(response: httpx.Response) -> HuellaTLSRechazada:
    """Construye el error con el mensaje que dice qué mirar, para no repetir el texto por ahí.

    El mensaje afirma menos que antes de #120, y a propósito: lo que el llamante sabe al elevarlo
    es que agotó los reintentos sin conseguir un solo 200, no que la causa sea la huella. Sigue
    siendo la hipótesis principal —es la que encaja con «nunca nos dejaron entrar»— pero darla por
    hecha es lo que mandó a la pista falsa el 03/08.
    """
    return HuellaTLSRechazada(
        f"429 {_MARCA_CLOUDFLARE} en {response.request.url} en TODOS los intentos y sin un solo "
        "200 en esta ejecución: esperar no lo está arreglando, así que lo más probable es que la "
        "tienda esté rechazando la huella TLS del cliente. Comprueba que `ContextoSinALPN` "
        "(scraper/tls.py) sigue surtiendo efecto con la versión de httpcore instalada; si httpcore "
        "ha dejado de llamar a `set_alpn_protocols()`, hay que buscar otro punto donde desactivar "
        "el ALPN. Si el contexto está bien, la otra causa con esta misma respuesta es un límite de "
        "ritmo de la tienda más largo que nuestro presupuesto de reintentos.",
        request=response.request,
        response=response,
    )
