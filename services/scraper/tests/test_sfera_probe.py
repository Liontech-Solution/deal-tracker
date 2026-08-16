"""Confirmación activa de Sfera (#4): sondeo por stock + PDP, sin navegador real.

La señal está verificada contra la web real (ver `test_sfera_live.py`): Sfera enruta por id
y devuelve 404 para uno que ya no existe; el endpoint de stock lista en `data.ADD` lo que se
puede comprar ahora mismo. Aquí se comprueba la lógica de decisión con una sesión falsa.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from scraper.config import Config
from scraper.stores.base import DelistCandidate, ProbeVerdict
from scraper.stores.sfera import CategoryConfig, SferaStore

_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0)
_CATS = [CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos")]


class FakeSession:
    """Sustituye a `BrowserSession`: respuestas de stock y status de la PDP por URL.

    Distingue **pedir** de **navegar** (`pedidas` / `navegadas`), que es lo único que separa las
    dos rutas de `BrowserSession`: las dos devuelven el mismo status. Sin esa distinción el doble
    no puede notar que la ficha se renderiza cuando no hace falta (#168, y el mismo apaño que
    #160 le hizo al doble de Hipercor).
    """

    def __init__(self, stock: dict[str, Any], statuses: dict[str, int]) -> None:
        self._stock = stock
        self._statuses = statuses
        self.visited: list[str] = []
        self.navegadas: list[str] = []
        self.pedidas: list[str] = []

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def goto(self, url: str) -> int:
        self.visited.append(url)
        self.navegadas.append(url)
        return self._statuses.get(url, 200)

    def pedir_html(self, url: str) -> tuple[int, str]:
        self.visited.append(url)
        self.pedidas.append(url)
        return self._statuses.get(url, 200), ""

    def get_json(self, url: str) -> Any:
        self.visited.append(url)
        for pid, payload in self._stock.items():
            if f"products={pid}" in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise RuntimeError(f"stock no simulado: {url}")


def _store(session: FakeSession) -> SferaStore:
    return SferaStore(_CFG, categories=_CATS, session_factory=lambda: session)  # type: ignore[arg-type]


def _stock(add: list[str]) -> dict[str, Any]:
    return {"success": True, "data": {"ADD": add, "COMING_SOON": []}}


def _candidate(pid: str) -> DelistCandidate:
    return DelistCandidate(pid, f"https://www.sfera.com/es/ninos/{pid}-x/")


def test_stock_comprable_confirma_vivo_sin_visitar_la_ficha() -> None:
    session = FakeSession(stock={"A1": _stock(["A1"])}, statuses={})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.ALIVE}
    assert not [u for u in session.visited if u.endswith("A1-x/")]  # no hizo falta la PDP


def test_agotado_pero_con_ficha_viva_no_es_baja() -> None:
    """Fuera de `ADD` con la PDP a 200: existe pero no se puede comprar. Sigue SIN ser baja.

    Este test afirmaba `ALIVE` hasta #426, y ese `ALIVE` era el fallo: disparaba `_rescue()`, que
    pone la racha a cero, así que el producto se quedaba en el catálogo indefinidamente con su
    último precio rebajado y sin una talla que comprar. Es el escenario de #197 en otra tienda.

    Lo que NO cambia, y por eso el nombre del test sigue valiendo: `UNBUYABLE` tampoco da de baja.
    Que hoy no quede stock no prueba que la prenda se haya retirado.
    """
    session = FakeSession(stock={"A1": _stock([])}, statuses={})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.UNBUYABLE}


def test_si_el_stock_no_contesta_no_se_acusa_de_agotado() -> None:
    """La regresión que protege el arreglo de arriba, y la trampa de #426.

    La señal vieja (`stock_lists_available()`) devolvía `False` tanto para «la tienda dice» como
    «la petición se cayó». Emitir `UNBUYABLE` sobre esa señal convertiría un fallo de red en un
    agotado — y ese veredicto alimenta contadores y, desde #427, una alarma. Con el stock mudo y la
    ficha viva, el veredicto conservador es `ALIVE`.
    """
    session = FakeSession(stock={"A1": RuntimeError("timeout")}, statuses={})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.ALIVE}


def test_un_json_de_stock_con_otra_forma_tampoco_acusa() -> None:
    """Lo mismo por la otra vía: la respuesta llega pero no se entiende (`data` sin `ADD`)."""
    session = FakeSession(stock={"A1": {"success": False, "data": {}}}, statuses={})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.ALIVE}


def test_ficha_404_confirma_la_retirada() -> None:
    url = "https://www.sfera.com/es/ninos/A1-x/"
    session = FakeSession(stock={"A1": _stock([])}, statuses={url: 404})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.DEAD}


def test_bloqueo_de_akamai_no_da_veredicto() -> None:
    """Un 403 es problema nuestro, no prueba de que el producto se haya retirado."""
    url = "https://www.sfera.com/es/ninos/A1-x/"
    session = FakeSession(stock={"A1": RuntimeError("403")}, statuses={url: 403})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {}


def test_sin_url_no_hay_veredicto_posible() -> None:
    session = FakeSession(stock={"A1": _stock([])}, statuses={})
    store = _store(session)

    assert store.probe_alive([DelistCandidate("A1", None)]) == {}


def test_la_ficha_se_pide_y_no_se_navega() -> None:
    """De la PDP solo se lee el status, y `pedir_html` lo da sin ejecutar el JS de la página.

    Lo único que sigue navegándose es la siembra de cookies, que es la precondición que este
    camino no puede darse a sí mismo (#168).
    """
    url = "https://www.sfera.com/es/ninos/A1-x/"
    session = FakeSession(stock={"A1": _stock([])}, statuses={url: 404})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.DEAD}
    assert session.pedidas == [url]
    assert url not in session.navegadas
    assert len(session.navegadas) == 1, "la única navegación es la siembra"


def test_sin_candidatos_no_abre_navegador() -> None:
    session = FakeSession(stock={}, statuses={})
    store = _store(session)

    assert store.probe_alive([]) == {}
    assert session.visited == []
