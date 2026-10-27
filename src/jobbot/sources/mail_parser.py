"""Deterministic parsing of job-alert emails. Never follows links or invokes AI."""
from __future__ import annotations

from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting
from .mail_urls import unwrap, owned, search_link, job_candidate
from .companies import country_codes, remote_from_location


@dataclass
class ParsedAlert:
    postings: list[JobPosting]
    provider: str | None
    status: str
    reason: str = ""
    search_links: list[str] = field(default_factory=list)
    unresolved_links: int = 0


def _domain(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def sender_provider(sender: str) -> str | None:
    address = parseaddr(sender)[1]
    host = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    for provider, domains in (("linkedin", ("linkedin.com",)), ("indeed", ("indeed.com",)),
                              ("computrabajo", ("computrabajo.com", "computrabajo.co.cr")),
                              ("trabajosdiarios", ("trabajosdiarios.com", "trabajosdiarios.co.cr")),
                              ("elempleo", ("elempleo.com",)),
                              ("dynamitejobs", ("dynamitejobs.com",))):
        if any(_domain(host, domain) for domain in domains):
            return provider
    return None


def job_link(url: str, provider: str, _depth: int = 0) -> tuple[str, str] | None:
    """Only canonical, provider-owned job links. Unknown trackers stay unsupported."""
    try:
        url = unwrap(url, provider)
        if not owned(url, provider):
            return None
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
        if provider == "dynamitejobs" and _domain(host, "dynamitejobs.com"):
            if re.fullmatch(r"/company/[A-Za-z0-9_-]+/remote-job/[A-Za-z0-9_-]+/?", parts.path):
                path = parts.path.rstrip("/")
                return hashlib.sha256(path.encode()).hexdigest(), "https://dynamitejobs.com" + path
        if provider == "elempleo" and host in ("elempleo.com", "www.elempleo.com"):
            match = re.fullmatch(r"/cr/ofertas-trabajo/[^/]+/([1-9][0-9]*)/?", parts.path)
            if match:
                return "cr:" + match.group(1), "https://www.elempleo.com" + parts.path.rstrip("/")
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
    generic = {"view in browser", "unsubscribe", "desde ayer", "editar", "términos", "terms", "privacy policy", "manage preferences", "email preferences", "no", "sí", "iniciar sesión", "centro de asistencia", "más información", "ver oferta", "ver empleo", "ver vacante", "ver trabajo", "view job", "view jobs", "apply", "apply now", "postularme", "aplicar", "ver más", "ver mas", "learn more", "view details"}
    if re.search(r"^(?:este empleo es|editar perfil|pausar estos|cancelar|buscar empleos|ver todos los empleos|ver m[aá]s empleos|see all jobs|browse jobs|en los [uú]ltimos|administrar alertas|gestiona tus|haz clic aqu[ií]|manage |unsubscribe|view in browser|privacy|pol[ií]tica)", value, re.I):
        return None
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
    after = clean[1:] if clean and clean[0] == title else []
    if provider == "indeed":
        after = [line for line in after if not re.fullmatch(r"[0-5](?:[.,]\d)?", line)]
    if provider in ("linkedin", "indeed") and after and not company:
        first = after[0]
        if not re.match(r"(?:Empresa|Company|Ubicaci[oó]n|Location)\s*:", first, re.I):
            if "·" in first:
                company, _, location = first.partition("·")
                company, location = company.strip()[:200], location.strip()[:250] or location
            elif len(after) >= 3 and after[1] == "·":
                company, location = first[:200], after[2][:250]
            elif len(after) >= 2 and not re.search(r"solicitud|post[uú]lese|hace \d|view job|ver oferta", first, re.I):
                company = first[:200]
                if not re.search(r"solicitud|post[uú]lese|hace \d|salario|por mes", after[1], re.I):
                    location = after[1][:250]
    if provider == "dynamitejobs" and " - " in title:
        title, company = title.rsplit(" - ", 1)
        title, company = title.strip(), company.strip()[:200]
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


def _html_candidates(content, provider, resolve_url=None):
    soup = BeautifulSoup(content, "html.parser")
    for node in soup.select("script, style, head"):
        node.decompose()
    records, searches, unresolved = [], [], set()
    potential = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor["href"])
        target = search_link(href) if provider == "computrabajo" else None
        if target:
            searches.append(target)
            continue
        link = job_link(href, provider)
        decoded = unwrap(href, provider)
        label = anchor.get_text(" ", strip=True)
        opaque_newsletter_job = provider == "dynamitejobs" and decoded.startswith(("https://t.dripemail2.com/c/", "https://t.dripemail.com/c/")) and " - " in label
        if not link and (job_candidate(decoded, provider) or opaque_newsletter_job):
            # Resolve only plausible vacancy titles/buttons, never feedback or account actions.
            if _title(label) or label.lower() in ("ver empleo", "ver oferta", "view job", "más información"):
                destination = resolve_url(decoded, provider) if resolve_url else None
                link = job_link(destination, provider) if destination else None
                if not link:
                    unresolved.add(decoded)
                    potential[id(anchor)] = (anchor, "unresolved:" + hashlib.sha256(href.encode()).hexdigest())
        if link:
            records.append((anchor, link))
            potential[id(anchor)] = (anchor, link[0])
    candidates = []
    ancestry = {id(anchor): {id(parent) for parent in anchor.parents} for anchor, _ in potential.values()}
    detected = {link[0] for _, link in records}
    for anchor, link in records:
        lines = [line.strip() for line in anchor.get_text("\n", strip=True).splitlines() if line.strip()]
        label_title = anchor.get_text(" ", strip=True) if not anchor.find(["div", "table", "p", "h1", "h2", "h3", "h4"]) else (lines[0] if lines else "")
        title = _title(label_title)
        inferred_title = title is None
        if title is None:
            # Single-vacancy match emails may link a button instead of the heading.
            for parent in anchor.parents:
                if parent.name in ("body", "html", "[document]"):
                    break
                identities = {identity for other, identity in potential.values() if other is parent or id(parent) in ancestry[id(other)]}
                if identities - {link[0]}:
                    break
                heading = parent.find(["h1", "h2", "h3", "h4", "strong"])
                if heading and _title(heading.get_text(" ", strip=True)):
                    title = _title(heading.get_text(" ", strip=True))
                    break
        if not title:
            continue
        card = anchor
        for parent in anchor.parents:
            if parent.name in ("body", "html", "[document]"):
                break
            identities = {identity for other, identity in potential.values() if other is parent or id(parent) in ancestry[id(other)]}
            if identities - {link[0]}:
                break
            text = parent.get_text("\n", strip=True)
            leading = [line.strip() for line in text.splitlines() if line.strip()]
            while leading and leading[0].lower() in ("ver empleo", "ver oferta", "view job", "apply now", "más información"):
                leading.pop(0)
            if leading and leading[0].startswith(title):
                card = parent
        snippet = card.get_text("\n", strip=True)
        if title in snippet:
            snippet = snippet[snippet.index(title):]
        candidates.append((int(inferred_title), link, title, snippet))
    candidates.sort(key=lambda item: item[0])
    return [(link, title, snippet) for _, link, title, snippet in candidates], detected, list(dict.fromkeys(searches)), len(unresolved)


