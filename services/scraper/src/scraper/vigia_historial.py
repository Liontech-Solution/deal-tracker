"""Serie temporal de lo que tarda el vigía, y la línea base que sale de ella (#111).

Vive aparte de `vigia.py` por la misma razón que `avisos.py`: el vigía **decide**, esto
**persiste**. Así la regla de comparación se testea sin BD y esto se testea sin red.

**Nada de aquí puede costar el veredicto.** El vigía existe para contestar «¿nos dejan entrar?» y
es el único CronJob con `suspend: false`; que no se pueda guardar una medida es una degradación,
no un fallo. Por eso `Historial` nunca eleva: ante el primer problema —BD inalcanzable, tabla que
todavía no existe, INSERT rechazado— se queda inerte con el motivo escrito en `motivo`, y el vigía
sigue sondeando, informando y abriendo issue igual que antes de que esto existiera.

Lo de «la tabla todavía no existe» no es hipotético: el vigía **no aplica migraciones** (eso lo
hacen `--migrate` del scraper y el initContainer del web), y en QA el despliegue va por releases
semver, así que hay una ventana real en la que el jueves llega antes que la `0022`.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass

import psycopg

from .config import Config
from .db import connect

# Muestras previas por debajo de las cuales no hay línea base. Con una sola, un jueves con el nodo
# ocupado ya cantaría y a la semana siguiente se desmentiría solo — que es exactamente cómo un
# vigía se convierte en ruido y acaba silenciado.
MUESTRAS_MINIMAS = 2

# Qué fracción de la cobertura habitual tiene que alcanzar una fila para servir de línea base. Una
# capa que reventó a la segunda de 32 hojas deja una medida honesta pero no representativa: se
# guarda (es un dato) y no se compara con ella.
#
# Es una proporción y no un mínimo absoluto por un caso medido: Cacles publica **una sola hoja**,
# así que cualquier suelo tipo «al menos 3 unidades» la dejaría sin línea base para siempre — y es
# justo la tienda cuyo 429 por huella TLS motivó el vigía. Lo que descalifica a una muestra no es
# ser pequeña, es ser pequeña **comparada con las de su propia tienda**.
PROPORCION_MINIMA = 0.7

_ULTIMAS = """
SELECT segundos, unidades
FROM vigia_run
WHERE retailer_slug = %s AND capa = %s AND unidades > 0
ORDER BY ran_at DESC
LIMIT %s
"""

_INSERTAR = """
INSERT INTO vigia_run (retailer_slug, capa, segundos, unidades)
VALUES (%s, %s, %s, %s)
"""


@dataclass(frozen=True)
class Base:
    """La línea base de una capa: la mediana de sus últimas ejecuciones, en segundos por unidad.

    Se guarda también la lista de muestras porque el informe la enseña: un ×11,8 contra una
    mediana es mucho más creíble cuando se ve que las cuatro muestras eran parecidas entre sí.
    """

    mediana: float
    muestras: tuple[float, ...]


@dataclass
class Historial:
    """Acceso a `vigia_run` que se degrada en vez de romper.

    Tras el primer fallo se queda inerte (`conn = None`) con el motivo escrito: si la BD no está,
    no está para todas las tiendas, y reintentar por cada una solo alarga el job y llena el log
    con el mismo error siete veces.
    """

    conn: psycopg.Connection | None
    motivo: str | None = None

    @classmethod
    def abrir(cls, config: Config) -> Historial:
        try:
            return cls(connect(config))
        except Exception as exc:
            return cls(None, f"no se pudo conectar a la base: {exc!r}")

    @property
    def disponible(self) -> bool:
        return self.conn is not None

    def linea_base(self, slug: str, capa: str, muestras: int) -> Base | None:
        """La mediana de las últimas `muestras` ejecuciones de esa tienda y capa, o `None`.

        `None` significa «no hay con qué comparar», que es un estado normal —una tienda nueva, o
        la primera semana tras desplegar esto— y nunca un error.
        """
        if self.conn is None:
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(_ULTIMAS, (slug, capa, muestras))
                filas = cur.fetchall()
        except Exception as exc:
            self._caerse(f"no se pudo leer la línea base: {exc!r}")
            return None

        if not filas:
            return None
        cobertura = max(unidades for _, unidades in filas)
        por_unidad = [
            float(seg) / unidades
            for seg, unidades in filas
            if unidades >= cobertura * PROPORCION_MINIMA
        ]
        if len(por_unidad) < MUESTRAS_MINIMAS:
            return None
        return Base(statistics.median(por_unidad), tuple(por_unidad))

    def guardar(self, medidas: Iterable[tuple[str, str, float, int]]) -> int:
        """Persiste `(slug, capa, segundos, unidades)`. Devuelve cuántas filas escribió."""
        if self.conn is None:
            return 0
        filas: Sequence[tuple[str, str, float, int]] = list(medidas)
        if not filas:
            return 0
        try:
            with self.conn.cursor() as cur:
                cur.executemany(_INSERTAR, filas)
            self.conn.commit()
        except Exception as exc:
            self._caerse(f"no se pudieron guardar las medidas: {exc!r}")
            return 0
        return len(filas)

    def cerrar(self) -> None:
        if self.conn is not None:
            with suppress(Exception):  # cerrar una conexión ya rota no es noticia
                self.conn.close()
            self.conn = None

    def _caerse(self, motivo: str) -> None:
        """Anota el motivo y se queda inerte.

        El rollback importa: psycopg deja la transacción abortada tras un fallo, así que sin él la
        conexión ya no serviría ni para cerrarse limpia.
        """
        self.motivo = motivo
        if self.conn is not None:
            with suppress(Exception):
                self.conn.rollback()
        self.cerrar()
