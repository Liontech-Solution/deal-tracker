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
    MARCA_COBERTURA,
    MARCA_DECLARACION_HUERFANA,
    MARCA_ESTACIONAL,
    MARCA_FOTO_MUERTA,
    SIN_VIGILANCIA_DE_HOJAS,
    Informe,
    Medida,
    comparar_con_base,
    revisar_cobertura,
    revisar_fotos,
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


def _producto(
    pid: str, *, variantes: int = 1, precio: str = "19.90", image_url: str | None = None
) -> ScrapedProduct:
    return ScrapedProduct(
        retailer_product_id=pid,
        name=f"Producto {pid}",
        gender="niña",
        section="zapateria",
        category="zapatos",
        url=None,
        image_url=image_url,
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


def test_una_hoja_de_campana_apagada_avisa_pero_no_rompe() -> None:
    """Está retirada de verdad, pero su id vuelve con la campaña: pedirlo cada jueves es ruido.

    Es el falso positivo que #176 midió en Mango —404 un día, viva con el MISMO `catalogId` al
    siguiente— y que Lefties tendría igual al acabar sus rebajas.
    """
    tienda = TiendaFalsa(
        hojas=[
            LeafHealth(_AMBITO, "viva", True, "HTTP 200"),
            LeafHealth(_AMBITO, "rebajas_nina.x", False, "HTTP 404", estacional=True),
        ]
    )
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien
    assert informe.avisos and "campaña apagada" in informe.avisos[0]
    assert "rebajas_nina.x" in informe.render()
    assert "RETIRADA" not in informe.render()


def test_una_hoja_retirada_de_verdad_sigue_siendo_accionable_junto_a_una_estacional() -> None:
    """La contraprueba: la marca no puede tapar a la de al lado."""
    tienda = TiendaFalsa(
        hojas=[
            LeafHealth(_AMBITO, "viva", True, "HTTP 200"),
            LeafHealth(_AMBITO, "rebajas_nina.x", False, "HTTP 404", estacional=True),
            LeafHealth(_AMBITO, "muerta", False, "HTTP 404"),
        ]
    )
    informe = Informe("falsa")
    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "1 hoja(s) RETIRADA(S)" in informe.render()
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


# --- capa de fotos (#429) ------------------------------------------------------------------


def _resolutor(respuestas: dict[str, int | None]) -> object:
    """Resolutor de pega: devuelve el código que le digan por URL. Sin red y sin pausas."""

    def pedir(url: str) -> int | None:
        return respuestas[url]

    return pedir


def test_una_foto_que_el_cdn_no_sirve_avisa_marcada_y_sin_abrir_issue() -> None:
    """El caso de #429 visto desde fuera: la tienda publica una URL que su CDN devuelve 404.

    Avisa y no acciona: una foto perdida no es «la tienda ha dejado de dejarnos entrar», que es la
    razón de ser del vigía, y sobre cinco productos un fallo no sostiene una cifra —la sostiene D7b.
    """
    productos = [
        _producto("1", image_url="https://cdn.invalid/viva.jpg"),
        _producto("2", image_url="https://cdn.invalid/muerta.jpg"),
    ]
    informe = Informe("falsa")

    revisar_fotos(
        productos,
        informe,
        _CFG,
        _resolutor({"https://cdn.invalid/viva.jpg": 200, "https://cdn.invalid/muerta.jpg": 404}),  # type: ignore[arg-type]
    )

    assert informe.esta_bien, "una foto muerta no bloquea una release"
    assert len(informe.avisos) == 1
    assert informe.avisos[0].startswith(MARCA_FOTO_MUERTA)
    assert f"⚠ {MARCA_FOTO_MUERTA}" in informe.render()
    assert "muerta.jpg" in informe.avisos[0]
    assert "viva.jpg" not in informe.avisos[0]


def test_un_403_no_es_una_foto_muerta_sino_una_sin_veredicto() -> None:
    """El falso positivo que esta capa tiene prohibido producir, y no es hipotético.

    Preparando #429 (16/08/2026) se resolvieron 40 URL por tienda a 6 en paralelo: salieron 403 en
    40/40 de Zara y 38/40 de Lefties, y las MISMAS URL dieron 200 una a una y pausadas. O sea que
    la comprobación estaba midiendo nuestro propio ritmo y lo habría reportado como catálogo sin
    fotos. Solo el 404/410 cuenta; lo demás se dice como «sin veredicto», que no es lo mismo que
    verde y tampoco es un hallazgo.
    """
    productos = [
        _producto("1", image_url="https://cdn.invalid/frenada.jpg"),
        _producto("2", image_url="https://cdn.invalid/rota.jpg"),
        _producto("3", image_url="https://cdn.invalid/sin-red.jpg"),
    ]
    informe = Informe("falsa")

    revisar_fotos(
        productos,
        informe,
        _CFG,
        _resolutor(  # type: ignore[arg-type]
            {
                "https://cdn.invalid/frenada.jpg": 403,
                "https://cdn.invalid/rota.jpg": 500,
                "https://cdn.invalid/sin-red.jpg": None,
            }
        ),
    )

    assert not informe.avisos, "ninguna de las tres es una foto muerta"
    assert informe.esta_bien
    assert "3 sin veredicto" in informe.render()


def test_una_muestra_sin_fotos_lo_dice_en_vez_de_pasar_en_verde() -> None:
    """Una comprobación que no pudo comprobar nada no es una comprobación en verde."""
    informe = Informe("falsa")

    revisar_fotos([_producto("1")], informe, _CFG, _resolutor({}))  # type: ignore[arg-type]

    assert not informe.avisos
    assert "fotos: los productos de la muestra no traen imagen" in informe.render()


def test_el_parseo_devuelve_los_productos_para_que_las_fotos_no_pidan_catalogo_otra_vez() -> None:
    """La capa de fotos se cuelga de lo ya parseado: cero peticiones extra de catálogo."""
    tienda = TiendaFalsa(
        entradas=[_entrada("1"), _entrada("2")],
        productos=[_producto("1", image_url="https://cdn.invalid/a.jpg"), _producto("2")],
    )
    informe = Informe("falsa")

    productos = revisar_parseo(tienda, informe, muestra=5)  # type: ignore[arg-type]

    assert [p.retailer_product_id for p in productos] == ["1", "2"]


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


def test_el_hallazgo_de_cobertura_se_marca_para_que_el_liston_lo_distinga() -> None:
    """Un `✖` de cobertura NO es un `✖` de «la tienda no nos deja entrar», y se nota en el texto.

    Esto lo fija un test a propósito, y el motivo es que hay un consumidor fuera de este repo: el
    listón de `/validar-qa` (`.claude/skills/validar-qa/SKILL.md`) lee el log del vigía y decide con
    él si una versión se promueve. Hasta #251 hacía P0 *cualquier* `✖`, y así cinco prendas de
    bañador de bebé —que el equipo había etiquetado `prioridad-4`— bloquearon dos releases seguidas.

    Si alguien reescribe el mensaje sin la marca, lo que tiene que romperse es ESTO y no una
    validación de QA dentro de tres semanas.
    """
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/ropa-deportiva", count=25)]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert informe.accionables[0].startswith(MARCA_COBERTURA)
    assert f"✖ {MARCA_COBERTURA}" in informe.render()


def test_la_hoja_estacional_se_marca_como_exenta() -> None:
    """La otra mitad de #251, y el extremo contrario: un `⚠` que NO debe abrir issue.

    El vigía ya declara en código que esto vuelve solo (`LeafHealth.estacional`), así que abrir una
    issue por ello sería el «hallazgo de relleno» que la propia skill prohíbe. La marca es lo que
    permite al listón eximirlo sin tener que interpretar la prosa.
    """
    tienda = TiendaFalsa(
        hojas=[
            LeafHealth(_AMBITO, "viva", True, "HTTP 200"),
            LeafHealth(_AMBITO, "rebajas_nina.x", False, "HTTP 404", estacional=True),
        ]
    )
    informe = Informe("falsa")

    revisar_hojas(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien, "sigue sin ser accionable: la marca clasifica, no cambia el veredicto"
    assert informe.avisos[0].startswith(MARCA_ESTACIONAL)
    assert f"⚠ {MARCA_ESTACIONAL}" in informe.render()


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


def test_una_declaracion_que_la_tienda_ya_no_publica_avisa_sin_abrir_issue() -> None:
    """La pregunta simétrica del hueco de cobertura (#260, cuarta casilla).

    Una declaración es una decisión sobre una ruta, y cuando la ruta se va la decisión se queda
    apuntando al vacío. Pasaba ya: 4 de las 84 de Lefties el 14/08/2026, y 0 en las otras cuatro
    tiendas que se enumeran.

    Avisa y **no** es accionable a propósito: una huérfana no esconde catálogo —para eso está el
    `✖ [cobertura]`— y muchas son de campaña, cuyo id vuelve con la temporada. Hacerla accionable
    abriría issue dos veces al año para reescribir la misma decisión.
    """
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/bano")]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    COBERTURA_DECLARADA["falsa"] = {
        "ninos/nina/bano": "no es del brief",
        "ninos/nina/rebajas-verano": "campaña: 5/0/5",
    }
    try:
        revisar_cobertura(tienda, informe)  # type: ignore[arg-type]
    finally:
        del COBERTURA_DECLARADA["falsa"]

    assert informe.esta_bien, "una declaración caducada no es un hallazgo accionable"
    assert len(informe.avisos) == 1
    assert "ninos/nina/rebajas-verano" in informe.avisos[0]
    assert "ninos/nina/bano" not in informe.avisos[0], "esa sigue publicada"


def test_la_declaracion_huerfana_se_marca_como_exenta() -> None:
    """La tercera marca, y la que faltaba (#430).

    Sin ella el listón la lee como `⚠` sin marca, que su tabla de severidad manda a **P1**, o sea
    abre issue por algo que el propio código de arriba declara benigno por diseño — el «hallazgo de
    relleno» que la skill prohíbe. En la validación de v0.5.0 hubo que bajarla a P2 razonándolo a
    mano, que es exactamente lo que la marca existe para evitar: que la severidad la decida el
    validador de turno y no el emisor.
    """
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/bano")]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    COBERTURA_DECLARADA["falsa"] = {
        "ninos/nina/bano": "no es del brief",
        "ninos/nina/rebajas-verano": "campaña: 5/0/5",
    }
    try:
        revisar_cobertura(tienda, informe)  # type: ignore[arg-type]
    finally:
        del COBERTURA_DECLARADA["falsa"]

    assert informe.esta_bien, "la marca clasifica, no cambia el veredicto"
    assert informe.avisos[0].startswith(MARCA_DECLARACION_HUERFANA)
    assert f"⚠ {MARCA_DECLARACION_HUERFANA}" in informe.render()


