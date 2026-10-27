"""Public Workday career-site JSON, as used by the unauthenticated job search."""
import re
from urllib.parse import urlsplit

from ..models import JobPosting
from .base import FetchResult
from .companies import CompanySource, country_codes, eligible, keyword_match, parse_date, remote_from_location
from .getonboard import html_to_text


class WorkdaySource(CompanySource):
    PAGE_SIZE = 20
    MAX_PAGES = 15

    @property
    def root(self):
        host = urlsplit(self.company.careers_url).hostname
        if not host or not re.fullmatch(r"[a-zA-Z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com", host):
            raise ValueError("Host de Workday inválido")
        return f"https://{host}/wday/cxs/{host.split('.')[0]}/{self.company.board_id}"

    def fetch(self, query):
        self._start()
        # Restrict large corporate boards using the public country facets, never guess their IDs.
        initial = self._post(self.root + "/jobs", {"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}})
        if not isinstance(initial, dict) or not isinstance(initial.get("jobPostings"), list):
            raise ValueError("Workday devolvió un listado inválido")
        targets = {query.residence_country} if query.residence_country else set(query.countries)
        facets = {}
        for facet in initial.get("facets", []):
            if facet.get("facetParameter") in ("Location_Country", "locationCountry"):
                ids = [value["id"] for value in facet.get("values", []) if value.get("id") and targets.intersection(country_codes(value.get("descriptor", "")))]
                if ids:
                    facets[facet["facetParameter"]] = ids
        # A small board without country facets can still be searched completely.
        scopes = [{"appliedFacets": facets, "searchText": ""}] if facets else [{"appliedFacets": {}, "searchText": "Costa Rica" if query.residence_country == "CR" else query.residence_country}]
        if query.include_remote_latam and self.company.include_global_remote:
            scopes.append({"appliedFacets": {}, "searchText": "LATAM"})
        found, failures, received = {}, [], 0
        for scope in scopes:
            for page in range(self.MAX_PAGES):
                try:
                    payload = self._post(self.root + "/jobs", {"limit": self.PAGE_SIZE, "offset": page * self.PAGE_SIZE, **scope})
                    if not isinstance(payload.get("jobPostings"), list):
                        raise ValueError("Listado Workday inválido")
                    items = payload["jobPostings"]
                    received += len(items)
                    for item in items:
                        path = str(item.get("externalPath") or "")
                        title = item.get("title") or ""
                        if not path.startswith("/job/") or ".." in path or "?" in path or "#" in path or not title:
                            failures.append("invalid_record")
                            continue
                        if path in found or not keyword_match(title, query):
                            continue
                        try:
                            detail = self._get(self.root + path).get("jobPostingInfo")
                            if not isinstance(detail, dict) or not detail.get("jobDescription"):
                                raise ValueError("Descripción Workday ausente")
                            locations = [detail.get("location") or item.get("locationsText") or ""] + (detail.get("additionalLocations") or [])
                            label = ", ".join(str(value) for value in locations)
                            country = (detail.get("country") or {}).get("descriptor", "")
                            codes = country_codes(label + " " + country)
                            remote = remote_from_location(str(detail.get("remoteType") or item.get("remoteType") or label))
                            posting = JobPosting(source=self.name, external_id=str(detail.get("jobReqId") or path), title=title, company=self.company.name,
                                url=self.company.careers_url.rstrip("/") + path, description=html_to_text(detail["jobDescription"])[:20000],
                                location=label or None, country=codes[0] if len(codes) == 1 else None, remote=remote,
                                posted_at=parse_date(detail.get("startDate")), raw={"platform": "workday", "country_codes": codes})
                            if eligible(posting, query):
                                found[path] = posting
                        except Exception as error:
                            failures.append("detail:" + type(error).__name__)
                    if not items or (isinstance(payload.get("total"), int) and (page + 1) * self.PAGE_SIZE >= payload["total"]) or len(items) < self.PAGE_SIZE:
                        break
                except Exception as error:
                    failures.append("page:" + type(error).__name__)
                    break
            else:
                failures.append("page_limit_reached")
        return FetchResult(found.values(), status="partial" if failures else "success", details={"received": received, "eligible": len(found), "failures": failures[:100], "coverage": "residence_country_and_latam"})
