"""Lever's documented public Postings API (no credentials or applications)."""
from urllib.parse import urlencode
import hashlib
import json

from ..models import JobPosting
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, remote_from_location
from .getonboard import html_to_text


class LeverSource(CompanySource):
    PAGE_SIZE = 100
    MAX_PAGES = 20

    def prepare(self, db):
        order = "finished_at desc, started_at desc" + (", rowid desc" if db.is_sqlite else ", id desc")
        rows = db.query(f"select details from runs where source = ? order by {order} limit 1", (self.name,))
        db.conn.commit()
        self._previous = json.loads(rows[0]['details'] or '{}') if rows else {}

    def after_store(self, db):
        self._details['checkpoint_safe'] = True

    def fetch(self, query):
        self._start()
        domain = "api.eu.lever.co" if self.company.api_region == "eu" else "api.lever.co"
        root = f"https://{domain}/v0/postings/{self.company.board_id}"
        found, failures, received = {}, [], 0
        key = hashlib.sha256(repr((root, query, self.PAGE_SIZE, 1)).encode()).hexdigest()
        old = getattr(self, '_previous', {})
        start = old.get('pending_offset') if old.get('checkpoint_safe') and old.get('cursor_key') == key else None
        start = start if isinstance(start, int) and start >= 0 else 0
        pending = None
        gaps = list(old.get('coverage_gaps', [])) if start else []
        signatures = set(old.get('page_signatures', [])) if start else set()
        for offset in range(start, start + self.MAX_PAGES * self.PAGE_SIZE, self.PAGE_SIZE):
            try:
                items = self._get(root + "?" + urlencode({"mode": "json", "skip": offset, "limit": self.PAGE_SIZE}))
                if not isinstance(items, list):
                    raise ValueError("Lever devolvió un listado inválido")
            except Exception as error:
                if offset == 0:
                    raise
                failures.append(type(error).__name__)
                pending = offset
                break
            received += len(items)
            signature = hashlib.sha256(repr(sorted(str(item.get('id')) for item in items if isinstance(item, dict))).encode()).hexdigest()
            if items and signature in signatures:
                failures.append('pagination_not_advancing')
                pending = offset
                break
            if items:
                signatures.add(signature)
            for item in items:
                try:
                    if not item.get("id") or not item.get("text") or not str(item.get("hostedUrl", "")).startswith("https://"):
                        raise ValueError("invalid_record")
                    categories = item.get("categories") or {}
                    locations = categories.get("allLocations") or [categories.get("location", "")]
                    label = ", ".join(str(value) for value in locations if value)
                    codes = country_codes(label)
                    sections = [item.get("descriptionPlain") or html_to_text(item.get("description") or "")]
                    sections.extend(str(section.get("text", "")) + "\n" + html_to_text(section.get("content") or "") for section in item.get("lists", []) if isinstance(section, dict))
                    sections.append(item.get("additionalPlain") or html_to_text(item.get("additional") or ""))
                    posting = JobPosting(source=self.name, external_id=str(item["id"]), title=item["text"], company=self.company.name,
                        url=item["hostedUrl"], description="\n\n".join(sections)[:20000], location=label or None,
                        country=codes[0] if len(codes) == 1 else None,
                        remote=False if item.get("workplaceType") in ("hybrid", "on-site") else True if item.get("workplaceType") == "remote" else remote_from_location(label),
                        raw={"platform": "lever", "country_codes": codes})
                    if eligible(posting, query) and keyword_match(posting.title, query):
                        found[posting.external_id] = posting
                except (ValueError, TypeError, AttributeError):
                    failures.append("invalid_record")
                    gaps.append('invalid_record')
            if len(items) < self.PAGE_SIZE:
                break
        else:
            failures.append("page_limit_reached")
            pending = offset + self.PAGE_SIZE
        failures = list(dict.fromkeys(failures + gaps))
        self._details = {"received": received, "eligible": len(found), "failures": failures[:100],
                         "cursor_key": key, "pending_offset": pending, "resumed": start > 0,
                         "cycle_complete": not failures, "coverage_gaps": list(set(gaps)),
                         "page_signatures": sorted(signatures)[-1000:] if pending is not None else []}
        return FetchResult(found.values(), status="partial" if failures else "success", details=self._details)
