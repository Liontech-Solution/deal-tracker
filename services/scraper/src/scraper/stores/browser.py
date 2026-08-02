"""Sesión de navegador headless (Playwright) para tiendas tras anti-bot (Akamai).

Algunas tiendas (p.ej. Sfera) sirven el HTML de documento con buenas cabeceras, pero
protegen sus APIs de listado/paginación con **Akamai Bot Manager**: exigen cookies
(`_abck`/`bm_sz`) que solo se obtienen ejecutando el sensor JS. Un cliente HTTP plano
(httpx) no las consigue. Este módulo levanta un **Chromium real** que ejecuta ese sensor;
como Akamai liga la validez de las cookies al *fingerprint* del navegador, TODAS las
peticiones de esas tiendas van por aquí:

  - `goto(url)`   — navega a una página de documento (siembra las cookies del origen).
  - `get_json(url)` — pide una API del mismo origen con `page.request` (mismo fingerprint
    + cookies que el navegador), con reintentos/backoff como el cliente httpx de Zara.

Se importa Playwright de forma **perezosa**: el paquete Python es una dependencia, pero el
binario de Chromium solo hace falta en ejecución real (los tests de parseo no lo necesitan).
"""

from __future__ import annotations

import contextlib
import random
import time
from collections.abc import Collection
from types import TracebackType
from typing import TYPE_CHECKING, Any

from ..config import Config

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

# Códigos que merece la pena reintentar (throttling / errores transitorios del servidor).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Parche mínimo anti-automatización: oculta los delatores más comunes que inspecciona el
# sensor de bots antes de dejar pasar. Suficiente para Sfera con Chromium en headless=new.
_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['es-ES', 'es', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""

# Flags de lanzamiento: desactiva la marca de automatización y hace Chromium viable en
# contenedor. `--headless=new` usa el headless "moderno" del Chromium COMPLETO (mucho menos
# detectable que el viejo headless-shell), evitando el bloqueo de Akamai.
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


class BrowserHTTPError(RuntimeError):
    """Respuesta no-OK de `get_json`, con el status accesible.

    Existe para que quien llama pueda distinguir una hoja retirada (404) de un fallo del que no
    se puede concluir nada, igual que hace Zara con `httpx.HTTPStatusError`.
    """

    def __init__(self, status: int, url: str, status_text: str = "") -> None:
        super().__init__(f"GET {url} -> HTTP {status} {status_text}".rstrip())
        self.status = status
        self.url = url


class BrowserSession:
    """Context manager que abre un Chromium con perfil realista y lo cierra al salir."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self) -> BrowserSession:
        from playwright.sync_api import sync_playwright

        args = list(_LAUNCH_ARGS)
        if self._config.browser_headless:
            # headless=False en Playwright selecciona el binario Chromium completo; el modo
            # headless real lo activa este flag (evita el headless-shell, más detectable).
            args.append("--headless=new")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,
            channel=self._config.browser_channel,
            args=args,
        )
        self._context = self._browser.new_context(
            user_agent=self._config.user_agent,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
        self._context.add_init_script(_STEALTH_INIT)
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._config.browser_nav_timeout * 1000)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                with contextlib.suppress(Exception):
                    closer.close()
        if self._pw is not None:
            self._pw.stop()

    def _polite_pause(self) -> None:
        """Pausa base entre peticiones con jitter (una cadencia fija es más detectable)."""
        base = self._config.request_delay
        if base > 0:
            time.sleep(base * random.uniform(0.5, 1.5))

    def _backoff(self, attempt: int) -> None:
        """Espera exponencial con jitter ante throttling/errores transitorios."""
        wait = self._config.retry_backoff * (2**attempt)
        time.sleep(wait * random.uniform(0.8, 1.2))

    def goto(self, url: str) -> int:
        """Navega a una página de documento (siembra cookies de Akamai). Devuelve el status."""
        assert self._page is not None, "usar dentro del context manager"
        self._polite_pause()
        resp = self._page.goto(url, wait_until="domcontentloaded")
        return resp.status if resp is not None else 0

    def bloquear(self, patron: str) -> None:
        """Aborta en el navegador toda petición que case con `patron` (glob de Playwright).

        Existe para que una prohibición del `robots.txt` la garantice el código y no la buena
        intención: en Hipercor `/api` está vetado, y una página puede pedirlo al hidratarse sin
        que el scraper lo escriba en ninguna URL. Bloqueándolo aquí, si alguna vez el dato
        dependiera de esa ruta, la pasada se quedaría sin él —visible— en vez de colarse.
        """
        assert self._page is not None, "usar dentro del context manager"
        self._page.route(patron, lambda route: route.abort())

    def descartar_recursos(self, tipos: Collection[str]) -> None:
        """Aborta los tipos de recurso indicados (`image`, `font`, `stylesheet`, `media`…).

        Es peso que el scraper no lee: las fotos que guardamos son URLs que vienen en el JSON de
        la página, no descargas. Medido en Hipercor, cuyas fichas hay que pedir de una en una:
        4,10 s -> 3,55 s por ficha con **el mismo dato** (mismas variantes). Además es cortesía,
        porque el ahorro de tráfico se lo lleva también la tienda.
        """
        assert self._page is not None, "usar dentro del context manager"
        conjunto = set(tipos)
        self._page.route(
            "**/*",
            lambda route: (
                route.abort() if route.request.resource_type in conjunto else route.continue_()
            ),
        )

    def get_html(self, url: str, espera_selector: str | None = None) -> tuple[int, str]:
        """Navega y devuelve `(status, HTML)`, con reintentos ante 429/5xx.

        Para tiendas cuyo dato viaja en la propia página (SSR) en vez de en una API: Hipercor
        publica el listado y la ficha en un `dataLayer`/`ld+json` embebidos, y su `robots.txt`
        veta `/api`, así que la página **es** la fuente. Devuelve el status en vez de elevar
        porque un 404 aquí es información (producto retirado), no un fallo.

        `espera_selector` aguarda a que ese elemento exista antes de leer el HTML: hay partes que
        el servidor no manda en el documento inicial y pinta el JS (en Hipercor, el selector de
        tallas). Se espera por **selector y no sondeando el HTML**, que en páginas de medio mega
        cuesta más que la propia navegación. Si no llega, se devuelve lo que haya: quien llama
        decide, y en la ingesta un detalle que no llega es un producto que no se actualiza, no un
        producto que se da de baja.
        """
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            resp = self._page.goto(url, wait_until="domcontentloaded")
            status = resp.status if resp is not None else 0
            if status not in _RETRYABLE_STATUS or attempt == retries:
                if espera_selector is not None and status == 200:
                    with contextlib.suppress(Exception):  # sin el elemento, se devuelve lo que hay
                        self._page.wait_for_selector(
                            espera_selector,
                            state="attached",
                            timeout=self._config.browser_hydrate_timeout * 1000,
                        )
                return status, self._page.content()
            self._backoff(attempt)
        return 0, ""  # inalcanzable (el último intento retorna), tranquiliza a mypy

    def get_json(self, url: str) -> Any:
        """GET de una API del mismo origen (fingerprint+cookies del navegador) con reintentos."""
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        timeout_ms = self._config.browser_nav_timeout * 1000
        for attempt in range(retries + 1):
            self._polite_pause()
            resp = self._page.request.get(url, timeout=timeout_ms)
            if resp.ok:
                return resp.json()
            if resp.status not in _RETRYABLE_STATUS or attempt == retries:
                raise BrowserHTTPError(resp.status, url, resp.status_text)
            self._backoff(attempt)
        return None  # inalcanzable (el último intento hace raise), tranquiliza a mypy
