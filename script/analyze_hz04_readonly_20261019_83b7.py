"""Offline analysis of SELECT-only extract and routing artifact without executing stored globals."""
from __future__ import annotations
import collections, datetime as dt, io, json, pathlib, pickle, pickletools
ROOT = pathlib.Path(__file__).resolve().parents[1]
HERE = pathlib.Path(__file__).parent
RAW = HERE/'investigate_hz04_readonly_20261019_83b7_result.json'
OUT = HERE/'analyze_hz04_readonly_20261019_83b7_result.json'

def inert_timestamp(*args):
    return {'inert_pandas_timestamp_args':args}
class RestrictedArtifactReader(pickle.Unpickler):
    def find_class(self,module,name):
        if (module,name)==('pandas._libs.tslibs.timestamps','_unpickle_timestamp'):
            return inert_timestamp
        raise pickle.UnpicklingError('Disallowed stored global: '+module+'.'+name)
    def persistent_load(self,pid):
        raise pickle.UnpicklingError('No persistent objects permitted')

def ratio(n,d): return 100*n/d if d else None

def summarize_history(rows, start=None,end=None):
    use=[r for r in rows if (start is None or r['date']>=start) and (end is None or r['date']<=end)]
    totals=collections.defaultdict(float); days=collections.defaultdict(set); shops=collections.defaultdict(lambda:collections.defaultdict(float)); sample=collections.defaultdict(float)
    for r in use:
        wh=r['warehouse_id'];totals[wh]+=r['orders'];days[wh].add(r['date']);shops[r['shop_id']][wh]+=r['orders']
        if r['is_sample']: sample[wh]+=r['orders']
    grand=sum(totals.values()); observed=sorted({r['date'] for r in use})
    return {'requested_start':start,'requested_end':end,'observed_start':min(observed) if observed else None,'observed_end':max(observed) if observed else None,'observed_days':len(observed),'total_orders':grand,'warehouse_totals':[{'warehouse_id':w,'orders':n,'share_pct':ratio(n,grand),'observed_days':len(days[w]),'mean_per_observed_day':n/len(days[w]),'sample_orders':sample[w]} for w,n in sorted(totals.items(),key=lambda x:-x[1])],'shop_warehouse_totals':[{'shop_id':s,'total_orders':sum(whs.values()),'warehouse_orders':dict(whs),'hz04_orders':whs.get('HZ04'), 'hz04_share_pct':ratio(whs['HZ04'],sum(whs.values())) if 'HZ04' in whs else None} for s,whs in sorted(shops.items(),key=lambda x:-x[1].get('HZ04',0))]}

r=json.loads(RAW.read_text(encoding='utf-8'))
a={'source':str(RAW),'selected_job':r['selected_job'],'captured_at':r['captured_at']}
job=next(j for j in r['jobs'] if j['job_id']==r['selected_job'])
a['selected_job_record']=job
cfg=job['config_snapshot'] or {}
a['config_keys']=list(cfg)
a['schema_tables']=r['tables']
a['warehouse_definitions']=r['warehouses']
a['job_logs']=r['job_logs']
a['batch_logs']=r['batch_logs']
a['history_batches']={k:r[k] for k in ['warehouse_orders_batches','channel_orders_batches']}
a['manual_adjustments']=r['prediction_totals']
a['duplicates']={k:r[k] for k in ['warehouse_prediction_duplicates','prediction_duplicates']}
shopdefs={s['shop_id']:s for s in r['shops']}
shop_wh=collections.defaultdict(dict)
for row in r['warehouse_prediction_shop']:shop_wh[row['shop_id']][row['warehouse_id']]=row['orders']
wh_total=sum(row['orders'] for row in r['warehouse_prediction_totals'])
a['warehouse_forecasts']=[]
for row in r['warehouse_prediction_totals']:
    daily=[d for d in r['warehouse_prediction_daily'] if d['warehouse_id']==row['warehouse_id']]
    peak=max(daily,key=lambda x:x['orders'])
    a['warehouse_forecasts'].append({**row,'share_pct':ratio(row['orders'],wh_total),'mean_per_observed_day':row['orders']/row['observed_days'],'peak':peak})
