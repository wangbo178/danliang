from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from typing import Literal, Annotated
from api.services.gmv_source_months import resolve_months
from api.services import expected_gmv as service

router = APIRouter()


class Source(BaseModel):
    shop: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    year: int = Field(ge=2000, le=2100)
    range: str = 'A1:CZ140'
    rule_key: str = Field(default='tmall', pattern=r'^[a-z][a-z0-9_]{0,39}$')
    parser: str = Field(default='tmall', pattern=r'^(tmall|douyin|douyin_baby|vip)$')
    amount_mode: Literal['source_conversion', 'source_value'] = 'source_conversion'
    history_enabled: bool = True
    months: list[Annotated[int, Field(strict=True, ge=1, le=12)]] | None = Field(default=None, min_length=1)


class Repair(BaseModel):
    provider: str
    reviewer: str


class Provider(BaseModel):
    model: str = ''
    api_key: str = Field(default='', max_length=4096)
    region: str | None = None


def execute(fn, *args):
    try:
        with service.read_batch():
            service.init()
            return fn(*args)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('')
def overview(compact: bool = False):
    with service.read_batch():
        service.init()
        serialize = service.run_summary if compact else service.public_run
        return {'sources': service.all_items('source'), 'runs': [serialize(r) for r in service.all_items('run')], 'rules': service.all_items('rule'), 'active': {r['rule_key']: service.active(r['rule_key'])['id'] for r in service.all_items('rule')}, 'tasks': service.all_items('task'), 'models': service.config()}


@router.get('/order-preview')
def order_preview(month: str | None = None,start_date: str | None = None,end_date: str | None = None):
    from api.services.gmv_order_preview import preview
    return execute(preview,month,start_date,end_date)


@router.get('/order-preview/warehouse')
def order_warehouse_preview(month: str | None = None,start_date: str | None = None,end_date: str | None = None):
    from api.services.gmv_warehouse_allocation import allocation
    return execute(allocation,month,start_date,end_date)


@router.get('/runs/{run_id}')
def run_detail(run_id: str):
    return execute(lambda: service.public_run(service.get('run', run_id)))


@router.get('/runs/{run_id}/forecast-inputs')
def forecast_inputs(run_id: str, version: str | None = None):
    from api.services.gmv_forecast_inputs import read
    return execute(read, run_id, version)


@router.put('/runs/{run_id}/forecast-inputs')
def save_forecast_inputs(run_id: str, payload: dict):
    from api.services.gmv_forecast_inputs import save
    return execute(save, run_id, payload)


@router.get('/runs/{run_id}/history-reference')
def history_reference(run_id: str, target_date: str):
    from api.services.gmv_history_reference import reference
    return execute(reference, run_id, target_date)


@router.get('/runs/{run_id}/history-reference/averages')
def history_reference_averages(run_id: str):
    from api.services.gmv_history_reference import reference_averages
    return execute(reference_averages, run_id)


@router.get('/runs/{run_id}/forecast-order-floor')
def forecast_order_floor(run_id: str, month: str):
    from api.services.gmv_history_reference import previous_month_minimum
    return execute(previous_month_minimum, run_id, month)


@router.get('/runs/{run_id}/forecast-cps-averages')
def forecast_cps_averages(run_id: str, months: str):
    from api.services.gmv_history_reference import cps_monthly_averages
    return execute(cps_monthly_averages, run_id, months.split(','))


@router.get('/runs/{run_id}/forecast-sampling-averages')
def forecast_sampling_averages(run_id: str, months: str):
    from api.services.gmv_history_reference import sampling_monthly_averages
    return execute(sampling_monthly_averages, run_id, months.split(','))


@router.get('/runs/{run_id}/forecast-alliance')
def forecast_alliance(run_id: str, months: str):
    from api.services.gmv_alliance_forecast import preview
    return execute(preview, run_id, months.split(','))


@router.get('/runs/{run_id}/forecast-baby-aov')
def forecast_baby_aov(run_id: str, months: str):
    from api.services.gmv_baby_aov_forecast import preview
    return execute(preview, run_id, months.split(','))


@router.get('/runs/{run_id}/forecast-special-shelf')
def forecast_special_shelf(run_id: str, start_date: str | None = None, end_date: str | None = None, scope: str = 'since_2024_10'):
    from api.services.gmv_special_shelf_forecast import preview
    return execute(preview, run_id, start_date, end_date, scope)


