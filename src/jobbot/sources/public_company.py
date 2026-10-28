"""Bounded discovery of public JobPosting data for custom career websites."""
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, quote
from xml.etree import ElementTree

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

    def _same_site(self, url):
        try:
            return same_site(url, self.host)
        except ValueError:
            return False

    def _clean_url(self, url):
        parts = urlsplit(url)
        return urlunsplit(("https", self.host, quote(parts.path, safe="/%:@-._~!$&'()*+,;="),
                              quote(parts.query, safe="=&%:@/?-._~!$'()*+,;"), ""))

    def prepare(self, db):
        self._previous = None
        order = "finished_at desc, started_at desc" + (", rowid desc" if db.is_sqlite else "")
        rows = db.query(f"select details from runs where source = ? order by {order} limit 1", (self.name,))
        db.conn.commit()
        if rows:
            try:
                self._previous = json.loads(rows[0]["details"] or "{}")
            except (ValueError, TypeError):
                pass

    def after_store(self, db):
        self._details["checkpoint_safe"] = True

    def fetch(self, query):
        from .public_adapters import (amazon, oracle, html_listing, detail_posting, phenom_links,
                                     shared_listing, metadata_posting, configured_boards,
                                     render_public_page, review_category, ADAPTER_REVISION)
        self._start()
        self._browser_pages = 0
        self._browser_issue = None
        rendered_pages = {}
        if re.fullmatch(r"[a-zA-Z0-9.-]+\.oraclecloud\.com", self.host):
            result = oracle(self, query)
            if result is not None:
                self._details = result.details
                return result
        if self.host.removeprefix("www.") == "amazon.jobs":
            try:
                result = amazon(self, query)
                if result is not None:
                    self._details = result.details
                    return result
            except Exception as error:
                self._details = {"adapter": "amazon_search", "failures": [self._failure(error)]}
                return FetchResult(status="partial", details=self._details)
        key = hashlib.sha256(json.dumps([ADAPTER_REVISION, self.company.careers_url, query.keywords, query.countries,
            query.residence_country, query.include_remote_latam], sort_keys=True).encode()).hexdigest()
        queue = [{"url": self._clean_url(self.company.careers_url)}]
        seen, found, failures = set(), {}, []
        old = getattr(self, "_previous", None)
        resumed = bool(old and old.get("checkpoint_safe") and old.get("cursor_key") == key and old.get("pending_items"))
        if resumed:
            queue = [item for item in old["pending_items"] if isinstance(item, dict) and self._same_site(item.get("url", ""))]
            seen = set(old.get("visited_urls", []))
        schemas_found = fetched = delegated = listings = 0
        delegated_boards = set()
        adapter_names = set()
        page_signatures = set(old.get("page_signatures", [])) if resumed else set()
        retry = []
        processed = 0
        while queue and processed < self.MAX_PAGES:
            item = queue.pop(0)
            url = item["url"]
            if url in seen:
                continue
            if self._clock() >= self._deadline:
                queue.insert(0, item)
                failures.append("time_limit_reached")
                break
            processed += 1
            seen.add(url)
            try:
                if item.get("rendered"):
                    page = rendered_pages.pop(url, None) or render_public_page(self, url)
                    if page is None:
                        raise RuntimeError("No se pudo leer la página con JavaScript")
                else:
                    page = self._get(url, item["form"]) if item.get("form") is not None else self._get(url)
                fetched += 1
                if 'name="jobbot-incomplete-pagination"' in page:
                    failures.append("pagination_unknown")
                if page.lstrip().startswith(("<?xml", "<urlset", "<sitemapindex")):
                    xml = ElementTree.fromstring(page)
                    locations = [node.text.strip() for node in xml.iter() if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]
                    is_index = xml.tag.rsplit("}", 1)[-1] == "sitemapindex"
                    locations = [u for u in locations if self._same_site(u) and (
                        re.search(r"job|career|position|vacan|oferta|empleo|opening", urlsplit(u).path, re.I)
                        or (is_index and "sitemap" in urlsplit(u).path.lower()))]
                    if len(locations) > 2000:
                        failures.append("sitemap_limit_reached")
                    queue += [{"url": self._clean_url(u)} for u in locations[:2000] if self._clean_url(u) not in seen]
                    continue
                schemas = list(job_schema(page))
                schemas_found += len(schemas)
                for schema in schemas:
                    try:
                        posting = structured_posting(schema, self.company, url)
                        if posting and eligible(posting, query) and keyword_match(posting.title, query):
                            found[posting.external_id] = posting
                    except (ValueError, TypeError, AttributeError):
                        failures.append("invalid_job_schema")
                if schemas:
                    continue
                if item.get("metadata"):
                    try:
                        posting = detail_posting(self, page, url, item["metadata"], item["adapter"])
                    except ValueError:
                        rendered = render_public_page(self, url, page)
                        if rendered is None:
                            raise
                        adapter_names.add("browser_html")
                        schemas = list(job_schema(rendered))
                        posting = structured_posting(schemas[0], self.company, url) if schemas else detail_posting(self, rendered, url, item["metadata"], item["adapter"])
                    schemas_found += 1
                    if posting and eligible(posting, query):
                        found[posting.external_id] = posting
                    continue
                shared = shared_listing(self, query, page, url, item.get("form"))
                if shared is not None:
                    listings += int(not shared.get("bootstrap"))
                    adapter_names.add(shared["adapter"])
                    failures.extend(shared["failures"])
                    records = shared["candidates"]
                    signature = hashlib.sha256(json.dumps([shared["adapter"], sorted(records)]).encode()).hexdigest()
                    if records and signature in page_signatures:
                        failures.append("pagination_not_advancing")
                        continue
                    if records:
                        page_signatures.add(signature)
                    for target, metadata in records.items():
                        if not keyword_match(metadata["title"], query):
                            continue
                        preview = metadata_posting(self, target, metadata, shared["adapter"])
                        if preview.raw.get("country_codes") and not eligible(preview, query):
                            continue
                        if metadata.get("description"):
                            posting = preview
                            if eligible(posting, query):
                                found[posting.external_id] = posting
                        elif self._clean_url(target) not in seen:
                            queue.append({"url": self._clean_url(target), "metadata": metadata, "adapter": shared["adapter"]})
                    for target in shared["following"]:
                        next_item = target if isinstance(target, dict) else {"url": target}
                        if self._same_site(next_item["url"]) and self._clean_url(next_item["url"]) not in seen:
                            queue.append({**next_item, "url": self._clean_url(next_item["url"])})
                    continue
                listing = html_listing(self, query, page, url)
                if listing is not None:
                    adapter, candidates, following, listing_url = listing
                    listings += 1
                    # Mark filtered first pages as visited too: don't revisit them through navigation.
                    seen.add(self._clean_url(listing_url))
                    queue += [{"url": self._clean_url(target), "metadata": metadata, "adapter": adapter}
                              for target, metadata in candidates.items() if self._clean_url(target) not in seen]
                    queue += [{"url": self._clean_url(target)} for target in following if self._clean_url(target) not in seen]
                    continue
                soup = BeautifulSoup(page, "html.parser")
                from ..catalog import company_from_url
                embedded = [node.get("href") or node.get("src") or "" for node in soup.select("a[href], iframe[src], script[src]")]
                direct_embeds = {urljoin(url, node.get("src") or "") for node in soup.select("iframe[src], script[src]")}
                published_links, empty_board = phenom_links(page)
                published_links += configured_boards(page)
                if empty_board:
                    listings += 1
                    continue
                embedded += published_links
                embedded += re.findall(r"https://(?:job-boards(?:\.eu)?\.greenhouse\.io|boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com)/[^\s\"<>]+", page)
                board_found = False
                for target in embedded:
                    try:
                        board = company_from_url(self.company.name, urljoin(url, target))
                    except ValueError:
                        continue
                    if not board or board.platform == "public_web":
                        continue
                    # An aggregator linking to one employer is not that employer's
                    # complete board. Only delegate embeds or matching employers.
                    employer = re.sub(r"[^a-z0-9]", "", self.company.id.lower())
                    token = re.sub(r"[^a-z0-9]", "", board.board_id.lower())
                    tenant = (urlsplit(board.careers_url).hostname or "").split(".")[0]
                    authoritative = (urljoin(url, target) in direct_embeds or target in published_links
                        or (len(employer) >= 3 and (employer in token or employer in tenant)))
                    if not authoritative:
                        continue
                    identity = (board.platform, board.board_id, board.api_region,
                        urlsplit(board.careers_url).hostname if board.platform == "workday" else "")
                    if identity in delegated_boards:
                        continue
                    if len(delegated_boards) >= 2:
                        failures.append("embedded_board_limit_reached")
                        continue
                    delegated_boards.add(identity)
                    try:
                        result = self._delegate(board, query)
                        delegated += 1
                        board_found = True
                        for posting in result:
                            identity_key = board.platform + ":" + (urlsplit(board.careers_url).hostname or "") + ":" + str(posting.external_id)
                            found[identity_key] = replace(posting, source=self.name, external_id=identity_key,
                                raw={**posting.raw, "origin_platform": board.platform, "platform": "public_web"})
                        if result.status != "success":
                            failures.append("embedded_board_partial")
                    except Exception as error:
                        failures.append("embedded_board:" + self._failure(error))
                # A company page with a complete public ATS board does not need an
                # unrelated crawl of its corporate navigation.
                if board_found:
                    continue
                if not item.get("rendered") and not item.get("form"):
                    rendered = render_public_page(self, url, page)
                    if rendered is not None:
                        adapter_names.add("browser_html")
                        rendered_pages[url] = rendered
                        queue.insert(0, {**item, "rendered": True})
                        seen.discard(url)
                        continue
                candidates = []
                candidate_metadata = {}
                for anchor in soup.select("a[href]"):
                    try:
                        target = urljoin(url, anchor["href"])
                        if not self._same_site(target):
                            continue
                        target = self._clean_url(target)
                    except ValueError:
                        continue
                    parts = urlsplit(target)
                    if target in seen or re.search(r"(?:login|sign.?in|register|privacy|policy|terms|unsubscribe|apply$|\.pdf$)", parts.path, re.I):
                        continue
                    label = anchor.get_text(" ", strip=True)
                    looks_like_job = bool(re.search(r"(?:/job[s]?/|/position[s]?/|/vacan[ct]|/career[s]?/|/employment/|/o/|/oferta|/requisition|/remote-job|/opening|/trabajo|/apply/)", parts.path, re.I))
                    if looks_like_job:
                        priority = 0 if keyword_match(label + " " + parts.path.replace("-", " "), query) else 1
                        candidates.append((priority, target))
                        if (keyword_match(label, query) and 8 <= len(label) <= 180
                            and re.search(r"/(?:jobs?|position|vacancy)/[^/?]*\d", parts.path, re.I)):
                            candidate_metadata[target] = {"title": label, "location": ""}
                distinct = list(dict.fromkeys(sorted(candidates)))
                if len(distinct) > self.MAX_LINKS:
                    failures.append("link_limit_reached")
                queue += [{"url": target, **({"metadata": candidate_metadata[target], "adapter": "public_html"}
                          if target in candidate_metadata else {})} for _, target in distinct[:self.MAX_LINKS]]
                if not candidates and url == self._clean_url(self.company.careers_url):
                    # Prefer sitemap URLs explicitly published by this host.
                    maps = re.findall(r"(?im)^sitemap:\s*(https://\S+)", self._robots or "")
                    maps += [urljoin(url, node["href"]) for node in soup.select("link[rel='sitemap'][href]")]
                    maps = [m for m in maps if self._same_site(m)]
                    queue += [{"url": self._clean_url(m)} for m in maps[:3] if self._clean_url(m) not in seen]
            except Exception as error:
                failures.append(self._failure(error))
                # Retry network/detail errors on the next run, without redoing successful pages.
                retry.append(item)
                seen.discard(url)
        queue = list({entry["url"]: entry for entry in queue if entry["url"] not in seen}.values())
        if queue:
            failures.append("page_limit_reached")
        evidence = bool(schemas_found or delegated or listings or (resumed and old.get("coverage_evidence")))
        if resumed and old.get("coverage_gaps"):
            failures.extend(old["coverage_gaps"])
        if not evidence:
            failures.append("no_readable_job_schema")
        pending = list({entry["url"]: entry for entry in queue + retry}.values())
        if len(pending) > 2000:
            failures.append("pending_limit_reached")
        if pending and len(seen) > 5000:
            failures.append("visited_limit_reached")
        self._details = {"pages_fetched": fetched, "job_schemas": schemas_found, "eligible": len(found),
            "delegated_boards": delegated, "recognized_lists": listings,
            "adapters": sorted(adapter_names),
            "browser_pages": self._browser_pages, "browser_issue": self._browser_issue,
            "review_category": review_category(failures + ([self._browser_issue] if self._browser_issue else []), evidence),
            "requires_adapter_review": not evidence or any(f in failures for f in (
                "pagination_unknown", "pagination_not_advancing", "unreadable_job_detail", "invalid_public_record", "invalid_public_listing", "invalid_job_schema")), "coverage_evidence": evidence,
            "coverage_gaps": [f for f in dict.fromkeys(failures) if f in {"link_limit_reached", "embedded_board_limit_reached", "sitemap_limit_reached", "pending_limit_reached", "visited_limit_reached", "pagination_unknown", "pagination_not_advancing"}],
            "page_signatures": sorted(page_signatures) if pending else [],
            "resumed": resumed, "cursor_key": key, "pending_items": pending[:2000],
            "pending_pages": len(pending),
            "visited_urls": sorted(seen)[-5000:] if pending else [], "cycle_complete": not pending and not failures,
            "failures": list(dict.fromkeys(failures))[:40]}
        return FetchResult(found.values(), status="partial" if failures else "success", details=self._details)

    @staticmethod
    def _failure(error):
        from urllib.error import HTTPError, URLError
        from ssl import SSLCertVerificationError
        from socket import gaierror
        if isinstance(error, HTTPError):
            return f"http_{error.code}"
        if isinstance(error, PermissionError):
            return "robots_blocked"
        if isinstance(error, ValueError) and "Descripción pública ausente" in str(error):
            return "unreadable_job_detail"
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, SSLCertVerificationError):
            return "tls_certificate_error"
        if isinstance(reason, gaierror):
            return "dns_error"
        if isinstance(reason, TimeoutError):
            return "timeout"
        return type(error).__name__

    def _delegate(self, company, query):
        from .greenhouse import GreenhouseSource
        from .smartrecruiters import SmartRecruitersSource
        from .lever import LeverSource
        from .ashby import AshbySource
        from .workday import WorkdaySource
        from .career_feeds import CareerFeedSource
        # Board links often point to a specific vacancy or /login. Detail URLs
        # must be built from the board root rather than appending to that path.
        host = urlsplit(company.careers_url).hostname
        if company.platform in {"workday", "greenhouse", "lever", "ashby", "smartrecruiters"}:
            company = replace(company, careers_url=f"https://{host}/{company.board_id}")
        budget = max(0.1, self._deadline - self._clock())
        if company.platform in ("workable", "bamboohr", "breezy", "recruitee"):
            source = CareerFeedSource(company, fetch_text=self._fetch_text)
            source.max_runtime = budget
        else:
            constructors = {"greenhouse": GreenhouseSource, "smartrecruiters": SmartRecruitersSource,
                            "lever": LeverSource, "ashby": AshbySource, "workday": WorkdaySource}
            source = constructors[company.platform](company, max_runtime=budget,
                fetch_json=(lambda url, *args: __import__("json").loads(self._fetch_text(url))) if self._fetch_text else None)
        return source.fetch(query)
