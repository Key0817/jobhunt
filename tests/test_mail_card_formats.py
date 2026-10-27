import base64
import gzip
import json
from dataclasses import replace
from email.message import EmailMessage
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote

from jobbot import repo
from jobbot.db import Database
from jobbot.models import SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.mail_parser import parse_alert, job_link
from jobbot.sources.mail_urls import unwrap, AlertLinkResolver
from jobbot.sources.computrabajo_alerts import ComputrabajoAlertSearch
from jobbot.sources.email_alerts import EmailAlertsSource, MailSettings
from jobbot.mail_imports import collection_summary


def mail(html, sender="jobs@linkedin.com"):
    message = EmailMessage()
    message["From"] = sender
    message.set_content("Job alert")
    message.add_alternative(html, subtype="html")
    return message.as_bytes()


def drip(url):
    payload = base64.urlsafe_b64encode(json.dumps({"url": url}).encode()).decode().rstrip("=")
    return "https://t.dripemail2.com/c/header." + payload + ".signature"


class CardParserTests(unittest.TestCase):
    def test_linkedin_all_titles_and_company_boundaries(self):
        titles = ["Desarrollador web", "System Engineer", "Workfront Fusion Developer", "Senior Mobile & React Native Engineer"]
        cards = "".join(f'<table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/{10000+i}"><img src="logo.png"></a></td><td><a href="https://www.linkedin.com/comm/jobs/view/{10000+i}">{title}</a><p>Empresa {i} · San José, Costa Rica (Híbrido)</p><span>Solicitud sencilla</span></td></tr></table>' for i, title in enumerate(titles))
        result = parse_alert(mail(cards))
        self.assertEqual(result.status, "parsed")
        self.assertEqual([posting.title for posting in result.postings], titles)
        self.assertEqual([posting.company for posting in result.postings], [f"Empresa {i}" for i in range(4)])
        self.assertNotIn("Empresa 1", result.postings[0].description)

    def test_indeed_multiple_cards_and_embedded_gzip_destination(self):
        target = "https://cr.indeed.com/rc/clk?jk=abc123def456&trackingId=private"
        encoded = base64.urlsafe_b64encode(gzip.compress(json.dumps({"u": target, "m": "tracking"}).encode())).decode().rstrip("=")
        first = f'<div><a href="https://cts.indeed.com/v3/{encoded}">Founding Engineer (Python, TypeScript &amp; React)</a><p>Acme</p><p>San José, Costa Rica</p></div>'
        second = '<div><a href="https://cr.indeed.com/rc/clk/dl?jk=def456abc123">IT Support Technician</a><p>Beta</p><p>Heredia, Costa Rica</p></div>'
        result = parse_alert(mail(first+second, "alert@jobalert.indeed.com"))
        self.assertEqual(len(result.postings), 2)
        self.assertEqual(result.postings[0].company, "Acme")
        self.assertEqual(result.postings[0].url, "https://www.indeed.com/viewjob?jk=abc123def456")
        self.assertNotIn("private", result.postings[0].description)
        self.assertNotIn("Beta", result.postings[0].description)

    def test_dynamite_decodes_newsletter_jobs_and_ignores_subscription_actions(self):
        first = drip("https://dynamitejobs.com/company/acme/remote-job/backend-developer?utm_source=mail")
        second = drip("https://dynamitejobs.com/company/beta/remote-job/marketing-manager")
        footer = drip("https://www.getdrip.com/preferences/recipient-private")
        result = parse_alert(mail(f'<h2>Latest Remote Jobs</h2><p><a href="{first}">Backend Developer - Acme</a></p><p><a href="{second}">Marketing Manager - Beta</a></p><a href="{footer}">Update your preferences</a>', "jobs@dynamitejobs.com"))
        self.assertEqual(result.status, "parsed")
        self.assertEqual(len(result.postings), 2)
        self.assertEqual(result.postings[0].company, "Acme")
        self.assertEqual(result.postings[0].title, "Backend Developer")
        self.assertEqual(result.postings[0].source, "dynamitejobs_email")
        self.assertNotIn("?", result.postings[0].url)

    def test_resolver_is_injected_for_sponsored_indeed_links(self):
        raw = mail('<div><a href="https://cr.indeed.com/pagead/clk/dl?ad=opaque">IAM Developer</a><p>Acme</p></div>', "alerts@indeed.com")
        result = parse_alert(raw, resolve_url=lambda url, provider: "https://www.indeed.com/viewjob?jk=abc123def456")
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.unresolved_links, 0)

    def test_sponsored_link_can_use_a_public_canonical_header_in_html(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.geturl.return_value = "https://cr.indeed.com/pagead/clk/dl?ad=opaque"
        response.headers = {"Content-Type": "text/html"}
        response.read.return_value = b'<link rel="canonical" href="https://www.indeed.com/viewjob?jk=abc123def456">'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch("jobbot.sources.mail_urls.build_opener", return_value=opener):
            result = AlertLinkResolver(deadline=999999, clock=lambda: 0)(response.geturl.return_value, "indeed")
        self.assertEqual(result, "https://www.indeed.com/viewjob?jk=abc123def456")

    def test_legacy_indeed_engage_redirect_is_resolved_once_and_cached(self):
        from unittest.mock import MagicMock
        url = "https://engage.indeed.com/f/a/opaque"
        destination = "https://cr.indeed.com/viewjob?jk=abc123def456"
        opener = MagicMock()
        opener.open.side_effect = HTTPError(url, 302, "redirect", {"Location": destination}, None)
        resolver = AlertLinkResolver(deadline=999999, clock=lambda: 0)
        with patch("jobbot.sources.mail_urls.build_opener", return_value=opener):
            self.assertEqual(resolver(url, "indeed"), destination)
            self.assertEqual(resolver(url, "indeed"), destination)
        self.assertEqual(opener.open.call_count, 1)

    def test_feedback_and_unsubscribe_links_are_never_navigated(self):
        with patch.object(AlertLinkResolver, "__call__") as resolver:
            parse_alert(mail('<a href="https://cts.indeed.com/v3/invalid">Este empleo es una mala coincidencia</a><a href="https://cts.indeed.com/v3/invalid">Pausar estos emails</a>', "alerts@indeed.com"), resolve_url=resolver)
            resolver.assert_not_called()

    def test_legacy_indeed_search_and_preferences_links_are_not_vacancies(self):
        labels = ["Ver todos los empleos", "desde ayer", "en los últimos 7 días", "Editar", "Términos", "Administrar alertas de empleo"]
        raw = mail("".join(f'<a href="https://engage.indeed.com/f/a/opaque-{i}">{label}</a>' for i, label in enumerate(labels)), "alert@indeed.com")
        with patch.object(AlertLinkResolver, "__call__") as resolver:
            result = parse_alert(raw, resolve_url=resolver)
        resolver.assert_not_called()
        self.assertEqual(result.unresolved_links, 0)

    def test_tracker_payload_cannot_point_to_foreign_or_local_hosts(self):
        for target in ("https://evil.test/company/acme/remote-job/dev", "http://127.0.0.1/admin", "https://dynamitejobs.com.evil.test/company/acme/remote-job/dev"):
            self.assertIsNone(job_link(drip(target), "dynamitejobs"))

    def test_malformed_unrelated_href_does_not_drop_valid_cards(self):
        raw = mail('<a href="http://[broken">Unrelated</a><a href="https://dynamitejobs.com/company/acme/remote-job/developer">Backend Developer - Acme</a>', "jobs@dynamitejobs.com")
        self.assertEqual(len(parse_alert(raw).postings), 1)

    def test_compressed_wrapper_size_is_bounded(self):
        encoded = base64.urlsafe_b64encode(gzip.compress(b"x"*200000)).decode().rstrip("=")
        url = "https://cts.indeed.com/v3/" + encoded
        self.assertEqual(unwrap(url, "indeed"), url)


class SearchExpansionTests(unittest.TestCase):
    def test_computrabajo_expands_search_into_individual_jobs(self):
        target = "https://cr.computrabajo.com/ofertas-de-trabajo/?q=developer&prov=1"
        href = "https://cr.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-developer-ABCDEF0123456789"
        source = ComputrabajoAlertSearch(fetch_text=lambda url: "User-agent: *\nDisallow:\n" if url.endswith("robots.txt") else f'<div id="offers-grid"><article><a href="{href}">Backend Developer</a><p>Empresa: Acme</p><p>Ubicación: Costa Rica</p></article></div>')
        result = source.fetch_search(target)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, href)
        self.assertEqual(result.status, "success")

    def test_computrabajo_403_is_reported_and_not_hammered_for_every_message(self):
        calls = []
        def fetch(url):
            calls.append(url)
            raise HTTPError(url, 403, "blocked", {}, None)
        source = ComputrabajoAlertSearch(fetch_text=fetch)
        first = source.fetch_search("https://cr.computrabajo.com/ofertas-de-trabajo/?q=developer")
        second = source.fetch_search("https://cr.computrabajo.com/ofertas-de-trabajo/?q=engineer")
        self.assertEqual(first.details["reason"], "http_403")
        self.assertEqual(second.status, "partial")
        self.assertEqual(len(calls), 1)


