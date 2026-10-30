"""Readers for public career lists whose HTML does not contain JobPosting JSON-LD."""
import json
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit, parse_qsl, urlunsplit

from bs4 import BeautifulSoup

from ..models import JobPosting
from .base import FetchResult
from .companies import country_codes, eligible, keyword_match, parse_date, remote_from_location
from .getonboard import html_to_text

# Increment when discovery changes so an old unsupported crawl is inspected again.
ADAPTER_REVISION = 3


def board_from_request(url):
    """Recognize only public vacancy feed endpoints, never candidate/account APIs."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return None
        host, path = parts.hostname or "", parts.path
        if host in {"boards-api.greenhouse.io", "boards-api.eu.greenhouse.io"}:
            match = re.fullmatch(r"/v1/boards/([A-Za-z0-9_-]+)/jobs/?", path)
            if match:
                return "https://job-boards" + (".eu" if ".eu." in host else "") + ".greenhouse.io/" + match.group(1)
        if host in {"api.lever.co", "api.eu.lever.co"}:
            match = re.fullmatch(r"/v0/postings/([A-Za-z0-9_-]+)/?", path)
            if match:
                return "https://jobs" + (".eu" if ".eu." in host else "") + ".lever.co/" + match.group(1)
        if host == "api.ashbyhq.com":
            match = re.fullmatch(r"/posting-api/job-board/([A-Za-z0-9_-]+)/?", path)
            if match:
                return "https://jobs.ashbyhq.com/" + match.group(1)
        for suffix, feed, root in (("bamboohr.com", "/careers/list", "/careers"),
                                   ("recruitee.com", "/api/offers", "/"), ("breezy.hr", "/json", "/")):
            if re.fullmatch(r"[A-Za-z0-9-]+\." + re.escape(suffix), host) and path.rstrip("/") == feed:
                return "https://" + host + root
    except ValueError:
        pass
    return None


def browser_request_allowed(url, resource, method, host, script_hosts, robots):
    """Keep rendering anonymous and restricted to public job-search traffic."""
    from .public_web import robots_allowed
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return False
        same = (parts.hostname or "").removeprefix("www.") == host.removeprefix("www.")
        if resource in {"image", "media", "font", "websocket"}:
            return False
        if not same:
            return method == "GET" and resource in {"script", "stylesheet"} and parts.hostname in script_hosts
        if not robots_allowed(robots, url):
            return False
        if re.search(r"/(?:login|signin|register|candidate|application|apply)(?:/|$)", parts.path, re.I):
            return False
        return method == "GET" or (method == "POST" and bool(re.search(
            r"/(?:api/(?:jobs|positions|apply/v2/jobs)|widgets|services/recruiting/v1/jobs)/?$", parts.path)))
    except ValueError:
        return False


def render_public_page(source, url, original=""):
    """Optional generic JavaScript reader; all browsers close before returning."""
    import os
    from importlib.util import find_spec
    if source._fetch_text is not None or os.environ.get("JOBBOT_PUBLIC_WEB_BROWSER", "auto").lower() in {"0", "false", "off"}:
        return None
    if find_spec("playwright") is None:
        source._browser_issue = "browser_not_installed"
        return None
    remaining = source._deadline - source._clock()
    if remaining < 4 or not source._same_site(url):
        return None
    from urllib.error import HTTPError
    from .public_web import robots_allowed
    if source._robots is None:
        try:
            source._robots = source._request(f"https://{source.host}/robots.txt")
        except HTTPError as error:
            if error.code in (404, 410):
                source._robots = ""
            else:
                source._browser_issue = source._failure(error)
                return None
        except Exception as error:
            source._browser_issue = source._failure(error)
            return None
    if not robots_allowed(source._robots, url):
        source._browser_issue = "robots_blocked"
        return None
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    soup = BeautifulSoup(original, "html.parser")
    script_hosts = {urlsplit(urljoin(url, n.get("src") or n.get("href") or "")).hostname
                    for n in soup.select("script[src], link[rel='stylesheet'][href]")}
    end = source._clock() + min(18, remaining)
    discovered = set()
    def board_markup():
        return ''.join('<iframe data-jobbot-public-board="true" src="' + link + '"></iframe>' for link in sorted(discovered))
    def milliseconds():
        return max(1, int((end - source._clock()) * 1000))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=milliseconds())
            context = None
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                def request_route(route):
                    request = route.request
                    board = board_from_request(request.url) if request.method == "GET" else None
                    if board:
                        discovered.add(board)
                    if browser_request_allowed(request.url, request.resource_type, request.method,
                                               source.host, script_hosts, source._robots or ""):
                        route.continue_()
                    else:
                        route.abort()
                context.route("**/*", request_route)
                page = context.new_page()
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=milliseconds())
                except BrowserTimeout:
                    response = None
                    if not source._same_site(page.url):
                        raise
                if response is not None and response.status >= 400:
                    source._browser_issue = f"browser_http_{response.status}"
                    return None
                try:
                    page.wait_for_load_state("networkidle", timeout=min(4000, milliseconds()))
                except BrowserTimeout:
                    pass
                if milliseconds() > 1000:
                    # Some public boards only load once their section enters the
                    # viewport. Scrolling does not click links or submit forms.
                    section = page.locator("#positions, #openings, #job-listings, #open-positions, #jobs")
                    try:
                        if section.count():
                            section.first.scroll_into_view_if_needed(timeout=milliseconds())
                        else:
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_load_state("networkidle", timeout=min(2000, milliseconds()))
                    except BrowserTimeout:
                        pass
                content = page.content()
                if len(content) > 8_000_000:
                    source._browser_issue = "browser_response_limit"
                    return None
                # A rendered first page is not proof that a JavaScript paginator
                # was exhausted. Preserve that gap instead of reporting success.
                buttons = page.locator("button[aria-label*='Next']:not([disabled]), button[aria-label*='next']:not([disabled]), button.load-more:not([disabled])")
                pending = any(buttons.nth(i).is_visible() for i in range(min(25, buttons.count())))
                if pending:
                    content += '<meta name="jobbot-incomplete-pagination" content="true">'
                source._browser_pages = getattr(source, "_browser_pages", 0) + 1
                return content + board_markup()
            finally:
                if context is not None:
                    try:
                        context.unroute_all(behavior="wait")
                        context.close()
                    except Exception:
                        pass
                browser.close()
    except Exception as error:
        source._browser_issue = "browser_timeout" if isinstance(error, BrowserTimeout) else "browser_unavailable"
        if discovered and original:
            return original + board_markup()
        return None


def configured_boards(page):
    """Explicit board configuration, including JSON escaped by React streaming."""
    text = page.replace(r'\"', '"').replace(r'\/', '/')
    links = []
    if "greenhouse" in text.lower():
        tokens = re.findall(r'''["'](?:boardToken|greenhouseBoardToken|greenhouse_board_token)["']\s*:\s*["']([A-Za-z0-9_-]+)["']''', text)
        links += ["https://job-boards.greenhouse.io/" + token for token in tokens]
    for region, token in re.findall(r"https://boards-api(\.eu)?\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)/jobs", text):
        links.append("https://job-boards" + region + ".greenhouse.io/" + token)
    return list(dict.fromkeys(links))


def review_category(failures, evidence):
    if any(f.startswith(("http_", "embedded_board:http_", "browser_http_")) or f in {
        "tls_certificate_error", "dns_error", "robots_blocked", "timeout", "browser_timeout", "URLError", "TimeoutError", "RemoteDisconnected"} for f in failures):
        return "access_or_network"
    if "pagination_unknown" in failures or "pagination_not_advancing" in failures:
        return "pagination_contract"
    if "unreadable_job_detail" in failures:
        return "description_contract"
    if any(f in failures for f in ("invalid_public_record", "invalid_public_listing", "invalid_job_schema")):
        return "adapter_contract"
    if not evidence:
        return "unsupported_format"
    return "coverage_pending" if failures else "complete"

# Shared markup families: selectors are for engines, not individual companies.
HTML_FORMATS = (
    ("hiringroom", ".vacancyDataContainer", "a[href*='/jobs/get_vacancy/']", ".card-vacancy", ".name__vacancy", ".card-text"),
    ("avature", ".article--result", ".article__header__text__title a[href]", ".article--result", None, ".article__header__text__subtitle"),
    ("wordpress_jobs", "li.job_listing", "li.job_listing a[href]", "li.job_listing", ".position h3, h3", ".location"),
    ("applicantpro", ".job-listing, .listing-title", ".job-listing a[href], .listing-title a[href]", ".job-listing", "h3, h2", ".location"),
    ("job_cards", ".job-card, .job-list-item, .job-posting, .position-card, .job-tile", ".job-card a[href], .job-list-item a[href], .job-posting a[href], .position-card a[href], .job-tile a[href]", ".job-card, .job-list-item, .job-posting, .position-card, .job-tile", "h2, h3, .job-title", ".location, .job-location, .position-location"),
)


def _location(value):
    """Only vacancy geography, never stringify complete API/person objects."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(filter(None, (_location(v) for v in value)))
    if isinstance(value, dict):
        return ", ".join(dict.fromkeys(_location(value[k]) for k in (
            "name", "city", "state", "country", "countryCode", "location", "location_name") if value.get(k)))
    return ""


