"""SIEM 장애·중복·불완전 기록이 관제 증거를 훼손하지 않는지 확인한다."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'observability'), str(ROOT / 'agents'), str(ROOT / 'agentops')]
from collector import Collector
from projection import document, outcome, finding_document, safe_document
from monitoring_api import Monitor, grouped_findings, install
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch


class FakeIndex:
    def __init__(self):
        self.calls, self.documents = [], {}
        self.fail, self.partial = False, False

    def request(self, method, path, payload):
        if self.fail:
            raise OSError('offline')
        lines = payload.decode().splitlines()
        self.calls.append((method, path))
        items = []
        for i in range(0, len(lines), 2):
            meta, body = json.loads(lines[i])['index'], json.loads(lines[i + 1])
            if self.partial and i == 0:
                items.append({'index': {'status': 429}})
                continue
            self.documents[(meta['_index'], meta['_id'])] = body
            items.append({'index': {'status': 201}})
        return {'items': items}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.state = base / 'agents', base / 'state'
        self.run = self.root / 'evidence/loop-1790145000000000000-soc-analyst'
        self.run.mkdir(parents=True)
        (self.root / 'tickets/xoc').mkdir(parents=True)
        (self.root / 'xoc').mkdir()
        shutil.copy(ROOT / 'agents/xoc/risk-zones.yaml', self.root / 'xoc/risk-zones.yaml')
        (self.run / 'job.json').write_text(json.dumps({'worker': 'soc-analyst', 'kind': 'periodic:soc'}))
        (self.root / 'tickets/xoc/state.json').write_text(json.dumps({'findings': {}, 'holds': {}, 'history': []}))
        self.client = FakeIndex()
        self.collector = Collector(self.root, self.state, self.client)

    def tearDown(self):
        self.collector.db.close()
        self.tmp.cleanup()

    def tool(self, name='siem_search', result=None, identifier='one'):
        return {'id': identifier, 'at': time.time(), 'tool': name, 'arguments': {}, 'result': result or {}, 'authorization': {'role': 'soc', 'mode': 'allow'}}

    def put(self, name, obj):
        (self.run / name).write_text(json.dumps(obj) + ('\n' if name.endswith('.jsonl') else ''))

    def pending(self):
        return self.collector.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]

    def test_retry_restart_and_duplicate_ack_are_idempotent(self):
        self.put('tools.jsonl', self.tool())
        self.client.fail = True
        self.collector.cycle()
        self.assertEqual(self.pending(), 1)
        self.collector.db.close()
        self.collector = Collector(self.root, self.state, self.client)
        self.client.fail = False
        self.collector.cycle()
        self.assertEqual(self.pending(), 0)
        self.assertEqual(len(self.client.documents), 1)
        self.collector.cycle()
        self.assertEqual(len(self.client.calls), 1)
        # ACK가 유실된 재전송도 같은 문서를 덮어써 건수가 늘지 않는다.
        self.collector.db.execute('DELETE FROM files')
        self.collector.db.execute('DELETE FROM records')
        self.collector.db.commit()
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 1)

    def test_bulk_partial_failure_keeps_only_unacknowledged_rows(self):
        self.put('tools.jsonl', self.tool())
        self.put('activity.jsonl', {'id': 'two', 'type': 'session.completed', 'at': time.time(), 'data': {}})
        self.collector.scan()
        self.client.partial = True
        self.assertEqual(self.collector.flush(), 1)
        self.assertEqual(self.pending(), 1)
        self.client.partial = False
        self.collector.flush()
        self.assertEqual(self.pending(), 0)
        self.assertEqual(len(self.client.documents), 2)

    def test_partial_line_waits_and_broken_line_never_advances(self):
        path = self.run / 'tools.jsonl'
        raw = json.dumps(self.tool())
        path.write_text(raw)
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 0)
        path.write_text(raw + '\n')
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 1)
        with path.open('a') as out:
            out.write('{broken}\n')
        before = self.collector.db.execute('SELECT offset FROM files WHERE path LIKE ? ', ('%tools.jsonl',)).fetchone()[0]
        self.collector.cycle()
        self.assertEqual(self.collector.db.execute('SELECT offset FROM files WHERE path LIKE ?', ('%tools.jsonl',)).fetchone()[0], before)
        self.assertEqual(self.collector.stats['source_error_count'], 1)
        self.assertIn('{broken}', path.read_text())

    def test_file_rotation_and_symlink(self):
        self.put('tools.jsonl', self.tool())
        self.collector.cycle()
        path = self.run / 'tools.jsonl'
        path.rename(self.run / 'old.jsonl')
        self.put('tools.jsonl', self.tool(identifier='new'))
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 2)
        (self.run / 'finding-leak.md').symlink_to('/etc/passwd')
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 2)
        self.assertGreater(self.collector.stats['source_error_count'], 0)

    def test_result_pair_uses_one_session_and_token_measurement(self):
        result = {'runtime': 'codex', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 12}}
        self.put('session-result.json', result)
        self.collector.cycle()
        self.put('result.json', {**result, 'verification': {'observed_live_evidence': True}})
        self.collector.cycle()
        self.assertEqual(len(self.client.documents), 1)
        self.assertEqual(next(iter(self.client.documents.values()))['tokens_total'], 112)

    def test_source_immutable_and_secrets_minimized(self):
        self.put('tools.jsonl', {**self.tool(), 'arguments': {'password': 'never-export', 'path': 'api_key=secret-value'}, 'result': {'raw': 'never-export'}})
        self.put('activity.jsonl', {'id': 'activity', 'type': 'request', 'data': {'prompt': 'private-prompt', 'reasoning': 'hidden'}})
        (self.run / 'finding-a.md').write_text('# 보고서\n\npassword=teacher-secret\n')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.collector.cycle()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        exported = json.dumps(list(self.client.documents.values()))
        for secret in ('never-export', 'secret-value', 'teacher-secret', 'private-prompt', 'hidden'):
            self.assertNotIn(secret, exported)

    def test_xoc_review_updates_same_document(self):
        row = {'id': 'f1', 'created_at': time.time(), 'worker': 'soc-analyst', 'rule': 'XOC-004', 'status': 'open', 'risk': 12}
        path = self.root / 'tickets/xoc/state.json'
        for state in ('open', 'resolved'):
            path.write_text(json.dumps({'findings': {'f1': {**row, 'status': state}}}))
            self.collector.cycle()
        self.assertEqual(len(self.client.documents), 1)
        self.assertEqual(next(iter(self.client.documents.values()))['status'], 'resolved')

    def test_control_history_remains_when_snapshot_changes(self):
        path = self.root / 'tickets/xoc/state.json'
        history = [{'at': time.time(), 'action': 'review', 'actor': 'instructor', 'finding_id': 'f1', 'status': 'resolved', 'reason': '증거를 검토한 뒤 종결합니다'}]
        path.write_text(json.dumps({'findings': {}, 'history': history}))
        self.collector.cycle()
        path.write_text(json.dumps({'findings': {}, 'history': []}))
        self.collector.cycle()
        rows = list(self.client.documents.values())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['assertion'], 'control_record')
        self.assertEqual(rows[0]['status'], 'resolved')

    def test_permission_results_do_not_become_success(self):
        for raw, expected in [('denied', 'denied'), ('approval_pending', 'pending'), ('approval_required', 'pending'), ('failed', 'error')]:
            self.assertEqual(outcome('disk_usage', {'status': raw}), expected)
        self.assertEqual(outcome('disk_usage', {'status': 'denied', 'code': 'xoc_hold'}), 'held')


class DashboardTests(unittest.TestCase):
    def test_groups_preserve_all_findings_without_closing(self):
        rows = [{'id': str(i), 'worker': 'soc', 'rule': 'R1', 'risk': 12, 'created_at': i, 'status': 'open'} for i in range(700)]
        before = json.dumps(rows)
        rows.append({'id': 'closed', 'worker': 'soc', 'rule': 'R1', 'risk': 25, 'status': 'resolved'})
        groups = grouped_findings(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]['count'], groups[0]['risk'], groups[0]['finding_id']), (700, 12, '699'))
        self.assertEqual(json.dumps(rows[:-1]), before)

    def test_siem_failure_is_unknown_not_zero(self):
        def failure():
            raise OSError('no credentials')
        with tempfile.TemporaryDirectory() as root:
            data = Monitor(root, failure).snapshot(24)
            self.assertFalse(data['agent']['available'])
            self.assertFalse(data['soc']['available'])
            self.assertNotIn('total', data['soc'])
            self.assertFalse(data['xoc']['available'])

    def test_sensitive_routes_authenticate_and_limit_queries(self):
        with tempfile.TemporaryDirectory() as root:
            app = FastAPI()
            monitor = install(app, Path(root), 'test-secret-key', None)
            with TestClient(app) as client:
                for path in ('summary', 'cases', 'detail?kind=xoc&id=x'):
                    r = client.get('/api/monitoring/' + path)
                    self.assertEqual(r.status_code, 401)
                    self.assertIn('no-store', r.headers['cache-control'])
                headers = {'x-api-key': 'test-secret-key'}
                self.assertEqual(client.get('/api/monitoring/summary?hours=9999', headers=headers).status_code, 400)
                self.assertEqual(client.get('/api/monitoring/cases?page=400', headers=headers).status_code, 422)
                self.assertEqual(client.get('/api/monitoring/detail?kind=soc&id=x&index=.opendistro_security', headers=headers).status_code, 400)
                with patch.object(monitor, 'snapshot', return_value={'available': True, 'value': 'test-secret-key'}):
                    r = client.get('/api/monitoring/summary', headers=headers)
                    self.assertEqual(r.json()['value'], '[REDACTED]')
                    self.assertIn('no-store', r.headers['cache-control'])


if __name__ == '__main__':
    unittest.main()
