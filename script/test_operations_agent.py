import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ecom_forecast'))
from api.services import operations_agent as a
from api.routers.operations_agent import Task

SAMPLE='11月达播机制表\n李芊默  11月11日    预估纯销15w   辛苦检查链接@B6李智'


class OperationsAgentTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.store=patch.object(a,'STORE',Path(self.folder.name)/'agent.sqlite3');self.store.start()
        a.STOP.clear()
        self.config=Task(name='意向跟进',engine='rules',groups=[{'id':'oc_group','name':'运营群'}],senders=[{'id':'ou_author','name':'指定人员'}]).model_dump()
        self.task=a.save_task(self.config)
        self.group=self.config['groups'][0]
        self.message={'id':'om_message','sender_id':'ou_author','sender':'指定人员','content':SAMPLE,'time':'2026-11-11T10:00:00+08:00'}

    def tearDown(self):self.store.stop();self.folder.cleanup()

    def test_sample_preserves_sales_basis_and_missing_products(self):
        result=a.analyze(SAMPLE,2026,'rules',self.task['id'])
        self.assertTrue(result['is_live'])
        self.assertEqual(result['anchor'],'李芊默')
        self.assertEqual(result['live_date'],'2026-11-11')
        self.assertEqual(result['sales_amount'],150000)
        self.assertEqual(result['sales_basis'],'纯销')
        self.assertIsNone(result['planned_quantity'])
        self.assertEqual(a.missing_fields(result),['商品及规格'])

    def test_sender_filter_deduplication_and_draft_mode_never_send(self):
        with patch.object(a.im,'send_message') as send:
            other={**self.message,'sender_id':'ou_other'};a.process_message(self.task,self.group,other,True)
            self.assertEqual(a.all_items('opportunity'),[])
            a.process_message(self.task,self.group,self.message,False)
            a.process_message(self.task,self.group,self.message,False)
        send.assert_not_called()
        items=a.all_items('opportunity');self.assertEqual(len(items),1)
        self.assertEqual(items[0]['question_status'],'draft')
        self.assertIn('商品及规格',items[0]['question_text'])

    def test_auto_requires_active_task_and_sends_only_to_original_sender_once(self):
        task={**self.task,'followup_mode':'auto'};a.put('task',task)
        with patch.object(a.im,'send_message',return_value={'message_id':'om_sent'}) as send:
            a.process_message(task,self.group,self.message,True)
            send.assert_not_called()
            task['enabled']=True;a.put('task',task)
            item=a.all_items('opportunity')[0]
            a.send_followup(item['id'],automatic=True)
            a.send_followup(item['id'],automatic=True)
        self.assertEqual(send.call_count,1)
        request=send.call_args.args[0]
        self.assertEqual(request.target_id,'ou_author')
        self.assertEqual(request.kind,'person')
        self.assertTrue(request.confirmed)
        self.assertEqual(a.get('opportunity',item['id'])['state'],'awaiting_reply')

    def test_stopped_task_does_not_send_and_unknown_send_is_not_repeated(self):
        a.process_message(self.task,self.group,self.message,False);item=a.all_items('opportunity')[0]
        with patch.object(a.im,'send_message') as send:
            a.send_followup(item['id'],automatic=True)
            item['question_status']='unknown';a.put('opportunity',item)
            a.send_followup(item['id'],False,a.question(item))
        send.assert_not_called()

    def test_private_reply_completes_plan_and_is_recorded(self):
        task={**self.task,'enabled':True};a.put('task',task)
        a.process_message(task,self.group,self.message,False);item=a.all_items('opportunity')[0]
        item.update(question_status='sent',state='awaiting_reply',question_sent_at='2026-11-11T01:00:00+00:00');a.put('opportunity',item)
        response={'items':[{'id':'om_reply','sender_id':'ou_author','content':'商品：舒敏特护霜50g','time':'2026-11-11T02:00:00Z'}],'has_more':False,'page_token':''}
        with patch.object(a.im,'messages',return_value=response):a.read_replies(task)
        result=a.get('opportunity',item['id'])
        self.assertEqual(result['analysis']['products'],['舒敏特护霜50g'])
        self.assertEqual(result['analysis']['sales_amount'],150000)
        self.assertEqual(result['state'],'ready')
        self.assertEqual(len(result['replies']),1)

    def test_more_than_three_pages_resume_same_read_window(self):
        pages=[{'items':[],'has_more':True,'page_token':f'token{i}'} for i in range(3)]+[{'items':[],'has_more':False,'page_token':''}]
        with patch.object(a.im,'messages',side_effect=pages) as read:
            a.read_window(self.task,self.group,False)
            cursor=a.get('task',self.task['id'])['cursors']['oc_group']
            self.assertEqual(cursor['page_token'],'token2')
            a.read_window(self.task,self.group,False)
        first,last=read.call_args_list[0].args[0],read.call_args_list[-1].args[0]
        self.assertEqual(first.start,last.start);self.assertEqual(first.end,last.end)
        self.assertIn('through',a.get('task',self.task['id'])['cursors']['oc_group'])

    def test_model_output_needs_real_evidence_and_matching_numbers(self):
        result=a.rules(SAMPLE,2026).model_dump(mode='json');result['sales_amount']=999999
        with patch('api.services.expected_gmv.model_call',return_value={'content':json.dumps(result)}):
            with self.assertRaisesRegex(ValueError,'数量或金额'):a.analyze(SAMPLE,2026,'deepseek',self.task['id'])
        result=a.rules(SAMPLE,2026).model_dump(mode='json');result['products']=['未出现的商品'];result['evidence']['products']='纯销15w'
        with patch('api.services.expected_gmv.model_call',return_value={'content':json.dumps(result)}):
            with self.assertRaisesRegex(ValueError,'商品名称'):a.analyze(SAMPLE,2026,'deepseek',self.task['id'])

    def test_manual_followup_rejects_stale_preview(self):
        a.process_message(self.task,self.group,self.message,False);item=a.all_items('opportunity')[0]
        with patch.object(a.im,'send_message') as send:
            with self.assertRaisesRegex(ValueError,'内容已变化'):a.send_followup(item['id'],False,'过期的草稿')
        send.assert_not_called()

    def test_disabled_dry_run_does_not_send_even_with_auto_config(self):
        task={**self.task,'followup_mode':'auto'};a.put('task',task)
        with patch('api.services.operations_queue.scan_task') as scan,patch.object(a.im,'send_message') as send:a.cycle(task['id'],dry_run=True)
        send.assert_not_called();scan.assert_called_once()
        self.assertTrue(scan.call_args.args[1])
        self.assertFalse(a.get('task',task['id'])['running'])

    def test_model_cannot_treat_sales_amount_as_order_quantity(self):
        result=a.rules(SAMPLE,2026).model_dump(mode='json')
        result['planned_quantity']=150000;result['evidence']['planned_quantity']='纯销15w'
        with patch('api.services.expected_gmv.model_call',return_value={'content':json.dumps(result)}):
            with self.assertRaisesRegex(ValueError,'不能把金额当作单量'):a.analyze(SAMPLE,2026,'deepseek',self.task['id'])

    def test_failed_extraction_does_not_advance_group_cursor(self):
        response={'items':[self.message],'has_more':False,'page_token':''}
        with patch.object(a.im,'messages',return_value=response),patch.object(a,'analyze',side_effect=ValueError('证据不符')):
            with self.assertRaises(ValueError):a.read_window(self.task,self.group,False)
        self.assertEqual(a.get('task',self.task['id'])['cursors'],{})

    def test_multiple_pending_events_do_not_guess_reply_assignment(self):
        a.process_message(self.task,self.group,self.message,False)
        a.process_message(self.task,self.group,{**self.message,'id':'om_other','content':SAMPLE.replace('11月11日','11月12日')},False)
        for item in a.all_items('opportunity'):
            item.update(question_status='sent',state='awaiting_reply',question_sent_at='2026-11-11T01:00:00+00:00');a.put('opportunity',item)
        with patch.object(a.im,'messages') as read:a.read_replies(self.task)
        read.assert_not_called()

    def test_recalled_source_message_stops_followup(self):
        a.process_message(self.task,self.group,self.message,False)
        a.process_message(self.task,self.group,{**self.message,'deleted':True},True)
        item=a.all_items('opportunity')[0]
        self.assertEqual(item['state'],'ignored')
        self.assertTrue(item['source_recalled'])
        with patch.object(a.im,'send_message') as send:
            with self.assertRaises(ValueError):a.send_followup(item['id'],False,a.question(item))
        send.assert_not_called()


if __name__=='__main__':unittest.main()
