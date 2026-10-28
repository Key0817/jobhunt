import asyncio
import json
import unittest
from datetime import timedelta

from jobbot import repo
from jobbot.api import app, get_db
from jobbot.db import Database
from jobbot.models import JobPosting


class JobPaginationTests(unittest.TestCase):
    def setUp(self):
        self.db = Database('sqlite:///:memory:')
        self.db.init_schema()
        self.addCleanup(self.db.close)

    def add(self, n, **kwargs):
        posting = JobPosting(source='test', external_id=str(n), title=f'Job {n}',
                             url=f'https://example.com/{n}', **kwargs)
        repo.insert_job_if_new(self.db, posting)
        return self.db.query('select id from jobs where external_id = ?', (str(n),))[0]['id']

    def test_all_records_beyond_old_limit_are_reachable_without_overlap(self):
        for n in range(1003):
            self.add(n)
        # Exercise tie-breaking: timestamps and scores can be identical across a batch.
        self.db.execute('update jobs set detected_at = ?', (repo.now(),))
        seen = []
        for page in range(1, 12):
            result = repo.paginated_jobs(self.db, page=page, page_size=100)
            self.assertEqual(result['total'], 1003)
            self.assertEqual(result['total_pages'], 11)
            seen.extend(job['id'] for job in result['items'])
        self.assertEqual(len(seen), 1003)
        self.assertEqual(len(set(seen)), 1003)

    def test_filters_are_applied_before_counting_and_paging(self):
        for n in range(10):
            self.add(n, remote=False)
        wanted = self.add(10, remote=True)
        result = repo.paginated_jobs(self.db, page_size=1, modality='remote')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], wanted)
        self.db.execute('update jobs set detected_at = ? where id = ?', (repo.now() - timedelta(days=8), wanted))
        self.assertEqual(repo.paginated_jobs(self.db, modality='remote', days=7)['total'], 0)

    def test_unknown_location_matches_existing_ui_semantics(self):
        self.add(1, location=None)
        self.add(2, location='San Jose', raw={'location_needs_review': True})
        self.add(3, location='San Jose')
        self.assertEqual(repo.paginated_jobs(self.db, modality='unknown')['total'], 2)

    def test_empty_and_removed_last_page_are_clamped(self):
        result = repo.paginated_jobs(self.db, page=99)
        self.assertEqual((result['page'], result['total_pages'], result['items']), (1, 1, []))
        for n in range(3):
            self.add(n)
        last = repo.paginated_jobs(self.db, page=2, page_size=2)['items'][0]
        repo.change_status(self.db, last['id'], 'descartada')
        result = repo.paginated_jobs(self.db, page=2, page_size=2, status='encontrada')
        self.assertEqual((result['page'], result['total']), (1, 2))

    def test_email_alias_filter_and_score_sort(self):
        first = self.add(1)
        self.add(2)
        repo.insert_job_if_new(self.db, JobPosting(source='linkedin_email', external_id='alias',
                               title='Alias', url='https://example.com/1'))
        self.db.execute('update jobs set score = 90 where id = ?', (first,))
        result = repo.paginated_jobs(self.db, source='email', min_score=80)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], first)

    def test_api_pagination_route_and_validation(self):
        self.add(1)
        app.dependency_overrides[get_db] = lambda: self.db
        self.addCleanup(app.dependency_overrides.clear)

        async def request(query):
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            async def send(message):
                messages.append(message)
            await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                       'method': 'GET', 'scheme': 'http', 'path': '/api/jobs/page',
                       'raw_path': b'/api/jobs/page', 'query_string': query.encode(),
                       'root_path': '', 'headers': [], 'server': ('test', 80), 'client': ('test', 123)}, receive, send)
            status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
            body = json.loads(b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'))
            return status, body

        status, body = asyncio.run(request('page=1&page_size=1'))
        self.assertEqual(status, 200)
        self.assertEqual((body['total'], len(body['items'])), (1, 1))
        self.assertEqual(asyncio.run(request('page=0'))[0], 422)
        self.assertEqual(asyncio.run(request('page_size=101'))[0], 422)
        self.assertEqual(asyncio.run(request('modality=invalid'))[0], 422)
