"""Deterministic parsing of job-alert emails. Never follows links or invokes AI."""
from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, unquote, urlsplit

from ..models import JobPosting
from .companies import country_codes, remote_from_location


@dataclass
class ParsedAlert:
    postings: list[JobPosting]
    provider: str | None
    status: str
    reason: str = ""


def _domain(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def sender_provider(sender: str) -> str | None:
    address = parseaddr(sender)[1]
    host = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    for provider, domains in (("linkedin", ("linkedin.com",)), ("indeed", ("indeed.com",)),
                              ("computrabajo", ("computrabajo.com", "computrabajo.co.cr")),
                              ("trabajosdiarios", ("trabajosdiarios.com", "trabajosdiarios.co.cr"))):
        if any(_domain(host, domain) for domain in domains):
            return provider
    return None


def job_link(url: str, provider: str, _depth: int = 0) -> tuple[str, str] | None:
    """Only canonical, provider-owned job links. Unknown trackers stay unsupported."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https"):
            return None
        query = parse_qs(parts.query)
        # Decode only a known provider wrapper, then validate the destination again.
        if provider == "computrabajo" and host == "go.computrabajo.com" and parts.path.startswith("/go/"):
            target = (query.get("url") or [None])[0]
            if target and _depth < 3:
                return job_link(target, provider, _depth + 1)
            return None
        if provider == "linkedin" and _domain(host, "linkedin.com"):
            match = re.search(r"/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d+)(?:/|$)", parts.path)
            identifier = match.group(1) if match else (query.get("currentJobId") or [None])[0]
            if identifier and str(identifier).isdigit():
                return str(identifier), f"https://www.linkedin.com/jobs/view/{identifier}"
        if provider == "indeed" and _domain(host, "indeed.com"):
            identifier = (query.get("jk") or query.get("vjk") or [None])[0]
            if identifier and re.fullmatch(r"[A-Za-z0-9_-]{6,64}", identifier):
                return identifier, f"https://www.indeed.com/viewjob?jk={identifier}"
        if provider == "trabajosdiarios" and any(_domain(host, domain) for domain in ("trabajosdiarios.com", "trabajosdiarios.co.cr")):
            match = re.fullmatch(r"/trabajo/([1-9][0-9]*)(/[^/?#]+)?/?", parts.path)
            if match and parts.username is None and parts.password is None and parts.port in (None, 80, 443):
                # Costa Rica's older hostname redirects to the current local domain.
                host = host.removeprefix("www.")
                if host == "cr.trabajosdiarios.com":
                    host = "trabajosdiarios.co.cr"
                identifier = f"{host}:{match.group(1)}"
                return identifier, f"https://{host}/trabajo/{match.group(1)}{(match.group(2) or '').rstrip('/')}"
        if provider == "computrabajo" and any(_domain(host, domain) for domain in ("computrabajo.com", "computrabajo.co.cr")):
            path = unquote(parts.path).rstrip("/")
            if "/ofertas-de-trabajo/oferta-de-trabajo-de-" in path:
                match = re.search(r"-([A-Fa-f0-9]{16,64})$", path)
                identifier = match.group(1).lower() if match else hashlib.sha256(path.encode()).hexdigest()
                return identifier, f"https://{host}{parts.path.rstrip('/')}"
    except ValueError:
        pass
    return None


class AlertHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments, self.links = [], []
        self.position = 0
        self.active = None
        self.hidden = 0

    def append(self, value):
        self.fragments.append(value)
        self.position += len(value)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("br", "p", "div", "tr", "td", "li", "h1", "h2", "h3"):
            self.append("\n")
        if tag == "a":
            self.active = (dict(attrs).get("href", ""), [], self.position)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == "a" and self.active:
            href, text, start = self.active
            self.links.append((href, "".join(text), start, self.position))
            self.active = None
        if tag in ("p", "div", "tr", "td", "li", "h1", "h2", "h3"):
            self.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.append(data)
            if self.active:
                self.active[1].append(data)


def _title(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value).strip()
    generic = {"ver oferta", "ver empleo", "ver vacante", "ver trabajo", "view job", "view jobs", "apply", "apply now", "postularme", "aplicar", "ver más", "ver mas", "learn more", "view details"}
    is_button = re.match(r"^(ver (?:la )?(?:oferta|empleo|vacante|trabajo)|view (?:job|details)|apply(?: now)?|aplicar|postularme)(?:\s|$)", value, re.I)
    if not 5 <= len(value) <= 220 or value.lower() in generic or is_button or value.startswith(("http:", "https:")):
        return None
    return value


def _posting(provider, identifier, url, title, snippet, alert_date):
    lines = [re.sub(r"\s+", " ", line).strip() for line in snippet.splitlines() if line.strip()]
    clean = []
    for line in lines:
        if re.search(r"unsubscribe|darse de baja|cancelar suscrip|gestionar alertas|manage alerts|privacy policy|política de privacidad|this email was sent|este correo|has recibido|you received|copyright|©", line, re.I):
            break
        clean.append(line)
    company = ""
    location = None
    for line in clean[:8]:
        match = re.match(r"(?:empresa|company)\s*:\s*(.+)", line, re.I)
        if match:
            company = match.group(1)[:200]
        match = re.match(r"(?:ubicaci[oó]n|location)\s*:\s*(.+)", line, re.I)
        if match:
            location = match.group(1)[:250]
        elif location is None and (country_codes(line) or re.search(r"\b(remote|remoto|latam|worldwide)\b", line, re.I)):
            location = line[:250]
    if provider == "trabajosdiarios":
        # The entire card is an anchor: title, company, pin/location, then badges.
        if len(clean) >= 3 and clean[0] == title and "📍" in clean[2]:
            company = clean[1][:200]
            marker = clean[2].replace("📍", "").strip()
            location = marker or (clean[3][:250] if len(clean) > 3 else None)
    codes = country_codes(location or "")
    if provider == "trabajosdiarios" and urlsplit(url).hostname == "trabajosdiarios.co.cr" and not codes:
        codes = ["CR"]
    body = re.sub(r"https?://\S+", "", "\n".join(clean))
    body = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[correo omitido]", body)[:1400]
    return JobPosting(
        source=f"{provider}_email", external_id=identifier, title=title, url=url,
        company=company, country=codes[0] if len(codes) == 1 else None, location=location,
        remote=remote_from_location(location or ""), posted_at=None,
        description="Resumen de alerta por correo. Descripción incompleta; confirmar requisitos y ubicación en la oferta original.\n" + body,
        raw={"description_incomplete": True, "location_needs_review": not bool(codes),
             "country_codes": codes, "alert_date": str(alert_date or "")[:100], "origin": "email_alert"},
    )


def parse_alert(raw: bytes) -> ParsedAlert:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    provider = sender_provider(str(message.get("From", "")))
    if provider is None:
        return ParsedAlert([], None, "ignored", "unrecognized_sender")
    body = message.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ParsedAlert([], provider, "needs_review", "missing_body")
    content = body.get_content()
    if not isinstance(content, str):
        return ParsedAlert([], provider, "needs_review", "unsupported_body")
    candidates = []
    detected = set()
    if body.get_content_type() == "text/html":
        parser = AlertHTML()
        parser.feed(content)
        text = "".join(parser.fragments)
        links = [(job_link(href, provider), label, start, end) for href, label, start, end in parser.links]
        links = [item for item in links if item[0] is not None]
        detected.update(item[0][0] for item in links)
        for index, (link, label, start, end) in enumerate(links):
            if provider == "trabajosdiarios":
                card = text[start:end]
                lines = [line.strip() for line in card.splitlines() if line.strip()]
                title = _title(lines[0]) if lines else None
                if title:
                    candidates.append((link, title, card))
                continue
            title = _title(label)
            if title:
                following = next((item[2] for item in links[index + 1:] if item[0][0] != link[0]), len(text))
                candidates.append((link, title, text[start:min(following, end + 1600)]))
    else:
        # Plain-text cards: title on the first line of a block, then metadata/link.
        for url in re.findall(r"https?://[^\s<>\"]+", content):
            link = job_link(url.rstrip(".,)"), provider)
            if link:
                detected.add(link[0])
        for block in re.split(r"\n\s*\n", content):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            title = _title(lines[0])
            if title is None:
                continue
            block_links = [job_link(url.rstrip(".,)"), provider) for url in re.findall(r"https?://[^\s<>\"]+", block)]
            block_links = [link for link in block_links if link]
            if len({link[0] for link in block_links}) == 1:
                candidates.append((block_links[0], title, block))
    postings = {}
    for (identifier, url), title, snippet in candidates:
        postings.setdefault(identifier, _posting(provider, identifier, url, title, snippet, message.get("Date")))
    complete = bool(postings) and not (detected - postings.keys())
    return ParsedAlert(list(postings.values()), provider, "parsed" if complete else "needs_review",
                       "" if complete else "unrecognized_template_or_links")
