"""Unauthenticated JSON feed used by Talent Costa Rica's public candidate website."""
import json
from datetime import datetime, timezone

from ..models import JobPosting, normalize
from .base import FetchResult, register
from .companies import country_codes, eligible, keyword_match, parse_date, remote_from_location
from .getonboard import html_to_text
from .public_web import PublicWebSource


@register
class ProcomerSource(PublicWebSource):
    name = "procomer"
    host = "talento.procomer.com"
    max_runtime = 300
    MAX_PAGES = 100

    def prepare(self, db):
        self._resume_page = 1
        rows = db.query("select details from runs where source = ? order by started_at desc limit 20", (self.name,))
        db.conn.commit()
        for row in rows:
            try:
                details = json.loads(row["details"] or "{}")
                if details.get("checkpoint_safe"):
                    self._resume_page = max(1, int(details.get("next_page", 1)))
                    break
            except (TypeError, ValueError):
                continue

    def after_store(self, db):
        # The pipeline records this cursor only after all returned vacancies were saved.
        self._details["checkpoint_safe"] = True

    def fetch(self, query):
        self._start()
        found, failures, received, seen = {}, [], 0, set()
        first_page = getattr(self, "_resume_page", 1)
        next_page = first_page
        for page in range(first_page, first_page + self.MAX_PAGES):
            try:
                payload = json.loads(self._get(f"/api/candidate/jobs?page={page}"))
                listing = payload["data"]["jobs"]
                if not isinstance(listing.get("data"), list):
                    raise ValueError("Listado público de PROCOMER inválido")
            except Exception as error:
                if page == first_page:
                    raise
                failures.append("page:" + type(error).__name__)
                break
            received += len(listing["data"])
            for item in listing["data"]:
                if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                    failures.append("invalid_record")
                    continue
                identifier = str(item["id"])
                if identifier in seen:
                    continue
                seen.add(identifier)
                expiry = parse_date(item.get("published_until"))
                if item.get("external_visibility") != "public" or not item.get("is_published") or item.get("done_hiring") or item.get("deleted_at") or (expiry and (expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc)):
                    continue
                label = ", ".join(str(item[key]) for key in ("distrito", "canton", "provincia", "location_name") if item.get(key))
                codes = country_codes(label)
                if normalize(item.get("provincia") or "") in {"san jose", "alajuela", "heredia", "cartago", "guanacaste", "limon", "puntarenas"}:
                    codes = ["CR"]
                text = "\n\n".join(html_to_text(item.get(key) or "") for key in ("description", "requirements", "desired", "specific_terms", "other"))[:20000]
                posting = JobPosting(source=self.name, external_id=identifier, title=item["name"],
                    company=str(item.get("anonymous_name") or item.get("organization_name") or ""),
                    url=f"https://{self.host}/job/{identifier}", description=text, location=label or None,
                    country=codes[0] if len(codes) == 1 else None,
                    remote=remote_from_location(str(item.get("workplace") or item.get("modalidad") or "")),
                    posted_at=parse_date(item.get("publish_date")), raw={"platform": self.name, "country_codes": codes})
                # Deliberately retain only vacancy fields, never owner/recruiter/application objects.
                if eligible(posting, query) and keyword_match(posting.title, query):
                    found[identifier] = posting
            if not listing["data"] or page >= int(listing.get("last_page") or page):
                next_page = 1
                break
            next_page = page + 1
        else:
            failures.append("page_limit_reached")
        self._details = {"received": received, "eligible": len(found), "failures": failures[:100], "first_page": first_page, "next_page": next_page, "checkpoint_safe": False}
        return FetchResult(found.values(), status="partial" if failures else "success", details=self._details)
