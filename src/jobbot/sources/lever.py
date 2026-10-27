"""Lever's documented public Postings API (no credentials or applications)."""
from urllib.parse import urlencode

from ..models import JobPosting
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, remote_from_location
from .getonboard import html_to_text


class LeverSource(CompanySource):
    PAGE_SIZE = 100
    MAX_PAGES = 20

    def fetch(self, query):
        self._start()
        domain = "api.eu.lever.co" if self.company.api_region == "eu" else "api.lever.co"
        root = f"https://{domain}/v0/postings/{self.company.board_id}"
        found, failures, received = {}, [], 0
        for page in range(self.MAX_PAGES):
            try:
                items = self._get(root + "?" + urlencode({"mode": "json", "skip": page * self.PAGE_SIZE, "limit": self.PAGE_SIZE}))
                if not isinstance(items, list):
                    raise ValueError("Lever devolvió un listado inválido")
            except Exception as error:
                if page == 0:
                    raise
                failures.append(type(error).__name__)
                break
            received += len(items)
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
            if len(items) < self.PAGE_SIZE:
                break
        else:
            failures.append("page_limit_reached")
        return FetchResult(found.values(), status="partial" if failures else "success", details={"received": received, "eligible": len(found), "failures": failures[:100]})
