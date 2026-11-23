"""GMV 业务核验、规则边界、任务审查和回滚回归；使用临时目录。"""
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecom_forecast'))
from api.services import expected_gmv as g
from api.services.gmv_parser import normalize, parse, verify, load_rule


class FeishuLinkTests(unittest.TestCase):
    def source(self, path='sheets/book', query='?from=from_copylink'):
        return {'url': 'https://example.feishu.cn/' + path + query, 'range': 'A1:BP16'}

    def test_share_link_resolves_visible_sheet_and_title(self):
        metadata = {'title': '渠道GMV版', 'sheets': [
            {'sheet_id': 'hidden', 'index': 0, 'is_hidden': True},
            {'sheet_id': 'later', 'index': 2},
            {'sheet_id': 'first', 'index': 1, 'sheet_name': '营销节奏'}]}
        raw = {'ranges': [{'row_indices': [1], 'col_indices': ['A'], 'cells': [[{'value': '25年'}]]}]}
        events = []
        with patch.object(g, 'cli', side_effect=[metadata, raw]) as cli:
            snapshot = g.read_sheet(self.source(), lambda *args: events.append(args))
        self.assertEqual(snapshot['title'], '渠道GMV版')
        self.assertEqual(snapshot['sheet_id'], 'first')
        self.assertIn('first', cli.call_args.args[0])
        self.assertTrue(any(e[0] == 'decision' for e in events))

    def test_explicit_sheet_and_wiki_remain_supported(self):
        raw = {'ranges': [{'row_indices': [1], 'col_indices': ['A'], 'cells': [[{'value': 1}]]}]}
        for path, info in [('sheets/book', {'title': '表格'}),
                           ('wiki/node', {'obj_type': 'sheet', 'obj_token': 'book', 'title': '知识库表格'})]:
            responses = [info, {'sheets': [{'sheet_id': 'chosen'}]}, raw] if path.startswith('wiki') else [info, raw]
            with self.subTest(path=path), patch.object(g, 'cli', side_effect=responses) as cli:
                result = g.read_sheet(self.source(path, '?sheet=chosen'))
                self.assertEqual(result['sheet_id'], 'chosen')
                self.assertEqual(cli.call_count, len(responses))

    def test_wiki_without_sheet_resolves_workbook(self):
        info = {'obj_type': 'sheet', 'obj_token': 'book', 'title': '规划'}
        metadata = {'sheets': [{'sheet_id': 'tab'}]}
        raw = {'ranges': [{'row_indices': [1], 'col_indices': ['A'], 'cells': [[{'value': 1}]]}]}
        with patch.object(g, 'cli', side_effect=[info, metadata, raw]):
            self.assertEqual(g.read_sheet(self.source('wiki/node'))['sheet_id'], 'tab')

    def test_invalid_sheet_or_missing_visible_sheet_is_rejected(self):
        for query in ['?sheet=', '?sheet=bad%2Fid']:
            with self.subTest(query=query), self.assertRaises(ValueError):
                g.validate_source(self.source(query=query))
        with patch.object(g, 'cli', return_value={'sheets': [{'sheet_id': 'hidden', 'is_hidden': True}]}) as cli:
            with self.assertRaisesRegex(ValueError, '未找到可读取的工作表'):
                g.read_sheet(self.source())
            self.assertEqual(cli.call_count, 1)


class GMVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.rules = self.directory / 'rules'
        self.rules.mkdir()
        self.code = (g.RULES / 'tmall.py').read_text(encoding='utf-8')
        (self.rules / 'tmall.py').write_text(self.code, encoding='utf-8')
        self.patches = [patch.object(g, 'STORE', self.directory / 'data'), patch.object(g, 'RULES', self.rules)]
        for p in self.patches:
            p.start()
        g.init()
        self.snapshot = json.loads((ROOT / 'script/fixtures/gmv_sep.json').read_text(encoding='utf-8'))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_real_sep_and_campaign_mismatch(self):
        result = parse(self.snapshot, self.code, 2026)
        self.assertTrue(verify(self.snapshot, result)['passed'])
        self.assertEqual(len(result['records']), 155)
        self.assertEqual(len(result['schedules']), 10)
        oct_snapshot = json.loads((ROOT / 'script/fixtures/gmv_oct_nov.json').read_text(encoding='utf-8'))
        oct_result = parse(oct_snapshot, self.code, 2026)
        report = verify(oct_snapshot, oct_result)
        self.assertEqual(len(oct_result['records']), 325)
        self.assertTrue(report['passed'])
        self.assertEqual(report['scope'], 'source_ranges')

    def test_blank_zero_and_tamper(self):
        result = parse(self.snapshot, self.code, 2026)
        self.assertTrue(any(r['amount'] == 0 for r in result['records']))
        result['records'][0]['amount'] += 100
        self.assertFalse(verify(self.snapshot, result)['passed'])
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['cells'] = [c for c in snapshot['cells'] if c['ref'] != 'D26']
        with self.assertRaises(ValueError):
            parse(snapshot, self.code, 2026)

    def test_channel_alias_swap_is_rejected(self):
        swapped = self.code.replace("['货架-常规', '货架-常规GMV']", "['百补']").replace("'百补': ['百补']", "'百补': ['货架-常规']")
        result = parse(self.snapshot, swapped, 2026)
        report = verify(self.snapshot, result, 2026)
        self.assertFalse(report['passed'])
        self.assertTrue(any('读取范围错误' in e for e in report['errors']))

    def test_scope_omissions_wrong_dates_and_schedule_swap(self):
        original = parse(self.snapshot, self.code, 2026)
        for kind in ['missing_record', 'wrong_date', 'schedule_swap', 'wrong_boundary']:
            result = copy.deepcopy(original)
            if kind == 'missing_record':
                result['records'].pop()
            elif kind == 'wrong_date':
                result['records'][0]['period'] = '2025-09-01'
            elif kind == 'schedule_swap':
                result['schedules'][0]['type'] = '店铺节奏'
            else:
                result['section_rows'][0] = 10
            self.assertFalse(verify(self.snapshot, result, 2026)['passed'], kind)

    def test_reviewer_must_supply_exact_range_evidence(self):
        result = parse(self.snapshot, self.code, 2026)
        review = {'approved': True, 'issues': [], 'summary': '通过', 'scope_checks': []}
        with self.assertRaises(ValueError):
            g.validate_scope_review(review, result)
        review['scope_checks'] = [{'target': name, 'cells': refs, 'correct': True, 'reason': '原表独立定位'} for name, refs in g.scope_cells(result).items()]
        g.validate_scope_review(review, result)
        review['scope_checks'][0]['cells'] = ['D15']
        with self.assertRaises(ValueError):
            g.validate_scope_review(review, result)

    def test_rule_code_cannot_execute(self):
        for code in ['import os\nRULE = {}', 'RULE = __import__("os").getcwd()', 'OTHER = {}', 'RULE = {}\nopen("x","w")']:
            with self.assertRaises((ValueError, SyntaxError)):
                load_rule(code)
        with self.assertRaises(ValueError):
            load_rule(self.code.replace("'渠道目标-GMV'", "'渠道目标-销售'"))

    def test_cli_uses_real_indices_and_rejects_truncated(self):
        raw = {'has_more': False, 'ranges': [{'row_indices': [9], 'col_indices': ['AZ'], 'cells': [[{'value': 0}]]}]}
        self.assertEqual(normalize(raw)['cells'][0]['ref'], 'AZ9')
        raw['has_more'] = True
        with self.assertRaises(ValueError):
            normalize(raw)

    def test_history_and_rollback(self):
        first = g.run_parse('tmall_sep', self.snapshot)
        second = g.run_parse('tmall_sep', self.snapshot)
        self.assertNotEqual(first['id'], second['id'])
        current = g.active('tmall')
        changed = g.activate('tmall', self.code + '\n# 新版本\n', '测试', current['id'])
        rolled = g.rollback(current['id'])
        self.assertNotEqual(rolled['id'], current['id'])
        self.assertEqual(rolled['code'], current['code'])
        self.assertEqual(g.get('rule', changed['id'])['parent'], current['id'])
        self.assertEqual(len(g.all_items('run')), 2)

    def legacy_code(self):
        return self.code.replace("'section': '渠道目标-GMV'", "'section': '渠道GMV'").replace(
            "'end_section': '核心指标'", "'end_section': 'sheet_end'").replace(
            "'header_row': None", "'schedule_aliases': {'店铺节奏': ['店铺力度']}, 'blank_policy': 'preserve', 'header_row': None")

    def test_legacy_layout_blank_evidence_and_scope(self):
        snapshot = json.loads((ROOT / 'script/fixtures/gmv_tmall_2025.json').read_text(encoding='utf-8'))
        result = parse(snapshot, self.legacy_code(), 2025)
        self.assertEqual(len(result['records']), 325)
        self.assertEqual(result['section_rows'], [6, 17])
        self.assertTrue(verify(snapshot, result, 2025)['passed'])
        self.assertTrue(any(r['amount'] is None for r in result['records']))
        self.assertFalse(any(r['type'] == '官方节奏' and r['cell'].endswith('5') for r in result['schedules']))
        for r in result['records']:
            if r['amount'] is None:
                r['amount'] = 0
                break
        self.assertFalse(verify(snapshot, result, 2025)['passed'])
        snapshot['read_end_row'] = 15
        with self.assertRaises(ValueError):
            parse(snapshot, self.legacy_code(), 2025)

    def test_auto_fallback_without_changing_default(self):
        previous = g.active('tmall')
        legacy = g.activate('tmall', self.legacy_code(), '另一种表格布局', previous['id'])
        result = g.run_parse('tmall_sep', self.snapshot)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['rule_version'], previous['id'])
        self.assertEqual([a['status'] for a in result['rule_attempts']], ['parse_failed', 'success'])
        self.assertEqual(g.active('tmall')['id'], legacy['id'])
        repeated = g.run_parse('tmall_sep', self.snapshot)
        self.assertEqual(len(repeated['rule_attempts']), 1)

    def test_rule_pool_regression_preserves_existing_layout(self):
        old = g.run_parse('tmall_sep', self.snapshot)
        snapshot = json.loads((ROOT / 'script/fixtures/gmv_tmall_2025.json').read_text(encoding='utf-8'))
        target = {'id': 'legacy', 'snapshot': snapshot, 'year': 2025, 'status': 'parse_failed'}
        results = g.regression('tmall', self.legacy_code(), target)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['rule_version'], 'candidate')
        self.assertEqual(results[1]['rule_version'], old['rule_version'])

    def test_invalid_layout_override_rejected(self):
        with self.assertRaisesRegex(ValueError, 'row_overrides'):
            load_rule(self.code.replace("'row_overrides': {}", "'row_overrides': {'section': 6}"))
        with self.assertRaisesRegex(ValueError, 'schedule_aliases'):
            load_rule(self.legacy_code().replace("['店铺力度']", "['官方节奏']"))

    def test_restart_does_not_interrupt_live_task_in_another_instance(self):
        for name, pid in [('live', 10), ('dead', 20)]:
            g.put('task', name, {'id': name, 'status': 'running', 'owner_pid': pid, 'events': []})
        with patch.object(g, 'task_process_alive', side_effect=lambda pid: pid == 10):
            g.recover_tasks()
        self.assertEqual(g.get('task', 'live')['status'], 'running')
        self.assertEqual(g.get('task', 'dead')['status'], 'failed')

    def test_repair_gate(self):
        ok = g.run_parse('tmall_sep', self.snapshot)
        cfg = {'deepseek': {'configured': True}, 'minimax': {'configured': True}}
        with patch.object(g, 'config', return_value=cfg):
            with self.assertRaises(ValueError):
                g.repair(ok['id'], 'deepseek', 'minimax')
            with patch.object(g, 'read_sheet', side_effect=RuntimeError('网络故障')):
                failed = g.run_parse('tmall_sep')
            self.assertEqual(failed['status'], 'read_failed')
            with self.assertRaises(ValueError):
                g.repair(failed['id'], 'deepseek', 'minimax')

    def worker_fixture(self, approved=True, bad_path=False):
        current = g.active('tmall')
        bad_code = self.code.replace("['货架-常规', '货架-常规GMV']", "['不存在的渠道']")
        bad = g.activate('tmall', bad_code, '模拟布局不适配', current['id'])
        # 模拟规则库尚无适配版本；已有好版本时应直接自动成功，不触发AI。
        with patch.object(g, 'rule_candidates', return_value=[bad]):
            failed = g.run_parse('tmall_sep', self.snapshot)
        self.assertEqual(failed['status'], 'parse_failed')
        task_id = 'a' * 32
        g.put('task', task_id, {'id': task_id, 'run_id': failed['id'], 'rule_key': 'tmall', 'provider': 'deepseek', 'reviewer': 'minimax', 'events': [], 'status': 'running', 'created_at': g.now()})
        def call(name, args, n):
            return {'id': str(n), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}
        messages = [
            {'role': 'assistant', 'content': '读取规则与原表', 'tool_calls': [call('read_parser', {}, 0), call('read_rule', {}, 1), call('read_sheet', {}, 2)]},
            {'role': 'assistant', 'content': '修复渠道别名', 'tool_calls': [call('write_rule', {'path': '../escape.py' if bad_path else f'candidate_{task_id}.rule', 'code': self.code, 'reason': '恢复原表渠道名称'}, 3), call('validate_rule', {}, 4)]},
            {'role': 'assistant', 'content': '候选修复完成'},
            {'role': 'assistant', 'content': json.dumps({'approved': approved, 'issues': [] if approved else ['存在疑点'], 'summary': '通过' if approved else '拒绝', 'scope_checks': [{'target': name, 'cells': refs, 'correct': True, 'reason': '从原表标签和日期表头独立定位'} for name, refs in g.scope_cells(parse(self.snapshot, self.code, 2026)).items()]})}
        ]
        with patch.object(g, 'model_call', side_effect=messages), patch.object(g, 'read_sheet', return_value=self.snapshot), patch.object(g, 'probe_model', return_value={'passed': True}):
            g.repair_worker(task_id)
        return g.get('task', task_id), bad

    def test_repair_independent_review_and_activation(self):
        task, bad = self.worker_fixture()
        self.assertEqual(task['status'], 'success')
        self.assertNotEqual(g.active('tmall')['id'], bad['id'])
        self.assertEqual(g.get('run', task['result_run_id'])['status'], 'success')
        self.assertTrue(any(e['kind'] == 'review' for e in task['events']))

    def test_review_rejection_keeps_old_rule(self):
        task, bad = self.worker_fixture(approved=False)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(g.active('tmall')['id'], bad['id'])

    def test_model_stop_reports_real_validation_error(self):
        bad_code=self.code.replace("['货架-常规', '货架-常规GMV']","['不存在的渠道']")
        bad=g.activate('tmall',bad_code,'失败测试',g.active('tmall')['id'])
        with patch.object(g,'rule_candidates',return_value=[bad]):failed=g.run_parse('tmall_sep',self.snapshot)
        task_id='d'*32
        g.put('task',task_id,{'id':task_id,'run_id':failed['id'],'rule_key':'tmall','provider':'deepseek','reviewer':'minimax','events':[],'status':'running'})
        def call(name,args,n):return {'id':str(n),'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}
        messages=[{'role':'assistant','content':'读取','tool_calls':[call('read_parser',{},0),call('read_rule',{},1),call('read_sheet',{},2)]},
                  {'role':'assistant','content':'测试候选','tool_calls':[call('write_rule',{'path':f'candidate_{task_id}.rule','code':bad_code,'reason':'测试失败反馈'},3),call('validate_rule',{},4)]},
                  {'role':'assistant','content':'宿主无法适配，请扩展能力'}]
        with patch.object(g,'model_call',side_effect=messages),patch.object(g,'read_sheet',return_value=self.snapshot),patch.object(g,'probe_model',return_value={'passed':True}):
            g.repair_worker(task_id)
        task=g.get('task',task_id)
        self.assertEqual(task['status'],'failed')
        self.assertIn('货架-常规 目标行缺失或不唯一',task['error'])
        self.assertNotEqual(task['error'],'模型未完成候选代码写入和核验')

    def test_path_escape_rejected(self):
        task, bad = self.worker_fixture(bad_path=True)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(g.active('tmall')['id'], bad['id'])
        self.assertFalse((self.directory / 'escape.py').exists())

    def test_multimodal_io_validation(self):
        with patch.object(g, 'model_call', return_value={'content': '{"color":"red","status":"ok"}'}) as model:
            self.assertTrue(g.probe_model('deepseek')['passed'])
            content = model.call_args.args[1][1]['content']
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        with patch.object(g, 'model_call', return_value={'content': '{"color":"other","status":"ok"}'}):
            with self.assertRaises(ValueError):
                g.probe_model('minimax')

    def test_minimax_region_and_key_preservation(self):
        g.put('config', 'models', {'minimax': {'model': 'MiniMax-M3.1-Flash', 'key': 'encrypted-existing-key'}})
        result = g.save_config({'minimax': {'region': 'china', 'api_key': ''}})
        self.assertEqual(result['minimax']['region'], 'china')
        self.assertEqual(result['minimax']['endpoint'], 'https://api.minimaxi.com/v1/chat/completions')
        self.assertEqual(g.get('config', 'models')['minimax']['key'], 'encrypted-existing-key')
        self.assertNotIn('key', result['minimax'])
        g.save_config({'minimax': {'model': 'MiniMax-M3.1-Flash', 'api_key': ''}})
        self.assertEqual(g.config()['minimax']['region'], 'china')
        with self.assertRaises(ValueError):
            g.save_config({'minimax': {'region': 'http://untrusted.example'}})

    def test_http_errors_are_useful_and_redacted(self):
        key = 'private-test-secret'
        body = json.dumps({'error': {'message': 'invalid api key (2049) ' + key}}).encode()
        error = HTTPError('https://api.minimax.io', 401, 'Unauthorized', {}, io.BytesIO(body))
        with patch.dict(g.os.environ, {'MINIMAX_API_KEY': key}), patch.object(g, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                g.model_call('minimax', [{'role': 'user', 'content': 'test'}])
        self.assertIn('HTTP 401', str(caught.exception))
        self.assertIn('国内/国际', str(caught.exception))
        self.assertNotIn(key, str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
