import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from jobbot import repo
from jobbot.api import create_cover_letter, create_job_cv, get_cv_detail
from jobbot.cv.ai import AIError, GeneratedText, generate_profile, generate_cover_letter, _post_json
from jobbot.cv.generator import generate_cv, OUTPUT_DIR
from jobbot.cv.profile import load_profile
from jobbot.db import Database
from jobbot.models import JobPosting

FACTS = {"personal": {"full_name": "Test Candidate", "email": "private@example.com", "phone": "123"},
         "experience": [{"role": "Backend", "technologies": ["Python"]}],
         "skills": {"languages": ["Python"]}, "default_language": "es",
         "base_profiles": {"manual": "MANUAL PROFILE DO NOT SEND"}}


class AIProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "test-secret", "GEMINI_MODEL": "test-model", "AI_TIMEOUT_SECONDS": "10"}, clear=True)
        self.env.start()
        self.dotenv = patch("jobbot.cv.ai.load_dotenv")
        self.dotenv.start()

    def tearDown(self):
        self.dotenv.stop()
        self.env.stop()

    @staticmethod
    def gemini(text, reason="STOP"):
        return {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": json.dumps({"text": text})}]}}]}

    def test_gemini_profile_uses_facts_and_offer_without_contact_or_manual_profiles(self):
        with patch("jobbot.cv.ai._post_json", return_value=self.gemini("Perfil Python.\nExperiencia real.")) as post:
            result = generate_profile(FACTS, "Backend Junior", "Python APIs", "Test Co")
        self.assertEqual(result, GeneratedText("Perfil Python. Experiencia real.", "gemini/test-model"))
        url, payload, headers, timeout = post.call_args.args
        self.assertTrue(url.endswith("test-model:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "test-secret")
        prompt = payload["contents"][0]["parts"][0]["text"]
        for text in ("Backend Junior", "Python APIs", "Test Co", "Test Candidate"):
            self.assertIn(text, prompt)
        for text in ("private@example.com", "MANUAL PROFILE DO NOT SEND", "phone"):
            self.assertNotIn(text, prompt)
        self.assertEqual(timeout, 10)

    def test_ollama_nonstreaming_structured_generation(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "ollama", "OLLAMA_MODEL": "local-model"}):
            with patch("jobbot.cv.ai._post_json", return_value={"done": True, "done_reason": "stop", "message": {"content": json.dumps({"text": "Carta real."})}}) as post:
                result = generate_cover_letter(FACTS, {"profile_text": "Perfil guardado."}, {"title": "Backend", "description": "Python", "company": "Co"})
        self.assertEqual(result.model_name, "ollama/local-model")
        payload = post.call_args.args[1]
        self.assertFalse(payload["stream"])
        self.assertIn("Perfil guardado.", payload["messages"][1]["content"])
        self.assertEqual(payload["format"]["required"], ["text"])

    def test_missing_key_is_explicit(self):
        os.environ.pop("GEMINI_API_KEY")
        with self.assertRaisesRegex(AIError, "GEMINI_API_KEY"):
            generate_profile(FACTS, "Job", "Description")

    def test_rejects_empty_long_and_truncated_responses(self):
        for response in (self.gemini(""), self.gemini("x" * 1201), self.gemini("Partial", "MAX_TOKENS"), {"candidates": []}):
            with self.subTest(response=str(response)[:80]):
                with patch("jobbot.cv.ai._post_json", return_value=response):
                    with self.assertRaises(AIError):
                        generate_profile(FACTS, "Job", "Description")

    def test_invalid_provider_and_timeout(self):
        for setting in ({"AI_PROVIDER": "invalid"}, {"AI_TIMEOUT_SECONDS": "oops"}):
            with patch.dict(os.environ, setting):
                with self.assertRaises(AIError):
                    generate_profile(FACTS, "Job", "Description")

    def test_http_errors_do_not_expose_provider_body_or_key(self):
        error = HTTPError("https://example.test?key=secret", 429, "quota secret", {}, io.BytesIO(b"private body"))
        with patch("jobbot.cv.ai.urlopen", side_effect=error):
            with self.assertRaises(AIError) as caught:
                _post_json("https://example.test", {}, {}, 10)
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn("límite", str(caught.exception))

    def test_http_json_transport(self):
        with patch("jobbot.cv.ai.urlopen", return_value=io.BytesIO(b'{"ok": true}')) as request:
            result = _post_json("https://example.test", {"message": "hola"}, {"x-goog-api-key": "test"}, 5)
        self.assertEqual(result, {"ok": True})
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(json.loads(sent.data), {"message": "hola"})
        self.assertEqual(request.call_args.kwargs["timeout"], 5)

    def test_malformed_ollama_message_is_reported(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "ollama"}):
            with patch("jobbot.cv.ai._post_json", return_value={"done": True, "message": None}):
                with self.assertRaises(AIError):
                    generate_profile(FACTS, "Job", "Description")

    def test_connection_error(self):
        with patch("jobbot.cv.ai.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(AIError):
                _post_json("https://example.test", {}, {}, 10)

    def test_profile_does_not_require_base_profiles(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as directory:
            path = Path(directory) / "profile.yml"
            path.write_text("profile:\n  skills:\n    languages: [Python]\n", encoding="utf-8")
            with patch("jobbot.cv.profile.PROFILE_PATH", path):
                self.assertIn("skills", load_profile())


class AICVIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        repo.insert_job_if_new(self.db, JobPosting(source="test", title="Backend Python", company="Correct Co", url="https://example.com/python", description="Python APIs"))
        self.job = repo.list_jobs(self.db)[0]
        self.cv = repo.save_cv_version(self.db, self.job["id"], "ai", "Perfil guardado.", "generated/test.docx", "ai", "gemini/test")

    def tearDown(self):
        self.db.close()

    def test_generate_save_reload_and_regenerate_letter_for_associated_job(self):
        repo.insert_job_if_new(self.db, JobPosting(source="test", title="Other role", company="Other Co", url="https://example.com/other"))
        for text in ("Primera carta.", "Carta regenerada."):
            with patch("jobbot.api.load_profile", return_value=FACTS):
                with patch("jobbot.api.generate_cover_letter", return_value=GeneratedText(text, "ollama/local")) as generate:
                    result = create_cover_letter(self.cv["id"], self.db)
            self.assertEqual(result["cover_letter"], text)
            self.assertEqual(generate.call_args.args[2]["id"], self.job["id"])
            reloaded = get_cv_detail(self.cv["id"], self.db)
            self.assertEqual(reloaded["cover_letter"], text)
            self.assertEqual(reloaded["cover_letter_model_name"], "ollama/local")
            self.assertEqual(reloaded["profile_text"], "Perfil guardado.")
            self.assertEqual(reloaded["model_name"], "gemini/test")

    def test_generation_failure_preserves_previous_letter(self):
        repo.save_cover_letter(self.db, self.cv["id"], "Carta anterior", "test/model")
        with patch("jobbot.api.load_profile", return_value=FACTS):
            with patch("jobbot.api.generate_cover_letter", side_effect=AIError("Fallo del modelo")):
                with self.assertRaises(HTTPException) as caught:
                    create_cover_letter(self.cv["id"], self.db)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(get_cv_detail(self.cv["id"], self.db)["cover_letter"], "Carta anterior")

    def test_unknown_cv_does_not_call_ai(self):
        with patch("jobbot.api.generate_cover_letter") as generate:
            with self.assertRaises(HTTPException) as caught:
                create_cover_letter("missing", self.db)
        generate.assert_not_called()
        self.assertEqual(caught.exception.status_code, 404)

    def test_generated_cv_persists_model_and_profile(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as directory:
            with patch("jobbot.cv.generator.OUTPUT_DIR", Path(directory)):
                with patch("jobbot.cv.generator.load_profile", return_value=FACTS):
                    with patch("jobbot.cv.generator.generate_profile", return_value=GeneratedText("Perfil IA real.", "gemini/test")) as generate:
                        result = generate_cv(self.job["id"], "Backend Python", "Python APIs", self.db, company="Correct Co")
            self.assertTrue(Path(result["file_path"]).is_file())
            saved = repo.get_cv_version(self.db, result["version_id"])
            self.assertEqual(saved["profile_text"], "Perfil IA real.")
            self.assertEqual(saved["model_name"], "gemini/test")
            self.assertEqual(saved["profile_key"], "ai")
            self.assertEqual(generate.call_args.args[-1], "Correct Co")

    def test_failed_profile_generation_does_not_create_version(self):
        before = len(repo.list_cv_versions(self.db, self.job["id"]))
        with patch("jobbot.cv.generator.load_profile", return_value=FACTS):
            with patch("jobbot.cv.generator.generate_profile", side_effect=AIError("Sin clave")):
                with self.assertRaises(HTTPException) as caught:
                    create_job_cv(self.job["id"], self.db)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(repo.list_cv_versions(self.db, self.job["id"])), before)

    def test_migrate_old_cv_table_is_idempotent_and_preserves_rows(self):
        db = Database("sqlite:///:memory:")
        try:
            db.execute("create table cv_versions (id text primary key, profile_text text)")
            db.execute("insert into cv_versions values ('old', 'Perfil antiguo')")
            db.conn.commit()
            db.migrate()
            db.migrate()
            row = db.query("select * from cv_versions")[0]
            self.assertEqual(row["profile_text"], "Perfil antiguo")
            self.assertIsNone(row["cover_letter"])
            self.assertIsNone(row["cover_letter_model_name"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
