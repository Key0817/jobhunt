import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from jobbot.catalog import integrate_pending, verify_board, apply_verification
from jobbot.models import SearchQuery
from jobbot.sources import build_sources
from jobbot.sources.companies import Company
from jobbot.sources.career_feeds import CareerFeedSource
from jobbot.sources.public_company import PublicCompanySource


QUERY = SearchQuery(keywords=("developer",), countries=("CR",), residence_country="CR")


def schema(title="Backend Developer", country="CR"):
    return {"@type": "JobPosting", "title": title, "description": "<p>Build APIs</p>",
            "identifier": {"value": "vacancy-1"}, "hiringOrganization": {"name": "Acme"},
            "jobLocation": {"address": {"addressLocality": "Heredia", "addressCountry": country}}}


def html(item):
    return '<html><script type="application/ld+json">' + json.dumps(item) + '</script></html>'


class PublicCompanyTests(unittest.TestCase):
    def company(self):
        return Company("acme", "Acme", "public_web", "acme", "https://example.com/careers")

    def test_follows_public_job_links_and_filters_country_before_storage(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith("robots.txt"):
                return "User-agent: *\nDisallow: /private/"
            if url.endswith("/careers"):
                return '<html><a href="/jobs/123">Backend Developer</a><a href="https://evil.test/jobs/1">Developer</a><a href="/private/jobs/2">Developer</a></html>'
            return html(schema())
        result = PublicCompanySource(self.company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, "https://example.com/jobs/123")
        self.assertEqual(result[0].country, "CR")
        self.assertEqual(result[0].description, "Build APIs")
        self.assertFalse(any("evil.test" in url or "/private/" in url for url in calls))

    def test_discovers_a_greenhouse_embed_and_keeps_company_provenance(self):
        from jobbot.sources.base import FetchResult
        from jobbot.models import JobPosting
        def fetch(url):
            return "" if url.endswith("robots.txt") else '<html><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe></html>'
        posting = JobPosting(source="greenhouse:acme", external_id="1", title="Developer", company="Acme", url="https://job-boards.greenhouse.io/acme/jobs/1", description="Build APIs", country="CR")
        with patch.object(PublicCompanySource, "_delegate", return_value=FetchResult([posting])) as delegate:
            result = PublicCompanySource(self.company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(result[0].source, "public_web:acme")
        self.assertEqual(result[0].raw["origin_platform"], "greenhouse")
        self.assertEqual(delegate.call_args.args[0].board_id, "acme")
        self.assertFalse(result.details["requires_adapter_review"])

    def test_ordinary_page_without_job_schema_is_partial_not_empty_success(self):
        result = PublicCompanySource(self.company(), fetch_text=lambda _: '<html><p>Welcome</p></html>').fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.details["requires_adapter_review"])
        self.assertEqual(len(result), 0)

    def test_page_limit_is_reported_without_losing_saved_candidates(self):
        def fetch(url):
            if url.endswith("robots.txt"):
                return ""
            return '<html><a href="/jobs/123">Developer</a><a href="/jobs/456">Developer</a></html>' if url.endswith("careers") else html(schema())
        source = PublicCompanySource(self.company(), fetch_text=fetch)
        source.MAX_PAGES = 2
        result = source.fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertIn("page_limit_reached", result.details["failures"])

    def test_external_schema_url_and_unknown_geography_are_not_assumed_local(self):
        for item in ({**schema(), "url": "https://evil.test/jobs/1"}, {**schema(), "jobLocation": {}}, schema(country="US")):
            result = PublicCompanySource(self.company(), fetch_text=lambda url: "" if url.endswith("robots.txt") else html(item)).fetch(QUERY)
            self.assertEqual(len(result), 0)


class CareerFeedTests(unittest.TestCase):
    def source(self, platform, data, detail=""):
        host = {"workable": "apply.workable.com", "bamboohr": "acme.bamboohr.com", "breezy": "acme.breezy.hr", "recruitee": "acme.recruitee.com"}[platform]
        company = Company("acme", "Acme", platform, "acme", f"https://{host}/acme")
        def fetch(url):
            if url.endswith("robots.txt"):
                return ""
            if url.endswith("/detail") or "/j/" in url:
                return detail
            return json.dumps(data)
        return CareerFeedSource(company, fetch_text=fetch)

    def test_workable_fetches_description_for_an_eligible_listing(self):
        data = {"jobs": [{"shortcode": "ABC123", "title": "Developer", "telecommuting": True, "locations": [{"countryCode": "CR", "country": "Costa Rica"}]}]}
        result = self.source("workable", data, html(schema())).fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertIn("Build APIs", result[0].description)

    def test_bamboohr_uses_title_geography_and_public_detail(self):
        data = {"result": [{"id": "123", "jobOpeningName": "Developer - Costa Rica", "atsLocation": {}}]}
        detail = json.dumps({"result": {"jobOpening": {"jobOpeningStatus": "Open", "description": "<p>Build APIs</p>"}}})
        result = self.source("bamboohr", data, detail).fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].country, "CR")

    def test_recruitee_and_breezy_feed_descriptions_and_empty_boards(self):
        item = {"id": 1, "title": "Developer", "country_code": "CR", "country": "Costa Rica", "description": "Build APIs", "careers_url": "https://careers.example.com/o/dev"}
        self.assertEqual(len(self.source("recruitee", {"offers": [item]}).fetch(QUERY)), 1)
        self.assertEqual(self.source("breezy", []).fetch(QUERY).status, "success")


