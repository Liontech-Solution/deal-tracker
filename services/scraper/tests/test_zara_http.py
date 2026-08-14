"""Tests de la capa HTTP del scraper: reintentos + backoff ante throttling/errores.

También el sondeo de vida (`_probe_one`) que usa la confirmación activa de bajas, y la
tolerancia a hojas de categoría retiradas (#41).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scraper.config import Config
from scraper.stores.base import ScrapeScope
from scraper.stores.zara import _SOLO_CONJUNTOS, CategoryConfig, ZaraStore

# Sin esperas reales: delay y backoff a 0 -> el test es instantáneo.
_CFG = Config(database_url="x", request_delay=0.0, request_retries=3, retry_backoff=0.0)


def _store_with(responses: list[int]) -> tuple[ZaraStore, httpx.Client, dict[str, int]]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        status = responses[min(i, len(responses) - 1)]
        if status == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(status, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ZaraStore(_CFG), client, calls


def test_reintenta_ante_503_y_acaba_con_exito() -> None:
    store, client, calls = _store_with([503, 503, 200])
    assert store._get_json(client, "https://x/y") == {"ok": True}
    assert calls["n"] == 3  # 2 fallos + 1 éxito


def test_no_reintenta_ante_404() -> None:
    store, client, calls = _store_with([404])
    with pytest.raises(httpx.HTTPStatusError):
        store._get_json(client, "https://x/y")
    assert calls["n"] == 1  # 404 no es reintentable: falla a la primera


def test_agota_reintentos_y_propaga() -> None:
    store, client, calls = _store_with([503])
    with pytest.raises(httpx.HTTPStatusError):
        store._get_json(client, "https://x/y")
    assert calls["n"] == 4  # 1 intento + 3 reintentos


# --- #4 Confirmación activa: el endpoint de detalle como sondeo de vida ------------------


def _probe_store(handler: Any) -> tuple[ZaraStore, httpx.Client]:
    return ZaraStore(_CFG), httpx.Client(transport=httpx.MockTransport(handler))


def test_sondeo_lista_con_producto_es_vivo() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(200, json=[{"seo": {"id": 1}}]))
    assert store._probe_one(client, "123") is True


def test_sondeo_lista_vacia_es_retirado() -> None:
    """Zara responde 200 con [] cuando ya no conoce el id: es el veredicto que buscamos."""
    store, client = _probe_store(lambda _r: httpx.Response(200, json=[]))
    assert store._probe_one(client, "123") is False


def test_sondeo_404_es_retirado() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(404, text="nope"))
    assert store._probe_one(client, "123") is False


def test_sondeo_5xx_agotado_no_da_veredicto() -> None:
    """Un fallo nuestro no es prueba de retirada: sin veredicto, la ingesta no da de baja."""
    store, client = _probe_store(lambda _r: httpx.Response(503, text="nope"))
    assert store._probe_one(client, "123") is None


def test_sondeo_respuesta_inesperada_no_da_veredicto() -> None:
    store, client = _probe_store(lambda _r: httpx.Response(200, json={"no": "es una lista"}))
    assert store._probe_one(client, "123") is None


# --- #41 Hojas de categoría retiradas: la pasada sigue, el ámbito sale de las bajas -------

# Tres hojas de ámbitos distintos, para poder comprobar cuál cae y cuál no.
_CATS = [
    CategoryConfig(111, "niña", "zapateria", "zapatos"),
    CategoryConfig(222, "niña", "ropa", "camisetas"),
    CategoryConfig(333, "niño", "ropa", "pantalones"),
]


def _listing(product_id: str) -> dict[str, Any]:
    """Payload de listado mínimo con un producto (lo que `_iter_product_nodes` busca)."""
    return {
        "productGroups": [
            {
                "elements": [
                    {
                        "seo": {"discernProductId": product_id},
                        "detail": {"colors": [{"id": "c1", "price": 1995}]},
                    }
                ]
            }
        ]
    }


def _catalog_store(status_by_cat: dict[int, int]) -> ZaraStore:
    """Tienda cuyo listado responde por categoría según `status_by_cat` (id -> status)."""

    def handler(request: httpx.Request) -> httpx.Response:
        cat_id = int(request.url.path.split("/category/")[1].split("/")[0])
        status = status_by_cat[cat_id]
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, json=_listing(f"p{cat_id}"))

    store = ZaraStore(_CFG, categories=list(_CATS))
    # El cliente lo abre `list_catalog`; se lo cambiamos por uno con transporte simulado.
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


def test_una_hoja_404_no_impide_ingerir_las_demas() -> None:
    """El fallo que motivó #41: una hoja de 47 tumbaba las 47."""
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    ids = [e.retailer_product_id for e in store.list_catalog()]

    assert ids == ["p111", "p333"]  # la hoja muerta se salta, las vivas se ingieren


