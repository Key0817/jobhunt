"""Fuente de PRUEBA con datos ficticios. Sirve para verificar el pipeline de punta a punta."""
from __future__ import annotations

from datetime import timedelta

from ..models import JobPosting, SearchQuery
from ..repo import now
from .base import Source, register


@register
class ExampleSource(Source):
    name = "example"

    def fetch(self, query: SearchQuery) -> list[JobPosting]:
        today = now()
        return [
            JobPosting(
                source=self.name, external_id="ex-1", title="Desarrollador Backend Python",
                company="Acme CR", country="CR", location="San José", remote=False,
                url="https://example.com/jobs/1", posted_at=today - timedelta(days=1),
                description="API REST con Python, FastAPI y PostgreSQL.",
            ),
            JobPosting(
                source=self.name, external_id="ex-2", title="Analista de Soporte Técnico",
                company="Soluciones TI", country="CR", location="Heredia", remote=False,
                url="https://example.com/jobs/2", posted_at=today - timedelta(days=3),
                description="Atención de incidentes, Windows, redes básicas.",
            ),
            # Misma oferta que la primera, con distinto formato: debe deduplicarse.
            JobPosting(
                source=self.name, external_id="ex-1", title="desarrollador backend  python",
                company="ACME CR", country="CR", url="https://example.com/jobs/1",
                posted_at=today - timedelta(days=1),
            ),
            # Demasiado antigua: el filtro de antigüedad debe descartarla.
            JobPosting(
                source=self.name, external_id="ex-4", title="Programador Java",
                company="Vieja Corp", country="MX", url="https://example.com/jobs/4",
                posted_at=today - timedelta(days=90),
            ),
        ]