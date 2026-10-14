"""Fuente: Get on Board (API pública v0, sin autenticación).
Referencia: https://www.getonbrd.com/api-doc.html
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..models import JobPosting, SearchQuery, normalize
from .base import Source, register

log = logging.getLogger("jobbot")

API_URL = "https://www.getonbrd.com/api/v0/search/jobs"
PER_PAGE = 100            # el máximo que permite la API es 120
MAX_PAGES = 3             # tope de páginas por palabra clave
PAUSE_SECONDS = 1.0       # espera entre peticiones, para no castigar el servidor 
RETRIES = 2               # reintentos ante errores temporales (429 o 5xx)
USER_AGENT = "jobbot-personal/0.1"
MAX_DESCRIPTION_CHARS = 20_000

# Nombre del país (sin tildes ni mayúsculas, ver models.normalize) -> código ISO.
COUNTRY_CODES = {
    "costa rica": "CR", "mexico": "MX", "colombia": "CO", "panama": "PA",
    "guatemala": "GT", "el salvador": "SV", "honduras": "HN", "nicaragua": "NI",
    "republica dominicana": "DO", "dominican republic": "DO", "ecuador": "EC",
    "peru": "PE", "chile": "CL", "argentina": "AR", "uruguay": "UY",
    "brasil": "BR", "brazil": "BR", "bolivia": "BO", "paraguay": "PY",
    "venezuela": "VE", "estados unidos": "US", "united states": "US",
    "espana": "ES", "spain": "ES",
}
DESCRIPTION_FIELDS = ("description", "projects", "functions", "desirable", "benefits")
RAW_FIELDS = (
    "countries", "remote_modality", "remote_zone", "min_salary", "max_salary",
    "category_name", "lang", "seniority", "modality", "tags",
    "location_cities", "location_regions",
)

_BLOCK_TAGS = re.compile(r"</?(?:br|p|div|li|ul|ol|h[1-6])\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")


def html_to_text(value: str | None) -> str:
    """Convierte el HTML de la oferta en texto plano, conservando los saltos de línea."""
    if not value:
        return ""
    text = _BLOCK_TAGS.sub("\n", value)
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _company_name(company) -> str:
    try:
        return (company["data"]["attributes"]["name"] or "").strip()
    except (KeyError, TypeError):
        return ""


def to_posting(job: dict) -> JobPosting | None:
    """Convierte una oferta de la API en un JobPosting. Devuelve None si le falta lo esencial."""
    attrs = job.get("attributes") or {}
    title = (attrs.get("title") or "").strip()
    url = (job.get("links") or {}).get("public_url")
    if not title or not url:
        return None

    names = [c for c in (attrs.get("countries") or []) if isinstance(c, str)]
    codes = sorted({COUNTRY_CODES[normalize(n)] for n in names if normalize(n) in COUNTRY_CODES})

    posted_at = None
    published = attrs.get("published_at")
    if isinstance(published, (int, float)):
        try:
            posted_at = datetime.fromtimestamp(published, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            posted_at = None

    parts = (html_to_text(attrs.get(field)) for field in DESCRIPTION_FIELDS)
    description = "\n\n".join(p for p in parts if p)
    remote = attrs.get("remote")
    job_id = job.get("id")

    return JobPosting(
        source="getonboard",
        external_id=str(job_id) if job_id is not None else None,
        title=title,
        url=url,
        company=_company_name(attrs.get("company")),
        country=codes[0] if len(codes) == 1 else None,
        location=", ".join(names) or None,
        remote=remote if isinstance(remote, bool) else None,
        description=description[:MAX_DESCRIPTION_CHARS],
        posted_at=posted_at,
        raw={field: attrs.get(field) for field in RAW_FIELDS},
    )


@register
class GetOnBoardSource(Source):
    name = "getonboard"

    def __init__(self, fetch_json=None, sleep=time.sleep, pause: float = PAUSE_SECONDS):
        # fetch_json y sleep se pueden reemplazar en los tests (sin red y sin esperas).
        self._fetch_json = fetch_json or self._http_get_json
        self._sleep = sleep
        self._pause = pause

    @staticmethod
    def _http_get_json(url: str) -> dict:
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    @staticmethod
    def _url(keyword: str, page: int) -> str:
        params = [("query", keyword), ("page", page), ("per_page", PER_PAGE),
                  ("lang", "es"), ("expand[]", "company")]
        return f"{API_URL}?{urllib.parse.urlencode(params)}"

    def _get(self, url: str) -> dict:
        """GET con reintentos y espera creciente ante errores temporales."""
        for attempt in range(RETRIES + 1):
            try:
                return self._fetch_json(url)
            except urllib.error.HTTPError as exc:
                temporary = exc.code == 429 or exc.code >= 500
                if not temporary or attempt == RETRIES:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == RETRIES:
                    raise
            wait = 2 ** (attempt + 1)
            log.warning("Get on Board: error temporal, reintento en %ss", wait)
            self._sleep(wait)
        raise RuntimeError("inalcanzable")

    def fetch(self, query: SearchQuery) -> list[JobPosting]:
        found: dict[str, JobPosting] = {}
        first_request = True
        for keyword in query.keywords:
            for page in range(1, MAX_PAGES + 1):
                if not first_request:
                    self._sleep(self._pause)
                first_request = False

                payload = self._get(self._url(keyword, page))
                jobs = payload.get("data") or []
                for job in jobs:
                    posting = to_posting(job)
                    if posting:
                        found.setdefault(posting.external_id or posting.url, posting)

                total_pages = (payload.get("meta") or {}).get("total_pages")
                if not jobs:
                    break
                if isinstance(total_pages, int):
                    if page >= total_pages:
                        break
                elif len(jobs) < PER_PAGE:
                    break
        log.info("Get on Board: %d ofertas únicas", len(found))
        return list(found.values())