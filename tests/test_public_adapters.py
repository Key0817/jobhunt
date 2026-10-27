import json
import unittest
from dataclasses import replace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from jobbot.db import Database
from jobbot.models import SearchQuery, JobPosting
from jobbot.pipeline import collect
from jobbot.sources.base import FetchResult
from jobbot.sources.companies import Company
from jobbot.sources.public_company import PublicCompanySource


QUERY = SearchQuery(keywords=("developer",), countries=("CR",), residence_country="CR")


def company(host="example.com"):
    return Company("acme", "Acme", "public_web", "acme", f"https://{host}/careers")


def schema(url):
    return '<script type="application/ld+json">' + json.dumps({
        "@type": "JobPosting", "title": "Developer", "description": "Build APIs",
        "url": url, "jobLocation": {"address": {"addressCountry": "CR"}}}) + '</script>'


class PublicAdapterTests(unittest.TestCase):
    def test_successfactors_searches_country_and_reads_html_details(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith("robots.txt"):
                return ""
            if "/job/" in url:
                return '<div class="jobdescription">Build APIs with Python</div>'
            return '<div id="searchresults"><table><tr><td><a class="jobTitle-link" href="/job/dev/123/">Developer</a></td><td class="jobLocation">Heredia, CR</td></tr></table></div>'

        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].country, "CR")
        self.assertIn("Python", result[0].description)
        self.assertTrue(any("locationsearch=Costa+Rica" in url for url in calls))

    def test_verified_empty_list_is_success_but_ordinary_page_is_not(self):
        empty = '<div id="noresults">No open positions</div>'
        result = PublicCompanySource(company(), fetch_text=lambda _: empty).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 0)
        unknown = PublicCompanySource(company(), fetch_text=lambda _: '<p>Welcome</p>').fetch(QUERY)
        self.assertEqual(unknown.status, "partial")

    def test_cursor_resumes_only_after_store_and_finishes_the_cycle(self):
        db = Database("sqlite:///:memory:")
        self.addCleanup(db.close)
        db.init_schema()
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith("robots.txt"):
                return ""
            if url.endswith("careers"):
                return '<a href="/jobs/1">Developer</a><a href="/jobs/2">Developer</a>'
            return schema(url)

        first = PublicCompanySource(company(), fetch_text=fetch)
        first.MAX_PAGES = 2
        a = collect(db, [first], QUERY)[0]
        self.assertEqual(a.status, "partial")
        self.assertEqual(a.inserted, 1)
        self.assertTrue(a.details["checkpoint_safe"])
        calls.clear()
        b = collect(db, [PublicCompanySource(company(), fetch_text=fetch)], QUERY)[0]
        self.assertEqual(b.status, "success")
        self.assertTrue(b.details["resumed"])
        self.assertTrue(b.details["cycle_complete"])
        self.assertFalse(any(url.endswith("careers") for url in calls))
        self.assertEqual(len(db.query("select * from jobs")), 2)

    def test_unknown_pages_do_not_become_success_just_because_the_cursor_finished(self):
        def fetch(url):
            return '' if url.endswith('robots.txt') else '<a href="/careers/more">Careers</a>'
        first = PublicCompanySource(company(), fetch_text=fetch)
        first.MAX_PAGES = 1
        result = first.fetch(QUERY)
        first.after_store(None)
        second = PublicCompanySource(company(), fetch_text=fetch)
        second._previous = result.details
        result = second.fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.details["requires_adapter_review"])

    def test_script_embed_uses_complete_board_without_crawling_corporate_links(self):
        def fetch(url):
            return '' if url.endswith('robots.txt') else '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script><a href="/careers/blog">Careers blog</a>'
        with patch.object(PublicCompanySource, "_delegate", return_value=FetchResult()) as delegate:
            result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.details["pages_fetched"], 1)
        self.assertEqual(delegate.call_count, 1)

    def test_aggregator_link_to_one_employer_does_not_prove_full_coverage(self):
        page = '<a href="https://jobs.lever.co/other/123">Developer</a>'
        with patch.object(PublicCompanySource, "_delegate") as delegate:
            result = PublicCompanySource(company(), fetch_text=lambda _: page).fetch(QUERY)
        delegate.assert_not_called()
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.details["requires_adapter_review"])

    def test_bad_link_does_not_discard_other_links_and_spaces_are_encoded(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('robots.txt'):
                return ''
            if url.endswith('careers'):
                return '<a href="https://[broken">Bad</a><a href="/jobs/developer role">Developer</a>'
            return schema(url)
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 1)
        self.assertTrue(any("developer%20role" in url for url in calls))

    def test_declared_sitemap_discovers_job_details(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return 'User-agent: *\nSitemap: https://example.com/jobs-sitemap.xml'
            if url.endswith('careers'):
                return '<p>Careers</p>'
            if url.endswith('.xml'):
                return '<?xml version="1.0"?><urlset><url><loc>https://example.com/jobs/1</loc></url></urlset>'
            return schema(url)
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 1)

    def test_http_block_retains_diagnostic_status(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            raise HTTPError(url, 403, 'Forbidden', {}, None)
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "partial")
        self.assertIn("http_403", result.details["failures"])

    def test_amazon_uses_published_country_and_complete_description(self):
        data = {"error": None, "hits": 1, "jobs": [{"id_icims": "123", "title": "Developer", "country_code": "CRI",
            "job_path": "/en/jobs/123/developer", "location": "CR, H, Heredia", "description": "Build APIs", "basic_qualifications": "Python"}]}
        def fetch(url):
            return '' if url.endswith('robots.txt') else json.dumps(data)
        result = PublicCompanySource(company("amazon.jobs"), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(result[0].country, "CR")
        self.assertIn("Python", result[0].description)

    def test_oracle_retains_only_public_vacancy_fields(self):
        c = replace(company("acme.fa.oraclecloud.com"), careers_url="https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs")
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if 'recruitingCEJobRequisitionDetails?' in url:
                return json.dumps({"items": [{"ExternalDescriptionStr": "Build APIs", "ExternalContactEmail": "private@example.test"}]})
            return json.dumps({"items": [{"TotalJobsCount": 1, "requisitionList": [{"Id": "123", "Title": "Developer", "PrimaryLocationCountry": "CR"}]}]})
        result = PublicCompanySource(c, fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(result[0].country, "CR")
        self.assertNotIn('private@', str(result[0]))
        self.assertEqual(result[0].url, "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123")

    def test_phenom_json_discovers_real_ats_without_executing_script(self):
        ddo = {"eagerLoadRefineSearch": {"status": 200, "totalHits": 50,
            "data": {"jobs": [{"applyUrl": "https://acme.wd1.myworkdayjobs.com/Jobs/job/1/apply"}]}}}
        page = '<script>phApp.ddo = ' + json.dumps(ddo) + '; throw Error("do not execute");</script>'
        with patch.object(PublicCompanySource, "_delegate", return_value=FetchResult()) as delegate:
            result = PublicCompanySource(company(), fetch_text=lambda _: page).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(delegate.call_args.args[0].board_id, "Jobs")

    def test_careers_page_html_description_has_explicit_country(self):
        c = replace(company('careers-page.com'), careers_url='https://careers-page.com/acme')
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/job/' not in url:
                return '<input id="id_position_name__icontains"><a href="/acme/job/1">Developer</a>'
            return '<span><span class="fa fa-map-marker"></span>San José, Costa Rica</span><div class="job-details-body">Build APIs</div>'
        result = PublicCompanySource(c, fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].country, 'CR')
