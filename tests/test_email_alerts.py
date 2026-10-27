import hashlib
from email.message import EmailMessage
import os
import unittest
from unittest.mock import patch

from jobbot import repo
from jobbot.db import Database
from jobbot.models import SearchQuery
from jobbot.pipeline import collect
from jobbot.sources import build_sources
from jobbot.sources.email_alerts import EmailAlertsSource, MailSettings, MailImportError
from jobbot.sources.mail_parser import parse_alert, job_link

QUERY = SearchQuery(keywords=("developer",), countries=("CR",), residence_country="CR")
SETTINGS = MailSettings()


def email(html=None, sender="Jobs <jobs-noreply@linkedin.com>", plain=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "test@gmail.com"
    msg["Subject"] = "Alertas de empleo"
    msg["Date"] = "Sun, 25 Oct 2026 10:00:00 -0600"
    msg.set_content(plain or "Alerta de trabajo")
    if html is not None:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


LINKEDIN = '<div><a href="https://www.linkedin.com/comm/jobs/view/12345?trackingId=abc">Backend Developer</a><p>Empresa: Acme</p><p>Ubicación: Costa Rica</p><p>Python y APIs</p></div>'


class ParserTests(unittest.TestCase):
    def test_linkedin_html_summary_has_real_identity_and_no_fake_publication_date(self):
        result = parse_alert(email(LINKEDIN))
        self.assertEqual(result.status, "parsed")
        posting, = result.postings
        self.assertEqual(posting.external_id, "12345")
        self.assertEqual(posting.url, "https://www.linkedin.com/jobs/view/12345")
        self.assertEqual(posting.title, "Backend Developer")
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(posting.country, "CR")
        self.assertIsNone(posting.posted_at)
        self.assertTrue(posting.raw["description_incomplete"])
        self.assertNotIn("test@gmail.com", posting.description)

    def test_multiple_jobs_keep_their_own_metadata(self):
        second = LINKEDIN.replace("12345", "23456").replace("Backend", "Frontend").replace("Acme", "Beta")
        result = parse_alert(email(LINKEDIN + second))
        self.assertEqual([p.company for p in result.postings], ["Acme", "Beta"])
        self.assertNotIn("Beta", result.postings[0].description)

    def test_duplicate_card_links_are_deduplicated(self):
        result = parse_alert(email(LINKEDIN + '<a href="https://www.linkedin.com/jobs/view/12345">View job</a>'))
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.status, "parsed")

    def test_partially_recognized_email_is_marked_for_review(self):
        result = parse_alert(email(LINKEDIN + '<a href="https://www.linkedin.com/jobs/view/99999">View job</a>'))
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.status, "needs_review")

    def test_indeed_plaintext_and_tracking_id(self):
        content = "Backend Developer\nEmpresa: Acme\nUbicación: Costa Rica\nhttps://cr.indeed.com/rc/clk?jk=abc123def456&utm_source=email"
        result = parse_alert(email(sender="Indeed <alert@indeed.com>", plain=content))
        self.assertEqual(result.postings[0].external_id, "abc123def456")
        self.assertEqual(result.postings[0].source, "indeed_email")

    def test_computrabajo_job_link(self):
        url = "https://cr.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-backend-developer-ABCDEF0123456789?utm_source=email"
        result = parse_alert(email(f'<a href="{url}">Backend Developer</a><p>Ubicación: Costa Rica</p>', sender="Computrabajo <alertas@computrabajo.com>"))
        self.assertEqual(result.postings[0].external_id, "abcdef0123456789")

    def test_computrabajo_wrapped_vacancy_is_decoded_without_following_links(self):
        from urllib.parse import quote
        target = "https://cr.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-developer-ABCDEF0123456789"
        wrapper = "https://go.computrabajo.com/go/goa?url=" + quote(target, safe="")
        result = parse_alert(email(f'<a href="{wrapper}">Backend Developer</a>', sender="alertas@computrabajo.com"))
        self.assertEqual(result.postings[0].url, target)
        self.assertEqual(result.postings[0].external_id, "abcdef0123456789")

    def test_computrabajo_general_search_is_not_a_vacancy(self):
        from urllib.parse import quote
        target = "https://cr.computrabajo.com/ofertas-de-trabajo/?q=developer"
        wrapper = "https://go.computrabajo.com/go/goa?url=" + quote(target, safe="")
        result = parse_alert(email(f'<a href="{wrapper}">Ver oportunidades</a>', sender="alertas@computrabajo.com"))
        self.assertEqual(result.postings, [])
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(job_link("https://go.computrabajo.com/go/goa?url=https%3A%2F%2Fevil.test%2Fofertas-de-trabajo%2Foferta-de-trabajo-de-developer-ABCDEF0123456789", "computrabajo"))

    def test_untrusted_sender_and_spoofed_link_domains_are_not_imported(self):
        self.assertEqual(parse_alert(email(LINKEDIN, sender="x@linkedin.com.evil.test")).status, "ignored")
        for url in ("https://linkedin.com.evil.test/jobs/view/123", "javascript:alert(1)", "https://example.com/jobs/view/123"):
            self.assertIsNone(job_link(url, "linkedin"))

    def test_unknown_trackers_and_missing_titles_are_not_invented(self):
        result = parse_alert(email('<a href="https://click.example.com/opaque">Backend Developer</a>'))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.postings, [])

    def test_ambiguous_plaintext_block_does_not_assign_one_title_to_two_jobs(self):
        content = "Backend Developer\nhttps://www.linkedin.com/jobs/view/12345\nFrontend Developer\nhttps://www.linkedin.com/jobs/view/23456"
        result = parse_alert(email(plain=content))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.postings, [])

    def test_tracking_parameters_and_footer_addresses_are_not_stored_in_summary(self):
        text = "Backend Developer\nEmpresa: Acme\nhttps://www.linkedin.com/jobs/view/12345?trackingId=private-tracking\nprivate@example.com"
        posting, = parse_alert(email(plain=text)).postings
        self.assertNotIn("private-tracking", posting.description)
        self.assertNotIn("private@example.com", posting.description)

    def test_attachments_are_not_parsed_as_alert_body(self):
        msg = EmailMessage()
        msg["From"] = "jobs-noreply@linkedin.com"
        msg.set_content("Sin alertas")
        msg.add_attachment(LINKEDIN.encode(), maintype="text", subtype="html", filename="attachment.html")
        self.assertEqual(parse_alert(msg.as_bytes()).postings, [])


