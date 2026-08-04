"""Sesión de navegador headless (Playwright) para tiendas tras anti-bot (Akamai).

Algunas tiendas (p.ej. Sfera) sirven el HTML de documento con buenas cabeceras, pero
protegen sus APIs de listado/paginación con **Akamai Bot Manager**: exigen cookies
(`_abck`/`bm_sz`) que solo se obtienen ejecutando el sensor JS. Un cliente HTTP plano
(httpx) no las consigue. Este módulo levanta un **Chromium real** que ejecuta ese sensor;
como Akamai liga la validez de las cookies al *fingerprint* del navegador, TODAS las
peticiones de esas tiendas van por aquí:

  - `goto(url)`   — navega a una página de documento (siembra las cookies del origen).
  - `get_html(url)` — navega y devuelve `(status, HTML)`, para tiendas cuyo dato viaja en la
    propia página (Hipercor, cuyo `robots.txt` veta la API).
  - `pedir_html(url)` — el mismo `(status, HTML)` **sin navegar**: descarga el documento servido
    y no ejecuta nada. Cuando el dato viene servido es el mismo resultado por una fracción del
    coste, y es la diferencia entre que la pasada de Hipercor quepa en su deadline o no (#160).
  - `get_json(url)` — pide una API del mismo origen con `page.request` (mismo fingerprint
    + cookies que el navegador), con reintentos/backoff como el cliente httpx de Zara.

Los dos últimos comparten transporte (`page.request`) y por tanto comparten una letra pequeña:
las cookies y el fingerprint son los del navegador, pero `route()` no los intercepta, así que
`bloquear()` y `descartar_recursos()` solo gobiernan lo que se navega.

Los tres reintentan **también cuando la navegación no llega a completarse** (timeout, `net::ERR_*`)
y elevan `BrowserUnreachable` al agotar los intentos. No es un detalle: en una tienda que va por
navegador ese es el fallo transitorio más probable, y mientras se coló fuera del bucle una sola
hoja lenta bastó para tumbar una pasada de tres horas y media (#107).

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


class BrowserUnreachable(RuntimeError):
    """La navegación no llegó a completarse tras agotar los reintentos.

    Hermana de `BrowserHTTPError`, y por el mismo motivo: quien llama tiene que poder distinguir
    **«no llegó respuesta»** de **«llegó una respuesta que dice 404»**. Lo primero es un fallo
    nuestro o de la red —la hoja no está retirada, es que no la hemos podido ver—; lo segundo es
    información de la tienda. Confundirlos es lo que convierte un timeout en bajas falsas.
    """

    def __init__(self, url: str, causa: BaseException) -> None:
        # Solo la primera línea de la causa: el mensaje de Playwright arrastra un "Call log" de
        # varias líneas que repite la URL que ya está aquí, y una pasada con unas cuantas hojas
        # lentas convertiría el log en un muro. La excepción original sigue encadenada (`from`).
        detalle = str(causa).splitlines()[0] if str(causa).strip() else type(causa).__name__
        self.motivo = f"{type(causa).__name__}: {detalle}"
        super().__init__(f"GET {url} -> {self.motivo}")
        self.url = url


class BrowserSession:
    """Context manager que abre un Chromium con perfil realista y lo cierra al salir."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        # Clase base de los errores de Playwright (`TimeoutError` hereda de ella). Se resuelve al
        # abrir el navegador y no al importar el módulo, para no romper el import perezoso que
        # documenta la cabecera: los tests de parseo no deben necesitar Playwright instalado.
        self._pw_error: type[BaseException] = Exception

    def __enter__(self) -> BrowserSession:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        self._pw_error = PlaywrightError

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
        """Navega a una página de documento (siembra cookies de Akamai). Devuelve el status.

        Reintenta ante fallo de navegación por lo mismo que `get_html`, y aquí importa igual: si la
        siembra de cookies de Sfera eleva, se lleva por delante la hoja que iba a listarse después.
        """
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = self._page.goto(url, wait_until="domcontentloaded")
            except self._pw_error as exc:
                if attempt == retries:
                    raise BrowserUnreachable(url, exc) from exc
                self._backoff(attempt)
                continue
            return resp.status if resp is not None else 0
        return 0  # inalcanzable (el último intento retorna o eleva), tranquiliza a mypy

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

        Lo que no descarta lo pasa al siguiente handler con `fallback()`, **no** con `continue_()`,
        y esa diferencia no es cosmética: Playwright evalúa las rutas de la última registrada a la
        primera y se para en cuanto una resuelve la petición. Con `continue_()`, este patrón
        `**/*` se comía todas las peticiones y dejaba **sin efecto** el veto de `bloquear()`
        —comprobado en vivo: el handler de `/api` no llegaba a invocarse nunca—. Con `fallback()`
        el orden de registro deja de importar.
        """
        assert self._page is not None, "usar dentro del context manager"
        conjunto = set(tipos)
        self._page.route(
            "**/*",
            lambda route: (
                route.abort() if route.request.resource_type in conjunto else route.fallback()
            ),
        )

    def get_html(self, url: str, espera_selector: str | None = None) -> tuple[int, str]:
        """Navega y devuelve `(status, HTML)`, con reintentos ante 429/5xx y fallos de navegación.

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

        Si la navegación no llega a completarse (timeout, `net::ERR_*`) se reintenta con el mismo
        backoff que un 429, y agotados los intentos eleva `BrowserUnreachable`. No es un caso raro:
        en una tienda que va por navegador es **el fallo transitorio más probable de todos**, y
        hasta #107 se colaba fuera del bucle —cuando no llega respuesta no hay status que mirar— y
        una sola hoja lenta tumbaba la pasada entera.
        """
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = self._page.goto(url, wait_until="domcontentloaded")
            except self._pw_error as exc:
                if attempt == retries:
                    raise BrowserUnreachable(url, exc) from exc
                self._backoff(attempt)
                continue
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

    def pedir_html(self, url: str) -> tuple[int, str]:
        """Pide una página **sin navegarla**: devuelve `(status, HTML servido)`.

        Misma pareja que `get_html` y misma lectura del status (un 404 es información, no un
        fallo), pero por `page.request` en vez de `page.goto`: se descarga el documento y ahí se
        acaba. No se ejecuta JavaScript, no hay layout ni paint, no se piden subrecursos. Para una
        tienda cuyo dato viaja **servido** dentro de la página, eso es exactamente el mismo dato
        por una fracción del coste — medido en Hipercor sobre fichas reales, 0,08-0,42 s frente a
        1,14-1,41 s navegando, con las mismas variantes, tallas y precios (#160). El ahorro es
        sobre todo de CPU, que es el recurso que agotaba la pasada en el cluster.

        **Sirve solo cuando lo que se parsea viene servido, y eso hay que comprobarlo por tienda y
        por página.** En Hipercor el `ld+json` y el `dataLayer` vienen en el documento, pero el
        selector de tallas lo pinta el JS: una ficha agotada —cuyas tallas solo están en ese
        selector— parseada de aquí sale con una variante sin talla en vez de con ocho. Por eso
        quien llama compara y navega como respaldo, en vez de confiar en que siempre valga.

        Y hay una precondición que no se ve: **las cookies del origen**. Sin ellas Hipercor
        contesta 403 a la ficha (la rejilla sí entra), así que una sesión que solo pida por aquí
        tiene que sembrar antes con un `goto()`. Es el mismo motivo por el que existe `goto()`.

        Ojo a lo que este camino **no** hace: `route()` no intercepta las peticiones de
        `page.request`, así que ni `bloquear()` ni `descartar_recursos()` se aplican aquí. No es un
        agujero en el veto del `robots.txt` —esto pide la URL que se le pasa y ninguna más, y una
        página que no se renderiza no puede pedir nada por su cuenta—, pero conviene saberlo antes
        de dar por hecho que un patrón bloqueado protege también a este camino.
        """
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        timeout_ms = self._config.browser_nav_timeout * 1000
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = self._page.request.get(url, timeout=timeout_ms)
            except self._pw_error as exc:
                if attempt == retries:
                    raise BrowserUnreachable(url, exc) from exc
                self._backoff(attempt)
                continue
            if resp.status not in _RETRYABLE_STATUS or attempt == retries:
                return resp.status, (resp.text() if resp.ok else "")
            self._backoff(attempt)
        return 0, ""  # inalcanzable (el último intento retorna), tranquiliza a mypy

    def get_json(self, url: str) -> Any:
        """GET de una API del mismo origen (fingerprint+cookies del navegador) con reintentos.

        Un fallo de red se reintenta como un 5xx y, agotados los intentos, eleva
        `BrowserUnreachable`: es «no he podido verlo», no «ya no está», y esa distinción es la que
        impide que un timeout acabe en bajas falsas.
        """
        assert self._page is not None, "usar dentro del context manager"
        retries = self._config.request_retries
        timeout_ms = self._config.browser_nav_timeout * 1000
        for attempt in range(retries + 1):
            self._polite_pause()
            try:
                resp = self._page.request.get(url, timeout=timeout_ms)
            except self._pw_error as exc:
                if attempt == retries:
                    raise BrowserUnreachable(url, exc) from exc
                self._backoff(attempt)
                continue
            if resp.ok:
                return resp.json()
            if resp.status not in _RETRYABLE_STATUS or attempt == retries:
                raise BrowserHTTPError(resp.status, url, resp.status_text)
            self._backoff(attempt)
        return None  # inalcanzable (el último intento hace raise), tranquiliza a mypy
