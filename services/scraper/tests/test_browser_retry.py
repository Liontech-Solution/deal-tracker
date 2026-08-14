"""Reintentos de `BrowserSession` ante fallos de navegación (#107).

Herméticos: no levantan Chromium. Se inyecta un doble de `Page` en la sesión ya construida, que
es lo que hacen los métodos de todas formas (`assert self._page is not None`), y se le da la
secuencia de respuestas que debe devolver.

La excepción que se eleva es la **`TimeoutError` de verdad de Playwright**, no una inventada: lo
que hay que demostrar es que la clase base que la sesión captura (`playwright.sync_api.Error`) la
cubre. Importar `playwright.sync_api` no rompe la hermeticidad — el paquete Python es dependencia
del scraper; lo que no hace falta es el binario del navegador.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from scraper.config import Config
from scraper.stores.browser import (
    BrowserHTTPError,
    BrowserSession,
    BrowserUnreachable,
    RutaVetada,
)

# Sin pausa ni backoff: lo que se mide es cuántos intentos hace, no cuánto espera.
_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0, request_retries=2)

_URL = "https://tienda.example/ninos/vestidos/"


class _RespuestaFalsa:
    def __init__(self, status: int, url: str = "") -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self.status_text = ""
        # La URL **final**, que es lo que expone `APIResponse.url` de Playwright: si hubo
        # redirección no es la que se pidió, y esa diferencia es justo lo que #282 vigila.
        self.url = url

    def json(self) -> Any:
        return {"ok": True}

    def text(self) -> str:
        return "<html>contenido</html>"


class _PeticionFalsa:
    """Doble de `page.request`, por donde van `get_json` y `pedir_html`."""

    def __init__(self, pagina: _PaginaFalsa) -> None:
        self._pagina = pagina

    def get(self, url: str, timeout: float | None = None) -> _RespuestaFalsa:
        return self._pagina._siguiente(url)


class _PaginaFalsa:
    """Doble de `Page`: consume `respuestas` en orden. Un elemento puede ser un status o elevar.

    `redirecciones` simula lo único que este doble no podía expresar y que #282 necesita: que se
    pida una URL y la respuesta venga de otra. Playwright sigue los 30x de forma transparente, así
    que desde aquí una redirección no se ve como un status — se ve como un `resp.url` distinto.
    """

    def __init__(
        self,
        *respuestas: int | BaseException,
        redirecciones: dict[str, str] | None = None,
    ) -> None:
        self.respuestas = list(respuestas)
        self.pedidas: list[str] = []
        self.rutas: list[str] = []
        self.redirecciones = redirecciones or {}
        self.request = _PeticionFalsa(self)

    def _siguiente(self, url: str) -> _RespuestaFalsa:
        self.pedidas.append(url)
        assert self.respuestas, f"petición no prevista: {url}"
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, BaseException):
            raise siguiente
        return _RespuestaFalsa(siguiente, self.redirecciones.get(url, url))

    def goto(self, url: str, wait_until: str | None = None) -> _RespuestaFalsa:
        return self._siguiente(url)

    def route(self, patron: str, handler: Any) -> None:
        self.rutas.append(patron)

    def content(self) -> str:
        return "<html>contenido</html>"

    def wait_for_selector(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _sesion(
    *respuestas: int | BaseException,
    redirecciones: dict[str, str] | None = None,
) -> tuple[BrowserSession, _PaginaFalsa]:
    """Sesión lista para usar sin `__enter__`: sin navegador, pero con lo que los métodos miran."""
    session = BrowserSession(_CFG)
    pagina = _PaginaFalsa(*respuestas, redirecciones=redirecciones)
    session._page = pagina  # type: ignore[assignment]
    session._pw_error = PlaywrightError  # lo que hace `__enter__` al abrir el navegador
    return session, pagina


def test_la_timeout_de_playwright_la_cubre_la_clase_base_que_se_captura() -> None:
    """El supuesto sobre el que se apoya todo lo demás, escrito para que un bump lo rompa aquí."""
    assert issubclass(PlaywrightTimeout, PlaywrightError)


# --- get_html: el camino de Hipercor -----------------------------------------------------------


def test_get_html_reintenta_un_timeout_y_sale_adelante() -> None:
    session, pagina = _sesion(PlaywrightTimeout("Timeout 45000ms exceeded"), 200)

    status, html = session.get_html(_URL)

    assert (status, html) == (200, "<html>contenido</html>")
    assert pagina.pedidas == [_URL, _URL], "el timeout tiene que costar un reintento, no la pasada"


def test_get_html_agota_los_reintentos_y_eleva_browser_unreachable() -> None:
    """Agotado el crédito, «no he podido verlo» es un tipo propio: NO es un 404 ni un catálogo."""
    session, pagina = _sesion(*[PlaywrightTimeout("Timeout") for _ in range(3)])

    with pytest.raises(BrowserUnreachable) as exc:
        session.get_html(_URL)

    assert exc.value.url == _URL
    assert "TimeoutError" in str(exc.value), "el mensaje tiene que decir qué pasó de verdad"
    assert len(pagina.pedidas) == 3  # request_retries=2 -> 1 intento + 2 reintentos


def test_get_html_sigue_reintentando_los_status_que_ya_reintentaba() -> None:
    """No regresión: el bucle de 429/5xx que ya existía no lo ha cambiado el camino nuevo."""
    session, pagina = _sesion(429, 503, 200)

    status, _ = session.get_html(_URL)

    assert status == 200
    assert len(pagina.pedidas) == 3


def test_get_html_devuelve_el_404_sin_reintentarlo() -> None:
    """Un 404 es información de la tienda (producto retirado), no un fallo que insistir."""
    session, pagina = _sesion(404)

    status, _ = session.get_html(_URL)

    assert status == 404
    assert len(pagina.pedidas) == 1


# --- goto y get_json: el camino de Sfera -------------------------------------------------------


def test_goto_reintenta_el_fallo_de_red() -> None:
    """La siembra de cookies de Sfera iba a pelo: si elevaba, se llevaba la hoja de detrás."""
    session, pagina = _sesion(PlaywrightError("net::ERR_CONNECTION_RESET"), 200)

    assert session.goto(_URL) == 200
    assert len(pagina.pedidas) == 2


def test_goto_agotado_eleva_browser_unreachable() -> None:
    session, _ = _sesion(*[PlaywrightError("net::ERR_CONNECTION_RESET") for _ in range(3)])

    with pytest.raises(BrowserUnreachable):
        session.goto(_URL)


def test_get_json_reintenta_el_timeout_y_conserva_el_error_de_status() -> None:
    session, pagina = _sesion(PlaywrightTimeout("Timeout"), 200)

    assert session.get_json(_URL) == {"ok": True}
    assert len(pagina.pedidas) == 2

    # Y un 404 sigue saliendo como `BrowserHTTPError`: es la señal con la que Sfera distingue una
    # hoja retirada, y confundirla con un fallo de red dejaría de dar bajas legítimas.
    session, _ = _sesion(404)
    with pytest.raises(BrowserHTTPError) as exc:
        session.get_json(_URL)
    assert exc.value.status == 404


# --- El veto de rutas por el camino de `page.request` (#282) ------------------------------------
#
# Hasta #282 no había ni un test aquí, y `pedir_html()` no tenía ninguno en absoluto: `bloquear()`
# registraba su patrón en `route()`, que no intercepta `page.request`, así que nada se caía si una
# redirección se llevaba la petición a una ruta vetada. Y el veto de Hipercor no es una
# optimización — es el `Disallow: /api` de su `robots.txt`, y el diseño entero de ese scraper sale
# de respetarlo.

_VETADO = "**/api/**"
_REJILLA = "https://tienda.example/ninos/zapatos/"
_BAJO_VETO = "https://tienda.example/api/firefly/productos"


def _sesion_con_veto(
    *respuestas: int | BaseException,
    redirecciones: dict[str, str] | None = None,
) -> tuple[BrowserSession, _PaginaFalsa]:
    session, pagina = _sesion(*respuestas, redirecciones=redirecciones)
    session.bloquear(_VETADO)
    return session, pagina


def test_bloquear_registra_el_patron_en_la_sesion_ademas_de_en_route() -> None:
    """Que la sesión lo sepa es la condición de todo lo demás: `route()` sola no basta."""
    session, pagina = _sesion_con_veto()

    assert pagina.rutas == [_VETADO], "sigue registrándose en Playwright, que gobierna lo navegado"
    assert session._vetados == [_VETADO], "y ahora también en la sesión, que gobierna page.request"


def test_una_redireccion_a_ruta_vetada_eleva_en_vez_de_colarse() -> None:
    """El caso de #282: se pide una rejilla legítima y la tienda redirige bajo `/api`."""
    session, pagina = _sesion_con_veto(200, redirecciones={_REJILLA: _BAJO_VETO})

    with pytest.raises(RutaVetada) as exc:
        session.pedir_html(_REJILLA)

    # La petición SÍ se hizo: esto detecta, no previene, y el mensaje no debe sugerir lo contrario.
    assert pagina.pedidas == [_REJILLA]
    assert exc.value.url == _BAJO_VETO
    assert exc.value.pedida == _REJILLA
    assert exc.value.patron == _VETADO
    assert "redirigió" in str(exc.value)


