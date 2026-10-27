"""Import career links and verify public boards: python -m jobbot.catalog --help."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, parse_qs, urlencode
import hashlib

import yaml

from .sources.companies import Company
from .sources.greenhouse import GreenhouseSource


def canonical_careers_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    return urlunsplit(("https", host, path, urlencode(sorted(params)), ""))


def managed_source(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host in ("elempleo.com", "cr.computrabajo.com", "trabajosdiarios.co.cr", "linkedin.com", "indeed.com"):
        return "email_alerts"
    return {"dynamitejobs.com": "dynamitejobs", "talento.procomer.com": "procomer", "getonbrd.com": "getonboard"}.get(host)


def company_from_url(name, url, allow_public=False):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return None
    segments = parts.path.strip("/").split("/")
    region = "eu" if host in ("job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io", "jobs.eu.lever.co") else "us"
    platform, board = None, segments[0]
    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"):
        platform = "greenhouse"
        if board == "embed":
            board = (parse_qs(parts.query).get("for") or [""])[0]
    elif host == "careers.smartrecruiters.com":
        platform = "smartrecruiters"
    elif host in ("jobs.lever.co", "jobs.eu.lever.co"):
        platform = "lever"
    elif host == "jobs.ashbyhq.com":
        platform = "ashby"
    elif re.fullmatch(r"[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com", host):
        platform = "workday"
        # Some links include a locale before the public board name.
        board = segments[1] if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segments[0]) and len(segments) > 1 else segments[0]
    elif host == "apply.workable.com" and board not in ("api", "j", ""):
        platform = "workable"
    elif re.fullmatch(r"[a-z0-9-]+\.bamboohr\.com", host):
        platform, board = "bamboohr", host.split(".")[0]
    elif re.fullmatch(r"[a-z0-9-]+\.breezy\.hr", host):
        platform, board = "breezy", host.split(".")[0]
    elif re.fullmatch(r"[a-z0-9-]+\.recruitee\.com", host):
        platform, board = "recruitee", host.split(".")[0]
    elif allow_public and host and "." in host and host not in ("localhost", "127.0.0.1"):
        platform = "public_web"
        url = canonical_careers_url(url)
        board = hashlib.sha256(url.encode()).hexdigest()[:20]
    if not platform or not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        return None
    identifier = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or board.lower()
    return Company(identifier, name, platform, board, url.split("#")[0], api_region=region)


def board_key(row):
    return row["platform"], row["board_id"], (urlsplit(row["careers_url"]).hostname if row["platform"] == "workday" else row.get("api_region", "us"))


def import_links(text, path="config/companies.yml", pending_path="config/company_pending.yml"):
    file = Path(path)
    data = yaml.safe_load(file.read_text(encoding="utf-8")) if file.exists() else {"companies": []}
    rows = data["companies"]
    boards = {board_key(row) for row in rows}
    ids = {row["id"] for row in rows}
    pending_file = Path(pending_path)
    pending = yaml.safe_load(pending_file.read_text(encoding="utf-8")) if pending_file.exists() else {"companies": []}
    pending_urls = {row["url"].rstrip("/") for row in pending["companies"]}
    added = 0
    for line in text.splitlines():
        match = re.match(r"\|\s*(.+?)\s*\|\s*\[[^\]]*\]\((https?://[^)\s]+)\)", line)
        if not match:
            continue
        name, url = match.groups()
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        name = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", name).replace("**", "").strip()
        company = company_from_url(name, url, allow_public=True)
        if company:
            row = asdict(company)
            if board_key(row) in boards:
                continue
            if row["id"] in ids:
                row["id"] += "-" + row["platform"] + "-" + row["board_id"].lower()
            row["enabled"] = False
            row["verification_status"] = "pending"
            row["managed_source"] = managed_source(url)
            rows.append(row)
            boards.add(board_key(row))
            ids.add(row["id"])
            added += 1
        elif url.rstrip("/") not in pending_urls:
            pending["companies"].append({"name": name, "url": url, "integration_status": "requires_adapter_review"})
            pending_urls.add(url.rstrip("/"))
    file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if pending["companies"]:
        pending_file.write_text(yaml.safe_dump(pending, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"added": added, "catalog_boards": len(rows), "pending_custom_sites": len(pending["companies"])}


def integrate_pending(path="config/companies.yml", pending_path="config/company_pending.yml"):
    file, pending_file = Path(path), Path(pending_path)
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    pending = (yaml.safe_load(pending_file.read_text(encoding="utf-8")) or {"companies": []}) if pending_file.exists() else {"companies": []}
    by_key = {board_key(row): row for row in data["companies"]}
    ids = {row["id"] for row in data["companies"]}
    added = merged = 0
    for entry in pending.get("companies", []):
        url = entry["url"]
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        company = company_from_url(entry["name"], url, allow_public=True)
        if not company:
            raise ValueError("Enlace del catálogo pendiente inválido: " + entry["name"])
        row = asdict(company)
        key = board_key(row)
        if key in by_key:
            existing = by_key[key]
            aliases = existing.setdefault("aliases", [])
            if entry["name"] != existing["name"] and entry["name"] not in aliases:
                aliases.append(entry["name"])
            merged += 1
            continue
        while row["id"] in ids:
            row["id"] += "-" + row["board_id"].lower()[:12]
        row.update(enabled=False, verification_status="pending", legacy_integration_status=entry.get("integration_status"), managed_source=managed_source(url))
        if (urlsplit(url).hostname or "").removeprefix("www.") in ("ane.cr", "brete.cr"):
            row["verification_status"] = "requires_account"
        if (urlsplit(url).hostname or "").removeprefix("www.") in ("github.com", "media1.tenor.com"):
            row["verification_status"] = "reference_only"
        data["companies"].append(row)
        by_key[key] = row
        ids.add(row["id"])
        added += 1
    file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if pending_file.exists():
        pending_file.write_text(yaml.safe_dump({"migrated_to": str(path), "companies": []}, sort_keys=False), encoding="utf-8")
    return {"added": added, "duplicates_merged": merged, "catalog_boards": len(data["companies"])}


def verify_board(row):
    fields = {key: row[key] for key in ("id", "name", "platform", "board_id", "careers_url")}
    company = Company(**fields, api_region=row.get("api_region", "us"))
    client = GreenhouseSource(company, max_runtime=15)
    client._start()
    board, platform = company.board_id, company.platform
    try:
        if row.get("managed_source"):
            return {"id": company.id, "status": "managed_source", "managed_source": row["managed_source"]}
        if row.get("verification_status") in ("requires_account", "reference_only"):
            return {"id": company.id, "status": row["verification_status"]}
        if platform == "public_web":
            from .sources.public_company import PublicCompanySource
            from .sources.dynamitejobs import job_schema
            source = PublicCompanySource(company)
            source.max_runtime = 12
            source._start()
            page = source._get(company.careers_url)
            if "<html" not in page.lower() and "<body" not in page.lower():
                return {"id": company.id, "status": "invalid_response"}
            return {"id": company.id, "status": "verified_public_access", "job_schemas_on_entry": len(list(job_schema(page)))}
        if platform in ("workable", "bamboohr", "breezy", "recruitee"):
            from .sources.career_feeds import CareerFeedSource
            source = CareerFeedSource(company)
            source.max_runtime = 15
            source._start()
            items = source.items(json.loads(source._get(source.feed_path())))
            return {"id": company.id, "status": "verified", "received": len(items)}
        if platform == "greenhouse":
            host = "boards-api.greenhouse.io"
            payload = client._get(f"https://{host}/v1/boards/{board}")
            valid = isinstance(payload, dict) and isinstance(payload.get("name"), str)
        elif platform == "lever":
            host = "api.eu.lever.co" if company.api_region == "eu" else "api.lever.co"
            payload = client._get(f"https://{host}/v0/postings/{board}?mode=json&limit=1")
            valid = isinstance(payload, list)
        elif platform == "smartrecruiters":
            payload = client._get(f"https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=1")
            valid = isinstance(payload, dict) and isinstance(payload.get("content"), list)
        elif platform == "ashby":
            payload = client._get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
            valid = isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
        elif platform == "workday":
            host = urlsplit(company.careers_url).hostname
            payload = client._post(f"https://{host}/wday/cxs/{host.split('.')[0]}/{board}/jobs", {"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}})
            valid = isinstance(payload, dict) and isinstance(payload.get("jobPostings"), list)
        else:
            valid = False
        return {"id": company.id, "status": "verified" if valid else "invalid_response"}
    except PermissionError:
        return {"id": company.id, "status": "robots_blocked"}
    except HTTPError as error:
        return {"id": company.id, "status": "not_found" if error.code in (404, 410) else "http_error", "http_status": error.code}
    except Exception as error:
        return {"id": company.id, "status": "unavailable", "error_type": type(error).__name__}


def apply_verification(row, result):
    row["verification_status"] = result["status"]
    temporary = (result["status"] == "unavailable" and result.get("error_type") in
                 ("TimeoutError", "URLError", "IncompleteRead", "ConnectionError")) or (
                 result["status"] == "http_error" and (result.get("http_status") == 429 or result.get("http_status", 0) >= 500))
    row["retry_pending"] = bool(temporary)
    if result["status"] in ("verified", "verified_public_access") or temporary:
        row["enabled"] = True
    elif result["status"] in ("not_found", "invalid_response", "robots_blocked", "managed_source", "requires_account", "reference_only") or result.get("http_status") in (401, 403, 406, 422) or result.get("error_type") == "ValueError":
        row["enabled"] = False


def verify_catalog(path="config/companies.yml", report="config/company_validation.json"):
    file = Path(path)
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    rows = data["companies"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(verify_board, rows))
    checked_at = datetime.now(timezone.utc).isoformat()
    by_id = {result["id"]: result for result in results}
    for row in rows:
        result = by_id[row["id"]]
        apply_verification(row, result)
        row["checked_at"] = checked_at
    file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    Path(report).write_text(json.dumps({"checked_at": checked_at, "boards": results}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"boards": len(rows), "enabled": sum(row.get("enabled", False) for row in rows), "verified": sum(result["status"] == "verified" for result in results)}


def catalog_status(path="config/companies.yml"):
    from collections import Counter
    rows = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["companies"]
    return {"total": len(rows), "enabled": sum(bool(row.get("enabled")) for row in rows),
            "retry_pending": sum(bool(row.get("retry_pending")) for row in rows),
            "enabled_by_platform": dict(Counter(row["platform"] for row in rows if row.get("enabled"))),
            "disabled_by_reason": dict(Counter(row.get("verification_status", "pending") for row in rows if not row.get("enabled")))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="Importa una tabla Markdown con empresas y enlaces")
    importer.add_argument("file")
    sub.add_parser("status", help="Muestra cobertura y motivos de desactivación sin consultar la red")
    sub.add_parser("integrate-pending", help="Consolida todos los enlaces pendientes en companies.yml")
    sub.add_parser("verify", help="Verifica y activa tableros públicos; no accede a Supabase")
    args = parser.parse_args()
    result = import_links(Path(args.file).read_text(encoding="utf-8")) if args.command == "import" else integrate_pending() if args.command == "integrate-pending" else catalog_status() if args.command == "status" else verify_catalog()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
