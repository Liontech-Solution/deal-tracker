"""Registro de scrapers por slug. Añadir una tienda = añadir una entrada aquí."""

from __future__ import annotations

from collections.abc import Callable

from ..config import Config
from .base import BaseStore
from .c_and_a import CAndAStore
from .cacles import CaclesStore
from .deditos import DeditosStore
from .hipercor import HipercorStore
from .hm import HMStore
from .lefties import LeftiesStore
from .mango import MangoStore
from .sfera import SferaStore
from .springfield import SpringfieldStore
from .zara import ZaraStore

# slug -> factoría que construye el scraper con la config.
_STORES: dict[str, Callable[[Config], BaseStore]] = {
    ZaraStore.slug: ZaraStore,
    SferaStore.slug: SferaStore,
    LeftiesStore.slug: LeftiesStore,
    CaclesStore.slug: CaclesStore,
    CAndAStore.slug: CAndAStore,
    HipercorStore.slug: HipercorStore,
    HMStore.slug: HMStore,
    MangoStore.slug: MangoStore,
    SpringfieldStore.slug: SpringfieldStore,
    DeditosStore.slug: DeditosStore,
}


def available_slugs() -> list[str]:
    return sorted(_STORES)


def get_store(slug: str, config: Config) -> BaseStore:
    try:
        factory = _STORES[slug]
    except KeyError:
        raise ValueError(
            f"Tienda desconocida: {slug!r}. Disponibles: {', '.join(available_slugs())}"
        ) from None
    return factory(config)
