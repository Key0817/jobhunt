"""Ranking local: puntúa cada oferta de 0 a 100 con reglas explicables (sin IA y sin red).

Cada punto tiene una razón. Los pesos y tu perfil viven en config/ranking.yml.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .models import JobPosting

LEVELS = ("junior", "semi", "senior")
ENGLISH_LEVELS = ("fluent", "intermediate", "basic")

# --- Constantes del modelo de puntaje -------------------------------------------------
ROLE_POINTS = 40          # un término de tu tipo de puesto en el título
ROLE_WEAK_POINTS = 8      # ... solo en la descripción
SKILL_TITLE = 4           # por peso de la tecnología, si está en el título
SKILL_DESCRIPTION = 1.5   # por peso de la tecnología, si está solo en la descripción
SKILLS_CAP = 30
INTEREST_TITLE = 3        # términos de interés (las keywords de search.yml)
INTEREST_DESCRIPTION = 1
INTERESTS_CAP = 15
AVOID_PENALTY = 50
YEAR_PENALTY = 6          # por cada año de experiencia pedido por encima de tu nivel
YEAR_PENALTY_CAP = 30
MAX_YEARS = {"junior": 2, "semi": 5, "senior": 99}
MAX_PLAUSIBLE_YEARS = 12  # "más de 20 años de experiencia" suele hablar de la empresa, no del puesto

# Puntos según el nivel que dice el título (filas) y el tuyo (columnas: junior, semi, senior).
TIER_POINTS = {
    "junior": (15, 5, -25),
    "semi": (-10, 10, -5),
    "senior": (-25, -15, 10),
    "lead": (-35, -25, 0),
}
TIER_ORDER = ("junior", "semi", "senior", "lead")
TIER_TERMS = {
    "semi": ["semi senior", "semisenior", "semi sr", "ssr", "mid senior", "mid level", "midlevel"],
    "junior": ["junior", "jr", "trainee", "intern", "internship", "entry level", "practicante",
               "pasante", "aprendiz", "becario", "graduate", "recien graduado"],
    "senior": ["senior", "sr"],
    "lead": ["lead", "principal", "staff", "architect", "arquitecto", "head of", "manager",
             "gerente", "jefe", "director", "vp"],
}

ENGLISH_ADVANCED = [
    "ingles avanzado", "ingles fluido", "ingles nativo", "ingles c1", "ingles c2", "ingles excelente",
    "advanced english", "fluent english", "english fluency", "fluent in english", "native english",
    "proficient in english", "excellent english", "excelente ingles", "english c1", "english c2",
    "bilingue", "bilingual", "professional proficiency in english", "full professional proficiency",
]
ENGLISH_INTERMEDIATE = [
    "ingles intermedio", "intermediate english", "ingles b1", "ingles b2", "english b1", "english b2",
    "upper intermediate", "conversational english", "ingles conversacional",
]
# Penalización por (inglés pedido) según tu nivel de inglés.
ENGLISH_PENALTY = {
    "fluent": {"advanced": 0, "intermediate": 0},
    "intermediate": {"advanced": 15, "intermediate": 0},
    "basic": {"advanced": 30, "intermediate": 15},
}

_SEPARATORS = str.maketrans({c: " " for c in "-_/|,:;()[]{}&—–\"'“”‘’!?¿¡*•·"})


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def prep(text: str | None) -> str:
    """Minúsculas, sin tildes y con guiones y signos convertidos en espacios.
    Conserva # + . para que 'c#', 'c++', '.net' y 'node.js' sigan siendo reconocibles."""
    text = _strip_accents(text or "").translate(_SEPARATORS)
    return re.sub(r"\s+", " ", text).strip()


