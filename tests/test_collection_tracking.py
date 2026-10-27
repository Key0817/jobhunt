import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from jobbot import repo
from jobbot.db import Database
from jobbot.models import JobPosting, SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.base import FetchResult, Source
from jobbot.sources.getonboard import GetOnBoardSource


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        self.posting = JobPosting(source="greenhouse:one", external_id="1", title="Developer", company="Acme", country="CR", url="https://example.com/jobs/1")

    def tearDown(self):
        self.db.close()

    def test_same_title_different_external_ids_are_distinct(self):
        self.assertTrue(repo.insert_job_if_new(self.db, self.posting))
        self.assertTrue(repo.insert_job_if_new(self.db, replace(self.posting, external_id="2", url="https://example.com/jobs/2")))
        self.assertEqual(len(repo.list_jobs(self.db)), 2)

    def test_same_external_id_is_scoped_by_board(self):
        repo.insert_job_if_new(self.db, self.posting)
        self.assertTrue(repo.insert_job_if_new(self.db, replace(self.posting, source="greenhouse:two", url="https://other.example/jobs/1")))

    def test_seen_again_updates_timestamp_without_changing_application_or_cv(self):
        repo.insert_job_if_new(self.db, self.posting)
        job = repo.list_jobs(self.db)[0]
        repo.change_status(self.db, job["id"], "solicitada")
        cv = repo.save_cv_version(self.db, job["id"], "ai", "Profile", "generated/test.docx", "ai")
        later = repo.now() + timedelta(days=1)
        with patch("jobbot.repo.now", return_value=later):
            self.assertFalse(repo.insert_job_if_new(self.db, replace(self.posting, title="Updated title")))
        saved = repo.get_job(self.db, job["id"])
        self.assertEqual(saved["status"], "solicitada")
        self.assertEqual(saved["last_seen_at"], later.isoformat())
        self.assertEqual(repo.get_cv_version(self.db, cv["id"])["job_id"], job["id"])

    def test_exact_canonical_url_merges_sources_and_preserves_provenance(self):
        repo.insert_job_if_new(self.db, self.posting)
        self.assertFalse(repo.insert_job_if_new(self.db, replace(self.posting, source="alert", external_id="email-1", url=self.posting.url + "?utm_source=email")))
        self.assertEqual(len(repo.list_jobs(self.db)), 1)
        self.assertEqual(len(self.db.query("select * from job_sources")), 2)

    def test_same_title_from_different_sources_is_not_automatically_merged(self):
        repo.insert_job_if_new(self.db, self.posting)
        self.assertTrue(repo.insert_job_if_new(self.db, replace(self.posting, source="other", external_id="1", url="https://other.example/jobs/1")))

    def test_migration_backfills_legacy_identity_without_recreating_job(self):
        p = self.posting
        self.db.execute("insert into jobs (id, dedupe_key, source, external_id, title, company, url, detected_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                        ("legacy", "old-title-based-key", p.source, p.external_id, p.title, p.company, p.url, repo.now()))
        self.db.conn.commit()
        self.db.migrate()
        self.db.migrate()
        self.assertFalse(repo.insert_job_if_new(self.db, p))
        self.assertEqual(repo.list_jobs(self.db)[0]["id"], "legacy")
        self.assertEqual(len(self.db.query("select * from job_sources")), 1)

    def test_pipeline_persists_partial_details(self):
        posting = self.posting
        class Partial(Source):
            name = "partial"
            def fetch(self, query):
                return FetchResult([posting], status="partial", details={"failures": ["page 2 timeout"]})
        summary, = collect(self.db, [Partial()], SearchQuery(keywords=(), countries=("CR",)))
        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.inserted, 1)
        row, = self.db.query("select status, details from runs")
        self.assertEqual(row["status"], "partial")
        self.assertEqual(json.loads(row["details"])["failures"], ["page 2 timeout"])

    def test_getonboard_timeout_is_persisted_as_partial(self):
        clock = [0]
        def fetch(url):
            clock[0] += 100
            return {"data": [], "meta": {"total_pages": 1}}
        source = GetOnBoardSource(fetch_json=fetch, categories=("a", "b"), pause=0, sleep=lambda n: None,
                                  clock=lambda: clock[0], max_runtime=50)
        with self.assertLogs("jobbot", level="WARNING"):
            summary, = collect(self.db, [source], SearchQuery(keywords=(), countries=(), residence_country=""))
        self.assertEqual(summary.status, "partial")
        self.assertTrue(summary.details["time_limit_reached"])

    def test_canonical_url_preserves_query_identifying_a_job(self):
        self.assertNotEqual(repo.canonical_job_url("https://example.com/jobs?id=1&utm_source=x"),
                            repo.canonical_job_url("https://example.com/jobs?id=2&utm_source=x"))


if __name__ == "__main__":
    unittest.main()