@router.get('/runs/{run_id}/history-reference-two')
def history_reference_two(run_id: str, dates: str, scope: str = 'since_2024_10'):
    from api.services.gmv_special_shelf_forecast import history_two
    return execute(history_two, run_id, dates.split(','), scope)


@router.get('/runs/{run_id}/forecast-super-anchor')
def forecast_super_anchor(run_id: str, mode: str = 'tail_only'):
    from api.services.gmv_super_anchor_forecast import preview
    return execute(preview, run_id, mode)


@router.get('/runs/{run_id}/forecast-second-anchor')
def forecast_second_anchor(run_id: str, mode: str = 'tail_only'):
    from api.services.gmv_super_anchor_forecast import preview
    return execute(preview, run_id, mode, '达播-二梯队')


@router.get('/runs/{run_id}/forecast-sample-festival')
def forecast_sample_festival(run_id: str, nearby_days: int = 1):
    from api.services.gmv_sample_festival import preview
    return execute(preview, run_id, nearby_days)


@router.post('/sources')
def create_source(payload: Source):
    def create():
        value = payload.model_dump()
        value['months'] = resolve_months(value)
        service.validate_source(value)
        if not any(r['rule_key'] == value['rule_key'] for r in service.all_items('rule')):
            template = (service.RULES / f'{value["parser"]}.py').read_text(encoding='utf-8')
            service.activate(value['rule_key'], template, '新店铺初始规则', None)
        if service.load_rule(service.active(value['rule_key'])['code']).get('parser', 'tmall') != value['parser']:
            raise ValueError('来源解析方式与规则标识不匹配')
        value['id'] = service.uuid.uuid4().hex
        service.put('source', value['id'], value)
        return value
    return execute(create)


@router.post('/imports/counter')
async def import_counter(file: UploadFile = File(...)):
    payload = await file.read(10 * 1024 * 1024 + 1)
    return execute(service.import_counter_excel, payload, file.filename or '')


@router.post('/manual/tmall-baby')
def save_tmall_baby(payload: dict):
    return execute(service.save_tmall_baby, payload)


@router.post('/manual/private-channel')
def save_private_channel(payload: dict):
    return execute(service.save_private_channel, payload)


@router.post('/manual/other')
def save_other_channel(payload: dict):
    return execute(service.save_other_channel, payload)


@router.post('/manual/ali-health')
def save_ali_health_channel(payload: dict):
    return execute(service.save_ali_health_channel, payload)


@router.post('/manual/pdd')
def save_pdd_channel(payload: dict):
    return execute(service.save_pdd_channel, payload)


@router.post('/manual/ka-channel')
def save_ka_channel(payload: dict):
    return execute(service.save_ka_channel, payload)


@router.put('/sources/{source_id}')
def update_source(source_id: str, payload: Source):
    def update():
        previous = service.get('source', source_id)
        value = payload.model_dump()
        value['months'] = resolve_months(value)
        if value['rule_key'] != previous['rule_key']:
            raise ValueError('不能改变既有来源的规则归属；请新增来源')
        if value['parser'] != previous.get('parser', 'tmall'):
            raise ValueError('不能改变既有来源的解析方式；请新增来源')
        service.validate_source(value)
        value['id'] = source_id
        service.put('source', source_id, value)
        return value
    return execute(update)


@router.post('/sources/{source_id}/parse')
def parse_source(source_id: str):
    return execute(service.run_parse, source_id)


@router.post('/runs/{run_id}/repair')
def repair_rule(run_id: str, payload: Repair):
    return execute(service.repair, run_id, payload.provider, payload.reviewer)


@router.get('/tasks/{task_id}')
def task_status(task_id: str):
    return execute(service.get, 'task', task_id)


@router.get('/runs/{run_id}/snapshot')
def snapshot(run_id: str):
    return execute(lambda: service.get('run', run_id).get('snapshot', {}))


@router.get('/runs/{run_id}/shelf-actuals')
@router.get('/runs/{run_id}/actuals')
def shelf_actuals(run_id: str):
    from api.services.gmv_actuals import store_actuals as query
    return execute(lambda: query(service.get('run', run_id)))


@router.post('/rules/{version}/rollback')
def rollback(version: str):
    return execute(service.rollback, version)


@router.put('/models')
def models(payload: dict[str, Provider]):
    return execute(service.save_config, {k: v.model_dump() for k, v in payload.items()})


@router.post('/models/{provider}/test')
def test_model(provider: str):
    try:
        return execute(service.probe_model, provider)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
