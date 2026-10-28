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
            if not identity or not keyword_match(title, query):
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
                    continue
                found[identity] = JobPosting(source=source.name, external_id=identity, title=title,
                    company=source.company.name, url=f"https://{source.host}{site_path}/job/{identity}",
                    description=text[:20000], location=location, country=preview.country, remote=remote,
                    posted_at=parse_date(item.get("PostedDate")),
                    raw={"platform": "public_web", "extraction": "oracle_candidate_experience", "country_codes": codes})
            except Exception as error:
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
            if not keyword_match(title, query):
                continue
            text = "\n\n".join(html_to_text(item.get(k) or "") for k in (
                "description", "basic_qualifications", "preferred_qualifications"))
            url = urljoin(source.company.careers_url, item.get("job_path") or "")
            if not source._same_site(url) or not re.search(r"/jobs/\d+/", urlsplit(url).path) or not text:
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
            if eligible(posting, query):
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
        if not source._same_site(target) or not keyword_match(title, query):
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
    node = soup.select_one("[itemprop='description'], .jobdescription, .jv-job-detail-description, "
        ".iCIMS_JobContent, #job-description, #job_description, .job-description, .job-details-body, .description")
    if node is None or not node.get_text(strip=True):
        raise ValueError("Descripción pública ausente")
    location = metadata.get("location") or ""
    node_location = soup.select_one("[itemprop='jobLocation'], .jobLocation, .jv-job-detail-location")
    if node_location:
        location = node_location.get_text(" ", strip=True)
    if adapter == "careers_page":
        marker = soup.select_one(".fa-map-marker, .fa-map-marker-alt")
        if marker:
            location = marker.parent.get_text(" ", strip=True)
    codes = country_codes(location)
    # iCIMS uses ISO country prefixes in its public location labels.
    codes = sorted(set(codes + re.findall(r"\b(CR|US|MX|CO|PA|GT|SV|HN|NI|DO|EC|PE|CL|AR|UY|BR)\b", location)))
    return JobPosting(source=source.name, external_id=urlsplit(url).path.rstrip("/"),
        title=metadata["title"], company=source.company.name, url=url,
        description=html_to_text(str(node))[:20000], location=location or None,
        country=codes[0] if len(codes) == 1 else None, remote=remote_from_location(location),
        raw={"platform": "public_web", "extraction": adapter, "country_codes": codes})
