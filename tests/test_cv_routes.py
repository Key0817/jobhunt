import asyncio
import json
import unittest
from unittest.mock import patch

from jobbot import repo
from jobbot.api import app, get_db
from jobbot.cv.ai import AIError, GeneratedText
from jobbot.db import Database
from jobbot.models import JobPosting


async def request(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
               "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
               "server": ("test", 80), "client": ("test", 123)}, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(response) if response else None


class CVRoutesTests(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        repo.insert_job_if_new(self.db, JobPosting(source="test", title="Backend", company="Test Co",
                                                url="https://example.com/job", description="Python APIs"))
        self.job = repo.list_jobs(self.db)[0]
        self.cv = repo.save_cv_version(self.db, self.job["id"], "ai", "Perfil original", "generated/test.docx", "ai", "test/model")
        self.path = f"/api/cvs/{self.cv['id']}"
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()

    def call(self, method, path, payload=None):
        return asyncio.run(request(method, path, payload))

    def test_cover_letter_route_generates_and_detail_reloads_saved_letter(self):
        with patch("jobbot.api.load_profile", return_value={"skills": ["Python"]}):
            with patch("jobbot.api.generate_cover_letter", return_value=GeneratedText("Carta de prueba", "test/model")) as generate:
                status, body = self.call("POST", self.path + "/cover-letter")
        self.assertEqual(status, 200)
        self.assertEqual(body["cover_letter"], "Carta de prueba")
        self.assertEqual(generate.call_args.args[2]["id"], self.job["id"])
        status, body = self.call("GET", self.path)
        self.assertEqual(status, 200)
        self.assertEqual(body["cover_letter"], "Carta de prueba")
        self.assertEqual(body["cover_letter_model_name"], "test/model")

    def test_model_not_found_is_503_with_explanation_not_route_404(self):
        with patch("jobbot.api.load_profile", return_value={}):
            with patch("jobbot.api.generate_cover_letter", side_effect=AIError("Modelo no habilitado")):
                status, body = self.call("POST", self.path + "/cover-letter")
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"], "Modelo no habilitado")

    def test_missing_cv_returns_explicit_404(self):
        with patch("jobbot.api.generate_cover_letter") as generate:
            status, body = self.call("POST", "/api/cvs/missing/cover-letter")
        self.assertEqual(status, 404)
        self.assertIn("versión de CV", body["detail"])
        generate.assert_not_called()

    def test_all_cv_statuses_persist_and_keep_approval_consistent(self):
        for target in ("approved", "pending_review", "approved", "rejected", "archived"):
            with self.subTest(status=target):
                status, body = self.call("PATCH", self.path + "/status", {"status": target})
                self.assertEqual(status, 200)
                self.assertEqual(body["status"], target)
                self.assertEqual(body["approved"], target == "approved")
                self.assertEqual(body["approved_at"] is not None, target == "approved")
                status, saved = self.call("GET", self.path)
                self.assertEqual(saved["status"], target)
                self.assertEqual(saved["profile_text"], "Perfil original")
        self.assertEqual(repo.get_job_detail(self.db, self.job["id"])["status"], self.job["status"])

    def test_repeated_approval_preserves_date_and_cover_letter(self):
        repo.save_cover_letter(self.db, self.cv["id"], "Carta guardada", "test/model")
        _, first = self.call("PATCH", self.path + "/status", {"status": "approved"})
        _, second = self.call("PATCH", self.path + "/status", {"status": "approved"})
        self.assertEqual(first["approved_at"], second["approved_at"])
        _, detail = self.call("GET", self.path)
        self.assertEqual(detail["cover_letter"], "Carta guardada")

    def test_invalid_status_is_rejected_without_changing_saved_state(self):
        for payload in ({"status": "solicitada"}, {"status": "invalid"}, {}):
            status, _ = self.call("PATCH", self.path + "/status", payload)
            self.assertEqual(status, 422)
        self.assertEqual(repo.get_cv_version(self.db, self.cv["id"])["status"], "pending_review")

    def test_status_for_missing_cv_returns_404(self):
        status, _ = self.call("PATCH", "/api/cvs/missing/status", {"status": "approved"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
