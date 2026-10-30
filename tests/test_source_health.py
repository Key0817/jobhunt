import json
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jobbot import repo
from jobbot.db import Database
from jobbot.models import JobPosting, SearchQuery
from jobbot.pipeline import collect
from jobbot.source_health import source_health, github_summary
from jobbot.sources.base import Source, FetchResult
from jobbot.sources.companies import Company
from jobbot.sources.public_company import PublicCompanySource


QUERY = SearchQuery(keywords=('developer',), countries=('CR',), residence_country='CR')
COMPANY = Company('test', 'Test', 'public_web', 'test', 'https://example.com/careers')


def schema(url, title='Developer', country='CR', date=None):
    record = {'@type': 'JobPosting', 'title': title, 'description': 'Develop applications',
              'url': url, 'jobLocation': {'address': {'addressCountry': country}}}
    if date:
        record['datePosted'] = date.isoformat()
    return record


def markup(records):
    return '<script type="application/ld+json">' + json.dumps(records) + '</script>'


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.db = Database('sqlite:///:memory:')
        self.db.init_schema()
        self.addCleanup(self.db.close)

    def test_more_than_forty_links_are_drained_across_safe_checkpoints(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            if url.endswith('careers'):
                return ''.join(f'<a href="/jobs/{n}">Developer</a>' for n in range(55))
            return markup(schema(url))
        source = PublicCompanySource(COMPANY, fetch_text=fetch)
        source.MAX_PAGES = 20
        result = collect(self.db, [source], QUERY, resume_passes=4)[0]
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.inserted, 55)
        self.assertEqual(self.db.query('select count(*) as n from jobs')[0]['n'], 55)
        health = source_health(self.db)[0]
        self.assertTrue(health['health']['cycle_complete'])
        self.assertIsNotNone(health['last_complete_cycle'])

    def test_itemlist_records_and_next_page_are_read(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            record = schema(url + '/job')
            text = markup({'@type': 'ItemList', 'itemListElement': [{'@type': 'ListItem', 'item': record}]})
            return text + ('<a rel="next" href="/page2">Next</a>' if url.endswith('careers') else '')
        result = PublicCompanySource(COMPANY, fetch_text=fetch).fetch(QUERY)
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result), 2)

    def test_metrics_distinguish_filters_age_and_database_duplicates(self):
        records = [schema('https://example.com/job/1'), schema('https://example.com/job/2', country='US'),
                   schema('https://example.com/job/3', title='Accountant'),
                   schema('https://example.com/job/4', date=repo.now() - timedelta(days=100))]
        def fetch(url):
            return '' if url.endswith('robots.txt') else markup(records)
        first = collect(self.db, [PublicCompanySource(COMPANY, fetch_text=fetch)], QUERY)[0]
        self.assertEqual(first.details['metrics']['records_seen'], 4)
        self.assertEqual(first.details['metrics']['discarded_location'], 1)
        self.assertEqual(first.details['metrics']['discarded_keywords'], 1)
        self.assertEqual(first.details['metrics']['discarded_date'], 1)
        second = collect(self.db, [PublicCompanySource(COMPANY, fetch_text=fetch)], QUERY)[0]
        self.assertEqual(second.details['metrics']['duplicates'], 1)
        self.assertEqual(second.inserted, 0)

    def test_loading_container_does_not_prove_an_empty_list(self):
        source = PublicCompanySource(COMPANY, fetch_text=lambda _: '<div id="searchresults">Loading…</div>')
        result = source.fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertFalse(result.details['cycle_complete'])

    def test_invalid_schema_is_retried_instead_of_disappearing_from_coverage(self):
        def fetch(url):
            if url.endswith('robots.txt'):
                return ''
            return markup({'@type': 'JobPosting', 'title': 'Developer', 'url': url})
        result = collect(self.db, [PublicCompanySource(COMPANY, fetch_text=fetch)], QUERY, resume_passes=2)[0]
        self.assertEqual(result.status, 'partial')
        self.assertFalse(result.details['health']['cycle_complete'])
        self.assertGreater(result.details['pending_pages'], 0)

    def test_historical_success_does_not_become_proof_and_complete_cycle_survives_a_failure(self):
        repo.record_run(self.db, 'old', repo.now(), repo.now(), 0, 0, None, status='success')
        self.assertIsNone(source_health(self.db, 'old')[0]['last_complete_cycle'])
        repo.record_run(self.db, 'test', repo.now(), repo.now(), 0, 0, None, status='success',
                        details={'health': {'cycle_complete': True}})
        repo.record_run(self.db, 'test', repo.now(), repo.now(), 0, 0, 'failed', status='failed',
                        details={'health': {'cycle_complete': False, 'outcome': 'failed'}, 'pending_items': ['private queue']})
        report = source_health(self.db, 'test')[0]
        self.assertEqual(report['status'], 'failed')
        self.assertIsNotNone(report['last_complete_cycle'])
        self.assertNotIn('private queue', json.dumps(report, default=str))

    def test_legacy_sources_keep_unknown_counts_and_actions_summary_is_bounded(self):
        class Legacy(Source):
            name = 'legacy'
            def fetch(self, query):
                return [JobPosting(source=self.name, title='Developer', url='https://example.com/1')]
        result = collect(self.db, [Legacy()], QUERY)[0]
        self.assertIsNone(result.details['metrics']['discarded_location'])
        self.assertEqual(result.details['health']['outcome'], 'unverified')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'summary.md'
            github_summary([result], path)
            self.assertIn('legacy', path.read_text(encoding='utf-8'))
            self.assertNotIn('pending_items', path.read_text(encoding='utf-8'))

    def test_partial_delegated_board_remains_pending(self):
        page = '<iframe src="https://job-boards.greenhouse.io/test"></iframe>'
        source = PublicCompanySource(COMPANY, fetch_text=lambda url: '' if url.endswith('robots.txt') else page)
        with patch.object(source, '_delegate', return_value=FetchResult(status='partial')):
            result = collect(self.db, [source], QUERY, resume_passes=2)[0]
        self.assertEqual(result.status, 'partial')
        self.assertFalse(result.details['health']['cycle_complete'])
        self.assertGreater(result.details['pending_pages'], 0)

    def test_cli_health_reads_the_report(self):
        import contextlib
        import io
        from jobbot.cli import main
        output = io.StringIO()
        with patch('jobbot.cli.Database', return_value=self.db), patch.object(self.db, 'close'), contextlib.redirect_stdout(output):
            self.assertEqual(main(['sources-health', '--source', 'public_web', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue()), [])
