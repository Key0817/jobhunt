"""Dynamite public sitemap + JobPosting JSON-LD. Does not call its excluded /api/."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..models import JobPosting
from .base import FetchResult, register
from .companies import country_codes, eligible, keyword_match, parse_date
from .getonboard import html_to_text
from .public_web import PublicWebSource


def job_schema(text):
    def walk(value):
        if isinstance(value, list):
            for child in value:
                yield from walk(child)
        elif isinstance(value, dict):
            if value.get("@type") == "JobPosting" or "JobPosting" in (value.get("@type") or []):
                yield value
            yield from walk(value.get("@graph", []))
    for script in BeautifulSoup(text, "html.parser").select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue


@register
class DynamiteJobsSource(PublicWebSource):
    name = "dynamitejobs"
    host = "dynamitejobs.com"
    MAX_DETAILS = 60

    def fetch(self, query):
        self._start()
        xml = ElementTree.fromstring(self._get("/sitemap/jobs-of-company.xml"))
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        links = []
        for entry in xml.findall("s:url", namespace):
            url = entry.findtext("s:loc", namespaces=namespace) or ""
            if urlsplit(url).hostname == self.host and "/remote-job/" in url and keyword_match(urlsplit(url).path.rsplit("/", 1)[-1].replace("-", " "), query):
                links.append(url)
        links = list(dict.fromkeys(links))
        found, failures, closed = [], [], 0
        for url in links[:self.MAX_DETAILS]:
            try:
                schemas = list(job_schema(self._get(url)))
                if not schemas:
                    raise ValueError("Falta JobPosting público")
                item = schemas[0]
                expiry = parse_date(item.get("validThrough"))
                if expiry and (expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc):
                    closed += 1
                    continue
                requirements = item.get("applicantLocationRequirements") or []
                requirements = requirements if isinstance(requirements, list) else [requirements]
                names = [str(value.get("name") or "") for value in requirements if isinstance(value, dict)]
                locations = item.get("jobLocation") or []
                locations = locations if isinstance(locations, list) else [locations]
                names += [", ".join(str(value) for value in (location.get("address") or {}).values() if isinstance(value, str)) for location in locations if isinstance(location, dict)]
                label = ", ".join(names)
                codes = country_codes(label)
                company = item.get("hiringOrganization") or {}
                posting = JobPosting(source=self.name, external_id=hashlib.sha256(urlsplit(url).path.encode()).hexdigest(),
                    title=item.get("title") or "", company=company.get("name") or "", url=url,
                    description=html_to_text(item.get("description") or "")[:20000], remote=item.get("jobLocationType") == "TELECOMMUTE",
                    country=codes[0] if len(codes) == 1 else None, location=label or None, posted_at=parse_date(item.get("datePosted")),
                    raw={"platform": self.name, "country_codes": codes})
                if posting.title and eligible(posting, query) and keyword_match(posting.title, query):
                    found.append(posting)
            except Exception as error:
                failures.append(type(error).__name__)
        if len(links) > self.MAX_DETAILS:
            failures.append("detail_limit_reached")
        return FetchResult(found, status="partial" if failures else "success", details={"candidate_links": len(links), "eligible": len(found), "closed": closed, "failures": failures[:100]})
