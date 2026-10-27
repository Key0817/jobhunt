"""Public career-site feeds for Workable, BambooHR, Breezy and Recruitee."""
import json
from urllib.parse import quote, urlsplit

from ..models import JobPosting
from .base import FetchResult
from .companies import country_codes, eligible, keyword_match, parse_date, remote_from_location
from .dynamitejobs import job_schema
from .getonboard import html_to_text
from .public_web import PublicWebSource


class CareerFeedSource(PublicWebSource):
    max_runtime = 60

    def __init__(self, company, **kwargs):
        super().__init__(**kwargs)
        self.company = company
        self.host = urlsplit(company.careers_url).hostname
        self.name = company.platform + ":" + company.id

    def feed_path(self):
        return {"workable": f"/api/v1/widget/accounts/{self.company.board_id}", "bamboohr": "/careers/list", "breezy": "/json", "recruitee": "/api/offers/"}[self.company.platform]

    def items(self, data):
        platform = self.company.platform
        value = data if platform == "breezy" else data.get({"workable": "jobs", "bamboohr": "result", "recruitee": "offers"}[platform]) if isinstance(data, dict) else None
        if not isinstance(value, list):
            raise ValueError("Feed público de empleos inválido")
        return value

    def fetch(self, query):
        self._start()
        items = self.items(json.loads(self._get(self.feed_path())))
        found, failures = {}, []
        platform = self.company.platform
        for item in items:
            try:
                if platform == "workable":
                    identifier, title = item.get("shortcode"), item.get("title")
                    locations = item.get("locations") or [{"country": item.get("country"), "city": item.get("city")}]
                    location = ", ".join(str(value.get(key) or "") for value in locations for key in ("city", "country"))
                    codes = sorted(set(country_codes(location) + [str(value["countryCode"]).upper() for value in locations if value.get("countryCode")]))
                    remote = item.get("telecommuting") if isinstance(item.get("telecommuting"), bool) else None
                    url = f"https://apply.workable.com/j/{identifier}"
                    description = item.get("description") or ""
                    date = item.get("published_on")
                elif platform == "bamboohr":
                    identifier, title = item.get("id"), item.get("jobOpeningName")
                    location = ", ".join(str(value or "") for value in (item.get("atsLocation") or {}).values())
                    codes = country_codes(location + " " + str(title or ""))
                    remote = item.get("isRemote") if isinstance(item.get("isRemote"), bool) else remote_from_location(str(title or ""))
                    url = f"https://{self.host}/careers/{identifier}"
                    description, date = "", None
                elif platform == "recruitee":
                    identifier, title = item.get("id"), item.get("title")
                    location = ", ".join(str(item.get(key) or "") for key in ("city", "state", "country"))
                    codes = [str(item["country_code"]).upper()] if item.get("country_code") else country_codes(location)
                    remote = item.get("remote") if isinstance(item.get("remote"), bool) else remote_from_location(location)
                    url = item.get("careers_url") or f"https://{self.host}/o/{item.get('slug', '')}"
                    description = (item.get("description") or "") + "\n" + (item.get("requirements") or "")
                    date = item.get("published_at")
                else:
                    identifier, title = item.get("id") or item.get("_id"), item.get("name")
                    address = item.get("location") or {}
                    location = address.get("name") or ", ".join(str(address.get(key) or "") for key in ("city", "country"))
                    country = address.get("country")
                    codes = [country.upper()] if isinstance(country, str) and len(country) == 2 else country_codes(location)
                    remote = item.get("remote") if isinstance(item.get("remote"), bool) else remote_from_location(location)
                    url = item.get("url") or f"https://{self.host}/p/{identifier}"
                    description, date = item.get("description") or "", item.get("published_date")
                if not identifier or not title or not str(url).startswith("https://"):
                    raise ValueError("invalid_record")
                posting = JobPosting(source=self.name, external_id=str(identifier), title=title, company=self.company.name,
                    url=url, description=html_to_text(description)[:20000], location=location or None, country=codes[0] if len(codes) == 1 else None,
                    remote=remote, posted_at=parse_date(date), raw={"platform": platform, "country_codes": codes})
                if not keyword_match(title, query) or not eligible(posting, query):
                    continue
                if platform == "bamboohr":
                    detail = json.loads(self._get(f"/careers/{quote(str(identifier), safe='')}/detail"))["result"]["jobOpening"]
                    if detail.get("jobOpeningStatus") != "Open":
                        continue
                    posting = JobPosting(**{**posting.__dict__, "description": html_to_text(detail.get("description") or "")[:20000]})
                elif platform == "workable" and not posting.description.strip():
                    schemas = list(job_schema(self._get(f"/j/{quote(str(identifier), safe='')}")))
                    text = html_to_text(schemas[0].get("description") or "") if schemas else ""
                    if not text.strip():
                        raise ValueError("description_unavailable")
                    posting = JobPosting(**{**posting.__dict__, "description": text[:20000]})
                if not posting.description.strip():
                    raise ValueError("description_unavailable")
                found[str(identifier)] = posting
            except Exception as error:
                failures.append(type(error).__name__)
            if self._clock() >= self._deadline:
                failures.append("time_limit_reached")
                break
        return FetchResult(found.values(), status="partial" if failures else "success", details={"received": len(items), "eligible": len(found), "failures": failures[:100]})
