from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta, timezone

from . import repo
from .db import Database
from .models import JobPosting, SearchQuery
from .ranking import RankingProfile
from .sources.base import Source, FetchResult
from .source_health import run_health

log = logging.getLogger("jobbot")


def _store_with_retry(db, posting, score):
    for attempt in range(3):
        try:
            return repo.insert_job_if_new(db, posting, score)
        except Exception as exc:
            # insert_job_if_new revierte la transacción antes de propagar el error.
            if getattr(exc, "sqlstate", None) not in {"40P01", "40001", "55P03"} or attempt == 2:
                raise
            log.warning("Conflicto temporal guardando %s; reintento %d/2", posting.source, attempt + 1)
            time.sleep(.5 * (attempt + 1))


@dataclass
class RunSummary:
    source: str
    found: int
    inserted: int
    error: str | None = None
    status: str = "success"
    details: dict | None = None


def _is_recent(p: JobPosting, max_age_days: int) -> bool:
    if p.posted_at is None:  
        return True
    posted = p.posted_at if p.posted_at.tzinfo else p.posted_at.replace(tzinfo=timezone.utc)
    return posted >= repo.now() - timedelta(days=max_age_days)


def _collect_once(db: Database, sources: list[Source], query: SearchQuery,
            profile: RankingProfile | None = None) -> list[RunSummary]:
    """Ejecuta cada fuente de forma aislada: si una falla, las demás siguen.
    Con un perfil de ranking, cada oferta nueva se guarda ya con su puntaje."""
    summaries: list[RunSummary] = []
    for source in sources:
        started = repo.now()
        found = inserted = 0
        error: str | None = None
        status = "success"
        details = {}
        metrics = {"records_seen": None, "discarded_location": None, "discarded_keywords": None,
                   "discarded_date": 0, "duplicates": 0, "stored": 0, "accepted_after_date": 0}
        try:
            source.prepare(db)
            result = source.fetch(query)
            status = getattr(result, "status", "success")
            details = getattr(result, "details", {})
            metrics.update(details.get("metrics", {}))
            # FetchResult explicitly declares the adapter's coverage contract.
            # Existing bare-list sources remain unverified in the health report.
            details.setdefault("cycle_complete", isinstance(result, FetchResult) and status == "success")
            postings = [p for p in result if _is_recent(p, query.max_age_days)]
            metrics.update(returned=len(result), discarded_date=len(result) - len(postings), accepted_after_date=len(postings))
            # Las consultas de checkpoints no deben retener bloqueos mientras se guarda.
            db.conn.commit()
            found = len(postings)
            for posting in postings:
                score = profile.rank(posting) if profile else None
                if _store_with_retry(db, posting, score):
                    inserted += 1
                else:
                    metrics["duplicates"] += 1
                metrics["stored"] += 1
            source.after_store(db)
        except Exception as exc:  
            db.conn.rollback()
            log.exception("La fuente %s falló", source.name)
            error = f"{type(exc).__name__}: {exc}"
            status = "partial" if inserted else "failed"
        metrics["inserted"] = inserted
        details["metrics"] = metrics
        details["health"] = run_health(status, details, metrics, error)
        repo.record_run(db, source.name, started, repo.now(), found, inserted, error, status=status, details=details)
        summaries.append(RunSummary(source.name, found, inserted, error, status, details))
        log.info("%s [%s]: %d encontradas, %d nuevas%s", source.name, status, found, inserted,
                 f" (ERROR: {error})" if error else "")
        log.info("%s: salud=%s | vistos=%s, ubicación=%s, keywords=%s, fecha=%d, duplicados=%d, pendientes=%s",
                 source.name, details["health"]["outcome"], metrics["records_seen"],
                 metrics["discarded_location"], metrics["discarded_keywords"], metrics["discarded_date"],
                 metrics["duplicates"], details["health"]["pending_pages"])
        if status == "partial":
            visible = {k: v for k, v in details.items() if k not in {"pending_items", "visited_urls", "cursor_key", "page_signatures"}}
            log.warning("%s: cobertura incompleta; detalles: %s", source.name, visible)
    return summaries


def collect(db: Database, sources: list[Source], query: SearchQuery,
            profile: RankingProfile | None = None, *, resume_passes: int = 1) -> list[RunSummary]:
    """Optionally drain safe public checkpoints, without repeating completed sources."""
    summaries = []
    for source in sources:
        total_found = total_inserted = 0
        previous_cursor = None
        for _ in range(max(1, min(resume_passes, 5))):
            summary = _collect_once(db, [source], query, profile)[0]
            total_found += summary.found
            total_inserted += summary.inserted
            details = summary.details or {}
            cursor = repr((details.get("pending_items"), details.get("pending_offset"), details.get("visited_urls")))
            pending = details.get("pending_pages", 0) or details.get("pending_offset") is not None
            if (summary.error or summary.status != "partial" or not details.get("checkpoint_safe")
                or not pending or not (details.get("pages_fetched") or details.get("received"))
                or cursor == previous_cursor):
                break
            previous_cursor = cursor
        summary.found, summary.inserted = total_found, total_inserted
        summaries.append(summary)
    return summaries
