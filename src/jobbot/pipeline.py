from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, timezone

from . import repo
from .db import Database
from .models import JobPosting, SearchQuery
from .ranking import RankingProfile
from .sources.base import Source

log = logging.getLogger("jobbot")


@dataclass
class RunSummary:
    source: str
    found: int
    inserted: int
    error: str | None = None


def _is_recent(p: JobPosting, max_age_days: int) -> bool:
    if p.posted_at is None:  
        return True
    posted = p.posted_at if p.posted_at.tzinfo else p.posted_at.replace(tzinfo=timezone.utc)
    return posted >= repo.now() - timedelta(days=max_age_days)


def collect(db: Database, sources: list[Source], query: SearchQuery,
            profile: RankingProfile | None = None) -> list[RunSummary]:
    """Ejecuta cada fuente de forma aislada: si una falla, las demás siguen.
    Con un perfil de ranking, cada oferta nueva se guarda ya con su puntaje."""
    summaries: list[RunSummary] = []
    for source in sources:
        started = repo.now()
        found = inserted = 0
        error: str | None = None
        try:
            postings = [p for p in source.fetch(query) if _is_recent(p, query.max_age_days)]
            found = len(postings)
            for posting in postings:
                score = profile.rank(posting) if profile else None
                if repo.insert_job_if_new(db, posting, score):
                    inserted += 1
        except Exception as exc:  
            log.exception("La fuente %s falló", source.name)
            error = f"{type(exc).__name__}: {exc}"
        repo.record_run(db, source.name, started, repo.now(), found, inserted, error)
        summaries.append(RunSummary(source.name, found, inserted, error))
        log.info("%s: %d encontradas, %d nuevas%s", source.name, found, inserted,
                 f" (ERROR: {error})" if error else "")
    return summaries