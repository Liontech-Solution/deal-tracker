"""Test de integración de la ingesta contra Postgres.

Cubre: upsert de catálogo, apilado de historial, detección de altas/bajas y el
**detalle condicional** (si la huella del listado no cambia, no se pide el detalle).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from scraper.ingest import CatalogScanAborted, ingest
from scraper.stores.base import (
    DelistCandidate,
    ListingEntry,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)

T1 = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 2, 8, 0, tzinfo=UTC)
T3 = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
T4 = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
# Muy posterior: con estas el detalle de las pasadas anteriores ya está rancio (umbral 7 días).
T_STALE = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
T_STALE2 = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)

# Lo que tarda a propósito la tienda del test del cronómetro. Suficiente para separarse del ruido
# de una transacción vacía sin alargar `just check` de forma perceptible.
_PAUSA_LENTA = 0.25


class FakeStore:
    """Scraper falso de dos fases. Registra a qué productos se les pidió detalle."""

    slug = "fake"
    name = "Fake Store"
    base_url = "https://fake.example/"

    def __init__(
        self,
        products: list[ScrapedProduct],
        signatures: dict[str, str],
        scopes: list[ScrapeScope] | None = None,
    ) -> None:
        self._by_id = {p.retailer_product_id: p for p in products}
        self._sigs = signatures
        self._scopes = scopes
        self.detail_calls: list[str] = []

    def scopes(self) -> Iterable[ScrapeScope]:
        if self._scopes is not None:
            return self._scopes
        # Por defecto: ámbitos deducidos de los productos, sin duplicar.
        out: list[ScrapeScope] = []
        for p in self._by_id.values():
            scope = ScrapeScope(p.gender, p.section, p.category)
            if scope not in out:
                out.append(scope)
        return out

    def list_catalog(self) -> Iterable[ListingEntry]:
        for pid, p in self._by_id.items():
            yield ListingEntry(pid, self._sigs[pid], p.gender, p.section, p.category)

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        for e in entries:
            self.detail_calls.append(e.retailer_product_id)
            product = self._by_id.get(e.retailer_product_id)
            if product is not None:
                yield product


class ProbingFakeStore(FakeStore):
    """Como `FakeStore`, pero además confirma bajas (implementa `SupportsAliveProbe`).

    `verdicts` mapea id -> sigue a la venta; lo que no esté no da veredicto. Con `explode`
    el sondeo entero revienta (bloqueo de la tienda).
    """

    def __init__(
        self,
        products: list[ScrapedProduct],
        signatures: dict[str, str],
        verdicts: dict[str, bool],
        *,
        scopes: list[ScrapeScope] | None = None,
        explode: bool = False,
    ) -> None:
        super().__init__(products, signatures, scopes)
        self._verdicts = verdicts
        self._explode = explode
        self.probed: list[str] = []

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, bool]:
        if self._explode:
            raise RuntimeError("tienda bloqueada")
        out: dict[str, bool] = {}
        for candidate in candidates:
            self.probed.append(candidate.retailer_product_id)
            verdict = self._verdicts.get(candidate.retailer_product_id)
            if verdict is not None:
                out[candidate.retailer_product_id] = verdict
        return out


class ScanningFakeStore(FakeStore):
    """Como `FakeStore`, pero informa de hojas caídas (implementa `SupportsScanReport`)."""

    def __init__(
        self,
        products: list[ScrapedProduct],
        signatures: dict[str, str],
        report: ScanReport,
        scopes: list[ScrapeScope] | None = None,
    ) -> None:
        super().__init__(products, signatures, scopes)
        self._report = report

    def scan_report(self) -> ScanReport:
        return self._report


def _variant(vid: str, price: str, list_price: str | None = None) -> ScrapedVariant:
    return ScrapedVariant(
        retailer_variant_id=vid,
        size="30",
        color="Negro",
        sku=f"sku-{vid}",
        price=Decimal(price),
        list_price=Decimal(list_price) if list_price else None,
        in_stock=True,
    )


def _product(
    pid: str,
    name: str,
    variants: list[ScrapedVariant],
    *,
    gender: str = "niña",
    section: str = "zapateria",
    category: str = "zapatos",
    barefoot: str | None = None,
    image_url: str | None = None,
    images: list[ScrapedImage] | None = None,
) -> ScrapedProduct:
    return ScrapedProduct(
        retailer_product_id=pid,
        name=name,
        gender=gender,
        section=section,
        category=category,
        barefoot=barefoot,
        url=f"https://fake.example/p{pid}.html",
        variants=variants,
        image_url=image_url,
        images=images or [],
    )


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def test_primera_pasada_persiste_catalogo_e_historial(db_conn: Any) -> None:
    store = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )

    result = ingest(db_conn, store, run_ts=T1)

    assert result.products_in_catalog == 2
    assert result.details_fetched == 2  # todo es nuevo -> se pide detalle de todo
    assert result.products_unchanged == 0
    assert result.variants_seen == 3
    assert sorted(store.detail_calls) == ["A", "B"]
    assert _scalar(db_conn, "SELECT count(*) FROM product") == 2
    assert _scalar(db_conn, "SELECT count(*) FROM variant") == 3
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 3
    assert (
        _scalar(db_conn, "SELECT status FROM scrape_run WHERE id = %s", (result.scrape_run_id,))
        == "success"
    )


def test_detalle_condicional_altas_y_bajas(db_conn: Any) -> None:
    store1 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    # Segunda pasada: A con la MISMA huella (sin cambios), B desaparece (baja), C nuevo (alta).
    store2 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")]),
            _product("C", "Sandalia", [_variant("C-1", "25.00")]),
        ],
        signatures={"A": "a1", "C": "c1"},
    )
    # `delist_min_misses=1`: aquí se comprueba el detalle condicional, no la histéresis.
    result = ingest(db_conn, store2, run_ts=T2, delist_min_misses=1)

    # Detalle condicional: a A NO se le pide detalle (huella intacta); a C sí.
    assert store2.detail_calls == ["C"]
    assert result.details_fetched == 1
    assert result.products_unchanged == 1

    # Altas: C existe con first_seen_at de la segunda pasada.
    assert _scalar(db_conn, "SELECT first_seen_at FROM product WHERE retailer_product_id='C'") == T2

    # Bajas: B descatalogado (producto y su variante).
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") == T2
    assert result.products_delisted == 1
    assert result.variants_delisted == 1

    # A sigue vivo: "tocado" aunque no se pidió su detalle (producto y variantes).
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A'") is None
    assert _scalar(db_conn, "SELECT last_seen_at FROM product WHERE retailer_product_id='A'") == T2
    assert (
        _scalar(
            db_conn,
            "SELECT count(*) FROM variant v JOIN product p ON p.id=v.product_id "
            "WHERE p.retailer_product_id='A' AND v.delisted_at IS NULL AND v.last_seen_at=%s",
            (T2,),
        )
        == 2
    )

    # Historial: 3 (run1) + 1 (C). A no genera precios nuevos al no pedirse su detalle.
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 4


def test_cambio_de_huella_fuerza_detalle_y_apila_precio(db_conn: Any) -> None:
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")])],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    # A rebaja: cambia la huella -> se pide detalle y se apila el nuevo precio con descuento.
    store2 = FakeStore(
        [
            _product(
                "A",
                "Bailarina",
                [_variant("A-1", "29.95", list_price="39.95"), _variant("A-2", "39.95")],
            )
        ],
        signatures={"A": "a2"},
    )
    result = ingest(db_conn, store2, run_ts=T2)

    assert store2.detail_calls == ["A"]
    assert result.details_fetched == 1
    assert result.products_unchanged == 0
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 4  # 2 + 2

    disc = _scalar(
        db_conn,
        "SELECT ph.discount_pct FROM price_history ph JOIN variant v ON v.id=ph.variant_id "
        "WHERE v.retailer_variant_id='A-1' AND ph.scraped_at=%s",
        (T2,),
    )
    assert disc == Decimal("25.03")  # (39.95-29.95)/39.95


def test_baja_solo_en_ambitos_escaneados(db_conn: Any) -> None:
    """#1: si un ámbito no se recorre en la pasada, sus productos NO se dan de baja."""
    store1 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    # Segunda pasada de OTRO ámbito (niño): A y B (niña) no se escanean -> no deben caer.
    store2 = FakeStore(
        [
            _product(
                "C", "Deportivo", [_variant("C-1", "30.00")], gender="niño", category="zapatillas"
            )
        ],
        signatures={"C": "c1"},
    )
    # Sin histéresis, para que la única razón de que A y B sobrevivan sea el ámbito.
    result = ingest(db_conn, store2, run_ts=T2, delist_min_misses=1)

    assert result.scanned_scopes == 1
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A'") is None
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE retailer_product_id='C'") == 1