class TrabajosDiariosTests(unittest.TestCase):
    sender = "Trabajos Diarios <alertas@trabajosdiarios.co.cr>"
    url = "https://trabajosdiarios.co.cr/trabajo/3102907/backend-developer-en-san-jose"

    def card(self, identifier="3102907", title="Backend Developer", company="Acme", location="San José"):
        return f'<a href="https://trabajosdiarios.co.cr/trabajo/{identifier}/backend-developer-en-san-jose?utm_source=email&amp;kna=private"><div>{title}</div><table><tr><td>{company}</td><td><table><tr><td>📍</td><td>{location}</td></tr></table></td></tr></table><div>Tiempo Completo</div></a>'

    def test_real_card_structure_separates_title_company_and_location(self):
        result = parse_alert(email(self.card(), sender=self.sender))
        self.assertEqual(result.status, "parsed")
        posting, = result.postings
        self.assertEqual(posting.title, "Backend Developer")
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(posting.location, "San José")
        self.assertEqual(posting.country, "CR")
        self.assertEqual(posting.source, "trabajosdiarios_email")
        self.assertEqual(posting.url, self.url)
        self.assertNotIn("private", posting.description)
        self.assertIsNone(posting.posted_at)

    def test_multiple_cards_do_not_mix_companies_or_metadata(self):
        result = parse_alert(email(self.card() + self.card("3103216", "Frontend Developer", "Beta", "Heredia"), sender=self.sender))
        self.assertEqual(len(result.postings), 2)
        self.assertEqual([p.company for p in result.postings], ["Acme", "Beta"])
        self.assertNotIn("Beta", result.postings[0].description)
        self.assertNotIn("Frontend", result.postings[0].description)

    def test_plaintext_and_older_costa_rica_hostname_share_identity(self):
        content = f"Backend Developer\nEmpresa: Acme\nUbicación: Costa Rica\n{self.url}"
        posting, = parse_alert(email(plain=content, sender="alertas@trabajosdiarios.com")).postings
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(job_link(self.url.replace("trabajosdiarios.co.cr", "cr.trabajosdiarios.com"), "trabajosdiarios"), (posting.external_id, self.url))

    def test_search_unsubscribe_spoofed_domains_and_invalid_ids_are_rejected(self):
        for url in ("https://trabajosdiarios.co.cr/ofertas-trabajo", "https://trabajosdiarios.co.cr/candidatos/baja/alertas", "https://trabajosdiarios.co.cr/trabajo/no-id/titulo", "https://trabajosdiarios.co.cr.evil.test/trabajo/123/titulo", "https://evil.test/trabajo/123/titulo", "javascript:alert(1)", "https://user:secret@trabajosdiarios.co.cr/trabajo/123/titulo"):
            self.assertIsNone(job_link(url, "trabajosdiarios"))
        self.assertEqual(parse_alert(email(self.card(), sender="x@trabajosdiarios.co.cr.evil.test")).status, "ignored")

    def test_button_without_title_is_not_imported_as_job(self):
        result = parse_alert(email(f'<a href="{self.url}">Ver oferta</a>', sender=self.sender))
        self.assertEqual(result.postings, [])
        self.assertEqual(result.status, "needs_review")