def metadata_posting(source, url, meta, adapter):
    location = meta.get("location") or ""
    codes = sorted(set(country_codes(location) + re.findall(
        r"\b(CR|US|MX|CO|PA|GT|SV|HN|NI|DO|EC|PE|CL|AR|UY|BR)\b", location)))
    for code in meta.get("country_codes", []):
        if isinstance(code, str) and re.fullmatch(r"[A-Z]{2}", code):
            codes = sorted(set(codes + [code]))
    remote = meta.get("remote")
    return JobPosting(source=source.name, external_id=str(meta.get("id") or urlsplit(url).path.rstrip("/")),
        title=meta["title"], company=source.company.name, url=url,
        description=html_to_text(meta.get("description") or "")[:20000], location=location or None,
        country=codes[0] if len(codes) == 1 else None,
        remote=remote if isinstance(remote, bool) else remote_from_location(location),
        posted_at=parse_date(meta.get("posted_at")),
        raw={"platform": "public_web", "extraction": adapter, "country_codes": codes})


def _record(item, source, page_url, adapter):
    if not isinstance(item, dict):
        return None
    item = item.get("data", item)
    if not isinstance(item, dict) or item.get("internal") is True or item.get("published") is False:
        return None
    title = next((item[k] for k in ("title", "jobTitle", "job_name", "name") if isinstance(item.get(k), str)), "")
    if not title:
        return None
    target = next((item[k] for k in ("url", "jobUrl", "absolute_url", "job_url", "apply_link") if isinstance(item.get(k), str)), "")
    if adapter == "jibe":
        slug = str(item.get("slug") or "")
        if not re.fullmatch(r"[\w-]+", slug):
            return None
        target = f"https://{source.host}/jobs/{slug}"
    elif adapter == "recruiterflow" and target and not target.startswith(("/", "https://")):
        target = "/" + target
    elif adapter == "phenom" and not target:
        identity = str(item.get("jobId") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]+", identity) and "/search-results" in page_url:
            prefix = urlsplit(page_url).path.split("/search-results", 1)[0]
            slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
            target = f"{prefix}/job/{identity}/{slug}"
    if not target:
        return None
    target = urljoin(page_url, target)
    if not source._same_site(target):
        return None
    description = "\n\n".join(item[k] for k in ("description", "jobDescription", "description_html", "qualifications", "responsibilities") if isinstance(item.get(k), str))
    location = ", ".join(dict.fromkeys(filter(None, (_location(item.get(k)) for k in (
        "locations", "location", "location_name", "full_location", "multipleLocations", "multi_location", "additional_locations", "country")))))
    code = item.get("country_code") or item.get("countryCode")
    remote = item.get("remote")
    if not isinstance(remote, bool):
        remote = remote_from_location(str(item.get("remote_type") or item.get("workplaceType") or ""))
    return target, {"title": title.strip(), "description": description, "location": location,
        "id": item.get("job_id") or item.get("jobId") or item.get("id") or item.get("req_id"),
        "posted_at": item.get("posted_date") or item.get("postedAt") or item.get("postedDate") or item.get("datePosted") or item.get("last_opened"),
        "remote": remote, "country_codes": [code] if isinstance(code, str) else []}


