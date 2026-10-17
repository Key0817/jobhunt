import json
import unittest
from datetime import timedelta

from jobbot import repo
from jobbot.db import Database
from jobbot.models import STATUSES, JobPosting
from jobbot.ranking import Score


def make_db() -> Database:
    db = Database("sqlite:///:memory:")
    db.init_schema()
    return db


def add(db, title, **kwargs):
    repo.insert_job_if_new(db, JobPosting(source="t", title=title, url=f"https://example.com/{title}",
                                          company="Acme", **kwargs))
    return db.query("select id from jobs where title = ?", (title,))[0]["id"]


class StatsTests(unittest.TestCase):
    def test_empty_database(self):
        stats = repo.get_stats(make_db())
        self.assertEqual((stats["total_jobs"], stats["applied"], stats["interviews"], stats["offers"]), (0, 0, 0, 0))
        self.assertEqual(stats["new_last_7_days"], 0)
        self.assertEqual(set(stats["by_status"]), set(STATUSES))  # todos los estados, aunque sea con 0
        self.assertEqual(len(stats["weekly_applications"]), 8)
        self.assertTrue(all(w["count"] == 0 for w in stats["weekly_applications"]))

    def test_counts_follow_the_pipeline(self):
        db = make_db()
        a, b, c = add(db, "A"), add(db, "B"), add(db, "C")
        repo.change_status(db, a, "solicitada")
        repo.change_status(db, a, "entrevista")
        repo.change_status(db, b, "solicitada")
        stats = repo.get_stats(db)
        self.assertEqual(stats["total_jobs"], 3)
        self.assertEqual(stats["applied"], 2)
        self.assertEqual(stats["interviews"], 1)
        self.assertEqual(stats["offers"], 0)
        self.assertEqual(stats["new_last_7_days"], 3)
        self.assertEqual(stats["by_status"]["encontrada"], 1)
        self.assertEqual(stats["by_status"]["solicitada"], 1)
        self.assertEqual(stats["by_status"]["entrevista"], 1)
        self.assertEqual(c, repo.list_jobs(db, status="encontrada")[0]["id"])

    def test_interviews_and_offers_keep_counting_after_a_rejection(self):
        db = make_db()
        a, b = add(db, "A"), add(db, "B")
        for status in ("solicitada", "entrevista", "oferta", "rechazada"):
            repo.change_status(db, a, status)
        repo.change_status(db, b, "solicitada")
        repo.change_status(db, b, "entrevista")
        repo.change_status(db, b, "rechazada")
        stats = repo.get_stats(db)
        self.assertEqual((stats["interviews"], stats["offers"], stats["applied"]), (2, 1, 2))
        self.assertEqual(stats["by_status"]["rechazada"], 2)

    def test_going_back_and_forth_does_not_double_count(self):
        db = make_db()
        a = add(db, "A")
        for status in ("solicitada", "encontrada", "solicitada", "entrevista", "solicitada", "entrevista"):
            repo.change_status(db, a, status)
        stats = repo.get_stats(db)
        self.assertEqual((stats["applied"], stats["interviews"]), (1, 1))

    def test_weekly_applications(self):
        db = make_db()
        a, b, c = add(db, "A"), add(db, "B"), add(db, "C")
        for job_id in (a, b, c):
            repo.change_status(db, job_id, "solicitada")
        # b se postuló hace 3 semanas; c hace 100 días (fuera de la ventana de 8 semanas)
        db.execute("update jobs set applied_at = ? where id = ?", (repo.now() - timedelta(weeks=3), b))
        db.execute("update jobs set applied_at = ? where id = ?", (repo.now() - timedelta(days=100), c))
        db.conn.commit()
        stats = repo.get_stats(db)
        weekly = stats["weekly_applications"]
        today = repo.now().date()
        this_monday = (today - timedelta(days=today.weekday())).isoformat()
        self.assertEqual(weekly[-1]["week"], this_monday)
        self.assertEqual(weekly[-1]["count"], 1)       # a, esta semana
        self.assertEqual(weekly[-4]["count"], 1)       # b, hace 3 semanas
        self.assertEqual(sum(w["count"] for w in weekly), 2)
        self.assertEqual(stats["applied"], 3)          # c sí cuenta como postulada, solo que no sale en el gráfico

    def test_old_offers_are_not_new(self):
        db = make_db()
        a = add(db, "A")
        db.execute("update jobs set detected_at = ? where id = ?", (repo.now() - timedelta(days=30), a))
        db.conn.commit()
        self.assertEqual(repo.get_stats(db)["new_last_7_days"], 0)


class JobDetailTests(unittest.TestCase):
    def test_detail_includes_reasons_and_modality(self):
        db = make_db()
        repo.insert_job_if_new(
            db, JobPosting(source="t", title="Dev", url="u", description="texto largo", raw={"remote_modality": "fully_remote"}),
            Score(55, ("+40 puesto", "+15 python")))
        job_id = repo.list_jobs(db)[0]["id"]
        detail = repo.get_job_detail(db, job_id)
        self.assertEqual(detail["reasons"], ["+40 puesto", "+15 python"])
        self.assertEqual(detail["remote_modality"], "fully_remote")
        self.assertEqual(detail["description"], "texto largo")

    def test_detail_of_an_unscored_offer(self):
        db = make_db()
        job_id = add(db, "Dev")
        detail = repo.get_job_detail(db, job_id)
        self.assertEqual(detail["reasons"], [])
        self.assertIsNone(detail["remote_modality"])

    def test_detail_of_an_unknown_offer(self):
        with self.assertRaises(LookupError):
            repo.get_job_detail(make_db(), "no-existe")


class ListColumnsTests(unittest.TestCase):
    def test_list_includes_what_the_dashboard_needs(self):
        db = make_db()
        add(db, "Dev", location="Remoto")
        job = repo.list_jobs(db)[0]
        for key in ("id", "title", "company", "status", "score", "url", "source", "location", "posted_at", "applied_at"):
            self.assertIn(key, job)
        self.assertEqual(job["url"], "https://example.com/Dev")
        self.assertEqual(job["location"], "Remoto")
        self.assertIsNone(job["applied_at"])


if __name__ == "__main__":
    unittest.main()
