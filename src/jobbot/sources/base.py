from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import JobPosting, SearchQuery

REGISTRY: dict[str, type["Source"]] = {}


class Source(ABC):
    """Contrato de toda fuente de ofertas (un archivo por página).

    - Devuelve JobPosting en el formato común.
    - Si algo falla, lanza una excepción: el pipeline la captura, la registra
      y sigue con las demás fuentes.
    """

    name: str  # identificador único, el mismo que se usa en config/search.yml

    @abstractmethod
    def fetch(self, query: SearchQuery) -> list[JobPosting]: ...


def register(cls: type[Source]) -> type[Source]:
    """Decorador: deja la fuente disponible por nombre."""
    REGISTRY[cls.name] = cls
    return cls