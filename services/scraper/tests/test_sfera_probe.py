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
from scraper.stores.browser import RespuestaHtml
from scraper.stores.sfera import CategoryConfig, SferaStore

_CFG = Config(database_url="x", request_delay=0.0, retry_backoff=0.0)
_CATS = [CategoryConfig("ninos/nina/zapatos", "niña", "zapateria", "zapatos")]


class FakeSession:
    """Sustituye a `BrowserSession`: respuestas de stock y status de la PDP por URL.

    Distingue **pedir** de **navegar** (`pedidas` / `navegadas`), que es lo único que separa las
    dos rutas de `BrowserSession`: las dos devuelven el mismo status. Sin esa distinción el doble
    no puede notar que la ficha se renderiza cuando no hace falta (#168, y el mismo apaño que
    #160 le hizo al doble de Hipercor).

    `destinos` simula la otra mitad que el status no cuenta (#454): a dónde acaba la petición tras
    las redirecciones. Por defecto la URL final es la pedida, que es el caso sano y el de todos los
    tests anteriores a esa issue.
    """

    def __init__(
        self,
        stock: dict[str, Any],
        statuses: dict[str, int],
        destinos: dict[str, str] | None = None,
    ) -> None:
        self._stock = stock
        self._statuses = statuses
        self._destinos = destinos or {}
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

    def pedir(self, url: str) -> RespuestaHtml:
        self.visited.append(url)
        self.pedidas.append(url)
        return RespuestaHtml(self._statuses.get(url, 200), "", self._destinos.get(url, url))

    def pedir_html(self, url: str) -> tuple[int, str]:
        respuesta = self.pedir(url)
        return respuesta.status, respuesta.html

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


# --- #454: un 200 no prueba que la ficha sea la nuestra -----------------------------------------


def test_la_ficha_de_otro_producto_no_rescata_al_nuestro() -> None:
    """El fallo de #454: 200 + redirección a OTRA ficha se leía como `ALIVE`.

    Y `ALIVE` no es un veredicto inocuo: dispara `_rescue()`, que pone la racha a cero, así que el
    producto retirado se quedaba en el catálogo indefinidamente con su último precio. Es la baja
    que no ocurre nunca — el fallo simétrico del que más se vigila.

    El veredicto correcto es **ausente del mapa**, no `DEAD`: que nos sirvan otra ficha no prueba
    que la nuestra se haya retirado (podría vivir bajo otra URL), y una baja falsa es un daño peor
    y menos reversible que un producto congelado. Ausente lo deja con la racha intacta, en
    `blocked_ids`, y lo cuenta en `unresolved`.
    """
    url = "https://www.sfera.com/es/ninos/A1-x/"
    session = FakeSession(
        stock={"A1": _stock([])},
        statuses={url: 200},
        destinos={url: "https://www.sfera.com/es/ninos/A2-otro-producto/"},
    )
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {}


def test_la_redireccion_al_canonico_del_mismo_producto_sigue_siendo_vivo() -> None:
    """El matiz que #454 avisa de no perder: la redirección sana es normal y no se toca.

    Sfera enruta por id y el slug le da igual, así que corregir el slug —o añadir la barra final—
    es exactamente lo que se espera de ella. Medido el 17/08/2026: dos ids mutados que resultaron
    ser de OTROS productos reales devolvieron 200 con el slug reescrito, y la URL final llevaba en
    los dos casos el id pedido. Si esto se leyera como desajuste, el sondeo dejaría de confirmar
    productos vivos y las bajas legítimas se atascarían.
    """
    url = "https://www.sfera.com/es/ninos/A1-slug-viejo/"
    session = FakeSession(
        stock={"A1": _stock(["A1"])},
        statuses={url: 200},
        destinos={url: "https://www.sfera.com/es/ninos/A1-slug-nuevo-canonico/"},
    )
    store = _store(session)

    assert store.probe_alive([DelistCandidate("A1", url)]) == {"A1": ProbeVerdict.ALIVE}


def test_un_id_contenido_en_otro_no_cuela_como_identidad() -> None:
    """La comprobación se ancla al segmento, no es un `in` suelto.

    `A20097413` está contenido en `A200974138`, así que un `in` sobre la URL daría por buena la
    ficha del segundo cuando preguntamos por el primero — y sería un rescate contra el producto
    equivocado, que es justo lo que esta issue viene a cerrar.
    """
    url = "https://www.sfera.com/es/ninos/A20097413-x/"
    session = FakeSession(
        stock={"A20097413": _stock([])},
        statuses={url: 200},
        destinos={url: "https://www.sfera.com/es/ninos/A200974138-camiseta/"},
    )
    store = _store(session)

    assert store.probe_alive([_candidate("A20097413")]) == {}


def test_el_404_manda_aunque_la_url_final_sea_otra() -> None:
    """El orden importa: un 404 es un veredicto y no necesita comprobar identidad.

    Si la comprobación de identidad se colara antes del status, un id retirado que además
    redirigiera acabaría en «sin veredicto» en vez de en `DEAD`, y la baja legítima no se
    produciría nunca. Es el fallo contrario al de esta issue y se protege aquí.
    """
    url = "https://www.sfera.com/es/ninos/A1-x/"
    session = FakeSession(
        stock={"A1": _stock([])},
        statuses={url: 404},
        destinos={url: "https://www.sfera.com/es/ninos/A2-otro/"},
    )
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.DEAD}


def test_el_atajo_por_stock_no_pasa_por_la_identidad() -> None:
    """Con stock comprable no se pide la ficha, así que no hay URL final que comprobar.

    El atajo de #426 pregunta por id al endpoint de stock: ahí la identidad no está en duda, y
    gastar una petición para confirmarla sería pagar por nada.
    """
    session = FakeSession(stock={"A1": _stock(["A1"])}, statuses={})
    store = _store(session)

    assert store.probe_alive([_candidate("A1")]) == {"A1": ProbeVerdict.ALIVE}
    assert session.pedidas == []