class AllCardsImportTests(unittest.TestCase):
    def setUp(self):
        self.db = Database("sqlite:///:memory:")
        self.db.init_schema()
        self.addCleanup(self.db.close)
        self.db.execute("insert into gmail_connections (id,email,folder,encrypted_credentials,enabled) values (?,?,?,?,?)", ("gmail", "test@gmail.com", "JobAlerts", "encrypted-test", True))
        self.db.conn.commit()
        token = patch("jobbot.gmail_oauth.access_token", return_value="test-token")
        token.start()
        self.addCleanup(token.stop)

    def source(self, settings=None):
        cards = '<div><a href="https://www.linkedin.com/jobs/view/12345">Backend Developer</a><p>Empresa: Acme</p></div><div><a href="https://www.linkedin.com/jobs/view/23456">System Engineer</a><p>Empresa: Beta</p></div>'
        class Client:
            def label_id(self, name): return "label"
            def message_ids(self, label, unread_only=True): return ["one"], False
            def message_size(self, identifier): return len(mail(cards))
            def raw_message(self, identifier): return mail(cards)
        return EmailAlertsSource(settings or MailSettings(), client_factory=lambda _: Client())

    def test_all_cards_import_by_default_and_rerun_does_not_duplicate(self):
        query = SearchQuery(keywords=("developer",), countries=("CR",))
        source = self.source()
        result, = collect(self.db, [source], query)
        self.assertEqual(result.inserted, 2)
        self.assertEqual(result.details["cards_found"], 2)
        self.assertEqual(result.details["filtered_offers"], 0)
        self.assertEqual(collect(self.db, [source], query)[0].inserted, 0)

    def test_reprocessing_repairs_old_email_metadata_without_changing_status(self):
        query = SearchQuery(keywords=("developer",), countries=("CR",))
        collect(self.db, [self.source()], query)
        job = repo.list_jobs(self.db)[0]
        repo.change_status(self.db, job["id"], "solicitada")
        self.db.execute("update jobs set title = ? where id = ?", ("Old concatenated title", job["id"]))
        self.db.conn.commit()
        collect(self.db, [self.source(replace(MailSettings(), reprocess_all=True))], query)
        detail = repo.get_job(self.db, job["id"])
        self.assertEqual(detail["status"], "solicitada")
        self.assertNotEqual(detail["title"], "Old concatenated title")

    def test_title_filter_is_explicit_opt_in_and_discard_count_is_visible(self):
        query = SearchQuery(keywords=("developer",), countries=("CR",))
        result, = collect(self.db, [self.source(replace(MailSettings(), apply_search_filters=True))], query)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.details["filtered_offers"], 1)

    def test_one_off_read_mail_recovery_does_not_change_default_unread_mode(self):
        source = self.source(replace(MailSettings(), unread_only=False, reprocess_all=True))
        query = SearchQuery(keywords=("developer",), countries=("CR",))
        self.assertEqual(collect(self.db, [source], query)[0].inserted, 2)
        self.assertEqual(collect(self.db, [source], query)[0].inserted, 0)
        self.assertTrue(MailSettings().unread_only)
