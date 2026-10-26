from __future__ import annotations

from . import example, getonboard 
from .base import REGISTRY, Source


def build_sources(names: list[str]) -> list[Source]:
    from .companies import PLATFORMS, load_companies
    from .greenhouse import GreenhouseSource
    from .smartrecruiters import SmartRecruitersSource

    unknown = [n for n in names if n not in REGISTRY and n not in PLATFORMS]
    if unknown:
        raise ValueError(f"Fuentes desconocidas: {unknown}. Disponibles: {sorted(set(REGISTRY) | PLATFORMS)}")
    companies = load_companies() if set(names) & PLATFORMS else []
    constructors = {"greenhouse": GreenhouseSource, "smartrecruiters": SmartRecruitersSource}
    result = []
    for name in dict.fromkeys(names):
        if name in constructors:
            result.extend(constructors[name](company) for company in companies if company.platform == name)
        else:
            result.append(REGISTRY[name]())
    return result
