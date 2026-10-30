"""Small, persisted diagnostics; unknown upstream counts stay unknown, never zero."""
import json
from pathlib import Path


def github_summary(summaries, path):
    """Use only bounded, non-personal diagnostic fields in the Actions summary."""
    lines = ["### Salud de las fuentes", "", "Los contadores desconocidos se muestran como `?`. Las métricas son de la última pasada; nuevas suma las pasadas.", "",
             "| Fuente | Estado | Diagnóstico | Vistos | Ubicación | Keywords | Fecha | Duplicados | Nuevas | Pendientes |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for summary in summaries:
        details = summary.details or {}
        metrics, health = details.get("metrics", {}), details.get("health", {})
        values = [summary.source, summary.status, health.get("outcome"), metrics.get("records_seen"),
                  metrics.get("discarded_location"), metrics.get("discarded_keywords"), metrics.get("discarded_date"),
                  metrics.get("duplicates"), summary.inserted, health.get("pending_pages")]
        lines.append("| " + " | ".join(str(v).replace("|", " ").replace("\n", " ") if v is not None else "?" for v in values) + " |")
    with Path(path).open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def run_health(status, details, metrics, error=None):
    failures = details.get("failures", [])
    pending = details.get("pending_pages")
    if pending is None and details.get("pending_offset") is not None:
        pending = 1  # A pending API page, not an estimate of all remaining pages.
    complete = status == "success" and not error and details.get("cycle_complete", False)
    if error or status == "failed":
        outcome = "failed"
    elif complete:
        outcome = "collected" if metrics["accepted_after_date"] else "no_matches"
    elif pending:
        outcome = "pending_pages"
    elif "no_readable_job_schema" in failures:
        outcome = "unreadable"
    elif details.get("review_category") == "access_or_network":
        outcome = "access_error"
    else:
        outcome = "incomplete" if status == "partial" else "unverified"
    return {"cycle_complete": complete, "outcome": outcome, "pending_pages": pending}


def source_health(db, prefix=None):
    """Latest attempts and last proven complete cycle, without leaking crawl queues."""
    where, params = (" where source = ? or source like ?", (prefix, prefix + ":%")) if prefix else ("", ())
    order = "finished_at desc, started_at desc" + (", rowid desc" if db.is_sqlite else ", id desc")
    latest = db.query("select * from (select source, started_at, finished_at, status, found, inserted, error, details, "
                      f"row_number() over (partition by source order by {order}) as position "
                      "from runs" + where + ") ranked where position = 1 order by source", params)
    proof = "json_extract(details, '$.health.cycle_complete') = 1" if db.is_sqlite else "details::jsonb -> 'health' ->> 'cycle_complete' = 'true'"
    complete = {row["source"]: row["finished_at"] for row in db.query(
        "select source, max(finished_at) as finished_at from runs where status = 'success' and error is null and "
        + proof + " group by source")}
    results = []
    for row in latest:
        details = json.loads(row.pop("details") or "{}")
        row.update(health=details.get("health", {"cycle_complete": False, "outcome": "historical_unverified"}),
                   metrics=details.get("metrics", {}), failures=details.get("failures", [])[:10],
                   last_complete_cycle=complete.get(row["source"]),
                   review_category=details.get("review_category"))
        # Errors may contain URLs or provider text; keep this report focused on categories.
        row.pop("error", None)
        results.append(row)
    return results
