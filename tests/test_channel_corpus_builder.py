from __future__ import annotations
import importlib.util,tempfile,unittest
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('builder',Path(__file__).parents[1]/'tools'/'build_channel_style_corpus.py'); builder=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(builder)
class CorpusBuilderTests(unittest.TestCase):
    def test_plain_string_and_entity_array(self):
        self.assertEqual(builder.visible_text('abc'),'abc'); self.assertEqual(builder.visible_text(['a',{'type':'bold','text':'b'},'c']),'abc')
    def test_truncated_export_recovers_complete_objects_only(self):
        raw='{"name":"x","type":"public_channel","id":1,"messages":[{"id":1,"type":"message","text":"ok"},{"id":2,"type":"message","text":["a",{"type":"bold","text":"b"}]},{"id":3,"type":"message","text":"broken"'
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'result 4.json'; p.write_text(raw,encoding='utf-8'); meta,msgs,truncated=builder.load_export(p); self.assertTrue(truncated); self.assertEqual(meta['id'],1); self.assertEqual([m['id'] for m in msgs],[1,2])
    def test_stable_identity_not_text_similarity_is_dedup_key(self):
        self.assertNotEqual(('1','10'),('1','11'))
if __name__=='__main__': unittest.main()
