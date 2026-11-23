"""运营消息读取、图片预览及用户确认后的消息发送。"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


class CliError(Exception):
    def __init__(self, detail):
        self.detail=detail
        super().__init__(detail['message'])


def executable():
    native=shutil.which('lark-cli.exe')
    if native:return native
    launcher=shutil.which('lark-cli.cmd') or shutil.which('lark-cli')
    if launcher:
        # npm 的 Windows 启动器是批处理；直接执行其原生程序，用户参数不经过 cmd。
        binary=Path(launcher).parent/'node_modules'/'@larksuite'/'cli'/'bin'/'lark-cli.exe'
        if binary.is_file():return str(binary)
        if os.name!='nt':return launcher
    raise CliError({'message':'未找到飞书 CLI 原生程序，请安装并登录 lark-cli。','kind':'cli_unavailable'})


READ_COMMANDS={('auth','status'),('contact','+search-user'),('im','+chat-search'),
               ('im','+chat-messages-list'),('im','+messages-search'),('im','+messages-resources-download'),
               ('docs','+fetch'),('wiki','+node-get'),('sheets','+workbook-info'),('sheets','+cells-get')}


def call(args,cwd=None,allow_send=False,timeout_seconds=55):
    if tuple(args[:2]) not in READ_COMMANDS and not (allow_send is True and tuple(args[:2])==('im','+messages-send')):
        raise ValueError('运营模块不支持该命令，发送消息必须通过专用入口确认')
    try:
        result=subprocess.run([executable(),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',
                              timeout=max(1,min(55,timeout_seconds)),cwd=cwd,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                              env={**os.environ,'LARKSUITE_CLI_NO_UPDATE_NOTIFIER':'1','LARKSUITE_CLI_NO_SKILLS_NOTIFIER':'1'})
    except subprocess.TimeoutExpired:
        raise CliError({'message':'发送超时，结果尚未确认，请先读取会话核实；重试会沿用本次发送标识。' if allow_send else '飞书 CLI 读取超时，请缩小时间范围或稍后重试。','kind':'timeout'})
    except OSError:
        raise CliError({'message':'飞书 CLI 无法启动，请检查本机安装。','kind':'cli_unavailable'})
    payload=None
    for stream in [result.stdout,result.stderr]:
        try:
            value=json.loads(stream)
            if isinstance(value,dict):payload=value;break
        except (ValueError,TypeError):pass
    if payload is None:
        raise CliError({'message':'飞书 CLI 未返回有效 JSON，请检查 CLI 安装与登录状态。','kind':'invalid_response'})
    if result.returncode or payload.get('ok') is False:
        error=payload.get('error',{})
        if not isinstance(error,dict):error={}
        raise CliError({'message':str(error.get('message') or '飞书消息操作失败')[:600],
                        'hint':str(error.get('hint') or '')[:600], 'kind':error.get('type','cli_error'),
                        'code':error.get('code'),'missing_scopes':error.get('missing_scopes',[])})
    return payload


def send_message(request):
    if request.confirmed is not True:
        raise CliError({'message':'请先确认收件对象和消息内容','kind':'confirmation_required'})
    payload=call(['im','+messages-send','--as','user',
                  '--user-id' if request.kind=='person' else '--chat-id',request.target_id,
                  '--text',request.text,'--idempotency-key',str(request.idempotency_key),'--format','json'],allow_send=True,timeout_seconds=getattr(request,'timeout_seconds',55))
    data=payload.get('data') or {}
    message_id=data.get('message_id')
    if not message_id:
        raise CliError({'message':'飞书未返回消息 ID，发送结果尚未确认，请先读取会话核实。','kind':'unconfirmed_result'})
    return {'message_id':message_id,'chat_id':data.get('chat_id'),'create_time':data.get('create_time'),
            'target_id':request.target_id,'identity':payload.get('identity','user'),
            'idempotency_key':str(request.idempotency_key)}


def status():
    try:
        payload=call(['auth','status','--json','--verify'])
        value=payload.get('data',payload);user=value.get('identities',{}).get('user',{})
        if value.get('verified') is False or not user or user.get('status') in {'not_logged_in','expired','invalid'}:
            return {'available':False,'message':'当前飞书用户尚未登录或授权已失效，请在本机完成飞书 CLI 用户登录。','kind':'authorization'}
        return {'available':True,'identity':'user','verified':value.get('verified'),
                'user_name':user.get('userName'),'user_status':user.get('status'),
                'token_status':user.get('tokenStatus'),'message':'通过本机飞书 CLI 读取当前登录用户有权访问的消息。'}
    except CliError as exc:
        return {'available':False,**exc.detail}


def page_info(payload):
    data=payload.get('data',{});pagination=payload.get('meta',{}).get('pagination',{})
    token=data.get('page_token') or pagination.get('next_token') or ''
    has_more=bool(data.get('has_more') or pagination.get('complete') is False)
    return {'has_more':has_more,'page_token':token,
            'pagination_warning':'飞书返回还有更多结果，但未提供下一页游标，请缩小筛选范围。' if has_more and not token else None}


def targets(kind,query,page_token=''):
    args=['contact','+search-user','--query',query,'--page-size','20'] if kind=='person' else [
        'im','+chat-search','--query',query,'--disable-search-by-user','--page-size','20']
    if kind=='group' and page_token:args+=['--page-token',page_token]
    payload=call([*args,'--as','user','--format','json']);data=payload.get('data',{})
    candidates=(data.get('users') if kind=='person' else data.get('chats')) or []
    items=[]
    for item in candidates:
        identity=item.get('open_id') if kind=='person' else item.get('chat_id')
        if not identity:continue
        items.append({'id':identity,'name':item.get('localized_name') or item.get('name') or identity,
                      'description':item.get('department','') if kind=='person' else item.get('description','')})
    return {'items':items,**page_info(payload),'refine_search':kind=='person' and bool(data.get('has_more'))}


def content_text(value):
    if not isinstance(value,str):return json.dumps(value,ensure_ascii=False,indent=2)
    try:
        parsed=json.loads(value)
        if isinstance(parsed,dict) and isinstance(parsed.get('text'),str):return parsed['text']
    except ValueError:pass
    return value


def image_keys(message_type,content):
    if message_type not in {'image','post','interactive'}:return []
    text=content if isinstance(content,str) else json.dumps(content,ensure_ascii=False)
    return list(dict.fromkeys(re.findall(r'\bimg_[A-Za-z0-9_-]+',text)))


def image_resource(message_id,file_key):
    if not re.fullmatch(r'om_[A-Za-z0-9_-]{1,196}',message_id) or not re.fullmatch(r'img_[A-Za-z0-9_-]{1,496}',file_key):
        raise CliError({'message':'图片资源标识无效','kind':'invalid_argument'})
    with TemporaryDirectory(prefix='ofs-feishu-image-') as folder:
        call(['im','+messages-resources-download','--as','user','--message-id',message_id,
              '--file-key',file_key,'--type','image','--output','image.bin','--format','json'],cwd=folder)
        path=Path(folder)/'image.bin'
        if not path.is_file() or path.resolve().parent!=Path(folder).resolve():
            raise CliError({'message':'飞书 CLI 未生成图片文件','kind':'invalid_resource'})
        if path.stat().st_size>20*1024*1024:
            raise CliError({'message':'图片超过20MB，暂不支持在线预览','kind':'resource_too_large'})
        data=path.read_bytes()
    media_type=None
    if data.startswith(b'\x89PNG\r\n\x1a\n'):media_type='image/png'
    elif data.startswith(b'\xff\xd8\xff'):media_type='image/jpeg'
    elif data.startswith((b'GIF87a',b'GIF89a')):media_type='image/gif'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':media_type='image/webp'
    elif data.startswith(b'BM'):media_type='image/bmp'
    elif data[4:12] in {b'ftypavif',b'ftypavis'}:media_type='image/avif'
    if not media_type:
        raise CliError({'message':'该资源不是支持预览的图片格式','kind':'unsupported_image'})
    return data,media_type


def messages(request):
    search=bool(request.keyword) or (request.kind=='person' and request.person_scope=='sent')
    if search:
        args=['im','+messages-search']
        args+=['--sender',request.target_id] if request.kind=='person' else ['--chat-id',request.target_id]
        if request.kind=='person' and request.person_scope=='conversation':
            # 私聊关键词仍先按会话读取，客户端筛选当前页；不扩张到其它群组。
            search=False
    if not search:
        args=['im','+chat-messages-list','--user-id' if request.kind=='person' else '--chat-id',request.target_id,'--order','desc']
    elif request.keyword:
        args+=['--query',request.keyword]
    args+=['--as','user','--start',request.start.isoformat(),'--end',request.end.isoformat(),
           '--page-size',str(request.page_size),'--no-reactions','--format','json']
    if request.page_token:args+=['--page-token',request.page_token]
    payload=call(args,timeout_seconds=getattr(request,'timeout_seconds',55));data=payload.get('data',{});items=[]
    for message in data.get('messages') or []:
        sender=message.get('sender') or {}
        if not isinstance(sender,dict):sender={'name':str(sender)}
        body=message.get('body') or {}
        raw=message.get('content',body.get('content','') if isinstance(body,dict) else '')
        text=content_text(raw)
        if request.keyword and not search and request.keyword.casefold() not in text.casefold():continue
        sender_id=sender.get('id') or sender.get('open_id') or ''
        if isinstance(sender_id,dict):sender_id=sender_id.get('open_id') or sender_id.get('user_id') or ''
        message_type=message.get('msg_type') or message.get('message_type')
        items.append({'id':message.get('message_id'),'type':message_type,
                      'sender':sender.get('name') or sender_id or '未知发送者','sender_id':sender_id,
                      'time':message.get('create_time'),'content':text,'raw_content':raw,
                      'deleted':bool(message.get('deleted')),'updated':bool(message.get('updated')),
                      'images':image_keys(message_type,raw) if not message.get('deleted') else [],
                      'chat_id':message.get('chat_id'),'thread_id':message.get('thread_id')})
    return {'items':items,**page_info(payload),'identity':payload.get('identity','user'),
            'filter_note':'私聊关键词只筛选当前已读取页；可继续加载下一页。' if request.keyword and not search else '',
            'target_id':request.target_id,'start':request.start.isoformat(),'end':request.end.isoformat()}
