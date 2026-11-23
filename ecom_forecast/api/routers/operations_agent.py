import re
from datetime import datetime
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,model_validator,ConfigDict
from api.services import operations_agent as service
from api.routers.operations import execute

router=APIRouter()


class Target(BaseModel):
    id:str=Field(max_length=200)
    name:str=Field(min_length=1,max_length=150)


class Task(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    groups:list[Target]=Field(min_length=1,max_length=10)
    senders:list[Target]=Field(min_length=1,max_length=20)
    interval_seconds:int=Field(default=60,ge=30,le=3600)
    lookback_hours:int=Field(default=24,ge=1,le=168)
    engine:Literal['rules','deepseek','minimax']='deepseek'
    followup_mode:Literal['draft','auto']='draft'
    shop_id:str=Field(default='',max_length=64)
    schedule_channel:str=Field(default='达播',min_length=1,max_length=64)
    window_minutes:int=Field(default=5,ge=1,le=60)
    scan_start:datetime|None=None
    scan_end:datetime|None=None

    @model_validator(mode='after')
    def targets_valid(self):
        if (self.scan_start is None)!=(self.scan_end is None):raise ValueError('扫描时间范围需同时指定开始和结束')
        if self.scan_start:
            if self.scan_start.utcoffset() is None or self.scan_end.utcoffset() is None or not 0<(self.scan_end-self.scan_start).total_seconds()<=168*3600:raise ValueError('扫描范围需带时区且不超过7天')
        for collection,prefix in [(self.groups,'oc_'),(self.senders,'ou_')]:
            if any(not re.fullmatch(prefix+r'[A-Za-z0-9]+',item.id) for item in collection):raise ValueError('请选择有效的群组和人员 ID')
            if len({item.id for item in collection})!=len(collection):raise ValueError('不能重复选择同一群组或人员')
        return self


class Followup(BaseModel):
    confirmed:Literal[True]
    question_text:str=Field(max_length=5000)


class Review(BaseModel):
    analysis:service.Analysis|None=None
    ignore:bool=False


class Preview(BaseModel):
    text:str=Field(min_length=1,max_length=16000)
    year:int=Field(default=2026,ge=2000,le=2100)
    engine:Literal['rules','deepseek','minimax']='rules'


def run(fn,*args):
    try:return execute(fn,*args)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@router.get('/state')
def state(window_page:int=Query(default=1,ge=1),window_page_size:int=Query(default=20,ge=1,le=100)):
    return run(service.state,window_page,window_page_size)


@router.post('/tasks')
def create(request:Task):return run(service.save_task,request.model_dump(mode='json'))


@router.put('/tasks/{identity}')
def update(identity:str,request:Task):return run(service.save_task,request.model_dump(mode='json'),identity)


@router.post('/tasks/{identity}/{operation}')
def action(identity:str,operation:Literal['start','stop','once']):return run(service.action,identity,operation)


@router.post('/opportunities/{identity}/followup')
def followup(identity:str,request:Followup):
    from api.services.operations_queue import approve
    return run(approve,identity,request.question_text)


@router.post('/queue/{identity}/retry')
def retry(identity:str):
    from api.services.operations_queue import retry_node
    return run(retry_node,identity)


@router.get('/windows/{identity}')
def window_detail(identity:str):
    from api.services.operations_queue import detail
    return run(detail,'window',identity)


@router.get('/queue/{identity}')
def node_detail(identity:str):
    from api.services.operations_queue import detail
    return run(detail,'node',identity)


@router.patch('/opportunities/{identity}')
def review(identity:str,request:Review):return run(service.update_opportunity,identity,request.analysis.model_dump(mode='json') if request.analysis else None,request.ignore)


@router.post('/preview')
def preview(request:Preview):
    from api.services import operations_documents as documents
    enriched=run(documents.enrich,[{'id':'preview','content':request.text}])
    analysis=run(service.analyze,documents.source(enriched[0]),request.year,request.engine,'preview')
    return {'analysis':analysis,'missing':service.missing_fields(analysis),
            'document_sources':[{k:v for k,v in item.items() if k!='content'} for item in enriched[0]['documents']]}
