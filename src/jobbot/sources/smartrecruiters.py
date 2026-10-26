"""SmartRecruiters public Posting API, list + full job description."""
from urllib.parse import quote, urlencode

from ..models import JobPosting, SearchQuery
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, parse_date, remote_from_location
from .getonboard import html_to_text


class SmartRecruitersSource(CompanySource):
    PAGE_SIZE = 100
    MAX_PAGES = 10

    def _posting(self, item: dict, description: str = "") -> JobPosting:
        location = item.get("location") or {}
        label = ", ".join(str(location[k]) for k in ("city", "region", "country") if location.get(k))
        country = str(location.get("country") or "").upper()
        codes = [country] if len(country) == 2 else country_codes(label)
        remote = location.get("remote")
        if not isinstance(remote, bool):
            remote = remote_from_location(label)
        return JobPosting(
            source=self.name, external_id=str(item["id"]), title=item["name"], company=self.company.name,
            url=f"https://jobs.smartrecruiters.com/{self.company.board_id}/{item['id']}",
            location=label or None, country=codes[0] if len(codes) == 1 else None, remote=remote,
            description=description[:20000], posted_at=parse_date(item.get("releasedDate")),
            raw={"platform": "smartrecruiters", "board_id": self.company.board_id, "country_codes": codes},
        )

    def fetch(self, query: SearchQuery) -> list[JobPosting]:
        self._start()
        root = f"https://api.smartrecruiters.com/v1/companies/{self.company.board_id}/postings"
        found, failures, received, successful_pages = {}, [], 0, 0
        last_error = None
        targets = [query.residence_country] if query.residence_country else list(query.countries)
        scopes = [{"country": code.lower()} for code in dict.fromkeys(targets)]
        if query.include_remote_latam and self.company.include_global_remote:
            scopes.append({})
        for scope in scopes:
            label = scope.get("country", "global")
            offset = 0
            for page in range(self.MAX_PAGES):
                try:
                    payload = self._get(root + "?" + urlencode({"limit": self.PAGE_SIZE, "offset": offset, **scope}))
                    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                        raise ValueError("SmartRecruiters devolvió un listado inválido")
                    successful_pages += 1
                except Exception as exc:
                    last_error = exc
                    failures.append(f"{label} page {page + 1}: {type(exc).__name__}")
                    break
                items = payload["content"]
                received += len(items)
                for item in items:
                    if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                        failures.append("invalid_record")
                        continue
                    if str(item["id"]) in found:
                        continue
                    try:
                        candidate = self._posting(item)
                        if not eligible(candidate, query) or not keyword_match(candidate.title, query):
                            continue
                        # Never follow an arbitrary ref URL supplied in a listing.
                        detail = self._get(root + "/" + quote(str(item["id"]), safe=""))
                        if not isinstance(detail, dict) or not isinstance(detail.get("jobAd"), dict):
                            raise ValueError("Descripción de SmartRecruiters inválida")
                        sections = detail["jobAd"].get("sections")
                        if not isinstance(sections, dict):
                            raise ValueError("Faltan las secciones de la oferta")
                        text = "\n\n".join(html_to_text(section.get("text")) for section in sections.values() if isinstance(section, dict) and section.get("text"))
                        if not text.strip():
                            raise ValueError("La descripción está vacía")
                        merged = {**item, **{key: detail[key] for key in ("releasedDate",) if key in detail}}
                        found[str(item["id"])] = self._posting(merged, text)
                    except Exception as exc:
                        failures.append(f"job {item['id']}: {type(exc).__name__}")
                offset += len(items)
                total = payload.get("totalFound")
                if not items or (isinstance(total, int) and offset >= total) or (not isinstance(total, int) and len(items) < self.PAGE_SIZE):
                    break
            else:
                failures.append(f"{label}: page_limit_reached")
            if self._clock() >= self._deadline:
                failures.append("time_limit_reached")
                break
        if not successful_pages and last_error is not None:
            raise last_error
        return FetchResult(found.values(), status="partial" if failures else "success", details={
            "received": received, "eligible": len(found), "failures": failures[:100],
            "scopes": [scope.get("country", "global") for scope in scopes],
        })
