from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import Database
from .models import STATUSES, JobPosting


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def insert_job_if_new(db: Database, p: JobPosting) -> bool:
    """Guarda la oferta si no existía (según dedupe_key). Devuelve True si era nueva."""
    ts = now()
    job_id = new_id()
    with db.transaction():
        cur = db.execute(
            """insert into jobs (id, dedupe_key, source, external_id, title, company, country,
                                 location, remote, url, description, posted_at, detected_at, raw)
               values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               on conflict (dedupe_key) do nothing""",
            (
                job_id, p.dedupe_key, p.source, p.external_id, p.title, p.company, p.country,
                p.location, p.remote, p.url, p.description, p.posted_at, ts,
                json.dumps(p.raw, default=str, ensure_ascii=False),
            ),
        )
        if cur.rowcount == 0:
            return False
        db.execute(
            "insert into status_history (id, job_id, from_status, to_status, changed_at) "
            "values (?, ?, ?, ?, ?)",
            (new_id(), job_id, None, "encontrada", ts),
        )
    return True


def change_status(db: Database, job_id: str, new_status: str) -> None:
    if new_status not in STATUSES:
        raise ValueError(f"Estado inválido: {new_status!r}. Válidos: {', '.join(STATUSES)}")
    rows = db.query("select status from jobs where id = ?", (job_id,))
    if not rows:
        raise LookupError(f"No existe la oferta {job_id}")
    old = rows[0]["status"]
    if old == new_status:
        return
    ts = now()
    with db.transaction():
        if new_status == "solicitada":
            db.execute(
                "update jobs set status = ?, applied_at = coalesce(applied_at, ?) where id = ?",
                (new_status, ts, job_id),
            )
        else:
            db.execute("update jobs set status = ? where id = ?", (new_status, job_id))
        db.execute(
            "insert into status_history (id, job_id, from_status, to_status, changed_at) "
            "values (?, ?, ?, ?, ?)",
            (new_id(), job_id, old, new_status, ts),
        )


def resolve_job_id(db: Database, prefix: str) -> str:
    """Permite usar solo el inicio del id en la CLI (basta con que sea único)."""
    rows = db.query("select id from jobs where id like ?", (prefix + "%",))
    if len(rows) == 1:
        return rows[0]["id"]
    raise LookupError(f"El id {prefix!r} coincide con {len(rows)} ofertas")


def list_jobs(db: Database, status: str | None = None, limit: int = 50) -> list[dict]:
    sql = "select id, status, score, title, company, country, source, posted_at from jobs"
    params: list = []
    if status:
        sql += " where status = ?"
        params.append(status)
    sql += " order by detected_at desc limit ?"
    params.append(limit)
    return db.query(sql, tuple(params))


def record_run(db: Database, source: str, started: datetime, finished: datetime,
               found: int, inserted: int, error: str | None) -> None:
    with db.transaction():
        db.execute(
            "insert into runs (id, source, started_at, finished_at, found, inserted, error) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), source, started, finished, found, inserted, error),
        )