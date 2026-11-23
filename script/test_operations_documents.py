import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ecom_forecast'))
from api.services import operations_documents as d

URL='https://tenant.feishu.cn/docx/DocumentToken'


class DocumentTests(unittest.TestCase):
    def test_only_recognized_feishu_links_and_tracking_deduplication(self):
        text=f'[机制表]({URL}?from=copy) {URL}。 https://evil.test/docx/evil https://tenant.feishu.cn.evil.test/docx/evil'
        self.assertEqual(d.links(text),[URL])
        for url in ['http://tenant.feishu.cn/docx/A','https://user:password@tenant.feishu.cn/docx/A','https://tenant.feishu.cn:1234/docx/A','https://evil.test/docx/A']:
            with self.assertRaises(ValueError):d.locator(url)

    def test_doc_fetch_preserves_content_revision_and_user_identity(self):
        payload={'ok':True,'data':{'document':{'title':'机制表','content':'主播：李芊默\n商品：舒敏霜','revision_id':7}}}
        with patch.object(d.im,'call',return_value=payload) as call:result=d.read_document(URL)
        self.assertEqual(result['content'],'主播：李芊默\n商品：舒敏霜');self.assertEqual(result['revision'],7)
        self.assertEqual(result['url'],URL);self.assertTrue(result['sha256'])
        args=call.call_args.args[0];self.assertEqual(args[:2],['docs','+fetch']);self.assertEqual(args[args.index('--as')+1],'user')
        self.assertEqual(args[args.index('--doc-format')+1],'markdown')

    def test_wiki_resolves_document_type_before_fetch(self):
        with patch.object(d.im,'call',side_effect=[{'data':{'obj_type':'docx','obj_token':'ActualDoc','title':'知识库'}},{'data':{'document':{'content':'商品：舒敏霜'}}}]) as call:
            result=d.read_document('https://tenant.feishu.cn/wiki/WikiToken')
        self.assertEqual(call.call_args_list[0].args[0][:2],['wiki','+node-get'])
        self.assertIn('ActualDoc',call.call_args_list[1].args[0]);self.assertEqual(result['title'],'知识库')

    def test_sheets_read_values_with_source_coordinates(self):
        metadata={'data':{'sheets':[{'sheet_id':'sheet1','title':'机制表','row_count':2,'column_count':2}]}}
        cells={'data':{'ranges':[{'row_indices':[1,2],'col_indices':['A','B'],'cells':[[{'value':'商品'},{'value':'目标'}],[{'value':'舒敏霜'},{'value':150000}]]}]}}
        with patch.object(d.im,'call',side_effect=[metadata,cells]):result=d.read_document('https://tenant.feishu.cn/sheets/Workbook?sheet=sheet1')
        self.assertIn('A2=舒敏霜',result['content']);self.assertIn('B2=150000',result['content']);self.assertIn('sheet1!A1:B2',result['content'])

    def test_partial_empty_and_oversized_document_are_errors(self):
        for document in [{'content':'','revision_id':1},{'content':'短文','truncated':True},{'content':'x'*30001}]:
            with patch.object(d.im,'call',return_value={'data':{'document':document}}):
                with self.assertRaises(ValueError):d.read_document(URL)

    def test_cached_snapshot_reused_across_retry_without_mutating_original(self):
        messages=[{'id':'one','content':URL},{'id':'two','content':URL}];cache={};saved=[]
        snapshot={'url':URL,'title':'表格','content':'商品：舒敏霜','read_at':'2026-11-12T00:00:00Z'}
        with patch.object(d,'read_document',return_value=snapshot) as read:
            result=d.enrich(messages,cache,on_snapshot=saved.append);d.enrich(messages,cache)
        self.assertEqual(read.call_count,1);self.assertEqual(len(saved),1)
        self.assertNotIn('documents',messages[0]);self.assertIn('商品：舒敏霜',d.source(result[0]))

    def test_new_reply_context_can_read_updated_document(self):
        snapshot={'url':URL,'title':'机制表','content':'商品：舒敏霜'};cache={}
        with patch.object(d,'read_document',return_value=snapshot) as read:
            d.enrich([{'content':URL}],cache,cache_scope='reply1');d.enrich([{'content':URL}],cache,cache_scope='reply1');d.enrich([{'content':URL}],cache,cache_scope='reply2')
        self.assertEqual(read.call_count,2)


if __name__=='__main__':unittest.main()
