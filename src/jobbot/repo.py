from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone

from .db import Database
from .models import STATUSES, CV_STATUSES, JobPosting
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


def canonical_job_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gh_src", "lever-source", "lever-origin", "trackingid", "trk"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(sorted(query)), parts.fragment))


def insert_job_if_new(db: Database, p: JobPosting, score: Score | None = None) -> bool:
    """Identifica por fuente/ID, conserva publicaciones distintas y actualiza última vista."""
    ts = now()
    canonical = canonical_job_url(p.url)
    source_key = f"id:{p.external_id}" if p.external_id else f"url:{canonical}"
    points, detail = _score_columns(score)
    inserted = False
    with db.transaction():
        rows = db.query("select job_id from job_sources where source = ? and source_key = ?", (p.source, source_key))
        if not rows:
            # Only merge different feeds automatically when the vacancy URL is identical.
            rows = db.query("select job_id from job_sources where canonical_url = ? limit 1", (canonical,))
        if not rows:
            rows = db.query("select id as job_id from jobs where url = ? limit 1", (p.url,))
        if rows:
            job_id = rows[0]["job_id"]
        else:
            job_id = new_id()
            cur = db.execute(
                "insert into jobs (id, dedupe_key, source, external_id, title, company, country, "
                "location, remote, url, description, posted_at, detected_at, last_seen_at, score, score_detail, raw) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "on conflict (dedupe_key) do nothing",
                (job_id, p.dedupe_key, p.source, p.external_id, p.title, p.company, p.country,
                 p.location, p.remote, p.url, p.description, p.posted_at, ts, ts, points, detail,
                 json.dumps(p.raw, default=str, ensure_ascii=False)),
            )
            inserted = cur.rowcount != 0
            if not inserted:
                job_id = db.query("select id from jobs where dedupe_key = ?", (p.dedupe_key,))[0]["id"]
            else:
                db.execute(
                    "insert into status_history (id, job_id, from_status, to_status, changed_at) values (?, ?, ?, ?, ?)",
                    (new_id(), job_id, None, "encontrada", ts),
                )
        db.execute("update jobs set last_seen_at = ? where id = ?", (ts, job_id))
        db.execute(
            "insert into job_sources (source, source_key, job_id, external_id, url, canonical_url, first_seen_at, last_seen_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?) on conflict (source, source_key) do update set "
            "url = excluded.url, canonical_url = excluded.canonical_url, last_seen_at = excluded.last_seen_at",
            (p.source, source_key, job_id, p.external_id, p.url, canonical, ts, ts),
        )
    return inserted


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
               found: int, inserted: int, error: str | None,
               status: str | None = None, details: dict | None = None) -> None:
    with db.transaction():
        db.execute(
            "insert into runs (id, source, started_at, finished_at, found, inserted, error, status, details) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), source, started, finished, found, inserted, error,
             status or ("failed" if error else "success"), json.dumps(details or {}, ensure_ascii=False)),
        )
        

def save_cv_version(
    db: Database,
    job_id: str,
    variant: str,
    profile_text: str,
    file_path: str,
    profile_key: str,
    model_name: str | None = None,
) -> dict:
    """Guarda una versión nueva del CV pendiente de revisión."""
    # Verifica que la oferta exista antes de guardar el CV.
    get_job(db, job_id)

    version_id = new_id()
    created_at = now()

    with db.transaction():
        db.execute(
            """
            insert into cv_versions (
                id, job_id, variant, profile_text, approved, created_at,
                profile_key, file_path, status, model_name, approved_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                job_id,
                variant,
                profile_text,
                False,
                created_at,
                profile_key,
                file_path,
                "pending_review",
                model_name,
                None,
            ),
        )

    return get_cv_version(db, version_id)


def get_cv_version(db: Database, version_id: str) -> dict:
    """Recupera una versión del CV por su identificador."""
    rows = db.query(
        "select * from cv_versions where id = ?",
        (version_id,),
    )
    if not rows:
        raise LookupError(f"No existe la versión de CV {version_id}")
    return rows[0]


def list_cv_versions(db: Database, job_id: str) -> list[dict]:
    """Lista las versiones de CV de una oferta, de la más reciente a la más antigua."""
    get_job(db, job_id)
    return db.query(
        """
        select *
        from cv_versions
        where job_id = ?
        order by created_at desc
        """,
        (job_id,),
    )


def list_all_cv_versions(db: Database) -> list[dict]:
    """Lista todas las versiones de CV con información de su oferta asociada."""
    return db.query(
        """
        select
            cv.id,
            cv.job_id,
            cv.variant,
            cv.profile_key,
            cv.status,
            cv.approved,
            cv.model_name,
            cv.created_at,
            j.title as job_title,
            j.company
        from cv_versions cv
        inner join jobs j on j.id = cv.job_id
        order by cv.created_at desc
        """
    )


def get_cv_version_detail(db: Database, version_id: str) -> dict:
    """Obtiene una versión de CV junto con la oferta asociada."""
    rows = db.query(
        """
        select
            cv.id,
            cv.job_id,
            cv.variant,
            cv.profile_text,
            cv.cover_letter,
            cv.cover_letter_model_name,
            cv.file_path,
            cv.profile_key,
            cv.status,
            cv.approved,
            cv.model_name,
            cv.created_at,
            j.title as job_title,
            j.company
        from cv_versions cv
        inner join jobs j on j.id = cv.job_id
        where cv.id = ?
        """,
        (version_id,),
    )

    if not rows:
        raise LookupError(f"No existe la versión de CV {version_id}")

    return rows[0]

def save_cover_letter(db: Database, version_id: str, text: str, model_name: str) -> dict:
    """Guarda la carta en la versión del CV sin cambiar su perfil o aprobación."""
    with db.transaction():
        updated = db.execute(
            "update cv_versions set cover_letter = ?, cover_letter_model_name = ? where id = ?",
            (text, model_name, version_id),
        )
        if updated.rowcount == 0:
            raise LookupError(f"No existe la versión de CV {version_id}")
    return get_cv_version_detail(db, version_id)


def change_cv_status(db: Database, version_id: str, status: str) -> dict:
    """Mantiene estado, aprobación y fecha de aprobación coherentes."""
    if status not in CV_STATUSES:
        raise ValueError("Estado de CV no válido.")
    approved = status == "approved"
    with db.transaction():
        result = db.execute(
            "update cv_versions set status = ?, approved = ?, "
            "approved_at = case when ? then coalesce(approved_at, ?) else null end "
            "where id = ?",
            (status, approved, approved, now() if approved else None, version_id),
        )
        if result.rowcount == 0:
            raise LookupError(f"No existe la versión de CV {version_id}")
    return get_cv_version(db, version_id)
