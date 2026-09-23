"""정밀 관제의 연결 관계·누락 의미·비밀값·기존 인덱스 이행을 검증한다."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'observability'), str(ROOT / 'agents')]
from projection import document, finding_document, control_document, safe_document
from enrichment import request_documents, grant_documents, change_approval_documents
from schema import mappings, VERSION, PROJECTION_VERSION
from collector import Collector


def assert_mapping(test, value, mapping):
    if isinstance(value, list):
        for row in value:
            assert_mapping(test, row, mapping)
    elif value is None:
        return
    elif isinstance(value, dict):
        test.assertIn('properties', mapping)
        for key, item in value.items():
            test.assertIn(key, mapping['properties'], key)
            assert_mapping(test, item, mapping['properties'][key])
    else:
        kind = mapping['type']
        test.assertIsInstance(value, {'long': int, 'boolean': bool}.get(kind, str))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.meta = dict(worker='soc-analyst', role='soc', run_id='loop-1790145000000000000-soc-analyst',
            started_at=1790145000, trigger='user_request', job=dict(job_id='job-1', attempt=2,
                retry_of='/agents/evidence/loop-old', retry_reason={'error': 'timeout'}, observation={}),
            manifest=dict(version='hv1', persona='조사 지침', policy={'constrain': {}},
                worker={'runtime': 'codex'}, model={'name': 'test', 'reasoning_effort': 'high'},
                role_skills={'soc-risk': {'sha256': 's1'}}, native={'source_hashes': {'AGENTS.md': 'v1'}},
                request=dict(id='req-1', task_id='task-2', revision=1, phase='work', depends_on=['task-1'],
                    parent_run_id='loop-parent', parent_call_id='call-parent', resumed_from_task_id='task-old')),
            loaded={'instructions_sha256': 'prompt-hash'})

    def project(self, name, raw):
        doc = document('evidence/' + self.meta['run_id'] + '/' + name, raw, self.meta, 1790145005, {}, line=1)[2]
        assert_mapping(self, doc, mappings())
        return doc

    def test_correlation_has_request_task_retry_parent_and_config(self):
        d = self.project('activity.jsonl', {'type': 'agent.plan', 'data': {'summary': '로그 조사', 'steps': ['수집', '교차 확인'], 'evidence': ['ev1']}})
        self.assertEqual(d['schema_version'], VERSION)
        self.assertEqual(d['correlation']['trace_id'], 'req-1')
        self.assertEqual(d['correlation']['depends_on_task_ids'], ['task-1'])
        self.assertEqual(d['correlation']['retry_of_run_id'], 'loop-old')
        self.assertEqual(d['correlation']['parent_call_id'], 'call-parent')
        self.assertEqual(d['config']['assigned_skills'], ['soc-risk'])
        self.assertEqual(d['explanation']['steps'], ['수집', '교차 확인'])
        self.assertNotIn('조사 지침', json.dumps(d, ensure_ascii=False))

    def test_tool_resources_are_paired_and_secrets_are_not_exported(self):
        raw = dict(id='call-1', tool='workspace_read', at=1790145001, started_at=1790145000, duration_ms=1000,
            arguments={'path': 'a', 'password': 'never-export-this'}, result={'status': 'ok', 'body': 'private-output'},
            authorization={'mode': 'ask', 'permission': 'user_request', 'role': 'soc', 'user_decisions': [
                {'request': 'permission-1', 'decision': 'once', 'permission': 'user_request', 'actor': 'instructor', 'decided_at': 1790144999}]},
            accesses=[{'path': '/a', 'operation': 'read'}, {'path': '/b', 'operation': 'write'}])
        d = safe_document(self.project('tools.jsonl', raw))
        self.assertEqual(mappings()['properties']['resource']['type'], 'nested')
        self.assertEqual([(x['id'], x['operation']) for x in d['resource'][:2]], [('/a','read'),('/b','write')])
        self.assertEqual(d['approval'][0]['actor'], 'instructor')
        self.assertEqual(d['approval_decisions'], ['once'])
        self.assertEqual(d['tool']['duration_ms'], 1000)
        self.assertNotIn('never-export-this', json.dumps(d))
        self.assertNotIn('private-output', json.dumps(d))

    def test_denial_is_not_success_and_pending_reference_is_present(self):
        denied = self.project('tools.jsonl', dict(id='x', tool='disk_usage', result={'status':'denied','code':'role_boundary'}, authorization={'mode':'allow'}))
        self.assertEqual(denied['access_policy']['configured_mode'], 'allow')
        self.assertEqual(denied['access_policy']['effective_decision'], 'denied')
        self.assertEqual(denied['error']['code'], 'role_boundary')
        pending = self.project('tools.jsonl', dict(id='p', tool='disk_usage', result={'status':'approval_required','permission_request':'p-1'}))
        self.assertIn('p-1', pending['approval_refs'])
        self.assertEqual(pending['approval_decisions'], ['pending'])
        pending = self.project('tools.jsonl', dict(id='p', tool='simulator_control', result={'status':'approval_pending','request_id':'change-1'}))
        self.assertIn('change-1', pending['approval_refs'])
        change = list(change_approval_documents(dict(id='change-1',created=1790145000,worker='facility-engineer',
            status='execution_failed',decision={'approve':True,'worker':'change-approver','at':1790145001}), 'approvals/change-1.json'))[0][2]
        self.assertEqual(change['approval_decisions'], ['approved'])
        self.assertEqual(change['status'], 'execution_failed')
        assert_mapping(self, change, mappings())

    def test_tokens_have_provider_semantics_without_double_count(self):
        codex = self.project('result.json', dict(runtime='codex', session_id='session-1', usage={'input_tokens':100,'cached_input_tokens':80,'output_tokens':12}))
        self.assertEqual(codex['usage']['total_tokens'], 112)
        self.assertEqual(codex['usage']['cache_read_tokens'], 80)
        claude = self.project('result.json', dict(runtime='claude', usage={'input_tokens':20,'cache_read_input_tokens':100,'cache_creation_input_tokens':30,'output_tokens':3}))
        self.assertEqual(claude['usage']['total_tokens'], 153)
        self.assertEqual(claude['usage']['input_semantics'], 'uncached')
        self.assertEqual(claude['usage']['cost_status'], 'not_measured')

    def test_missing_is_not_zero_or_verified_success(self):
        d = self.project('result.json', {'usage': {}, 'verification': {'observed_live_evidence': False, 'tool_calls': 0}, 'request_outcome': {'status':'blocked'}})
        self.assertNotIn('duration_ms', d['execution'])
        self.assertNotIn('total_tokens', d['usage'])
        self.assertIn('execution.duration_ms', d['coverage']['missing'])
        self.assertFalse(d['quality']['observed_live_evidence'])
        self.assertEqual(d['quality']['task_status'], 'blocked')
        self.assertEqual(d['quality']['verification_basis'], 'receipt_presence')

    def test_rework_is_declared_and_skill_read_is_observed(self):
        d = self.project('activity.jsonl', {'type':'agent.review','data': {'rework_cause':'기간 오류','summary':'다시 조회','evidence':['ev'],'tool_call_id':'c'}})
        self.assertTrue(d['quality']['rework_reported'])
        self.assertEqual(d['assertion'], 'agent_declared')
        d = self.project('tools.jsonl', {'tool':'skill_read','id':'r','result':{'name':'soc-risk','sha256':'actual-hash','content':'private'}})
        self.assertEqual(d['tool']['skill_sha256'], 'actual-hash')
        self.assertNotIn('content', d['tool'])

    def test_findings_and_reviews_keep_actor_distinct_from_subject(self):
        row = dict(id='f1', worker='soc-analyst', run_id=self.meta['run_id'], created_at=1790145000,
            rule='XOC-1', rule_version=2, risk=12, likelihood=3, impact=4, source=['training:ir'], evidence={'tools.jsonl':'sha'})
        d = finding_document(row, self.meta)[2]
        self.assertEqual(d['correlation']['request_id'], 'req-1')
        self.assertEqual(d['detection']['rule_version'], '2')
        review = control_document({'at':1790145001,'actor':'instructor','action':'review','finding_id':'f1'}, row)[2]
        self.assertEqual(review['actor']['id'], 'instructor')
        self.assertEqual(review['subject']['worker'], 'soc-analyst')
        self.assertEqual(review['correlation']['finding_id'], 'f1')
        for doc in (d, review):
            assert_mapping(self, doc, mappings())

    def test_approval_decision_state_and_history_have_stable_ids(self):
        row = dict(id='req-1', created=1790145000, revision=1, title='디스크 조회', status='waiting_input',
            messages=[{'text':'must-not-export'}], permission_requests=[dict(id='p1', created=1790145001,
                task_id='t1', status='deferred', decision='defer', actor='instructor', decided_at=1790145002)],
            events=[dict(at=1790145002,kind='permission_decision',permission_request='p1',decision='defer',actor='instructor')])
        first = list(request_documents(row, 'tickets/requests/req-1/request.json'))
        row['status'] = 'queued'
        second = list(request_documents(row, 'tickets/requests/req-1/request.json'))
        self.assertEqual([x[:2] for x in first], [x[:2] for x in second])
        self.assertNotIn('must-not-export', json.dumps(second))
        for _, _, doc in second:
            assert_mapping(self, doc, mappings())
        revocation = list(grant_documents({'events':[dict(at=1790145004,kind='revoked',grant='g1',actor='instructor')]}, 'permissions.json'))[0][2]
        self.assertEqual(revocation['approval_refs'], ['g1'])
        assert_mapping(self, revocation, mappings())

    def test_mapping_upgrade_updates_existing_indexes_without_deletes(self):
        spec = importlib.util.spec_from_file_location('observer_setup', ROOT / 'observability/setup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        class Client:
            def request(self, method, path, body=None):
                calls.append((method, path, body))
                return {}
        module.configure(Client(), {'username':'writer','password':'test'}, {'username':'reader','password':'test'})
        mapping = next(c for c in calls if c[1].endswith('/_mapping'))
        self.assertEqual(mapping[1], '/kt66-agent-events-*,kt66-agent-findings-v1,kt66-agent-tickets-v1/_mapping')
        self.assertEqual(mapping[2]['properties']['approval']['type'], 'nested')
        self.assertFalse(any(c[0] == 'DELETE' for c in calls))

    def test_upgrade_replays_source_with_same_document_id(self):
        with tempfile.TemporaryDirectory() as td:
            root, state = Path(td) / 'agents', Path(td) / 'state'
            run = root / 'evidence' / self.meta['run_id']
            run.mkdir(parents=True)
            source = json.dumps({'id':'call1','tool':'log_read','at':1790145000}) + '\n'
            (run / 'tools.jsonl').write_text(source)
            c = Collector(root, state, None)
            c.consume(run / 'tools.jsonl', self.meta, {})
            original = c.db.execute('SELECT id FROM outbox').fetchone()[0]
            c.set('projection_version', PROJECTION_VERSION - 1)
            c.db.commit()
            c.db.close()
            c = Collector(root, state, None)
            self.assertEqual(c.db.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)
            c.consume(run / 'tools.jsonl', self.meta, {})
            self.assertEqual(c.db.execute('SELECT id FROM outbox').fetchall(), [(original,)])
            self.assertEqual((run / 'tools.jsonl').read_text(), source)
            c.db.close()


if __name__ == '__main__':
    unittest.main()
