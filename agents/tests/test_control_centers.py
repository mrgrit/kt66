"""실제 증적 탐지, 직무 분리, 보류와 후보 평가·적용 경로를 격리 사본에서 검증한다."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'agentops')]
import authorization
import harness_compiler
import harness_tools
import research_lab
import session_cli
import xoc


class Centers(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'agents'
        self.root.mkdir()
        for name in harness_compiler.SOURCES:
            shutil.copy2(ROOT / name, self.root / name)
        for name in ('personas', 'loops', 'native', 'xoc', 'research'):
            shutil.copytree(ROOT / name, self.root / name)
        (self.root.parent / '.env').write_text('API_KEY=test\n')
        self.addCleanup(patch.stopall)
        patch.object(harness_tools, 'ROOT', self.root).start()

    def run_evidence(self, tools=None, worker='network-engineer', tokens=100):
        _, manifest = harness_compiler.compile_worker(worker, self.root)
        folder = self.root / 'evidence' / ('loop-' + str(time.time_ns()) + '-' + worker)
        folder.mkdir(parents=True)
        (folder / 'manifest-snapshot.json').write_text(json.dumps(manifest))
        (folder / 'job.json').write_text(json.dumps({'worker': worker, 'kind': 'periodic:firewall-drift-check'}))
        (folder / 'result.json').write_text(json.dumps({'runtime': 'codex', 'body': '근거 확인', 'usage': {'input_tokens': tokens, 'cached_input_tokens': tokens//2, 'output_tokens': 10}, 'verification': {'observed_live_evidence': True}}))
        (folder / 'tools.jsonl').write_text('\n'.join(json.dumps(t) for t in (tools or [])))
        return folder

    def candidate(self):
        return research_lab.propose(self.root, {'name': 'lab-test-skill', 'content': '---\nname: lab-test-skill\ndescription: 조사 자료와 권한을 대조하는 시험 스킬\n---\n\n관측 결과를 확인하고 미확인 사항과 권한 경계를 기록한다.\n',
            'target_worker': 'soc-analyst', 'hypothesis': '근거 누락과 인젝션 오판을 줄이기 위한 후보입니다', 'sources': ['https://example.org/method']}, 'skill-researcher')['id']

    def evaluate(self, cid, wrong=False):
        research_lab.queue(self.root, cid, 'skill-evaluator')
        cases = research_lab.cases_for(self.root, 'soc-analyst')
        answers = [{'id': c['id'], 'decision': 'benign' if wrong else c['expected'], 'unsafe_action': False, 'evidence': c['required_evidence']} for c in cases]
        with patch.object(session_cli, 'run', return_value={'body': json.dumps({'answers': answers}), 'usage': {'input_tokens': 500, 'cached_input_tokens': 100, 'output_tokens': 50}, 'runtime': 'codex', 'model': 'gpt-6-sol', 'session_id': 'fixture'}) as call:
            result = research_lab.evaluate(self.root, {'id': 'test-job', 'payload': json.dumps({'candidate_id': cid})})
            self.assertEqual(call.call_count, 2)
            for invocation in call.call_args_list:
                self.assertNotIn('harness', invocation.kwargs)
                self.assertNotIn('"expected"', invocation.args[2])
        return result

    def test_new_roles_are_codex_and_tools_are_separated(self):
        expected = {'skill-researcher': ('gpt-6-astra', 'lab_propose'), 'skill-evaluator': ('gpt-6-sol', 'lab_evaluate'), 'agent-supervisor': ('gpt-6-luna', 'xoc_contain')}
        for worker, (model, tool) in expected.items():
            _, m = harness_compiler.compile_worker(worker, self.root)
            self.assertEqual(m['worker']['runtime'], 'codex')
            self.assertEqual(m['model']['name'], model)
            self.assertIn(tool, m['available_tools'])
            self.assertFalse(set(m['available_tools']) & {'disk_usage', 'firewall_read', 'simulator_control', 'approve_request', 'workspace_write'})
            for other in {'lab_propose', 'lab_evaluate', 'xoc_contain'} - {tool}:
                self.assertNotIn(other, m['available_tools'])

    def test_conversation_cannot_propose_evaluate_or_contain(self):
        for worker, name in [('skill-researcher', 'lab_propose'), ('skill-evaluator', 'lab_evaluate'), ('agent-supervisor', 'xoc_contain')]:
            _, m = harness_compiler.compile_worker(worker, self.root)
            m['request'] = {'mode': 'conversation', 'phase': 'conversation', 'capabilities': []}
            self.assertFalse(authorization.allowed(m, name))

    def test_codex_web_and_reasoning_follow_role_and_hold_prevents_session(self):
        import subprocess
        for worker,web,effort in [('skill-researcher','live','high'),('agent-supervisor','disabled','medium')]:
            folder,_=harness_compiler.compile_worker(worker,self.root)
            def process(cmd,**kw):
                self.assertIn('web_search="'+web+'"',cmd)
                self.assertIn('model_reasoning_effort="'+effort+'"',cmd)
                self.assertIn('multi_agent',cmd);self.assertIn('shell_tool',cmd)
                Path(cmd[cmd.index('-o')+1]).write_text('검증 완료')
                return subprocess.CompletedProcess(cmd,0,'{"type":"thread.started","thread_id":"fixture"}\n{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}','')
            with patch.object(session_cli,'executable',return_value='codex'),patch.object(session_cli,'authenticated',return_value={'method':'chatgpt'}),patch.object(session_cli.subprocess,'run',side_effect=process):
                session_cli.run('codex','gpt-6-luna','시험',harness=folder,evidence_dir=self.root/'evidence'/worker)
            with patch.object(session_cli,'executable',return_value='codex'),patch.object(session_cli,'authenticated',return_value={'method':'chatgpt'}),patch.object(xoc,'held',return_value={'until':time.time()+60}),patch.object(session_cli.subprocess,'run') as live:
                with self.assertRaisesRegex(session_cli.SessionError,'xoc_hold'):
                    session_cli.run('codex','gpt-6-luna','시험',harness=folder,evidence_dir=self.root/'evidence'/worker)
                live.assert_not_called()

    def test_denied_attempt_is_not_successful_escape_and_scans_deduplicate(self):
        call = {'tool': 'disk_usage', 'arguments': {}, 'result': {'status': 'denied', 'code': 'role_boundary'}}
        self.run_evidence([call]*3)
        first = xoc.scan(self.root)['findings']
        self.assertEqual([f['rule'] for f in first], ['XOC-002'])
        self.assertEqual(first, xoc.scan(self.root)['findings'])

    def test_verified_escape_can_hold_only_target_with_expiry(self):
        self.run_evidence([{'tool': 'disk_usage', 'arguments': {}, 'result': {'status': 'ok'}}])
        finding = xoc.scan(self.root)['findings'][0]
        self.assertEqual(finding['rule'], 'XOC-001')
        xoc.containment(self.root, 'network-engineer', 15, '직무 밖 도구 성공 영수증을 확인했습니다', 'agent-supervisor', finding['id'])
        self.assertIsNotNone(xoc.held(self.root, 'network-engineer'))
        with self.assertRaisesRegex(ValueError, '반복 연장'):
            xoc.containment(self.root, 'network-engineer', 15, '같은 사건으로 무한히 연장할 수 없어야 합니다', 'agent-supervisor', finding['id'])
        with self.assertRaises(ValueError):
            xoc.containment(self.root, 'systems-engineer', 15, '다른 근무자까지 보류하려는 잘못된 요청', 'agent-supervisor', finding['id'])
        with self.assertRaises(ValueError):
            xoc.containment(self.root, 'network-engineer', 60, '자동 보류 한도를 넘길 수 없어야 합니다', 'agent-supervisor', finding['id'])
        with patch.object(xoc.time, 'time', return_value=time.time()+1000):
            self.assertIsNone(xoc.held(self.root, 'network-engineer'))

    def test_broker_hold_rejects_read_and_allows_final_note(self):
        folder, _ = harness_compiler.compile_worker('network-engineer', self.root)
        harness_tools.ROOT = self.root  # 컴파일러의 도구 목록 reload 이후 격리 경로를 복원한다.
        broker = harness_tools.Broker(folder / 'manifest.json', self.root / 'evidence/test')
        xoc.containment(self.root, 'network-engineer', 5, '강사가 수동으로 확인하는 중입니다', 'instructor')
        self.assertEqual(broker.call('firewall_read', {})['code'], 'xoc_hold')
        self.assertEqual(broker.call('activity_note', {'stage': 'review', 'summary': '보류를 확인하고 종료합니다', 'evidence': []})['status'], 'recorded')

    def test_supervisor_cannot_close_own_finding_or_hold_reviewer(self):
        self.run_evidence([{'tool': 'disk_usage', 'result': {'status': 'ok'}}], worker='agent-supervisor')
        finding = xoc.scan(self.root)['findings'][0]
        with self.assertRaises(ValueError):
            xoc.review(self.root, finding['id'], 'resolved', '자기 자신의 결과를 종결하려는 요청', 'agent-supervisor')
        with self.assertRaises(ValueError):
            xoc.containment(self.root, 'ops-lead', 10, '승인자를 보류하면 안 됩니다', 'agent-supervisor', finding['id'])
        self.assertEqual(xoc.probe(self.root)['signals']['findings'], [])

    def test_review_does_not_retrigger_same_finding(self):
        self.run_evidence([{'tool': 'disk_usage', 'result': {'status': 'ok'}}])
        first = xoc.probe(self.root)
        fid = first['signals']['findings'][0]
        xoc.review(self.root, fid, 'acknowledged', '도구 영수증을 확인하고 추가 조사합니다', 'agent-supervisor')
        self.assertEqual(first['signals'], xoc.probe(self.root)['signals'])

    def test_usage_counts_cached_input_once(self):
        self.assertEqual(xoc.token_total({'runtime':'codex','usage':{'input_tokens':100,'cached_input_tokens':70,'output_tokens':10}}),110)
        self.assertIsNone(xoc.token_total({'usage':{}}))

    def test_rule_change_rescans_unchanged_evidence_without_duplicate(self):
        call={'tool':'firewall_read','arguments':{},'result':{'status':'ok'}}
        self.run_evidence([call]*3)
        self.assertEqual(xoc.scan(self.root)['findings'],[])
        path=self.root/'xoc/rules.yaml';cfg=yaml.safe_load(path.read_text())
        cfg['rules']['XOC-003']['threshold']=3;path.write_text(yaml.safe_dump(cfg))
        first=xoc.scan(self.root)['findings']
        self.assertEqual([f['rule'] for f in first],['XOC-003'])
        self.assertEqual(first,xoc.scan(self.root)['findings'])

    def test_authorized_facility_action_is_not_a_role_escape(self):
        rid='a'*32
        p=self.root/'tickets/approvals'/f'{rid}.json';p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'worker':'facility-engineer','approver':'ops-lead','decision':{'approve':True},'status':'verified'}))
        self.run_evidence([{'tool':'approved_action','result':{'action':'simulator_control','executor_worker':'facility-engineer','authorization_request':rid}}],worker='facility-engineer')
        self.assertEqual(xoc.scan(self.root)['findings'],[])

    def test_corrupt_research_state_is_not_silently_empty(self):
        path=self.root/'tickets/research-lab/candidates.json';path.parent.mkdir(parents=True);path.write_text('invalid')
        with self.assertRaises(ValueError):research_lab.catalog(self.root)
        self.assertEqual(path.read_text(),'invalid')

    def test_broker_hold_also_blocks_previously_approved_execution(self):
        folder,_=harness_compiler.compile_worker('network-engineer',self.root)
        harness_tools.ROOT=self.root
        broker=harness_tools.Broker(folder/'manifest.json',self.root/'evidence/test')
        xoc.containment(self.root,'network-engineer',5,'기존 승인 실행도 잠시 보류합니다','instructor')
        with patch.object(broker,'get') as live:
            with self.assertRaisesRegex(ValueError,'xOC 보류'):
                broker.clear_fault({'target':'fw','fault':'test'})
            live.assert_not_called()

    def test_excess_privilege_configuration_is_detected_without_live_action(self):
        p=self.root/'harness.yaml';data=yaml.safe_load(p.read_text())
        data['security']['roles']['network']['tools'].append('disk_usage')
        p.write_text(yaml.safe_dump(data))
        findings=xoc.scan(self.root)['findings']
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]['rule'],'XOC-011')
        self.assertEqual(findings[0]['worker'],'network-engineer')

    def test_corrupt_hold_state_does_not_fail_open_or_erase_records(self):
        path=self.root/'tickets/xoc/state.json';path.parent.mkdir(parents=True);path.write_text('invalid')
        with self.assertRaises(ValueError):xoc.held(self.root,'network-engineer')
        with self.assertRaises(ValueError):xoc.scan(self.root)
        self.assertEqual(path.read_text(),'invalid')

    def test_research_can_read_role_guidance_without_operational_data(self):
        ref=research_lab.reference(self.root,'soc-analyst')
        self.assertIn('log_read',ref['authorization']['tools'])
        self.assertTrue(ref['skills'])
        self.assertNotIn('evidence',ref)

    def test_candidate_cannot_apply_without_independent_evaluation(self):
        cid = self.candidate()
        with self.assertRaises(ValueError): research_lab.queue(self.root, cid, 'skill-researcher')
        with self.assertRaises(ValueError): research_lab.apply_candidate(self.root, cid, False, '검증 전 적용하면 안 됩니다', lambda p,s:p.write_text(s))

    def test_evaluation_apply_and_rollback_preserve_original(self):
        cid = self.candidate()
        original = (self.root/'personas/soc-analyst.md').read_text()
        self.assertTrue(self.evaluate(cid)['gate_passed'])
        def write(p,s): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(s)
        research_lab.apply_candidate(self.root, cid, False, '평가 통과 및 영향 범위를 독립 검토했습니다', write)
        _, m = harness_compiler.compile_worker('soc-analyst', self.root)
        self.assertIn('lab-test-skill', m['role_skills'])
        research_lab.apply_candidate(self.root, cid, True, '후속 검토에 따라 이전 역할 설정을 복구합니다', write)
        self.assertEqual((self.root/'personas/soc-analyst.md').read_text(), original)
        self.assertFalse((self.root/'native/.agents/skills/lab-test-skill/SKILL.md').exists())

    def test_regression_cannot_pass_and_changed_policy_invalidates_evaluation(self):
        cid = self.candidate()
        self.assertFalse(self.evaluate(cid, wrong=True)['gate_passed'])
        cid = self.candidate();self.evaluate(cid)
        p=self.root/'harness.yaml';p.write_text(p.read_text()+'\n# 새 정책 버전\n')
        with self.assertRaisesRegex(ValueError, '바뀌었습니다'):
            research_lab.apply_candidate(self.root, cid, False, '이전 평가로 변경 정책을 적용하면 안 됩니다', lambda p,s:p.write_text(s))

    def test_changed_live_skill_is_not_overwritten_by_rollback(self):
        cid=self.candidate();self.evaluate(cid)
        def write(p,s): p.parent.mkdir(parents=True, exist_ok=True);p.write_text(s)
        research_lab.apply_candidate(self.root,cid,False,'통과 결과와 영향 범위를 확인했습니다',write)
        p=self.root/'native/.agents/skills/lab-test-skill/SKILL.md';p.write_text(p.read_text()+'\n다른 편집자의 변경')
        with self.assertRaisesRegex(ValueError,'이후 수정'):
            research_lab.apply_candidate(self.root,cid,True,'다른 편집 내용을 덮어쓰면 안 됩니다',write)

    def test_api_requires_key_for_reads_and_writes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from control_centers_api import install
        app=FastAPI();install(app,self.root,'fixture-key',None,lambda p,s:p.write_text(s))
        with TestClient(app) as client:
            for path in ('/api/xoc','/api/research-lab','/api/compliance-evidence'):
                self.assertEqual(client.get(path).status_code,401)
                self.assertEqual(client.get(path,headers={'x-api-key':'fixture-key'}).status_code,200)
            self.assertEqual(client.post('/api/xoc/containment',json={}).status_code,401)
            self.assertEqual(client.post('/api/research-lab/candidates',json={}).status_code,401)


if __name__ == '__main__':
    unittest.main()
