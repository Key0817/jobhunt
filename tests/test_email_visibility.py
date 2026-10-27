import json
import unittest
from dataclasses import replace
from jobbot import repo
from jobbot.db import Database
from jobbot.models import JobPosting
from jobbot.mail_imports import collection_summary


class EmailVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.db = Database('sqlite:///:memory:')
        self.db.init_schema()
        self.addCleanup(self.db.close)
        self.posting = JobPosting(source='linkedin_email', external_id='1', title='Developer', company='Acme', country='CR', url='https://www.linkedin.com/jobs/view/1', description='Resumen de la alerta', raw={'description_incomplete': True, 'location_needs_review': True})

    def test_email_filter_applies_before_limit_and_preserves_detail_links(self):
        repo.insert_job_if_new(self.db, self.posting)
        for n in range(5):
            repo.insert_job_if_new(self.db, replace(self.posting, source='greenhouse:acme', external_id=str(n), url=f'https://example.com/{n}'))
        self.db.execute("update jobs set score = 99 where source like 'greenhouse:%'")
        self.db.conn.commit()
        jobs = repo.list_jobs(self.db, source='email', limit=1)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job['source'], 'linkedin_email')
        self.assertTrue(job['description_incomplete'])
        self.assertTrue(job['location_needs_review'])
        self.assertNotIn('raw', job)
        detail = repo.get_job_detail(self.db, job['id'])
        self.assertEqual(detail['description'], self.posting.description)
        self.assertEqual(detail['url'], self.posting.url)
        self.assertEqual(detail['origins'][0]['label'], 'LinkedIn · correo')

    def test_email_alias_finds_existing_job_without_duplicate(self):
        repo.insert_job_if_new(self.db, replace(self.posting, source='greenhouse:acme'))
        self.assertFalse(repo.insert_job_if_new(self.db, self.posting))
        self.assertEqual(len(repo.list_jobs(self.db, source='email')), 1)
        self.assertEqual(len(repo.list_jobs(self.db, source='linkedin_email')), 1)
        self.assertEqual(len(repo.list_jobs(self.db, source='indeed_email')), 0)
        self.assertEqual(collection_summary(self.db)['total_offers'], 1)

    def test_trabajos_diarios_is_in_email_filters_and_summary(self):
        repo.insert_job_if_new(self.db, replace(self.posting, source='trabajosdiarios_email'))
        job, = repo.list_jobs(self.db, source='email')
        self.assertEqual(job['source_display'], 'Trabajos Diarios · correo')
        self.assertEqual(len(repo.list_jobs(self.db, source='trabajosdiarios_email')), 1)
        self.assertEqual(collection_summary(self.db)['total_offers'], 1)

    def test_summary_explains_partial_run_without_message_identifiers(self):
        repo.insert_job_if_new(self.db, self.posting)
        details = json.dumps({'fetched_messages': 5, 'more_messages_pending': True, 'message_statuses': {'needs_review': 3}, 'retry_uids': ['private-message-id']})
        self.db.execute('insert into runs (id,source,started_at,finished_at,status,found,inserted,details) values (?,?,?,?,?,?,?,?)', ('run','email_alerts',repo.now(),repo.now(),'partial',2,2,details))
        self.db.conn.commit()
        summary = collection_summary(self.db)
        self.assertEqual(summary['total_offers'], 1)
        self.assertEqual(summary['last_run']['inserted'], 2)
        self.assertTrue(summary['last_run']['more_pending'])
        self.assertEqual(summary['last_run']['read_errors'], 1)
        self.assertEqual(summary['last_run']['review_messages'], 3)
        self.assertNotIn('private-message-id', json.dumps(summary, default=str))
