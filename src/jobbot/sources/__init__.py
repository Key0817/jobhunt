from __future__ import annotations

from . import example, getonboard, email_alerts, dynamitejobs, procomer
from .base import REGISTRY, Source


def build_sources(names: list[str], db=None) -> list[Source]:
    from .email_alerts import mail_enabled
    import logging
    from .companies import PLATFORMS, load_companies
    from .greenhouse import GreenhouseSource
    from .smartrecruiters import SmartRecruitersSource
    from .lever import LeverSource
    from .ashby import AshbySource
    from .workday import WorkdaySource
    from .career_feeds import CareerFeedSource
    from .public_company import PublicCompanySource
    import hashlib
    shards = {f"public_web_{index}" for index in range(8)}

    unknown = [n for n in names if n not in REGISTRY and n not in PLATFORMS and n not in shards]
    if unknown:
        raise ValueError(f"Fuentes desconocidas: {unknown}. Disponibles: {sorted(set(REGISTRY) | PLATFORMS)}")
    companies = load_companies() if set(names) & (PLATFORMS | shards) else []
    constructors = {"greenhouse": GreenhouseSource, "smartrecruiters": SmartRecruitersSource,
                    "lever": LeverSource, "ashby": AshbySource, "workday": WorkdaySource, "public_web": PublicCompanySource,
                    **{platform: CareerFeedSource for platform in ("workable", "bamboohr", "breezy", "recruitee")}}
    result = []
    for name in dict.fromkeys(names):
        if name == "email_alerts" and not mail_enabled(db):
            if len(names) == 1:
                raise ValueError("Conecta Gmail desde Ajustes para importar las alertas.")
            logging.getLogger(__name__).info("Correo desactivado: conecta Gmail desde Ajustes.")
            continue
        if name in shards:
            index = int(name.rsplit("_", 1)[1])
            result.extend(PublicCompanySource(company) for company in companies if company.platform == "public_web" and int(hashlib.sha256(company.id.encode()).hexdigest(), 16) % 8 == index)
        elif name in constructors:
            result.extend(constructors[name](company) for company in companies if company.platform == name)
        else:
            result.append(REGISTRY[name]())
    return result
