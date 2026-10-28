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
    def test_browser_reads_javascript_job_schema_through_the_same_pipeline(self):
        page = schema('https://example.com/jobs/123')
        with patch('jobbot.sources.public_adapters.render_public_page', return_value=page) as render:
            result = PublicCompanySource(company(), fetch_text=lambda _: '<main>Loading jobs</main>').fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 1)
        self.assertIn('browser_html', result.details['adapters'])
        self.assertEqual(render.call_count, 1)

    def test_empty_loading_job_cards_do_not_prove_a_verified_empty_board(self):
        result = PublicCompanySource(company(), fetch_text=lambda _: '<div class="job-card">Loading</div>').fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.details['requires_adapter_review'])

    def test_browser_does_not_turn_incomplete_js_pagination_into_success(self):
        page = schema('https://example.com/jobs/123') + '<meta name="jobbot-incomplete-pagination" content="true">'
        with patch('jobbot.sources.public_adapters.render_public_page', return_value=page):
            result = PublicCompanySource(company(), fetch_text=lambda _: '<p>Loading</p>').fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(len(result), 1)
        self.assertTrue(result.details['requires_adapter_review'])

    def test_browser_network_policy_allows_search_but_blocks_applications(self):
        from jobbot.sources.public_adapters import browser_request_allowed as allowed
        def check(url, resource='fetch', method='GET', robots=''):
            return allowed(url, resource, method, 'example.com', {'cdn.example.net'}, robots)
        self.assertTrue(check('https://example.com/api/jobs', method='POST'))
        self.assertTrue(check('https://example.com/CANDIDATEPORTAL', resource='document'))
        self.assertFalse(check('https://example.com/jobs/1/apply', method='POST'))
        self.assertFalse(check('https://example.com/candidate/profile', method='POST'))
        self.assertFalse(check('https://example.com/private/jobs', robots='User-agent: *\nDisallow: /private'))
        self.assertFalse(check('https://evil.test/jobs'))
        self.assertTrue(check('https://cdn.example.net/script.js', resource='script'))
        self.assertFalse(check('https://cdn.example.net/collect', resource='fetch', method='POST'))

    def test_resumed_browser_page_rechecks_robots_before_launching(self):
        from jobbot.sources.public_adapters import render_public_page
        source = PublicCompanySource(company())
        with patch('importlib.util.find_spec', return_value=object()), patch.object(
            source, '_request', return_value='User-agent: *\nDisallow: /jobs') as request:
            self.assertIsNone(render_public_page(source, 'https://example.com/jobs/123'))
        self.assertEqual(source._browser_issue, 'robots_blocked')
        self.assertEqual(request.call_args.args[0], 'https://example.com/robots.txt')

    def test_browser_discovers_only_known_public_job_feed_routes(self):
        from jobbot.sources.public_adapters import board_from_request
        self.assertEqual(board_from_request('https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true'), 'https://job-boards.greenhouse.io/acme')
        self.assertEqual(board_from_request('https://acme.bamboohr.com/careers/list'), 'https://acme.bamboohr.com/careers')
        self.assertIsNone(board_from_request('https://boards-api.greenhouse.io/v1/boards/acme/candidates'))
        self.assertIsNone(board_from_request('https://evil.test/v1/boards/acme/jobs'))
        self.assertIsNone(board_from_request('http://api.lever.co/v0/postings/acme'))

    def test_jibe_feed_keeps_full_description_and_multiple_countries(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/api/jobs' not in url:
                return '<script>window._jibe = {}; window.searchConfig = {};</script>'
            return json.dumps({'totalCount': 1, 'jobs': [{'data': {'slug': '12', 'title': 'Developer',
                'description': 'Build APIs', 'qualifications': 'Python', 'country_code': 'US',
                'full_location': 'United States; Escazú, Costa Rica', 'recruiterEmail': 'private@example.test'}}]})
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 1)
        self.assertIn('Python', result[0].description)
        self.assertEqual(result[0].raw['country_codes'], ['CR', 'US'])
        self.assertNotIn('private@', str(result[0]))

    def test_jibe_server_page_cap_does_not_skip_remaining_jobs(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('robots.txt'):
                return ''
            if '/api/jobs' not in url:
                return '<script>window._jibe = {}; window.searchConfig = {};</script>'
            number = int(parse_qs(urlsplit(url).query)['page'][0])
            ids = ['1', '2'] if number == 1 else ['3']
            return json.dumps({'totalCount': 3, 'jobs': [{'data': {'slug': i, 'title': 'Developer',
                'description': 'Build APIs', 'country_code': 'CR'}} for i in ids]})
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 3)
        self.assertTrue(any('page=2' in u and 'limit=2' in u for u in calls))

    def test_identifying_a_feed_does_not_prove_it_was_read_successfully(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/api/jobs' in url:
                return '<p>Service unavailable</p>'
            return '<script>window._jibe = {}; window.searchConfig = {};</script>'
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.details['requires_adapter_review'])

    def test_recruiterflow_reads_all_location_groups_and_preserves_remote(self):
        data = {'location': [['Europe', [{'job_name': 'Developer', 'job_id': 1, 'apply_link': 'acme/jobs/1', 'remote_type': 'Remote'}]],
                             ['LATAM', [{'job_name': 'Developer', 'job_id': 1, 'apply_link': 'acme/jobs/1', 'remote_type': 'Remote'}]]]}
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/jobs/1' in url:
                return '<main><h2>Responsibilities</h2>' + ('Build APIs. ' * 15) + '<h2>Requirements</h2>Python experience</main>'
            return '<script>window.jobsList = ' + json.dumps(data) + ';</script>'
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].remote)
        self.assertIn('LATAM', result[0].location)

    def test_hiringroom_reads_next_page_with_public_search_form(self):
        calls = []
        card = '<a href="/jobs/get_vacancy/1"><div class="card-vacancy"><h4 class="name__vacancy">Developer</h4><p class="card-text">Costa Rica</p></div></a>'
        def fetch(url, form=None):
            calls.append((url, form))
            if url.endswith('robots.txt'):
                return ''
            if '/get_vacancy/' in url:
                return '<div class="job-description">Build APIs</div>'
            if '/getVacanciesForPortal/' in url:
                self.assertEqual(form, {'selectedPage': 2, 'typePortal': 'external'})
                return json.dumps({'result': 'success', 'data': {'htmlContent': card.replace('/1', '/2'), 'pagination': ''}})
            return '<script>let typePortal="external";</script><div class="vacancyDataContainer">' + card + '</div><div class="paginationLinks"><li class="actual">1</li><a class="dd_siguiente">Next</a></div>'
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 2)
        self.assertTrue(any(form for _, form in calls))

    def test_job_search_post_respects_robots(self):
        calls = []
        def fetch(url, form=None):
            calls.append(url)
            return 'User-agent: *\nDisallow: /jobs/getVacanciesForPortal'
        source = PublicCompanySource(company(), fetch_text=fetch)
        with self.assertRaises(PermissionError):
            source._get('/jobs/getVacanciesForPortal/2', {'selectedPage': 2})
        self.assertEqual(len(calls), 1)

    def test_hydrated_job_lists_ignore_non_job_queries(self):
        value = {'props': {'pageProps': {'dehydratedState': {'queries': [
            {'queryKey': ['board', 'locations'], 'state': {'data': {'items': [{'name': 'Developer'}], 'totalItems': 1}}},
            {'queryKey': ['board', 'job-posts'], 'state': {'data': {'items': [
                {'id': '1', 'name': 'Developer', 'url': 'https://example.com/jobs/1', 'locations': [{'countryCode': 'CR'}]}], 'totalItems': 1}}}]}}}}
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/jobs/1' in url:
                return '<div class="job_description">Build APIs</div>'
            return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(value) + '</script>'
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 1)

    def test_unmapped_hydration_pagination_stays_under_review(self):
        value = {'props': {'pageProps': {'dehydratedState': {'queries': [{'queryKey': ['job-posts'],
            'state': {'data': {'items': [], 'totalItems': 10}}}]}}}}
        page = '<script id="__NEXT_DATA__">' + json.dumps(value) + '</script>'
        result = PublicCompanySource(company(), fetch_text=lambda _: page).fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.details['requires_adapter_review'])
        self.assertEqual(result.details['review_category'], 'pagination_contract')

    def test_escaped_greenhouse_configuration_discovers_one_shared_board(self):
        page = '<script>self.data = "{\\"_type\\":\\"greenhouseJobBoard\\",\\"boardToken\\":\\"acme\\"}";</script>'
        with patch.object(PublicCompanySource, '_delegate', return_value=FetchResult()) as delegate:
            result = PublicCompanySource(company(), fetch_text=lambda _: page).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(delegate.call_args.args[0].board_id, 'acme')

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

    def test_dynamic_successfactors_starts_from_filtered_public_page(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if '/job/' in url:
                return '<div class="jobdescription">Build APIs</div>'
            if 'locationsearch=' in url:
                return '<div id="searchresults"><tr><td><a class="jobTitle-link" href="/job/role/123">Developer</a></td><td class="jobLocation">Costa Rica</td></tr></div>'
            return '<div id="job-table"></div><script src="/successfactors.js"></script>'
        result = PublicCompanySource(company(), fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 1)
        self.assertIn('successfactors_js', result.details['adapters'])

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

    def test_oracle_repeated_page_cannot_produce_false_success(self):
        c = replace(company("acme.fa.oraclecloud.com"), careers_url="https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs")
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            return json.dumps({"items": [{"TotalJobsCount": 26, "requisitionList": [
                {"Id": str(i), "Title": "Accountant", "PrimaryLocationCountry": "US"} for i in range(25)]}]})
        result = PublicCompanySource(c, fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertIn('pagination_not_advancing', result.details['failures'])
        self.assertEqual(result.details['pending_offset'], 25)

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