def _streak(conn: Any, pid: str) -> Any:
    return _scalar(conn, "SELECT missing_streak FROM product WHERE retailer_product_id=%s", (pid,))


def test_histeresis_no_da_de_baja_a_la_primera(db_conn: Any) -> None:
    """#3: con umbral 2, hace falta faltar en DOS pasadas seguidas para descatalogar."""
    store1 = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    only_a = [_product("A", "Bailarina", [_variant("A-1", "39.95")])]
    # Primera ausencia de B: sospechosa, pero no se da de baja todavía.
    store2 = FakeStore(only_a, signatures={"A": "a1"})
    result = ingest(db_conn, store2, run_ts=T2, delist_min_misses=2)

    assert result.products_delisted == 0
    assert result.products_missing == 1
    assert result.variants_missing == 1
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None
    assert _streak(db_conn, "B") == 1

    # Segunda ausencia consecutiva: ahora sí, producto y variante.
    store3 = FakeStore(only_a, signatures={"A": "a1"})
    result = ingest(db_conn, store3, run_ts=T3, delist_min_misses=2)

    assert result.products_delisted == 1
    assert result.variants_delisted == 1
    assert result.products_missing == 0
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") == T3

    # Y si vuelve tras la baja, arranca de cero: no debe recaer a la primera ausencia.
    store4 = FakeStore(
        [*only_a, _product("B", "Botín", [_variant("B-1", "45.00")])],
        signatures={"A": "a1", "B": "b1"},
    )
    ingest(db_conn, store4, run_ts=T4, delist_min_misses=2)

    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None
    assert _streak(db_conn, "B") == 0


