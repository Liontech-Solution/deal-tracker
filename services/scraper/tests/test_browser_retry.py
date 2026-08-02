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
from scraper.stores.browser import BrowserHTTPError, BrowserSession, BrowserUnreachable

# Sin pausa ni backoff: lo que se mide es cuántos intentos hace, no cuánto espera.
_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0, request_retries=2)

_URL = "https://tienda.example/ninos/vestidos/"


class _RespuestaFalsa:
    def __init__(self, status: int) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self.status_text = ""

    def json(self) -> Any:
        return {"ok": True}


class _PeticionFalsa:
    """Doble de `page.request`, que es por donde va `get_json`."""

    def __init__(self, pagina: _PaginaFalsa) -> None:
        self._pagina = pagina

    def get(self, url: str, timeout: float | None = None) -> _RespuestaFalsa:
        return self._pagina._siguiente(url)


class _PaginaFalsa:
    """Doble de `Page`: consume `respuestas` en orden. Un elemento puede ser un status o elevar."""

    def __init__(self, *respuestas: int | BaseException) -> None:
        self.respuestas = list(respuestas)
        self.pedidas: list[str] = []
        self.request = _PeticionFalsa(self)

    def _siguiente(self, url: str) -> _RespuestaFalsa:
        self.pedidas.append(url)
        assert self.respuestas, f"petición no prevista: {url}"
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, BaseException):
            raise siguiente
        return _RespuestaFalsa(siguiente)

    def goto(self, url: str, wait_until: str | None = None) -> _RespuestaFalsa:
        return self._siguiente(url)

    def content(self) -> str:
        return "<html>contenido</html>"

    def wait_for_selector(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _sesion(*respuestas: int | BaseException) -> tuple[BrowserSession, _PaginaFalsa]:
    """Sesión lista para usar sin `__enter__`: sin navegador, pero con lo que los métodos miran."""
    session = BrowserSession(_CFG)
    pagina = _PaginaFalsa(*respuestas)
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
