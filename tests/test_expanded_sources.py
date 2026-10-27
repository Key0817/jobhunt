import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import yaml

from jobbot.catalog import company_from_url, import_links, verify_board
from jobbot.models import SearchQuery
from jobbot.sources.ashby import AshbySource
from jobbot.sources.companies import Company, load_companies
from jobbot.sources.dynamitejobs import DynamiteJobsSource
from jobbot.sources.greenhouse import GreenhouseSource
from jobbot.sources.lever import LeverSource
from jobbot.sources.procomer import ProcomerSource
from jobbot.sources.public_web import PublicWebSource, robots_allowed
from jobbot.sources.workday import WorkdaySource
from jobbot.sources.mail_parser import parse_alert
from email.message import EmailMessage


QUERY = SearchQuery(keywords=("developer",), countries=("CR",), residence_country="CR")


def company(platform):
    url = "https://example.wd1.myworkdayjobs.com/Careers" if platform == "workday" else "https://example.com"
    return Company("example", "Example", platform, "Careers", url)


class ExpandedCompanyTests(unittest.TestCase):
    def test_lever_paginates_and_retains_description_and_country_eligibility(self):
        calls = []
        def fetch(url):
            calls.append(url)
            offset = int(parse_qs(urlsplit(url).query)["skip"][0])
            if offset >= 2:
                return []
            return [{"id": str(offset), "text": "Backend Developer", "hostedUrl": f"https://jobs.lever.co/example/{offset}",
                     "categories": {"location": "Costa Rica" if not offset else "United States"},
                     "descriptionPlain": "Build APIs", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}]}]
        source = LeverSource(company("lever"), fetch_json=fetch)
        source.PAGE_SIZE = 1
        result = source.fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("Python", result[0].description)
        self.assertEqual(result[0].country, "CR")

    def test_lever_page_failure_preserves_already_found_jobs(self):
        def fetch(url):
            if "skip=0" not in url:
                raise TimeoutError()
            return [{"id": "1", "text": "Developer", "hostedUrl": "https://example.com/1", "categories": {"location": "Remote LATAM"}, "workplaceType": "remote"}]
        source = LeverSource(company("lever"), fetch_json=fetch, sleep=lambda _: None)
        source.PAGE_SIZE = 1
        result = source.fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result), 1)

    def test_ashby_secondary_locations_private_jobs_and_remote_restrictions(self):
        item = {"title": "Developer", "jobUrl": "https://jobs.ashbyhq.com/example/abc", "descriptionPlain": "Build APIs", "location": "US", "isRemote": True,
                "secondaryLocations": [{"location": "Costa Rica"}], "publishedAt": "2026-10-26T00:00:00Z"}
        data = {"jobs": [item, {**item, "isListed": False}, {**item, "secondaryLocations": [], "location": "Remote United States"}]}
        result = AshbySource(company("ashby"), fetch_json=lambda _: data).fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0].posted_at)
        self.assertEqual(result[0].description, "Build APIs")

    def test_greenhouse_eu_uses_the_verified_shared_public_api(self):
        urls = []
        GreenhouseSource(replace(company("greenhouse"), api_region="eu"), fetch_json=lambda url: urls.append(url) or {"jobs": []}).fetch(QUERY)
        self.assertTrue(urls[0].startswith("https://boards-api.greenhouse.io/"))

    def test_workday_uses_actual_country_facet_and_fetches_detail(self):
        requests = []
        def fetch(url, body=None):
            requests.append((url, body))
            if body and body["limit"] == 1:
                return {"jobPostings": [], "facets": [{"facetParameter": "Location_Country", "values": [{"descriptor": "Costa Rica", "id": "country-cr"}]}]}
            if body:
                return {"jobPostings": [{"title": "Developer", "externalPath": "/job/CR/Developer_REQ1"}], "total": 1}
            return {"jobPostingInfo": {"jobReqId": "REQ1", "jobDescription": "<p>Build APIs</p>", "location": "San Jose", "country": {"descriptor": "Costa Rica"}, "startDate": "2026-10-26"}}
        result = WorkdaySource(company("workday"), fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual(len(result), 1)
        self.assertEqual(requests[1][1]["appliedFacets"], {"Location_Country": ["country-cr"]})
        self.assertEqual(result[0].description, "Build APIs")
        self.assertEqual(result[0].url, "https://example.wd1.myworkdayjobs.com/Careers/job/CR/Developer_REQ1")

    def test_workday_rejects_external_paths_and_reports_missing_descriptions(self):
        def fetch(url, body=None):
            return {"jobPostings": [{"title": "Developer", "externalPath": "https://evil.test"}], "total": 1} if body else {}
        result = WorkdaySource(company("workday"), fetch_json=fetch).fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result), 0)