def _result(adapter, records=(), following=(), failures=(), bootstrap=False):
    return {"adapter": adapter, "candidates": dict(records), "following": list(following), "failures": list(failures), "bootstrap": bootstrap}


def shared_listing(source, query, page, page_url, form=None):
    """One dispatcher for public feeds, embedded state and reusable HTML families."""
    soup = BeautifulSoup(page, "html.parser")
    if (soup.select_one("#jobSearch_j_id1, #job-table") and "successfactors" in page.lower()
        and not soup.select_one("a.jobTitle-link, #searchresults, #noresults")
        and query.residence_country == "CR" and not dict(parse_qsl(urlsplit(page_url).query)).get("locationsearch")):
        return _result("successfactors_js", following=[_url(page_url, locationsearch="Costa Rica", q="", startrow=0)], bootstrap=True)
    match = re.search(r"\bphApp\.ddo\s*=\s*", page)
    if match and "/search-results" in urlsplit(page_url).path:
        try:
            data = json.JSONDecoder().raw_decode(page[match.end():])[0]["eagerLoadRefineSearch"]
            jobs = data["data"]["jobs"]
            # Prefer the complete underlying ATS board when the publisher exposes it.
            ats = any(re.search(r"\.myworkdayjobs\.com|greenhouse\.io|jobs\.lever\.co", str(job.get("applyUrl", ""))) for job in jobs if isinstance(job, dict))
            if not ats and data.get("status") == 200 and isinstance(jobs, list) and isinstance(data.get("totalHits"), int):
                records = [_record(item, source, page_url, "phenom") for item in jobs]
                offset = int(dict(parse_qsl(urlsplit(page_url).query)).get("from", 0))
                following, failures = [], []
                if jobs and not any(records):
                    failures.append("invalid_public_record")
                if offset + len(jobs) < data["totalHits"]:
                    if jobs:
                        following.append(_url(page_url, **{"from": offset + len(jobs), "s": 1}))
                    else:
                        failures.append("pagination_not_advancing")
                return _result("phenom", filter(None, records), following, failures)
        except (ValueError, KeyError, TypeError):
            pass
    if "/jobs/getVacanciesForPortal/" in urlsplit(page_url).path and page.lstrip().startswith("{"):
        data = json.loads(page)
        if data.get("result") != "success" or not isinstance(data.get("data"), dict):
            return _result("hiringroom", failures=["invalid_public_listing"])
        data = data["data"]
        if not isinstance(data.get("htmlContent"), str) or not isinstance(data.get("pagination"), str):
            return _result("hiringroom", failures=["invalid_public_listing"])
        if data.get("total_vacancies") == 0:
            return _result("hiringroom")
        wrapped = '<div class="vacancyDataContainer">' + data["htmlContent"] + '</div><nav class="paginationLinks">' + data["pagination"] + '</nav>'
        parsed = shared_listing(source, query, wrapped, page_url, form=form)
        return parsed
    if soup.select_one(".job_listings") and not soup.select_one("li.job_listing"):
        match = re.search(r"(?:var\s+)?job_manager_ajax_filters\s*=\s*", page)
        if match:
            try:
                config = json.JSONDecoder().raw_decode(page[match.end():])[0]
                target = urljoin(page_url, config["ajax_url"].replace("%%endpoint%%", "get_listings"))
                if source._same_site(target):
                    return _result("wordpress_jobs", following=[_url(target, page=1, search_location="Costa Rica" if query.residence_country == "CR" else "")], bootstrap=True)
            except (ValueError, KeyError, TypeError):
                pass
    if "get_listings" in page_url and page.lstrip().startswith("{"):
        data = json.loads(page)
        if isinstance(data.get("html"), str) and isinstance(data.get("max_num_pages"), int):
            parsed = shared_listing(source, query, data["html"], page_url)
            if parsed is None and data.get("found_jobs") is False:
                parsed = _result("wordpress_jobs")
            if parsed is not None:
                number = int(dict(parse_qsl(urlsplit(page_url).query)).get("page", 1))
                if number < data["max_num_pages"]:
                    parsed["following"].append(_url(page_url, page=number + 1))
                return parsed
    # iCIMS/Jibe search portals expose complete descriptions in an anonymous feed.
    if "window.searchConfig" in page and "window._jibe" in page:
        params = {"page": 1, "limit": 50}
        if query.residence_country == "CR":
            params["location"] = "Costa Rica"
        return _result("jibe", following=[f"https://{source.host}/api/jobs?" + urlencode(params)], bootstrap=True)
    if urlsplit(page_url).path == "/api/jobs" and page.lstrip().startswith("{"):
        data = json.loads(page)
        if not isinstance(data.get("jobs"), list) or not isinstance(data.get("totalCount"), int):
            return None
        records = [_record(item, source, page_url, "jibe") for item in data["jobs"]]
        params = dict(parse_qsl(urlsplit(page_url).query))
        number, size = int(params.get("page", 1)), int(params.get("limit", 50))
        following, failures = [], []
        if data["jobs"] and not any(records):
            failures.append("invalid_public_record")
        if number == 1 and data["jobs"]:
            size = min(size, len(data["jobs"]))
        if (number - 1) * size + len(data["jobs"]) < data["totalCount"]:
            if not data["jobs"]:
                failures.append("pagination_not_advancing")
            else:
                following = [_url(page_url, page=number + 1, limit=size)]
        return _result("jibe", filter(None, records), following, failures)

    match = re.search(r"window\.jobsList\s*=\s*", page)
    if match:
        try:
            data = json.JSONDecoder().raw_decode(page[match.end():])[0]
            groups = data["location"]
            records = {}
            for label, jobs in groups:
                for item in jobs:
                    record = _record(item, source, page_url, "recruiterflow")
                    if record:
                        target, meta = record
                        meta["location"] = str(label)
                        if target in records:
                            meta["location"] = records[target]["location"] + ", " + meta["location"]
                        records[target] = meta
            return _result("recruiterflow", records.items())
        except (ValueError, KeyError, TypeError):
            return None

    # Public React Query hydration. Query keys must identify vacancies, not
    # sessions, locations, translations or employee profiles.
    node = soup.select_one("script#__NEXT_DATA__")
    if node:
        try:
            props = json.loads(node.string or "{}").get("props", {}).get("pageProps", {})
            for entry in props.get("dehydratedState", {}).get("queries", []):
                if not re.search(r"job-posts|jobpostings|vacancies|job-search", str(entry.get("queryKey", "")), re.I):
                    continue
                data = entry["state"]["data"]
                if not isinstance(data.get("items"), list) or not isinstance(data.get("totalItems"), int):
                    continue
                records = [_record(item, source, page_url, "next_public_jobs") for item in data["items"]]
                failures = ["pagination_unknown"] if len(data["items"]) < data["totalItems"] else []
                if data["items"] and not any(records):
                    failures.append("invalid_public_record")
                return _result("next_public_jobs", filter(None, records), failures=failures)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

    for adapter, signal, selector, card_selector, title_selector, location_selector in HTML_FORMATS:
        if not soup.select_one(signal):
            continue
        anchors = soup.select(selector)
        if not anchors:
            if adapter == "hiringroom" and re.search(r'''totalVacancies\s*=\s*["']?0(?:["';\s])''', page):
                return _result(adapter)
            # A loading container is not an explicitly empty vacancy list.
            continue
        records = {}
        card_ids = {id(node) for node in soup.select(card_selector)}
        for anchor in anchors:
            target = urljoin(page_url, anchor.get("href") or "")
            if not source._same_site(target) or re.search(r"/candidates/new|/(?:login|apply|save|favorites|bookmark|share|register|alerts)(?:/|$)", urlsplit(target).path, re.I):
                continue
            if adapter == "job_cards" and not (re.search(r"/(?:jobs?|positions?|vacancies|openings?|o|careers?)/.+", urlsplit(target).path, re.I)
                or set(dict(parse_qsl(urlsplit(target).query))) & {"jobId", "job_id", "pid", "gh_jid"}):
                continue
            card = next((parent for parent in anchor.parents if id(parent) in card_ids), None)
            if card is None:
                card = anchor.select_one(card_selector) or anchor.parent
            title_node = card.select_one(title_selector) if title_selector else None
            title = (title_node or anchor).get_text(" ", strip=True)
            if not title:
                continue
            if not keyword_match(title, query):
                source._observe(target, "keywords")
                continue
            location = card.select_one(location_selector)
            records.setdefault(target, {"title": title, "location": location.get_text(" ", strip=True) if location else ""})
        following = []
        if adapter == "hiringroom" and soup.select_one(".paginationLinks .dd_siguiente"):
            current = soup.select_one(".paginationLinks .actual")
            portal = re.search(r'''(?:var|let|const)\s+typePortal\s*=\s*["'](external|microsite)["']''', page)
            portal_type = (form or {}).get("typePortal") or (portal.group(1) if portal else None)
            if current and current.get_text(strip=True).isdigit() and portal_type == "external":
                number = int(current.get_text(strip=True)) + 1
                following.append({"url": f"https://{source.host}/jobs/getVacanciesForPortal/{number}",
                                  "form": {"selectedPage": number, "typePortal": portal_type}})
            else:
                return _result(adapter, records.items(), failures=["pagination_unknown"])
        for node in soup.select("a[rel='next'][href], .pagination a.next[href], .paginationNextLink[href]"):
            target = urljoin(page_url, node["href"])
            if source._same_site(target):
                following.append(target)
        return _result(adapter, records.items(), following)
    return None


