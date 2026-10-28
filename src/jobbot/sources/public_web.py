"""Bounded, polite access to public pages, respecting robots.txt and host boundaries."""
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, urljoin, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .base import Source


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or (new.hostname or "").removeprefix("www.") != (old.hostname or "").removeprefix("www."):
            raise ValueError("La bolsa redirige fuera del host público autorizado")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def robots_allowed(text, url):
    """Interpret wildcard and terminal-$ rules, using longest matching Allow/Disallow."""
    groups, agents, rules = [], [], []
    for raw in text.splitlines() + ["User-agent: __end__"]:
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in ("allow", "disallow") and agents and value:
            rules.append((key, value))
    specific = [rules for agents, rules in groups if any(agent != "*" and agent in "jobbot-personal" for agent in agents)]
    applicable = specific or [rules for agents, rules in groups if "*" in agents]
    parts = urlsplit(url)
    target = parts.path + ("?" + parts.query if parts.query else "")
    matches = []
    for rules in applicable:
        for key, pattern in rules:
            terminal = pattern.endswith("$")
            pattern = pattern[:-1] if terminal else pattern
            expression = "^" + re.escape(pattern).replace(r"\*", ".*") + ("$" if terminal else "")
            if re.search(expression, target):
                matches.append((len(pattern.replace("*", "")), key == "allow"))
    return max(matches)[1] if matches else True


class PublicWebSource(Source):
    host = ""
    max_runtime = 120

    def __init__(self, fetch_text=None, sleep=time.sleep, clock=time.monotonic):
        self._fetch_text = fetch_text
        self._sleep, self._clock = sleep, clock
        self._robots = None
        self._last_request = None
        self._deadline = float("inf")

    def _start(self):
        self._deadline = self._clock() + self.max_runtime

    def _request(self, url, form=None):
        if urlsplit(url).hostname != self.host or urlsplit(url).scheme != "https":
            raise ValueError("URL fuera de la bolsa autorizada")
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError("Presupuesto de tiempo de la bolsa agotado")
        if self._fetch_text:
            return self._fetch_text(url) if form is None else self._fetch_text(url, form)
        if self._last_request is not None:
            self._sleep(max(0, 0.6 - (self._clock() - self._last_request)))
        self._last_request = self._clock()
        request = Request(url, data=urlencode(form, doseq=True).encode() if form is not None else None,
            headers={"User-Agent": "jobbot-personal/0.3", "Accept": "text/html,application/json,application/xml",
                     **({"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {})})
        with build_opener(SameHostRedirect()).open(request, timeout=min(15, remaining)) as response:
            raw = response.read(8_000_001)
            charset = response.headers.get_content_charset() or "utf-8"
        if len(raw) > 8_000_000:
            raise ValueError("Respuesta pública demasiado grande")
        return raw.decode(charset, errors="replace")

    def _get(self, path, form=None):
        url = urljoin(f"https://{self.host}/", path)
        if self._robots is None:
            try:
                self._robots = self._request(f"https://{self.host}/robots.txt")
            except HTTPError as error:
                if error.code not in (404, 410):
                    raise
                self._robots = ""
        if not robots_allowed(self._robots, url):
            raise PermissionError("robots.txt no permite consultar esta ruta")
        return self._request(url, form)

