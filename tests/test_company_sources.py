import json
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from jobbot.models import SearchQuery
from jobbot.sources import build_sources
from jobbot.sources.companies import Company, load_companies
from jobbot.sources.greenhouse import GreenhouseSource
from jobbot.sources.smartrecruiters import SmartRecruitersSource

QUERY = SearchQuery(keywords=("developer", "engineer"), countries=("CR",), residence_country="CR")
GH = Company("test", "Test Co", "greenhouse", "testboard", "https://example.com")
SR = Company("test", "Test Co", "smartrecruiters", "testboard", "https://example.com")


def gh_job(identifier=1, location="San José, Costa Rica", title="Backend Developer"):
    return {"id": identifier, "title": title, "absolute_url": f"https://example.com/jobs/{identifier}",
            "location": {"name": location}, "content": "<p>Python &amp; APIs</p>", "updated_at": "2026-01-01T00:00:00Z"}


def sr_job(identifier=1, country="cr", title="Software Engineer", remote=False):
    return {"id": identifier, "name": title, "location": {"city": "San José", "country": country, "remote": remote},
            "releasedDate": "2026-10-20T00:00:00Z"}


DETAIL = {"jobAd": {"sections": {"jobDescription": {"text": "<p>Build APIs</p>"}, "qualifications": {"text": "Python"}}}}


class GreenhouseTests(unittest.TestCase):
    def test_full_text_location_and_no_fake_publication_date(self):
        result = GreenhouseSource(GH, fetch_json=lambda url: {"jobs": [gh_job()]}).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, "Python & APIs")
        self.assertEqual(result[0].country, "CR")
        self.assertEqual(result[0].source, "greenhouse:test")
        self.assertIsNone(result[0].posted_at)

    def test_encoded_html_is_returned_as_plain_text(self):
        item = gh_job()
        item["content"] = "&lt;p&gt;Build &amp;amp; test APIs&lt;/p&gt;"
        result = GreenhouseSource(GH, fetch_json=lambda url: {"jobs": [item]}).fetch(QUERY)
        self.assertEqual(result[0].description, "Build & test APIs")

    def test_remote_does_not_mean_worldwide(self):
        data = [gh_job(1, "Remote, United States"), gh_job(2, "Remote, LATAM"),
                gh_job(3, "Remote, Worldwide"), gh_job(4, "Remote"), gh_job(5, "Mexico City, Mexico"),
                gh_job(6, title="Sales Manager")]
        result = GreenhouseSource(GH, fetch_json=lambda url: {"jobs": data}).fetch(QUERY)
        self.assertEqual([p.external_id for p in result], ["2", "3"])

    def test_remote_option_can_be_disabled(self):
        query = SearchQuery(keywords=(), countries=("CR",), residence_country="CR", include_remote_latam=False)
        result = GreenhouseSource(GH, fetch_json=lambda url: {"jobs": [gh_job(1, "Remote, LATAM"), gh_job(2)]}).fetch(query)
        self.assertEqual([p.external_id for p in result], ["2"])

    def test_invalid_response_is_not_a_successful_empty_board(self):
        for payload in ({"error": "Not Found"}, {"jobs": None}, []):
            with self.assertRaises(ValueError):
                GreenhouseSource(GH, fetch_json=lambda url: payload).fetch(QUERY)

    def test_invalid_records_are_reported_as_partial(self):
        result = GreenhouseSource(GH, fetch_json=lambda url: {"jobs": [gh_job(), {"title": "Broken"}]}).fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.details["invalid_records"], 1)
        self.assertEqual(len(result), 1)


