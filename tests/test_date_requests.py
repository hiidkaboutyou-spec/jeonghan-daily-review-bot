import os
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
from app.x_client import XCollector, XCollectionError
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
        asyncio.run(self.app.handle_message({'text':'محتوای جونگهان ۲۰۲۶۱۰۰۴ لایو','from':{'id':1}}))
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

    def test_repeat_completed_historical_day_collects_late_posts_without_duplicate_delivery(self):
        # Explicit repeat after a historical day completed is a real refresh,
        # not a silent no-op. It must fetch all sources and deliver only new IDs.
        first = self.update('first', text='Jeonghan live')
        late = self.update('late', minute=10, text='Jeonghan live recap')
        seen_alpha = 0

        async def collect(handle, start, end):
            nonlocal seen_alpha
            if handle == 'alpha':
                seen_alpha += 1
                return [first] if seen_alpha == 1 else [first, late]
            return []

        self.app.collector.collect_source.side_effect = collect
        identifier = enqueue(self.app, '2026-10-04', 'live')
        self.tick(8)
        job = self.app.state.data['date_requests']['jobs'][identifier]
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(job['delivered'], ['first'])
        enqueue(self.app, '2026-10-04', 'live')
        self.assertEqual(job['status'], 'collecting')
        self.assertEqual(job['pending_sources'], ['alpha', 'beta'])
        self.tick(8)
        self.assertEqual(seen_alpha, 2)
        self.assertEqual(job['delivered'], ['first', 'late'])
        self.assertEqual(self.app.writer.write_group.call_count, 2)
        keys = [c.kwargs.get('delivery_key') for c in self.app.telegram.send_message.call_args_list]
        self.assertIn(f'date-request:{identifier}:overview:1', keys)
        self.assertIn(f'date-request:{identifier}:overview:2', keys)
        self.assertIn(f'date-request:{identifier}:finished:1:1', keys)
        self.assertIn(f'date-request:{identifier}:finished:2:2', keys)

    def test_birthday_live_intent_and_actual_private_delivery_are_event_scoped(self):
        # The real birthday-live request must not return every unrelated live,
        # greeting, or entertainment post from the same calendar day.
        live_seed = self.update('birthday-seed', text='JEONGHAN birthday LIVE')
        live_seed.conversation_id = 'birthday-thread'
        continuation = self.update('continuation', minute=1, text='He talked about his cake')
        continuation.conversation_id = 'birthday-thread'
        unrelated_live = self.update('unrelated-live', minute=2, text='Hoshi live replay')
        unrelated_live.conversation_id = 'different-live'
        greeting = self.update('birthday-greeting', minute=3, text='Happy birthday JEONGHAN!')
        greeting.conversation_id = 'birthday-greeting'
        korean = self.update('korean', author='beta', minute=4, text='정한 생일 라이브')
        japanese = self.update('japanese', author='beta', minute=5, text='ジョンハン 誕生日ライブ')
        foreign_thread = self.update('foreign-thread', author='beta', minute=6, text='Unrelated message')
        foreign_thread.conversation_id = 'birthday-thread'
        self.app.collector.collect_source.side_effect = [
            [unrelated_live, live_seed, continuation, greeting],
            [korean, japanese, foreign_thread],
        ]
        asyncio.run(self.app.handle_message({
            'text': 'تمام آپدیت‌های لایو تولد جونگهان ۲۶۱۰۰۴ رو به ترتیب بفرست',
            'from': {'id': 1},
        }))
        job = next(iter(self.app.state.data['date_requests']['jobs'].values()))
        self.assertEqual(job['topic'], 'birthday_live')
        self.tick(12)
        self.assertEqual(job['selected'], [
            'birthday-seed', 'continuation', 'korean', 'japanese',
        ])
        self.assertEqual(job['delivered'], job['selected'])
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(self.app.writer.write_group.call_count, 4)

    def test_birthday_live_is_not_a_general_birthday_or_general_live_search(self):
        updates = [
            self.update('wishes', text='Happy birthday JEONGHAN'),
            self.update('other-live', minute=1, text='Joshua went live'),
            self.update('event', minute=2, text='Jeonghan birthday live'),
        ]
        self.assertEqual(
            [u.id for u in select_updates(updates, 'birthday_live')], ['event']
        )
        self.assertEqual(
            [u.id for u in select_updates(updates, 'live')], ['other-live', 'event']
        )

    def test_current_day_stays_open_for_new_posts_and_can_be_rescanned(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        identifier = enqueue(self.app, day, "all")
        self.tick(4)
        job = self.app.state.data["date_requests"]["jobs"][identifier]
        self.assertEqual(job["status"], "partial")
        self.assertEqual(set(job["coverage"].values()), {"complete"})

        enqueue(self.app, day, "all")
        self.assertEqual(job["status"], "collecting")
        self.assertEqual(job["pending_sources"], ["alpha", "beta"])
        self.assertFalse(job.get("local_loaded", True))

    def test_provisional_snapshot_is_rescanned_after_the_day_ends(self):
        identifier = enqueue(self.app, "2026-10-04", "all")
        job = self.app.state.data["date_requests"]["jobs"][identifier]
        job.update({
            "status": "partial",
            "coverage": {"alpha": "complete", "beta": "complete"},
            "pending_sources": [],
            "provisional_day": True,
            "local_loaded": True,
        })
        enqueue(self.app, "2026-10-04", "all")
        self.assertEqual(job["status"], "collecting")
        self.assertEqual(job["pending_sources"], ["alpha", "beta"])
        self.assertFalse(job["local_loaded"])

    def test_partial_public_source_is_never_complete(self):
        async def partial(*args):
            self.app.collector.last_errors=['public fallback']
            return [self.update('1')]
        self.app.collector.collect_source.side_effect=partial
        identifier=enqueue(self.app,'2026-10-04','all')
        self.tick(5)
        self.assertEqual(self.app.state.data['date_requests']['jobs'][identifier]['status'],'partial')

    def test_timeline_to_search_fallback_cannot_claim_complete_source(self):
        sources=[{'handle':'alpha', 'mode':'full_feed'}]
        self.app.settings.sources=sources
        collector=XCollector({'auth_token':'test','ct0':'test'},sources,{})
        collector._collect_source_timeline=AsyncMock(side_effect=XCollectionError('timeline unavailable'))
        collector._run_queries=AsyncMock(return_value=[self.update('1')])
        self.app.collector=collector

        identifier=enqueue(self.app,'2026-10-04','all')
        # Other integration modules intentionally toggle this process-wide flag.
        # Keep this regression on the authenticated collector path it is proving.
        with patch.dict(os.environ, {'X_PROVIDER_PREFLIGHT': ''}, clear=False):
            self.tick()

        job=self.app.state.data['date_requests']['jobs'][identifier]
        self.assertEqual(job['coverage']['alpha'],'partial')
        self.assertTrue(any('source_timeline_fallback' in error for error in collector.last_errors))

        self.tick(3)
        self.assertEqual(job['status'],'partial')

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

    def test_free_fxtwitter_recovery_keeps_reply_chain_with_missing_conversation_id(self):
        # FxTwitter returns the reply_to_id but may omit the common
        # conversation_id. Free public recovery must retain the whole
        # same-author thread without inferring other authors' content.
        seed = self.update('seed', text='Jeonghan birthday LIVE')
        continuation = self.update('part-2', minute=1, text='He says he missed everyone')
        continuation.reply_to_id = 'seed'
        continuation.conversation_id = 'part-2'
        later = self.update('part-3', minute=2, text='He talks about his cake')
        later.reply_to_id = 'part-2'
        later.conversation_id = 'part-3'
        other_author = self.update('other', author='beta', minute=3, text='Unrelated')
        other_author.reply_to_id = 'seed'
        other_author.conversation_id = 'other'
        disconnected = self.update('disconnected', minute=4, text='Unrelated')
        disconnected.reply_to_id = 'unknown-post'
        disconnected.conversation_id = 'disconnected'

        actual = select_updates(
            [later, other_author, disconnected, continuation, seed],
            'birthday_live',
        )
        self.assertEqual([u.id for u in actual], ['seed', 'part-2', 'part-3'])

    def test_free_reply_chain_does_not_turn_unrelated_live_into_birthday_live(self):
        event = self.update('birth', text='Birthday LIVE of JEONGHAN')
        normal_live = self.update('other-live', minute=1, text='Hoshi live')
        unrelated_reply = self.update('other-part', minute=2, text='Unrelated live discussion')
        unrelated_reply.reply_to_id = 'other-live'
        unrelated_reply.conversation_id = 'other-part'
        self.assertEqual(
            [u.id for u in select_updates(
                [event, normal_live, unrelated_reply],
                'birthday_live',
            )],
            ['birth'],
        )

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
