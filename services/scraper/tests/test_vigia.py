"""El vigía y, sobre todo, el vigía del vigía (#67).

Los tests interesantes de este fichero no son los del veredicto —esos solo fijan una regla que ya
existía— sino `test_toda_tienda_registrada_tiene_vigilancia`: es el que hace que **añadir una tienda
sin vigilancia rompa `just check`** en vez de descubrirse meses después, cuando la tienda deje de
dejarnos entrar y no haya nadie mirando.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from scraper import vigia
from scraper.config import Config
from scraper.stores.base import (
    LeafHealth,
    ListingEntry,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    SupportsLeafHealth,
)
from scraper.stores.registry import available_slugs, get_store
from scraper.vigia import (
    SIN_VIGILANCIA_DE_HOJAS,
    Informe,
    Medida,
    comparar_con_base,
    revisar_hojas,
    revisar_parseo,
    revisar_tienda,
)
from scraper.vigia_historial import Base

_CFG = Config(database_url="postgresql://unused")
_AMBITO = ScrapeScope("niña", "zapateria", "zapatos")


# --- el vigía del vigía --------------------------------------------------------------------


@pytest.mark.parametrize("slug", available_slugs())
def test_toda_tienda_registrada_tiene_vigilancia(slug: str) -> None:
    """Una tienda nueva entra en el vigía sola (recorre el registro), pero solo si sabe sondearse.

    Este test es la red que evita el olvido: implementar `check_leaves()` no es opcional de hecho
    aunque el `Protocol` lo llame capacidad opcional, porque sin él esa tienda deja de ingerir una
    categoría caducada sin que nadie se entere. Si de verdad no se puede sondear, la salida es
    declararlo en `SIN_VIGILANCIA_DE_HOJAS` **con el motivo**, que es una decisión revisable.
    """
    store = get_store(slug, _CFG)
    if isinstance(store, SupportsLeafHealth):
        return
    assert SIN_VIGILANCIA_DE_HOJAS.get(slug), (
        f"la tienda {slug!r} no implementa `check_leaves()` (SupportsLeafHealth), así que el vigía "
        "no puede comprobar que sus categorías siguen existiendo. Impleméntalo, o declara el "
        "motivo en `SIN_VIGILANCIA_DE_HOJAS` de scraper/vigia.py."
    )


# --- dobles --------------------------------------------------------------------------------


class TiendaFalsa:
    """Doble mínimo: devuelve lo que le digan, sin red. Cumple `BaseStore` estructuralmente."""

    slug = "falsa"
    name = "Falsa"
    base_url = "https://ejemplo.invalid"

    def __init__(
        self,
        hojas: list[LeafHealth] | None = None,
        entradas: list[ListingEntry] | None = None,
        productos: list[ScrapedProduct] | None = None,
    ) -> None:
        self._hojas = hojas
        self._entradas = entradas if entradas is not None else []
        self._productos = productos if productos is not None else []
        self.entradas_pedidas: list[ListingEntry] = []

    def scopes(self):  # type: ignore[no-untyped-def]
        return [_AMBITO]

    def list_catalog(self):  # type: ignore[no-untyped-def]
        # Generador a propósito: el vigía corta con `islice` y hay que poder comprobar que la
        # tienda no llega a producir el catálogo entero.
        yield from self._entradas

    def fetch_details(self, entries):  # type: ignore[no-untyped-def]
        self.entradas_pedidas = list(entries)
        return self._productos

    def check_leaves(self):  # type: ignore[no-untyped-def]
        if self._hojas is None:
            raise AssertionError("esta tienda falsa no sondea hojas")
        return self._hojas


class TiendaSinHojas:
    """Cumple `BaseStore` pero NO `SupportsLeafHealth`: el caso que el meta-test persigue."""

    slug = "sinhojas"
    name = "Sin hojas"
    base_url = "https://ejemplo.invalid"

    def scopes(self):  # type: ignore[no-untyped-def]
        return [_AMBITO]

    def list_catalog(self):  # type: ignore[no-untyped-def]
        return iter(())

    def fetch_details(self, entries):  # type: ignore[no-untyped-def]
        return iter(())


def _entrada(pid: str) -> ListingEntry:
    return ListingEntry(pid, "huella", "niña", "zapateria", "zapatos")


def _producto(pid: str, *, variantes: int = 1, precio: str = "19.90") -> ScrapedProduct:
    return ScrapedProduct(
        retailer_product_id=pid,
        name=f"Producto {pid}",
        gender="niña",
        section="zapateria",
        category="zapatos",
        url=None,
        variants=[
            ScrapedVariant(f"{pid}-{i}", "25", "rojo", None, Decimal(precio), None, True)
            for i in range(variantes)
        ],
    )


# --- capa de hojas -------------------------------------------------------------------------


def test_una_hoja_retirada_es_accionable() -> None:
    """Pide un id nuevo en CATEGORIES: alguien puede arreglarlo, así que rompe."""
    tienda = TiendaFalsa(
        hojas=[
            LeafHealth(_AMBITO, "viva", True, "HTTP 200"),
            LeafHealth(_AMBITO, "muerta", False, "HTTP 404"),
        ]
    )
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "RETIRADA" in informe.render()
    assert "muerta" in informe.render()


def test_una_hoja_sin_veredicto_avisa_pero_no_rompe() -> None:
    """Un 403 suelto de Akamai es rutina; un vigía que canta por eso acaba silenciado."""
    tienda = TiendaFalsa(
        hojas=[
            LeafHealth(_AMBITO, "viva", True, "HTTP 200"),
            LeafHealth(_AMBITO, "dudosa", None, "HTTP 403"),
        ]
    )
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien
    assert informe.avisos and "sin veredicto" in informe.avisos[0]


def test_que_ninguna_hoja_este_viva_si_rompe() -> None:
    """La forma que tendría una regresión de la huella TLS: 429 en todas, ninguna confirmada."""
    detalle = (
        "HTTP 429 local_rate_limited en todos los intentos y sin un solo 200: "
        "probablemente nos rechazan la huella TLS"
    )
    tienda = TiendaFalsa(hojas=[LeafHealth(_AMBITO, f"h{i}", None, detalle) for i in range(4)])
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "es un bloqueo" in informe.render()


def test_una_tienda_sin_sondeo_de_hojas_es_accionable() -> None:
    """Sin `check_leaves()` no hay vigilancia, y eso no puede pasar en silencio."""
    informe = Informe("sinhojas")
    revisar_hojas(TiendaSinHojas(), informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "SupportsLeafHealth" in informe.render()


# --- capa de parseo ------------------------------------------------------------------------


def test_el_parseo_corta_la_muestra_sin_barrer_el_catalogo() -> None:
    """`islice` sobre el generador: el vigía no puede costar una pasada entera de Zara."""
    tienda = TiendaFalsa(
        entradas=[_entrada(f"p{i}") for i in range(50)],
        productos=[_producto("p0"), _producto("p1")],
    )
    informe = Informe("falsa")
    revisar_parseo(tienda, informe, muestra=2)  # type: ignore[arg-type]

    assert informe.esta_bien
    assert len(tienda.entradas_pedidas) == 2, "solo se pide el detalle de la muestra"
    assert "2 entradas -> 2 productos" in informe.render()


def test_un_listado_vacio_es_accionable() -> None:
    """O nos han cerrado la puerta o el endpoint cambió de forma: las dos hay que mirarlas."""
    informe = Informe("falsa")
    revisar_parseo(TiendaFalsa(entradas=[]), informe, muestra=5)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "ni una entrada" in informe.render()


def test_productos_sin_variantes_son_accionables() -> None:
    """Listado vivo y parseo roto: es el fallo que un chequeo de hojas NO ve."""
    tienda = TiendaFalsa(entradas=[_entrada("p0")], productos=[_producto("p0", variantes=0)])
    informe = Informe("falsa")
    revisar_parseo(tienda, informe, muestra=5)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "sin una sola variante" in informe.render()


def test_si_el_sondeo_de_hojas_revienta_no_se_intenta_el_parseo() -> None:
    """Medido con Lefties sin Chromium: el segundo error salía derivado y mandaba a la pista falsa.

    El sondeo fallaba por «falta el navegador» y el parseo, sobre el mismo navegador a medio
    arrancar, contestaba «usa la API async» — un síntoma que no tiene nada que ver con la causa. El
    vigía existe para señalar bien, así que dice una sola cosa y esa es la cierta.
    """

    class TiendaQueRevienta(TiendaFalsa):
        def check_leaves(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("falta el navegador")

        def list_catalog(self):  # type: ignore[no-untyped-def]
            raise AssertionError("no se debería haber llegado al parseo")

    informe = Informe("falsa")
    revisar_hojas_ok = True
    try:
        revisar_hojas(TiendaQueRevienta(), informe)  # type: ignore[arg-type]
    except RuntimeError:
        revisar_hojas_ok = False
    assert not revisar_hojas_ok, "el doble debe reventar para que el test tenga sentido"

    # Y por la vía real: `revisar_tienda` la envuelve y no llega a tocar `list_catalog`.
    from scraper.stores import registry

    registry._STORES["quereventa"] = lambda config: TiendaQueRevienta()  # type: ignore[assignment]
    try:
        completo = revisar_tienda("quereventa", _CFG, muestra=5)
    finally:
        del registry._STORES["quereventa"]

    assert not completo.esta_bien
    assert "falta el navegador" in completo.render()
    assert "omitido" in completo.render()


def test_un_precio_a_cero_es_accionable() -> None:
    """El precio ha cambiado de sitio o de unidad: ingerirlo ensuciaría el histórico."""
    tienda = TiendaFalsa(entradas=[_entrada("p0")], productos=[_producto("p0", precio="0")])
    informe = Informe("falsa")
    revisar_parseo(tienda, informe, muestra=5)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "precio <= 0" in informe.render()


# --- cronómetro y comparación con el histórico (#111) ---------------------------------------


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Sustituye el reloj del vigía por una lista de instantes, para fijar duraciones exactas.

    Se parchea `vigia._reloj` y no `time.monotonic`: parchear el módulo `time` es global y
    afectaría a cualquier otra cosa del proceso.
    """

    def instalar(instantes: list[float]) -> None:
        it = iter(instantes)
        monkeypatch.setattr(vigia, "_reloj", lambda: next(it))

    return instalar