def test_histeresis_se_reinicia_al_reaparecer(db_conn: Any) -> None:
    """#3: un blip aislado no se acumula — al volver a verse, la racha se pone a cero."""
    both = [
        _product("A", "Bailarina", [_variant("A-1", "39.95")]),
        _product("B", "Botín", [_variant("B-1", "45.00")]),
    ]
    sigs = {"A": "a1", "B": "b1"}
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    only_a = [_product("A", "Bailarina", [_variant("A-1", "39.95")])]
    ingest(db_conn, FakeStore(only_a, signatures={"A": "a1"}), run_ts=T2)
    assert _streak(db_conn, "B") == 1

    # B reaparece con la misma huella: no se le pide detalle, pero se "toca" y resetea la racha.
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T3)
    assert _streak(db_conn, "B") == 0
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None

    # Vuelve a faltar: cuenta como primera ausencia, no como segunda.
    result = ingest(db_conn, FakeStore(only_a, signatures={"A": "a1"}), run_ts=T4)
    assert result.products_delisted == 0
    assert _streak(db_conn, "B") == 1


def test_red_seguridad_omite_bajas_en_caida_sospechosa(db_conn: Any) -> None:
    """#2: una caída brusca de lo observado en un ámbito omite sus bajas (posible fallo)."""
    store1 = FakeStore(
        [_product(f"A{i}", f"P{i}", [_variant(f"A{i}-1", "20.00")]) for i in range(4)],
        signatures={f"A{i}": "s" for i in range(4)},
    )
    ingest(db_conn, store1, run_ts=T1, delist_min_baseline=3)

    # Solo aparece 1 de 4 (caída del 75% > umbral): sospechoso -> no se dan bajas.
    store2 = FakeStore(
        [_product("A0", "P0", [_variant("A0-1", "20.00")])],
        signatures={"A0": "s"},
        scopes=[ScrapeScope("niña", "zapateria", "zapatos")],
    )
    result = ingest(db_conn, store2, run_ts=T2, delist_min_baseline=3)

    assert result.skipped_scopes == 1
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NULL") == 4
    # Y la pasada sospechosa tampoco gasta intentos de histéresis: el contador no se mueve.
    assert _scalar(db_conn, "SELECT max(missing_streak) FROM product") == 0


def test_baja_normal_cuando_la_caida_es_moderada(db_conn: Any) -> None:
    """#2 (contraste): una caída moderada SÍ da de baja lo que falta."""
    store1 = FakeStore(
        [_product(f"A{i}", f"P{i}", [_variant(f"A{i}-1", "20.00")]) for i in range(4)],
        signatures={f"A{i}": "s" for i in range(4)},
    )
    ingest(db_conn, store1, run_ts=T1, delist_min_baseline=3)

    # Aparecen 3 de 4 (cae 1, dentro del umbral): A3 se da de baja con normalidad.
    store2 = FakeStore(
        [_product(f"A{i}", f"P{i}", [_variant(f"A{i}-1", "20.00")]) for i in range(3)],
        signatures={f"A{i}": "s" for i in range(3)},
        scopes=[ScrapeScope("niña", "zapateria", "zapatos")],
    )
    result = ingest(db_conn, store2, run_ts=T2, delist_min_baseline=3, delist_min_misses=1)

    assert result.skipped_scopes == 0
    assert result.products_delisted == 1
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A3'") == T2


# --- #4 Confirmación activa -------------------------------------------------------------


def _dos_productos() -> tuple[list[ScrapedProduct], dict[str, str]]:
    both = [
        _product("A", "Bailarina", [_variant("A-1", "39.95")]),
        _product("B", "Botín", [_variant("B-1", "45.00")]),
    ]
    return both, {"A": "a1", "B": "b1"}


def _solo_a() -> list[ScrapedProduct]:
    return [_product("A", "Bailarina", [_variant("A-1", "39.95")])]


