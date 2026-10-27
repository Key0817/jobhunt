"""Fuente: Get on Board (API pública v0, sin autenticación).
Referencia: https://www.getonbrd.com/api-doc.html
"""
from __future__ import annotations

import gzip
import html
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..models import JobPosting, SearchQuery, normalize
from .base import Source, FetchResult, register

log = logging.getLogger("jobbot")

BASE_URL = "https://www.getonbrd.com/api/v0"
# Categorías de TI. Los identificadores salen de GET /api/v0/categories.
CATEGORIES = (
    "programacion", "desarrollo-mobile", "sysadmin-devops-qa", "technical-support",
    "data-science-analytics", "cybersecurity", "machine-learning-ai",
)
PER_PAGE = 100            # el máximo que permite la API es 120
MAX_PAGES = 6             # tope de páginas por categoría y pasada
PAUSE_SECONDS = 1.0       # espera entre peticiones, para no castigar su servidor
RETRIES = 2               # reintentos ante errores temporales (429, 5xx, cortes de red)
TIMEOUT_SECONDS = 10      # máximo sin recibir datos del servidor (una petición normal tarda 1-3 s)
MAX_RUNTIME_SECONDS = 240 # presupuesto total de tiempo por corrida
MAX_CONSECUTIVE_FAILURES = 3   # si fallan 3 consultas seguidas, el servicio está mal: paramos
USER_AGENT = "jobbot-personal/0.1"
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
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
        # Un solo país reconocido -> lo usamos. Varios o ninguno ("Remoto") -> None.
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

    def __init__(self, fetch_json=None, sleep=time.sleep, pause: float = PAUSE_SECONDS,
                 clock=time.monotonic, max_runtime: float = MAX_RUNTIME_SECONDS,
                 categories=CATEGORIES):
        # fetch_json, sleep y clock se pueden reemplazar en los tests (sin red ni esperas).
        self._fetch_json = fetch_json or self._http_get_json
        self._sleep = sleep
        self._pause = pause
        self._clock = clock
        self._max_runtime = max_runtime
        self._categories = tuple(categories)

    @staticmethod
    def _decode(raw: bytes, encoding: str | None) -> dict:
        if encoding and encoding.lower() == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw)

    @staticmethod
    def _http_get_json(url: str) -> dict:
        # Pedimos gzip: el JSON se comprime muchísimo y hay menos riesgo de cortes y timeouts.
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                     "Accept-Encoding": "gzip"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return GetOnBoardSource._decode(response.read(), response.headers.get("Content-Encoding"))

    @staticmethod
    def _url(category: str, page: int, country: str | None = None) -> str:
        params = [("page", page), ("per_page", PER_PAGE), ("lang", "es"), ("expand[]", "company")]
        if country:
            params.append(("country_code", country))
        return f"{BASE_URL}/categories/{urllib.parse.quote(category)}/jobs?{urllib.parse.urlencode(params)}"

    def _get(self, url: str) -> dict:
        """GET con reintentos y espera creciente ante errores temporales."""
        for attempt in range(RETRIES + 1):
            try:
                return self._fetch_json(url)
            except urllib.error.HTTPError as exc:
                temporary = exc.code == 429 or exc.code >= 500
                if not temporary or attempt == RETRIES:
                    raise
            except NETWORK_ERRORS:
                if attempt == RETRIES:
                    raise
            wait = 2 ** (attempt + 1)
            log.warning("Get on Board: error temporal, reintento en %ss", wait)
            self._sleep(wait)
        raise RuntimeError("inalcanzable")

    def _tasks(self, query: SearchQuery) -> list[tuple[str, str | None]]:
        """Qué consultar, en orden de valor.

        Pasada A (con country_code): ofertas que aceptan a alguien que vive en tu país.
        Pasada B (sin filtro): ofertas 100% remotas, que se pueden hacer desde cualquier lugar.
        """
        tasks: list[tuple[str, str | None]] = []
        if query.residence_country:
            tasks += [(c, query.residence_country.lower()) for c in self._categories]
        tasks += [(c, None) for c in self._categories]
        return tasks

    def fetch(self, query: SearchQuery) -> list[JobPosting]:
        found: dict[str, JobPosting] = {}
        failed_tasks: list[str] = []
        capped_tasks: list[str] = []
        consecutive_failures = 0
        last_error: Exception | None = None
        first_request = True
        deadline = self._clock() + self._max_runtime
        out_of_time = False

        for category, country in self._tasks(query):
            label = f"{category} [{country or 'global'}]"
            task_failed = False
            for page in range(1, MAX_PAGES + 1):
                if self._clock() >= deadline:
                    out_of_time = True
                    break
                if not first_request:
                    self._sleep(self._pause)
                first_request = False

                started = time.monotonic()
                try:
                    payload = self._get(self._url(category, page, country))
                except NETWORK_ERRORS + (ValueError,) as exc:
                    # Una consulta que falla no debe tirar abajo las demás ni
                    # hacernos perder lo ya recolectado.
                    task_failed = True
                    last_error = exc
                    failed_tasks.append(label)
                    log.warning("Get on Board: se omite %s (pág. %d): %s: %s",
                                label, page, type(exc).__name__, exc)
                    break

                jobs = payload.get("data") or []
                kept = 0
                for job in jobs:
                    posting = to_posting(job)
                    if posting is None:
                        continue
                    # Con country_code, el servidor ya filtró por ti. Sin filtro, solo nos sirven
                    # las 100% remotas: las presenciales, híbridas o remotas de otro país no.
                    if country is None and posting.raw.get("remote_modality") != "fully_remote":
                        continue
                    found.setdefault(posting.external_id or posting.url, posting)
                    kept += 1
                log.info("Get on Board: %s pág. %d -> %d ofertas, %d útiles (%.1f s)",
                         label, page, len(jobs), kept, time.monotonic() - started)

                total_pages = (payload.get("meta") or {}).get("total_pages")
                if not jobs:
                    break
                if isinstance(total_pages, int):
                    if page >= total_pages:
                        break
                elif len(jobs) < PER_PAGE:
                    break
                if page == MAX_PAGES:
                    capped_tasks.append(label)

            if out_of_time:
                log.warning("Get on Board: se agotó el presupuesto de tiempo (%d s) en %s; "
                            "las consultas restantes no se hicieron", self._max_runtime, label)
                break
            consecutive_failures = consecutive_failures + 1 if task_failed else 0
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.warning("Get on Board: %d consultas seguidas fallaron; se detiene la corrida",
                            consecutive_failures)
                break

        if failed_tasks and not found:
            raise last_error  # no se obtuvo nada: que el pipeline lo registre como fallo
        if failed_tasks:
            log.warning("Get on Board: corrida parcial; fallaron: %s", ", ".join(dict.fromkeys(failed_tasks)))
        log.info("Get on Board: %d ofertas únicas", len(found))
        partial = bool(failed_tasks or capped_tasks or out_of_time)
        return FetchResult(found.values(), status="partial" if partial else "success", details={
            "failed_tasks": list(dict.fromkeys(failed_tasks)),
            "page_limited_tasks": capped_tasks,
            "time_limit_reached": out_of_time,
        })
