"""Greenhouse Job Board API: https://docs.greenhouse.io/job-board.html"""
import html

from ..models import JobPosting, SearchQuery
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, remote_from_location
from .getonboard import html_to_text


class GreenhouseSource(CompanySource):
    def fetch(self, query: SearchQuery) -> list[JobPosting]:
        self._start()
        payload = self._get(f"https://boards-api.greenhouse.io/v1/boards/{self.company.board_id}/jobs?content=true")
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Greenhouse devolvió un listado inválido")
        postings, invalid = [], 0
        for item in payload["jobs"]:
            if not isinstance(item, dict) or not item.get("id") or not item.get("title") or not item.get("absolute_url"):
                invalid += 1
                continue
            location = str((item.get("location") or {}).get("name") or "")
            codes = country_codes(location)
            posting = JobPosting(
                source=self.name, external_id=str(item["id"]), title=item["title"], url=item["absolute_url"],
                company=self.company.name, location=location or None,
                country=codes[0] if len(codes) == 1 else None, remote=remote_from_location(location),
                description=html_to_text(html.unescape(item.get("content") or ""))[:20000],
                # updated_at is not the original publication date; do not invent posted_at.
                raw={"platform": "greenhouse", "board_id": self.company.board_id,
                     "country_codes": codes, "updated_at": item.get("updated_at")},
            )
            if eligible(posting, query) and keyword_match(posting.title, query):
                postings.append(posting)
        return FetchResult(postings, status="partial" if invalid else "success", details={
            "received": len(payload["jobs"]), "eligible": len(postings), "invalid_records": invalid,
        })