def test_sondeo_rescata_al_que_sigue_a_la_venta(db_conn: Any) -> None:
    """#4: si la tienda confirma que B sigue vivo (se movió de categoría), no se da de baja."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": True})
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert store.probed == ["B"]
    assert (result.probes_sent, result.probes_alive, result.probes_dead) == (1, 1, 0)
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None
    # Rescatado del todo: producto y variantes vuelven a racha cero, sin bajas colgando.
    assert _streak(db_conn, "B") == 0
    assert _scalar(db_conn, "SELECT count(*) FROM variant WHERE delisted_at IS NOT NULL") == 0
    assert _scalar(db_conn, "SELECT max(missing_streak) FROM variant") == 0


def test_sondeo_confirma_la_retirada_y_da_de_baja(db_conn: Any) -> None:
    """#4 (contraste): confirmada la retirada, la baja sigue su curso normal."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": False})
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert (result.probes_sent, result.probes_alive, result.probes_dead) == (1, 0, 1)
    assert result.products_delisted == 1
    assert result.variants_delisted == 1
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") == T2


def test_sondeo_sin_veredicto_no_da_de_baja(db_conn: Any) -> None:
    """#4: sin confirmación (tienda bloqueada) no se descataloga; la racha se conserva."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={}, explode=True)
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert result.probes_unresolved == 1
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'") is None
    assert _streak(db_conn, "B") == 1  # no se rescata: sigue siendo candidato en la siguiente
    # El fallo se ve en el run para que la monitorización lo cace.
    assert _scalar(db_conn, "SELECT errors FROM scrape_run WHERE id = %s", (result.scrape_run_id,))


def test_sondeo_respeta_el_tope_y_no_da_de_baja_lo_no_sondeado(db_conn: Any) -> None:
    """#4: el tope acota las peticiones; lo que se queda fuera espera a otra pasada."""
    products = [_product(f"A{i}", f"P{i}", [_variant(f"A{i}-1", "20.00")]) for i in range(3)]
    sigs = {f"A{i}": "s" for i in range(3)}
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    # Faltan A1 y A2 (caída moderada: la red de seguridad no salta) y solo cabe un sondeo.
    store = ProbingFakeStore(
        [products[0]],
        signatures={"A0": "s"},
        verdicts={"A1": False, "A2": False},
        scopes=[ScrapeScope("niña", "zapateria", "zapatos")],
    )
    result = ingest(
        db_conn, store, run_ts=T2, delist_min_baseline=5, delist_min_misses=1, delist_probe_max=1
    )

    assert len(store.probed) == 1
    assert result.probes_sent == 1
    assert result.probes_unresolved == 1  # el que no cupo
    assert result.products_delisted == 1  # solo el confirmado
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NULL") == 2


def test_sondeo_desactivado_vuelve_a_la_histeresis(db_conn: Any) -> None:
    """#4: con `delist_probe=False` se descataloga por ausencia, sin preguntar a la tienda."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": True})
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1, delist_probe=False)

    assert store.probed == []
    assert result.probes_sent == 0
    assert result.products_delisted == 1


def test_tienda_sin_sondeo_mantiene_el_comportamiento(db_conn: Any) -> None:
    """#4: la capacidad es opcional; quien no la implementa da de baja por histéresis."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    result = ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T2)
    assert result.probes_sent == 0
    assert result.products_delisted == 0  # aún no llega al umbral de histéresis (2)

    result = ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T3)
    assert result.probes_sent == 0
    assert result.products_delisted == 1


def _last_detail(conn: Any, pid: str) -> Any:
    return _scalar(conn, "SELECT last_detail_at FROM product WHERE retailer_product_id=%s", (pid,))


def test_refresco_forzado_reobserva_aunque_no_cambie_nada(db_conn: Any) -> None:
    """El detalle rancio se vuelve a pedir: es lo que hace crecer la serie de una prenda estable."""
    store1 = FakeStore(_solo_a(), signatures={"A": "a1"})
    ingest(db_conn, store1, run_ts=T1)

    # Misma huella y mismo precio, pero 19 días después: toca volver a mirar.
    store2 = FakeStore(_solo_a(), signatures={"A": "a1"})
    result = ingest(db_conn, store2, run_ts=T_STALE)

    assert store2.detail_calls == ["A"]
    assert result.details_refreshed == 1
    assert result.products_unchanged == 0
    # La segunda observación es el punto de histórico que el aviso y la honestidad necesitaban.
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 2
    assert _scalar(db_conn, "SELECT count(DISTINCT price) FROM price_history") == 1
    assert _last_detail(db_conn, "A") == T_STALE


def test_detalle_reciente_no_se_refresca(db_conn: Any) -> None:
    """El ahorro del detalle condicional sigue intacto mientras la ficha no envejezca."""
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T1)

    store2 = FakeStore(_solo_a(), signatures={"A": "a1"})
    result = ingest(db_conn, store2, run_ts=T2)  # solo 1 día: dentro del umbral

    assert store2.detail_calls == []
    assert result.details_refreshed == 0
    assert result.products_unchanged == 1
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 1


def test_refresco_desactivado_mantiene_el_comportamiento(db_conn: Any) -> None:
    """`detail_max_age_days=0` es el escape hatch: se vuelve al detalle solo por huella."""
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T1)

    store2 = FakeStore(_solo_a(), signatures={"A": "a1"})
    result = ingest(db_conn, store2, run_ts=T_STALE, detail_max_age_days=0)

    assert store2.detail_calls == []
    assert result.details_refreshed == 0
    assert result.products_unchanged == 1