def test_las_dos_capas_publican_su_ritmo(reloj) -> None:  # type: ignore[no-untyped-def]
    """La señal que #111 existe para publicar: no solo cuánto, sino cuánto POR unidad.

    Un absoluto por tienda envejece mal —los catálogos crecen—, así que lo comparable entre
    semanas es el ritmo, y para eso tiene que salir impreso.
    """
    reloj([0.0, 1468.4, 2000.0, 2013.2])
    hojas = [LeafHealth(_AMBITO, f"h{i}", True, "HTTP 200") for i in range(32)]
    tienda = TiendaFalsa(
        hojas=hojas,
        entradas=[_entrada(f"p{i}") for i in range(5)],
        productos=[_producto(f"p{i}") for i in range(5)],
    )
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]
    revisar_parseo(tienda, informe, muestra=5)  # type: ignore[arg-type]

    assert informe.tiempos["hojas"].segundos == pytest.approx(1468.4)
    assert (informe.tiempos["hojas"].unidades, informe.tiempos["hojas"].unidad) == (32, "hoja")
    assert informe.tiempos["parseo"].segundos == pytest.approx(13.2)
    assert (informe.tiempos["parseo"].unidades, informe.tiempos["parseo"].unidad) == (
        5,
        "producto",
    )
    assert "tiempos: hojas 24m 28s (45,9 s/hoja · 32)" in informe.render()
    assert "parseo 13,2 s (2,6 s/producto · 5)" in informe.render()
    assert "total 24m 42s" in informe.render()