def phenom_links(page):
    """Read the JSON sent to anonymous visitors, without executing JavaScript."""
    match = re.search(r"\bphApp\.ddo\s*=\s*", page)
    if not match:
        return [], False
    try:
        data = json.JSONDecoder().raw_decode(page[match.end():])[0]["eagerLoadRefineSearch"]
        jobs = data["data"]["jobs"]
        if data.get("status") != 200 or not isinstance(jobs, list):
            return [], False
        return [item["applyUrl"] for item in jobs if isinstance(item, dict) and isinstance(item.get("applyUrl"), str)], data.get("totalHits") == 0 and not jobs
    except (ValueError, KeyError, TypeError):
        return [], False


def _url(base, **params):
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def oracle(source, query):
    """Anonymous requisitions on the employer's external Candidate Experience site."""
    match = re.search(r"(/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_]+))", source.company.careers_url)
    if not match:
        return None
    site_path, site = match.groups()
    root = "/hcmRestApi/resources/latest/"
    found, failures, received = {}, [], 0
    key = hashlib.sha256(repr((source.company.careers_url, query)).encode()).hexdigest()
    previous = getattr(source, "_previous", None) or {}
    start = previous.get("pending_offset") if previous.get("checkpoint_safe") and previous.get("cursor_key") == key else None
    last_page_ids = set(previous.get("last_page_ids", [])) if isinstance(start, int) and start >= 0 else set()
    start = start if isinstance(start, int) and start >= 0 else 0
    pending_offset = None
    facet_id = None
    try:
        probe = json.loads(source._get(root + "recruitingCEJobRequisitions?" + urlencode({
            "onlyData": "true", "expand": "locationsFacet", "finder": f"findReqs;siteNumber={site},limit=1,offset=0"})))["items"][0]
        # Use the site's published facet IDs instead of guessing IDs or relying
        # on workLocationCountryCode, which some tenants silently ignore.
        labels = {"CR": "costa rica", "MX": "mexico", "CO": "colombia", "US": "united states"}
        for facet in probe.get("locationsFacet", []):
            if str(facet.get("Name", "")).lower() == labels.get(query.residence_country):
                facet_id = str(facet["Id"])
                break
    except Exception as error:
        return FetchResult(status="partial", details={"adapter": "oracle_candidate_experience",
            "cursor_key": key, "pending_offset": start, "resumed": start > 0,
            "failures": [source._failure(error)]})
    for offset in range(start, start + 250, 25):
        finder = f"findReqs;siteNumber={site},limit=25,offset={offset},sortBy=POSTING_DATES_DESC"
        if facet_id:
            finder += ",selectedLocationsFacet=" + facet_id
        try:
            payload = json.loads(source._get(root + "recruitingCEJobRequisitions?" + urlencode({
                "onlyData": "true", "expand": "requisitionList", "finder": finder})))
            listing = payload["items"][0]
            rows = listing["requisitionList"]
            if not isinstance(rows, list) or not isinstance(listing.get("TotalJobsCount"), int):
                raise ValueError("Listado público de Oracle inválido")
        except Exception as error:
            failures.append(source._failure(error))
            pending_offset = offset
            break
        received += len(rows)
        page_ids = {str(item.get("Id")) for item in rows if item.get("Id") is not None}
        if page_ids and page_ids <= last_page_ids:
            failures.append("pagination_not_advancing")
            pending_offset = offset
            break
        for item in rows:
            title, identity = item.get("Title") or "", str(item.get("Id") or "")
            if not identity:
                source._observe(repr(item), "invalid")
                continue
            if not keyword_match(title, query):
                source._observe(identity, "keywords")
                continue
            location = ", ".join(str(item.get(k) or "") for k in ("PrimaryLocation", "PrimaryLocationCountry"))
            codes = country_codes(location)
            country = item.get("PrimaryLocationCountry")
            if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country):
                codes = sorted(set(codes + [country]))
            remote = remote_from_location(str(item.get("WorkplaceType") or ""))
            preview = JobPosting(source=source.name, title=title, url="", location=location,
                country=codes[0] if len(codes) == 1 else None, remote=remote, raw={"country_codes": codes})
            if not eligible(preview, query):
                source._observe(identity, "location")
                continue
            try:
                detail = json.loads(source._get(root + "recruitingCEJobRequisitionDetails?" + urlencode({
                    "onlyData": "true", "finder": f"ById;Id={identity},siteNumber={site}"})))["items"][0]
                # Retain only public vacancy fields, never contact or application data.
                text = "\n\n".join(html_to_text(detail.get(k) or "") for k in (
                    "ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr"))
                if not text.strip():
                    raise ValueError("Descripción pública de Oracle ausente")
                expiry = parse_date(item.get("PostingEndDate"))
                if expiry and (expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc):
                    source._observe(identity, "expired")
                    continue
                source._observe(identity, "accepted")
                found[identity] = JobPosting(source=source.name, external_id=identity, title=title,
                    company=source.company.name, url=f"https://{source.host}{site_path}/job/{identity}",
                    description=text[:20000], location=location, country=preview.country, remote=remote,
                    posted_at=parse_date(item.get("PostedDate")),
                    raw={"platform": "public_web", "extraction": "oracle_candidate_experience", "country_codes": codes})
            except Exception as error:
                source._observe(identity, "invalid")
                failures.append("detail:" + source._failure(error))
                pending_offset = offset
        if pending_offset is not None:
            break
        last_page_ids = page_ids
        if offset + len(rows) >= listing["TotalJobsCount"]:
            break
        if not rows:
            failures.append("inconsistent_total")
            break
    else:
        failures.append("page_limit_reached")
        pending_offset = offset + 25
    return FetchResult(found.values(), status="partial" if failures else "success", details={
        "adapter": "oracle_candidate_experience", "coverage": "residence_country", "received": received,
        "cursor_key": key, "pending_offset": pending_offset, "resumed": start > 0, "cycle_complete": not failures,
        "last_page_ids": sorted(last_page_ids),
        "failures": list(dict.fromkeys(failures))})


