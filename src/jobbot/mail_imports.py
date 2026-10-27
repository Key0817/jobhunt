"""Persistence and public summary of Gmail imports, without exposing message contents."""
from dataclasses import dataclass
import json

from . import repo

# Retain the existing namespace so the OAuth refactor does not re-import read messages.
MESSAGE_NAMESPACE = "1"


@dataclass(frozen=True)
class ImportRecord:
    mailbox_key: str
    message_id: str
    digest: str
    status: str
    job_count: int


def processed_ids(db, mailbox_key, reprocess_review=False):
    rows = db.query("select uid as message_id, status from email_imports where mailbox_key = ? and uid_validity = ?",
                    (mailbox_key, MESSAGE_NAMESPACE))
    db.conn.commit()
    return {row["message_id"] for row in rows if not (reprocess_review and row["status"] in ("needs_review", "ignored"))}


def acknowledge(db, records):
    with db.transaction():
        for record in records:
            db.execute(
                "insert into email_imports (mailbox_key, uid_validity, uid, message_hash, status, job_count, processed_at) "
                "values (?, ?, ?, ?, ?, ?, ?) on conflict (mailbox_key, uid_validity, uid) do update set "
                "message_hash = excluded.message_hash, status = excluded.status, job_count = excluded.job_count, processed_at = excluded.processed_at",
                (record.mailbox_key, MESSAGE_NAMESPACE, record.message_id, record.digest, record.status, record.job_count, repo.now()),
            )


def collection_summary(db):
    condition, params = repo.source_condition("email")
    total = db.query("select count(*) as n from jobs where " + condition, tuple(params))[0]["n"]
    review = db.query("select count(*) as n from email_imports where status = ?", ("needs_review",))[0]["n"]
    rows = db.query("select status, found, inserted, finished_at, details from runs where source = ? order by started_at desc limit 1", ("email_alerts",))
    last = None
    if rows:
        row = rows[0]
        try:
            details = json.loads(row.pop("details") or "{}")
        except (TypeError, ValueError):
            details = {}
        last = {**row, "fetched_messages": details.get("fetched_messages", 0),
                "more_pending": bool(details.get("more_messages_pending")),
                "review_messages": details.get("message_statuses", {}).get("needs_review", 0),
                "read_errors": details.get("read_errors", len(details.get("retry_uids", [])))}
    return {"total_offers": total, "pending_review_messages": review, "last_run": last}