def test_una_capa_que_revienta_a_mitad_conserva_lo_medido(reloj) -> None:  # type: ignore[no-untyped-def]
    """«Murió tras 2 hojas en 8 min» es un dato, y es justo el que delata que nos regulan el paso.

    Sin el `finally` del cronómetro, la tienda que peor se está portando sería la única sin medida.
    """
    reloj([0.0, 480.0])

    class TiendaQueRevientaAMitad(TiendaFalsa):
        def check_leaves(self):  # type: ignore[no-untyped-def]
            yield LeafHealth(_AMBITO, "h0", True, "HTTP 200")
            yield LeafHealth(_AMBITO, "h1", None, "HTTP 403")
            raise RuntimeError("Timeout 45000ms exceeded")

    informe = Informe("falsa")
    with pytest.raises(RuntimeError):
        revisar_hojas(TiendaQueRevientaAMitad(), informe)  # type: ignore[arg-type]

    assert informe.tiempos["hojas"] == Medida(480.0, 2, "hoja")


def test_sin_linea_base_no_se_compara_y_se_dice() -> None:
    """Casilla 3 de #111: sin histórico, un número lento suelto no es accionable ni aviso.

    Y hay que decirlo, porque callar confundiría «va bien» con «no lo he mirado» durante las
    primeras semanas de serie.
    """
    informe = Informe("falsa", tiempos={"hojas": Medida(1468.4, 32, "hoja")})
    comparar_con_base(informe, {"hojas": None}, factor=3.0)

    assert informe.esta_bien
    assert not informe.avisos
    assert "sin línea base" in informe.render()


def test_un_ritmo_muy_por_encima_de_su_base_avisa_pero_no_acciona() -> None:
    """El caso medido: Hipercor a ×11,8 con la puerta abierta y el veredicto verde.

    Es `aviso` y no `accionable` a propósito: `main()` solo publica en GitHub lo accionable, así
    que una tienda lenta se lee en el log y no abre issue por sí sola.
    """
    informe = Informe("falsa", tiempos={"hojas": Medida(1468.4, 32, "hoja")})
    base = Base(3.9, (4.1, 3.7, 4.0, 3.8))
    comparar_con_base(informe, {"hojas": base}, factor=3.0)

    assert informe.esta_bien, "un ritmo lento no puede tumbar el job"
    assert not informe.accionables
    assert len(informe.avisos) == 1
    assert "×11,8" in informe.avisos[0]
    assert "mediana de 4" in informe.avisos[0]
    assert "base: hojas 3,9 s/hoja (×11,8)" in informe.render()


def test_un_ritmo_dentro_del_factor_se_publica_sin_avisar() -> None:
    """La tendencia se publica siempre; el aviso solo cuando se sale del factor.

    Un cluster compartido da ×2 sin que pase nada: avisar por eso es cómo un vigía se convierte en
    ruido y acaba silenciado.
    """
    informe = Informe("falsa", tiempos={"hojas": Medida(240.0, 32, "hoja")})
    comparar_con_base(informe, {"hojas": Base(3.9, (4.1, 3.7))}, factor=3.0)

    assert not informe.avisos
    assert "(×1,9)" in informe.render()
