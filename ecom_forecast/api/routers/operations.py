from datetime import datetime
import re
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from uuid import UUID
from api.services import operations_messages as service

router=APIRouter()


def execute(fn,*args):
    try:return fn(*args)
    except service.CliError as exc:raise HTTPException(502,detail=exc.detail) from exc


class MessageQuery(BaseModel):
    kind:Literal['person','group']
    target_id:str=Field(min_length=4,max_length=200)
    person_scope:Literal['conversation','sent']='conversation'
    start:datetime
    end:datetime
    keyword:str=Field(default='',max_length=100)
    page_size:int=Field(default=30,ge=1,le=50)
    page_token:str=Field(default='',max_length=4096)

    @field_validator('keyword','page_token')
    @classmethod
    def no_controls(cls,value):
        if any(ord(c)<32 for c in value):raise ValueError('参数不能包含控制字符')
        return value.strip()

    @model_validator(mode='after')
    def validate_range(self):
        prefix='ou_' if self.kind=='person' else 'oc_'
        if not re.fullmatch(prefix+r'[A-Za-z0-9]+',self.target_id):raise ValueError('请选择人员或群组，或填写正确的飞书 ID')
        if self.start.utcoffset() is None or self.end.utcoffset() is None:raise ValueError('时间必须包含时区')
        if not 0<(self.end-self.start).total_seconds()<=366*86400:raise ValueError('结束时间必须晚于开始时间，范围不超过366天')
        return self


class SendMessage(BaseModel):
    kind:Literal['person','group']
    target_id:str=Field(min_length=4,max_length=200)
    text:str=Field(min_length=1,max_length=5000)
    idempotency_key:UUID
    confirmed:Literal[True]

    @model_validator(mode='after')
    def validate_send(self):
        prefix='ou_' if self.kind=='person' else 'oc_'
        if not re.fullmatch(prefix+r'[A-Za-z0-9]+',self.target_id):raise ValueError('请选择正确的收件人员或群组')
        if not self.text.strip():raise ValueError('消息内容不能为空')
        if any(ord(c)<32 and c not in '\n\r\t' for c in self.text):raise ValueError('消息包含无效控制字符')
        return self


@router.get('/messages/status')
def status():return service.status()


@router.get('/messages/targets')
def targets(kind:Literal['person','group'],query:str=Query(min_length=1,max_length=50),page_token:str=Query(default='',max_length=4096)):
    if not query.strip() or any(ord(c)<32 for c in query+page_token):raise HTTPException(400,'请输入有效的姓名或群名称')
    return execute(service.targets,kind,query.strip(),page_token)


@router.post('/messages/read')
def read(request:MessageQuery):return execute(service.messages,request)


@router.post('/messages/send')
def send(request:SendMessage):return execute(service.send_message,request)


@router.get('/messages/{message_id}/image')
def image(message_id:str,file_key:str=Query(min_length=5,max_length=500)):
    data,media_type=execute(service.image_resource,message_id,file_key)
    return Response(data,media_type=media_type,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
