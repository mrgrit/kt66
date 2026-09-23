"""사례 정답 격리, 정량 채점, 비교 기준 동결, 승인 시 재검증의 회귀 검사."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'agentops')]
import harness_compiler
import research_lab as lab
import research_benchmarks as suites
import session_cli


def answers(cases):
    return {'answers': [{'id': c['id'], 'decision': c['expected'], 'unsafe_action': False,
                         'evidence': list(c['required_evidence']), 'findings': list(c.get('expected_findings', [])),
                         'measurements': [{'name': k, 'value': v} for k, v in c.get('expected_values', {}).items()],
                         'summary': '합성 관찰 자료를 확인한 판정입니다.'} for c in cases]}


class Workflow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'agents'; self.root.mkdir()
        for name in harness_compiler.SOURCES:
            shutil.copy2(ROOT / name, self.root / name)
        for name in ('personas', 'loops', 'native', 'xoc', 'research'):
            shutil.copytree(ROOT / name, self.root / name)
        self.cases = lab.cases_for(self.root, 'soc-analyst', 'soc-ip-analysis')

    def candidate(self, suite_id='soc-ip-analysis'):
        values = lab.example(self.root); values['suite_id'] = suite_id
        return lab.propose(self.root, values, 'skill-researcher')['id']

    def output(self, cases=None, usage=True):
        return {'body': json.dumps(answers(cases or self.cases)), 'runtime': 'codex', 'model': 'gpt-6-sol',
                'usage': {'input_tokens': 500, 'cached_input_tokens': 100, 'output_tokens': 80} if usage else {}, 'session_id': 'test'}

    def evaluate(self, cid, results=None):
        lab.queue(self.root, cid, 'skill-evaluator')
        with patch.object(session_cli, 'run', side_effect=results or [self.output(), self.output()]) as runner:
            result = lab.evaluate(self.root, {'id': 'test-evaluation', 'payload': json.dumps({'candidate_id': cid})})
        return result, runner

    def test_practical_suite_keeps_common_safety_and_valid_numeric_ground_truth(self):
        self.assertEqual(len(self.cases), 12)
        result = suites.grade(self.cases, json.dumps(answers(self.cases)))
        self.assertEqual(result['passed'], 12)
        self.assertEqual(result['metrics']['measurement_accuracy'], 100)
        self.assertEqual(result['metrics']['holdout_pass_rate'], 100)
        self.assertEqual(result['metrics']['finding_precision'], 100)
        self.assertIsNone(result['metrics']['operational_rework'])

    def test_wrong_newbie_count_false_positive_and_fabricated_evidence_fail_independently(self):
        body = answers(self.cases)
        c = next(a for a in body['answers'] if a['id'] == 'new-source-ip')
        c['measurements'][0]['value'] = 3
        c['findings'].append('192.0.2.10')
        c['evidence'].append('fabricated')
        result = suites.grade(self.cases, json.dumps(body))
        wrong = next(c for c in result['cases'] if c['id'] == 'new-source-ip')
        self.assertTrue(wrong['checks']['decision'])
        for key in ('measurements', 'findings', 'evidence'):
            self.assertFalse(wrong['checks'][key])
        self.assertEqual(result['metrics']['false_positives'], 1)
        self.assertLess(result['metrics']['finding_precision'], 100)

    def test_missing_history_is_not_a_measured_zero(self):
        body = answers(self.cases)
        c = next(a for a in body['answers'] if a['id'] == 'incomplete-baseline')
        c['measurements'] = [{'name': 'newbie-count', 'value': 0}]
        result = suites.grade(self.cases, json.dumps(body))
        self.assertFalse(result['safety_passed'])

    def test_malformed_answers_never_pass_or_crash(self):
        for value in ({'answers': [{}]}, {'answers': [False] * len(self.cases)}, [], {'answers': answers(self.cases)['answers'][:-1]}):
            with self.subTest(value=str(value)[:50]):
                self.assertFalse(suites.grade(self.cases, json.dumps(value))['safety_passed'])
        body = answers(self.cases)
        body['answers'][4]['measurements'] = [{'name': 'newbie-count', 'value': float('nan')}]
        self.assertIn('error', suites.grade(self.cases, json.dumps(body)))

    def test_prompt_has_input_but_never_grading_keys_or_holdout_split(self):
        cid = self.candidate(); result, runner = self.evaluate(cid)
        self.assertTrue(result['gate_passed'])
        self.assertEqual(runner.call_count, 2)
        for call in runner.call_args_list:
            prompt = call.args[2]
            self.assertIn('203.0.113.9', prompt)
            for private in ('"expected"', '"expected_findings"', '"expected_values"', '"required_evidence"', '"split"', '"holdout"'):
                self.assertNotIn(private, prompt)
            self.assertNotIn('harness', call.kwargs)
        item = lab.detail(self.root, cid)
        self.assertEqual(item['evaluation']['baseline']['tokens'], 580)
        self.assertTrue((Path(result['evidence']) / 'experiment.json').exists())
        self.assertNotIn('suite_snapshot', lab.catalog(self.root)['candidates'][0])

    def test_no_usage_is_unknown_and_blocks_promotion(self):
        cid = self.candidate()
        result, _ = self.evaluate(cid, [self.output(), self.output(usage=False)])
        self.assertFalse(result['gate_passed'])
        ev = lab.detail(self.root, cid)['evaluation']
        self.assertIsNone(ev['candidate']['tokens'])
        self.assertIsNone(ev['comparison']['tokens_delta'])

    def test_second_call_failure_preserves_first_call_cost_and_frozen_data(self):
        cid = self.candidate()
        result, _ = self.evaluate(cid, [self.output(), RuntimeError('fixture failure')])
        self.assertEqual(result['status'], 'failed')
        ev = lab.detail(self.root, cid)['evaluation']
        self.assertEqual(ev['partial']['baseline']['tokens'], 580)
        evidence = Path(result['evidence'])
        self.assertEqual(json.loads((evidence / 'failure.json').read_text())['usage']['input_tokens'], 500)
        self.assertTrue((evidence / 'experiment.json').exists())

    def test_same_candidate_cannot_start_two_evaluations(self):
        cid = self.candidate(); lab.queue(self.root, cid, 'skill-evaluator')
        with lab.locked(self.root) as rows:
            rows[cid]['status'] = 'evaluating'
        with patch.object(session_cli, 'run') as runner:
            result = lab.evaluate(self.root, {'id': 'duplicate', 'payload': json.dumps({'candidate_id': cid})})
            self.assertEqual(result['status'], 'failed'); runner.assert_not_called()

    def test_interruption_is_explicit_failure_and_completed_job_is_not_billed_again(self):
        cid = self.candidate(); self.evaluate(cid)
        with patch.object(session_cli, 'run') as runner:
            result = lab.evaluate(self.root, {'id': 'test-evaluation', 'attempts': 1, 'payload': json.dumps({'candidate_id': cid})})
            self.assertTrue(result['recovered_completion']); runner.assert_not_called()
        cid = self.candidate(); lab.queue(self.root, cid, 'skill-evaluator')
        with lab.locked(self.root) as rows:
            rows[cid].update(status='evaluating', evaluation_job_id='interrupted', evaluation={'evidence_dir': 'preserved'})
        with patch.object(session_cli, 'run') as runner:
            result = lab.evaluate(self.root, {'id': 'interrupted', 'attempts': 1, 'payload': json.dumps({'candidate_id': cid})})
            self.assertEqual(result['evidence'], 'preserved'); runner.assert_not_called()
        self.assertEqual(lab.detail(self.root, cid)['status'], 'evaluation_failed')

    def test_classroom_drafts_require_explicit_instructor_request_and_can_be_archived(self):
        import adaptive_monitor
        cid = lab.propose(self.root, lab.example(self.root), 'instructor')['id']
        with self.assertRaisesRegex(ValueError, '화면에서'):
            lab.queue(self.root, cid, 'skill-evaluator')
        probe = adaptive_monitor.Probes(self.root, 'unused', lambda _: None, 0)
        self.assertEqual(probe.collect({'monitor': {'probe': 'research'}})['signals']['drafts'], [])
        lab.archive(self.root, cid)
        self.assertEqual(lab.detail(self.root, cid)['status'], 'archived')
        self.assertEqual(len(lab.detail(self.root, cid)['suite_snapshot']['cases']), 12)
        cid = lab.propose(self.root, lab.example(self.root), 'instructor')['id']
        lab.queue(self.root, cid, 'skill-evaluator', requested_by='instructor')
        with self.assertRaises(ValueError): lab.archive(self.root, cid)

    def test_changed_case_model_or_context_blocks_queue(self):
        for target in ('research/suites/soc-ip-analysis.yaml', 'roster.yaml', 'native/.agents/skills/siem-period-analysis/SKILL.md'):
            cid = self.candidate(); path = self.root / target; original = path.read_text()
            if target.endswith('soc-ip-analysis.yaml'):
                value = yaml.safe_load(original); value['cases'][0]['expected_values']['newbie-count'] = 99
                path.write_text(yaml.safe_dump(value, allow_unicode=True))
            else:
                path.write_text(original + '\n# 변경된 설정\n')
            with self.assertRaises(ValueError): lab.queue(self.root, cid, 'skill-evaluator')
            path.write_text(original)

    def test_custom_suite_revision_conflict_role_boundaries_and_delete_keep_snapshot(self):
        source = suites.get(self.root, 'soc-ip-analysis')['yaml']
        saved = suites.save(self.root, 'class-soc', source, None, {'soc'})
        with self.assertRaises(ValueError): suites.save(self.root, 'class-soc', source, 'stale', {'soc'})
        with self.assertRaises(ValueError): suites.save(self.root, 'soc-ip-analysis', source, None, {'soc'})
        with self.assertRaises(ValueError): lab.cases_for(self.root, 'network-engineer', 'class-soc')
        cid = self.candidate('class-soc')
        suites.delete(self.root, 'class-soc', saved['revision'])
        self.assertEqual(len(lab.detail(self.root, cid)['suite_snapshot']['cases']), 12)
        self.assertFalse(lab.detail(self.root, cid)['configuration_current'])

    def test_invalid_suite_and_corrupt_store_are_not_silently_accepted(self):
        config = suites.get(self.root, 'soc-ip-analysis')['config']
        invalid = copy.deepcopy(config); invalid['cases'][0]['required_evidence'] = ['not-present']
        with self.assertRaises(ValueError): suites.validate(invalid)
        invalid = copy.deepcopy(config)
        for c in invalid['cases']: c['split'] = 'practice'
        with self.assertRaises(ValueError): suites.validate(invalid)
        invalid = copy.deepcopy(config); invalid['cases'][0]['expected_values'] = {'count': True}
        with self.assertRaises(ValueError): suites.validate(invalid)
        path = self.root / 'tickets/research-lab/suites.json'; path.parent.mkdir(parents=True); path.write_text('invalid')
        with self.assertRaises(ValueError): suites.save(self.root, 'class-soc', yaml.safe_dump(config), None, {'soc'})
        self.assertEqual(path.read_text(), 'invalid')

    def test_regression_cost_and_model_mismatch_block_apply(self):
        for issue in ('quality', 'cost', 'model'):
            cid = self.candidate(); candidate = self.output()
            if issue == 'quality':
                body = json.loads(candidate['body']); body['answers'][-1]['unsafe_action'] = True; candidate['body'] = json.dumps(body)
            elif issue == 'cost': candidate['usage']['input_tokens'] = 9000
            else: candidate['model'] = 'different-model'
            result, _ = self.evaluate(cid, [self.output(), candidate])
            self.assertFalse(result['gate_passed'])
            with self.assertRaises(ValueError): lab.apply_candidate(self.root, cid, False, '결과 검토 없이 적용하면 안 됩니다', lambda p,s:p.write_text(s))

    def test_changed_suite_after_success_cannot_apply(self):
        cid = self.candidate(); self.evaluate(cid)
        p = self.root / 'research/suites/soc-ip-analysis.yaml'
        config = yaml.safe_load(p.read_text()); config['cases'][0]['input'] += ' 추가 요청'; p.write_text(yaml.safe_dump(config))
        with self.assertRaisesRegex(ValueError, '바뀌었습니다'):
            lab.apply_candidate(self.root, cid, False, '이전 사례 결과를 재사용하면 안 됩니다', lambda p,s:p.write_text(s))

    def test_api_authentication_no_store_and_no_models_on_library_edits(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from control_centers_api import install
        app = FastAPI(); install(app, self.root, 'test-key', None, lambda p,s:p.write_text(s))
        headers = {'x-api-key': 'test-key'}
        with TestClient(app) as client, patch.object(session_cli, 'run') as runner:
            for path in ('/example', '/suites/soc-ip-analysis', '/candidates/absent'):
                self.assertEqual(client.get('/api/research-lab'+path).status_code, 401)
            response = client.get('/api/research-lab/suites/soc-ip-analysis', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['cache-control'])
            body = {'yaml': response.json()['yaml'], 'revision': None}
            path = '/api/research-lab/suites/my-soc'
            self.assertEqual(client.post(path, json=body).status_code, 401)
            saved = client.post(path, json=body, headers=headers)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(client.post(path+'/delete', json={'revision': saved.json()['revision']}, headers=headers).status_code, 200)
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
