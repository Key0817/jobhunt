"""Configuración y utilidades compartidas para bolsas públicas de empresas."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import yaml

from ..models import JobPosting, SearchQuery, normalize
from .base import Source
from .getonboard import COUNTRY_CODES

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLATFORMS = {"greenhouse", "smartrecruiters", "lever", "ashby", "workday", "workable", "bamboohr", "breezy", "recruitee", "public_web"}


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    platform: str
    board_id: str
    careers_url: str
    include_global_remote: bool = True
    api_region: str = "us"


def load_companies(path: str | Path | None = None) -> list[Company]:
    file = Path(path or os.environ.get("JOBBOT_COMPANIES", PROJECT_ROOT / "config" / "companies.yml"))
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
        raise ValueError("companies.yml debe contener una lista companies.")
    result, ids, boards = [], set(), set()
    for row in data["companies"]:
        if not isinstance(row, dict) or not isinstance(row.get("enabled", False), bool):
            raise ValueError("Cada empresa debe ser un objeto con enabled booleano.")
        if not row.get("enabled", False):
            continue
        for key in ("id", "name", "platform", "board_id", "careers_url"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"Falta {key} en una empresa activa.")
        if row["platform"] not in PLATFORMS:
            raise ValueError(f"Plataforma no soportada: {row['platform']}")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", row[key]) for key in ("id", "board_id")):
            raise ValueError("id y board_id solo pueden contener letras, números, guiones y guion bajo.")
        region = row.get("api_region", "us")
        if region not in ("us", "eu"):
            raise ValueError("api_region debe ser us o eu.")
        host = urlsplit(row["careers_url"]).hostname
        if row["platform"] == "workday" and not (host and re.fullmatch(r"[a-zA-Z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com", host)):
            raise ValueError("Workday requiere una URL pública myworkdayjobs.com válida.")
        board = (row["platform"], row["board_id"], host if row["platform"] == "workday" else region)
        if row["id"] in ids or board in boards:
            raise ValueError("Empresa o board_id duplicado en companies.yml.")
        ids.add(row["id"])
        boards.add(board)
        include_global = row.get("include_global_remote", True)
        if not isinstance(include_global, bool):
            raise ValueError("include_global_remote debe ser booleano.")
        result.append(Company(**{key: row[key] for key in ("id", "name", "platform", "board_id", "careers_url")},
                              include_global_remote=include_global, api_region=region))
    return result


def country_codes(location: str) -> list[str]:
    text = normalize(location)
    return sorted({code for name, code in COUNTRY_CODES.items() if re.search(r"\b" + re.escape(name) + r"\b", text)})


def remote_from_location(location: str) -> bool | None:
    text = normalize(location)
    if re.search(r"\b(hybrid|hibrido|onsite|on site|presencial)\b", text):
        return False
    return True if re.search(r"\b(remote|remoto|remota)\b", text) else None


def keyword_match(title: str, query: SearchQuery) -> bool:
    text = normalize(title)
    return not query.keywords or any(
        re.search(r"\b" + re.escape(normalize(term)) + r"\b", text)
        for term in query.keywords if normalize(term)
    )


def eligible(posting: JobPosting, query: SearchQuery) -> bool:
    targets = {query.residence_country} if query.residence_country else set(query.countries)
    codes = set(posting.raw.get("country_codes", []))
    if posting.country:
        codes.add(posting.country)
    if targets & codes:
        return True
    if not query.include_remote_latam or posting.remote is not True:
        return False
    location = normalize(posting.location or "")
    # Remote alone does not establish eligibility from Costa Rica.
    return bool(re.search(r"\b(latam|latin america|latinamerica|latinoamerica|america latina|worldwide|global|anywhere|americas)\b", location))


def parse_date(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class CompanySource(Source):
    def __init__(self, company: Company, fetch_json=None, sleep=time.sleep,
                 clock=time.monotonic, max_runtime: float = 45):
        self.company = company
        self.name = f"{company.platform}:{company.id}"
        self._fetch_json = fetch_json
        self._sleep = sleep
        self._clock = clock
        self._max_runtime = max_runtime
        self._deadline = float("inf")

    def _start(self):
        self._deadline = self._clock() + self._max_runtime

    def _get(self, url: str):
        return self._request(url)

    def _post(self, url: str, payload: dict):
        return self._request(url, payload)

    def _request(self, url: str, payload=None):
        for attempt in range(2):
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("Presupuesto de tiempo de la empresa agotado")
            try:
                if self._fetch_json:
                    return self._fetch_json(url, payload) if payload is not None else self._fetch_json(url)
                request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                  headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "jobbot-personal/0.3"})
                with urlopen(request, timeout=min(10, remaining)) as response:
                    raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError("Respuesta de la bolsa demasiado grande")
                return json.loads(raw)
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise
                if attempt:
                    raise
            except (URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                if attempt:
                    raise
            self._sleep(min(1, max(0, self._deadline - self._clock())))
        raise RuntimeError("Sin respuesta de la bolsa")
