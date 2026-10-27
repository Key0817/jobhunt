from __future__ import annotations

from . import example, getonboard, email_alerts
from .base import REGISTRY, Source


def build_sources(names: list[str], db=None) -> list[Source]:
    from .email_alerts import mail_enabled
    import logging
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
        if name == "email_alerts" and not mail_enabled(db):
            if len(names) == 1:
                raise ValueError("Conecta Gmail desde Ajustes para importar las alertas.")
            logging.getLogger(__name__).info("Correo desactivado: conecta Gmail desde Ajustes.")
            continue
        if name in constructors:
            result.extend(constructors[name](company) for company in companies if company.platform == name)
        else:
            result.append(REGISTRY[name]())
    return result