class PublicSourceTests(unittest.TestCase):
    def test_robots_wildcards_and_allow_take_precedence(self):
        rules = "User-agent: *\nDisallow: /api/\nDisallow: */private/\nAllow: /api/public/\n"
        self.assertFalse(robots_allowed(rules, "https://example.com/api/jobs"))
        self.assertFalse(robots_allowed(rules, "https://example.com/cr/private/me"))
        self.assertTrue(robots_allowed(rules, "https://example.com/api/public/jobs"))
        self.assertTrue(robots_allowed(rules, "https://example.com/jobs"))

    def test_public_source_refuses_robots_blocked_requests_and_foreign_hosts(self):
        source = ProcomerSource(fetch_text=lambda _: "User-agent: *\nDisallow: /api/")
        source._start()
        with self.assertRaises(PermissionError):
            source._get("/api/candidate/jobs")
        with self.assertRaises(ValueError):
            source._request("https://evil.test/jobs")

    def test_procomer_paginates_without_storing_internal_or_personal_fields(self):
        item = {"id": 1, "name": "Developer", "external_visibility": "public", "is_published": True, "organization_name": "Acme", "provincia": "Heredia", "canton": "Belen", "description": "<p>Build APIs</p>", "requirements": "Python", "publish_date": "2026-10-26", "workplace": "on-site", "owner": {"email": "private@example.com"}, "organization": {"password": "private"}}
        def fetch(url):
            if url.endswith("robots.txt"):
                return "User-agent: *\nDisallow:\n"
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            return json.dumps({"data": {"jobs": {"data": [{**item, "id": page}], "last_page": 2}}})
        result = ProcomerSource(fetch_text=fetch).fetch(QUERY)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].company, "Acme")
        self.assertEqual(result[0].country, "CR")
        self.assertFalse(result[0].remote)
        self.assertNotIn("private", repr(result))
        self.assertIn("Python", result[0].description)

    def test_procomer_excludes_closed_and_nonpublic_jobs(self):
        items = [{"id": 1, "name": "Developer", "external_visibility": "private", "is_published": True}, {"id": 2, "name": "Developer", "external_visibility": "public", "is_published": True, "published_until": "2001-01-01"}]
        source = ProcomerSource(fetch_text=lambda url: "" if url.endswith("robots.txt") else json.dumps({"data": {"jobs": {"data": items, "last_page": 1}}}))
        self.assertEqual(len(source.fetch(QUERY)), 0)

    def test_procomer_resumes_only_after_a_successful_database_acknowledgement(self):
        from jobbot.db import Database
        from jobbot.pipeline import collect
        db = Database("sqlite:///:memory:")
        db.init_schema()
        self.addCleanup(db.close)
        pages = []
        def fetch(url):
            if url.endswith("robots.txt"):
                return ""
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            pages.append(page)
            return json.dumps({"data": {"jobs": {"data": [{"id": page, "name": "Developer", "external_visibility": "public", "is_published": True, "provincia": "Heredia", "description": "Build APIs"}], "last_page": 3}}})
        source = ProcomerSource(fetch_text=fetch)
        source.MAX_PAGES = 1
        first, = collect(db, [source], QUERY)
        self.assertEqual(first.details["next_page"], 2)
        self.assertTrue(first.details["checkpoint_safe"])
        second, = collect(db, [source], QUERY)
        self.assertEqual(pages, [1, 2])
        self.assertEqual(second.details["next_page"], 3)
        with patch("jobbot.pipeline.repo.insert_job_if_new", side_effect=RuntimeError("DB failed")), self.assertLogs("jobbot", level="ERROR"):
            failed, = collect(db, [source], QUERY)
        self.assertFalse(failed.details["checkpoint_safe"])
        collect(db, [source], QUERY)
        self.assertEqual(pages, [1, 2, 3, 3])

    def test_dynamite_uses_sitemap_and_schema_not_excluded_api(self):
        calls = []
        schema = {"@type": "JobPosting", "title": "Developer", "description": "<p>Build APIs</p>", "hiringOrganization": {"name": "Acme"}, "applicantLocationRequirements": [{"name": "Costa Rica"}], "jobLocationType": "TELECOMMUTE"}
        def fetch(url):
            calls.append(url)
            if url.endswith("robots.txt"):
                return "User-agent: *\nDisallow: /api/"
            if "/sitemap/" in url:
                return '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://dynamitejobs.com/company/acme/remote-job/backend-developer</loc></url><url><loc>https://evil.test/remote-job/backend-developer</loc></url></urlset>'
            return '<script type="application/ld+json">' + json.dumps(schema) + '</script>'
        result = DynamiteJobsSource(fetch_text=fetch).fetch(QUERY)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].remote)
        self.assertEqual(result[0].company, "Acme")
        self.assertFalse(any("/api/" in url for url in calls))

    def test_elempleo_email_imports_only_individual_costa_rica_links(self):
        message = EmailMessage()
        message["From"] = "alertas@elempleo.com"
        message.set_content("Developer\nEmpresa: Acme\nUbicación: Costa Rica\nhttps://www.elempleo.com/cr/ofertas-trabajo/developer/12345?utm_source=email")
        posting, = parse_alert(message.as_bytes()).postings
        self.assertEqual(posting.source, "elempleo_email")
        self.assertEqual(posting.url, "https://www.elempleo.com/cr/ofertas-trabajo/developer/12345")


