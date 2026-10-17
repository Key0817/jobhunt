from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone

from .db import Database
from .models import STATUSES, JobPosting
from .ranking import RankingProfile, Score


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def _score_columns(score: Score | None) -> tuple[float, str]:
    if score is None:
        return 0.0, "{}"
    return float(score.points), json.dumps(score.to_dict(), ensure_ascii=False)


def _to_datetime(value) -> datetime | None:
    """PostgreSQL devuelve datetime; SQLite, texto ISO."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def insert_job_if_new(db: Database, p: JobPosting, score: Score | None = None) -> bool:
    """Guarda la oferta si no existía (según dedupe_key). Devuelve True si era nueva."""
    ts = now()
    job_id = new_id()
    points, detail = _score_columns(score)
    with db.transaction():
        cur = db.execute(
            """insert into jobs (id, dedupe_key, source, external_id, title, company, country,
                                 location, remote, url, description, posted_at, detected_at,
                                 score, score_detail, raw)
               values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               on conflict (dedupe_key) do nothing""",
            (
                job_id, p.dedupe_key, p.source, p.external_id, p.title, p.company, p.country,
                p.location, p.remote, p.url, p.description, p.posted_at, ts,
                points, detail,
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


def list_jobs(db: Database, status: str | None = None, limit: int = 50,
              sort: str = "score", min_score: float = 0) -> list[dict]:
    """sort='score' (mejores primero) o 'recent' (las últimas detectadas primero)."""
    sql = ("select id, status, score, title, company, country, location, source, url, "
           "posted_at, applied_at from jobs")
    conditions, params = [], []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if min_score:
        conditions.append("score >= ?")
        params.append(min_score)
    if conditions:
        sql += " where " + " and ".join(conditions)
    sql += " order by score desc, detected_at desc" if sort == "score" else " order by detected_at desc"
    sql += " limit ?"
    params.append(limit)
    return db.query(sql, tuple(params))


def get_job(db: Database, job_id: str) -> dict:
    rows = db.query("select * from jobs where id = ?", (job_id,))
    if not rows:
        raise LookupError(f"No existe la oferta {job_id}")
    return rows[0]


def get_job_detail(db: Database, job_id: str) -> dict:
    """La oferta completa, con las razones del puntaje y la modalidad ya interpretadas."""
    job = get_job(db, job_id)
    raw = json.loads(job.get("raw") or "{}")
    detail = json.loads(job.get("score_detail") or "{}")
    job["remote_modality"] = raw.get("remote_modality")
    job["reasons"] = detail.get("reasons", [])
    return job


def get_stats(db: Database, weeks: int = 8) -> dict:
    """Números del dashboard. Las postuladas se cuentan por applied_at (se fija al pasar a
    'solicitada' y no cambia); entrevistas y ofertas, por el historial, para que sigan contando
    aunque la oferta después se rechace."""
    by_status = {s: 0 for s in STATUSES}
    for row in db.query("select status, count(*) as n from jobs group by status"):
        by_status[row["status"]] = int(row["n"])

    applied = int(db.query("select count(*) as n from jobs where applied_at is not null")[0]["n"])
    reached = {row["to_status"]: int(row["n"]) for row in db.query(
        "select to_status, count(distinct job_id) as n from status_history "
        "where to_status in ('entrevista', 'oferta') group by to_status")}
    new_week = int(db.query("select count(*) as n from jobs where detected_at >= ?",
                            (now() - timedelta(days=7),))[0]["n"])

    # Postulaciones por semana (semanas que empiezan el lunes, de la más antigua a la actual).
    today = now().date()
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    counts = {first_monday + timedelta(weeks=i): 0 for i in range(weeks)}
    since = datetime.combine(first_monday, time.min, tzinfo=timezone.utc)
    for row in db.query("select applied_at from jobs where applied_at is not null and applied_at >= ?", (since,)):
        moment = _to_datetime(row["applied_at"])
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        monday = moment.date() - timedelta(days=moment.weekday())
        if monday in counts:
            counts[monday] += 1

    return {
        "total_jobs": sum(by_status.values()),
        "new_last_7_days": new_week,
        "applied": applied,
        "interviews": reached.get("entrevista", 0),
        "offers": reached.get("oferta", 0),
        "by_status": by_status,
        "weekly_applications": [{"week": monday.isoformat(), "count": n} for monday, n in counts.items()],
    }


def rerank_all(db: Database, profile: RankingProfile) -> int:
    """Recalcula el puntaje de todas las ofertas guardadas. Devuelve cuántas actualizó."""
    rows = db.query("select id, source, title, url, description, posted_at from jobs")
    updates = []
    for row in rows:
        posting = JobPosting(source=row["source"], title=row["title"], url=row["url"],
                             description=row["description"], posted_at=_to_datetime(row["posted_at"]))
        points, detail = _score_columns(profile.rank(posting))
        updates.append((points, detail, row["id"]))
    with db.transaction():
        db.executemany("update jobs set score = ?, score_detail = ? where id = ?", updates)
    return len(updates)


def record_run(db: Database, source: str, started: datetime, finished: datetime,
               found: int, inserted: int, error: str | None) -> None:
    with db.transaction():
        db.execute(
            "insert into runs (id, source, started_at, finished_at, found, inserted, error) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), source, started, finished, found, inserted, error),
        )