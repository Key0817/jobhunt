import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from jobbot import repo
from jobbot.cli import main
from jobbot.db import Database
from jobbot.models import JobPosting, SearchQuery
from jobbot.pipeline import collect
from jobbot.pipeline import RunSummary
from jobbot.ranking import RankingProfile, Score
from jobbot.sources.example import ExampleSource

PROFILE_DATA = {
    "level": "junior",
    "english": "intermediate",
    "families": {
        "desarrollo": {"weight": 1.0, "title_terms": ["desarrollador", "developer"]},
        "soporte": {"weight": 1.0, "title_terms": ["soporte"]},
    },
    "skills": {"python": 3},
    "avoid_title": [],
}
QUERY = SearchQuery(keywords=("backend",), countries=("CR",), max_age_days=30)


def make_db() -> Database:
    db = Database("sqlite:///:memory:")
    db.init_schema()
    return db


def posting(title, description="", days=1, source="t"):
    return JobPosting(source=source, title=title, url=f"https://example.com/{title}", company="Acme",
                      description=description, posted_at=repo.now() - timedelta(days=days))


class MigrationTests(unittest.TestCase):
    def test_adds_the_missing_column_to_an_old_database(self):
        db = Database("sqlite:///:memory:")
        db.execute("create table jobs (id text primary key, title text, score real not null default 0)")
        db.execute("insert into jobs (id, title) values ('1', 'vieja')")
        db.conn.commit()
        self.assertNotIn("score_detail", db.columns("jobs"))
        db.migrate()
        self.assertIn("score_detail", db.columns("jobs"))
        self.assertEqual(db.query("select score_detail from jobs")[0]["score_detail"], "{}")  # filas viejas
        db.migrate()  # segunda vez: no pasa nada

    def test_migrate_on_a_database_without_tables_does_nothing(self):
        db = Database("sqlite:///:memory:")
        db.migrate()
        self.assertEqual(db.columns("jobs"), set())

    def test_new_databases_already_have_the_column(self):
        self.assertIn("score_detail", make_db().columns("jobs"))


class RepoScoringTests(unittest.TestCase):
    def test_insert_stores_score_and_reasons(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("Dev"), Score(73, ("+40 algo", "+33 otra cosa")))
        row = db.query("select score, score_detail from jobs")[0]
        self.assertEqual(row["score"], 73)
        self.assertEqual(json.loads(row["score_detail"]), {"points": 73, "reasons": ["+40 algo", "+33 otra cosa"]})

    def test_insert_without_score_defaults_to_zero(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("Dev"))
        row = db.query("select score, score_detail from jobs")[0]
        self.assertEqual((row["score"], row["score_detail"]), (0, "{}"))

    def test_list_is_sorted_by_score_by_default(self):
        db = make_db()
        for title, points in (("bajo", 10), ("alto", 90), ("medio", 50)):
            repo.insert_job_if_new(db, posting(title), Score(points))
        self.assertEqual([j["title"] for j in repo.list_jobs(db)], ["alto", "medio", "bajo"])

    def test_list_can_sort_by_recency(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("primera"), Score(90))
        repo.insert_job_if_new(db, posting("segunda"), Score(10))
        self.assertEqual([j["title"] for j in repo.list_jobs(db, sort="recent")], ["segunda", "primera"])

    def test_list_filters_by_minimum_score(self):
        db = make_db()
        for title, points in (("bajo", 10), ("alto", 90), ("medio", 50)):
            repo.insert_job_if_new(db, posting(title), Score(points))
        self.assertEqual([j["title"] for j in repo.list_jobs(db, min_score=50)], ["alto", "medio"])

    def test_get_job(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("Dev"))
        job_id = repo.list_jobs(db)[0]["id"]
        self.assertEqual(repo.get_job(db, job_id)["title"], "Dev")
        with self.assertRaises(LookupError):
            repo.get_job(db, "no-existe")

    def test_rerank_all_recomputes_every_score(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("Python Developer", days=1))
        repo.insert_job_if_new(db, posting("Cocinero", days=1))
        profile = RankingProfile.from_dict(PROFILE_DATA)
        self.assertEqual(repo.rerank_all(db, profile), 2)
        rows = {j["title"]: j for j in repo.list_jobs(db)}
        self.assertEqual(rows["Python Developer"]["score"], 57)  # 40 + 12 + 5 de frescura
        self.assertEqual(rows["Cocinero"]["score"], 5)
        detail = json.loads(repo.get_job(db, rows["Python Developer"]["id"])["score_detail"])
        self.assertTrue(any("publicada" in r for r in detail["reasons"]))  # la fecha llegó bien desde la base

    def test_rerank_all_reflects_a_changed_profile(self):
        db = make_db()
        repo.insert_job_if_new(db, posting("Senior Developer"))
        repo.rerank_all(db, RankingProfile.from_dict(PROFILE_DATA))
        as_junior = repo.list_jobs(db)[0]["score"]
        repo.rerank_all(db, RankingProfile.from_dict({**PROFILE_DATA, "level": "senior"}))
        as_senior = repo.list_jobs(db)[0]["score"]
        self.assertTrue(as_senior > as_junior)

    def test_rerank_on_an_empty_database(self):
        self.assertEqual(repo.rerank_all(make_db(), RankingProfile.from_dict(PROFILE_DATA)), 0)