class CatalogTests(unittest.TestCase):
    def test_known_boards_and_workday_validation(self):
        self.assertEqual(company_from_url("Acme", "https://job-boards.eu.greenhouse.io/acme").api_region, "eu")
        self.assertEqual(company_from_url("Acme", "https://jobs.lever.co/acme").platform, "lever")
        self.assertEqual(company_from_url("Acme", "https://jobs.ashbyhq.com/acme").platform, "ashby")
        self.assertIsNone(company_from_url("Acme", "https://jobs.lever.co.evil.test/acme"))
        self.assertIsNone(company_from_url("Acme", "https://example.com/careers"))

    def test_import_is_idempotent_and_never_activates_unverified_boards(self):
        with tempfile.TemporaryDirectory() as folder:
            path, pending = Path(folder) / "companies.yml", Path(folder) / "pending.yml"
            text = "| Acme | [Careers](https://jobs.lever.co/acme) |\n| Custom | [Site](https://example.com/careers) |"
            first = import_links(text, path, pending)
            second = import_links(text, path, pending)
            self.assertEqual(first["added"], 2)
            self.assertEqual(second["added"], 0)
            self.assertEqual(load_companies(path), [])
            self.assertEqual(first["pending_custom_sites"], 0)

    def test_verification_checks_response_shape_without_supabase(self):
        row = {"id": "acme", "name": "Acme", "platform": "greenhouse", "board_id": "acme", "careers_url": "https://job-boards.greenhouse.io/acme"}
        with patch("jobbot.catalog.GreenhouseSource._get", return_value={"name": "Acme"}):
            self.assertEqual(verify_board(row)["status"], "verified")
        with patch("jobbot.catalog.GreenhouseSource._get", return_value={"error": "missing"}):
            self.assertEqual(verify_board(row)["status"], "invalid_response")