def parse_alert(raw: bytes, resolve_url=None) -> ParsedAlert:
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
    candidates, detected, searches, unresolved = [], set(), [], 0
    if body.get_content_type() == "text/html":
        candidates, detected, searches, unresolved = _html_candidates(content, provider, resolve_url)
    else:
        for url in re.findall(r'https?://[^\s<>\"]+', content):
            link = job_link(url.rstrip(".,)"), provider)
            if link:
                detected.add(link[0])
            target = search_link(url) if provider == "computrabajo" else None
            if target:
                searches.append(target)
        for block in re.split(r"\n\s*\n", content):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            title = _title(lines[0])
            if title is None:
                continue
            links = [job_link(url.rstrip(".,)"), provider) for url in re.findall(r'https?://[^\s<>\"]+', block)]
            links = [link for link in links if link]
            if len({link[0] for link in links}) == 1:
                candidates.append((links[0], title, block))
    postings = {}
    for (identifier, url), title, snippet in candidates:
        postings.setdefault(identifier, _posting(provider, identifier, url, title, snippet, message.get("Date")))
    complete = bool(postings) and not (detected - postings.keys()) and not searches and not unresolved
    return ParsedAlert(list(postings.values()), provider, "parsed" if complete else "needs_review",
                       "" if complete else "search_link" if searches else "unrecognized_template_or_links", list(dict.fromkeys(searches)), unresolved)
