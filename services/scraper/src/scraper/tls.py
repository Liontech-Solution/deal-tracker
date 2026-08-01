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


class ContextoSinALPN(ssl.SSLContext):
    """`SSLContext` que ignora el ALPN que httpcore impone en cada conexión.

    Sobrescribir el método es la única vía: httpcore lo llama después de recibir el contexto y
    antes del handshake, así que cualquier ajuste hecho al construirlo se pierde.
    """

    def set_alpn_protocols(self, alpn_protocols: Iterable[str]) -> None:
        return None


def contexto_sin_alpn() -> ssl.SSLContext:
    """Contexto de cliente equivalente al de httpx (mismos CA de `certifi`), pero sin ALPN.

    La verificación de certificado y de nombre se mantienen: lo que se quita es la extensión que
    delata al cliente, no la seguridad.
    """
    ctx = ContextoSinALPN(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx
