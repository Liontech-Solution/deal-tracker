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

from scraper import progreso as progreso_mod
from scraper.ingest import (
    CatalogScanAborted,
    ProbeOutcome,
    _ExistingProduct,
    _moved_out_counts,
    _success_message,
    ingest,
)
from scraper.stores.base import (
    DelistCandidate,
    ListingEntry,
    ProbeVerdict,
    ProductTags,
    ScanReport,
    ScrapedImage,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
)
from scraper.tags import TAG_DEPORTIVA

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
        verdicts: dict[str, ProbeVerdict],
        *,
        scopes: list[ScrapeScope] | None = None,
        explode: bool = False,
    ) -> None:
        super().__init__(products, signatures, scopes)
        self._verdicts = verdicts
        self._explode = explode
        self.probed: list[str] = []

    def probe_alive(self, candidates: Iterable[DelistCandidate]) -> Mapping[str, ProbeVerdict]:
        if self._explode:
            raise RuntimeError("tienda bloqueada")
        out: dict[str, ProbeVerdict] = {}
        for candidate in candidates:
            self.probed.append(candidate.retailer_product_id)
            verdict = self._verdicts.get(candidate.retailer_product_id)
            if verdict is not None:
                out[candidate.retailer_product_id] = verdict
        return out


class TaggingFakeStore(FakeStore):
    """Como `FakeStore`, pero declara ejes transversales (implementa `SupportsProductTags`)."""

    def __init__(
        self,
        products: list[ScrapedProduct],
        signatures: dict[str, str],
        tags: ProductTags,
        scopes: list[ScrapeScope] | None = None,
    ) -> None:
        super().__init__(products, signatures, scopes)
        self._tags = tags

    def product_tags(self) -> ProductTags:
        return self._tags


def _tags(por_producto: dict[str, set[str]], fiables: set[str] | None = None) -> ProductTags:
    """`fiables` por defecto son las etiquetas observadas: el caso normal, todo se pudo listar."""
    if fiables is None:
        fiables = {t for tags in por_producto.values() for t in tags}
    return ProductTags(por_producto=por_producto, fiables=fiables)


def _etiquetas(conn: Any) -> set[tuple[str, str]]:
    """`(retailer_product_id, tag)` de lo que hay guardado, que es lo que el filtro leerá."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.retailer_product_id, t.tag "
            "FROM product_tag t JOIN product p ON p.id = t.product_id"
        )
        return {(str(row[0]), str(row[1])) for row in cur.fetchall()}


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
    # Y CUÁL es (#170). El número solo dice que hay que ir a mirar; sin el nombre hay que recorrer
    # los ámbitos a mano, y mientras tanto ese ámbito no da bajas en ninguna pasada.
    assert result.skipped_scope_names == ["niña/zapateria/zapatos"]
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
    assert result.skipped_scope_names == [], "sin sospecha no hay nada que nombrar (ni un () vacío)"
    assert result.products_delisted == 1
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A3'") == T2


# --- #174 Una mudanza de ámbito no es una desaparición -----------------------------------


def _en_ambito(pid: str, gender: str) -> ScrapedProduct:
    return _product(pid, f"P{pid}", [_variant(f"{pid}-1", "20.00")], gender=gender)


def test_una_mudanza_de_ambito_no_es_caida_sospechosa(db_conn: Any) -> None:
    """#174: el caso de Hipercor. Reclasificar no puede leerse como que la tienda se ha roto.

    El ámbito de origen se queda a cero y aun así no es sospechoso, porque sus cuatro productos
    están ahí mismo, listados bajo `unisex`. Las huellas NO cambian a propósito: así no se pide
    detalle y el `gender` guardado sigue siendo el viejo, que es exactamente el estado en el que
    la run #45 de `dev` dio el falso positivo.
    """
    sigs = {f"A{i}": "s" for i in range(4)}
    ingest(
        db_conn,
        FakeStore([_en_ambito(f"A{i}", "niña") for i in range(4)], signatures=sigs),
        run_ts=T1,
        delist_min_baseline=3,
    )

    mudados = FakeStore(
        [_en_ambito(f"A{i}", "unisex") for i in range(4)],
        signatures=sigs,
        scopes=[
            ScrapeScope("niña", "zapateria", "zapatos"),
            ScrapeScope("unisex", "zapateria", "zapatos"),
        ],
    )
    result = ingest(db_conn, mudados, run_ts=T2, delist_min_baseline=3, delist_min_misses=1)

    assert result.skipped_scopes == 0, "el ámbito de origen se vació, pero nadie ha desaparecido"
    assert result.skipped_scope_names == []
    assert result.remapped_scopes == 1
    assert result.remapped_scope_names == ["niña/zapateria/zapatos"]
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NULL") == 4
    # Y queda escrito en la fila de la pasada, no solo en el log de un pod que se recicla.
    message = _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
    assert message is not None
    assert "ambitos remapeados: niña/zapateria/zapatos" in message
    assert "caida sospechosa" not in message
    # El rescate no es un error: `errors` cuenta fallos, y aquí no ha habido ninguno.
    assert _scalar(db_conn, "SELECT errors FROM scrape_run ORDER BY id DESC LIMIT 1") == 0


def test_la_mudanza_no_rescata_al_ambito_que_se_vacia_de_verdad(db_conn: Any) -> None:
    """#174 (contraste): el que desaparece sin reaparecer en otro sitio SIGUE protegido.

    Es la garantía de que descontar mudanzas no afloja la red: si el rescate se aplicara por
    totales de tienda, este ámbito la perdería en cuanto otro creciera al mismo tiempo.
    """
    sigs = {f"A{i}": "s" for i in range(4)} | {f"B{i}": "s" for i in range(4)}
    ingest(
        db_conn,
        FakeStore(
            [_en_ambito(f"A{i}", "niña") for i in range(4)]
            + [_en_ambito(f"B{i}", "niño") for i in range(4)],
            signatures=sigs,
        ),
        run_ts=T1,
        delist_min_baseline=3,
    )

    # `niña` se muda entera a `unisex`; de `niño` desaparecen 3 de 4 sin aparecer en ningún lado.
    store2 = FakeStore(
        [_en_ambito(f"A{i}", "unisex") for i in range(4)] + [_en_ambito("B0", "niño")],
        signatures={f"A{i}": "s" for i in range(4)} | {"B0": "s"},
        scopes=[
            ScrapeScope("niña", "zapateria", "zapatos"),
            ScrapeScope("niño", "zapateria", "zapatos"),
            ScrapeScope("unisex", "zapateria", "zapatos"),
        ],
    )
    result = ingest(db_conn, store2, run_ts=T2, delist_min_baseline=3, delist_min_misses=1)

    assert result.remapped_scope_names == ["niña/zapateria/zapatos"]
    assert result.skipped_scope_names == ["niño/zapateria/zapatos"], "esta caída sí es sospechosa"
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT max(missing_streak) FROM product") == 0


def test_mudanza_parcial_da_de_baja_lo_que_falta_de_verdad(db_conn: Any) -> None:
    """#174: descontar la mudanza no indulta al que sí falta, solo quita la sospecha del ámbito."""
    sigs = {f"A{i}": "s" for i in range(4)}
    ingest(
        db_conn,
        FakeStore([_en_ambito(f"A{i}", "niña") for i in range(4)], signatures=sigs),
        run_ts=T1,
        delist_min_baseline=3,
    )

    # Tres se mudan a `unisex` y A3 desaparece de verdad. Sin descontar, el ámbito de origen caería
    # de 4 a 0 y omitiría las bajas; descontando, A3 sigue su camino normal.
    store2 = FakeStore(
        [_en_ambito(f"A{i}", "unisex") for i in range(3)],
        signatures={f"A{i}": "s" for i in range(3)},
        scopes=[
            ScrapeScope("niña", "zapateria", "zapatos"),
            ScrapeScope("unisex", "zapateria", "zapatos"),
        ],
    )
    result = ingest(db_conn, store2, run_ts=T2, delist_min_baseline=3, delist_min_misses=1)

    assert result.skipped_scopes == 0
    assert result.products_delisted == 1
    assert _scalar(db_conn, "SELECT delisted_at FROM product WHERE retailer_product_id='A3'") == T2


def test_moved_out_no_cuenta_a_los_que_ya_estaban_de_baja() -> None:
    """#174 (unitario): un producto dado de baja que reaparece en otro ámbito no es una mudanza.

    `prior_active` no lo contaba, así que sumarlo restaría de una caída que sí es real — y es un
    caso que pasa de verdad: un producto se descataloga, la tienda lo repesca y de paso lo publica
    en otra rama.
    """
    existing = {
        "vivo": _ExistingProduct(
            id=1,
            signature="s",
            delisted=False,
            last_detail_at=None,
            gender="niña",
            section="zapateria",
            category="zapatos",
        ),
        "de-baja": _ExistingProduct(
            id=2,
            signature="s",
            delisted=True,
            last_detail_at=None,
            gender="niña",
            section="zapateria",
            category="zapatos",
        ),
    }
    entries = [
        ListingEntry("vivo", "s", "unisex", "zapateria", "zapatos"),
        ListingEntry("de-baja", "s", "unisex", "zapateria", "zapatos"),
        ListingEntry("nuevo", "s", "unisex", "zapateria", "zapatos"),  # sin pasado: tampoco cuenta
    ]

    assert _moved_out_counts(entries, existing) == {("niña", "zapateria", "zapatos"): 1}


def test_moved_out_ignora_al_que_se_queda_donde_estaba() -> None:
    """#174 (unitario): sin cambio de ámbito no hay nada que descontar (ni una clave a cero)."""
    existing = {
        "quieto": _ExistingProduct(
            id=1,
            signature="s",
            delisted=False,
            last_detail_at=None,
            gender="niña",
            section="zapateria",
            category="zapatos",
        )
    }
    entries = [ListingEntry("quieto", "s", "niña", "zapateria", "zapatos")]

    assert _moved_out_counts(entries, existing) == {}


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

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": ProbeVerdict.ALIVE})
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

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": ProbeVerdict.DEAD})
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
        verdicts={"A1": ProbeVerdict.DEAD, "A2": ProbeVerdict.DEAD},
        scopes=[ScrapeScope("niña", "zapateria", "zapatos")],
    )
    result = ingest(
        db_conn, store, run_ts=T2, delist_min_baseline=5, delist_min_misses=1, delist_probe_max=1
    )

    assert len(store.probed) == 1
    assert result.probes_sent == 1
    assert result.probes_over_cap == 1  # el que no cupo: rutina, no error (#261)
    assert result.probes_unresolved == 0
    assert result.products_delisted == 1  # solo el confirmado
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NULL") == 2


def _sondeos(conn: Any, run_ts: Any) -> tuple[Any, ...]:
    """`(errors, sent, alive, dead, over_cap, unresolved, unbuyable, limpia)` de esa pasada."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT errors, probes_sent, probes_alive, probes_dead, probes_over_cap, "
            "probes_unresolved, probes_unbuyable, message IS NULL FROM scrape_run "
            "WHERE started_at = %s",
            (run_ts,),
        )
        row = cur.fetchone()
    assert row is not None
    return tuple(row)


def test_fuera_del_tope_no_cuenta_como_error_y_queda_en_su_columna(db_conn: Any) -> None:
    """#261: un candidato que no cupo en el tope no es un error; se persiste en `probes_over_cap`.

    Es lo que hacía ilegible el `errors = 60` de Zara: la pasada estaba limpia y el número decía
    otra cosa. Se afirma sobre la FILA, no sobre `IngestResult`, porque el valor de esto es que la
    pregunta se responda con una consulta sin abrir el log de un pod que se recicla.
    """
    products = [_product(f"A{i}", f"P{i}", [_variant(f"A{i}-1", "20.00")]) for i in range(3)]
    sigs = {f"A{i}": "s" for i in range(3)}
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    # Faltan A1 y A2; solo cabe un sondeo y el que se sondea sigue a la venta.
    store = ProbingFakeStore(
        [products[0]],
        signatures={"A0": "s"},
        verdicts={"A1": ProbeVerdict.ALIVE, "A2": ProbeVerdict.ALIVE},
        scopes=[ScrapeScope("niña", "zapateria", "zapatos")],
    )
    ingest(
        db_conn, store, run_ts=T2, delist_min_baseline=5, delist_min_misses=1, delist_probe_max=1
    )

    # errors = 0 y `message` a NULL: la pasada está limpia, que es justo lo que antes no se veía.
    assert _sondeos(db_conn, T2) == (0, 1, 1, 0, 1, 0, 0, True)


def test_agotado_no_cuenta_como_error_y_queda_en_su_columna(db_conn: Any) -> None:
    """#197: la tienda contesta que existe pero sin talla comprable. Ni baja, ni rescate, ni error.

    Es el gemelo del test de #261 de arriba y por el mismo motivo: son 33 productos de Lefties en
    TODAS las pasadas, así que meterlos en `errors` dejaría a la tienda con un número permanente
    que no significa nada — que es exactamente lo que hacía ilegible el `errors = 60` de Zara.

    Lo que sí tiene que pasar es que el producto **conserve la racha**: si se rescatara (racha a 0)
    volvería a empezar el conteo en cada pasada y se quedaría en el catálogo para siempre con su
    último precio rebajado, que es el fallo que abrió la issue.
    """
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    # B ha desaparecido del listado y la tienda dice que existe, pero agotado del todo.
    store = ProbingFakeStore(
        _solo_a(), signatures={"A": "a1"}, verdicts={"B": ProbeVerdict.UNBUYABLE}
    )
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    errors, sent, alive, dead, over_cap, unresolved, unbuyable, limpia = _sondeos(db_conn, T2)
    assert (sent, alive, dead, over_cap, unresolved, unbuyable) == (1, 0, 0, 0, 0, 1)
    assert errors == 0 and limpia, "no es un fallo: la tienda ha contestado, y con claridad"

    # Ni se da de baja...
    assert result.products_delisted == 0
    assert _scalar(db_conn, "SELECT count(*) FROM product WHERE delisted_at IS NULL") == 2
    # ...ni se rescata: la racha sigue subiendo, que es lo que hace el estado observable.
    assert _scalar(db_conn, "SELECT missing_streak FROM product WHERE retailer_product_id = 'B'")


def test_sondeo_sin_veredicto_si_cuenta_como_error(db_conn: Any) -> None:
    """#261: la contrapartida — una tienda que no contesta NO se puede quedar en silencio.

    Es la mitad que no se toca al sacar los sondeos de `errors`: que la tienda deje de dejarnos
    entrar es el fallo silencioso que el proyecto entero existe para cazar.
    """
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={}, explode=True)
    ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    errors, sent, _alive, _dead, over_cap, unresolved, _unbuyable, _limpia = _sondeos(db_conn, T2)
    assert (sent, over_cap, unresolved) == (1, 0, 1)
    assert errors == 1  # el sondeo sin veredicto sí suma


def test_el_sondeo_mudo_llega_hasta_scrape_run_message(db_conn: Any) -> None:
    """De punta a punta: `ProbeOutcome` -> `_success_message` -> la fila de la pasada (#357).

    `errors` ya contaba estos sondeos, pero un número sin explicación es lo que dejó a Sfera
    mandando 45 de 45 sin veredicto durante semanas mientras la única pista era un `errors = 45`
    que nadie sabía leer. Se afirma sobre la FILA porque ahí es donde se lee meses después, cuando
    el log del pod hace tiempo que se recicló.
    """
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={}, explode=True)
    ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert _sondeos(db_conn, T2)[-1] is False  # `message` ya no es NULL
    mensaje = _scalar(db_conn, "SELECT message FROM scrape_run WHERE started_at = %s", (T2,))
    assert "sondeo sin respuesta" in mensaje
    assert "1 de 1" in mensaje


def test_pasada_limpia_deja_los_sondeos_a_cero(db_conn: Any) -> None:
    """#261: sin candidatos, las cinco columnas quedan a 0 — el caso que hace útil la consulta."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    assert _sondeos(db_conn, T1) == (0, 0, 0, 0, 0, 0, 0, True)


def test_sondeo_desactivado_vuelve_a_la_histeresis(db_conn: Any) -> None:
    """#4: con `delist_probe=False` se descataloga por ausencia, sin preguntar a la tienda."""
    both, sigs = _dos_productos()
    ingest(db_conn, FakeStore(both, signatures=sigs), run_ts=T1)

    store = ProbingFakeStore(_solo_a(), signatures={"A": "a1"}, verdicts={"B": ProbeVerdict.ALIVE})
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


def test_refresh_all_reobserva_un_detalle_reciente(db_conn: Any) -> None:
    """#143: la reobservación bajo demanda no mira la edad, que es justo lo que la hacía imposible.

    Sin esto, un catálogo ingerido hace horas no se puede volver a observar: el umbral es un entero
    en días y su valor más agresivo sigue siendo un día.
    """
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T1)

    store2 = FakeStore(_solo_a(), signatures={"A": "a1"})
    # T2 es un día: sin la palanca, este mismo caso es `test_detalle_reciente_no_se_refresca`.
    result = ingest(db_conn, store2, run_ts=T2, detail_refresh_all=True)

    assert store2.detail_calls == ["A"]
    assert result.details_refreshed == 1
    assert result.products_unchanged == 0
    assert _last_detail(db_conn, "A") == T2


def test_refresh_all_respeta_el_tope_y_empieza_por_lo_mas_rancio(db_conn: Any) -> None:
    """Salta el umbral de EDAD, no el de PRESUPUESTO: sin el tope, esto es una pasada en frío."""
    products = [_product(pid, f"P{pid}", [_variant(f"{pid}-1", "20.00")]) for pid in "ABC"]
    ingest(db_conn, FakeStore(products, signatures={"A": "a1", "B": "b1", "C": "c1"}), run_ts=T1)
    # Cambios de huella que renuevan el detalle de A y B; C se queda con el de T1.
    ingest(db_conn, FakeStore(products, signatures={"A": "a2", "B": "b1", "C": "c1"}), run_ts=T2)
    ingest(db_conn, FakeStore(products, signatures={"A": "a2", "B": "b2", "C": "c1"}), run_ts=T3)

    # T3 es el mismo día del último detalle: ninguno de los tres es rancio para el umbral.
    store = FakeStore(products, signatures={"A": "a2", "B": "b2", "C": "c1"})
    result = ingest(db_conn, store, run_ts=T3, detail_refresh_max=1, detail_refresh_all=True)

    assert store.detail_calls == ["C"]  # el más rancio primero, igual que el refresco por edad
    assert result.details_refreshed == 1
    assert result.products_unchanged == 2


def test_refresh_all_gana_al_escape_hatch(db_conn: Any) -> None:
    """`detail_max_age_days=0` apaga el refresco *periódico*; pedirlo a mano es otra pregunta.

    Que las dos cosas se dijeran con el mismo parámetro era el problema de #143, así que conviene
    que quede fijado que no se estorban.
    """
    ingest(db_conn, FakeStore(_solo_a(), signatures={"A": "a1"}), run_ts=T1)

    store2 = FakeStore(_solo_a(), signatures={"A": "a1"})
    result = ingest(db_conn, store2, run_ts=T_STALE, detail_max_age_days=0, detail_refresh_all=True)

    assert store2.detail_calls == ["A"]
    assert result.details_refreshed == 1


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


# --- #139 Reparto de género y género rancio -----------------------------------------------


def test_el_reparto_de_genero_sale_del_listado_de_la_pasada(db_conn: Any) -> None:
    """El otro eje del brief, publicado por la pasada en vez de por una consulta a mano.

    Sale del listado y no de la base a propósito: es lo que ESTA pasada ha decidido, que es justo
    lo que no se veía en ningún sitio cuando la base decía otra cosa (#139).
    """
    store = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95")], gender="niña"),
            _product("B", "Botín", [_variant("B-1", "45.00")], gender="niño"),
            _product("C", "Camiseta", [_variant("C-1", "9.95")], gender="unisex"),
            _product("D", "Zapato", [_variant("D-1", "30.00")], gender="unisex"),
        ],
        signatures={"A": "a1", "B": "b1", "C": "c1", "D": "d1"},
    )

    result = ingest(db_conn, store, run_ts=T1)

    assert result.gender_counts == {"niña": 1, "niño": 1, "unisex": 2}
    assert result.gender_stale == 0


def test_el_genero_guardado_no_se_reescribe_sin_detalle_y_la_pasada_lo_cuenta(
    db_conn: Any,
) -> None:
    """El fondo de #139: una tienda ingerida antes de un arreglo de género conserva el viejo.

    `gender` solo lo escribe `_upsert_product`, o sea el detalle. Un producto cuya huella no cambia
    pasa por `_touch_seen` y se queda con el género anterior, así que el catálogo puede contradecir
    al código que lo produjo sin que nada falle. La cifra existe para que eso se vea en el resumen
    y no haga falta una consulta a mano contra la base para descubrirlo.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="niña")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )

    # Segunda pasada: la tienda ya lo publica en las dos ramas (`unisex`), pero la huella no ha
    # cambiado, así que no se le pide detalle y la fila conserva el `niña`.
    result = ingest(
        db_conn,
        FakeStore(
            [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="unisex")],
            signatures={"A": "a1"},
        ),
        run_ts=T2,
    )

    assert result.gender_counts == {"unisex": 1}
    assert result.gender_stale == 1
    assert _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "niña"


def test_el_refresco_forzado_arregla_el_genero_rancio(db_conn: Any) -> None:
    """Y esta es la salida: sin cambio de huella, quien lo corrige es el refresco periódico.

    Importa porque es lo que decide cuánto tarda en repararse un entorno ya ingerido: no se arregla
    en la siguiente pasada, se arregla cuando al producto le toca el turno de refresco.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="niña")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )
    store = FakeStore(
        [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="unisex")],
        signatures={"A": "a1"},
    )

    # T_STALE deja el detalle por encima del umbral de 7 días: entra en el refresco forzado.
    result = ingest(db_conn, store, run_ts=T_STALE)

    assert store.detail_calls == ["A"]
    assert result.gender_stale == 0
    assert (
        _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "unisex"
    )


def test_refresh_all_repara_el_genero_sin_esperar_al_umbral(db_conn: Any) -> None:
    """El criterio de cierre de #143, en pequeño: reparar hoy lo ingerido hoy.

    Es el caso del test anterior con el reloj en contra — que fue exactamente lo que pasó en `dev`
    con Lefties (#139): el `last_detail_at` más viejo tenía 21 h 27 min y el Job de cura refrescó
    cero productos, así que la única salida era esperar a que el día cumpliese.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="niña")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )
    store = FakeStore(
        [_product("A", "Camiseta", [_variant("A-1", "9.95")], gender="unisex")],
        signatures={"A": "a1"},
    )

    result = ingest(db_conn, store, run_ts=T2, detail_refresh_all=True)  # un día: sin la palanca, 0

    assert store.detail_calls == ["A"]
    assert result.gender_stale == 0
    assert (
        _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "unisex"
    )


# --- #172 Una hoja caída no puede reetiquetar el género de lo que sí se vio ----------------
#
# Las tiendas que colapsan géneros (`ambito_cruzado()`) marcan `unisex` lo que la tienda publica en
# las dos ramas. Si una rama se cae, el producto solo se ve en la otra y el listado lo emite con el
# género de la superviviente: eso NO se persiste, porque no es un cambio, es media observación.


def _sin_una_rama(caida: ScrapeScope, leaf: str = "hoja-x") -> ScanReport:
    """Un informe con esa hoja caída, construido por el camino real (`leaf_gone`).

    A mano no valdría: lo que se está probando es justo lo que `leaf_gone` deduce de la hoja que
    recibe, así que un `ScanReport(...)` con los conjuntos puestos a dedo probaría el test.
    """
    report = ScanReport()
    for _ in range(3):
        report.leaf_ok()
    report.leaf_gone(caida, leaf, tambien_unisex=True)
    return report


def test_la_rama_superviviente_no_degrada_el_unisex_guardado(db_conn: Any) -> None:
    """El fondo de #172: 32 productos de Hipercor pasaron de `unisex` a `niña` sin moverse.

    Con la hoja de niño caída, el listado ve el producto solo en la de niña y lo emite como `niña`.
    El género entra en el alcance de un interés (`interest.gender`), así que persistirlo lo saca
    del aviso que su usuario había pedido — y ahí se queda hasta la siguiente pasada completa.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="unisex")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )

    # La huella cambia, así que esta vez SÍ se le pide el detalle: es la condición para que el
    # género llegue a escribirse (`_upsert_product`), o sea para que el daño sea real.
    store = ScanningFakeStore(
        [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="niña")],
        signatures={"A": "a2"},
        report=_sin_una_rama(ScrapeScope("niño", "zapateria", "zapatos")),
        scopes=[_ZAPATOS, ScrapeScope("niño", "zapateria", "zapatos")],
    )
    result = ingest(db_conn, store, run_ts=T2)

    assert store.detail_calls == ["A"]  # se le pidió el detalle: el género pasó por el upsert
    assert (
        _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "unisex"
    )
    assert result.gender_frozen == 1
    # El reparto del listado NO se maquilla: dice lo que la tienda enseñó, que es la señal de que
    # se ha caído una rama. Lo que cambia es lo que se guarda.
    assert result.gender_counts == {"niña": 1}
    # Y queda escrito en la fila de la pasada, no solo en el stdout de un pod que se recicla.
    assert "generos conservados: 1" in _scalar(
        db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1"
    )


def test_sin_hoja_caida_el_genero_se_corrige_como_siempre(db_conn: Any) -> None:
    """La protección no puede congelar las correcciones legítimas: es media issue #172.

    Mismo escenario que el test anterior sin la hoja caída. Si esto se rompe, la tienda que deja de
    publicar un producto en las dos ramas nunca podría volver a tener género propio.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="unisex")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )

    result = ingest(
        db_conn,
        FakeStore(
            [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="niña")],
            signatures={"A": "a2"},
        ),
        run_ts=T2,
    )

    assert _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "niña"
    assert result.gender_frozen == 0


def test_solo_se_protege_el_unisex_no_un_cambio_de_nino_a_nina(db_conn: Any) -> None:
    """La regla es estrecha a propósito: un `niño`→`niña` no es el artefacto del cruce.

    Un producto que estaba en `niño` y ahora se lista en `niña` no puede ser el efecto de la rama
    caída (ese siempre viene de un `unisex`), así que congelarlo sería frenar una corrección real
    por un motivo que no le aplica.
    """
    ingest(
        db_conn,
        FakeStore(
            [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="niño")],
            signatures={"A": "a1"},
        ),
        run_ts=T1,
    )

    store = ScanningFakeStore(
        [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="niña")],
        signatures={"A": "a2"},
        report=_sin_una_rama(ScrapeScope("niño", "zapateria", "zapatos")),
        scopes=[_ZAPATOS, ScrapeScope("niño", "zapateria", "zapatos")],
    )
    result = ingest(db_conn, store, run_ts=T2)

    assert _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "niña"
    assert result.gender_frozen == 0


def test_un_producto_nuevo_en_el_ambito_sospechoso_se_guarda_como_lo_ve_el_listado(
    db_conn: Any,
) -> None:
    """El límite aceptado de #172: de lo que no existe todavía no hay nada que conservar.

    Un producto cruzado que aparece POR PRIMERA VEZ durante una pasada con la rama contraria caída
    se guarda con el género de la superviviente, porque es la única información que hay. Se cura
    cuando la hoja vuelve y le toca detalle, igual que el `gender_stale` de siempre.
    """
    store = ScanningFakeStore(
        [_product("A", "Zapato", [_variant("A-1", "39.95")], gender="niña")],
        signatures={"A": "a1"},
        report=_sin_una_rama(ScrapeScope("niño", "zapateria", "zapatos")),
        scopes=[_ZAPATOS, ScrapeScope("niño", "zapateria", "zapatos")],
    )
    result = ingest(db_conn, store, run_ts=T1)

    assert _scalar(db_conn, "SELECT gender FROM product WHERE retailer_product_id = 'A'") == "niña"
    assert result.gender_frozen == 0


def test_leaf_gone_marca_la_rama_contraria_sin_contarla_como_hoja() -> None:
    """Las dos consecuencias de una hoja caída, que son distintas y solo una estaba atendida.

    Sacar el `unisex` de las bajas ya estaba; señalar la rama superviviente es lo que faltaba. Y
    ninguna de las dos puede inflar `dead_ratio`: es una hoja caída, no tres.
    """
    report = ScanReport()
    report.leaf_gone(ScrapeScope("niño", "zapateria", "zapatos"), "hoja-nino", tambien_unisex=True)

    assert report.cross_gender_suspect == {ScrapeScope("niña", "zapateria", "zapatos")}
    assert report.failed_scopes == {
        ScrapeScope("niño", "zapateria", "zapatos"),
        ScrapeScope("unisex", "zapateria", "zapatos"),
    }
    assert (report.leaves_total, report.leaves_failed) == (1, 1)
    assert report.failed_leaves == ["hoja-nino"]


def test_una_hoja_unisex_caida_no_deja_ninguna_rama_bajo_sospecha() -> None:
    """El falso positivo que hay que evitar: Mango tiene hoy una hoja `unisex` caída (#176).

    Ahí no hay ninguna rama superviviente que pueda mentir sobre el género, así que congelar algo
    por eso sería frenar correcciones legítimas sin ganar nada.
    """
    report = ScanReport()
    report.leaf_gone(
        ScrapeScope("unisex", "ropa", "sudaderas"), "sudaderas_newborn", tambien_unisex=True
    )

    assert report.cross_gender_suspect == set()
    assert report.failed_scopes == {ScrapeScope("unisex", "ropa", "sudaderas")}


def test_filtro_vacio_saca_el_ambito_de_las_bajas_sin_contarlo_como_hoja_caida() -> None:
    """Una hoja que responde pero cuyo filtro no casa con nada (#200).

    Las dos mitades importan y son opuestas: el ámbito TIENE que salir de las bajas —si no, un
    cambio de rotulación descatalogaría de golpe todos los conjuntos ya ingeridos— y NO puede
    contar como hoja caída, porque la hoja se ha listado y en Sfera además ha emitido su `resto`.
    Si contara, `dead_ratio` subiría y `SCRAPER_SCAN_MAX_DEAD_RATIO` abortaría pasadas buenas.
    """
    report = ScanReport()
    for _ in range(4):
        report.leaf_ok()
    report.filtro_vacio(ScrapeScope("niña", "ropa", "conjuntos"), "ninos/nina/ropa-deportiva")

    assert report.failed_scopes == {ScrapeScope("niña", "ropa", "conjuntos")}
    assert report.empty_filter_leaves == ["ninos/nina/ropa-deportiva"]
    # Ni una hoja caída ni un nombre en la lista de caídas: son cosas distintas.
    assert (report.leaves_total, report.leaves_failed) == (4, 0)
    assert report.failed_leaves == []
    assert report.dead_ratio == 0.0


def test_el_mensaje_de_la_pasada_nombra_la_hoja_con_el_filtro_vacio() -> None:
    """Sin esto el caso sería mudo, que es justo el fallo que `filtro_vacio` viene a quitar (#200).

    El mensaje solo nombraba ámbitos cuando había hojas caídas, y aquí no hay ninguna: la pasada se
    ve perfecta y hay una categoría entera sin detección de bajas.
    """
    report = ScanReport()
    report.leaf_ok()
    report.filtro_vacio(
        ScrapeScope("niño", "ropa", "conjuntos"), "/kids/boys/clothing/sets-outfits"
    )

    mensaje = _success_message(report, suspicious=set())

    assert mensaje is not None
    assert "/kids/boys/clothing/sets-outfits" in mensaje
    assert "niño/ropa/conjuntos" in mensaje


def test_el_residuo_no_saca_el_ambito_de_las_bajas_ni_cuenta_como_hoja() -> None:
    """La decisión conservadora de #358: una hoja sin residuo se publica, no cambia comportamiento.

    Que un lookbook no tenga residuo clasificable es un estado legítimo —puede que hoy no le quede
    nada fuera de sus conjuntos—, así que sacar su ámbito de las bajas por eso metería falsos
    positivos en el camino más delicado del scraper. Lo que faltaba era la señal, no una red nueva.
    """
    report = ScanReport()
    for _ in range(4):
        report.leaf_ok()
    report.residuo("2622124", 0)

    assert report.barren_residual_leaves == ["2622124"]
    assert report.residual_entries == 0
    # Y nada más se mueve: ni bajas, ni `dead_ratio`, ni la lista de hojas caídas.
    assert report.failed_scopes == set()
    assert (report.leaves_total, report.leaves_failed) == (4, 0)
    assert report.failed_leaves == []
    assert report.dead_ratio == 0.0


def test_el_residuo_suma_por_hojas_y_solo_marca_las_secas() -> None:
    report = ScanReport()
    report.residuo("2622124", 25)
    report.residuo("2428167", 16)
    report.residuo("2426354", 0)

    assert report.residual_entries == 41
    assert report.barren_residual_leaves == ["2426354"]


def test_el_mensaje_nombra_la_hoja_que_ha_dejado_de_aportar_residuo() -> None:
    """Sobrevive al reciclado del log del pod, que es donde se lee meses después (#358)."""
    report = ScanReport()
    report.leaf_ok()
    report.residuo("2622124", 0)

    mensaje = _success_message(report, suspicious=set())

    assert mensaje is not None
    assert "2622124" in mensaje


def test_el_mensaje_NO_publica_la_cifra_del_residuo_cuando_todo_va_bien() -> None:
    """El contrato que hace útil `WHERE message IS NOT NULL`, y que el residuo podía romper.

    El rescate aporta decenas de prendas en TODAS las pasadas de Zara: publicar el total aquí
    dejaría `message` distinto de NULL siempre, y entonces la consulta que sirve para encontrar las
    pasadas con algo que contar dejaría de distinguir nada. La cifra va al resumen de `run.py`.
    """
    report = ScanReport()
    report.leaf_ok()
    report.residuo("2622124", 25)

    assert _success_message(report, suspicious=set()) is None


def test_el_mensaje_nombra_el_sondeo_que_volvio_entero_sin_veredicto() -> None:
    """La firma de la tienda que no contesta (#357).

    Sfera llevaba pasadas mandando sondeos que volvían enteros sin respuesta —45 de 45 el
    10/08/2026— y lo único visible era un `errors` sin explicación: los sondeos sin veredicto
    suman ahí, pero no los nombraba nadie. `_confirm_candidates` envuelve `probe_alive()` en un
    `except Exception`, así que un fallo de transporte deja a TODOS los candidatos sin veredicto
    de una vez y se ve como `unresolved == sent` exacto.
    """
    report = ScanReport()
    report.leaf_ok()

    mensaje = _success_message(report, suspicious=set(), probe=ProbeOutcome(sent=45, unresolved=45))

    assert mensaje is not None
    assert "sondeo sin respuesta" in mensaje
    assert "45 de 45" in mensaje


def test_el_mensaje_NO_habla_de_agotados_ni_cuando_lo_estan_todos() -> None:
    """#197: `unbuyable` se queda FUERA de `message`, y es una decisión medida.

    Tentador ponerlo, porque «todos agotados» es también la firma de la señal de stock rota. Pero
    el umbral «todos» que sirve para `unresolved` aquí falsea desde el caso más pequeño: **un solo**
    candidato legítimamente agotado ya cumple `unbuyable == sent`, y en Lefties eso va a pasar en
    pasadas sanas en cuanto la cohorte de #197 entre en el pool. La frase saldría casi siempre y
    rompería `WHERE message IS NOT NULL`, que es justo lo que este mecanismo existe para proteger.

    El rastro durable es `scrape_run.probes_unbuyable`, y quien vigila su tendencia es el validador
    de QA. Alarmar sin falsos positivos necesita cruzarlo con el stock del listado —si el parser se
    rompe, el catálogo entero se queda sin stock—, y eso es medición e issue aparte.
    """
    report = ScanReport()
    report.leaf_ok()

    # Todos agotados: sigue sin ser un mensaje, por muy sospechoso que suene.
    assert (
        _success_message(report, suspicious=set(), probe=ProbeOutcome(sent=33, unbuyable=33))
        is None
    )
    # Y el caso pequeño que lo destapó: uno solo.
    assert (
        _success_message(report, suspicious=set(), probe=ProbeOutcome(sent=1, unbuyable=1)) is None
    )
    # Mezclado, tampoco.
    assert (
        _success_message(
            report, suspicious=set(), probe=ProbeOutcome(sent=50, alive=17, unbuyable=33)
        )
        is None
    )


def test_el_mensaje_NO_habla_de_sondeos_cuando_alguno_si_contesta() -> None:
    """El umbral es «todos», no «algunos»: mismo contrato que la cifra del residuo.

    Un sondeo parcialmente sin veredicto es rutina —se reintenta en la siguiente pasada— y sigue
    contando en `errors` y en `probes_unresolved`. Publicarlo aquí dejaría `message` distinto de
    NULL casi siempre y rompería `WHERE message IS NOT NULL`.
    """
    report = ScanReport()
    report.leaf_ok()

    assert (
        _success_message(
            report, suspicious=set(), probe=ProbeOutcome(sent=50, alive=49, unresolved=1)
        )
        is None
    )


def test_el_mensaje_NO_habla_de_sondeos_en_una_pasada_sana() -> None:
    """Ni cuando no se sondeó nada, ni cuando el sondeo dio veredictos limpios."""
    report = ScanReport()
    report.leaf_ok()

    assert _success_message(report, suspicious=set(), probe=ProbeOutcome()) is None
    assert (
        _success_message(
            report, suspicious=set(), probe=ProbeOutcome(sent=10, alive=7, dead=3, over_cap=134)
        )
        is None
    )


def test_una_tienda_que_no_colapsa_generos_no_marca_nada() -> None:
    """`tambien_unisex` es lo que distingue a las cuatro tiendas que cruzan de las otras cinco."""
    report = ScanReport()
    report.leaf_gone(ScrapeScope("niño", "zapateria", "zapatos"), "hoja-nino")

    assert report.cross_gender_suspect == set()
    assert report.failed_scopes == {ScrapeScope("niño", "zapateria", "zapatos")}


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


def test_una_pasada_con_exito_y_hojas_caidas_dice_cuales(db_conn: Any) -> None:
    """El agujero de #151: la pasada de Sfera cerró en `success` con `errors = 15` y dentro había
    una hoja retirada —un ámbito entero sin detección de bajas—. Cuando alguien fue a mirar, el
    único sitio donde lo ponía era el log del pod, ya reciclado.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    store = ScanningFakeStore(
        [],
        signatures={},
        report=ScanReport(
            leaves_total=4,
            leaves_failed=1,
            failed_scopes={_CAMISETAS},
            failed_leaves=["ninos/bebe-nino/punto-y-jerseis"],
        ),
        scopes=[_CAMISETAS, _ZAPATOS],
    )
    ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    # La pasada es un éxito: una hoja retirada no la tumba, y ese es justo el motivo por el que
    # pasa inadvertida.
    assert _scalar(db_conn, "SELECT status FROM scrape_run ORDER BY id DESC LIMIT 1") == "success"
    message = _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
    assert message is not None
    assert "hojas caidas 1/4" in message
    assert "ninos/bebe-nino/punto-y-jerseis" in message  # QUÉ hoja, que es lo que faltaba
    assert "niña/ropa/camisetas" in message  # y qué ámbito se queda sin bajas


def test_una_pasada_limpia_deja_el_mensaje_a_null(db_conn: Any) -> None:
    """El simétrico del anterior, y el que hace útil al otro: sin esto, `message IS NOT NULL`
    dejaría de ser la consulta que separa las pasadas que hay que mirar de las que no.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    assert _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1") is None
    assert _scalar(db_conn, "SELECT status FROM scrape_run ORDER BY id DESC LIMIT 1") == "success"


def test_la_hoja_sin_residuo_llega_hasta_scrape_run_message(db_conn: Any) -> None:
    """De punta a punta: `ScanReport` -> `_success_message` -> la fila de la pasada (#358).

    La pasada sale `success` —una hoja sin residuo no la tumba, y por eso pasaba inadvertida— así
    que el único sitio donde queda escrito es este, que es el que sobrevive al log del pod.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    report = ScanReport(leaves_total=4)
    report.residuo("2622124", 0)
    report.residuo("2428167", 16)
    store = ScanningFakeStore([], signatures={}, report=report, scopes=[_CAMISETAS, _ZAPATOS])
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert _scalar(db_conn, "SELECT status FROM scrape_run ORDER BY id DESC LIMIT 1") == "success"
    message = _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
    assert message is not None
    assert "2622124" in message, "la hoja seca, que es lo accionable"
    assert "2428167" not in message, "la que aporta no ensucia el mensaje"
    # El total sí viaja en el resultado, que es lo que imprime el resumen de `run.py`.
    assert result.residual_entries == 16
    assert result.barren_residual_leaves == ["2622124"]


def test_un_residuo_sano_no_ensucia_el_mensaje_de_la_pasada(db_conn: Any) -> None:
    """El simétrico, y el que protege el contrato: con el rescate vivo, `message` sigue NULL."""
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    report = ScanReport(leaves_total=4)
    report.residuo("2622124", 25)
    store = ScanningFakeStore([], signatures={}, report=report, scopes=[_CAMISETAS, _ZAPATOS])
    result = ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    assert _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1") is None
    assert result.residual_entries == 25


def test_una_hoja_caida_sin_ruta_sigue_contando_aunque_no_se_pueda_nombrar(db_conn: Any) -> None:
    """Desde #155 `leaf` es obligatorio, así que esto ya no le puede pasar a una tienda.

    El caso se conserva porque el informe se construye desde **datos**, no desde el tipo: quien
    escribe el mensaje recibe un `ScanReport` y no puede asumir que venga poblado. Lo que se fija
    aquí es que la degradación siga siendo el aviso de siempre y no un corchete vacío.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    store = ScanningFakeStore(
        [],
        signatures={},
        report=ScanReport(leaves_total=4, leaves_failed=1, failed_scopes={_CAMISETAS}),
        scopes=[_CAMISETAS, _ZAPATOS],
    )
    ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    message = _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
    assert message is not None
    assert "hojas caidas 1/4" in message
    assert "[" not in message  # sin ruta que nombrar, no se inventa un corchete vacío
    assert "niña/ropa/camisetas" in message


def test_con_muchas_hojas_caidas_se_nombran_las_primeras_y_se_cuenta_el_resto(
    db_conn: Any,
) -> None:
    """`message` tiene tope, y una tienda con muchas hojas muertas no puede gastárselo entero en
    nombres: se nombran `_MAX_NAMED_LEAVES` en orden estable —alfabético, no el del recorrido, para
    que dos pasadas comparables den el mismo texto— y el resto se resume con `+N`.

    La proporción de este caso no es decorado. Nombrar muchas hojas solo puede pasar en una pasada
    que **sobrevivió**, y sobrevivir es justo lo que acota el ratio: con 7 de 10 la pasada aborta
    por `SCRAPER_SCAN_MAX_DEAD_RATIO` (0,34) y no llega a escribir `message`. O sea que el `+N` es
    el síntoma de un catálogo GRANDE con varias hojas retiradas, no el de una tienda rota — esa
    otra la corta el umbral mucho antes, y con una excepción, no con un mensaje.
    """
    products, sigs = _dos_ambitos()
    ingest(db_conn, FakeStore(products, signatures=sigs), run_ts=T1)

    hojas = [f"ninos/hoja-{n}" for n in range(7)]
    store = ScanningFakeStore(
        [],
        signatures={},
        report=ScanReport(
            leaves_total=25,  # 7/25 = 0,28: por debajo del umbral, la pasada sobrevive
            leaves_failed=7,
            failed_scopes={_CAMISETAS},
            failed_leaves=hojas,
        ),
        scopes=[_CAMISETAS, _ZAPATOS],
    )
    ingest(db_conn, store, run_ts=T2, delist_min_misses=1)

    message = _scalar(db_conn, "SELECT message FROM scrape_run ORDER BY id DESC LIMIT 1")
    assert message is not None
    assert "hojas caidas 7/25" in message
    assert "[ninos/hoja-0, ninos/hoja-1, ninos/hoja-2, ninos/hoja-3, ninos/hoja-4 +2]" in message
    assert "ninos/hoja-6" not in message, "las que no caben se cuentan, no se nombran"


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


# --- progreso de la pasada (#146) -------------------------------------------------------------


class _RelojFalso:
    """Reloj que solo avanza cuando se le dice, no al leerlo.

    `test_vigia.py` usa una lista de instantes porque allí se cronometra un tramo entero y las
    lecturas son dos, contables con la mano. Aquí no vale: el latido lee el reloj varias veces por
    ficha y el número de lecturas es un detalle de implementación, así que un test atado a él se
    rompe en cuanto alguien añade una lectura — y se rompe diciendo algo que no es. Lo que este
    test quiere fijar es *cuánto tarda la tienda*, y eso es exactamente `avanza()`.
    """

    def __init__(self) -> None:
        self.ahora = 0.0

    def avanza(self, segundos: float) -> None:
        self.ahora += segundos

    def __call__(self) -> float:
        return self.ahora


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> _RelojFalso:
    """Sustituye el reloj de la ingesta por uno que el test hace avanzar.

    Se parchea `progreso._reloj` y no `time.monotonic`, por lo mismo que en `test_vigia.py`:
    parchear el módulo `time` es global y afectaría a cualquier otra cosa del proceso — empezando
    por la pausa de `test_una_pasada_con_exito_registra_su_duracion`, aquí al lado.
    """
    falso = _RelojFalso()
    monkeypatch.setattr(progreso_mod, "_reloj", falso)
    return falso


class _TiendaQueTarda(FakeStore):
    """Tienda cuyo detalle cuesta `segundos_por_ficha` de reloj (falso) por producto."""

    def __init__(self, *args: Any, reloj: _RelojFalso, segundos_por_ficha: float, **kw: Any):
        super().__init__(*args, **kw)
        self._reloj = reloj
        self._coste = segundos_por_ficha

    def fetch_details(self, entries: Iterable[ListingEntry]) -> Iterable[ScrapedProduct]:
        for product in super().fetch_details(entries):
            self._reloj.avanza(self._coste)
            yield product


def _lineas(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "scraper.ingest"]


def test_una_pasada_rapida_no_emite_ni_un_latido(
    db_conn: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """La garantía de que el modo normal no se ensucia, que era el punto 3 de #146.

    Sale gratis por ir el latido por tiempo y no por número de fichas: una pasada caliente de Zara
    (1m35s) no llega al primer aviso de 5 min. Este test es lo que impide que alguien lo convierta
    en «cada N fichas» sin darse cuenta de que eso llena el log de las nueve tiendas.
    """
    store = FakeStore(*_dos_productos())
    with caplog.at_level("INFO", logger="scraper.ingest"):
        ingest(db_conn, store, run_ts=T1)

    latidos = [ln for ln in _lineas(caplog) if "listando" in ln or "fichas " in ln]
    assert latidos == []
    # Pero los hitos SÍ salen, que son los que no miran el reloj.
    assert any("pasada arrancada" in ln for ln in _lineas(caplog))
    assert any(ln.startswith("fake · listado:") for ln in _lineas(caplog))


def _cuatro_productos() -> tuple[list[ScrapedProduct], dict[str, str]]:
    productos = [_product(pid, f"Prenda {pid}", [_variant(f"{pid}-1", "10.00")]) for pid in "ABCD"]
    return productos, {pid: f"{pid}1" for pid in "ABCD"}


def test_la_pasada_larga_late_una_vez_por_ventana_y_no_por_ficha(
    db_conn: Any, caplog: pytest.LogCaptureFixture, reloj: _RelojFalso
) -> None:
    """Con la ventana en 300 s y fichas de 200 s, late en la 2ª y en la 4ª. No en las cuatro.

    Es la invariante que hace utilizable el log de una pasada de cinco horas: 1224 fichas a una
    línea cada una son 1224 líneas y ningún ritmo; a una cada 5 minutos son ~60 y se lee de un
    vistazo.
    """
    productos, sigs = _cuatro_productos()
    store = _TiendaQueTarda(productos, sigs, reloj=reloj, segundos_por_ficha=200.0)

    with caplog.at_level("INFO", logger="scraper.ingest"):
        ingest(db_conn, store, run_ts=T1, progress_every_seconds=300.0)

    fichas = [ln for ln in _lineas(caplog) if "fichas " in ln]
    assert len(fichas) == 2, fichas
    assert "fichas 2/4 (50%)" in fichas[0]
    assert "fichas 4/4 (100%)" in fichas[1]
    # El ritmo es el de la fase 2 sola y es el número con el que se compara una tienda consigo
    # misma entre pasadas: 400 s de reloj entre 2 fichas.
    assert "200.0 s/ficha" in fichas[0]


def test_progreso_desactivado_no_emite_latidos_pero_si_los_hitos(
    db_conn: Any, caplog: pytest.LogCaptureFixture, reloj: _RelojFalso
) -> None:
    """`0` apaga el latido. Los hitos se quedan: son dos líneas por pasada, no son el ruido."""
    productos, sigs = _cuatro_productos()
    store = _TiendaQueTarda(productos, sigs, reloj=reloj, segundos_por_ficha=9000.0)

    with caplog.at_level("INFO", logger="scraper.ingest"):
        ingest(db_conn, store, run_ts=T1, progress_every_seconds=0)

    assert [ln for ln in _lineas(caplog) if "fichas " in ln or "listando" in ln] == []
    assert any("pasada arrancada" in ln for ln in _lineas(caplog))


def test_la_frontera_se_anuncia_aunque_no_haya_nada_que_pedir(
    db_conn: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """El caso que más se parece a una colgada: segunda pasada sin cambios, 0 fichas.

    Sin esta línea, una pasada que no pide detalle no escribe nada entre el arranque y el resumen
    — y es justo la forma que tiene una pasada sana de parecer muerta.
    """
    productos, sigs = _dos_productos()
    ingest(db_conn, FakeStore(productos, sigs), run_ts=T1)

    with caplog.at_level("INFO", logger="scraper.ingest"):
        ingest(db_conn, FakeStore(productos, sigs), run_ts=T2)

    frontera = [ln for ln in _lineas(caplog) if "listado:" in ln]
    assert len(frontera) == 1
    assert "se piden 0 fichas, 2 sin cambios" in frontera[0]


# --- ejes transversales (#180) -----------------------------------------------------------------


def _ropa(pid: str, name: str) -> ScrapedProduct:
    """Un producto de `ropa`, que es la única sección donde `deportiva` aplica."""
    variants = [_variant(f"{pid}-1", "19.95")]
    return _product(pid, name, variants, section="ropa", category="camisetas")


def test_las_etiquetas_se_escriben_desde_el_listado_y_no_desde_el_detalle(db_conn: Any) -> None:
    """La propiedad que decide el diseño: marcar NO depende de que se pida la ficha.

    Segunda pasada sin cambios de huella, o sea 0 fichas pedidas. Si la marca colgara del
    `ScrapedProduct` —que es lo natural, y lo que había que descartar—, aquí no se escribiría nada
    y en régimen estacionario el eje se quedaría vacío para casi todo el catálogo.
    """
    productos = [_ropa("A", "Camiseta"), _ropa("B", "Jersey")]
    sigs = {"A": "a1", "B": "b1"}
    ingest(db_conn, FakeStore(productos, sigs), run_ts=T1)  # sin etiquetas todavía

    store = TaggingFakeStore(productos, sigs, _tags({"A": {TAG_DEPORTIVA}}))
    result = ingest(db_conn, store, run_ts=T2)

    assert result.details_fetched == 0  # nadie pidió ficha…
    assert _etiquetas(db_conn) == {("A", TAG_DEPORTIVA)}  # …y aun así quedó marcado
    assert result.tag_counts == {TAG_DEPORTIVA: 1}


def test_la_etiqueta_no_se_pone_en_una_seccion_donde_no_aplica(db_conn: Any) -> None:
    """`deportiva` es solo de `ropa`: el calzado deportivo ya se encuentra por `zapatillas`.

    La tienda puede declarar lo que quiera —su cajón de deporte podría traer una zapatilla—; quien
    decide es el eje, en un solo sitio (`tags.SECCION_APLICABLE`), y no cada scraper por su cuenta.
    """
    productos = [_ropa("A", "Camiseta"), _product("Z", "Zapatilla", [_variant("Z-1", "29.95")])]
    store = TaggingFakeStore(
        productos,
        {"A": "a1", "Z": "z1"},
        _tags({"A": {TAG_DEPORTIVA}, "Z": {TAG_DEPORTIVA}}),
    )

    ingest(db_conn, store, run_ts=T1)

    assert _etiquetas(db_conn) == {("A", TAG_DEPORTIVA)}  # la zapatilla NO


def test_la_prenda_que_sale_del_cajon_de_deporte_deja_de_estar_marcada(db_conn: Any) -> None:
    """Reconciliación, no acumulación: si la tienda deja de decirlo, nosotros también."""
    productos = [_ropa("A", "Camiseta"), _ropa("B", "Jersey")]
    sigs = {"A": "a1", "B": "b1"}
    ingest(db_conn, TaggingFakeStore(productos, sigs, _tags({"A": {TAG_DEPORTIVA}})), run_ts=T1)
    assert _etiquetas(db_conn) == {("A", TAG_DEPORTIVA)}

    ingest(db_conn, TaggingFakeStore(productos, sigs, _tags({"B": {TAG_DEPORTIVA}})), run_ts=T2)

    assert _etiquetas(db_conn) == {("B", TAG_DEPORTIVA)}


def test_una_fuente_caida_no_borra_las_etiquetas_que_ya_habia(db_conn: Any) -> None:
    """LA red de seguridad de este eje, y no tiene ninguna otra detrás.

    Una hoja que no se pudo listar no significa «esta tienda ya no publica nada deportivo». Sin
    este acote, la primera pasada posterior a un 404 borraría las marcas de toda la tienda de una
    vez — sin histéresis ni sondeo que lo frenen, que es lo que sí protege a las bajas.
    """
    productos = [_ropa("A", "Camiseta"), _ropa("B", "Jersey")]
    sigs = {"A": "a1", "B": "b1"}
    ingest(db_conn, TaggingFakeStore(productos, sigs, _tags({"A": {TAG_DEPORTIVA}})), run_ts=T1)

    # La hoja se cayó: no se observó nada Y el eje queda fuera de `fiables`.
    caida = TaggingFakeStore(productos, sigs, _tags({}, fiables=set()))
    result = ingest(db_conn, caida, run_ts=T2)

    assert _etiquetas(db_conn) == {("A", TAG_DEPORTIVA)}  # intacto
    assert result.tag_counts == {}  # y no se publica una cifra que no se ha medido


def test_una_pasada_vacia_pero_fiable_si_borra(db_conn: Any) -> None:
    """El contraste del anterior, que es lo que lo hace una prueba y no una tautología.

    Si la hoja se listó bien y de verdad ya no trae nada, la marca tiene que irse. Sin este caso,
    `fiables` podría estar siempre vacío y el test de arriba pasaría igual.
    """
    productos = [_ropa("A", "Camiseta")]
    sigs = {"A": "a1"}
    ingest(db_conn, TaggingFakeStore(productos, sigs, _tags({"A": {TAG_DEPORTIVA}})), run_ts=T1)

    vacia = TaggingFakeStore(productos, sigs, _tags({}, fiables={TAG_DEPORTIVA}))
    ingest(db_conn, vacia, run_ts=T2)

    assert _etiquetas(db_conn) == set()


def test_una_tienda_sin_ejes_no_toca_la_tabla(db_conn: Any) -> None:
    """Zara, Hipercor, Springfield y Cacles no publican cajón de deporte: no implementan nada."""
    productos = [_ropa("A", "Camiseta")]
    result = ingest(db_conn, FakeStore(productos, {"A": "a1"}), run_ts=T1)

    assert _etiquetas(db_conn) == set()
    assert result.tag_counts == {}


def _filas_agregado(conn: Any, scope: str = "todas") -> Any:
    """Filas del agregado en un ámbito. Desde la 0038 (#371) hay una por producto Y ámbito."""
    return _scalar(conn, "SELECT count(*) FROM product_agg WHERE scope = %s", (scope,))


def _precio_agregado(conn: Any, retailer_product_id: str, scope: str = "todas") -> Any:
    # El `scope` NO es opcional en la consulta aunque lo sea en la firma: desde la 0038 un producto
    # tiene dos filas aquí, y sin el filtro esto devolvía la que entregase primero el planificador.
    # Con todo en stock las dos valen lo mismo, así que la ausencia no se notaba — hasta que una
    # variante se agote y el test empiece a fallar un día sí y otro no.
    return _scalar(
        conn,
        """
        SELECT pa.price_from FROM product_agg pa
        JOIN product p ON p.id = pa.product_id
        WHERE p.retailer_product_id = %s AND pa.scope = %s
        """,
        (retailer_product_id, scope),
    )


def test_la_pasada_deja_al_dia_el_agregado_del_catalogo(db_conn: Any) -> None:
    """La pasada repuebla `product_agg`, que es de donde lee el catálogo (0035, #314).

    Sin esto el web serviría el agregado de la pasada anterior **sin dar ningún síntoma**: no hay
    error ni fila que falte, solo precios viejos. Por eso se comprueba aquí, en el único sitio que
    escribe `price_history`, y no solo desde el web.
    """
    store = FakeStore(
        [
            _product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "20.00")]),
            _product("B", "Botín", [_variant("B-1", "45.00")]),
        ],
        signatures={"A": "a1", "B": "b1"},
    )

    ingest(db_conn, store, run_ts=T1)

    # Una fila por producto vivo con precio, y el precio de su variante más barata.
    assert _filas_agregado(db_conn) == 2
    assert _precio_agregado(db_conn, "A") == Decimal("20.00")

    # Y la pasada tiene que dejar poblados LOS DOS ámbitos de la 0038 (#371), no solo el ancho:
    # el catálogo lee `con_stock` en cuanto alguien filtra por disponibilidad, y si la ingesta se
    # dejara ese ámbito sin repoblar el filtro devolvería el agregado de la pasada anterior sin
    # dar ningún síntoma — que es el mismo fallo que este test vino a cubrir para `todas`.
    # Las variantes de la fixture van todas con stock, así que aquí los dos ámbitos coinciden.
    assert _filas_agregado(db_conn, "con_stock") == 2
    assert _precio_agregado(db_conn, "A", "con_stock") == Decimal("20.00")

    # Segunda pasada: A se abarata y B desaparece del listado.
    store2 = FakeStore(
        [_product("A", "Bailarina", [_variant("A-1", "39.95"), _variant("A-2", "9.99")])],
        signatures={"A": "a2"},
    )
    ingest(db_conn, store2, run_ts=T2, delist_min_misses=1)

    assert _precio_agregado(db_conn, "A") == Decimal("9.99")
    # B se ha dado de baja, así que sale del agregado: el refresco va DESPUÉS de las bajas
    # justo para esto, porque son ellas las que deciden qué variantes siguen vivas.
    assert _precio_agregado(db_conn, "B") is None


def test_el_agregado_se_refresca_por_tienda_y_no_arrastra_a_las_demas(db_conn: Any) -> None:
    """El refresco es POR TIENDA, que es lo que permite que nueve CronJobs no se pisen."""
    zara = FakeStore([_product("A", "Bailarina", [_variant("A-1", "39.95")])], {"A": "a1"})
    sfera = FakeStore([_product("S", "Botín", [_variant("S-1", "45.00")])], {"S": "s1"})
    sfera.slug = "sfera"  # type: ignore[misc]
    sfera.name = "Sfera"  # type: ignore[misc]

    ingest(db_conn, zara, run_ts=T1)
    ingest(db_conn, sfera, run_ts=T1)
    assert _filas_agregado(db_conn) == 2

    # Otra pasada de Zara: la fila de Sfera sigue en pie y con su precio.
    ingest(db_conn, zara, run_ts=T2)
    assert _filas_agregado(db_conn) == 2
    # El borrado del refresco parcial es por tienda y NO por ámbito: si se llevara por delante el
    # `con_stock` de Sfera, esto lo vería y el `todas` de arriba no.
    assert _filas_agregado(db_conn, "con_stock") == 2
    assert _precio_agregado(db_conn, "S") == Decimal("45.00")
