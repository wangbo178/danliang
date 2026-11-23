"""Read-only order stability audit. Outputs JSON locally; never imports API initialization.
Run: python script/analyze_order_stability_20261019_c7f1.py > local-result.json
All database queries use a consistent READ ONLY transaction. No missing day is zero-filled.
"""
import ast
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import pandas as pd
import pymysql

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'ecom_forecast/api/database.py').read_text(encoding='utf-8'))
url = urlsplit(next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == 'DATABASE_URL' for x in n.targets)))
conn = pymysql.connect(host=url.hostname, port=url.port or 3306, user=url.username, password=url.password, database=url.path.lstrip('/'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()
cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')

def query(sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()

def frame(sql, args=None):
    return pd.DataFrame(query(sql, args))

def clean(x):
    if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [clean(v) for v in x]
    if isinstance(x, (pd.Timestamp, dt.datetime, dt.date)): return str(x)
    if isinstance(x, (np.integer,)): return int(x)
    if isinstance(x, (np.floating, float)): return None if not np.isfinite(x) else float(x)
    if x is pd.NA: return None
    if isinstance(x, np.ndarray): return clean(x.tolist())
    if hasattr(x, 'as_integer_ratio') and not isinstance(x, (int, str)): return float(x)
    return x

def stats(s):
    v = s.dropna().astype(float)
    return dict(n=len(v), missing=int(s.isna().sum()), zero=int((v == 0).sum()), total=v.sum(), mean=v.mean(), median=v.median(), std=v.std(ddof=1), cv=v.std(ddof=1)/v.mean() if v.mean() else None, p10=v.quantile(.1), p90=v.quantile(.9), p95=v.quantile(.95), p99=v.quantile(.99), min=v.min(), max=v.max())

def score(y, p):
    ok = y.notna() & p.notna()
    a, f = y[ok], p[ok]
    return dict(n=int(ok.sum()), first=a.index.min() if len(a) else None, last=a.index.max() if len(a) else None, actual=a.sum(), predicted=f.sum(), wape=(f-a).abs().sum()/a.sum() if a.sum() else None, bias=(f.sum()-a.sum())/a.sum() if a.sum() else None)

out = {'read_at': str(dt.datetime.now()), 'method': 'Latest batch only, sum normal plus sample, aggregate date/shop/platform/channel; ddof=1; observed groups only, missing dates NaN, no zero imputation.'}
out['batches'] = query('SELECT upload_batch,COUNT(*) rows_n,MIN(date) first_day,MAX(date) last_day,COUNT(DISTINCT date) days,COUNT(DISTINCT shop_id) shops,COUNT(DISTINCT channel) channels,SUM(order_count) orders,MAX(id) last_id,MAX(created_at) uploaded FROM channel_orders GROUP BY upload_batch ORDER BY last_id DESC')
out['batch_logs'] = query("SELECT data_type,batch_id,row_count,data_start,data_end,source_filename,uploaded_at,time_source FROM upload_batch_logs ORDER BY uploaded_at DESC LIMIT 35")
latest = query('SELECT upload_batch FROM channel_orders ORDER BY created_at DESC,id DESC LIMIT 1')[0]['upload_batch']
out['overview_default_batch'] = query("SELECT batch_id,uploaded_at FROM upload_batch_logs WHERE data_type='channel' AND row_count>0 ORDER BY uploaded_at DESC,id DESC LIMIT 1")
out['latest_batch'] = latest
out['channels'] = query('SELECT platform_id,channel_id,name,channel_type,include_in_forecast FROM channels')
out['platforms'] = query('SELECT platform_id,name FROM platforms')
out['calendar'] = query('SELECT name,start_date,end_date,campaign_type FROM campaign_calendar ORDER BY start_date')
out['aux_counts'] = {t: query('SELECT COUNT(*) n FROM '+t)[0]['n'] for t in ['live_schedules','commissions','anchor_live_orders']}
out['raw_quality'] = query('SELECT COUNT(*) rows_n,SUM(order_count IS NULL) null_orders,SUM(order_count<0) negative_orders,SUM(order_count=0) zero_rows,SUM(platform IS NULL OR platform=\'\') missing_platform FROM channel_orders WHERE upload_batch=%s',(latest,))[0]
out['sample_split'] = query('SELECT is_sample,COUNT(*) rows_n,SUM(order_count) orders FROM channel_orders WHERE upload_batch=%s GROUP BY is_sample',(latest,))
out['duplicate_keys'] = query('SELECT COUNT(*) groups_n,SUM(n-1) extra_rows,SUM(n>1) duplicated_groups,MAX(n) max_rows_per_key FROM (SELECT date,shop_id,platform,channel,COUNT(*) n FROM channel_orders WHERE upload_batch=%s GROUP BY date,shop_id,platform,channel) z',(latest,))[0]
out['duplicate_with_sample'] = query('SELECT COUNT(*) duplicated_groups,SUM(n-1) extra_rows,MAX(n) max_rows_per_key FROM (SELECT date,shop_id,platform,channel,is_sample,COUNT(*) n FROM channel_orders WHERE upload_batch=%s GROUP BY date,shop_id,platform,channel,is_sample HAVING COUNT(*)>1) z',(latest,))[0]
d = frame('SELECT date,shop_id,platform,channel,SUM(order_count) orders,COUNT(*) rows_n,SUM(CASE WHEN is_sample=1 THEN order_count ELSE 0 END) sample_orders FROM channel_orders WHERE upload_batch=%s GROUP BY date,shop_id,platform,channel',(latest,))
d['date'] = pd.to_datetime(d['date'])
d[['orders','sample_orders']] = d[['orders','sample_orders']].astype(float)
d['platform'] = d['platform'].fillna('(NULL)')
idx = pd.date_range(d.date.min(), d.date.max(), freq='D')
y = d.groupby('date').orders.sum().reindex(idx)
out['coverage'] = dict(first=idx.min(),last=idx.max(),calendar_days=len(idx),observed_days=int(y.notna().sum()),missing_dates=[str(v.date()) for v in y.index[y.isna()]],group_keys=len(d),shop_count=d.shop_id.nunique(),channel_count=d.channel.nunique(),platform_count=d.platform.nunique())
out['overall'] = stats(y)
day_cov = d.groupby('date').agg(keys=('orders','size'),shops=('shop_id','nunique'),channels=('channel','nunique'),platforms=('platform','nunique'),zeros=('orders',lambda x:int((x==0).sum())),orders=('orders','sum'))
out['recent_day_coverage'] = day_cov.tail(35).reset_index().to_dict('records')
out['day_key_count_quantiles'] = day_cov['keys'].quantile([0,.1,.5,.9,1]).to_dict()
# Presence gaps are unknown, not necessarily zero sales. Diagnose within each observed series lifetime.
series_coverage = d.groupby(['shop_id','platform','channel']).agg(first=('date','min'),last=('date','max'),n=('date','nunique'),total=('orders','sum'),zero_days=('orders',lambda x:int((x==0).sum())))
series_coverage['span'] = (series_coverage['last']-series_coverage['first']).dt.days+1
series_coverage['missing_in_lifetime'] = series_coverage['span']-series_coverage['n']
out['series_presence'] = dict(series_n=len(series_coverage),series_with_gaps=int((series_coverage.missing_in_lifetime>0).sum()),missing_keydays_within_lifetime=int(series_coverage.missing_in_lifetime.sum()),explicit_zero_keydays=int((d.orders==0).sum()),worst=series_coverage.sort_values('missing_in_lifetime',ascending=False).head(12).reset_index().to_dict('records'))

def calendar_names(date):
    return [c['name'] for c in out['calendar'] if pd.Timestamp(c['start_date']) <= date <= pd.Timestamp(c['end_date'])]
out['top_days'] = [dict(date=t,orders=v,calendar=calendar_names(t)) for t,v in y.nlargest(15).items()]
out['bottom_days'] = [dict(date=t,orders=v,calendar=calendar_names(t)) for t,v in y.nsmallest(10).items()]
for freq,name in [('Y','yearly'),('Q','quarterly'),('M','monthly')]:
    out[name] = [dict(period=str(k),**stats(v)) for k,v in y.groupby(y.index.to_period(freq))]
out['weekday'] = [dict(weekday=int(k),**stats(v)) for k,v in y.groupby(y.index.dayofweek)]
# Campaign labels are descriptive configured calendar, not proof all campaign days have equal intensity.
event = pd.Series([bool(calendar_names(t)) for t in idx], index=idx)
out['campaign_comparison'] = dict(active=stats(y[event]),ordinary=stats(y[~event]))
valid = y.dropna()
# Descriptive in-sample variance decomposition, NOT prediction evaluation.
month = pd.get_dummies(valid.index.to_period('M').astype(str),drop_first=True,dtype=float).to_numpy()
dow = pd.get_dummies(valid.index.dayofweek,drop_first=True,dtype=float).to_numpy()
x0 = np.ones((len(valid),1)); xa=np.column_stack([x0,month]); xb=np.column_stack([xa,dow]); xc=np.column_stack([xb,event.loc[valid.index].astype(float).to_numpy()])
tss=float(np.square(valid-valid.mean()).sum()); prev=tss; decomposition=[]
for label,x in [('month_level_trend_seasonality',xa),('extra_dayofweek',xb),('extra_campaign_flag',xc)]:
    residual=valid.to_numpy()-x@np.linalg.lstsq(x,valid.to_numpy(),rcond=None)[0]
    sse=float(residual@residual)
    decomposition.append(dict(component=label,incremental_variance_fraction=(prev-sse)/tss,residual_cv=np.std(residual,ddof=1)/valid.mean()))
    prev=sse
out['descriptive_decomposition']=dict(components=decomposition,residual_variance_fraction=prev/tss,n=len(valid),warning='Uses full-sample month dummies, explains but does not forecast or establish causality.')
# Compare historical batches only over common dates; never add batch totals.
comp=frame('SELECT upload_batch,date,SUM(order_count) orders FROM channel_orders GROUP BY upload_batch,date')
comp['date']=pd.to_datetime(comp.date);comp['orders']=comp.orders.astype(float)
out['batch_comparison']=[]
for batch,g in comp.groupby('upload_batch'):
    s=g.set_index('date').orders
    common=s.index.intersection(y.dropna().index)
    diff=y.loc[common]-s.loc[common]
    out['batch_comparison'].append(dict(batch=batch,common_days=len(common),current_total_on_common=y.loc[common].sum(),batch_total_on_common=s.loc[common].sum(),difference=diff.sum(),changed_days=int((diff!=0).sum()),mean_absolute_daily_difference=diff.abs().mean(),current_vs_old_wape=diff.abs().sum()/s.loc[common].sum()))
# Infer live narrowly by names, independent of potentially overloaded model channel_type.
chname={r['channel_id']:r['name'] for r in out['channels']}
d['channel_label']=d.channel.map(chname).fillna(d.channel)
d['is_live']=d.channel_label.str.contains('直播|达播|自播',regex=True)|d.channel.str.contains(r'(?:^|_)zb(?:$|_)|live',case=False,regex=True)
series={'COMPANY':y}
out['platform_stats']=[]
for key,g in d.groupby('platform'):
    s=g.groupby('date').orders.sum().reindex(idx)
    out['platform_stats'].append(dict(platform=key,**stats(s),share=s.sum()/y.sum()))
    series['platform:'+str(key)]=s
out['channel_stats']=[]
for (p,c),g in d.groupby(['platform','channel']):
    s=g.groupby('date').orders.sum().reindex(idx)
    out['channel_stats'].append(dict(platform=p,channel=c,name=chname.get(c,c),**stats(s),share=s.sum()/y.sum()))
    series['channel:'+str(p)+':'+str(c)]=s
out['platform_stats'].sort(key=lambda r:r['total'],reverse=True)
out['channel_stats'].sort(key=lambda r:r['total'],reverse=True)
out['live_stats']=[]
for b,g in d.groupby('is_live'):
    s=g.groupby('date').orders.sum().reindex(idx)
    series['live' if b else 'nonlive']=s
    out['live_stats'].append(dict(live=bool(b),**stats(s),share=s.sum()/y.sum(),top1pct_days_volume=s.loc[y>=y.quantile(.99)].sum(),top1pct_days_share=s.loc[y>=y.quantile(.99)].sum()/y[y>=y.quantile(.99)].sum(),top5pct_days_share=s.loc[y>=y.quantile(.95)].sum()/y[y>=y.quantile(.95)].sum()))
out['spike_share']=dict(top1pct_days=int((y>=y.quantile(.99)).sum()),top1pct_volume_share=y[y>=y.quantile(.99)].sum()/y.sum(),top5pct_days=int((y>=y.quantile(.95)).sum()),top5pct_volume_share=y[y>=y.quantile(.95)].sum()/y.sum())
out['recent_windows']={str(n):stats(y.tail(n)) for n in [30,90,180,365]}
# One-step rolling observes yesterday's actual; no future information, strict calendar shifts.
selected=['COMPANY','live','nonlive']+['platform:'+str(r['platform']) for r in out['platform_stats'][:4]]+['channel:'+str(r['platform'])+':'+str(r['channel']) for r in out['channel_stats'][:8]]
out['baselines_rolling']=[];out['baselines_fixed_origin']=[]
for key in selected:
    s=series[key]
    lags=pd.concat([s.shift(k) for k in [7,14,21,28]],axis=1)
    preds={'lag7':s.shift(7),'past4_same_weekday':lags.mean(axis=1).where(lags.notna().all(axis=1)),'mean7':s.shift(1).rolling(7,min_periods=7).mean(),'mean28':s.shift(1).rolling(28,min_periods=28).mean()}
    for n in [30,90]:
        window=s.tail(n)
        # Same target dates across models per group, so coverage is explicit and comparisons fair.
        valid_idx=window.index[window.notna() & pd.concat([p.loc[window.index] for p in preds.values()],axis=1).notna().all(axis=1)]
        for model,p in preds.items():out['baselines_rolling'].append(dict(series=key,window=n,model=model,**score(window.loc[valid_idx],p.loc[valid_idx])))
        # n=90 comprises three disjoint fixed-origin 30-day forecasts, never refreshed within a block.
        targets=s.index[-n:];fps={k:pd.Series(index=targets,dtype=float) for k in ['seasonal_naive7_recursive','past4_same_weekday_fixed','mean28_fixed']}
        for offset in range(0,len(targets),30):
            block=targets[offset:offset+30];origin=block[0];history=s.loc[s.index<origin]
            for date in block:
                for model in fps:
                    if model=='seasonal_naive7_recursive':
                        lookup=origin-pd.Timedelta(days=7)+pd.Timedelta(days=(date-origin).days%7)
                        val=history.get(lookup,np.nan)
                    elif model=='past4_same_weekday_fixed':
                        vv=history[history.index.dayofweek==date.dayofweek].tail(4)
                        val=vv.mean() if len(vv)==4 and vv.notna().all() else np.nan
                    else:val=history.tail(28).mean() if len(history)>=28 and history.tail(28).notna().all() else np.nan
                    fps[model].loc[date]=val
        valid_idx=targets[s.loc[targets].notna() & pd.concat([p for p in fps.values()],axis=1).notna().all(axis=1)]
        for model,p in fps.items():out['baselines_fixed_origin'].append(dict(series=key,window=n,blocks=n//30,model=model,**score(s.loc[valid_idx],p.loc[valid_idx])))
# Tail integrity and sensitivity: sparse rows do not distinguish no sale from unreported actual.
out['channel_recent_coverage']=query('SELECT channel,MIN(date) first_day,MAX(date) last_day,COUNT(DISTINCT date) days,SUM(order_count) orders FROM channel_orders WHERE upload_batch=%s AND date>=%s GROUP BY channel ORDER BY channel',(latest,str((idx.max()-pd.Timedelta(days=29)).date())))
out['tail_channel_daily']=query('SELECT date,channel,SUM(order_count) orders FROM channel_orders WHERE upload_batch=%s AND date>=%s GROUP BY date,channel ORDER BY date,channel',(latest,str((idx.max()-pd.Timedelta(days=9)).date())))
out['duplicate_value_diagnostic']=query('SELECT COUNT(*) duplicated_keys,SUM(n) rows_n,SUM(total) orders_on_duplicated_keys,SUM(lo) lower_bound_redundant_if_exact_duplicates,SUM(lo=hi) keys_equal_order_count,SUM(CASE WHEN lo=hi THEN lo ELSE 0 END) equal_count_possible_redundancy FROM (SELECT date,shop_id,platform,channel,is_sample,COUNT(*) n,MIN(order_count) lo,MAX(order_count) hi,SUM(order_count) total FROM channel_orders WHERE upload_batch=%s GROUP BY date,shop_id,platform,channel,is_sample HAVING COUNT(*)>1) a',(latest,))
out['recent_sensitivity']=[]
for end in [idx.max(),pd.Timestamp('2026-10-10')]:
    s=y.loc[:end]
    for days in [30,90]:
        v=s.tail(days); lag4=pd.concat([s.shift(k) for k in [7,14,21,28]],axis=1)
        for name,p in [('lag7',s.shift(7)),('past4_same_weekday',lag4.mean(axis=1).where(lag4.notna().all(axis=1))),('mean7',s.shift(1).rolling(7).mean()),('mean28',s.shift(1).rolling(28).mean())]:
            out['recent_sensitivity'].append(dict(end=end,window=days,type='one_step_rolling',model=name,**score(v,p.loc[v.index])))
    target=s.tail(30);hist=s.loc[s.index<target.index[0]];origin=target.index[0]
    p=pd.Series([hist.loc[origin-pd.Timedelta(days=7)+pd.Timedelta(days=(date-origin).days%7)] for date in target.index],index=target.index)
    out['recent_sensitivity'].append(dict(end=end,window=30,type='fixed_origin',model='seasonal_naive7_recursive',**score(target,p)))
    p=pd.Series([hist[hist.index.dayofweek==date.dayofweek].tail(4).mean() for date in target.index],index=target.index)
    out['recent_sensitivity'].append(dict(end=end,window=30,type='fixed_origin',model='past4_same_weekday_fixed',**score(target,p)))
# Relevant recent completed tasks: no configuration secrets or full snapshots printed.
out['training_statuses']=query('SELECT status,COUNT(*) n FROM training_jobs GROUP BY status')
jobs=query("SELECT job_id,status,phases,config_snapshot,started_at,finished_at FROM training_jobs WHERE status IN ('completed','success','done') ORDER BY id DESC LIMIT 3")
out['recent_jobs']=[]
for j in jobs:
    cfg=j.pop('config_snapshot')
    if isinstance(cfg,str): cfg=json.loads(cfg)
    j['config_keys']=list(cfg or {})
    keep=['forecast_days','prediction_days','forecast_start','forecast_end','train_end_date','train_start_date','holdout_days','backtest_days','channel_batch','channel_upload_batch','upload_batch','data_batch','val_days','phases','training_data_batches']
    j['config_selected']={k:cfg[k] for k in keep if cfg and k in cfg}
    j['metrics']=query('SELECT level,group_key,mae,rmse,mape,wape,bias,n_samples,total_actual,total_predicted,raw_wape,raw_bias FROM evaluation_metrics WHERE job_id=%s',(j['job_id'],))
    j['backtest_coverage']=query('SELECT level,MIN(date) first_day,MAX(date) last_day,COUNT(*) rows_n,SUM(actual_orders) actual,SUM(predicted_orders) predicted FROM backtest_daily_results WHERE job_id=%s GROUP BY level',(j['job_id'],))
    j['prediction_coverage']=query('SELECT MIN(date) first_day,MAX(date) last_day,COUNT(*) rows_n FROM predictions WHERE job_id=%s',(j['job_id'],))
    out['recent_jobs'].append(j)
conn.rollback();conn.close()
print(json.dumps(clean(out),ensure_ascii=False,indent=2,default=str))
