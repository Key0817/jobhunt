from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import SearchQuery


def load_dotenv(path: str = ".env") -> None:
    """Carga KEY=VALUE de un .env sin sobrescribir variables ya definidas."""
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or "sqlite:///jobbot.db"


def load_search_config(path: str | None = None) -> tuple[SearchQuery, list[str]]:
    file = Path(path or os.environ.get("JOBBOT_CONFIG", "config/search.yml"))
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    residence = data.get("residence_country", "CR")
    query = SearchQuery(
        keywords=tuple(data.get("keywords", [])),
        countries=tuple(c.upper() for c in data.get("countries", [])),
        include_remote_latam=bool(data.get("include_remote_latam", True)),
        max_age_days=int(data.get("max_age_days", 14)),
        residence_country=str(residence or "").upper(),
    )
    return query, list(data.get("sources", []))