"""Vigía en vivo: comprueba que las tiendas nos siguen dejando entrar (#67).

Uso:
    python -m scraper.vigia                      # todas las tiendas del registro
    python -m scraper.vigia --retailer cacles    # una sola, para depurar
    python -m scraper.vigia --dry-run            # informa por consola y no abre issue

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
from collections.abc import Iterable
from dataclasses import dataclass, field

from .avisos import AvisoGitHub
from .config import Config, load_dotenv
from .stores.base import BaseStore, LeafHealth, SupportsLeafHealth
from .stores.registry import available_slugs, get_store

# Tiendas registradas a las que se les perdona no tener `check_leaves()`, **con el motivo escrito**.
# Vacío a propósito: las cuatro de hoy lo implementan. Existe para que la excepción sea una decisión
# explícita y revisable y no un olvido silencioso; quien añada una entrada aquí está diciendo «esta
# tienda no se puede sondear por hojas y sé por qué».
SIN_VIGILANCIA_DE_HOJAS: dict[str, str] = {}

# Cuántos productos se llevan hasta el final (listado -> detalle -> parseo) por tienda. Cinco basta
# para saber que la cadena entera sigue produciendo productos con variantes y precios, y acota el
# gasto: en Zara, que pide el detalle de uno en uno, son cinco peticiones y no 2219.
MUESTRA_POR_DEFECTO = 5


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

    @property
    def esta_bien(self) -> bool:
        return not self.accionables

    def render(self) -> str:
        partes = [f"## {self.slug}", *self.lineas]
        partes += [f"✖ {motivo}" for motivo in self.accionables]
        partes += [f"⚠ {motivo}" for motivo in self.avisos]
        return "\n".join(partes)


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

    vivas = 0
    retiradas: list[LeafHealth] = []
    sin_veredicto: list[LeafHealth] = []
    for hoja in store.check_leaves():
        if hoja.alive:
            vivas += 1
        elif hoja.alive is False:
            retiradas.append(hoja)
        else:
            sin_veredicto.append(hoja)

    total = vivas + len(retiradas) + len(sin_veredicto)
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
    entradas = list(itertools.islice(store.list_catalog(), muestra))
    if not entradas:
        informe.accionables.append(
            "el listado no devolvió ni una entrada: o la tienda nos ha cerrado la puerta o el "
            "endpoint de catálogo ha cambiado de forma"
        )
        return

    productos = list(store.fetch_details(entradas))
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
        "--dry-run", action="store_true", help="informa por consola sin abrir issue en GitHub"
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


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    config = Config.from_env()

    slugs = [args.retailer] if args.retailer else available_slugs()
    informes = [revisar_tienda(slug, config, args.muestra) for slug in slugs]
    print(informar(informes))

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
