import unittest
from unittest.mock import MagicMock, patch

from jobbot.db import Database
from jobbot.models import JobPosting, SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.base import Source


class Deadlock(RuntimeError):
    sqlstate = "40P01"


class Board(Source):
    name = "test"

    def fetch(self, query):
        return [JobPosting(source=self.name, title="Developer", url="https://example.test/job")]


class ResilienceTests(unittest.TestCase):
    def test_deadlock_retries_without_losing_posting_or_history(self):
        from jobbot import repo
        db = Database("sqlite:///:memory:")
        self.addCleanup(db.close)
        db.init_schema()
        original = repo.insert_job_if_new
        calls = []

        def insert(*args):
            calls.append(1)
            if len(calls) == 1:
                raise Deadlock("simulated")
            return original(*args)

        with patch("jobbot.pipeline.repo.insert_job_if_new", side_effect=insert), patch("jobbot.pipeline.time.sleep"):
            result = collect(db, [Board()], SearchQuery(keywords=(), countries=()))[0]
        self.assertEqual(result.status, "success")
        self.assertEqual(result.inserted, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(db.query("select * from status_history")), 1)

    def test_database_failure_is_not_retried_forever_and_next_source_runs(self):
        db = Database("sqlite:///:memory:")
        self.addCleanup(db.close)
        db.init_schema()
        with patch("jobbot.pipeline.repo.insert_job_if_new", side_effect=Deadlock("simulated")) as insert, patch("jobbot.pipeline.time.sleep"):
            summaries = collect(db, [Board(), Board()], SearchQuery(keywords=(), countries=()))
        self.assertEqual(insert.call_count, 6)
        self.assertTrue(all(s.status == "failed" for s in summaries))
        self.assertEqual(len(db.query("select * from runs")), 2)

    def postgres_stub(self):
        db = Database.__new__(Database)
        db.is_sqlite = False
        db.conn = MagicMock()
        db.execute = MagicMock()
        db.query = MagicMock(return_value=[{"revision": "installed"}])
        db._migrate_schema = MagicMock()
        return db

    def test_installed_revision_does_not_repeat_schema_ddl(self):
        db = self.postgres_stub()
        db.migrate()
        db._migrate_schema.assert_not_called()
        db.conn.commit.assert_called_once()
        self.assertFalse(any("alter table" in c.args[0] for c in db.execute.call_args_list))

    def test_new_revision_migrates_once_and_commits_as_one_transaction(self):
        db = self.postgres_stub()
        db.query.return_value = []
        db.migrate()
        db._migrate_schema.assert_called_once()
        db.conn.commit.assert_called_once()
        self.assertTrue(any("insert into jobbot_schema_revisions" in c.args[0] for c in db.execute.call_args_list))

    def test_migration_deadlock_rolls_back_and_retries(self):
        db = self.postgres_stub()
        db.execute.side_effect = [Deadlock("simulated"), None, None, None]
        with patch("time.sleep"):
            db.migrate()
        db.conn.rollback.assert_called_once()
        db.conn.commit.assert_called_once()
