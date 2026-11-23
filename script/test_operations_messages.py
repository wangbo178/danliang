import json
import sys
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ecom_forecast'))
from api.services import operations_messages as s
from api.routers.operations import MessageQuery,SendMessage


class OperationsMessagesTests(unittest.TestCase):
    def query(self,**kwargs):
        return MessageQuery(**{'kind':'person','target_id':'ou_test','start':'2026-11-10T00:00:00+08:00','end':'2026-11-11T00:00:00+08:00',**kwargs})

    def test_validate_ids_dates_and_controls(self):
        for args in [{'target_id':'oc_wrong'},{'target_id':'ou_test & echo bad'},
                     {'start':'2026-11-10T00:00:00'}, {'start':'2026-11-11T00:00:00+08:00'},
                     {'page_size':51},{'keyword':'a\nnew flag'},{'page_token':'a\0b'}]:
            with self.assertRaises(ValidationError):self.query(**args)

    def test_person_conversation_uses_user_id_with_pagination_and_normalizes(self):
        payload={'ok':True,'identity':'user','data':{'messages':[{'message_id':'om_1','msg_type':'text','content':'{"text":"hello <script>"}',
                  'create_time':'1794312000000','sender':{'name':'发送者','id':'ou_sender'},'updated':True}], 'has_more':True,'page_token':'next'}}
        with patch.object(s,'call',return_value=payload) as call:
            result=s.messages(self.query(page_token='cursor'))
        args=call.call_args.args[0]
        self.assertEqual(args[:4],['im','+chat-messages-list','--user-id','ou_test'])
        self.assertIn('--no-reactions',args)
        self.assertEqual(args[args.index('--as')+1],'user')
        self.assertEqual(args[args.index('--page-token')+1],'cursor')
        self.assertEqual(result['items'][0]['content'],'hello <script>')
        self.assertEqual(result['page_token'],'next')

    def test_group_keyword_and_person_sender_search(self):
        with patch.object(s,'call',return_value={'data':{'messages':[]}}) as call:
            s.messages(self.query(kind='group',target_id='oc_group',keyword='进度'))
            args=call.call_args.args[0]
            self.assertEqual(args[:4],['im','+messages-search','--chat-id','oc_group'])
            self.assertEqual(args[args.index('--query')+1],'进度')
            s.messages(self.query(person_scope='sent'))
            self.assertEqual(call.call_args.args[0][:4],['im','+messages-search','--sender','ou_test'])

    def test_private_keyword_stays_in_private_chat_and_paging_survives_empty_page(self):
        with patch.object(s,'call',return_value={'data':{'messages':[{'content':'别的消息','body':None}], 'has_more':True,'page_token':'next'}}) as call:
            result=s.messages(self.query(keyword='进度'))
        self.assertEqual(call.call_args.args[0][1],'+chat-messages-list')
        self.assertEqual(result['items'],[])
        self.assertTrue(result['has_more'])
        self.assertTrue(result['filter_note'])

    def test_null_search_results_and_group_pagination(self):
        with patch.object(s,'call',return_value={'data':{'chats':None,'has_more':False}}):
            self.assertEqual(s.targets('group','不匹配')['items'],[])
        with patch.object(s,'call',return_value={'data':{'chats':[{'chat_id':'oc_g','name':'群名'}],'has_more':True,'page_token':'next'}}) as call:
            result=s.targets('group','群','cursor')
        self.assertEqual(result['items'][0]['id'],'oc_g')
        self.assertIn('--disable-search-by-user',call.call_args.args[0])
        self.assertEqual(result['page_token'],'next')

    def test_cli_stderr_error_details_and_timeout_without_shell(self):
        error={'ok':False,'error':{'type':'authorization','message':'权限不足','hint':'请授权','missing_scopes':['im:message:readonly']}}
        completed=SimpleNamespace(returncode=1,stdout='',stderr=json.dumps(error))
        with patch.object(s,'executable',return_value='lark-cli.exe'),patch.object(s.subprocess,'run',return_value=completed) as run:
            with self.assertRaises(s.CliError) as raised:s.call(['im','+chat-search','--query','a&b'])
        self.assertEqual(raised.exception.detail['missing_scopes'],['im:message:readonly'])
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][-1],'a&b')
        with self.assertRaises(ValueError):s.call(['im','+messages-send'])
        with patch.object(s,'executable',return_value='lark-cli.exe'),patch.object(s.subprocess,'run',side_effect=subprocess.TimeoutExpired('cli',55)):
            with self.assertRaises(s.CliError) as timeout:s.call(['im','+chat-search'])
        self.assertEqual(timeout.exception.detail['kind'],'timeout')

    def test_status_does_not_return_secrets(self):
        with patch.object(s,'call',return_value={'verified':True,'identities':{'user':{'status':'ready','userName':'测试','accessToken':'secret'}}}):
            value=s.status()
        self.assertTrue(value['available'])
        self.assertNotIn('secret',json.dumps(value))

    def test_extract_image_and_rich_text_resources_without_duplicates(self):
        self.assertEqual(s.image_keys('image','{"image_key":"img_v3_abc-123"}'),['img_v3_abc-123'])
        self.assertEqual(s.image_keys('post','![图片](img_first) ![图片](img_second) ![重复](img_first)'),['img_first','img_second'])
        self.assertEqual(s.image_keys('text','文字中的 img_not_an_image'),[])

    def test_image_download_is_temporary_and_returns_sniffed_content_type(self):
        png=b'\x89PNG\r\n\x1a\n' + b'image content'
        folders=[]
        def download(args,cwd=None):
            folders.append(Path(cwd))
            self.assertEqual(args[:2],['im','+messages-resources-download'])
            self.assertIn('--as',args)
            self.assertEqual(args[args.index('--type')+1],'image')
            self.assertEqual(args[args.index('--output')+1],'image.bin')
            (Path(cwd)/'image.bin').write_bytes(png)
            return {'ok':True}
        with patch.object(s,'call',side_effect=download):
            data,mime=s.image_resource('om_test','img_v3_test')
        self.assertEqual((data,mime),(png,'image/png'))
        self.assertFalse(folders[0].exists())
        for message,key in [('om_test','../image'),('../message','img_test')]:
            with self.assertRaises(s.CliError):s.image_resource(message,key)

    def test_reject_non_image_response(self):
        def download(args,cwd=None):
            (Path(cwd)/'image.bin').write_text('<html>error</html>')
        with patch.object(s,'call',side_effect=download):
            with self.assertRaises(s.CliError) as raised:s.image_resource('om_test','img_test')
        self.assertEqual(raised.exception.detail['kind'],'unsupported_image')

    def test_send_requires_confirmation_valid_target_and_nonempty_text(self):
        valid={'kind':'person','target_id':'ou_test','text':'消息','idempotency_key':str(uuid4()),'confirmed':True}
        for change in [{'confirmed':False},{'text':'  '},{'text':'a\0b'},{'target_id':'oc_wrong'},{'idempotency_key':'invalid'}]:
            with self.assertRaises(ValidationError):SendMessage(**{**valid,**change})
        with self.assertRaises(ValidationError):SendMessage(**{k:v for k,v in valid.items() if k!='confirmed'})
        with self.assertRaises(s.CliError),patch.object(s,'call') as call:
            s.send_message(SimpleNamespace(confirmed=False))
        call.assert_not_called()

    def test_send_exact_text_with_fixed_user_identity_and_stable_idempotency(self):
        text='第一行\n  第二行 "quote" & $literal'
        request=SendMessage(kind='person',target_id='ou_test',text=text,idempotency_key=uuid4(),confirmed=True)
        payload={'ok':True,'identity':'user','data':{'message_id':'om_sent','chat_id':'oc_chat','create_time':'100'}}
        with patch.object(s,'call',return_value=payload) as call:
            result=s.send_message(request);s.send_message(request)
        args=call.call_args.args[0]
        self.assertEqual(args[:4],['im','+messages-send','--as','user'])
        self.assertEqual(args[args.index('--text')+1],text)
        self.assertEqual(args[args.index('--user-id')+1],'ou_test')
        self.assertEqual(args[args.index('--idempotency-key')+1],str(request.idempotency_key))
        self.assertEqual(call.call_args_list[0],call.call_args_list[1])
        self.assertTrue(call.call_args.kwargs['allow_send'])
        self.assertEqual(result['message_id'],'om_sent')
        request=SendMessage(kind='group',target_id='oc_group',text=text,idempotency_key=uuid4(),confirmed=True)
        with patch.object(s,'call',return_value=payload) as call:s.send_message(request)
        self.assertIn('--chat-id',call.call_args.args[0])
        self.assertNotIn('--user-id',call.call_args.args[0])

    def test_missing_send_receipt_is_not_reported_as_success(self):
        request=SendMessage(kind='person',target_id='ou_test',text='消息',idempotency_key=uuid4(),confirmed=True)
        with patch.object(s,'call',return_value={'ok':True,'data':{}}):
            with self.assertRaises(s.CliError) as raised:s.send_message(request)
        self.assertEqual(raised.exception.detail['kind'],'unconfirmed_result')


if __name__=='__main__':unittest.main()
