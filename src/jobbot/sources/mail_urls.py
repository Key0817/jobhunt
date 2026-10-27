"""Decode known alert wrappers locally; bound optional navigation to provider domains."""
import base64
import json
import re
import time
import zlib
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


DOMAINS = {"linkedin": ("linkedin.com",), "indeed": ("indeed.com",),
           "computrabajo": ("computrabajo.com", "computrabajo.co.cr"),
           "trabajosdiarios": ("trabajosdiarios.co.cr", "trabajosdiarios.com"),
           "dynamitejobs": ("dynamitejobs.com",), "elempleo": ("elempleo.com",)}


def owned(url, provider):
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and not parts.username and not parts.password and parts.port in (None, 80, 443) and any(
            parts.hostname == suffix or (parts.hostname or "").endswith("." + suffix) for suffix in DOMAINS.get(provider, ()))
    except ValueError:
        return False


def decode64(value):
    if len(value) > 100000:
        raise ValueError("Wrapper demasiado grande")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def unwrap(url, provider):
    """Payloads are untrusted data, never authentication; validate every decoded destination."""
    current = url.strip()
    for _ in range(4):
        target = None
        try:
            parts = urlsplit(current)
            host = (parts.hostname or "").lower()
            query = parse_qs(parts.query)
            if owned(current, provider):
                target = next((query[key][0] for key in ("url", "destination", "dest", "redirect", "redirectUrl") if query.get(key) and query[key][0].startswith(("https://", "http://"))), None)
            if provider == "indeed" and host == "cts.indeed.com" and parts.path.startswith("/v3/"):
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = inflater.decompress(decode64(parts.path.split("/v3/", 1)[1]), 65537)
                if len(raw) > 65536 or inflater.unconsumed_tail:
                    return current
                payload = json.loads(raw)
                target = payload.get("u") or payload.get("url")
            elif provider == "dynamitejobs" and host in ("t.dripemail2.com", "t.dripemail.com") and parts.path.startswith("/c/"):
                segments = parts.path.rsplit("/", 1)[-1].split(".")
                if len(segments) == 3:
                    target = json.loads(decode64(segments[1])).get("url")
            if not isinstance(target, str) or not owned(target, provider) or target == current:
                return current
            current = target
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, zlib.error):
            return current
    return current


def search_link(url):
    try:
        target = unwrap(url, "computrabajo")
        parts = urlsplit(target)
        if not owned(target, "computrabajo") or parts.hostname not in ("cr.computrabajo.com", "www.computrabajo.co.cr", "computrabajo.co.cr"):
            return None
        if not re.fullmatch(r"/ofertas-de-trabajo/?", parts.path):
            return None
        query = parse_qs(parts.query)
        # Omit recipient IDs and campaign parameters from public application links.
        clean = {key: query[key][0] for key in ("q", "prov", "p", "page", "by") if query.get(key)}
        return urlunsplit(("https", parts.hostname, "/ofertas-de-trabajo/", urlencode(clean), ""))
    except ValueError:
        return None


def job_candidate(url, provider):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if provider == "indeed" and owned(url, provider):
        return parts.path.startswith(("/rc/clk", "/pagead/clk", "/viewjob", "/v3/")) or "jk=" in parts.query or (parts.hostname == "engage.indeed.com" and parts.path.startswith("/f/a/"))
    if provider == "dynamitejobs":
        return owned(url, provider) and "/remote-job/" in parts.path
    return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlertLinkResolver:
    """Navigate job click redirects only. Never open feedback/unsubscribe/account links."""
    def __init__(self, deadline, clock=time.monotonic, max_requests=100):
        self.deadline, self.clock, self.max_requests = deadline, clock, max_requests
        self.requests = 0
        self.cache = {}

    def __call__(self, url, provider):
        from .mail_parser import job_link
        key = (provider, url)
        if key in self.cache:
            return self.cache[key]
        current = unwrap(url, provider)
        result = None
        for _ in range(4):
            if job_link(current, provider):
                result = current
                break
            if not owned(current, provider) or not job_candidate(current, provider) or self.requests >= self.max_requests or self.clock() >= self.deadline:
                break
            self.requests += 1
            try:
                request = Request(current, headers={"User-Agent": "jobbot-personal/0.3"})
                with build_opener(NoRedirect()).open(request, timeout=max(0.1, min(6, self.deadline - self.clock()))) as response:
                    current = response.geturl()
                    if job_link(current, provider):
                        result = current
                    elif "html" in (response.headers.get("Content-Type") or ""):
                        from bs4 import BeautifulSoup
                        raw = response.read(256001)
                        if len(raw) <= 256000:
                            soup = BeautifulSoup(raw, "html.parser")
                            for node in soup.select('link[rel="canonical"][href], meta[property="og:url"][content]'):
                                target = urljoin(current, node.get("href") or node.get("content") or "")
                                if job_link(target, provider):
                                    result = target
                                    break
                    break
            except HTTPError as error:
                if error.code not in (301, 302, 303, 307, 308) or not error.headers.get("Location"):
                    break
                destination = urljoin(current, error.headers["Location"])
                if not owned(destination, provider):
                    break
                current = unwrap(destination, provider)
            except (OSError, ValueError):
                break
        self.cache[key] = result
        return result
