"""Ashby's documented public job board feed."""
from urllib.parse import urlsplit

from ..models import JobPosting
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, parse_date
from .getonboard import html_to_text


class AshbySource(CompanySource):
    def fetch(self, query):
        self._start()
        payload = self._get(f"https://api.ashbyhq.com/posting-api/job-board/{self.company.board_id}")
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Ashby devolvió un listado inválido")
        found, invalid = [], 0
        for item in payload["jobs"]:
            if not isinstance(item, dict) or not item.get("title") or not str(item.get("jobUrl", "")).startswith("https://"):
                invalid += 1
                continue
            if item.get("isListed") is False:
                continue
            locations = [item.get("location") or ""] + [location.get("location", "") for location in item.get("secondaryLocations", []) if isinstance(location, dict)]
            label = ", ".join(locations)
            codes = country_codes(label)
            posting = JobPosting(source=self.name, external_id=urlsplit(item["jobUrl"]).path.rstrip("/").rsplit("/", 1)[-1],
                title=item["title"], company=self.company.name, url=item["jobUrl"], location=label or None,
                country=codes[0] if len(codes) == 1 else None, remote=item.get("isRemote") if isinstance(item.get("isRemote"), bool) else None,
                description=(item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml") or ""))[:20000],
                posted_at=parse_date(item.get("publishedAt")), raw={"platform": "ashby", "country_codes": codes})
            if eligible(posting, query) and keyword_match(posting.title, query):
                found.append(posting)
        return FetchResult(found, status="partial" if invalid else "success", details={"received": len(payload["jobs"]), "eligible": len(found), "invalid_records": invalid})
