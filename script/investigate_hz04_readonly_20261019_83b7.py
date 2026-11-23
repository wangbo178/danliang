"""Read-only HZ04 investigation. Reusable: python <this_file> [--detail]. No product imports/writes."""
from __future__ import annotations
import argparse, ast, datetime as dt, decimal, json, pathlib, re
from urllib.parse import urlparse, unquote
import pymysql

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(__file__).with_name('investigate_hz04_readonly_20261019_83b7_result.json')
parser = argparse.ArgumentParser()
parser.add_argument('--detail', action='store_true')
args = parser.parse_args()
module = ast.parse((ROOT/'ecom_forecast/api/database.py').read_text(encoding='utf-8-sig'))
url = next(ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DATABASE_URL' for t in n.targets))
u = urlparse(url)
conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username), password=unquote(u.password), database=u.path.lstrip('/'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=False, read_timeout=240)
result = {'captured_at': dt.datetime.now().isoformat(), 'read_only': True}
def q(sql, params=None):
    if not re.match(r'^\s*(SELECT|SHOW)\b', sql, re.I):
        raise ValueError('Only SELECT/SHOW permitted')
    with conn.cursor() as c:
        c.execute(sql, params)
        return c.fetchall()
def decode(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode('utf-8', errors='replace')
    raise TypeError(type(v).__name__)
def parse_json(v):
    if isinstance(v, str):
        try: return json.loads(v)
        except ValueError: return v
    return v
try:
    with conn.cursor() as c:
        c.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
    result['server'] = q('SELECT NOW() server_now, @@version version, @@transaction_read_only transaction_read_only')
    tables = [next(iter(r.values())) for r in q('SHOW TABLES')]
    result['tables'] = tables
    wanted = ['training_jobs','warehouse_predictions','predictions','warehouse_orders','channel_orders','upload_batch_logs','warehouses','shops'] + [t for t in tables if ('warehouse' in t or 'rule' in t or 'split' in t or 'alias' in t)]
    result['schema'] = {t:q('SHOW COLUMNS FROM `'+t+'`') for t in dict.fromkeys(wanted) if t in tables}
    result['jobs'] = q('SELECT id,job_id,status,phases,config_snapshot,started_at,finished_at,created_at,error_msg FROM training_jobs ORDER BY created_at DESC LIMIT 12')
    for row in result['jobs']:
        row['config_snapshot'] = parse_json(row['config_snapshot'])
        row['phases'] = parse_json(row['phases'])
    result['latest_completed'] = q("SELECT job_id,finished_at,created_at FROM training_jobs WHERE status='done' ORDER BY finished_at DESC,created_at DESC LIMIT 1")
    result['warehouses'] = q('SELECT * FROM warehouses ORDER BY warehouse_id')
    result['shops'] = q('SELECT * FROM shops ORDER BY shop_id')
    result['batch_logs'] = q("SELECT * FROM upload_batch_logs WHERE data_type IN ('channel','warehouse') ORDER BY uploaded_at DESC,id DESC") if 'upload_batch_logs' in tables else []
    for t in ['warehouse_orders','channel_orders']:
        result[t+'_batches'] = q(f'SELECT upload_batch,COUNT(*) rows_n,MIN(date) date_start,MAX(date) date_end,MIN(created_at) created_min,MAX(created_at) created_max,SUM(order_count) orders,SUM(is_sample=1) sample_rows FROM {t} GROUP BY upload_batch ORDER BY created_max DESC')
    if result['latest_completed']:
        job = result['latest_completed'][0]['job_id']
        result['selected_job'] = job
        result['job_logs'] = q('SELECT id,log_level,created_at,log_text FROM job_logs WHERE job_id=%s ORDER BY id', (job,))
        result['warehouse_prediction_range'] = q('SELECT MIN(date) date_start,MAX(date) date_end,COUNT(DISTINCT date) observed_days,COUNT(*) rows_n,SUM(warehouse_orders) orders,SUM(sample_orders) samples FROM warehouse_predictions WHERE job_id=%s',(job,))
        result['warehouse_prediction_totals'] = q('SELECT warehouse_id,COUNT(*) rows_n,COUNT(DISTINCT date) observed_days,MIN(date) date_start,MAX(date) date_end,SUM(warehouse_orders) orders,SUM(sample_orders) samples FROM warehouse_predictions WHERE job_id=%s GROUP BY warehouse_id ORDER BY orders DESC',(job,))
        result['warehouse_prediction_daily'] = q('SELECT date,warehouse_id,SUM(warehouse_orders) orders,SUM(sample_orders) samples FROM warehouse_predictions WHERE job_id=%s GROUP BY date,warehouse_id ORDER BY date,warehouse_id',(job,))
        result['warehouse_prediction_shop'] = q('SELECT shop_id,warehouse_id,COUNT(DISTINCT date) observed_days,SUM(warehouse_orders) orders,SUM(sample_orders) samples FROM warehouse_predictions WHERE job_id=%s GROUP BY shop_id,warehouse_id ORDER BY orders DESC',(job,))
        result['prediction_totals'] = q('SELECT COUNT(*) rows_n,MIN(date) date_start,MAX(date) date_end,SUM(predicted_orders) model_orders,SUM(COALESCE(manual_predicted_orders,predicted_orders)) effective_orders,SUM(manual_predicted_orders IS NOT NULL) adjusted_rows,SUM(COALESCE(manual_predicted_orders,predicted_orders)-predicted_orders) adjustment_delta FROM predictions WHERE job_id=%s',(job,))
        result['prediction_shop'] = q('SELECT shop_id,SUM(predicted_orders) model_orders,SUM(COALESCE(manual_predicted_orders,predicted_orders)) effective_orders,SUM(manual_predicted_orders IS NOT NULL) adjusted_rows FROM predictions WHERE job_id=%s GROUP BY shop_id ORDER BY effective_orders DESC',(job,))
        result['prediction_daily_shop'] = q('SELECT date,shop_id,SUM(predicted_orders) model_orders,SUM(COALESCE(manual_predicted_orders,predicted_orders)) effective_orders FROM predictions WHERE job_id=%s GROUP BY date,shop_id ORDER BY date,shop_id',(job,))
        result['warehouse_prediction_duplicates'] = q('SELECT date,shop_id,channel,warehouse_id,COUNT(*) n FROM warehouse_predictions WHERE job_id=%s GROUP BY date,shop_id,channel,warehouse_id HAVING COUNT(*)>1 LIMIT 30',(job,))
        result['prediction_duplicates'] = q('SELECT date,shop_id,channel,COUNT(*) n FROM predictions WHERE job_id=%s GROUP BY date,shop_id,channel HAVING COUNT(*)>1 LIMIT 30',(job,))
    for t in tables:
        if t not in wanted and ('warehouse' in t or 'rule' in t or 'split' in t or 'alias' in t):
            result['table_'+t] = q('SELECT * FROM `'+t+'` LIMIT 500')
    if args.detail:
        # Aggregate each historical upload batch independently; never concatenate snapshots.
        for t in ['warehouse_orders','channel_orders']:
            groupcols = 'upload_batch,date,shop_id,warehouse_id,is_sample' if t=='warehouse_orders' else 'upload_batch,date,shop_id,is_sample'
            result[t+'_daily_shop'] = q(f'SELECT {groupcols},COUNT(*) source_rows,SUM(order_count) orders FROM {t} GROUP BY {groupcols} ORDER BY upload_batch,date,shop_id')
finally:
    conn.rollback()
    conn.close()
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=decode), encoding='utf-8')
print(json.dumps({'output':str(OUT),'selected_job':result.get('selected_job'),'latest_completed':result.get('latest_completed'),'jobs':[{'job_id':r['job_id'],'status':r['status'],'created_at':r['created_at'],'config_keys':list(r['config_snapshot']) if isinstance(r['config_snapshot'],dict) else None} for r in result.get('jobs',[])],'tables':result.get('tables'),'warehouse_prediction_range':result.get('warehouse_prediction_range'),'warehouse_prediction_totals':result.get('warehouse_prediction_totals'),'prediction_totals':result.get('prediction_totals'),'warehouse_orders_batches':result.get('warehouse_orders_batches'),'channel_orders_batches':result.get('channel_orders_batches')},ensure_ascii=False,indent=2,default=decode))
