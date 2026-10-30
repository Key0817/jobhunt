import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

from jobbot.db import Database
from jobbot.models import SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.companies import Company
from jobbot.sources.lever import LeverSource

QUERY = SearchQuery(keywords=('developer',), countries=('CR',), residence_country='CR')
COMPANY = Company('test', 'Test', 'lever', 'test', 'https://jobs.lever.co/test')


def job(n):
    return {'id': str(n), 'text': 'Developer', 'hostedUrl': f'https://jobs.lever.co/test/{n}',
            'categories': {'location': 'Costa Rica'}, 'descriptionPlain': 'Build applications'}


class NetworkRecoveryTests(unittest.TestCase):
    def test_two_timeouts_then_success(self):
        calls, waits = [], []
        def fetch(url):
            calls.append(url)
            if len(calls) < 3:
                raise TimeoutError('read timeout')
            return []
        source = LeverSource(COMPANY, fetch_json=fetch, sleep=waits.append)
        self.assertEqual(source.fetch(QUERY).status, 'success')
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(waits), 2)
        self.assertGreater(waits[1], waits[0])

    def test_permanent_404_is_not_retried(self):
        calls = []
        def fetch(url):
            calls.append(url)
            raise HTTPError(url, 404, 'missing board', {}, None)
        with self.assertRaises(HTTPError):
            LeverSource(COMPANY, fetch_json=fetch).fetch(QUERY)
        self.assertEqual(len(calls), 1)

    def test_retry_after_is_respected(self):
        calls, waits = [], []
        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 429, 'rate limit', {'Retry-After': '4'}, None)
            return []
        LeverSource(COMPANY, fetch_json=fetch, sleep=waits.append).fetch(QUERY)
        self.assertEqual(waits, [4])

    def test_retry_cannot_exceed_company_budget(self):
        def fetch(url):
            raise TimeoutError('read timeout')
        waits = []
        with self.assertRaises(TimeoutError):
            LeverSource(COMPANY, fetch_json=fetch, sleep=waits.append, max_runtime=0.5).fetch(QUERY)
        self.assertEqual(waits, [])

    def test_checkpoint_resumes_failed_page_after_store(self):
        db = Database('sqlite:///:memory:')
        db.init_schema()
        self.addCleanup(db.close)
        calls, recover = [], [False]
        def fetch(url):
            offset = int(parse_qs(urlsplit(url).query)['skip'][0])
            calls.append(offset)
            if offset == 1 and not recover[0]:
                raise TimeoutError('read timeout')
            return [job(offset)] if offset < 2 else []
        source = LeverSource(COMPANY, fetch_json=fetch, sleep=lambda _: None)
        source.PAGE_SIZE = 1
        first = collect(db, [source], QUERY)[0]
        self.assertEqual(first.status, 'partial')
        self.assertEqual(first.inserted, 1)
        self.assertEqual(first.details['pending_offset'], 1)
        recover[0] = True
        calls.clear()
        second = collect(db, [source], QUERY)[0]
        self.assertEqual(second.status, 'success')
        self.assertTrue(second.details['resumed'])
        self.assertEqual(calls, [1, 2])
        self.assertEqual(db.query('select count(*) as n from jobs')[0]['n'], 2)

    def test_ignored_pagination_does_not_report_success(self):
        source = LeverSource(COMPANY, fetch_json=lambda _: [job(1)])
        source.PAGE_SIZE = 1
        result = source.fetch(QUERY)
        self.assertEqual(result.status, 'partial')
        self.assertIn('pagination_not_advancing', result.details['failures'])

    def test_failed_storage_does_not_acknowledge_cursor(self):
        from unittest.mock import patch
        db = Database('sqlite:///:memory:')
        db.init_schema()
        self.addCleanup(db.close)
        source = LeverSource(COMPANY, fetch_json=lambda _: [job(1)])
        with patch('jobbot.pipeline._store_with_retry', side_effect=RuntimeError('storage failed')):
            result = collect(db, [source], QUERY)[0]
        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.details.get('checkpoint_safe', False))