def test_el_ambito_de_la_hoja_muerta_sale_del_informe() -> None:
    """Su ámbito no puede contar como escaneado: lo no visto ahí no está retirado."""
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    list(store.list_catalog())
    report = store.scan_report()

    assert (report.leaves_total, report.leaves_failed) == (3, 1)
    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "camisetas")}
    # Y con su nombre (#155): el ámbito no identifica la hoja, así que sin el id no se sabe cuál
    # de las que lo alimentan hay que ir a buscar al árbol de la tienda.
    assert report.failed_leaves == ["222"]


def test_una_hoja_5xx_agotada_sigue_abortando_la_pasada() -> None:
    """La tolerancia es solo para hojas retiradas: un fallo transitorio no se traga."""
    store = _catalog_store({111: 200, 222: 503, 333: 200})

    with pytest.raises(httpx.HTTPStatusError):
        list(store.list_catalog())


def test_403_no_cuenta_como_hoja_retirada() -> None:
    """Un bloqueo no es una categoría que la tienda haya quitado."""
    store = _catalog_store({111: 200, 222: 403, 333: 200})

    with pytest.raises(httpx.HTTPStatusError):
        list(store.list_catalog())


def test_relistar_no_acumula_hojas_del_recorrido_anterior() -> None:
    store = _catalog_store({111: 200, 222: 404, 333: 200})

    list(store.list_catalog())
    list(store.list_catalog())

    assert store.scan_report().leaves_total == 3


# --- #358 El rescate del residuo tiene que decir cuánto aporta ----------------------------------
#
# El arreglo de #289 recupera decenas de prendas por pasada y podía dejar de hacerlo **sin que
# nada se pusiera rojo**: la hoja sigue respondiendo 200, su filtro sigue casando —así que
# `filtro_vacio()` no salta— y lo único que cambia es cuántos de sus productos sabemos clasificar.


def _nodo(pid: str, familia: str, nombre: str = "") -> dict[str, Any]:
    return {
        "seo": {"discernProductId": pid},
        "familyName": familia,
        "name": nombre or familia,
        "detail": {"colors": [{"id": "c1", "price": 1995}]},
    }


def _listing_de(*nodos: dict[str, Any]) -> dict[str, Any]:
    return {"productGroups": [{"elements": list(nodos)}]}


def _store_lookbook(listings: dict[int, dict[str, Any]], categorias: list[Any]) -> ZaraStore:
    """Tienda con un listado a medida por hoja, para poder fabricar el residuo."""

    def handler(request: httpx.Request) -> httpx.Response:
        cat_id = int(request.url.path.split("/category/")[1].split("/")[0])
        return httpx.Response(200, json=listings[cat_id])

    store = ZaraStore(_CFG, categories=categorias)
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return store


# Una hoja-lookbook de conjuntos (con filtro) y la hoja propia de pantalones, en ese orden: es el
# orden real, y es lo que hace que el residuo se emita al final y no le quite el hueco a nadie.
_LOOKBOOK = CategoryConfig(444, "niña", "ropa", "conjuntos", filtro=_SOLO_CONJUNTOS)
_PANTALONES = CategoryConfig(555, "niña", "ropa", "pantalones")


def test_el_residuo_publica_cuanto_aporta_por_hoja() -> None:
    """Dos pantalones que solo salen en el lookbook: los rescata y lo dice."""
    store = _store_lookbook(
        {
            444: _listing_de(
                _nodo("c1", "CONJUNTO"),
                _nodo("p1", "PANTALON BEBE"),
                _nodo("p2", "PANTALON"),
            ),
            555: _listing_de(_nodo("p9", "PANTALON")),
        },
        [_LOOKBOOK, _PANTALONES],
    )

    ids = [e.retailer_product_id for e in store.list_catalog()]
    report = store.scan_report()

    assert ids == ["c1", "p9", "p1", "p2"], "el residuo va al final, después de las hojas propias"
    assert report.residual_by_leaf == {"444": 2}
    assert report.residual_entries == 2
    assert report.barren_residual_leaves == []


def test_solo_cuenta_lo_que_el_residuo_APORTA_no_lo_que_parsea() -> None:
    """Una prenda que ya reclamó su hoja propia no la aporta esto, aunque el lookbook la traiga.

    Es la diferencia entre contar en la hoja y contar tras el cruce contra `emitted`, y es la que
    hace que la cifra no se infle: sumar lo parseado contaría dos veces al que sale en dos sitios.
    """
    store = _store_lookbook(
        {
            444: _listing_de(_nodo("c1", "CONJUNTO"), _nodo("p9", "PANTALON")),
            555: _listing_de(_nodo("p9", "PANTALON")),
        },
        [_LOOKBOOK, _PANTALONES],
    )

    list(store.list_catalog())

    assert store.scan_report().residual_by_leaf == {"444": 0}