def test_la_ruta_vetada_no_es_un_fallo_de_red_y_no_se_reintenta() -> None:
    """Quien llama ramifica por tipo, así que esto no puede llegarle como `BrowserUnreachable`.

    Y no se reintenta: repetir una petición que incumple el `robots.txt` la incumple otras dos
    veces. Con `request_retries=2` una sola entrada en `respuestas` basta para demostrarlo — si
    hubiera reintento, el doble reventaría con «petición no prevista».
    """
    session, pagina = _sesion_con_veto(200, redirecciones={_REJILLA: _BAJO_VETO})

    with pytest.raises(RutaVetada) as exc:
        session.pedir_html(_REJILLA)

    assert not isinstance(exc.value, BrowserUnreachable | BrowserHTTPError)
    assert len(pagina.pedidas) == 1


def test_una_url_vetada_de_entrada_ni_llega_a_pedirse() -> None:
    """La otra mitad: si es la tienda quien construye la URL vetada, esto sí es prevención."""
    session, pagina = _sesion_con_veto(200)

    with pytest.raises(RutaVetada) as exc:
        session.pedir_html(_BAJO_VETO)

    assert pagina.pedidas == [], "no debe salir la petición"
    assert exc.value.pedida == _BAJO_VETO
    assert "redirigió" not in str(exc.value)


