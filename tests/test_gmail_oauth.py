import asyncio
import base64
import hashlib
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from jobbot import gmail_oauth as oauth
from jobbot.api import app, get_db
from jobbot.db import Database
from jobbot.sources import build_sources
from jobbot.sources.email_alerts import EmailAlertsSource, MailSettings
from jobbot.sources.gmail_api import GmailAPIClient
from jobbot.models import SearchQuery
from jobbot.pipeline import collect


class OAuthFixture(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MAIL_OAUTH_CLIENT_ID": "test-client", "MAIL_OAUTH_CLIENT_SECRET": "test-secret",
            "GMAIL_TOKEN_KEY": Fernet.generate_key().decode(), "GMAIL_OAUTH_REDIRECT_URI": oauth.REDIRECT,
            "MAIL_ENABLED": "false"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        self.addCleanup(self.db.close)

    def begin(self):
        return oauth.begin_connection(self.db, "http://localhost:5173", "JobAlerts")

    def finish(self, state):
        with patch("jobbot.gmail_oauth.exchange_code", return_value=("test-access", "test-refresh")):
            with patch("jobbot.gmail_oauth.gmail_get", return_value={"emailAddress": "test@gmail.com"}):
                return oauth.finish_connection(self.db, state, state, "test-code")


class OAuthTests(OAuthFixture):
    def test_authorization_url_has_state_pkce_offline_access_and_only_read_scope(self):
        url, state = self.begin()
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["scope"], [oauth.SCOPE])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("test-secret", url)
        row = self.db.query("select * from gmail_oauth_states")[0]
        verifier = oauth.decrypt(row["encrypted_verifier"])["verifier"]
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertNotEqual(row["state_hash"], state)
        self.assertNotIn(verifier, row["encrypted_verifier"])

    def test_finish_saves_encrypted_refresh_and_metadata_only_in_status(self):
        _, state = self.begin()
        origin = self.finish(state)
        self.assertEqual(origin, "http://localhost:5173")
        row = oauth.get_connection(self.db)
        self.assertNotIn("test-refresh", row["encrypted_credentials"])
        self.assertEqual(oauth.decrypt(row["encrypted_credentials"])["refresh_token"], "test-refresh")
        status = oauth.connection_status(self.db)
        self.assertTrue(status["connected"])
        self.assertNotIn("refresh", json.dumps(status))
        self.assertNotIn("test-secret", json.dumps(status))

    def test_state_requires_matching_browser_cookie(self):
        _, state = self.begin()
        with self.assertRaises(oauth.GmailOAuthError):
            oauth.finish_connection(self.db, state, "wrong-cookie", "code")
        self.assertEqual(len(self.db.query("select * from gmail_oauth_states")), 1)

    def test_state_is_one_use(self):
        _, state = self.begin()
        self.finish(state)
        with self.assertRaises(oauth.GmailOAuthError):
            self.finish(state)

    def test_expired_state_and_denied_consent_do_not_store_connection(self):
        _, state = self.begin()
        self.db.execute("update gmail_oauth_states set expires_at = ?", (int(time.time()) - 1,))
        self.db.conn.commit()
        with self.assertRaises(oauth.GmailOAuthError):
            self.finish(state)
        _, state = self.begin()
        with self.assertRaises(oauth.GmailOAuthError):
            oauth.finish_connection(self.db, state, state, "", denied=True)
        self.assertIsNone(oauth.get_connection(self.db))

    def test_wrong_encryption_key_is_reported(self):
        encrypted = oauth.encrypt({"refresh_token": "private"})
        with patch.dict(os.environ, {"GMAIL_TOKEN_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(oauth.GmailOAuthError):
                oauth.decrypt(encrypted)

    def test_configuration_does_not_accept_remote_redirect(self):
        with patch.dict(os.environ, {"GMAIL_OAUTH_REDIRECT_URI": "https://evil.test/api/settings/gmail/callback"}):
            with self.assertRaises(oauth.GmailOAuthError):
                self.begin()

    def test_refreshes_access_with_same_client_and_does_not_expose_refresh(self):
        _, state = self.begin()
        self.finish(state)
        def refresh(credentials, request):
            credentials.token = "new-access"
        with patch("jobbot.gmail_oauth.Credentials.refresh", new=refresh):
            self.assertEqual(oauth.access_token(self.db), "new-access")
        with patch.dict(os.environ, {"MAIL_OAUTH_CLIENT_ID": "different-client"}):
            with self.assertRaises(oauth.GmailOAuthError):
                oauth.access_token(self.db)

    def test_disconnect_disables_collect_even_when_legacy_mail_enabled(self):
        _, state = self.begin()
        self.finish(state)
        with patch("jobbot.gmail_oauth.requests.post") as revoke:
            revoke.return_value.status_code = 200
            self.assertTrue(oauth.disconnect(self.db))
        row = oauth.get_connection(self.db)
        self.assertIsNone(row["encrypted_credentials"])
        self.assertFalse(row["enabled"])
        with patch.dict(os.environ, {"MAIL_ENABLED": "true"}):
            self.assertEqual([s.name for s in build_sources(["example", "email_alerts"], db=self.db)], ["example"])

    def test_connected_api_transport_imports_and_acknowledges_raw_alert(self):
        from email.message import EmailMessage
        _, state = self.begin()
        self.finish(state)
        message = EmailMessage()
        message["From"] = "jobs-noreply@linkedin.com"
        message.set_content("Backend Developer\nEmpresa: Acme\nUbicación: Costa Rica\nhttps://www.linkedin.com/jobs/view/12345")
        raw = message.as_bytes()
        def get(token, path, query=""):
            if path == "labels": return {"labels": [{"id": "label-1", "name": "JobAlerts"}]}
            if path == "messages": return {"messages": [{"id": "abc123"}]}
            if "minimal" in query: return {"sizeEstimate": len(raw)}
            return {"raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
        with patch("jobbot.gmail_oauth.access_token", return_value="test-access"):
            with patch("jobbot.sources.gmail_api.gmail_get", side_effect=get):
                summary, = collect(self.db, [EmailAlertsSource()], SearchQuery(keywords=("developer",), countries=("CR",)))
        self.assertEqual(summary.inserted, 1)
        self.assertEqual(summary.status, "success")
        self.assertEqual(self.db.query("select uid from email_imports")[0]["uid"], "abc123")

    def test_exchange_rejects_missing_read_permission(self):
        with patch("jobbot.gmail_oauth._flow") as make:
            make.return_value.oauth2session.token = {"scope": "openid"}
            with self.assertRaises(oauth.GmailOAuthError):
                oauth.exchange_code(oauth.OAuthConfig.load(), "state", "verifier", "code")

    def test_saved_connection_enables_collect_without_app_password(self):
        _, state = self.begin()
        self.finish(state)
        sources = build_sources(["example", "email_alerts"], db=self.db)
        self.assertEqual([s.name for s in sources], ["example", "email_alerts"])
        self.assertEqual(MailSettings.from_env().max_messages, 50)


class GmailAPITests(unittest.TestCase):
    def test_adapter_only_reads_the_selected_label_and_decodes_raw_mime(self):
        raw = b"From: alert@indeed.com\nSubject: Test\n\nBody"
        calls = []
        def get(token, path, query=""):
            calls.append((path, query))
            if path == "labels": return {"labels": [{"id": "label-1", "name": "JobAlerts"}]}
            if path == "messages": return {"messages": [{"id": "abc123"}]}
            if "minimal" in query: return {"sizeEstimate": len(raw)}
            return {"raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
        client = GmailAPIClient("test-access")
        with patch("jobbot.sources.gmail_api.gmail_get", side_effect=get):
            self.assertEqual(client.label_id("JobAlerts"), "label-1")
            self.assertEqual(client.message_ids("label-1"), (["abc123"], False))
            client.message_size("abc123")
            self.assertEqual(client.raw_message("abc123"), raw)
        search_query = parse_qs(calls[1][1])
        self.assertEqual(search_query["labelIds"], ["label-1"])
        self.assertEqual(search_query["q"], ["is:unread"])
        self.assertNotIn("after:", search_query["q"][0])

    def test_missing_label_does_not_read_other_folders(self):
        client = GmailAPIClient("test-access")
        with patch("jobbot.sources.gmail_api.gmail_get", return_value={"labels": []}) as get:
            self.assertIsNone(client.label_id("JobAlerts"))
            get.assert_called_once()


async def asgi_request(method, path, payload=None, origin="http://localhost:5173", client="127.0.0.1", cookie=None, query=b""):
    body = json.dumps(payload).encode() if payload is not None else b""
    sent, received = [], False
    headers = [(b"host", b"localhost:8000"), (b"content-type", b"application/json")]
    if origin: headers.append((b"origin", origin.encode()))
    if cookie: headers.append((b"cookie", cookie.encode()))
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}
    async def send(message): sent.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query,
        "root_path": "", "headers": headers, "client": (client, 123), "server": ("localhost", 8000)}, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    content = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], content, start["headers"]


class GmailRouteTests(OAuthFixture):
    def setUp(self):
        super().setUp()
        app.dependency_overrides[get_db] = lambda: self.db
        self.addCleanup(lambda: app.dependency_overrides.pop(get_db, None))

    def test_local_settings_get_status_without_secrets(self):
        status, content, _ = asyncio.run(asgi_request("GET", "/api/settings/gmail"))
        self.assertEqual(status, 200)
        self.assertNotIn(b"test-secret", content)
        self.assertTrue(json.loads(content)["configured"])

    def test_remote_client_cannot_manage_connection(self):
        status, _, _ = asyncio.run(asgi_request("POST", "/api/settings/gmail/connect", {}, client="203.0.113.1"))
        self.assertEqual(status, 403)

    def test_foreign_or_missing_origin_cannot_start_oauth(self):
        for origin in ("https://evil.test", None):
            status, _, _ = asyncio.run(asgi_request("POST", "/api/settings/gmail/connect", {}, origin=origin))
            self.assertEqual(status, 403)

    def test_start_sets_httponly_browser_cookie(self):
        status, content, headers = asyncio.run(asgi_request("POST", "/api/settings/gmail/connect", {"folder": "JobAlerts"}))
        self.assertEqual(status, 200)
        cookies = [value for key, value in headers if key == b"set-cookie"]
        self.assertTrue(any(b"gmail_oauth_state" in cookie and b"HttpOnly" in cookie for cookie in cookies))
        self.assertIn("accounts.google.com", json.loads(content)["authorization_url"])

    def test_popup_navigation_sets_cookie_even_when_frontend_uses_127(self):
        status, content, _ = asyncio.run(asgi_request("POST", "/api/settings/gmail/connect", {"folder": "JobAlerts"}, origin="http://127.0.0.1:5173"))
        self.assertEqual(status, 200)
        launch = urlsplit(json.loads(content)["launch_url"])
        self.assertEqual(launch.hostname, "localhost")
        status, _, headers = asyncio.run(asgi_request("GET", launch.path, query=launch.query.encode(), origin=None))
        self.assertEqual(status, 302)
        location = next(value for key, value in headers if key == b"location")
        self.assertTrue(location.startswith(b"https://accounts.google.com/"))
        cookies = [value for key, value in headers if key == b"set-cookie"]
        self.assertTrue(any(b"gmail_oauth_state" in cookie and b"HttpOnly" in cookie for cookie in cookies))
        self.assertTrue(any(b"gmail_oauth_origin" in cookie and b"127.0.0.1:5173" in cookie for cookie in cookies))
        self.assertEqual(len(self.db.query("select * from gmail_oauth_states")), 1)

    def test_popup_navigation_rejects_unknown_or_expired_state(self):
        status, content, headers = asyncio.run(asgi_request("GET", "/api/settings/gmail/authorize", query=b"state=unknown", origin=None))
        self.assertEqual(status, 200)
        self.assertFalse(any(key == b"location" for key, _ in headers))
        self.assertIsNone(oauth.get_connection(self.db))

    def test_callback_with_matching_cookie_connects_and_clears_state_cookie(self):
        _, state = self.begin()
        cookie = f"gmail_oauth_state={state}; gmail_oauth_origin=http://localhost:5173"
        query = f"state={state}&code=private-code".encode()
        with patch("jobbot.gmail_oauth.exchange_code", return_value=("test-access", "test-refresh")):
            with patch("jobbot.gmail_oauth.gmail_get", return_value={"emailAddress": "test@gmail.com"}):
                status, content, headers = asyncio.run(asgi_request("GET", "/api/settings/gmail/callback", origin=None, cookie=cookie, query=query))
        self.assertEqual(status, 200)
        self.assertTrue(oauth.connection_status(self.db)["connected"])
        self.assertNotIn(b"private-code", content)
        self.assertNotIn(b"test-refresh", content)
        self.assertTrue(any(key == b"set-cookie" and b"gmail_oauth_state" in value and b"Max-Age=0" in value for key, value in headers))
        self.assertEqual(self.db.query("select * from gmail_oauth_states"), [])

    def test_callback_rejects_replay_and_never_echoes_auth_code(self):
        _, state = self.begin()
        query = f"state={state}&code=private-code".encode()
        status, content, _ = asyncio.run(asgi_request("GET", "/api/settings/gmail/callback", origin=None, query=query))
        self.assertEqual(status, 200)
        self.assertNotIn(b"private-code", content)
        self.assertIn(b"no coincide", content)


if __name__ == "__main__":
    unittest.main()
