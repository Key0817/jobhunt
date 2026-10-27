"""Expand a CR Computrabajo search alert when the public website permits access."""
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .base import FetchResult
from .mail_parser import job_link, _title, _posting
from .mail_urls import search_link
from .public_web import PublicWebSource


class ComputrabajoAlertSearch(PublicWebSource):
    host = "cr.computrabajo.com"
    max_runtime = 30
    MAX_PAGES = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cache = {}
        self.blocked = None

    def fetch(self, query):
        raise NotImplementedError("Use fetch_search con el destino de la alerta")

    def fetch_search(self, url):
        target = search_link(url)
        if not target or urlsplit(target).hostname != self.host:
            return FetchResult([], status="partial", details={"reason": "unsupported_search_domain"})
        if target in self.cache:
            return self.cache[target]
        if self.blocked:
            return FetchResult([], status="partial", details={"reason": self.blocked})
        self._start()
        queue, seen, found, failures = [target], set(), {}, []
        while queue and len(seen) < self.MAX_PAGES:
            page_url = queue.pop(0)
            seen.add(page_url)
            try:
                soup = BeautifulSoup(self._get(page_url), "html.parser")
                links_seen = 0
                for anchor in soup.select("a[href]"):
                    link = job_link(urljoin(page_url, anchor["href"]), "computrabajo")
                    title = _title(anchor.get_text(" ", strip=True))
                    if not link or not title:
                        continue
                    links_seen += 1
                    card = anchor.find_parent("article") or anchor.find_parent(class_="box_offer") or anchor.parent
                    posting = _posting("computrabajo", link[0], link[1], title, card.get_text("\n", strip=True), None)
                    found[link[0]] = posting
                if not links_seen and not soup.select_one(".box_offer, #offers-grid, #offersGrid"):
                    failures.append("unrecognized_search_html")
                for anchor in soup.select('a[rel="next"][href]'):
                    next_url = urljoin(page_url, anchor["href"])
                    parts = urlsplit(next_url)
                    if parts.scheme == "https" and parts.hostname == self.host and parts.path.startswith("/ofertas-de-trabajo") and next_url not in seen:
                        queue.append(next_url)
            except HTTPError as error:
                reason = f"http_{error.code}"
                failures.append(reason)
                if error.code in (401, 403, 429):
                    self.blocked = reason
                break
            except PermissionError:
                self.blocked = "robots_blocked"
                failures.append(self.blocked)
                break
            except Exception as error:
                failures.append(type(error).__name__)
                break
        if queue:
            failures.append("page_limit_reached")
        result = FetchResult(found.values(), status="partial" if failures else "success", details={
            "reason": failures[0] if failures else "parsed", "failures": failures})
        self.cache[target] = result
        return result
