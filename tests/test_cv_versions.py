
import unittest

from jobbot.db import Database
from jobbot.models import JobPosting
from jobbot import repo


class TestCVVersions(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()

        posting = JobPosting(
            source="test",
            title="Junior Full Stack Developer",
            url="https://example.com/job",
            company="Empresa de prueba",
            description="React y TypeScript",
        )
        repo.insert_job_if_new(self.db, posting)
        rows = repo.list_jobs(self.db, limit=1)
        self.job_id = rows[0]["id"]

    def tearDown(self):
        self.db.close()

    def test_save_and_list_cv_version(self):
        version = repo.save_cv_version(
            db=self.db,
            job_id=self.job_id,
            variant="full_stack",
            profile_text="Perfil profesional de prueba.",
            file_path="generated/test_cv.docx",
            profile_key="full_stack",
            model_name=None,
        )

        self.assertEqual(version["job_id"], self.job_id)
        self.assertEqual(version["profile_key"], "full_stack")
        self.assertEqual(version["file_path"], "generated/test_cv.docx")
        self.assertEqual(version["status"], "pending_review")
        self.assertFalse(version["approved"])

        versions = repo.list_cv_versions(self.db, self.job_id)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["id"], version["id"])


if __name__ == "__main__":
    unittest.main()
