"""Vigía en vivo: comprueba que las tiendas nos siguen dejando entrar (#67).

Uso:
    python -m scraper.vigia                      # todas las tiendas del registro
    python -m scraper.vigia --retailer cacles    # una sola, para depurar
    python -m scraper.vigia --dry-run            # informa por consola, sin issue ni histórico

**Por qué existe.** Un scraper deja de funcionar de dos maneras: porque la tienda cambió (una hoja
de categoría caduca, el JSON cambia de forma) o porque la tienda dejó de dejarnos entrar. Lo segundo
es silencioso y se descubre tarde: el arreglo de la huella TLS de Cacles (`scraper/tls.py`) se apoya
en un detalle interno de httpcore, y si un bump lo rompe volvemos a comer 429 sin que nadie se
entere. La señal existía —`--check-categories` y los tests `*_LIVE=1`— pero **solo corría a mano**,
y nadie la lanza tres semanas después, que es justo cuando hace falta.

**Por qué en el cluster y no en CI.** La pregunta que responde no es «¿la tienda está viva?» sino
«¿nos deja entrar *a nosotros*?», y eso depende de por dónde salimos a internet. Un runner de
GitHub tiene otra IP y otra reputación que el cluster: contestaría por otro. Corre como CronJob
(`deal-tracker/base/vigia-cronjob.yaml` en el repo de manifiestos), que además es el único
`suspend: false` del despliegue — un vigía suspendido es exactamente el problema que viene a
resolver.

**Por qué no son tests.** La imagen del scraper solo copia `src` (ver `Dockerfile`): no lleva pytest
ni los tests. Lo que tenga que correr en el cluster tiene que vivir aquí.

**Por qué además cronometra (#111).** El veredicto puede ser correcto y aun así ocultar la avería:
el 02/08/2026 el sondeo de Hipercor tardó 24 min 28 s desde el pod y 2 min 04 s desde fuera, verde
las dos veces, porque la tienda seguía regulándonos el paso tras el bloqueo de #107. Así que cada
capa se cronometra, se normaliza a segundos por unidad —un absoluto envejece mal, los catálogos
crecen—, se guarda en `vigia_run` y se compara contra la mediana de las últimas ejecuciones de esa
misma tienda. Sirve para dimensionar el `activeDeadlineSeconds` con una tendencia en vez de
descubrirlo cuando una pasada muere por deadline y hace rollback.

**Cómo se entera de las tiendas nuevas.** Recorriendo `registry.available_slugs()`, no una lista
propia. Registrar una tienda es meterla en el vigía, y el CronJob tampoco nombra tiendas, así que
añadir una no obliga a tocar el repo de manifiestos. Lo que el registro no puede garantizar —que la
tienda implemente `check_leaves()`— lo vigila `test_toda_tienda_registrada_tiene_vigilancia`, que
rompe `just check`.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from .avisos import AvisoGitHub
from .config import Config, load_dotenv
from .stores.base import (
    BaseStore,
    LeafHealth,
    ListingEntry,
    ScrapedProduct,
    SupportsLeafHealth,
)
from .stores.registry import available_slugs, get_store
from .vigia_historial import Base, Historial

# El reloj, indirecto a propósito: así los tests fijan duraciones sin parchear el módulo `time`
# entero, que es global y afectaría a cualquier otra cosa que corra en el mismo proceso.
_reloj = time.monotonic

# Tiendas registradas a las que se les perdona no tener `check_leaves()`, **con el motivo escrito**.
# Vacío a propósito: las cuatro de hoy lo implementan. Existe para que la excepción sea una decisión
# explícita y revisable y no un olvido silencioso; quien añada una entrada aquí está diciendo «esta
# tienda no se puede sondear por hojas y sé por qué».
SIN_VIGILANCIA_DE_HOJAS: dict[str, str] = {}

# Cuántos productos se llevan hasta el final (listado -> detalle -> parseo) por tienda. Cinco basta
# para saber que la cadena entera sigue produciendo productos con variantes y precios, y acota el
# gasto: en Zara, que pide el detalle de uno en uno, son cinco peticiones y no 2219.
MUESTRA_POR_DEFECTO = 5


@dataclass(frozen=True)
class Medida:
    """Lo que tardó una capa y sobre cuántas unidades, que es lo que la hace comparable.

    Un absoluto por tienda envejece mal —los catálogos crecen— así que lo que se compara entre
    semanas es `por_unidad`: segundos por hoja sondeada, segundos por producto pedido.
    """

    segundos: float
    unidades: int
    unidad: str

    @property
    def por_unidad(self) -> float | None:
        """`None` cuando no llegó a cubrirse ni una unidad: no hay ritmo que calcular."""
        return self.segundos / self.unidades if self.unidades else None

    def render(self) -> str:
        if self.por_unidad is None:
            return f"{_duracion(self.segundos)} (sin unidades)"
        ritmo = f"{_numero(self.por_unidad)} s/{self.unidad}"
        return f"{_duracion(self.segundos)} ({ritmo} · {self.unidades})"


@dataclass
class Informe:
    """Lo que el vigía tiene que contar de UNA tienda.

    Separa `accionable` de `aviso` por la misma razón que `--check-categories`: un vigía que da
    falsas alarmas rutinarias acaba silenciado, que es peor que no tenerlo. Solo lo accionable
    —algo que alguien puede arreglar— sale != 0 y abre issue; lo demás se cuenta y se sigue.
    """

    slug: str
    lineas: list[str] = field(default_factory=list)
    accionables: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    tiempos: dict[str, Medida] = field(default_factory=dict)
    # Aparte de `lineas` solo por el orden del informe: la comparación con el histórico se lee
    # justo debajo de los tiempos, que es lo que explica.
    comparaciones: list[str] = field(default_factory=list)

    @property
    def esta_bien(self) -> bool:
        return not self.accionables

    @property
    def segundos(self) -> float:
        return sum(m.segundos for m in self.tiempos.values())

    def render(self) -> str:
        partes = [f"## {self.slug}", *self.lineas]
        if self.tiempos:
            capas = " · ".join(f"{capa} {m.render()}" for capa, m in self.tiempos.items())
            partes.append(f"tiempos: {capas} · total {_duracion(self.segundos)}")
        partes += self.comparaciones
        partes += [f"✖ {motivo}" for motivo in self.accionables]
        partes += [f"⚠ {motivo}" for motivo in self.avisos]
        return "\n".join(partes)


@contextmanager
def _cronometrar(
    informe: Informe, capa: str, unidad: str, contar: Callable[[], int]
) -> Iterator[None]:
    """Anota en el informe lo que tardó el bloque, pase lo que pase dentro.

    El `finally` no es celo: una capa que revienta a mitad **conserva lo medido hasta ahí**, y
    «murió tras 12 hojas en 8 min» es justo el dato que hace falta cuando una tienda nos empieza a
    regular el paso. `contar` se llama al final para que las unidades sean las que se llegaron a
    cubrir y no las que se esperaban.
    """
    inicio = _reloj()
    try:
        yield
    finally:
        informe.tiempos[capa] = Medida(_reloj() - inicio, contar(), unidad)


def revisar_hojas(store: BaseStore, informe: Informe) -> None:
    """Capa 1: ¿siguen existiendo las hojas de categoría configuradas?

    La política de veredicto vive aquí y `run.py --check-categories` la reutiliza, para que no haya
    dos copias de la misma regla (el repo ya paga ese precio en otro sitio, ver #39):

    - Una hoja **RETIRADA** es accionable: pide un id nuevo en `CATEGORIES`.
    - Una hoja **SIN VEREDICTO** avisa pero no rompe: medido contra Sfera, un chequeo normal ya trae
      un 403 suelto de Akamai.
    - Que **ninguna** hoja se confirme viva sí rompe: eso ya no es un blip, es un bloqueo. Es la
      forma en la que se vería una regresión de la huella TLS, que devolvería 429 en todas.
    """
    if not isinstance(store, SupportsLeafHealth):
        motivo = SIN_VIGILANCIA_DE_HOJAS.get(store.slug)
        if motivo:
            informe.lineas.append(f"hojas: sin sondeo por decisión ({motivo})")
        else:
            informe.accionables.append(
                "sin vigilancia de hojas: no implementa `SupportsLeafHealth`, así que una "
                "categoría caducada dejaría de ingerirse sin que nadie se entere (ver "
                "`check_leaves()` en stores/base.py)"
            )
        return

    # Los contadores viven fuera del `with` para que el cronómetro pueda contar las hojas que se
    # llegaron a sondear aunque `check_leaves()` reviente a mitad.
    vivas = 0
    retiradas: list[LeafHealth] = []
    sin_veredicto: list[LeafHealth] = []

    def sondeadas() -> int:
        return vivas + len(retiradas) + len(sin_veredicto)

    with _cronometrar(informe, "hojas", "hoja", sondeadas):
        for hoja in store.check_leaves():
            if hoja.alive:
                vivas += 1
            elif hoja.alive is False:
                retiradas.append(hoja)
            else:
                sin_veredicto.append(hoja)

    total = sondeadas()
    informe.lineas.append(f"hojas: {vivas}/{total} vivas")
    if retiradas:
        informe.accionables.append(
            f"{len(retiradas)} hoja(s) RETIRADA(S) — busca sus ids nuevos y actualiza CATEGORIES:\n"
            + "\n".join(f"  - {_describe(h)}" for h in retiradas)
        )
    if total and not vivas:
        informe.accionables.append(
            "ninguna hoja confirmada viva: esto no es un blip, es un bloqueo. Si el detalle dice "
            "429 `local_rate_limited`, es la huella TLS y no el ritmo (scraper/tls.py)."
        )
    if sin_veredicto:
        informe.avisos.append(
            f"{len(sin_veredicto)} hoja(s) sin veredicto (fallo del sondeo, no retirada):\n"
            + "\n".join(f"  - {_describe(h)}" for h in sin_veredicto)
        )


def revisar_parseo(store: BaseStore, informe: Informe, muestra: int) -> None:
    """Capa 2: la cadena entera —listado, detalle, parseo— sigue produciendo productos usables.

    Genérica a propósito: se apoya solo en `BaseStore`, así que **cubre también a las tiendas que
    todavía no existen**. Es lo que hoy comprueban a mano `test_sfera_live.py` y
    `test_cacles_live_acepta_nuestra_huella`, pero sin código por tienda que alguien tenga que
    acordarse de escribir.

    `islice` sobre el generador y no `list()`: las cuatro tiendas emiten perezosamente, así que
    cortar a los `muestra` primeros para el recorrido en la primera hoja en vez de barrer el
    catálogo entero.
    """
    # La unidad de esta capa es el producto **pedido**, no el emitido: lo que fija el coste es
    # cuántas fichas se piden, y así el ritmo sigue siendo comparable la semana que la tienda deje
    # de emitir alguno.
    entradas: list[ListingEntry] = []
    productos: list[ScrapedProduct] = []

    with _cronometrar(informe, "parseo", "producto", lambda: len(entradas)):
        entradas = list(itertools.islice(store.list_catalog(), muestra))
        if entradas:
            productos = list(store.fetch_details(entradas))

    if not entradas:
        informe.accionables.append(
            "el listado no devolvió ni una entrada: o la tienda nos ha cerrado la puerta o el "
            "endpoint de catálogo ha cambiado de forma"
        )
        return

    if not productos:
        informe.accionables.append(
            f"{len(entradas)} entradas en el listado pero ningún producto con detalle: el parseo "
            "se ha quedado sin nada que emitir"
        )
        return

    variantes = sum(len(p.variants) for p in productos)
    if not variantes:
        informe.accionables.append(
            f"{len(productos)} productos sin una sola variante: sin talla/color no hay nada que "
            "seguir ni precio que registrar"
        )
        return

    sin_precio = [v for p in productos for v in p.variants if v.price <= 0]
    if sin_precio:
        informe.accionables.append(
            f"{len(sin_precio)}/{variantes} variantes con precio <= 0: el precio ha cambiado de "
            "sitio o de unidad en el JSON de la tienda"
        )
        return

    informe.lineas.append(
        f"parseo: {len(entradas)} entradas -> {len(productos)} productos, {variantes} variantes, "
        "precios > 0"
    )


def revisar_tienda(slug: str, config: Config, muestra: int) -> Informe:
    """Las dos capas sobre una tienda. Nunca eleva: un fallo inesperado ES el hallazgo."""
    informe = Informe(slug)
    try:
        store = get_store(slug, config)
    except Exception as exc:  # config imposible, dependencia que falta al arrancar...
        informe.accionables.append(f"no se ha podido construir el scraper: {exc!r}")
        return informe

    # Una excepción aquí no es un error del vigía, es el hallazgo: se anota y se sigue con las
    # demás tiendas, que una reviente no puede dejar a las otras sin mirar.
    try:
        revisar_hojas(store, informe)
    except Exception as exc:
        informe.accionables.append(f"el sondeo de hojas reventó — {type(exc).__name__}: {exc}")
        # Y NO se sigue con el parseo. Medido con Lefties sin Chromium instalado: el fallo de la
        # primera capa deja el navegador a medio arrancar y el de la segunda sale distinto y
        # engañoso («usa la API async»), tapando la causa real con un síntoma derivado. Un vigía
        # que apunta a la pista falsa es peor que uno que dice una sola cosa cierta.
        informe.avisos.append("smoke de parseo omitido: el sondeo de hojas ya falló")
        return informe
    try:
        revisar_parseo(store, informe, muestra)
    except Exception as exc:
        informe.accionables.append(f"el smoke de parseo reventó — {type(exc).__name__}: {exc}")
    return informe


def comparar_con_base(informe: Informe, bases: dict[str, Base | None], factor: float) -> None:
    """Capa 3: ¿nos están dejando entrar al ritmo de siempre? (#111)

    Pura y sin BD a propósito — la línea base se lee fuera y entra por parámetro— porque la regla
    es lo único que hay que poder testear sin Postgres.

    **Avisa, nunca acciona**, y eso no es timidez: `main()` solo publica en GitHub las tiendas con
    accionables, así que una tienda verde pero lenta se lee en el log y no abre issue. Un número
    lento suelto no es accionable —puede ser el nodo, puede ser el jueves— mientras que la serie sí
    lo será; promoverlo es una decisión para cuando haya varias semanas de histórico.

    Sin línea base no se compara y se dice, que es lo que evita confundir «va bien» con «no lo he
    mirado» durante las primeras semanas.
    """
    comparadas: list[str] = []
    sin_base: list[str] = []
    for capa, medida in informe.tiempos.items():
        ritmo = medida.por_unidad
        base = bases.get(capa)
        if ritmo is None:
            continue
        if base is None:
            sin_base.append(capa)
            continue
        veces = ritmo / base.mediana if base.mediana else 0.0
        comparadas.append(f"{capa} {_numero(base.mediana)} s/{medida.unidad} (×{_numero(veces)})")
        if veces >= factor:
            muestras = " ".join(_numero(m) for m in base.muestras)
            informe.avisos.append(
                f"{capa}: {_numero(ritmo)} s/{medida.unidad} contra una línea base de "
                f"{_numero(base.mediana)} — ×{_numero(veces)}. La puerta sigue abierta, pero nos "
                f"están dejando entrar más despacio (mediana de {len(base.muestras)}: {muestras})"
            )
    if comparadas:
        informe.comparaciones.append("base: " + " · ".join(comparadas))
    if sin_base:
        informe.comparaciones.append(
            f"base: sin línea base ({', '.join(sin_base)}) — no se compara"
        )


def _numero(valor: float) -> str:
    """Un decimal y coma, como se escriben las medidas en las issues de este repo."""
    return f"{valor:.1f}".replace(".", ",")


def _duracion(segundos: float) -> str:
    """Legible de un vistazo: `13,2 s` para lo corto, `24m 28s` a partir del minuto.

    El corte se decide sobre el valor **ya redondeado** para que 59,96 s salga como `1m 00s` y no
    como el `60,0 s` que nadie escribe.
    """
    if round(segundos, 1) < 60:
        return f"{_numero(segundos)} s"
    minutos, resto = divmod(round(segundos), 60)
    return f"{minutos}m {resto:02d}s"


def _describe(hoja: LeafHealth) -> str:
    ambito = f"{hoja.scope.gender}/{hoja.scope.section}/{hoja.scope.category}"
    return f"{hoja.leaf} ({ambito}) {hoja.detail}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scraper.vigia",
        description="Smoke en vivo de todas las tiendas: avisa antes de que falle el CronJob",
    )
    parser.add_argument(
        "--retailer",
        help=f"revisa solo esta tienda ({', '.join(available_slugs())}); por defecto, todas",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="informa por consola sin abrir issue en GitHub ni guardar la medida en `vigia_run`",
    )
    parser.add_argument(
        "--muestra",
        type=int,
        default=MUESTRA_POR_DEFECTO,
        help=f"productos llevados hasta el parseo por tienda (por defecto {MUESTRA_POR_DEFECTO})",
    )
    return parser.parse_args(argv)


def informar(informes: Iterable[Informe]) -> str:
    """Cuerpo del informe, el mismo que se imprime y que se manda a la issue."""
    return "\n\n".join(inf.render() for inf in informes)


def _medidas(informes: Iterable[Informe]) -> list[tuple[str, str, float, int]]:
    """Las medidas en la forma plana que persiste `vigia_historial`, para que ese módulo no tenga
    que saber qué es un `Informe`."""
    return [
        (inf.slug, capa, m.segundos, m.unidades)
        for inf in informes
        for capa, m in inf.tiempos.items()
    ]


def _resumen_de_tiempos(informes: Sequence[Informe]) -> str:
    total = sum(inf.segundos for inf in informes)
    lenta = max(informes, key=lambda inf: inf.segundos)
    return f"⏱ total {_duracion(total)} — la más lenta: {lenta.slug} ({_duracion(lenta.segundos)})"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    config = Config.from_env()

    slugs = [args.retailer] if args.retailer else available_slugs()
    # El historial se abre antes de sondear porque la línea base tiene que estar en el informe que
    # se publica, no en un segundo pase. Si no está, `Historial` se queda inerte y todo lo demás
    # sigue igual: el veredicto no puede depender de que haya base de datos.
    historial = Historial.abrir(config)
    try:
        informes = []
        for slug in slugs:
            informe = revisar_tienda(slug, config, args.muestra)
            bases = {
                capa: historial.linea_base(slug, capa, config.vigia_base_muestras)
                for capa in informe.tiempos
            }
            comparar_con_base(informe, bases, config.vigia_factor_aviso)
            informes.append(informe)
        # No se guarda en `--dry-run`: la serie mezcla mal, y una ejecución desde un portátil
        # contra la misma base metería tiempos de fuera en la línea base del cluster — que es
        # justo la diferencia que esto existe para medir (×11,8 en Hipercor).
        if not args.dry_run:
            historial.guardar(_medidas(informes))
    finally:
        historial.cerrar()

    print(informar(informes))
    if informes:
        print(_resumen_de_tiempos(informes))
    if historial.motivo:
        print(f"(sin historial: {historial.motivo})")

    malas = [inf for inf in informes if not inf.esta_bien]
    if not malas:
        print(f"\n✔ {len(informes)} tienda(s) revisadas, todas nos dejan entrar.")
        return 0

    culpables = ", ".join(inf.slug for inf in malas)
    print(f"\n✖ {len(malas)}/{len(informes)} tienda(s) con algo que arreglar: {culpables}")
    if args.dry_run:
        print("(--dry-run: no se abre issue)")
        return 1

    aviso = AvisoGitHub.from_env()
    if aviso is None:
        # Igual que Keycloak y Telegram: sin configurar, apagado. Es la ruta de dev local.
        print("(sin VIGIA_GITHUB_TOKEN/VIGIA_GITHUB_REPO: no se abre issue)")
        return 1
    try:
        print(aviso.publicar(informar(malas)))
    except Exception as exc:
        # Que falle el aviso no puede tapar el hallazgo: ya está impreso arriba y el job sale != 0.
        print(f"⚠ el hallazgo no se pudo publicar en GitHub: {exc!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