def test_refresco_respeta_el_tope_y_empieza_por_lo_mas_rancio(db_conn: Any) -> None:
    """El presupuesto por pasada reparte el coste, y la cola se sirve por antigüedad."""
    products = [_product(pid, f"P{pid}", [_variant(f"{pid}-1", "20.00")]) for pid in "ABC"]
    ingest(db_conn, FakeStore(products, signatures={"A": "a1", "B": "b1", "C": "c1"}), run_ts=T1)
    # Cambios de huella que renuevan el detalle de A y B; C se queda con el de T1.
    ingest(db_conn, FakeStore(products, signatures={"A": "a2", "B": "b1", "C": "c1"}), run_ts=T2)
    ingest(db_conn, FakeStore(products, signatures={"A": "a2", "B": "b2", "C": "c1"}), run_ts=T3)
    assert (_last_detail(db_conn, "A"), _last_detail(db_conn, "B")) == (T2, T3)

    store = FakeStore(products, signatures={"A": "a2", "B": "b2", "C": "c1"})
    result = ingest(db_conn, store, run_ts=T_STALE, detail_refresh_max=1)

    assert store.detail_calls == ["C"]  # el más rancio primero
    assert result.details_refreshed == 1
    assert result.products_unchanged == 2  # A y B esperan su turno en la siguiente pasada


def test_refresco_conserva_la_huella_y_no_encadena_refrescos(db_conn: Any) -> None:
    """Tras refrescar, la huella sigue siendo la del listado: la pasada siguiente no repite."""
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T1)
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T_STALE)

    assert _scalar(db_conn, "SELECT listing_signature FROM product") == "a1"

    store3 = FakeStore(_solo_a(), signatures={"A": "a1"})
    result = ingest(db_conn, store3, run_ts=T_STALE2)

    assert store3.detail_calls == []
    assert result.products_unchanged == 1


def test_refresco_no_provoca_bajas(db_conn: Any) -> None:
    """Refrescar no es "no haber visto": ni el producto ni sus variantes acumulan ausencia."""

    def catalogo() -> list[ScrapedProduct]:
        return [_product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "39.95")])]

    ingest(db_conn, FakeStore(catalogo(), signatures={"A": "a1"}), run_ts=T1)
    result = ingest(db_conn, FakeStore(catalogo(), signatures={"A": "a1"}), run_ts=T_STALE)

    assert result.details_refreshed == 1
    assert (result.products_delisted, result.variants_delisted) == (0, 0)
    assert (result.products_missing, result.variants_missing) == (0, 0)
    assert _scalar(db_conn, "SELECT max(missing_streak) FROM variant") == 0
    assert _scalar(db_conn, "SELECT count(*) FROM variant WHERE last_seen_at=%s", (T_STALE,)) == 2


IMG = "https://static.example/p/A-1.jpg?ts=1"


def test_foto_se_persiste_y_se_actualiza(db_conn: Any) -> None:
    """La foto primaria entra con el detalle y se actualiza cuando la tienda cambia la suya."""
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], image_url=IMG)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)
    assert _scalar(db_conn, "SELECT image_url FROM product") == IMG

    otra = "https://static.example/p/A-2.jpg?ts=2"
    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "34.95")], image_url=otra)],
        signatures={"A": "a2"},  # huella distinta -> se vuelve a pedir el detalle
    )
    ingest(db_conn, store2, run_ts=T2)
    assert _scalar(db_conn, "SELECT image_url FROM product") == otra


def test_pasada_sin_foto_no_borra_la_que_habia(db_conn: Any) -> None:
    """Una tienda que aún no da foto (o un parseo fallido) no debe dejar la ficha sin imagen."""
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], image_url=IMG)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "34.95")], image_url=None)],
        signatures={"A": "a2"},
    )
    ingest(db_conn, store2, run_ts=T2)

    assert _scalar(db_conn, "SELECT image_url FROM product") == IMG


# --- galería de fotos por color -------------------------------------------------------------

_GALERIA = [
    ScrapedImage(color="Negro", url="https://static.example/p/A-negro-0.jpg"),
    ScrapedImage(color="Negro", url="https://static.example/p/A-negro-1.jpg"),
    ScrapedImage(color="Rosa", url="https://static.example/p/A-rosa-0.jpg"),
]


def _galeria(conn: Any) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute("SELECT color, position, url FROM product_image ORDER BY color, position")
        return cur.fetchall()


def test_galeria_se_persiste_y_la_segunda_pasada_es_idempotente(db_conn: Any) -> None:
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=_GALERIA)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)
    assert _galeria(db_conn) == [
        ("Negro", 0, "https://static.example/p/A-negro-0.jpg"),
        ("Negro", 1, "https://static.example/p/A-negro-1.jpg"),
        ("Rosa", 0, "https://static.example/p/A-rosa-0.jpg"),
    ]

    # Huella cambiada -> se vuelve a pedir el detalle y a reemplazar la galería: mismo contenido,
    # sin duplicar (el reemplazo es DELETE + INSERT, no un INSERT ciego).
    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "34.95")], images=_GALERIA)],
        signatures={"A": "a2"},
    )
    ingest(db_conn, store2, run_ts=T2)
    assert _scalar(db_conn, "SELECT count(*) FROM product_image") == 3


def test_galeria_nueva_reemplaza_entera_a_la_anterior(db_conn: Any) -> None:
    """La tienda retira un color: sus fotos deben desaparecer, no fusionarse con las nuevas."""
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=_GALERIA)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    solo_negro = [ScrapedImage(color="Negro", url="https://static.example/p/nueva.jpg")]
    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "34.95")], images=solo_negro)],
        signatures={"A": "a2"},
    )
    ingest(db_conn, store2, run_ts=T2)

    assert _galeria(db_conn) == [("Negro", 0, "https://static.example/p/nueva.jpg")]


def test_pasada_sin_galeria_no_borra_la_que_habia(db_conn: Any) -> None:
    """Mismo criterio que `image_url`: lista vacía = "esta pasada no sabe de fotos", no "no hay"."""
    store1 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=_GALERIA)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store1, run_ts=T1)

    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "34.95")], images=[])],
        signatures={"A": "a2"},
    )
    ingest(db_conn, store2, run_ts=T2)

    assert _scalar(db_conn, "SELECT count(*) FROM product_image") == 3


def test_la_galeria_se_clava_con_el_color_de_las_variantes(db_conn: Any) -> None:
    """La invariante que sostiene el emparejamiento foto<->precio, comprobada ya en BD.

    Es el mismo SQL que se usa para verificar una pasada real: cero fotos con un color que
    ninguna variante del producto tenga.
    """
    store = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=_GALERIA[:2])],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store, run_ts=T1)

    huerfanas = _scalar(
        db_conn,
        """
        SELECT count(*) FROM product_image i
        WHERE i.color IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM variant v WHERE v.product_id = i.product_id AND v.color = i.color
          )
        """,
    )
    assert huerfanas == 0


def test_dos_colores_con_el_mismo_nombre_no_chocan(db_conn: Any) -> None:
    """Caso real de Lefties: dos colores distintos de la tienda con el MISMO nombre.

    Si cada scraper numerase por su cuenta, las dos series arrancarían en 0 y violarían el UNIQUE
    de `product_image`. Como la posición la asigna la ingesta por nombre de color, se fusionan en
    una sola galería — que es además lo que la ficha quiere, porque agrupa por nombre y para el
    usuario esos dos marrones son el mismo color.
    """
    galeria = [
        ScrapedImage(color="Marrón", url="https://static.example/p/marron-a.jpg"),
        ScrapedImage(color="Marrón", url="https://static.example/p/marron-b.jpg"),
        # ...y aquí la tienda cambia al segundo color, que se llama igual:
        ScrapedImage(color="Marrón", url="https://static.example/p/marron-c.jpg"),
    ]
    store = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=galeria)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store, run_ts=T1)

    assert _galeria(db_conn) == [
        ("Marrón", 0, "https://static.example/p/marron-a.jpg"),
        ("Marrón", 1, "https://static.example/p/marron-b.jpg"),
        ("Marrón", 2, "https://static.example/p/marron-c.jpg"),
    ]


def test_la_galeria_guarda_la_ficha_de_la_que_sale_cada_foto(db_conn: Any) -> None:
    """El caso de H&M (#123), que es el contrario del de Lefties de aquí arriba.

    Mismo nombre de color, pero dos ARTÍCULOS con ficha propia: fusionarlos enseña la foto de uno
    junto al precio del otro. `variant_url` los separa sin tocar la numeración.
    """
    galeria = [
        ScrapedImage(
            color="Azul marino", url="https://s.example/a-0.jpg", variant_url="https://t/a"
        ),
        ScrapedImage(
            color="Azul marino", url="https://s.example/a-1.jpg", variant_url="https://t/a"
        ),
        ScrapedImage(
            color="Azul marino", url="https://s.example/b-0.jpg", variant_url="https://t/b"
        ),
    ]
    store = FakeStore(
        [_product("A", "Pantalón", [_variant("A-1", "12.99")], images=galeria)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store, run_ts=T1)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT color, position, url, variant_url FROM product_image ORDER BY color, position"
        )
        filas = cur.fetchall()

    # La numeración NO cambia: sigue siendo por color y arranca en 0 una sola vez, que es lo que
    # deja intacta la consulta de la tarjeta del catálogo (join por position = 0).
    assert [(f[0], f[1]) for f in filas] == [
        ("Azul marino", 0),
        ("Azul marino", 1),
        ("Azul marino", 2),
    ]
    # Y cada foto sabe de qué ficha viene, que es lo que la ficha necesita para no mezclarlas.
    assert [f[3] for f in filas] == ["https://t/a", "https://t/a", "https://t/b"]


def test_una_tienda_que_no_distingue_fichas_deja_variant_url_a_null(db_conn: Any) -> None:
    """Las otras seis tiendas no pasan `variant_url`, y eso NO es un dato que falte.

    Es lo que hace que la ficha caiga al respaldo «fotos de este color sin ficha atribuida», o sea
    al comportamiento de siempre. Lo mismo vale para todo lo ingerido antes de la 0023.
    """
    store = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")], images=_GALERIA)],
        signatures={"A": "a1"},
    )
    ingest(db_conn, store, run_ts=T1)

    assert _scalar(db_conn, "SELECT count(*) FROM product_image WHERE variant_url IS NOT NULL") == 0


# --- #30 Marca barefoot -------------------------------------------------------------------


def test_la_marca_barefoot_se_persiste_y_la_ropa_queda_en_null(db_conn: Any) -> None:
    """NULL en la ropa no es "sin datos": es "la pregunta no aplica".

    Es la distinción de la que depende el filtro por defecto del catálogo, que deja pasar toda la
    ropa y solo el calzado `si`. Si la ropa entrara como `desconocido`, desaparecería del catálogo.
    """
    store = FakeStore(
        [
            _product("A", "Bailarina barefoot", [_variant("A-1", "39.95")], barefoot="si"),
            _product("B", "Botín de tacón", [_variant("B-1", "45.00")], barefoot="no"),
            _product("C", "Zapato sin señal", [_variant("C-1", "30.00")], barefoot="desconocido"),
            _product(
                "D",
                "Camiseta",
                [_variant("D-1", "9.95")],
                section="ropa",
                category="camisetas",
                barefoot=None,
            ),
        ],
        signatures={"A": "a1", "B": "b1", "C": "c1", "D": "d1"},
    )

    result = ingest(db_conn, store, run_ts=T1)

    with db_conn.cursor() as cur:
        cur.execute("SELECT retailer_product_id, barefoot FROM product ORDER BY 1")
        assert cur.fetchall() == [("A", "si"), ("B", "no"), ("C", "desconocido"), ("D", None)]

    # El informe que pide #30 sale de la propia pasada, y solo cuenta calzado.
    assert result.barefoot_counts == {"si": 1, "no": 1, "desconocido": 1}


def test_la_reclasificacion_pisa_el_valor_anterior(db_conn: Any) -> None:
    """Sin COALESCE, al revés que la foto: un `si` que pasa a `desconocido` debe degradarse.

    La clasificación se recalcula entera en cada pasada (categoría de la tienda, heurística y
    correcciones manuales); conservar lo viejo dejaría clavado un veredicto ya rectificado, que es
    justo lo que la lista de correcciones manuales existe para poder arreglar.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Bailarina", [_variant("A-1", "39.95")], barefoot="si")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )
    assert _scalar(db_conn, "SELECT barefoot FROM product WHERE retailer_product_id = 'A'") == "si"

    # Segunda pasada: cambia la huella (para que se pida el detalle) y el veredicto.
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Bailarina", [_variant("A-1", "39.95")], barefoot="desconocido")],
            signatures={"A": "a2"},
        ),
        run_ts=T2,
    )
    assert (
        _scalar(db_conn, "SELECT barefoot FROM product WHERE retailer_product_id = 'A'")
        == "desconocido"
    )


# --- #41 Hojas de categoría caídas: sin bajas falsas, y aborto si caen demasiadas ---------

_CAMISETAS = ScrapeScope("niña", "ropa", "camisetas")
_ZAPATOS = ScrapeScope("niña", "zapateria", "zapatos")


def _dos_ambitos() -> tuple[list[ScrapedProduct], dict[str, str]]:
    """Un producto en cada ámbito: uno cuya hoja se caerá y otro con la hoja sana."""
    return (
        [
            _product(
                "A", "Camiseta", [_variant("A-1", "9.95")], section="ropa", category="camisetas"
            ),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        {"A": "a1", "B": "b1"},
    )


def test_hoja_caida_no_da_de_baja_lo_que_no_se_ha_podido_mirar(db_conn: Any) -> None:
    """La trampa de #41: sin esto, saltar la hoja descataloga sus productos en dos pasadas.

    La red de seguridad por umbral no lo cubriría: un ámbito alimentado por varias hojas apenas
    baja al caerse una, así que la caída nunca llega al 50 % que dispara la sospecha.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    # Segunda pasada: no se ve NINGUNO de los dos, pero la hoja de camisetas está caída (1 de 4,
    # por debajo del umbral de aborto). Sin histéresis, para aislar el efecto del ámbito.
    store = ScanningFakeStore(
        [],
        signatures={},
        report=ScanReport(leaves_total=4, leaves_failed=1, failed_scopes={_CAMISETAS}),
        scopes=[_CAMISETAS, _ZAPATOS],
    )
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    # El de la hoja caída sobrevive intacto; el del ámbito sano sí se da de baja.
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A'") is None
    assert _streak(db_conn, "A") == 0  # ni siquiera gasta un intento de histéresis
    assert (
        _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='B'")
        is not None
    )
    assert (result.leaves_scanned, result.leaves_failed) == (4, 1)
    assert (result.unscanned_scopes, result.scanned_scopes) == (1, 1)


