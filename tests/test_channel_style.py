from __future__ import annotations
import gzip,json,tempfile,unittest
from pathlib import Path
from app.channel_style import ChannelStyleMemory,analyze_source,chronological_style_bonus,historical_base_style_weight,verify_hard_facts
class ChannelStyleTests(unittest.TestCase):
    def _root(self,rows):
        td=tempfile.TemporaryDirectory(); root=Path(td.name); (root/'data').mkdir(); (root/'config').mkdir()
        with gzip.open(root/'data'/'channel_style_examples.jsonl.gz','wt',encoding='utf-8') as fh:
            for row in rows: fh.write(json.dumps(row,ensure_ascii=False)+'\n')
        (root/'config'/'channel_style_profile.json').write_text('{}',encoding='utf-8'); (root/'config'/'channel_glossary.json').write_text('{"categories":{}}',encoding='utf-8'); return td,root
    def _row(self,eid,date='2023-01-01',text='جونگهان امروز خیلی کیوت بود'):
        return {'example_id':eid,'channel_id':'1','message_id':eid,'text':text,'content_type':'OTHER','source_language':'fa','date':date,'line_count':1,'char_count':len(text),'has_dialogue':False,'has_laughter':False,'has_media':False,'format_prefix':''}
    def test_date_has_zero_style_weight(self):
        self.assertEqual(historical_base_style_weight('2023'),historical_base_style_weight('2026')); self.assertEqual(chronological_style_bonus('2023'),0.0); self.assertEqual(chronological_style_bonus('2026'),0.0)
    def test_changing_only_date_cannot_change_retrieval_score(self):
        scores=[]
        for date in ('2023-01-01','2026-12-31'):
            td,root=self._root([self._row('same',date)])
            try:
                memory=ChannelStyleMemory(root,root/'state.sqlite3'); result=memory.retrieve_examples('جونگهان امروز خیلی کیوت بود',analyze_source('جونگهان امروز خیلی کیوت بود'),limit=1); self.assertEqual(len(result),1); scores.append(result[0].score); memory.close()
            finally: td.cleanup()
        self.assertEqual(scores[0],scores[1])
    def test_rebuild_is_duplicate_safe_across_repeated_indexing(self):
        td,root=self._root([self._row('a')])
        try:
            memory=ChannelStyleMemory(root,root/'state.sqlite3'); self.assertEqual(memory.sample_count,1); self.assertEqual(memory.rebuild_from_derived_corpus(),1); self.assertEqual(memory.rebuild_from_derived_corpus(),1); self.assertEqual(memory._count_examples(),1); memory.close()
        finally: td.cleanup()
    def test_hard_fact_gate_numbers_urls_hashtags_laughter(self):
        src='Jeonghan 2026-08-20 https://example.com #JEONGHAN ㅋㅋㅋ'; issues=verify_hard_facts(src,'جونگهان 2026-08-21',analyze_source(src)); self.assertTrue(issues)
    def test_feedback_requires_explicit_confirmation_and_scrubs_context(self):
        td,root=self._root([self._row('a')])
        try:
            memory=ChannelStyleMemory(root,root/'state.sqlite3'); kwargs=dict(source_text='x',source_language='en',content_type='OTHER',generated_text='a',final_user_text='b',timestamp='2026-01-01',context={'TELEGRAM_BOT_TOKEN':'secret','safe':'ok'}); self.assertFalse(memory.add_confirmed_feedback(**kwargs,confirmed=False)); self.assertTrue(memory.add_confirmed_feedback(**kwargs,confirmed=True)); row=memory.conn.execute('select context_json from translation_feedback').fetchone(); self.assertNotIn('TOKEN',row[0]); self.assertIn('safe',row[0]); memory.close()
        finally: td.cleanup()
if __name__=='__main__': unittest.main()
