from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import JobPosting, SearchQuery

REGISTRY: dict[str, type["Source"]] = {}


class FetchResult(list[JobPosting]):
    """Lista compatible con fuentes existentes, con información de cobertura."""

    def __init__(self, postings=(), *, status: str = "success", details: dict | None = None):
        super().__init__(postings)
        self.status = status
        self.details = details or {}


class Source(ABC):
    """Contrato de toda fuente de ofertas (un archivo por página).

    - Devuelve JobPosting en el formato común.
    - Si algo falla, lanza una excepción: el pipeline la captura, la registra
      y sigue con las demás fuentes.
    """

    name: str  # identificador único, el mismo que se usa en config/search.yml

    def prepare(self, db) -> None:
        """Optional database-backed checkpoint setup before fetching."""

    def after_store(self, db) -> None:
        """Acknowledge fetched data only after every posting was stored successfully."""

    @abstractmethod
    def fetch(self, query: SearchQuery) -> list[JobPosting]: ...


def register(cls: type[Source]) -> type[Source]:
    """Decorador: deja la fuente disponible por nombre."""
    REGISTRY[cls.name] = cls
    return cls