def test_una_hoja_con_filtro_que_deja_de_aportar_sale_marcada() -> None:
    """El modo de fallo entero de #358, en un test: Zara re-rotula y el rescate se va a cero.

    `PANTALON BEBÉ` con tilde entra igual desde el endurecimiento de `_familia_base()`, así que
    para simular la rotura hace falta una familia que de verdad no conozcamos. Lo que importa es
    que la hoja responde 200 y su filtro SÍ casa —el conjunto entra— o sea que ninguna de las redes
    que ya existían se enteraría.
    """
    store = _store_lookbook(
        {
            444: _listing_de(_nodo("c1", "CONJUNTO"), _nodo("x1", "PANTALONCITO DE VERANO")),
            555: _listing_de(_nodo("p9", "PANTALON")),
        },
        [_LOOKBOOK, _PANTALONES],
    )

    list(store.list_catalog())
    report = store.scan_report()

    assert report.residual_by_leaf == {"444": 0}
    assert report.barren_residual_leaves == ["444"]
    # Y ninguna de las redes anteriores lo habría visto:
    assert report.empty_filter_leaves == [], "el filtro casó: el conjunto entró"
    assert (report.leaves_total, report.leaves_failed) == (2, 0)
    assert report.failed_scopes == set(), "una hoja sin residuo NO sale de las bajas (#358)"


def test_las_hojas_sin_filtro_no_entran_en_el_contador() -> None:
    """Solo las hojas con `FiltroDeHoja` tienen residuo; las demás llenarían esto de ceros."""
    store = _catalog_store({111: 200, 222: 200, 333: 200})

    list(store.list_catalog())

    assert store.scan_report().residual_by_leaf == {}
    assert store.scan_report().barren_residual_leaves == []


def test_una_hoja_con_filtro_caida_no_se_siembra_en_el_residuo() -> None:
    """«No aportó nada» y «no se pudo ni mirar» son señales distintas y llevan a sitios distintos.

    Una hoja retirada ya tiene la suya —`failed_leaves`, `dead_ratio`— y manda ir al árbol de
    categorías a buscar el id nuevo. Colarla además en `barren_residual_leaves` mandaría a mirar
    `_FAMILIA_RESIDUAL`, que no tiene nada que ver, y diluiría la única señal que #358 añade.

    Hoy sale bien porque el `continue` de la hoja caída corta antes de la siembra. Esto lo fija:
    subir el `setdefault` por encima del `try` es justo el refactor que lo rompería sin ruido.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        cat_id = int(request.url.path.split("/category/")[1].split("/")[0])
        if cat_id == 444:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, json=_listing_de(_nodo("p9", "PANTALON")))

    store = ZaraStore(_CFG, categories=[_LOOKBOOK, _PANTALONES])
    store._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    list(store.list_catalog())
    report = store.scan_report()

    assert report.residual_by_leaf == {}, "la hoja caída no se siembra"
    assert report.barren_residual_leaves == []
    # Y la señal que sí le toca sigue en su sitio.
    assert report.failed_leaves == ["444"]
    assert (report.leaves_total, report.leaves_failed) == (2, 1)


def test_relistar_no_acumula_el_residuo_del_recorrido_anterior() -> None:
    store = _store_lookbook(
        {
            444: _listing_de(_nodo("c1", "CONJUNTO"), _nodo("p1", "PANTALON")),
            555: _listing_de(_nodo("p9", "PANTALON")),
        },
        [_LOOKBOOK, _PANTALONES],
    )

    list(store.list_catalog())
    list(store.list_catalog())

    assert store.scan_report().residual_by_leaf == {"444": 1}


# --- #41 Chequeo preventivo de las hojas (`--check-categories`) -----------------------------


def test_check_leaves_distingue_viva_retirada_y_sin_veredicto() -> None:
    """Un 5xx agotado NO es una hoja retirada: no se puede concluir nada de él."""
    store = _catalog_store({111: 200, 222: 404, 333: 503})

    salud = {leaf.leaf: leaf.alive for leaf in store.check_leaves()}

    assert salud == {"111": True, "222": False, "333": None}


def test_check_categories_sale_distinto_de_cero_si_hay_una_hoja_retirada() -> None:
    """Es lo que hace que un CronJob de vigilancia avise en vez de callarse."""
    sana = _catalog_store({111: 200, 222: 200, 333: 200})
    rota = _catalog_store({111: 200, 222: 404, 333: 200})

    assert _check_categories_con(sana) == 0
    assert _check_categories_con(rota) == 1


def test_un_sondeo_sin_veredicto_suelto_no_hace_fallar_el_chequeo() -> None:
    """Medido contra Sfera: un chequeo normal ya trae un 403 suelto de Akamai.

    Si eso rompiera el vigía, la alarma sonaría de rutina y acabaría silenciada.
    """
    store = _catalog_store({111: 200, 222: 503, 333: 200})

    assert _check_categories_con(store) == 0


def test_un_bloqueo_total_si_hace_fallar_el_chequeo() -> None:
    """Ninguna hoja confirmada viva ya no es un blip, y no puede pasar en silencio."""
    store = _catalog_store({111: 503, 222: 503, 333: 503})

    assert _check_categories_con(store) == 1


def _check_categories_con(store: ZaraStore) -> int:
    """Ejecuta el comando con una tienda ya construida (sin pasar por el registry)."""
    import scraper.run as run_mod

    original = run_mod.get_store
    run_mod.get_store = lambda _slug, _cfg: store  # type: ignore[assignment]
    try:
        return run_mod._check_categories(_CFG, "zara")
    finally:
        run_mod.get_store = original  # type: ignore[assignment]