def test_una_redireccion_benigna_no_molesta() -> None:
    """La canonicalización de barra final es el 30x más común y no puede tumbar una pasada.

    Es exactamente el motivo por el que esto no usa `max_redirects=0`, que Playwright ofrece pero
    elevando ante cualquier redirección.
    """
    sin_barra = "https://tienda.example/ninos/zapatos"
    session, _ = _sesion_con_veto(200, redirecciones={sin_barra: _REJILLA})

    assert session.pedir_html(sin_barra) == (200, "<html>contenido</html>")


def test_sin_bloquear_no_hay_veto_que_valga() -> None:
    """Sfera y Lefties entran por aquí y no llaman a `bloquear()`: para ellas esto es no-op."""
    session, pagina = _sesion(200, redirecciones={_REJILLA: _BAJO_VETO})

    assert session.pedir_html(_REJILLA) == (200, "<html>contenido</html>")
    assert pagina.pedidas == [_REJILLA]


def test_get_json_comparte_el_veto_porque_comparte_transporte() -> None:
    """La asimetría es del transporte, no de una tienda: arreglar solo `pedir_html` la deja a
    medias."""
    session, _ = _sesion_con_veto(200, redirecciones={_REJILLA: _BAJO_VETO})

    with pytest.raises(RutaVetada):
        session.get_json(_REJILLA)


def test_el_veto_casa_exactamente_lo_que_casa_route() -> None:
    """El emparejado va con `fnmatch` y no con el interno de Playwright, así que hay que fijarlo.

    Los casos son los que se contrastaron contra `glob_to_regex_pattern` el 14/08/2026: coinciden
    en los seis. El último es el que más cuesta creerse y por eso está escrito — `**/api/**` **no**
    casa `/api` sin barra final, ni en Playwright ni aquí. Si esto dejara de cumplirse, el veto
    significaría una cosa al navegar y otra al pedir, que es peor que no tenerlo.
    """
    session, _ = _sesion_con_veto()

    for url in (
        "https://tienda.example/api/firefly",
        "https://tienda.example/api/",
        "https://tienda.example/dinamico/api/x",
    ):
        with pytest.raises(RutaVetada):
            session._comprobar_veto(url)

    for url in (
        "https://tienda.example/ninos/zapatos/",
        "https://tienda.example/apianas/x",
        "https://tienda.example/api",
    ):
        session._comprobar_veto(url)  # no eleva
