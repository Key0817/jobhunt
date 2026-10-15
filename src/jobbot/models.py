from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

# Orden del flujo (columnas del tablero). Debe coincidir con el CHECK de schema.sql.
STATUSES = (
    "encontrada",
    "aplicable",
    "cv_listo",
    "solicitada",
    "entrevista",
    "oferta",
    "rechazada",
    "descartada",
)


def normalize(text: str | None) -> str:
    """Minúsculas, sin tildes ni signos: 'Desarrollador/a Sr.' -> 'desarrollador a sr'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass(frozen=True)
class SearchQuery:
    keywords: tuple[str, ...]
    countries: tuple[str, ...]
    include_remote_latam: bool = True
    max_age_days: int = 14
    residence_country: str = "CR"


@dataclass(frozen=True)
class JobPosting:
    """Formato común: todas las fuentes devuelven ofertas con esta forma."""

    source: str
    title: str
    url: str
    company: str = ""
    external_id: str | None = None
    country: str | None = None  
    location: str | None = None
    remote: bool | None = None
    description: str = ""
    posted_at: datetime | None = None
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def dedupe_key(self) -> str:
        """Misma empresa + mismo título + mismo país = misma oferta, venga de la fuente que venga."""
        base = f"{normalize(self.company)}|{normalize(self.title)}|{(self.country or '').upper()}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()