def test_demasiadas_hojas_caidas_abortan_la_pasada_sin_escribir(db_conn: Any) -> None:
    """Media tienda muerta no son categorías retiradas: es un bloqueo o un cambio de API."""
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    store = ScanningFakeStore(
        [],
        signatures={},
        report=ScanReport(leaves_total=4, leaves_failed=2, failed_scopes={_CAMISETAS, _ZAPATOS}),
        scopes=[_CAMISETAS, _ZAPATOS],
    )
    with pytest.raises(CatalogScanAborted):
        ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    # Rollback del contenido: el catálogo anterior queda intacto, sin bajas ni precios nuevos.
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NOT NULL") == 0
    assert _scalar(db_conn, "SELECT count(*) FROM price_history") == 2
    # Pero la pasada fallida SÍ deja rastro: si no, la avería solo se vería en los logs.
    assert _scalar(db_conn, "SELECT status FROM scrape_run ORDER BY id DESC LIMIT 1") == "failed"
    assert "CatalogScanAborted" in _scalar(
        db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1"
    )


def test_una_tienda_sin_informe_se_comporta_igual_que_siempre(db_conn: Any) -> None:
    """`SupportsScanReport` es opcional: quien no lo implemente no cambia de comportamiento."""
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    result = ingest(
        db_conn,
        FakeStore([], signatures={}, scopes=[_CAMISETAS, _ZAPATOS]),
        run_ts=T2,
        delist_min_misses=1,
    )

    assert (result.leaves_scanned, result.leaves_failed, result.unscanned_scopes) == (0, 0, 0)
    assert result.products_delisted == 2


def test_una_pasada_que_revienta_deja_rastro_en_scrape_run(db_conn: Any) -> None:
    """Vale para cualquier fallo, no solo para el aborto por hojas caídas.

    Antes, el rollback se llevaba por delante la fila de la pasada: una tienda podía estar días
    sin ingerir y en la BD no se distinguía de una tienda que nadie había programado todavía.
    """

    class TiendaRota(FakeStore):
        def list_catalog(self) -> Iterable[ListingEntry]:
            raise RuntimeError("la tienda nos ha bloqueado")

    with pytest.raises(RuntimeError):
        ingest(db_conn, TiendaRota([], signatures={}), run_ts=T1)

    assert _scalar(db_conn, "SELECT count(*) FROM product") == 0
    assert _scalar(db_conn, "SELECT status FROM scrape_run ORDER BY id DESC LIMIT 1") == "failed"
    assert (
        _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
        == "RuntimeError: la tienda nos ha bloqueado"
    )
    # La tienda queda dada de alta aunque no ingiriera nada: la fila de la pasada la necesita.
    assert _scalar(db_conn, "SELECT count(*) FROM retailer") == 1


def test_una_pasada_con_exito_registra_su_duracion(db_conn: Any) -> None:
    """`finished_at` tiene que ser la hora real de fin, no la de inicio de la transacción.

    La pasada entera va en UNA transacción, así que con `now()` —que en Postgres devuelve la hora
    de inicio de la transacción— `finished_at` salía igual que `started_at` y toda pasada con éxito
    quedaba registrada con duración cero. Se veía en `dev`: las cuatro pasadas buenas a 0,0 min y la
    única con duración real era una **fallida**, porque ese camino abre transacción nueva.

    Duele donde no se ve: el `activeDeadlineSeconds` de cada CronJob se fija a ojo si la BD no sabe
    cuánto tarda la tienda, y pasarse del deadline no es perder una pasada, es no poblar nunca
    (la ingesta es atómica).

    Por eso el test mide contra una pasada que **tarda**: sin la pausa, las dos implementaciones
    dan una diferencia indistinguible de cero y el test no probaría nada.
    """

    class TiendaLenta(FakeStore):
        def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
            time.sleep(_PAUSA_LENTA)
            yield from super().fetch_details(entries)

    store = TiendaLenta(
        [_product("A", "Bailarina", [_variant("A-1", "39.95")])],
        signatures={"A": "a1"},
    )
    # `run_ts` es lo que se guarda tal cual en `started_at`, así que tiene que ser de ahora para
    # que la resta signifique algo: con las T1..T4 fijas del resto del fichero, la diferencia
    # saldría en días y pasaría igual con la implementación vieja.
    result = ingest(db_conn, store, run_ts=datetime.now(UTC))

    duracion = _scalar(
        db_conn,
        "SELECT finished_at - started_at FROM scrape_run WHERE id = %s",
        (result.scrape_run_id,),
    )
    assert duracion is not None
    assert duracion.total_seconds() >= _PAUSA_LENTA / 2
