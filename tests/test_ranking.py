import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobbot.models import JobPosting
from jobbot.ranking import RankingProfile, prep, required_years, term_pattern

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

BASE = {
    "level": "junior",
    "english": "intermediate",
    "families": {
        "desarrollo": {"weight": 1.0, "title_terms": ["developer", "desarrollador", "front end", "c#", ".net"]},
        "soporte": {"weight": 0.5, "title_terms": ["soporte"]},
    },
    "skills": {"python": 3, "sql": 2, "java": 1},
    "avoid_title": ["ventas", "marketing"],
}


def profile(interests=(), **overrides):
    return RankingProfile.from_dict({**BASE, **overrides}, interests)


def job(title, description="", days=30, posted_at=None):
    """days=30 por defecto: sin bono de frescura, para que las cuentas sean exactas."""
    return JobPosting(source="t", title=title, url="u", description=description,
                      posted_at=posted_at or NOW - timedelta(days=days))


def points(p, title, description="", **kwargs):
    return p.rank(job(title, description, **kwargs), now=NOW).points


class PrepAndPatternTests(unittest.TestCase):
    def test_prep_removes_accents_and_separators(self):
        self.assertEqual(prep("Front-End / Soporte Técnico"), "front end soporte tecnico")

    def test_whole_word_matching(self):
        java = term_pattern("java")
        self.assertIsNone(java.search(prep("JavaScript developer")))
        self.assertIsNotNone(java.search(prep("Java developer")))
        self.assertIsNone(term_pattern("qa").search(prep("aqua engineer")))
        self.assertIsNotNone(term_pattern("qa").search(prep("QA analyst")))

    def test_terms_with_symbols(self):
        self.assertIsNotNone(term_pattern("c#").search(prep("Senior C# developer")))
        self.assertIsNotNone(term_pattern(".net").search(prep("Desarrollador .NET")))
        self.assertIsNone(term_pattern(".net").search(prep("Desarrollador ASP.NET")))
        self.assertIsNotNone(term_pattern("node.js").search(prep("Backend Node.js")))
        self.assertIsNone(term_pattern("c").search(prep("C# developer")))


class RequiredYearsTests(unittest.TestCase):
    def test_spanish_and_english_formulations(self):
        self.assertEqual(required_years("Pedimos 5 años de experiencia en Java"), 5)
        self.assertEqual(required_years("3+ years of experience with Python"), 3)
        self.assertEqual(required_years("Experience of 3+ years"), 3)
        self.assertEqual(required_years("Experiencia de al menos 4 años"), 4)

    def test_range_uses_the_lower_bound(self):
        self.assertEqual(required_years("1-3 años de experiencia"), 1)

    def test_takes_the_maximum_when_there_are_several(self):
        self.assertEqual(required_years("5 años de experiencia en Java y 2 años de experiencia en Python"), 5)

    def test_company_age_is_ignored(self):
        self.assertIsNone(required_years("Con más de 20 años de experiencia en el mercado"))

    def test_nothing_to_find(self):
        self.assertIsNone(required_years("Sin experiencia previa"))
        self.assertIsNone(required_years(""))
        self.assertIsNone(required_years(None))


class RoleAndSkillTests(unittest.TestCase):
    def test_role_in_title(self):
        score = profile().rank(job("Python Developer"), now=NOW)
        self.assertEqual(score.points, 52)  # 40 del puesto + 12 de python en el título (4 x 3)
        self.assertTrue(any("puesto de desarrollo" in r for r in score.reasons))
        self.assertTrue(any("python (título)" in r for r in score.reasons))

    def test_accents_and_hyphens_do_not_matter(self):
        self.assertEqual(points(profile(), "Desarrollador Front-End"), 40)
        self.assertEqual(points(profile(), "Desarrollador FRONT END"), 40)

    def test_role_only_in_description_is_a_weak_signal(self):
        self.assertEqual(points(profile(), "Analista", "trabajarás como developer"), 8)

    def test_skill_in_description_counts_less_than_in_title(self):
        sin = points(profile(), "Developer")
        en_descripcion = points(profile(), "Developer", "usamos python")
        en_titulo = points(profile(), "Developer Python")
        self.assertTrue(sin < en_descripcion < en_titulo)

    def test_skills_are_capped(self):
        skills = {f"tec{i}": 3 for i in range(10)}
        title = "Developer " + " ".join(skills)
        self.assertEqual(points(profile(skills=skills), title), 70)  # 40 + tope de 30

    def test_best_family_wins(self):
        self.assertEqual(points(profile(), "Soporte técnico"), 20)        # 40 x 0.5
        self.assertEqual(points(profile(), "Soporte developer"), 40)      # gana la de mayor peso

    def test_interests_from_search_keywords(self):
        p = profile(interests=["backend"])
        self.assertEqual(points(p, "Backend Developer"), 43)   # 40 + 3 por el título
        self.assertEqual(points(p, "Developer", "experiencia en backend"), 41)  # 40 + 1 por la descripción

    def test_interests_are_capped(self):
        terms = [f"int{i}" for i in range(10)]
        p = profile(interests=terms)
        self.assertEqual(points(p, "Developer " + " ".join(terms)), 55)  # 40 + tope de 15


