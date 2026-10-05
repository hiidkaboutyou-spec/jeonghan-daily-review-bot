import unittest
from datetime import timezone

from app.main import parse_date_query


class DateParsingRegression(unittest.TestCase):
    def test_compact_and_persian_dates(self):
        expected = parse_date_query('2026-10-04', timezone.utc)
        for text in ('20261004', '۲۶۱۰۰۴', '۲۰۲۶۱۰۰۴', 'لایو 261004 جونگهان'):
            with self.subTest(text=text):
                self.assertEqual(parse_date_query(text, timezone.utc), expected)

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from app.date_requests import enqueue, process_date_requests, route_date_request, select_updates, sync_repository_requests
from app.archive_store import ArchiveStore
from app.state import StateStore
from app.models import Update
from app.personal_assistant import PersonalAssistantReviewApplication
from app.x_client import XCollector
from app.ai import GroupCopy


class DateBundleRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = PersonalAssistantReviewApplication.__new__(PersonalAssistantReviewApplication)
        self.app.settings = SimpleNamespace(
            sources=[{'handle':'alpha', 'mode':'full_feed'}, {'handle':'beta', 'mode':'full_feed'}],
            runtime={'date_request_steps_per_tick':1}, themes={'themes':{'live':{}, 'general':{}}}, admin_user_id=1)
        self.app.state = StateStore(Path(self.temp.name)/'state.json')
        self.app.archive_db = ArchiveStore(Path(self.temp.name)/'archive.db')
        self.app.collector = XCollector({}, self.app.settings.sources, {})
        self.app.collector.collect_source = AsyncMock(return_value=[])
        self.app.telegram = Mock()
        self.app.telegram.is_admin_message.return_value = True
        self.app.telegram.send_message.return_value = {'message_id':1}
        self.app.inbox = Mock()
        self.app.themes = Mock()
        self.app.themes.caption.side_effect = lambda group, update, body, *args: body
        self.app.writer = Mock()
        self.app.writer.last_manual_review = {}
        self.app.writer.write_group.side_effect = lambda group: GroupCopy(group.title, group.category, {u.id:'ترجمهٔ درست' for u in group.updates})

    def tearDown(self):
        self.app.archive_db.conn.close()
        self.temp.cleanup()

    def update(self, identifier, author='alpha', minute=0, text='Jeonghan live'):
        return Update(id=identifier, author=author, author_name=author, url='', text=text,
                      created_at=__import__('datetime').datetime(2026,10,4,1,minute,tzinfo=timezone.utc))

    def tick(self, count=1):
        for _ in range(count):
            asyncio.run(process_date_requests(self.app))

    def test_real_admin_entrypoint_enqueues_and_resumes_all_sources_ordered(self):
        self.app.collector.collect_source.side_effect = [[self.update('later',minute=10), self.update('foreign','outsider')], [self.update('early','beta')]]
        asyncio.run(self.app.handle_message({'text':'محتوای جونگهان ۲۰۲۶۱۰۰۴ لایو تولد','from':{'id':1}}))
        jobs = self.app.state.data['date_requests']['jobs']
        self.assertEqual(len(jobs),1)
        self.tick()
        self.app.state = StateStore(self.app.state.path)
        self.tick(5)
        job = next(iter(self.app.state.data['date_requests']['jobs'].values()))
        self.assertEqual(job['delivered'], ['early','later'])
        self.assertEqual(job['status'],'complete')
        self.assertEqual(self.app.collector.collect_source.await_count,2)
        self.assertEqual(self.app.writer.write_group.call_count,2)
        self.assertTrue(all(c.kwargs.get('delivery_key') for c in self.app.telegram.send_message.call_args_list))
        enqueue(self.app,'2026-10-04','live')
        self.tick()
        self.assertEqual(self.app.writer.write_group.call_count,2)

    def test_partial_public_source_is_never_complete(self):
        async def partial(*args):
            self.app.collector.last_errors=['public fallback']
            return [self.update('1')]
        self.app.collector.collect_source.side_effect=partial
        identifier=enqueue(self.app,'2026-10-04','all')
        self.tick(5)
        self.assertEqual(self.app.state.data['date_requests']['jobs'][identifier]['status'],'partial')

    def test_missing_translation_is_pending_not_raw_or_seen(self):
        self.app.archive_db.index_update(self.update('1'))
        self.app.writer.write_group.side_effect=lambda group: GroupCopy('title','live',{})
        identifier=enqueue(self.app,'2026-10-04','live')
        self.tick(3)
        job=self.app.state.data['date_requests']['jobs'][identifier]
        for _ in range(2):
            job['retry_after']=''
            self.tick()
        self.assertEqual(job['status'],'translation_pending')
        self.assertFalse(self.app.state.is_seen('1'))
        self.assertEqual(job['delivered'],[])

    def test_request_send_failure_reuses_persisted_translation_after_restart(self):
        self.app.archive_db.index_update(self.update('1'))
        identifier=enqueue(self.app,'2026-10-04','live')
        self.tick(2)
        self.app.telegram.send_message.side_effect=[{'message_id':1}, RuntimeError('send failed')]
        self.tick()
        self.assertEqual(self.app.writer.write_group.call_count,1)
        self.app.state=StateStore(self.app.state.path)
        self.app.telegram.send_message.side_effect=None
        self.tick(2)
        self.assertEqual(self.app.writer.write_group.call_count,1)
        self.assertEqual(self.app.state.data['date_requests']['jobs'][identifier]['delivered'],['1'])

    def test_author_scoped_search_recovery_remains_partial(self):
        self.app.collector.cookies={'auth_token':'test'}
        self.app.collector.collect_source.side_effect=RuntimeError('timeline unavailable')
        self.app.collector._run_queries=AsyncMock(return_value=[self.update('1'),self.update('outside','outsider')])
        identifier=enqueue(self.app,'2026-10-04','live')
        self.tick(5)
        job=self.app.state.data['date_requests']['jobs'][identifier]
        self.assertEqual(job['status'],'partial')
        self.assertEqual(job['delivered'],['1'])
        self.assertIn('from:alpha',self.app.collector._run_queries.call_args_list[0].args[0][0])

    def test_date_picker_search_uses_date_bundle(self):
        asyncio.run(self.app.run_search('2026-10-04'))
        self.assertEqual(len(self.app.state.data['date_requests']['jobs']),1)

    def test_weverse_text_post_is_not_a_live_seed(self):
        self.assertEqual(select_updates([self.update('1',text='Jeonghan Weverse post')],'live'),[])

    def test_archive_has_no_500_or_eight_item_cap(self):
        for index in range(510):
            self.app.archive_db.index_update(self.update(str(index)))
        identifier=enqueue(self.app,'2026-10-04','all')
        self.tick(3)
        self.assertEqual(len(self.app.state.data['date_requests']['jobs'][identifier]['selected']),510)

    def test_invalid_date_does_not_become_global_search(self):
        self.assertFalse(route_date_request(self.app,''))
        self.assertTrue(route_date_request(self.app,'/date 20261399'))
        self.assertNotIn('date_requests',self.app.state.data)

    def test_thread_extension_and_authorship(self):
        seed=self.update('seed',text='live')
        seed.conversation_id='thread'
        reply=self.update('reply',text='He said hello')
        reply.conversation_id='thread'
        unrelated=self.update('other','beta',text='advert')
        unrelated.conversation_id='thread'
        self.assertEqual({u.id for u in select_updates([seed,reply,unrelated],'live')},{'seed','reply'})

    def test_bridge_accepts_only_fixed_schema_once_after_restart(self):
        self.app.settings.runtime={'repository_content_requests':True}
        response=Mock()
        response.iter_content.return_value=[b'[{"id":"bad","date":"2026-99-99","topic":"live"},{"id":"request-1","date":"2026-10-04","topic":"live"}]']
        with patch('app.date_requests.requests.get',return_value=response) as get:
            sync_repository_requests(self.app)
            self.app.state=StateStore(self.app.state.path)
            self.app.state.data['date_requests']['bridge_checked_at']=''
            sync_repository_requests(self.app)
        self.assertEqual(len(self.app.state.data['date_requests']['jobs']),1)
        self.assertIn('/main/config/content_requests.json',get.call_args.args[0])

    def test_actual_webhook_maintenance_advances_jobs_and_isolates_job_errors(self):
        from app.webhook_server import WebhookRuntime
        runtime=WebhookRuntime()
        runtime.application=self.app
        runtime._save_and_backup_if_changed=Mock()
        self.app.process_due_reminders=AsyncMock()
        self.app.run_scheduled_scan=AsyncMock()
        self.app.deliver_pending=AsyncMock()
        self.app.settings.runtime['date_request_steps_per_tick']=5
        try:
            identifier=enqueue(self.app,'2026-10-04','live')
            runtime.maintenance_sync()
            self.assertFalse(self.app.state.data['date_requests']['jobs'][identifier]['pending_sources'])
            with patch('app.date_requests._process_date_step',new=AsyncMock(side_effect=RuntimeError('job error'))):
                runtime.last_scan_at=__import__('datetime').datetime.min.replace(tzinfo=timezone.utc)
                runtime.maintenance_sync()
            self.assertEqual(self.app.run_scheduled_scan.await_count,2)
            self.assertEqual(self.app.state.data['date_requests']['last_error'],'RuntimeError')
        finally:
            runtime.executor.shutdown(wait=True,cancel_futures=True)
