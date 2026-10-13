import unittest
from datetime import timedelta

from jobbot import repo
from jobbot.db import Database
from jobbot.models import JobPosting, SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.base import Source
from jobbot.sources.example import ExampleSource

QUERY = SearchQuery(keywords=("developer",), countries=("CR",), max_age_days=14)


class BrokenSource(Source):
    name = "broken"

    def fetch(self, query):
        raise RuntimeError("la página cambió su HTML")


class OkSource(Source):
    name = "ok"

    def fetch(self, query):
        return [JobPosting(source="ok", title="QA Engineer", company="Beta", country="CR",
                           url="https://example.com/qa", posted_at=repo.now())]


def make_db() -> Database:
    db = Database("sqlite:///:memory:")
    db.init_schema()
    return db


class DedupeKeyTests(unittest.TestCase):
    def test_same_job_different_format_has_same_key(self):
        a = JobPosting(source="a", title="Desarrollador Backend Python", company="Acme CR", country="CR", url="u1")
        b = JobPosting(source="b", title="desarrollador  backend, python", company="ACME  cr", country="cr", url="u2")
        self.assertEqual(a.dedupe_key, b.dedupe_key)

    def test_accents_are_ignored(self):
        a = JobPosting(source="a", title="Soporte Técnico", company="Soluciones", country="CR", url="u")
        b = JobPosting(source="a", title="Soporte Tecnico", company="Soluciones", country="CR", url="u")
        self.assertEqual(a.dedupe_key, b.dedupe_key)

    def test_different_country_is_different_job(self):
        a = JobPosting(source="a", title="Dev", company="X", country="CR", url="u")
        b = JobPosting(source="a", title="Dev", company="X", country="MX", url="u")
        self.assertNotEqual(a.dedupe_key, b.dedupe_key)


class PipelineTests(unittest.TestCase):
    def test_collect_dedupes_and_filters_old(self):
        db = make_db()
        (summary,) = collect(db, [ExampleSource()], QUERY)
        # 4 ofertas: 1 antigua se descarta, 1 duplicada no se guarda dos veces
        self.assertEqual(summary.found, 3)
        self.assertEqual(summary.inserted, 2)
        self.assertEqual(len(repo.list_jobs(db)), 2)

    def test_second_run_inserts_nothing(self):
        db = make_db()
        collect(db, [ExampleSource()], QUERY)
        (summary,) = collect(db, [ExampleSource()], QUERY)
        self.assertEqual(summary.inserted, 0)
        self.assertEqual(len(repo.list_jobs(db)), 2)

    def test_failing_source_does_not_stop_others(self):
        db = make_db()
        summaries = collect(db, [BrokenSource(), OkSource()], QUERY)
        self.assertIn("RuntimeError", summaries[0].error)
        self.assertIsNone(summaries[1].error)
        self.assertEqual(summaries[1].inserted, 1)
        runs = db.query("select source, error from runs order by source")
        self.assertEqual([r["source"] for r in runs], ["broken", "ok"])
        self.assertIsNotNone(runs[0]["error"])

    def test_undated_posting_is_kept(self):
        class Undated(Source):
            name = "undated"

            def fetch(self, query):
                return [JobPosting(source="undated", title="Dev", company="Z", url="u")]

        db = make_db()
        (summary,) = collect(db, [Undated()], QUERY)
        self.assertEqual(summary.inserted, 1)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        collect(self.db, [OkSource()], QUERY)
        self.job_id = repo.list_jobs(self.db)[0]["id"]

    def test_new_job_has_initial_history(self):
        hist = self.db.query("select from_status, to_status from status_history where job_id = ?", (self.job_id,))
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["to_status"], "encontrada")

    def test_change_status_records_history_and_applied_at_once(self):
        repo.change_status(self.db, self.job_id, "solicitada")
        first = self.db.query("select applied_at from jobs where id = ?", (self.job_id,))[0]["applied_at"]
        self.assertIsNotNone(first)
        repo.change_status(self.db, self.job_id, "entrevista")
        repo.change_status(self.db, self.job_id, "solicitada")
        again = self.db.query("select applied_at from jobs where id = ?", (self.job_id,))[0]["applied_at"]
        self.assertEqual(first, again)  # la fecha de postulación no cambia
        hist = self.db.query("select to_status from status_history where job_id = ? order by changed_at", (self.job_id,))
        self.assertEqual(len(hist), 4)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            repo.change_status(self.db, self.job_id, "volando")

    def test_unknown_job_rejected(self):
        with self.assertRaises(LookupError):
            repo.change_status(self.db, "no-existe", "aplicable")

    def test_prefix_resolution(self):
        self.assertEqual(repo.resolve_job_id(self.db, self.job_id[:6]), self.job_id)
        with self.assertRaises(LookupError):
            repo.resolve_job_id(self.db, "zzzz")

    def test_list_filter_by_status(self):
        repo.change_status(self.db, self.job_id, "aplicable")
        self.assertEqual(len(repo.list_jobs(self.db, "aplicable")), 1)
        self.assertEqual(len(repo.list_jobs(self.db, "encontrada")), 0)


if __name__ == "__main__":
    unittest.main()