"""Bounded discovery of public JobPosting data for custom career websites."""
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import JobPosting
from .base import FetchResult
from .companies import country_codes, eligible, keyword_match, parse_date
from .dynamitejobs import job_schema
from .getonboard import html_to_text
from .public_web import PublicWebSource


def same_site(url, host):
    parts = urlsplit(url)
    return parts.scheme == "https" and not parts.username and not parts.password and parts.port in (None, 443) and (parts.hostname or "").removeprefix("www.") == host.removeprefix("www.")


def structured_posting(item, company, page_url):
    title = item.get("title") or ""
    description = html_to_text(item.get("description") or "")
    if not title or not description.strip():
        raise ValueError("JobPosting sin título o descripción")
    url = urljoin(page_url, item.get("url") or page_url)
    if not same_site(url, urlsplit(company.careers_url).hostname or ""):
        raise ValueError("Vacante fuera del sitio autorizado")
    expiry = parse_date(item.get("validThrough"))
    if expiry and (expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc):
        return None
    places = item.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    labels = []
    explicit_codes = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address") or {}
        if isinstance(address, str):
            labels.append(address)
            continue
        values = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            value = address.get(key)
            if isinstance(value, dict):
                value = value.get("name") or ""
            if isinstance(value, str):
                values.append(value)
                if key == "addressCountry" and re.fullmatch(r"[A-Za-z]{2}", value):
                    explicit_codes.append(value.upper())
        labels.append(", ".join(values))
    requirements = item.get("applicantLocationRequirements") or []
    requirements = requirements if isinstance(requirements, list) else [requirements]
    labels.extend(str(value.get("name") or "") for value in requirements if isinstance(value, dict))
    location = ", ".join(value for value in labels if value)
    codes = sorted(set(country_codes(location) + explicit_codes))
    identity = item.get("identifier") or {}
    identity = identity.get("value") if isinstance(identity, dict) else identity
    organization = item.get("hiringOrganization") or {}
    return JobPosting(source="public_web:" + company.id, external_id=str(identity or hashlib.sha256(url.encode()).hexdigest()),
        title=str(title), company=str(organization.get("name") or company.name) if isinstance(organization, dict) else company.name,
        url=url, description=description[:20000], country=codes[0] if len(codes) == 1 else None,
        location=location or None, remote=True if item.get("jobLocationType") == "TELECOMMUTE" else None,
        posted_at=parse_date(item.get("datePosted")), raw={"platform": "public_web", "country_codes": codes, "extraction": "JobPosting"})


class PublicCompanySource(PublicWebSource):
    max_runtime = 35
    MAX_PAGES = 8
    MAX_LINKS = 40

    def __init__(self, company, **kwargs):
        super().__init__(**kwargs)
        self.company = company
        self.name = "public_web:" + company.id
        self.host = urlsplit(company.careers_url).hostname or ""

    def fetch(self, query):
        self._start()
        queue = [self.company.careers_url]
        seen, found, failures = set(), {}, []
        schemas_found = fetched = delegated = 0
        delegated_boards = set()
        while queue and len(seen) < self.MAX_PAGES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                page = self._get(url)
                fetched += 1
                schemas = list(job_schema(page))
                schemas_found += len(schemas)
                for item in schemas:
                    try:
                        posting = structured_posting(item, self.company, url)
                        if posting and eligible(posting, query) and keyword_match(posting.title, query):
                            found[posting.external_id] = posting
                    except (ValueError, TypeError, AttributeError):
                        failures.append("invalid_job_schema")
                # Details do not lead back to the full website or recommendation loops.
                if schemas:
                    continue
                soup = BeautifulSoup(page, "html.parser")
                from ..catalog import company_from_url
                # Follow only recognized public ATS boards, not arbitrary external links.
                embedded = [node.get("href") or node.get("src") or "" for node in soup.select("a[href], iframe[src]")]
                embedded += re.findall(r"https://(?:job-boards(?:\.eu)?\.greenhouse\.io|boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com)/[^\s\"<>]+", page)
                for target in embedded:
                    board = company_from_url(self.company.name, urljoin(url, target))
                    if not board or board.platform == "public_web" or (board.platform, board.board_id) in delegated_boards or len(delegated_boards) >= 2:
                        continue
                    delegated_boards.add((board.platform, board.board_id))
                    try:
                        result = self._delegate(board, query)
                        delegated += 1
                        for posting in result:
                            identity = board.platform + ":" + str(posting.external_id)
                            found[identity] = replace(posting, source=self.name, external_id=identity,
                                raw={**posting.raw, "origin_platform": board.platform, "platform": "public_web"})
                        if result.status != "success":
                            failures.append("embedded_board_partial")
                    except Exception as error:
                        failures.append("embedded_board:" + type(error).__name__)
                candidates = []
                for anchor in soup.select("a[href]"):
                    target = urljoin(url, anchor["href"])
                    if not same_site(target, self.host):
                        continue
                    parts = urlsplit(target)
                    target = urlunsplit(("https", self.host, parts.path, parts.query, ""))
                    if target in seen or target in queue or re.search(r"(?:login|sign.?in|register|privacy|policy|terms|unsubscribe|apply$|\.pdf$)", parts.path, re.I):
                        continue
                    label = anchor.get_text(" ", strip=True)
                    looks_like_job = bool(re.search(r"(?:/job[s]?/|/position[s]?/|/vacan[ct]|/career[s]?/|/employment/|/o/|/oferta|/requisition|/remote-job|/opening|/trabajo|/apply/)", parts.path, re.I))
                    if not looks_like_job:
                        continue
                    priority = 0 if keyword_match(label + " " + parts.path.replace("-", " "), query) else 1
                    candidates.append((priority, target))
                queue.extend(target for _, target in sorted(dict.fromkeys(candidates))[:self.MAX_LINKS] if target not in queue)
            except Exception as error:
                failures.append(type(error).__name__)
            if self._clock() >= self._deadline:
                failures.append("time_limit_reached")
                break
        if queue:
            failures.append("page_limit_reached")
        if not schemas_found and not delegated:
            failures.append("no_readable_job_schema")
        return FetchResult(found.values(), status="partial" if failures else "success", details={
            "pages_fetched": fetched, "job_schemas": schemas_found, "eligible": len(found),
            "delegated_boards": delegated, "requires_adapter_review": not bool(schemas_found or delegated), "failures": list(dict.fromkeys(failures))[:40]})

    def _delegate(self, company, query):
        from .greenhouse import GreenhouseSource
        from .smartrecruiters import SmartRecruitersSource
        from .lever import LeverSource
        from .ashby import AshbySource
        from .workday import WorkdaySource
        from .career_feeds import CareerFeedSource
        budget = max(0.1, min(15, self._deadline - self._clock()))
        if company.platform in ("workable", "bamboohr", "breezy", "recruitee"):
            source = CareerFeedSource(company, fetch_text=self._fetch_text)
            source.max_runtime = budget
        else:
            constructors = {"greenhouse": GreenhouseSource, "smartrecruiters": SmartRecruitersSource,
                            "lever": LeverSource, "ashby": AshbySource, "workday": WorkdaySource}
            source = constructors[company.platform](company, max_runtime=budget,
                fetch_json=(lambda url, *args: __import__("json").loads(self._fetch_text(url))) if self._fetch_text else None)
        return source.fetch(query)