class PendingMigrationTests(unittest.TestCase):
    def test_all_pending_rows_are_consolidated_and_duplicates_do_not_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            companies, pending = Path(folder)/"companies.yml", Path(folder)/"pending.yml"
            companies.write_text("companies: []\n", encoding="utf-8")
            pending.write_text(yaml.safe_dump({"companies": [
                {"name": "Acme", "url": "https://example.com/careers#jobs"},
                {"name": "Acme duplicate", "url": "https://www.example.com/careers/"},
                {"name": "Bamboo", "url": "https://acme.bamboohr.com/careers"},
                {"name": "Elempleo", "url": "https://www.elempleo.com/cr/ofertas-empleo/"}]}), encoding="utf-8")
            result = integrate_pending(companies, pending)
            self.assertEqual(result["added"], 3)
            self.assertEqual(result["duplicates_merged"], 1)
            self.assertEqual(yaml.safe_load(pending.read_text())["companies"], [])
            rows = yaml.safe_load(companies.read_text())["companies"]
            self.assertEqual({row["platform"] for row in rows}, {"bamboohr", "public_web"})
            self.assertEqual(next(row for row in rows if row["name"] == "Elempleo")["managed_source"], "email_alerts")
            self.assertTrue(all(not row["enabled"] for row in rows))
            self.assertEqual(integrate_pending(companies, pending)["added"], 0)

    def test_shards_cover_each_enabled_custom_company_exactly_once(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/"companies.yml"
            rows = [{"id": f"acme-{i}", "name": "Acme", "platform": "public_web", "board_id": f"board-{i}", "careers_url": "https://example.com/careers", "enabled": True} for i in range(17)]
            file.write_text(yaml.safe_dump({"companies": rows}), encoding="utf-8")
            with patch.dict("os.environ", {"JOBBOT_COMPANIES": str(file)}):
                combined = build_sources([f"public_web_{i}" for i in range(8)])
                all_sources = build_sources(["public_web"])
            self.assertEqual(len(combined), 17)
            self.assertEqual({source.name for source in combined}, {source.name for source in all_sources})

    def test_temporary_network_failure_is_retried_but_auth_and_robots_are_not(self):
        row = {"enabled": False}
        apply_verification(row, {"status": "unavailable", "error_type": "TimeoutError"})
        self.assertTrue(row["enabled"])
        self.assertTrue(row["retry_pending"])
        for result in ({"status": "robots_blocked"}, {"status": "http_error", "http_status": 403}, {"status": "requires_account"}, {"status": "unavailable", "error_type": "ValueError"}):
            apply_verification(row, result)
            self.assertFalse(row["enabled"])
            self.assertFalse(row["retry_pending"])

    def test_managed_and_account_sources_do_not_scrape(self):
        row = {"id": "elempleo", "name": "Elempleo", "platform": "public_web", "board_id": "elempleo", "careers_url": "https://www.elempleo.com/", "managed_source": "email_alerts"}
        with patch("jobbot.sources.public_company.PublicCompanySource._get") as get:
            self.assertEqual(verify_board(row)["status"], "managed_source")
            get.assert_not_called()
