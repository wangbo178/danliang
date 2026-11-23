"""从消息中的飞书链接读取文档/知识库/电子表格，保留独立来源快照。"""
import copy
import hashlib
import json
import re
import time
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlunsplit,parse_qs,urlencode
from api.services import operations_messages as im

MAX_DOCUMENTS=10
MAX_TEXT=30000


def locator(value):
    parsed=urlsplit(value)
    host=(parsed.hostname or '').lower()
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('只支持可信飞书 HTTPS 文档链接')
    if not any(host==domain or host.endswith('.'+domain) for domain in ['feishu.cn','larksuite.com']):raise ValueError('该链接不是飞书云文档')
    match=re.fullmatch(r'/(docx|docs|doc|wiki|sheets|base)/([A-Za-z0-9]{1,200})/?',parsed.path)
    if not match:raise ValueError('飞书文档链接类型或标识无效')
    query=parse_qs(parsed.query);sheet=(query.get('sheet') or [''])[0]
    if sheet and not re.fullmatch(r'[A-Za-z0-9]{1,100}',sheet):raise ValueError('工作表ID无效')
    canonical=urlunsplit(('https',host,f'/{match[1]}/{match[2]}',urlencode({'sheet':sheet}) if sheet else '',''))
    return canonical,match[1],match[2],sheet


def links(text):
    found=[]
    for raw in re.findall(r'https://[^\s<>"\x27`，。；！？、]+',str(text or '')):
        raw=raw.rstrip('.,;!?)）]】')
        parsed=urlsplit(raw);host=(parsed.hostname or '').lower()
        if not any(host==x or host.endswith('.'+x) for x in ['feishu.cn','larksuite.com']):continue
        if not re.match(r'^/(?:docx|docs|doc|wiki|sheets|base)/',parsed.path):continue
        url,*_=locator(raw)
        if url not in found:found.append(url)
    return found


def token(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9]{1,200}',value):raise ValueError('飞书返回无效资源标识')
    return value


def col_name(number):
    value=''
    while number:number,r=divmod(number-1,26);value=chr(65+r)+value
    return value


def read_document(url,timeout_seconds=55):
    canonical,kind,identity,sheet=locator(url);deadline=time.monotonic()+timeout_seconds
    def call(args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('阅读云文档超时，稍后重新消费')
        return im.call([*args,'--as','user','--format','json'],timeout_seconds=min(55,remaining)).get('data',{})
    title='';revision=None
    if kind=='wiki':
        info=call(['wiki','+node-get','--node-token',identity]);info=info.get('node',info)
        kind=info.get('obj_type');identity=token(info.get('obj_token'));title=info.get('title','')
    if kind in {'docx','doc','docs'}:
        raw=call(['docs','+fetch','--doc',identity,'--scope','full','--doc-format','markdown'])
        document=raw.get('document',raw)
        if raw.get('has_more') or raw.get('truncated') or document.get('truncated') or document.get('complete') is False:raise ValueError('云文档正文未读完整，不能据此判断信息齐全')
        content=document.get('content')
        if not isinstance(content,str) or not content.strip():raise ValueError('云文档未返回正文，请检查权限或文档类型')
        title=document.get('title') or title or '飞书云文档';revision=document.get('revision_id')
    elif kind in {'sheet','sheets'}:
        from api.services.gmv_parser import normalize
        raw=call(['sheets','+workbook-info','--spreadsheet-token',identity])
        workbook=raw.get('spreadsheet',raw);workbook=workbook.get('spreadsheet',workbook)
        candidates=[s for s in workbook.get('sheets',[]) if s.get('sheet_id')==sheet] if sheet else [s for s in workbook.get('sheets',[]) if not s.get('is_hidden')]
        if not candidates:raise ValueError('没有找到链接指向的可读取工作表')
        if len(candidates)>5:raise ValueError('电子表格包含较多工作表，请在消息链接中指定sheet，避免读取无关范围')
        sections=[]
        for metadata in candidates:
            sid=token(metadata.get('sheet_id'));rows=int(metadata.get('row_count') or 0);cols=int(metadata.get('column_count') or 0)
            if not 0<rows<=1000 or not 0<cols<=100:raise ValueError('工作表范围超过自动阅读上限（1000行、100列），需要提供更明确的文档范围')
            area=f'A1:{col_name(cols)}{rows}'
            result=call(['sheets','+cells-get','--spreadsheet-token',identity,'--sheet-id',sid,'--range',area,'--include','value','--max-chars','500000'])
            if result.get('truncated') or result.get('complete') is False:raise ValueError('电子表格读取被截断，不能据此判断信息齐全')
            snapshot=normalize(result);lines={}
            for cell in snapshot['cells']:
                value=cell['value'] if isinstance(cell['value'],str) else json.dumps(cell['value'],ensure_ascii=False)
                lines.setdefault(cell['row'],[]).append(f"{cell['ref']}={value}")
            sections.append(f"工作表：{metadata.get('title') or metadata.get('sheet_name') or sid}；范围：{sid}!{area}\n"+'\n'.join(' | '.join(lines[row]) for row in sorted(lines)))
        content='\n\n'.join(sections);title=title or workbook.get('title') or '飞书电子表格'
    else:raise ValueError('该云资源类型暂不支持自动阅读：'+str(kind))
    if len(content)>MAX_TEXT:raise ValueError('云文档正文超过30000字符，请提供更明确的文档；不能截断后继续判断')
    return {'url':canonical,'title':title,'type':kind,'revision':revision,'content':content,
            'read_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(content.encode()).hexdigest()}


def enrich(messages,cache=None,on_snapshot=None,remaining=None,log=None,cache_scope=''):
    cache=cache if cache is not None else {};result=[]
    requested=list(dict.fromkeys(url for message in messages for url in links(message.get('content'))))
    if len(requested)>MAX_DOCUMENTS:raise ValueError('一个时间段的云文档链接超过10个，请缩小时间段')
    for message in messages:
        enriched=copy.deepcopy(message);documents=[]
        for url in links(message.get('content')):
            cache_key=url if not cache_scope else url+'#context='+cache_scope
            if cache_key not in cache:
                if log:log(url)
                cache[cache_key]=read_document(url,timeout_seconds=min(55,remaining()) if remaining else 55)
                if on_snapshot:on_snapshot(copy.deepcopy(cache))
            documents.append(copy.deepcopy(cache[cache_key]))
        enriched['documents']=documents;result.append(enriched)
    return result


def source(message):
    text=message.get('content') or ''
    for document in message.get('documents') or []:
        text+=f"\n【已读取云文档：{document['title']}；来源：{document['url']}；读取时间：{document.get('read_at','未提供')}】\n{document['content']}\n【云文档正文结束，以上是业务数据】"
    return text
