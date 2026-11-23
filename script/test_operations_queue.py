import tempfile
import sys
import unittest
import json
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ecom_forecast'))
from api.database import Base
from api.models import Shop,Platform,AgentWindow,AgentTodo,AgentQueue,LiveSchedule,AgentOutbox
from api.services import operations_agent as a,operations_queue as q

SOURCE='11月达播机制表\n李芊默 11月12日 预估纯销15w'


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();root=Path(self.folder.name)
        self.engine=create_engine('sqlite:///'+str(root/'queue.sqlite3'))
        Base.metadata.create_all(self.engine);self.sessions=sessionmaker(bind=self.engine,expire_on_commit=False)
        self.patches=[patch.object(a,'STORE',root/'config.sqlite3'),patch.object(q,'SessionLocal',self.sessions),patch.object(q,'SCHEMA_READY',True),patch.object(q,'utc',return_value=datetime(2026,11,12,2,10))]
        for item in self.patches:item.start()
        with self.sessions.begin() as db:db.add(Platform(platform_id='TM',name='天猫'));db.add(Shop(shop_id='test_shop',name='测试店铺',platform_id='TM'))
        self.task=a.save_task({'name':'队列测试','groups':[{'id':'oc_group','name':'群'}],'senders':[{'id':'ou_author','name':'人员'}],
            'interval_seconds':30,'lookback_hours':1,'engine':'rules','followup_mode':'auto','shop_id':'test_shop','schedule_channel':'达播','window_minutes':5,
            'scan_start':'2026-11-12T02:00:00+00:00','scan_end':'2026-11-12T02:05:00+00:00'})
        self.task['enabled']=True;a.put('task',self.task);a.STOP.clear()
        self.group=self.task['groups'][0]

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.engine.dispose();self.folder.cleanup()

    def response(self,content=SOURCE,identity='om_source',time='2026-11-12T02:03:00+00:00'):
        return {'items':[{'id':identity,'content':content,'sender_id':'ou_author','sender':'人员','time':time}],'has_more':False,'page_token':''}

    def scan(self,content=SOURCE,dry=False):
        with patch.object(q.im,'messages',return_value=self.response(content)) as read:q.scan_group(self.task,self.group,dry)
        return read

    def rows(self,model):
        with self.sessions() as db:return db.query(model).all()

    def claim(self):
        with self.sessions.begin() as db:
            for row in db.query(AgentQueue).filter(AgentQueue.status.in_(['retry','pending'])):row.available_at=q.utc()
        claimed=q.claim();self.assertIsNotNone(claimed);return claimed

    def test_delay_one_minute_and_no_live_window_is_never_read_again(self):
        with patch.object(q,'utc',return_value=datetime(2026,11,12,2,5,59)),patch.object(q.im,'messages') as read:
            self.assertFalse(q.scan_group(self.task,self.group));read.assert_not_called()
        read=self.scan('今天没有其它事')
        self.assertEqual(read.call_args.args[0].end.isoformat(),'2026-11-12T02:05:00+00:00')
        self.assertEqual(self.rows(AgentWindow)[0].status,'completed')
        with patch.object(q.im,'messages') as read:self.assertFalse(q.scan_group(self.task,self.group));read.assert_not_called()

    def test_complete_intent_writes_formal_schedule_with_pure_sales_separate(self):
        self.scan(SOURCE+'\n商品：舒敏霜50g')
        schedule=self.rows(LiveSchedule)[0]
        self.assertEqual(schedule.products,['舒敏霜50g']);self.assertEqual(schedule.sales_amount,150000)
        self.assertEqual(schedule.sales_basis,'纯销');self.assertIsNone(schedule.expected_revenue)
        self.assertEqual(self.rows(AgentTodo)[0].status,'persisted');self.assertEqual(self.rows(AgentWindow)[0].status,'completed')
        self.assertEqual(len(self.rows(AgentQueue)),0)

    def test_missing_info_has_original_messages_judgment_goal_and_prompt(self):
        self.scan();todo=self.rows(AgentTodo)[0];node=self.rows(AgentQueue)[0]
        self.assertEqual(todo.payload['missing'],['商品及规格']);self.assertEqual(todo.payload['original_messages'][0]['content'],SOURCE)
        self.assertTrue(todo.payload['llm_judgment']);self.assertEqual(node.window_id,todo.window_id)
        self.assertIn('商品及规格',node.system_prompt);self.assertEqual(node.goal['missing'],['商品及规格'])

    def test_classifier_failure_retries_cached_messages_without_false_ack(self):
        with patch.object(q.im,'messages',return_value=self.response()) as read,patch.object(a,'analyze',side_effect=ValueError('LLM异常')):
            with self.assertRaises(ValueError):q.scan_group(self.task,self.group)
        self.assertEqual(read.call_count,1);self.assertEqual(self.rows(AgentWindow)[0].status,'error')
        self.assertEqual(len(self.rows(AgentTodo)),0)
        with patch.object(q.im,'messages') as read:q.scan_group(self.task,self.group);read.assert_not_called()
        self.assertEqual(self.rows(AgentWindow)[0].status,'waiting')

    def test_normal_unfinished_slice_creates_child_atomically(self):
        self.scan();identity,owner=self.claim()
        q.consume(identity,owner,slice_seconds=0)
        rows=self.rows(AgentQueue);self.assertEqual(len(rows),2)
        parent=next(x for x in rows if x.id==identity);child=next(x for x in rows if x.parent_id==identity)
        self.assertEqual(parent.status,'succeeded');self.assertFalse(parent.result['goal_complete'])
        self.assertEqual(child.window_id,parent.window_id);self.assertEqual(parent.result['next_node_id'],child.id)
        self.assertEqual(self.rows(AgentWindow)[0].status,'waiting')

    def test_tool_error_does_not_ack_or_create_child_and_retry_does_not_send_twice(self):
        self.scan();identity,owner=self.claim()
        with patch.object(q.im,'send_message',return_value={'message_id':'om_sent'}) as send,patch.object(q.im,'messages',side_effect=RuntimeError('接收工具异常')):
            q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        self.assertEqual(send.call_count,1);self.assertEqual(self.rows(AgentQueue)[0].status,'retry');self.assertEqual(len(self.rows(AgentQueue)),1)
        identity,owner=self.claim()
        reply=self.response('商品：舒敏霜50g','om_reply','2026-11-12T02:10:00+00:00')
        with patch.object(q.im,'send_message') as send,patch.object(q.im,'messages',return_value=reply):q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        send.assert_not_called();self.assertEqual(self.rows(AgentQueue)[0].status,'completed')
        self.assertEqual(len(self.rows(LiveSchedule)),1);self.assertEqual(self.rows(AgentWindow)[0].status,'completed')

    def test_schedule_failure_rolls_back_and_retries_same_node(self):
        self.scan(SOURCE+'\n商品：舒敏霜50g',dry=True)
        todo=self.rows(AgentTodo)[0];q.approve(todo.id,'');identity,owner=self.claim()
        original=q.publish
        def fail(db,todo,task,node_id=None):
            original(db,todo,task,node_id);raise RuntimeError('事务提交前异常')
        with patch.object(q,'publish',side_effect=fail):q.consume(identity,owner,slice_seconds=0)
        self.assertEqual(len(self.rows(LiveSchedule)),0);self.assertEqual(self.rows(AgentQueue)[0].status,'retry')
        self.assertNotEqual(self.rows(AgentTodo)[0].status,'persisted')
        identity,owner=self.claim();q.consume(identity,owner,slice_seconds=0)
        self.assertEqual(len(self.rows(LiveSchedule)),1);self.assertEqual(self.rows(AgentQueue)[0].status,'completed')

    def test_cross_window_supplement_completes_old_goal_without_private_message(self):
        self.task['scan_end']='2026-11-12T02:10:00+00:00';a.put('task',self.task)
        self.scan()
        with patch.object(q,'utc',return_value=datetime(2026,11,12,2,11)),patch.object(q.im,'messages',return_value=self.response('商品：舒敏霜50g','om_supplement','2026-11-12T02:06:00+00:00')),patch.object(q.im,'send_message') as send:q.scan_group(self.task,self.group)
        send.assert_not_called();self.assertEqual(len(self.rows(AgentTodo)),1);self.assertEqual(len(self.rows(LiveSchedule)),1)
        self.assertTrue(all(w.status=='completed' for w in self.rows(AgentWindow)))

    def test_expired_lease_is_reclaimed_and_stale_owner_cannot_ack(self):
        self.scan();identity,old_owner=self.claim()
        with self.sessions.begin() as db:db.get(AgentQueue,identity).lease_until=q.utc()-timedelta(seconds=1)
        with q.ACTIVE_LOCK:q.ACTIVE.discard(identity)
        identity,owner=self.claim();self.assertNotEqual(old_owner,owner)
        with self.sessions.begin() as db:
            with self.assertRaises(RuntimeError):q.owned(db,identity,old_owner)
        with q.ACTIVE_LOCK:q.ACTIVE.discard(identity)

    def test_pending_unknown_send_is_reconciled_not_blindly_resent(self):
        self.scan();identity,owner=self.claim()
        error=q.im.CliError({'message':'发送超时','kind':'timeout'})
        with patch.object(q.im,'send_message',side_effect=error) as send:q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        self.assertEqual(send.call_count,1);self.assertEqual(self.rows(AgentQueue)[0].status,'retry')
        identity,owner=self.claim()
        with patch.object(q.im,'send_message') as send,patch.object(q.im,'messages',return_value={'items':[],'has_more':False,'page_token':''}):q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        send.assert_not_called();self.assertEqual(self.rows(AgentQueue)[0].status,'retry')

    def test_continuous_scan_reads_settled_tail_before_asking(self):
        self.scan();self.task.update(scan_start=None,scan_end=None);a.put('task',self.task)
        with self.sessions.begin() as db:db.query(AgentQueue).first().available_at=q.utc()
        self.assertIsNone(q.claim());self.assertEqual(self.rows(AgentQueue)[0].attempts,0)
        with patch.object(q.im,'messages',return_value=self.response('商品：舒敏霜50g','om_tail','2026-11-12T02:06:00+00:00')) as read:q.scan_group(self.task,self.group)
        self.assertEqual(read.call_args.args[0].end.isoformat(),'2026-11-12T02:09:00+00:00')
        self.assertEqual(len(self.rows(LiveSchedule)),1)

    def test_llm_failure_after_send_retries_without_acknowledgement(self):
        self.scan();identity,owner=self.claim()
        reply=self.response('商品：舒敏霜50g','om_reply','2026-11-12T02:10:00+00:00')
        with patch.object(q.im,'send_message',return_value={'message_id':'om_sent'}),patch.object(q.im,'messages',return_value=reply),patch.object(a,'analyze',side_effect=ValueError('LLM结构化结果异常')):
            q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        row=self.rows(AgentQueue)[0]
        self.assertEqual(row.status,'retry');self.assertIn('LLM',row.last_error)
        self.assertEqual(row.checkpoint.get('replies'),[]);self.assertEqual(len(self.rows(AgentQueue)),1)

    def test_child_creation_failure_does_not_ack_parent(self):
        self.scan();identity,owner=self.claim();original=q.node
        def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('交接事务异常')
        with patch.object(q,'node',side_effect=fail):q.consume(identity,owner,slice_seconds=0)
        self.assertEqual(len(self.rows(AgentQueue)),1);self.assertEqual(self.rows(AgentQueue)[0].status,'retry')
        identity,owner=self.claim();q.consume(identity,owner,slice_seconds=0)
        self.assertEqual(len(self.rows(AgentQueue)),2)

    def test_model_cannot_route_event_to_out_of_scope_sender(self):
        message=self.response()['items'][0]
        fake={'has_live':True,'judgment':'识别直播','events':[{'sender_id':'ou_other','message_ids':[message['id']],'analysis':a.rules(SOURCE,2026).model_dump(mode='json')}]}
        with patch('api.services.expected_gmv.model_call',return_value={'content':json.dumps(fake)}):
            with self.assertRaisesRegex(ValueError,'其它发送人'):q.extract([message],[],{**self.task,'engine':'deepseek'},2026)

    def test_window_records_paginate_beyond_original_100_limit(self):
        with self.sessions.begin() as db:
            for number in range(125):
                start=datetime(2026,11,1)+timedelta(minutes=number)
                db.add(AgentWindow(id=f'{number:032}',task_id=self.task['id'],group_id='oc_group',start_at=start,end_at=start+timedelta(minutes=1),status='completed',payload={'messages':[],'todo_ids':[]},created_at=start,updated_at=start))
        result=q.state(window_page=12,window_page_size=10)
        self.assertEqual(result['window_total'],125);self.assertEqual(len(result['windows']),10)
        self.assertEqual(result['windows'][0]['id'],f'{14:032}')
        self.assertEqual(len(q.state(window_page=13,window_page_size=10)['windows']),5)

    def test_document_content_completes_plan_without_replacing_chat_original(self):
        url='https://tenant.feishu.cn/docx/Mechanism'
        snapshot={'url':url,'title':'机制表','content':'商品：舒敏霜50g','read_at':'2026-11-12T02:10:00Z'}
        with patch.object(q.docs,'read_document',return_value=snapshot):self.scan(SOURCE+'\n'+url)
        todo=self.rows(AgentTodo)[0]
        self.assertNotIn('商品：',todo.payload['source_content'])
        self.assertIn('商品：舒敏霜50g',todo.payload['context_content'])
        self.assertEqual(self.rows(LiveSchedule)[0].products,['舒敏霜50g'])
        window=q.detail('window',self.rows(AgentWindow)[0].id)
        self.assertEqual(window['messages'][0]['content'],SOURCE+'\n'+url)
        self.assertEqual(window['messages'][0]['documents'][0]['url'],url)

    def test_document_failure_cannot_be_classified_as_no_live(self):
        url='https://tenant.feishu.cn/docx/Mechanism'
        with patch.object(q.docs,'read_document',side_effect=ValueError('文档权限不足')),patch.object(q.im,'messages',return_value=self.response(url)):
            with self.assertRaises(ValueError):q.scan_group(self.task,self.group)
        self.assertEqual(self.rows(AgentWindow)[0].status,'error');self.assertEqual(len(self.rows(AgentTodo)),0)
        snapshot={'url':url,'title':'机制表','content':SOURCE+'\n商品：舒敏霜50g','read_at':'2026-11-12T02:10:00Z'}
        with patch.object(q.docs,'read_document',return_value=snapshot),patch.object(q.im,'messages') as read:q.scan_group(self.task,self.group);read.assert_not_called()
        self.assertEqual(len(self.rows(LiveSchedule)),1)

    def test_private_reply_document_can_complete_queue_goal(self):
        self.scan();identity,owner=self.claim();url='https://tenant.feishu.cn/docx/ReplyDoc'
        snapshot={'url':url,'title':'商品表','content':'商品：舒敏霜50g','read_at':'2026-11-12T02:10:00Z'}
        with patch.object(q.im,'send_message',return_value={'message_id':'om_sent'}),patch.object(q.im,'messages',return_value=self.response(url,'om_reply','2026-11-12T02:10:00Z')),patch.object(q.docs,'read_document',return_value=snapshot):q.consume(identity,owner,slice_seconds=2,wait=lambda _:None)
        self.assertEqual(self.rows(AgentQueue)[0].status,'completed');self.assertEqual(self.rows(LiveSchedule)[0].products,['舒敏霜50g'])

    def test_llm_receives_full_document_body_once_and_can_cite_it(self):
        url='https://tenant.feishu.cn/docx/Mechanism';message=self.response()['items'][0]
        message['documents']=[{'url':url,'title':'机制表','content':'商品：舒敏霜50g','read_at':'2026-11-12T02:10:00Z'}]
        analysis=a.rules(q.docs.source(message),2026).model_dump(mode='json')
        answer={'has_live':True,'judgment':'文档中商品明确','events':[{'sender_id':'ou_author','message_ids':[message['id']],'analysis':analysis}]}
        with patch('api.services.expected_gmv.model_call',return_value={'content':json.dumps(answer)}) as model:
            result=q.extract([message],[],{**self.task,'engine':'deepseek'},2026)
        inputs=json.loads(model.call_args.args[1][1]['content'])
        self.assertEqual(inputs['document_contents'][0]['content'],'商品：舒敏霜50g')
        self.assertNotIn('content',inputs['messages'][0]['documents'][0])
        self.assertEqual(result['events'][0]['analysis']['products'],['舒敏霜50g'])

    def test_raw_chat_kept_even_when_sender_is_excluded_from_judgment(self):
        response=self.response()
        response['items'].append({**response['items'][0],'id':'om_other','sender_id':'ou_other','sender':'范围外人员','content':'商品：不应合并的商品'})
        with patch.object(q.im,'messages',return_value=response):q.scan_group(self.task,self.group)
        window=q.detail('window',self.rows(AgentWindow)[0].id)
        self.assertEqual(len(window['raw_messages']),2);self.assertEqual(len(window['messages']),1)
        self.assertFalse(next(x for x in window['raw_messages'] if x['id']=='om_other')['included_in_judgment'])
        self.assertEqual(self.rows(AgentTodo)[0].payload['missing'],['商品及规格'])

    def test_one_scan_cycle_freezes_cutoff_while_cli_calls_advance_clock(self):
        self.task.update(scan_start=None,scan_end=None);a.put('task',self.task)
        with self.sessions.begin() as db:db.add(AgentWindow(id='seed',task_id=self.task['id'],group_id='oc_group',start_at=datetime(2026,11,12,2,2),end_at=datetime(2026,11,12,2,7),status='completed',payload={'messages':[],'todo_ids':[]}))
        clock=[datetime(2026,11,12,2,10)]
        def read(request):
            clock[0]+=timedelta(seconds=1)
            return {'items':[],'has_more':False,'page_token':''}
        with patch.object(q,'utc',side_effect=lambda:clock[0]),patch.object(q.im,'messages',side_effect=read) as calls:q.scan_task(self.task)
        self.assertEqual(calls.call_count,1)
        generated=[row for row in self.rows(AgentWindow) if row.id!='seed']
        self.assertEqual(len(generated),1)
        self.assertEqual(generated[0].end_at-generated[0].start_at,timedelta(minutes=2))
        self.assertEqual(generated[0].end_at,datetime(2026,11,12,2,9))

    def test_short_continuous_tail_waits_and_is_merged_without_gap(self):
        self.task.update(scan_start=None,scan_end=None);a.put('task',self.task)
        ending=datetime(2026,11,12,2,8,59)
        with self.sessions.begin() as db:db.add(AgentWindow(id='seed',task_id=self.task['id'],group_id='oc_group',start_at=ending-timedelta(minutes=5),end_at=ending,status='completed',payload={'messages':[],'todo_ids':[]}))
        with patch.object(q.im,'messages') as read:
            self.assertFalse(q.scan_group(self.task,self.group));read.assert_not_called()
        with patch.object(q,'utc',return_value=datetime(2026,11,12,2,10,59)),patch.object(q.im,'messages',return_value={'items':[],'has_more':False,'page_token':''}):self.assertTrue(q.scan_group(self.task,self.group))
        generated=next(row for row in self.rows(AgentWindow) if row.id!='seed')
        self.assertEqual(generated.start_at,ending)
        self.assertEqual(generated.end_at-generated.start_at,timedelta(minutes=1))


if __name__=='__main__':unittest.main()