def amazon(source, query):
    """The public search response includes descriptions; no detail crawl is needed."""
    country = {"CR": "CRI", "MX": "MEX", "CO": "COL", "US": "USA"}.get(query.residence_country)
    if not country:
        return None
    found, failures, received = {}, [], 0
    for offset in range(0, 1000, 100):
        try:
            payload = json.loads(source._get("/en/search.json?" + urlencode({
                "offset": offset, "result_limit": 100, "sort": "recent", "country": country})))
        except Exception as error:
            if offset == 0:
                raise
            failures.append(source._failure(error))
            break
        if payload.get("error") or not isinstance(payload.get("jobs"), list):
            raise ValueError("Listado público de Amazon inválido")
        rows = payload["jobs"]
        received += len(rows)
        for item in rows:
            title = item.get("title") or ""
            identity = str(item.get("id_icims") or item.get("id") or item.get("job_path") or repr(item))
            if not keyword_match(title, query):
                source._observe(identity, "keywords")
                continue
            text = "\n\n".join(html_to_text(item.get(k) or "") for k in (
                "description", "basic_qualifications", "preferred_qualifications"))
            url = urljoin(source.company.careers_url, item.get("job_path") or "")
            if not source._same_site(url) or not re.search(r"/jobs/\d+/", urlsplit(url).path) or not text:
                source._observe(identity, "invalid")
                failures.append("invalid_record")
                continue
            location = str(item.get("location") or item.get("normalized_location") or "")
            code = item.get("country_code") or ""
            codes = country_codes(location)
            if code == country:
                codes = sorted(set(codes + [query.residence_country]))
            date = parse_date(item.get("posted_date"))
            if date is None:
                try:
                    date = datetime.strptime(item.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            posting = JobPosting(source=source.name, external_id=str(item.get("id_icims") or item.get("id") or url),
                title=title, company=item.get("company_name") or source.company.name, url=url,
                description=text[:20000], location=location, country=codes[0] if len(codes) == 1 else None,
                remote=remote_from_location(location), posted_at=date,
                raw={"platform": "public_web", "extraction": "amazon_search", "country_codes": codes})
            if source._accept(posting, query, identity):
                found[posting.external_id] = posting
        if len(rows) < 100 or (isinstance(payload.get("hits"), int) and offset + len(rows) >= payload["hits"]):
            break
    else:
        failures.append("page_limit_reached")
    return FetchResult(found.values(), status="partial" if failures else "success", details={
        "adapter": "amazon_search", "coverage": "residence_country", "received": received, "failures": failures})


def html_listing(source, query, page, page_url):
    """Recognize explicit public listing containers, including verified empty lists."""
    soup = BeautifulSoup(page, "html.parser")
    if soup.select_one("a.jobTitle-link, #searchresults, #noresults"):
        adapter, selector = "successfactors", "a.jobTitle-link"
    elif source.host == "www.careers-page.com" or source.host == "careers-page.com":
        if not soup.select_one("a[href*='/job/'], #id_position_name__icontains"):
            return None
        adapter, selector = "careers_page", "a[href*='/job/']"
    elif source.host == "jobs.jobvite.com" and soup.select_one(".jv-page-container, .jv-job-list, .jv-job-listing, .jv-careersite"):
        adapter, selector = "jobvite", "a[href*='/job/']"
    elif source.host.endswith(".icims.com") and soup.select_one(".iCIMS_JobsTable, .iCIMS_SubHeader_Jobs"):
        adapter, selector = "icims", ".iCIMS_JobsTable .title a[href]"
    elif source.host.endswith(".applytojob.com") and soup.select_one("#resumator-jobs, #resumator-job-list, .resumator-job-title, .list-group-item, #job_listings"):
        adapter, selector = "jazzhr", "a[href*='/apply/']"
    else:
        return None

    # A country search avoids crawling hundreds of irrelevant corporate roles.
    # Only apply a parameter supported by the recognized SuccessFactors template.
    if adapter == "successfactors" and query.residence_country == "CR" and not dict(parse_qsl(urlsplit(page_url).query)).get("locationsearch"):
        page_url = _url(page_url, locationsearch="Costa Rica", q="", startrow=0)
        soup = BeautifulSoup(source._get(page_url), "html.parser")
        if not soup.select_one("#searchresults, a.jobTitle-link, #noresults"):
            rendered = render_public_page(source, page_url, str(soup))
            if rendered is None:
                return None
            soup = BeautifulSoup(rendered, "html.parser")
            if not soup.select_one("#searchresults, a.jobTitle-link, #noresults"):
                return None

    # An empty container can be a JavaScript loading shell. Require actual
    # records or a published empty-state message before treating it as a list.
    if not soup.select(selector) and not re.search(
        r"no (?:open positions|jobs found|jobs available|results found|matching jobs)"
        r"|no (?:hay|tenemos) (?:vacantes|ofertas)|ninguna (?:oferta|vacante)",
        soup.get_text(" ", strip=True), re.I):
        return None

    candidates = {}
    for anchor in soup.select(selector):
        target = urljoin(page_url, anchor.get("href") or "")
        path = urlsplit(target).path
        if re.search(r"/apply/?$", path) and adapter != "jazzhr":
            continue
        if adapter == "jazzhr" and not re.search(r"/apply/(?:jobs/details/[A-Za-z0-9]+|[A-Za-z0-9]+/[^/]+)", path):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or title.lower() in {"apply now", "learn more", "view all jobs"}:
            continue
        if not source._same_site(target):
            continue
        if not keyword_match(title, query):
            source._observe(target, "keywords")
            continue
        card = anchor.find_parent("tr") or anchor.find_parent("li") or anchor.parent
        location = ""
        loc = card.select_one(".jobLocation, .jv-job-list-location, .location, .header.left")
        if loc:
            location = loc.get_text(" ", strip=True)
        candidates.setdefault(target, {"title": title, "location": location})

    following = []
    for anchor in soup.select("a[rel='next'], a.paginationItemLast, a[href*='startrow='], a.iCIMS_Paginator_Next"):
        target = urljoin(page_url, anchor.get("href") or "")
        if not source._same_site(target):
            continue
        if adapter == "successfactors":
            current = int(dict(parse_qsl(urlsplit(page_url).query)).get("startrow", 0))
            offset = dict(parse_qsl(urlsplit(target).query)).get("startrow", "0")
            if not offset.isdigit() or int(offset) <= current:
                continue
        following.append(target)
    if adapter == "icims":
        for anchor in soup.select("a[href]"):
            if "next page of results" in anchor.get_text(" ", strip=True).lower():
                target = urljoin(page_url, anchor["href"])
                if source._same_site(target):
                    following.append(target)
    # Return the next page, not the last page link, when both are present.
    if adapter == "successfactors" and following:
        following = [min(following, key=lambda u: int(dict(parse_qsl(urlsplit(u).query)).get("startrow", 0)))]
    return adapter, candidates, following, page_url


def detail_posting(source, page, url, metadata, adapter):
    soup = BeautifulSoup(page, "html.parser")
    if adapter == "phenom":
        match = re.search(r"\bphApp\.ddo\s*=\s*", page)
        if match:
            try:
                data = json.JSONDecoder().raw_decode(page[match.end():])[0]
                for key in ("jobDetail", "jobDetailV2", "eagerLoadJobDetail"):
                    detail = data.get(key, {}).get("data", {})
                    detail = detail.get("job", detail)
                    description = detail.get("description") or detail.get("jobDescription")
                    if isinstance(description, str) and description.strip():
                        return metadata_posting(source, url, {**metadata, "description": description}, adapter)
            except (ValueError, TypeError, AttributeError):
                pass
    node = soup.select_one("[itemprop='description'], .jobdescription, .jv-job-detail-description, "
        ".iCIMS_JobContent, #job-description, #job_description, #jobDescription, .job_description, "
        ".job-description, .job-details-body, .job-description-content, .job-detail-description, "
        ".position-description, [data-ui='job-description'], .description")
    if node is None:
        # Verified vacancy links may use ordinary CMS rich text instead of a
        # dedicated description class. Require actual requirements/responsibilities.
        main = soup.select_one("main, [role='main'], .article__content__view, .entry-content")
        if main:
            for extra in main.select("form, nav, footer, aside, script, style, .related-jobs, .job-recommendations"):
                extra.decompose()
            text = main.get_text(" ", strip=True)
            signs = sum(bool(re.search(pattern, text, re.I)) for pattern in (
                r"responsibilit|responsabilidad|funciones", r"requirement|requisito|qualification", r"experience|experiencia"))
            if signs >= 2 and len(text) >= 150:
                node = main
    if node is None or not node.get_text(strip=True):
        raise ValueError("Descripción pública ausente")
    location = metadata.get("location") or ""
    node_location = soup.select_one("[itemprop='jobLocation'], .jobLocation, .jv-job-detail-location, .job-location, .position-location, [data-testid='job-location']")
    if node_location:
        location = node_location.get_text(" ", strip=True)
    if adapter == "careers_page":
        marker = soup.select_one(".fa-map-marker, .fa-map-marker-alt")
        if marker:
            location = marker.parent.get_text(" ", strip=True)
    return metadata_posting(source, url, {**metadata, "description": str(node), "location": location}, adapter)


def audit_catalog(output, only_review=False, company_ids=None):
    """Read-only audit of all configured sites, grouped by host to limit traffic."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path
    from collections import Counter
    from .companies import load_companies
    from .public_company import PublicCompanySource
    from ..config import load_search_config, load_dotenv, database_url
    from ..db import Database

    load_dotenv()
    query, _ = load_search_config()
    companies = [c for c in load_companies() if c.platform == "public_web"]
    if company_ids:
        companies = [c for c in companies if c.id in company_ids]
    if only_review:
        load_dotenv()
        db = Database(database_url())
        try:
            rows = db.query("select source, details from runs where source like ? order by finished_at desc", ("public_web:%",))
            latest = {}
            for row in rows:
                if row["source"] not in latest:
                    latest[row["source"]] = json.loads(row["details"] or "{}")
            companies = [c for c in companies if latest.get("public_web:" + c.id, {}).get("requires_adapter_review")]
        finally:
            db.close()
    groups = {}
    for company in companies:
        groups.setdefault(urlsplit(company.careers_url).hostname, []).append(company)

    def check_group(group):
        rows = []
        for company in group:
            source = PublicCompanySource(company)
            try:
                result = source.fetch(query)
                details = result.details
                rows.append({"id": company.id, "name": company.name, "url": company.careers_url,
                    "status": result.status, "found": len(result),
                    "requires_adapter_review": details.get("requires_adapter_review", False),
                    "review_category": details.get("review_category") or review_category(details.get("failures", []), True),
                    "adapters": details.get("adapters") or [details.get("adapter", "structured_or_embedded")],
                    "browser_pages": details.get("browser_pages", 0), "browser_issue": details.get("browser_issue"),
                    "failures": details.get("failures", []), "pending_pages": details.get("pending_pages", 0)})
            except Exception as error:
                rows.append({"id": company.id, "name": company.name, "url": company.careers_url,
                    "status": "failed", "found": 0, "requires_adapter_review": True,
                    "failures": [source._failure(error)], "review_category": "access_or_network"})
        return rows

    rows = []
    file = Path(output)
    file.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(check_group, group) for group in groups.values()]):
            chunk = future.result()
            rows.extend(chunk)
            file.write_text(json.dumps({"read_only": True, "reviewed": len(rows), "total": len(companies),
                "counts": dict(Counter(r["status"] for r in rows)), "companies": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
            for row in chunk:
                print(f"{len(rows)}/{len(companies)} {row['id']}: {row['status']}, {row['found']} ofertas, {row['review_category']}", flush=True)
    return rows


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Auditar lectores públicos sin guardar ofertas ni checkpoints.")
    parser.add_argument("--audit", action="store_true", required=True)
    parser.add_argument("--only-review", action="store_true", help="Solo fuentes cuyo último run requiere revisar el adaptador")
    parser.add_argument("--output", default="generated/public-web-audit.json")
    parser.add_argument("--companies", help="IDs separados por comas para una auditoría acotada")
    args = parser.parse_args()
    audit_catalog(args.output, args.only_review, set(args.companies.split(",")) if args.companies else None)