class FakeGmail:
    def __init__(self, messages=None, failure_id=None):
        self.messages = messages if messages is not None else {"one": email(LINKEDIN)}
        self.failure_id = failure_id
        self.calls = []

    def label_id(self, name):
        self.calls.append(("label", name))
        return "label-1"

    def message_ids(self, label):
        self.calls.append(("list", label))
        return list(self.messages), False

    def message_size(self, identifier):
        self.calls.append(("size", identifier))
        if identifier == self.failure_id:
            raise RuntimeError("temporary read error")
        return len(self.messages[identifier])

    def raw_message(self, identifier):
        self.calls.append(("raw", identifier))
        return self.messages[identifier]


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        self.db.execute("insert into gmail_connections (id,email,folder,encrypted_credentials,enabled) values (?,?,?,?,?)",
                        ("gmail", "test@gmail.com", "JobAlerts", "encrypted-test", True))
        self.db.conn.commit()
        token = patch("jobbot.gmail_oauth.access_token", return_value="test-token")
        token.start()
        self.addCleanup(token.stop)

    def tearDown(self):
        self.db.close()

    def source(self, client=None, settings=SETTINGS):
        client = client or FakeGmail()
        return EmailAlertsSource(settings=settings, client_factory=lambda token: client), client

    def test_pipeline_imports_once_and_preserves_processed_ids(self):
        source, client = self.source()
        summary, = collect(self.db, [source], QUERY)
        self.assertEqual(summary.inserted, 1)
        first_count = len([call for call in client.calls if call[0] == "raw"])
        again, = collect(self.db, [source], QUERY)
        self.assertEqual(again.inserted, 0)
        self.assertEqual(len([call for call in client.calls if call[0] == "raw"]), first_count)
        self.assertEqual(len(self.db.query("select * from email_imports")), 1)
        self.assertTrue(all(call[0] in ("label", "list", "size", "raw") for call in client.calls))

    def test_database_failure_does_not_acknowledge_messages(self):
        source, _ = self.source()
        with patch("jobbot.pipeline.repo.insert_job_if_new", side_effect=RuntimeError("database failure")):
            with self.assertLogs("jobbot", level="ERROR"):
                result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.db.query("select * from email_imports"), [])
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)

    def test_new_message_id_does_not_duplicate_same_job(self):
        source, _ = self.source()
        collect(self.db, [source], QUERY)
        source, _ = self.source(FakeGmail({"two": email(LINKEDIN)}))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(len(self.db.query("select * from email_imports")), 2)

    def test_duplicate_digest_cards_keep_the_richer_metadata(self):
        short = '<div><a href="https://www.linkedin.com/jobs/view/12345">Backend Developer</a></div>'
        source, _ = self.source(FakeGmail({"one": email(LINKEDIN), "two": email(short)}))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(repo.list_jobs(self.db)[0]["company"], "Acme")
        source, _ = self.source(FakeGmail({"three": email(short)}))
        collect(self.db, [source], QUERY)
        self.assertEqual(repo.list_jobs(self.db)[0]["company"], "Acme")

    def test_old_parser_checkpoint_is_reprocessed_once(self):
        from jobbot import repo
        mailbox_key = hashlib.sha256(b"gmail-api|test@gmail.com|JobAlerts").hexdigest()
        self.db.execute("insert into email_imports (mailbox_key,uid_validity,uid,message_hash,status,job_count,processed_at) values (?,?,?,?,?,?,?)", (mailbox_key,"1","one","old-digest","parsed",1,repo.now()))
        self.db.conn.commit()
        source, client = self.source()
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)
        count = len([call for call in client.calls if call[0] == "raw"])
        again, = collect(self.db, [source], QUERY)
        self.assertEqual(again.inserted, 0)
        self.assertEqual(len([call for call in client.calls if call[0] == "raw"]), count)

    def test_message_error_preserves_other_messages_and_retries_failure(self):
        client = FakeGmail({"one": email(LINKEDIN), "two": email(LINKEDIN.replace("12345", "23456"))}, failure_id="two")
        source, _ = self.source(client)
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.details["read_errors"], 1)
        self.assertEqual([row["uid"] for row in self.db.query("select uid from email_imports")], ["one"])
        client.failure_id = None
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)

    def test_unknown_template_is_recorded_for_review(self):
        source, _ = self.source(FakeGmail({"one": email("Unrecognized template")}))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.inserted, 0)
        self.assertEqual(self.db.query("select status from email_imports")[0]["status"], "needs_review")

    def test_unknown_location_is_not_invented(self):
        source, _ = self.source(FakeGmail({"one": email('<a href="https://www.linkedin.com/jobs/view/12345">Backend Developer</a>')}))
        collect(self.db, [source], QUERY)
        job = repo.list_jobs(self.db)[0]
        self.assertIsNone(job["country"])
        self.assertTrue(job["location_needs_review"])

    def test_known_foreign_country_is_filtered(self):
        from dataclasses import replace
        source, _ = self.source(FakeGmail({"one": email(LINKEDIN.replace("Costa Rica", "United States"))}), replace(SETTINGS, apply_search_filters=True))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(self.db.query("select status from email_imports")[0]["status"], "filtered")

    def test_batch_limit_leaves_backlog_for_next_run(self):
        from dataclasses import replace
        client = FakeGmail({"one": email(LINKEDIN), "two": email(LINKEDIN.replace("12345", "23456"))})
        source, _ = self.source(client, replace(SETTINGS, max_messages=1))
        first, = collect(self.db, [source], QUERY)
        self.assertEqual(first.status, "partial")
        second, = collect(self.db, [source], QUERY)
        self.assertEqual(second.status, "success")
        self.assertEqual(len(repo.list_jobs(self.db)), 2)

    def test_review_messages_can_be_reprocessed(self):
        from dataclasses import replace
        client = FakeGmail({"one": email("Unknown template")})
        source, _ = self.source(client)
        collect(self.db, [source], QUERY)
        client.messages["one"] = email(LINKEDIN)
        source, _ = self.source(client, replace(SETTINGS, reprocess_review=True))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(self.db.query("select status from email_imports")[0]["status"], "parsed")

    def test_previously_ignored_provider_can_be_reexamined_without_duplicates(self):
        from dataclasses import replace
        client = FakeGmail({"one": email("unsupported", sender="x@example.com")})
        source, _ = self.source(client)
        collect(self.db, [source], QUERY)
        self.assertEqual(self.db.query("select status from email_imports")[0]["status"], "ignored")
        client.messages["one"] = email(TrabajosDiariosTests().card(), sender=TrabajosDiariosTests.sender)
        source, _ = self.source(client, replace(SETTINGS, reprocess_review=True))
        result, = collect(self.db, [source], QUERY)
        self.assertEqual(result.inserted, 1)
        again, = collect(self.db, [source], QUERY)
        self.assertEqual(again.inserted, 0)

    def test_migration_keeps_checkpoints(self):
        source, _ = self.source()
        collect(self.db, [source], QUERY)
        before = self.db.query("select * from email_imports")
        self.db.migrate()
        self.assertEqual(self.db.query("select * from email_imports"), before)

    def test_disconnected_mail_does_not_fall_back_to_passwords(self):
        self.db.execute("update gmail_connections set enabled = ?", (False,))
        self.db.conn.commit()
        source, _ = self.source()
        with self.assertLogs("jobbot", level="ERROR"):
            result, = collect(self.db, [source], QUERY)
        self.assertIn("Conecta Gmail desde Ajustes", result.error)
        self.assertFalse(any(call[0] == "raw" for call in source._client_factory("x").calls))


class SettingsTests(unittest.TestCase):
    def test_settings_only_contain_import_limits(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(MailSettings.from_env().max_messages, 50)

    def test_invalid_limits_are_explained(self):
        for value in ("invalid", "0", "10000"):
            with patch.dict(os.environ, {"MAIL_MAX_MESSAGES": value}, clear=True):
                with self.assertRaises(MailImportError):
                    MailSettings.from_env()

    def test_import_requires_oauth_connection(self):
        with self.assertRaisesRegex(ValueError, "Conecta Gmail"):
            build_sources(["email_alerts"])


if __name__ == "__main__":
    unittest.main()