hz_total=next(x['orders'] for x in r['warehouse_prediction_totals'] if x['warehouse_id']=='HZ04')
predshop={s['shop_id']:s for s in r['prediction_shop']}
a['hz04_shop_contributors']=[]
for row in sorted((x for x in r['warehouse_prediction_shop'] if x['warehouse_id']=='HZ04'), key=lambda x:-x['orders']):
    s=row['shop_id'];total=sum(shop_wh[s].values())
    a['hz04_shop_contributors'].append({**row,'shop':shopdefs.get(s),'hz04_contribution_pct':ratio(row['orders'],hz_total),'shop_hz04_share_pct':ratio(row['orders'],total),'shop_warehouse_orders':shop_wh[s],'shop_channel_prediction':predshop.get(s)})
a['conservation_by_shop']=[{'shop_id':s,'warehouse_total':sum(ws.values()),'channel_effective_total':predshop.get(s,{}).get('effective_orders'),'delta':sum(ws.values())-predshop[s]['effective_orders'] if s in predshop else None} for s,ws in shop_wh.items()]
a['hz04_daily']=[x for x in r['warehouse_prediction_daily'] if x['warehouse_id']=='HZ04']
a['current_json_config']={k:v for k,v in json.loads((ROOT/'ecom_forecast/config/user_config.json').read_text(encoding='utf-8')).items() if k in ['routing','warehouses','shops','last_train_time']}
a['snapshot_routing_keys']={k:v for k,v in cfg.items() if any(term in k for term in ['warehouse','routing','batch','train','date','forecast','sample'])}
a['all_batch_history']={}
for batch in r['warehouse_orders_batches']:
    bid=batch['upload_batch'];rows=[x for x in r['warehouse_orders_daily_shop'] if x['upload_batch']==bid]
    last=batch['date_end'];end=dt.date.fromisoformat(last)
    a['all_batch_history'][str(bid)]={'all':summarize_history(rows),'last30':summarize_history(rows,start=(end-dt.timedelta(days=29)).isoformat(),end=last),'last90':summarize_history(rows,start=(end-dt.timedelta(days=89)).isoformat(),end=last)}
# Default routing window is independently confirmed from current code and stored artifact.
train_batch=cfg.get('training_data_batches',{}).get('warehouse')
if train_batch:
    rows=[x for x in r['warehouse_orders_daily_shop'] if x['upload_batch']==train_batch]
    a['training_batch_stable_window']=summarize_history(rows,start='2026-01-14')
    a['training_batch_warehouse_codes']=sorted({x['warehouse_id'] for x in rows})
    a['training_batch_hz04_recorded_rows']=sum(x['source_rows'] for x in rows if x['warehouse_id']=='HZ04')
    a['training_batch_hz04_missing_note']='No HZ04 records. This is missing coverage, not proof that actual HZ04 orders were zero.'
    active={x['warehouse_id'] for x in cfg.get('warehouses',[]) if x.get('is_active')}
    active_rows=[x for x in rows if x['warehouse_id'] in active]
    end=dt.date.fromisoformat(max(x['date'] for x in rows))
    a['history_current_active_codes_only']={'note':'Restricts to exact canonical active warehouse IDs. Does not rename or merge unknown warehouse aliases, and does not create zero records.', 'active_codes':sorted(active),'stable':summarize_history(active_rows,start='2026-01-14'),'last30':summarize_history(active_rows,start=(end-dt.timedelta(days=29)).isoformat(),end=end.isoformat()),'last90':summarize_history(active_rows,start=(end-dt.timedelta(days=89)).isoformat(),end=end.isoformat())}
    asof='2026-10-19'
    future_hz=[x for x in a['hz04_daily'] if x['date']>=asof]
    a['hz04_asof_today_and_future']={'asof':asof,'observed_days':len(future_hz),'orders':sum(x['orders'] for x in future_hz),'note':'API rank uses whole 30-day task, not only remaining dates.'}
    tops={x['shop_id'] for x in a['hz04_shop_contributors'][:10]}
    a['top10_history_comparison']={period:[{**x,'warehouse_share_pct':{w:ratio(n,x['total_orders']) for w,n in x['warehouse_orders'].items()}} for x in data['shop_warehouse_totals'] if x['shop_id'] in tops] for period,data in {**a['all_batch_history'][train_batch],'stable':a['training_batch_stable_window']}.items()}