class LevelTests(unittest.TestCase):
    def test_junior_user(self):
        p = profile(level="junior")
        self.assertEqual(points(p, "Junior Developer"), 55)
        self.assertEqual(points(p, "Semi Senior Developer"), 30)
        self.assertEqual(points(p, "Senior Developer"), 15)
        self.assertEqual(points(p, "Developer Lead"), 5)

    def test_semi_senior_is_not_read_as_senior(self):
        p = profile(level="junior")
        self.assertEqual(points(p, "Semi-Senior Developer"), 30)  # -10, no -25

    def test_other_levels(self):
        self.assertEqual(points(profile(level="semi"), "Semi Senior Developer"), 50)
        self.assertEqual(points(profile(level="senior"), "Senior Developer"), 50)
        self.assertEqual(points(profile(level="senior"), "Junior Developer"), 15)

    def test_title_that_admits_several_levels_uses_the_most_accessible(self):
        self.assertEqual(points(profile(level="junior"), "Junior / Semi Senior Developer"), 55)

    def test_years_of_experience_penalty(self):
        self.assertEqual(points(profile(level="junior"), "Developer", "5 años de experiencia"), 22)  # -18
        self.assertEqual(points(profile(level="semi"), "Developer", "5 años de experiencia"), 40)
        self.assertEqual(points(profile(level="junior"), "Developer", "10 años de experiencia"), 10)  # tope -30

    def test_range_and_company_age_do_not_penalize(self):
        p = profile(level="junior")
        self.assertEqual(points(p, "Developer", "1-3 años de experiencia"), 40)
        self.assertEqual(points(p, "Developer", "más de 20 años de experiencia en el mercado"), 40)


class PenaltyAndBonusTests(unittest.TestCase):
    def test_non_it_area_in_title(self):
        score = profile().rank(job("Developer de marketing"), now=NOW)
        self.assertEqual(score.points, 0)  # 40 - 50, nunca por debajo de 0
        self.assertTrue(any("área ajena" in r for r in score.reasons))
        self.assertEqual(points(profile(), "Ejecutivo de Ventas"), 0)

    def test_english_penalty_depends_on_your_level(self):
        avanzado = "Se requiere inglés avanzado"
        self.assertEqual(points(profile(english="fluent"), "Developer", avanzado), 40)
        self.assertEqual(points(profile(english="intermediate"), "Developer", avanzado), 25)
        self.assertEqual(points(profile(english="basic"), "Developer", avanzado), 10)

    def test_intermediate_english_only_bothers_a_basic_level(self):
        intermedio = "inglés intermedio (B2)"
        self.assertEqual(points(profile(english="intermediate"), "Developer", intermedio), 40)
        self.assertEqual(points(profile(english="basic"), "Developer", intermedio), 25)

    def test_recency_bonus(self):
        p = profile()
        self.assertEqual(points(p, "Developer", days=1), 45)
        self.assertEqual(points(p, "Developer", days=5), 43)
        self.assertEqual(points(p, "Developer", days=10), 41)
        self.assertEqual(points(p, "Developer", days=20), 40)

    def test_naive_datetime_is_treated_as_utc(self):
        naive = datetime(2026, 10, 14, 12, 0)
        self.assertEqual(profile().rank(job("Developer", posted_at=naive), now=NOW).points, 45)

    def test_offer_without_date_gets_no_recency_bonus(self):
        sin_fecha = JobPosting(source="t", title="Developer", url="u")
        self.assertEqual(profile().rank(sin_fecha, now=NOW).points, 40)

    def test_score_never_exceeds_100(self):
        skills = {f"tec{i}": 3 for i in range(10)}
        terms = [f"int{i}" for i in range(10)]
        title = "Junior Developer " + " ".join(skills) + " " + " ".join(terms)
        p = profile(skills=skills, interests=terms)
        self.assertEqual(points(p, title, days=1), 100)  # 40 + 30 + 15 + 15 + 5 = 105


class ProfileTests(unittest.TestCase):
    def test_reasons_are_signed_and_serializable(self):
        score = profile().rank(job("Senior Python Developer", days=1), now=NOW)
        self.assertTrue(score.reasons)
        self.assertTrue(all(r[0] in "+-" for r in score.reasons))
        data = score.to_dict()
        self.assertEqual(data["points"], score.points)
        self.assertEqual(data["reasons"], list(score.reasons))

    def test_invalid_level_or_english_is_rejected(self):
        with self.assertRaises(ValueError):
            profile(level="experto")
        with self.assertRaises(ValueError):
            profile(english="nativo")

    def test_shipped_profile_loads_and_scores_within_range(self):
        path = Path(__file__).parent.parent / "config" / "ranking.yml"
        p = RankingProfile.load(path, interests=["backend"])
        score = p.rank(job("Desarrollador Backend Python Junior", "Django y SQL", days=1), now=NOW)
        self.assertTrue(0 <= score.points <= 100)


if __name__ == "__main__":
    unittest.main()
