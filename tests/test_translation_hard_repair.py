import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from app.ai import GroupCopy
from app.models import Update, EventGroup
from app.channel_translation_v2_install import install_direct_v2
from app.channel_style_runtime import verify_hard_facts


class InstalledHardRepair(unittest.TestCase):
    def test_korean_speakers_accept_valid_multiword_persian_names(self):
        source='정한: 말 왜 이렇게 많은 거야?!\n정한: 하 진짜 짜증나….\n정한: 조용히 할게요 ㅋㅋㅋ'
        body='یون جونگهان: چرا این‌قدر حرف می‌زنی؟!\nیون جونگهان: آه واقعاً رو اعصابمه….\nیون جونگهان: ساکت می‌شم ㅋㅋㅋ'
        self.assertEqual(verify_hard_facts(source,body),[])
        self.assertIn('speaker turn structure lost', verify_hard_facts(source,body.splitlines()[0]))
    def writer(self, repaired):
        memory=SimpleNamespace(retrieve_examples=lambda *a,**k: [], relevant_glossary=lambda *a: [])
        writer=install_direct_v2(SimpleNamespace(memory=memory, _client_or_none=lambda: object()))
        writer._direct_group=Mock(return_value=GroupCopy('title','general',{'1':'جونگهان ۳ تا سیب خورد.'}))
        payloads=[]
        def generate(client,prompt,schema,**kwargs):
            payloads.extend(json.loads(prompt.split('FAILED ITEMS:\n')[1]))
            return {'title':'title','category':'general','items':[{'id':'1','body':repaired}]}
        writer._generate_json_v2=generate
        update=Update(id='1',author='source',author_name='source',url='',text='Jeonghan ate 2 apples.',created_at=datetime.now(timezone.utc))
        group=EventGroup(key='x',category='general',title='title',updates=[update])
        return writer,group,payloads

    def test_installed_repair_gets_hard_number_failure_and_fixes_it(self):
        writer,group,payloads=self.writer('جونگهان ۲ تا سیب خورد.')
        result=writer.write_group(group)
        self.assertTrue(any('num' in reason or 'number' in reason for reason in payloads[0]['quality_failures']),payloads)
        self.assertEqual(result.bodies['1'],'جونگهان ۲ تا سیب خورد.')
        self.assertNotIn('1',writer.last_manual_review)

    def test_unrepaired_hard_failure_cannot_pass_finalizer(self):
        writer,group,payloads=self.writer('جونگهان ۳ تا سیب خورد.')
        result=writer.write_group(group)
        self.assertIn('1',writer.last_manual_review)
        self.assertIn('⚠️',result.bodies['1'])