# Compare routing-relevant task snapshot against live DB, not stale user_config.json.
snapshops={str(x['shop_id']):x for x in cfg.get('shops',[])}
snapbindings={str(x['shop_id']):x for x in cfg.get('shop_warehouse_binding',[])}
snapwh={str(x['warehouse_id']):x for x in cfg.get('warehouses',[])}
a['config_comparison']={'shop_binding_differences':[],'warehouse_differences':[],'current_hz04_single_bindings':[x for x in r['shops'] if x['binding_type']=='single' and x['warehouse_id']=='HZ04']}
for sid,live in shopdefs.items():
    snap=snapbindings.get(sid,snapshops.get(sid,{}))
    changes={k:{'snapshot':snap.get(k),'current_db':live.get(k)} for k in ['binding_type','warehouse_id','effective_date'] if snap.get(k)!=live.get(k)}
    if changes:a['config_comparison']['shop_binding_differences'].append({'shop_id':sid,'changes':changes})
for live in r['warehouses']:
    wid=live['warehouse_id'];snap=snapwh.get(wid,{})
    changes={k:{'snapshot':snap.get(k),'current_db':live.get(k)} for k in ['name','is_active'] if snap.get(k)!=live.get(k)}
    if changes:a['config_comparison']['warehouse_differences'].append({'warehouse_id':wid,'changes':changes})
a['routing_tables_present']=[t for t in r['tables'] if t in ['warehouse_rules','warehouse_split','warehouse_splits','warehouse_aliases']]
a['hz04_binding_vs_uniform_contribution']={'single_bound_orders':sum(x['orders'] for x in a['hz04_shop_contributors'] if x['shop'] and x['shop']['binding_type']=='single' and x['shop']['warehouse_id']=='HZ04'),'multi_orders':sum(x['orders'] for x in a['hz04_shop_contributors'] if x['shop'] and x['shop']['binding_type']=='multi')}
# Local artifact is parsed only with an inert pandas timestamp stand-in; no artifact global is executed.
p=ROOT/'ecom_forecast/models/warehouse_router.joblib'
b=p.read_bytes();ops=list(pickletools.genops(b))
art=RestrictedArtifactReader(io.BytesIO(b)).load()
a['routing_artifact']={'path':str(p),'bytes':len(b),'mtime':dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(),'deserialization':'restricted globals; timestamp replaced with inert local stub','keys':list(art),'contents':art}
OUT.write_text(json.dumps(a,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'output':str(OUT),'job':a['selected_job_record'],'warehouse_forecasts':a['warehouse_forecasts'],'hz04_top10':a['hz04_shop_contributors'][:10],'manual_adjustments':a['manual_adjustments'],'conservation_max_abs_delta':max(abs(x['delta'] or 0) for x in a['conservation_by_shop']),'snapshot_routing_keys':a['snapshot_routing_keys'],'artifact_keys':a['routing_artifact']['keys'],'artifact_contents':art,'history_summary':{k:{w:{kk:vv for kk,vv in v.items() if kk!='shop_warehouse_totals'} for w,v in windows.items()} for k,windows in a['all_batch_history'].items()}},ensure_ascii=False,indent=2))
