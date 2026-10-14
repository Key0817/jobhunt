from __future__ import annotations

from . import example, getonboard 
from .base import REGISTRY, Source


def build_sources(names: list[str]) -> list[Source]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"Fuentes desconocidas: {unknown}. Disponibles: {sorted(REGISTRY)}")
    return [REGISTRY[n]() for n in names]