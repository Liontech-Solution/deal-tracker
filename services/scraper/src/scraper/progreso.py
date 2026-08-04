"""Latido de progreso de una pasada larga (#146).

Existe porque una pasada de horas era indistinguible de una colgada: el contenedor de la pasada en
frío de Hipercor escribió **0 bytes en 5 horas**, así que de los cuatro intentos fallidos de la §4
de #93 sabemos «más de 300 minutos» y no dónde se fueron.

Vive en su propio módulo y no dentro de `ingest.py` porque lo usan los dos lados de la pasada, y las
tiendas están por debajo de la ingesta: importar de `ingest` desde `stores/` invertiría esa capa.
"""

from __future__ import annotations

import logging
import time

# El reloj, indirecto a propósito: así los tests fijan duraciones sin parchear el módulo `time`
# entero, que es global y afectaría a cualquier otra cosa que corra en el mismo proceso. Mismo
# idiom que `vigia._reloj`, y por la misma razón.
_reloj = time.monotonic


def duracion(segundos: float) -> str:
    """`8112.4` -> `2h15m`; `183.0` -> `3m`; `12.5` -> `12s`."""
    if segundos < 60:
        return f"{segundos:.0f}s"
    minutos, seg = divmod(int(segundos), 60)
    if minutos < 60:
        return f"{minutos}m" if seg < 30 else f"{minutos + 1}m"
    horas, minutos = divmod(minutos, 60)
    return f"{horas}h{minutos:02d}m"


class Latido:
    """Emite como mucho una línea cada `cada_segundos`, por el logger que se le pase.

    Se le pregunta desde dentro de los bucles que ya se están recorriendo — sin hilos ni señales,
    que para esto serían una fuente de problemas nueva a cambio de nada: lo que hay que reportar
    solo cambia cuando avanza el bucle.

    Va por TIEMPO y no por número de vueltas para que el volumen del log no dependa del tamaño del
    catálogo, y de paso sale gratis que una pasada corta no se ensucie: Zara en caliente (1m35s) no
    llega al primer aviso de 5 min.
    """

    def __init__(self, cada_segundos: float, slug: str, log: logging.Logger) -> None:
        self._cada = cada_segundos
        self._slug = slug
        self._log = log
        self.inicio = _reloj()
        self._ultimo = self.inicio

    @property
    def transcurrido(self) -> float:
        return _reloj() - self.inicio

    def anuncia(self, mensaje: str) -> None:
        """Publica sin mirar el reloj: para los hitos (arranque, frontera entre fases)."""
        self._log.info("%s · %s", self._slug, mensaje)

    def late(self, mensaje: str) -> None:
        """Publica solo si ha pasado la ventana desde el último aviso."""
        if self._cada <= 0:
            return
        ahora = _reloj()
        if ahora - self._ultimo < self._cada:
            return
        self._ultimo = ahora
        self._log.info("%s · %s · %s", self._slug, mensaje, duracion(ahora - self.inicio))