class SmartRecruitersTests(unittest.TestCase):
    def test_fetches_description_and_follows_pagination(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if "?" not in url:
                return DETAIL
            offset = int(parse_qs(urlsplit(url).query)["offset"][0])
            return {"content": [sr_job(offset + 1)], "totalFound": 2}
        result = SmartRecruitersSource(SR, fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual(len(result), 2)
        self.assertEqual(result.status, "success")
        self.assertIn("Build APIs", result[0].description)
        self.assertIn("Python", result[0].description)
        self.assertIsNotNone(result[0].posted_at)
        self.assertEqual(len(calls), 4)

    def test_skips_foreign_remote_and_unrelated_titles_before_fetching_detail(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if "?" not in url:
                return DETAIL
            return {"content": [sr_job(1, country="us", remote=True), sr_job(2, title="Sales Manager"), sr_job(3)], "totalFound": 3}
        result = SmartRecruitersSource(SR, fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual([p.external_id for p in result], ["3"])
        self.assertEqual(len(calls), 2)

    def test_detail_failure_preserves_other_jobs_and_reports_partial(self):
        def fetch(url):
            if "?" in url:
                return {"content": [sr_job(1), sr_job(2)], "totalFound": 2}
            if url.endswith("/2"):
                raise HTTPError(url, 404, "Not Found", {}, None)
            return DETAIL
        result = SmartRecruitersSource(SR, fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual([p.external_id for p in result], ["1"])
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.details["failures"])

    def test_page_failure_preserves_previous_page(self):
        def fetch(url):
            if "?" not in url:
                return DETAIL
            if "offset=0" not in url:
                raise TimeoutError("timeout")
            return {"content": [sr_job(1)], "totalFound": 2}
        result = SmartRecruitersSource(SR, fetch_json=fetch, sleep=lambda n: None).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual(len(result), 1)
        self.assertEqual(result.status, "partial")

    def test_page_cap_is_partial(self):
        class Limited(SmartRecruitersSource):
            MAX_PAGES = 1
        def fetch(url):
            return {"content": [sr_job(1)], "totalFound": 999} if "?" in url else DETAIL
        result = Limited(SR, fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("page_limit_reached" in item for item in result.details["failures"]))

    def test_residence_country_is_queried_before_the_global_pass(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if "?" not in url:
                return DETAIL
            params = parse_qs(urlsplit(url).query)
            return {"content": [sr_job(1)] if params.get("country") == ["cr"] else [], "totalFound": 1 if "country" in params else 0}
        result = SmartRecruitersSource(SR, fetch_json=fetch).fetch(QUERY)
        self.assertIn("country=cr", calls[0])
        self.assertEqual(result.details["scopes"], ["cr", "global"])
        self.assertEqual(len(result), 1)

    def test_company_can_limit_search_to_residence_country(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return {"content": [], "totalFound": 0}
        result = SmartRecruitersSource(replace(SR, include_global_remote=False), fetch_json=fetch).fetch(QUERY)
        self.assertEqual(result.details["scopes"], ["cr"])
        self.assertEqual(len(calls), 1)

    def test_first_page_failure_is_not_empty_success(self):
        def fetch(url):
            raise HTTPError(url, 404, "Not Found", {}, None)
        with self.assertRaises(HTTPError):
            SmartRecruitersSource(SR, fetch_json=fetch).fetch(replace(QUERY, include_remote_latam=False))

    def test_retries_transient_http_error(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 503, "Busy", {}, None)
            return {"content": [], "totalFound": 0}
        result = SmartRecruitersSource(SR, fetch_json=fetch, sleep=lambda n: None).fetch(replace(QUERY, include_remote_latam=False))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(calls), 2)


class CompaniesConfigTests(unittest.TestCase):
    def load(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "companies.yml"
            file.write_text(json.dumps({"companies": rows}), encoding="utf-8")
            return load_companies(file)

    def test_disabled_entries_are_not_requested(self):
        self.assertEqual(self.load([{"enabled": False}]), [])

    def test_invalid_board_and_duplicates_are_rejected(self):
        row = {"id": "test", "name": "Test", "platform": "greenhouse", "board_id": "../evil", "careers_url": "https://example.com", "enabled": True}
        with self.assertRaises(ValueError):
            self.load([row])
        row["board_id"] = "test"
        with self.assertRaises(ValueError):
            self.load([row, row])

    def test_config_expands_to_separate_company_sources(self):
        sources = build_sources(["greenhouse", "smartrecruiters"])
        self.assertGreater(len(sources), 2)
        self.assertEqual(len({source.name for source in sources}), len(sources))
        self.assertTrue(all(":" in source.name for source in sources))


if __name__ == "__main__":
    unittest.main()