def term_pattern(term: str) -> re.Pattern:
    """Coincidencia de palabra completa: 'java' no coincide con 'javascript', ni 'qa' con 'aqua'."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(prep(term)) + r"(?![a-z0-9#+])")


def _first_match(patterns: list[tuple[str, re.Pattern]], text: str) -> str | None:
    for term, pattern in patterns:
        if pattern.search(text):
            return term
    return None


# --- Años de experiencia pedidos ------------------------------------------------------
_YEARS = r"(?:anos|ano|years?|yrs?)"
_EXP = r"(?:experiencia|experience|exp)"
_RANGE = re.compile(rf"(\d{{1,2}})\s*(?:-|–|a|to)\s*\d{{1,2}}\s*\+?\s*{_YEARS}\s+(?:de\s+|of\s+)?{_EXP}")
_YEARS_PATTERNS = [
    re.compile(rf"(\d{{1,2}})\s*\+?\s*{_YEARS}\s+(?:de\s+|of\s+)?{_EXP}"),
    re.compile(rf"{_EXP}\s+(?:de\s+|of\s+|minima\s+de\s+|minimum\s+of\s+)?"
               rf"(?:al menos\s+|at least\s+|minimo\s+de\s+)?(\d{{1,2}})\s*\+?\s*{_YEARS}"),
]


def required_years(description: str | None) -> int | None:
    """Años de experiencia que pide la oferta (el máximo mencionado). En un rango, el mínimo."""
    text = _strip_accents(description or "")
    values: list[int] = []
    for match in _RANGE.finditer(text):
        values.append(int(match.group(1)))
    text = _RANGE.sub(" ", text)  # para que "1-3 años" no se lea también como "3 años"
    for pattern in _YEARS_PATTERNS:
        values.extend(int(m.group(1)) for m in pattern.finditer(text))
    values = [v for v in values if v <= MAX_PLAUSIBLE_YEARS]
    return max(values) if values else None


# --- Perfil y resultado ---------------------------------------------------------------
@dataclass(frozen=True)
class Score:
    points: int
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {"points": self.points, "reasons": list(self.reasons)}


@dataclass
class _Family:
    name: str
    weight: float
    terms: list[tuple[str, re.Pattern]]


class RankingProfile:
    def __init__(self, level: str, english: str, families: list[_Family],
                 skills: list[tuple[str, float, re.Pattern]], avoid_title: list[tuple[str, re.Pattern]],
                 interests: list[tuple[str, re.Pattern]]):
        self.level = level
        self.english = english
        self.families = families
        self.skills = skills
        self.avoid_title = avoid_title
        self.interests = interests
        self._tier_patterns = {t: [(term, term_pattern(term)) for term in terms]
                               for t, terms in TIER_TERMS.items()}
        self._english_adv = [(t, term_pattern(t)) for t in ENGLISH_ADVANCED]
        self._english_int = [(t, term_pattern(t)) for t in ENGLISH_INTERMEDIATE]

    # -- construcción ----------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict, interests=()) -> "RankingProfile":
        level = data.get("level", "junior")
        english = data.get("english", "intermediate")
        if level not in LEVELS:
            raise ValueError(f"level inválido: {level!r}. Usa uno de {LEVELS}")
        if english not in ENGLISH_LEVELS:
            raise ValueError(f"english inválido: {english!r}. Usa uno de {ENGLISH_LEVELS}")
        families = []
        for name, spec in (data.get("families") or {}).items():
            terms = [(t, term_pattern(t)) for t in (spec or {}).get("title_terms", [])]
            families.append(_Family(name, float((spec or {}).get("weight", 1.0)), terms))
        skills = [(str(t), float(w), term_pattern(str(t))) for t, w in (data.get("skills") or {}).items()]
        avoid = [(str(t), term_pattern(str(t))) for t in data.get("avoid_title") or []]
        interest_terms = [(str(t), term_pattern(str(t))) for t in interests if str(t).strip()]
        return cls(level, english, families, skills, avoid, interest_terms)

    @classmethod
    def load(cls, path: str | Path = "config/ranking.yml", interests=()) -> "RankingProfile":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, interests)

    # -- reglas ----------------------------------------------------------------------
    def _job_tier(self, title: str) -> str | None:
        text, found = title, []
        for tier in ("semi", "junior", "senior", "lead"):  # "semi senior" se quita antes de buscar "senior"
            hit = False
            for _, pattern in self._tier_patterns[tier]:
                if pattern.search(text):
                    hit = True
                    if tier == "semi":
                        text = pattern.sub(" ", text)
            if hit:
                found.append(tier)
        # Si el título admite varios niveles ("Junior / Semi Senior"), vale el más accesible.
        return min(found, key=TIER_ORDER.index) if found else None

    def rank(self, posting: JobPosting, now: datetime | None = None) -> Score:
        title = prep(posting.title)
        description = prep(posting.description)
        total = 0.0
        reasons: list[str] = []

        def add(points: float, reason: str) -> None:
            nonlocal total
            points = round(points)
            if points:
                total += points
                reasons.append(f"{points:+d} {reason}")

        # 1) Tipo de puesto
        best = None
        for family in self.families:
            term = _first_match(family.terms, title)
            if term and (best is None or family.weight > best[0]):
                best = (family.weight, family.name, term)
        if best:
            add(ROLE_POINTS * best[0], f"puesto de {best[1]} en el título ({best[2]})")
        else:
            for family in self.families:
                term = _first_match(family.terms, description)
                if term:
                    add(ROLE_WEAK_POINTS * family.weight, f"{family.name} mencionado en la descripción ({term})")
                    break

        # 2) Tecnologías
        skill_points, names = 0.0, []
        for term, weight, pattern in self.skills:
            if pattern.search(title):
                skill_points += SKILL_TITLE * weight
                names.append(f"{term} (título)")
            elif pattern.search(description):
                skill_points += SKILL_DESCRIPTION * weight
                names.append(term)
        if names:
            add(min(skill_points, SKILLS_CAP), "tecnologías: " + ", ".join(names))

        # 3) Términos de interés (keywords de search.yml)
        interest_points, hits = 0.0, []
        for term, pattern in self.interests:
            if pattern.search(title):
                interest_points += INTEREST_TITLE
                hits.append(term)
            elif pattern.search(description):
                interest_points += INTEREST_DESCRIPTION
                hits.append(term)
        if hits:
            add(min(interest_points, INTERESTS_CAP), "intereses: " + ", ".join(hits[:5]))

        # 4) Nivel de experiencia
        tier = self._job_tier(title)
        if tier:
            add(TIER_POINTS[tier][LEVELS.index(self.level)], f"nivel {tier} en el título")
        years = required_years(posting.description)
        if years is not None:
            over = years - MAX_YEARS[self.level]
            if over > 0:
                add(-min(YEAR_PENALTY * over, YEAR_PENALTY_CAP), f"pide {years} años de experiencia")

        # 5) Áreas que no son de TI
        term = _first_match(self.avoid_title, title)
        if term:
            add(-AVOID_PENALTY, f"área ajena en el título ({term})")

        # 6) Inglés
        text = f"{title} {description}"
        if _first_match(self._english_adv, text):
            add(-ENGLISH_PENALTY[self.english]["advanced"], "pide inglés avanzado")
        elif _first_match(self._english_int, text):
            add(-ENGLISH_PENALTY[self.english]["intermediate"], "pide inglés intermedio")

        # 7) Frescura
        if posting.posted_at:
            posted = posting.posted_at if posting.posted_at.tzinfo else posting.posted_at.replace(tzinfo=timezone.utc)
            days = ((now or datetime.now(timezone.utc)) - posted).days
            if days <= 3:
                add(5, "publicada en los últimos 3 días")
            elif days <= 7:
                add(3, "publicada en la última semana")
            elif days <= 14:
                add(1, "publicada en las últimas 2 semanas")

        return Score(int(max(0, min(100, round(total)))), tuple(reasons))