def test_una_declaracion_de_una_rama_que_aun_tiene_hijas_no_es_huerfana() -> None:
    """Declarar la rama basta, así que la rama puede no emitirse como nodo y seguir viva.

    Sin esta mitad, cada declaración por rama de H&M o C&A —que es como están escritas— saldría
    como huérfana el primer jueves.
    """
    tienda = TiendaConArbol(
        {"ninos/nina": [_nodo("ninos/nina/bano/bikinis", depth=2)]},
        mapeadas=[],
    )
    informe = Informe("falsa")

    COBERTURA_DECLARADA["falsa"] = {"ninos/nina/bano": "no es del brief"}
    try:
        revisar_cobertura(tienda, informe)  # type: ignore[arg-type]
    finally:
        del COBERTURA_DECLARADA["falsa"]

    assert informe.esta_bien
    assert not informe.avisos


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


def test_el_cajon_que_contiene_una_hoja_mapeada_no_es_un_hueco() -> None:
    """El simétrico del anterior, y lo que destapó H&M (#179).

    `/kids/boys/clothing` no es catálogo que nos falte: es el cajón donde están `trousers` y
    `nightwear`, que sí ingerimos. Es la primera tienda con taxonomía de tres niveles —Sfera y
    Springfield cuelgan las hojas de la raíz—, así que hasta ahora nadie emitía un nodo intermedio.
    """
    tienda = TiendaConArbol(
        {
            "/kids/boys": [
                _nodo("/kids/boys/clothing", hijas=True),
                _nodo("/kids/boys/clothing/trousers", depth=2),
            ]
        },
        mapeadas=["/kids/boys/clothing/trousers"],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert informe.esta_bien, informe.accionables


def test_el_cajon_no_tapa_a_una_hija_nueva() -> None:
    """El límite de la regla anterior: silencia el nodo intermedio, nunca lo que cuelga de él.

    Si callara también a las hijas, la capa entera dejaría de servir justo donde vive el brief.
    """
    tienda = TiendaConArbol(
        {
            "/kids/boys": [
                _nodo("/kids/boys/clothing", hijas=True),
                _nodo("/kids/boys/clothing/trousers", depth=2),
                _nodo("/kids/boys/clothing/dungarees", depth=2),
            ]
        },
        mapeadas=["/kids/boys/clothing/trousers"],
    )
    informe = Informe("falsa")

    revisar_cobertura(tienda, informe)  # type: ignore[arg-type]

    assert not informe.esta_bien
    assert "dungarees" in informe.accionables[0]
    assert "/kids/boys/clothing " not in informe.accionables[0]


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


# --- el barrido: lo que sobrevive a que maten el proceso (#258) ------------------------------
#
# Hasta aquí nada ejercía `main()`, y por eso el 07/08/2026 nadie vio venir que un barrido que se
# come su plazo no deja rastro: el informe se construía entero en memoria y se imprimía al final,
# así que el `DeadlineExceeded` se lo llevaba junto con el pod. Estos tests fijan lo contrario —que
# cada tienda se publica y se persiste EN CUANTO termina— y que quedarse sin plazo se cuenta.


class _HistorialFalso:
    """Doble de `Historial` que apunta cada `guardar` en la traza compartida, sin base de datos."""

    motivo: str | None = None

    def __init__(self, traza: list[str]) -> None:
        self.traza = traza
        self.tandas: list[list[tuple[str, str, float, int]]] = []

    def linea_base(self, slug: str, capa: str, muestras: int) -> Base | None:
        return None

    def guardar(self, medidas) -> int:  # type: ignore[no-untyped-def]
        filas = list(medidas)
        self.tandas.append(filas)
        self.traza.append(f"guardar:{','.join(sorted({f[0] for f in filas}))}")
        return len(filas)

    def cerrar(self) -> None:
        self.traza.append("cerrar")


def _montar_barrido(
    monkeypatch: pytest.MonkeyPatch,
    slugs: list[str],
    config: Config,
    instantes: list[float] | None = None,
) -> tuple[list[str], _HistorialFalso]:
    """Deja `main()` listo para correr sin red, sin base de datos y sin `.env`.

    Devuelve `(traza, historial)`: la traza es el orden real de los hechos —sondeos y escrituras
    entrelazados—, que es justo lo que estos tests miran.
    """
    traza: list[str] = []
    historial = _HistorialFalso(traza)

    def revisar_falsa(slug: str, cfg: Config, muestra: int) -> Informe:
        traza.append(f"sondeo:{slug}")
        return Informe(slug, tiempos={"hojas": Medida(10.0, 4, "hoja")})

    monkeypatch.setattr(vigia, "load_dotenv", lambda: None)
    monkeypatch.setattr(vigia.Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(vigia, "available_slugs", lambda: slugs)
    monkeypatch.setattr(vigia, "revisar_tienda", revisar_falsa)
    monkeypatch.setattr(vigia.Historial, "abrir", classmethod(lambda cls, cfg: historial))
    if instantes is not None:
        it = iter(instantes)
        monkeypatch.setattr(vigia, "_reloj", lambda: next(it))
    return traza, historial


def test_cada_tienda_se_publica_y_se_persiste_antes_de_sondear_la_siguiente(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """El corazón de #258: el informe deja de existir solo en memoria.

    Lo que se comprueba no es que la salida contenga las tres tiendas —eso también pasaba antes—
    sino el ORDEN: que cuando el vigía empieza con `b`, lo de `a` ya está escrito y guardado. Es la
    diferencia entre un plazo agotado que dice en qué tienda se atascó y uno mudo.
    """
    cfg = Config(database_url="postgresql://unused")
    traza, historial = _montar_barrido(monkeypatch, ["a", "b", "c"], cfg)

    assert vigia.main([]) == 0

    assert traza == [
        "sondeo:a",
        "guardar:a",
        "sondeo:b",
        "guardar:b",
        "sondeo:c",
        "guardar:c",
        "cerrar",
    ]
    # Una tanda por tienda, no una sola con las tres al final: lo que se persiste DURANTE el
    # barrido es lo único que sobrevive a que el controlador mate el pod.
    assert [len(t) for t in historial.tandas] == [1, 1, 1]
    salida = capsys.readouterr().out
    assert salida.index("## a") < salida.index("## b") < salida.index("## c")


def test_lo_ya_barrido_sigue_impreso_aunque_el_bucle_muera_despues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un `KeyboardInterrupt` a mitad imita lo que hace el controlador al agotarse el plazo.

    `revisar_tienda` no eleva nunca (un fallo de tienda ES el hallazgo), así que lo que de verdad
    puede tumbar el bucle es que maten el proceso. Antes eso se llevaba el informe entero; ahora lo
    de las tiendas que terminaron ya está fuera.
    """
    cfg = Config(database_url="postgresql://unused")
    traza, historial = _montar_barrido(monkeypatch, ["a", "b"], cfg)
    original = vigia.revisar_tienda

    def revienta_en_b(slug: str, config: Config, muestra: int) -> Informe:
        if slug == "b":
            raise KeyboardInterrupt
        return original(slug, config, muestra)

    monkeypatch.setattr(vigia, "revisar_tienda", revienta_en_b)

    with pytest.raises(KeyboardInterrupt):
        vigia.main([])

    assert "## a" in capsys.readouterr().out
    assert historial.tandas == [[("a", "hojas", 10.0, 4)]]
    assert traza[-1] == "cerrar"  # el `finally` cierra la conexión igual


def test_al_agotarse_el_plazo_se_corta_y_lo_que_falta_es_accionable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Salir por su propio pie es lo único que convierte un plazo agotado en un informe.

    El corte va ENTRE tiendas: `b` no llega a sondearse, así que no deja una medida a medias que
    luego envenene su línea base durante semanas.
    """
    cfg = Config(database_url="postgresql://unused", vigia_plazo_segundos=100.0)
    # arranque=0; comprobación antes de `a` = 1 (cabe); antes de `b` = 150 (se pasó).
    traza, _ = _montar_barrido(monkeypatch, ["a", "b", "c"], cfg, instantes=[0.0, 1.0, 150.0])

    assert vigia.main(["--dry-run"]) == 1

    assert traza == ["sondeo:a", "cerrar"]
    salida = capsys.readouterr().out
    assert "## barrido incompleto" in salida
    assert "✖ el barrido se quedó sin plazo (1m 40s) con 2 tienda(s) sin sondear: b, c" in salida


def test_sin_plazo_configurado_el_barrido_no_se_corta(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`0` es el valor por defecto y el de local, donde nadie mata el proceso.

    Va aparte porque el fallo que importa aquí es el silencioso: un plazo mal leído que cortase
    barridos legítimos dejaría tiendas sin mirar y el informe seguiría diciendo que todo va bien.
    """
    cfg = Config(database_url="postgresql://unused")
    traza, _ = _montar_barrido(monkeypatch, ["a", "b"], cfg, instantes=[0.0, 9e9, 9e9])

    assert vigia.main([]) == 0

    assert [p for p in traza if p.startswith("sondeo:")] == ["sondeo:a", "sondeo:b"]
    assert "barrido incompleto" not in capsys.readouterr().out
