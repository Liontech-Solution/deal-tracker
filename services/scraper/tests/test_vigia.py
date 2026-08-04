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
    CategoryNode,
    LeafHealth,
    ListingEntry,
    ScrapedProduct,
    ScrapedVariant,
    ScrapeScope,
    SupportsCategoryTree,
    SupportsCoverageWatch,
    SupportsLeafHealth,
)
from scraper.stores.registry import available_slugs, get_store
from scraper.vigia import (
    COBERTURA_DECLARADA,
    COBERTURA_SIN_VIGILAR,
    SIN_VIGILANCIA_DE_HOJAS,
    Informe,
    Medida,
    comparar_con_base,
    revisar_cobertura,
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


# --- capa de cobertura (#156) --------------------------------------------------------------


class TiendaConArbol(TiendaFalsa):
    """`TiendaFalsa` que además publica un árbol: cumple `SupportsCoverageWatch`."""

    def __init__(
        self,
        nodos: dict[str, list[CategoryNode]],
        mapeadas: list[str],
        *,
        separador: str = "/",
        hojas: list[LeafHealth] | None = None,
    ) -> None:
        super().__init__(hojas=hojas)
        self._nodos = nodos
        self._mapeadas = mapeadas
        self._separador = separador

    def mapped_leaves(self):  # type: ignore[no-untyped-def]
        return self._mapeadas

    def tree_roots(self):  # type: ignore[no-untyped-def]
        return list(self._nodos)

    def tree_separator(self):  # type: ignore[no-untyped-def]
        return self._separador

    def category_tree(self, root):  # type: ignore[no-untyped-def]
        nodos = self._nodos[root]
        if isinstance(nodos, Exception):
            raise nodos
        yield from nodos


def _nodo(path: str, *, count: int | None = 7, depth: int = 1, hijas: bool = False) -> CategoryNode:
    return CategoryNode(path, path.rsplit("/", 1)[-1], count, depth, hijas)


def test_una_categoria_publicada_y_sin_mapear_es_accionable_y_se_nombra() -> None:
    """El caso de #156: la tienda publica `ropa-deportiva` y nadie la ingiere.

    Accionable y no aviso porque `main()` solo abre issue con lo accionable, y un hallazgo que se
    queda en el log del pod es exactamente el punto ciego que esta capa viene a tapar.
    """
    tienda = TiendaConArbol(
        {
            "ninos/nina": [
                _nodo("ninos/nina/camisetas"),
                _nodo("ninos/nina/ropa-deportiva", count=25),
            ]
        },
        mapeadas=["ninos/nina/camisetas"],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert len(informe.accionables) == 1
    assert "ninos/nina/ropa-deportiva (25)" in informe.accionables[0]
    assert "ninos/nina/camisetas" not in informe.accionables[0]


def test_una_categoria_declarada_no_suena() -> None:
    """La mitad que evita la alarma semanal: lo que ya decidimos no ingerir se calla."""
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/bano")]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    COBERTURA_DECLARADA["falsa"] = {"ninos/nina/bano": "no es del brief"}
    try:
        revisar_cobertura(tienda, informe)  # type: ignore[arg-type]
    finally:
        del COBERTURA_DECLARADA["falsa"]

    assert informe.esta_bien
    assert not informe.accionables


def test_lo_que_cuelga_de_una_hoja_ya_mapeada_no_es_un_hueco() -> None:
    """Medido en C&A: 53 de 122 rutas eran subcategorías de hojas que YA ingerimos.

    Sus productos entran por el padre, así que señalarlas sería ruido — y ese ruido era la mitad
    del informe, que es como una capa así deja de leerse.
    """
    tienda = TiendaConArbol(
        {"3-7": [_nodo("3-7-1"), _nodo("3-7-1-2", depth=2), _nodo("3-7-1-10", depth=2)]},
        mapeadas=["3-7-1"],
        separador="-",
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien, informe.accionables


def test_el_separador_evita_que_una_hermana_tape_a_otra() -> None:
    """La trampa del prefijo pelado: `3-1-11` (Calcetines) empieza por `3-1-1` (Camisetas).

    Sin el separador, mapear una taparía a la otra y un hueco real pasaría por cubierto.
    """
    tienda = TiendaConArbol(
        {"3-1": [_nodo("3-1-11")]},
        mapeadas=["3-1-1"],
        separador="-",
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "3-1-11" in informe.accionables[0]


def test_solo_se_nombra_la_rama_y_no_todas_sus_hijas() -> None:
    """Una rama sin cubrir arrastra las suyas; nombrarlas todas haría del hallazgo una parrafada."""
    tienda = TiendaConArbol(
        {
            "ninos/nina": [
                _nodo("ninos/nina/bano", hijas=True),
                _nodo("ninos/nina/bano/bikinis", depth=2),
                _nodo("ninos/nina/bano/banadores", depth=2),
            ]
        },
        mapeadas=[],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert "1 categoría(s)" in informe.accionables[0]
    assert "ninos/nina/bano " in informe.accionables[0]
    assert "bikinis" not in informe.accionables[0]


def test_la_raiz_no_se_señala_a_si_misma() -> None:
    """Alguna tienda se emite a sí misma al pedir su árbol; la raíz es la pregunta, no el hueco."""
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina", depth=0)]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien, informe.accionables


def test_una_raiz_que_revienta_avisa_y_las_demas_se_barren() -> None:
    """Un 403 suelto de Akamai es rutina: no puede llevarse por delante el resto del barrido."""
    tienda = TiendaConArbol(
        {
            "ninos/nina": RuntimeError("403 de Akamai"),  # type: ignore[dict-item]
            "ninos/nino": [_nodo("ninos/nino/ropa-deportiva")],
        },
        mapeadas=[],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert len(informe.avisos) == 1
    assert "403 de Akamai" in informe.avisos[0]
    assert "ninos/nino/ropa-deportiva" in informe.accionables[0]


def test_una_tienda_que_no_enumera_su_arbol_no_es_un_hallazgo() -> None:
    """Solo 3 de 9 saben enumerarse; exigirlo obligaría a escribirlo en seis tiendas más."""
    informe = Informe("falsa")

    revisar_cobertura(TiendaFalsa(), informe)  # type: ignore[arg-type]

    assert informe.esta_bien
    assert any("no la sabe enumerar" in linea for linea in informe.lineas)


def test_la_cobertura_se_cronometra_como_las_demas_capas() -> None:
    """Sin la medida en `vigia_run` no habría línea base con la que ver su deriva (#111)."""
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/camisetas")]},
        mapeadas=["ninos/nina/camisetas"],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert informe.tiempos["cobertura"].unidades == 1
    assert informe.tiempos["cobertura"].unidad == "nodo"


# --- el vigía de las declaraciones ----------------------------------------------------------


@pytest.mark.parametrize("slug", available_slugs())
def test_toda_tienda_que_enumera_su_arbol_se_vigila_o_se_declara(slug: str) -> None:
    """Simétrico del de hojas, pero acotado: solo obliga a quien YA sabe enumerar su árbol.

    No se exige `SupportsCategoryTree` a todas —tres tiendas no lo implementan y pedirlo sería otra
    issue entera—, pero la que sabe enumerarse o se vigila (`SupportsCoverageWatch`) o dice por qué
    no. Sin esto, añadir `category_tree()` a una tienda la dejaría fuera de la capa en silencio.
    """
    store = get_store(slug, _CFG)
    if not isinstance(store, SupportsCategoryTree):
        return
    # El separador se le exige a TODA la que enumera, se vigile o no (#179): sin él, `--tree` no
    # sabe qué cuelga de una hoja ingerida y lo publica como hueco, que es ruido en el único
    # instrumento que tienen las tiendas declaradas.
    assert store.tree_separator(), f"{slug!r} no declara con qué separador anida sus rutas"
    if isinstance(store, SupportsCoverageWatch):
        assert list(store.tree_roots()), f"{slug!r} declara vigilancia de cobertura sin raíces"
        return
    assert COBERTURA_SIN_VIGILAR.get(slug), (
        f"la tienda {slug!r} sabe enumerar su árbol pero no implementa `SupportsCoverageWatch`, "
        "así que el vigía no puede ver una categoría nueva. Impleméntalo, o declara el motivo en "
        "`COBERTURA_SIN_VIGILAR` de scraper/vigia.py."
    )


@pytest.mark.parametrize("slug", sorted(COBERTURA_DECLARADA))
def test_cobertura_declarada_no_solapa_con_lo_mapeado(slug: str) -> None:
    """Una declaración que además ingerimos está caducada, y una caducada TAPA lo que hay que ver.

    Es el modo de fallo propio de una lista de excepciones: se escribe una vez, la tienda cambia y
    nadie la revisa. Que rompa `just check` es lo que la obliga a envejecer con ruido.
    """
    store = get_store(slug, _CFG)
    mapeadas = set(store.mapped_leaves())  # type: ignore[attr-defined]
    solapan = mapeadas & set(COBERTURA_DECLARADA[slug])
    assert not solapan, (
        f"{slug!r} declara como «fuera a propósito» {sorted(solapan)}, pero además están en "
        "CATEGORIES. Quita la declaración: sobra, y de paso taparía una hija que sí fuese un hueco."
    )


@pytest.mark.parametrize("slug", sorted(COBERTURA_DECLARADA))
def test_toda_declaracion_lleva_su_motivo(slug: str) -> None:
    """El motivo hace la excepción revisable; sin él es un olvido con formato de decisión."""
    sin_motivo = [ruta for ruta, motivo in COBERTURA_DECLARADA[slug].items() if not motivo.strip()]
    assert not sin_motivo, f"{slug!r} declara sin motivo: {sin_motivo}"