class PipelineScoringTests(unittest.TestCase):
    def test_collect_with_a_profile_stores_scores(self):
        db = make_db()
        profile = RankingProfile.from_dict(PROFILE_DATA, interests=["backend"])
        collect(db, [ExampleSource()], QUERY, profile)
        rows = {j["title"]: j["score"] for j in repo.list_jobs(db)}
        self.assertTrue(rows["Desarrollador Backend Python"] > rows["Analista de Soporte Técnico"] > 0)

    def test_collect_without_a_profile_leaves_scores_at_zero(self):
        db = make_db()
        collect(db, [ExampleSource()], QUERY)
        self.assertTrue(all(j["score"] == 0 for j in repo.list_jobs(db)))


class CliTests(unittest.TestCase):
    def test_partial_coverage_is_warning_but_strict_mode_fails(self):
        self.run_cli("init-db")
        with mock.patch("jobbot.cli.collect", return_value=[RunSummary("board", 1, 1, status="partial")]):
            self.assertEqual(self.run_cli("collect")[0], 0)
            self.assertEqual(self.run_cli("collect", "--strict")[0], 1)

    def test_real_error_is_failure_even_after_some_posts_were_saved(self):
        self.run_cli("init-db")
        for status in ("failed", "partial"):
            with self.subTest(status=status), mock.patch("jobbot.cli.collect", return_value=[
                RunSummary("board", 3, 1, error="Database error", status=status)]):
                self.assertEqual(self.run_cli("collect")[0], 1)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = self.folder.name
        self.paths = {name: os.path.join(root, name) for name in ("search.yml", "ranking.yml", "t.db")}
        with open(self.paths["search.yml"], "w", encoding="utf-8") as f:
            f.write("sources: [example]\nkeywords: [backend]\nmax_age_days: 30\n")
        with open(self.paths["ranking.yml"], "w", encoding="utf-8") as f:
            f.write("level: junior\nenglish: intermediate\nfamilies:\n"
                    "  desarrollo:\n    title_terms: [desarrollador]\n"
                    "  soporte:\n    title_terms: [soporte]\nskills:\n  python: 3\n")
        env = {"DATABASE_URL": f"sqlite:///{self.paths['t.db']}",
               "JOBBOT_CONFIG": self.paths["search.yml"], "JOBBOT_RANKING": self.paths["ranking.yml"]}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def collect(self):
        with self.assertLogs("jobbot", level="INFO"):
            code, _, _ = self.run_cli("collect")
        self.assertEqual(code, 0)

    def test_collect_then_list_orders_by_score(self):
        self.run_cli("init-db")
        self.collect()
        _, out, _ = self.run_cli("list")
        self.assertLess(out.index("Desarrollador Backend Python"), out.index("Analista de Soporte Técnico"))

    def test_list_min_score_filters(self):
        self.run_cli("init-db")
        self.collect()
        _, out, _ = self.run_cli("list", "--min-score", "50")
        self.assertIn("Desarrollador Backend Python", out)
        self.assertNotIn("Analista de Soporte Técnico", out)

    def test_rank_command_reports_a_summary(self):
        self.run_cli("init-db")
        self.collect()
        code, out, _ = self.run_cli("rank")
        self.assertEqual(code, 0)
        self.assertIn("Ofertas recalculadas: 2", out)
        self.assertIn("puntaje >= 70", out)

    def test_show_explains_the_score(self):
        self.run_cli("init-db")
        self.collect()
        _, listing, _ = self.run_cli("list", "--limit", "1")
        code, out, _ = self.run_cli("show", listing.split()[0])
        self.assertEqual(code, 0)
        self.assertIn("Desarrollador Backend Python", out)
        self.assertIn("puesto de desarrollo", out)
        self.assertIn("Puntaje:", out)

    def test_show_unknown_id_is_a_friendly_error(self):
        self.run_cli("init-db")
        code, _, err = self.run_cli("show", "zzzz")
        self.assertEqual(code, 1)
        self.assertIn("Error:", err)

    def test_collect_without_ranking_file_warns_and_leaves_scores_at_zero(self):
        self.run_cli("init-db")
        with mock.patch.dict(os.environ, {"JOBBOT_RANKING": os.path.join(self.folder.name, "no-existe.yml")}):
            with self.assertLogs("jobbot", level="WARNING") as logs:
                code, _, _ = self.run_cli("collect")
        self.assertEqual(code, 0)
        self.assertTrue(any("ranking.yml" in line for line in logs.output))
        _, out, _ = self.run_cli("list")
        self.assertTrue(all(line.split()[1] == "0" for line in out.strip().splitlines()))

    def test_rank_without_ranking_file_is_an_error(self):
        self.run_cli("init-db")
        with mock.patch.dict(os.environ, {"JOBBOT_RANKING": os.path.join(self.folder.name, "no-existe.yml")}):
            code, _, err = self.run_cli("rank")
        self.assertEqual(code, 1)
        self.assertIn("ranking.yml", err)

    def test_an_old_database_is_migrated_automatically(self):
        # Una base creada antes de existir score_detail (como tu Supabase hoy).
        db = Database(os.environ["DATABASE_URL"])
        db.init_schema()
        db.conn.execute("alter table jobs drop column score_detail")
        db.conn.commit()
        self.assertNotIn("score_detail", db.columns("jobs"))
        db.close()
        self.collect()  # collect migra solo, antes de guardar nada
        _, out, _ = self.run_cli("list")
        self.assertIn("Desarrollador Backend Python", out)


if __name__ == "__main__":
    unittest.main()
