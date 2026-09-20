"""Isolated evidence and HTTP contract tests. No model or live write is used."""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'noc'),str(ROOT/'agents')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_control import Observatory, router, usage_of
from activity_audit import record, scrub

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)/'agents'
        (self.root/'tickets').mkdir(parents=True);(self.root/'evidence').mkdir()
        db=sqlite3.connect(self.root/'tickets/loop-engine.sqlite3')
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,worker TEXT,kind TEXT,payload TEXT,status TEXT,created REAL,updated REAL,attempts INTEGER,not_before REAL,evidence TEXT,result TEXT)')
        db.close();self.store=Observatory(self.root)
    def tearDown(self):self.temp.cleanup()
    def put(self,path,data):path.write_text(json.dumps(data,ensure_ascii=False))
    def run_record(self,job='job1',worker='soc-analyst',age=30,status='completed',runtime='codex',result=True):
        at=time.time()-age;rid=f'loop-{int(at*1e9)}-{worker}';p=self.root/'evidence'/rid;p.mkdir()
        self.put(p/'job.json',{'job_id':job,'kind':'periodic:triage','observation':{'loop':'triage'}})
        if result:self.put(p/'result.json',{'body':'<script>untrusted()</script>','runtime':runtime,'session_id':'session1','usage':{'input_tokens':100,'cached_input_tokens':40,'output_tokens':5},'verification':{'tool_calls':1,'observed_live_evidence':True}})
        with sqlite3.connect(self.root/'tickets/loop-engine.sqlite3') as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(job,worker,'periodic:triage','{}',status,at,at+1,1,0,'/host/agents/evidence/'+rid,'{}'))
        return rid,p
    def test_runtime_token_accounting_and_unknown(self):
        self.assertEqual(usage_of({'runtime':'codex','usage':{'input_tokens':100,'cached_input_tokens':60,'output_tokens':10}})['total'],110)
        u=usage_of({'runtime':'claude','usage':{'input_tokens':2,'cache_creation_input_tokens':30,'cache_read_input_tokens':60,'output_tokens':10}})
        self.assertEqual(u['total'],102);self.assertEqual(u['input'],2)
        self.assertIsNone(usage_of({})['total'])
    def test_complete_run_with_tool_error_is_flagged_and_source_preserved(self):
        rid,p=self.run_record()
        (p/'tools.jsonl').write_text(json.dumps({'at':'2026-09-20T00:00:00Z','tool':'error:cycle_state','arguments':{},'result':{'status':'failed','error':'invalid key'}})+'\n')
        data=self.store.list(hours=0)
        self.assertEqual(data['items'][0]['status'],'completed')
        self.assertIn('tool_failure',[f['rule'] for f in data['items'][0]['findings']])
        d=self.store.detail(rid);self.assertEqual(d['timeline'][0]['source'],'tools.jsonl#L1')
        self.assertEqual(d['coverage']['decision'],'not_recorded')
        self.assertFalse(d['request']['captured']);self.assertIn('<script>',d['outcome']['body'])
    def test_pagination_filters_and_updates(self):
        first,_=self.run_record(job='a',age=10);self.run_record(job='b',age=20,worker='network-engineer')
        last,_=self.run_record(job='c',age=30)
        page=self.store.list(hours=0,limit=1)
        self.assertEqual(page['items'][0]['id'],first)
        second=self.store.list(hours=0,limit=1,cursor=page['next_cursor'])
        self.assertNotEqual(second['items'][0]['id'],first)
        self.assertEqual(self.store.list(hours=0,worker='soc-analyst')['total'],2)
        self.assertEqual(self.store.list(hours=0,q='network')['total'],1)
        self.assertEqual(self.store.list(hours=0,updated_since=time.time()+1)['total'],0)
    def test_retry_preserves_separate_attempts_and_periodic_cycles(self):
        old,oldp=self.run_record(age=60,result=False,status='failed')
        self.put(oldp/'failure.json',{'status':'failed','error':'timeout'})
        new,p=self.run_record(age=20)
        other,_=self.run_record(job='nextcycle',age=5)
        detail=self.store.detail(new)
        self.assertEqual(detail['run']['attempt'],2)
        self.assertEqual(detail['run']['previous_attempt'],old)
        self.assertEqual(len(detail['attempts']),2)
        self.assertEqual(self.store.detail(other)['run']['attempt'],1)
    def test_declared_notes_remain_distinct_from_tool_evidence(self):
        rid,p=self.run_record()
        record(p,'request',{'prompt':'task'})
        record(p,'agent.plan',{'stage':'plan','summary':'inspect alarms','evidence':['job.json'],'steps':['observe','verify']})
        d=self.store.detail(rid)
        self.assertTrue(d['request']['captured']);self.assertEqual(d['declared'][0]['type'],'agent.plan')
        self.assertEqual(d['coverage']['tools'],'not_recorded')
    def test_redaction_in_api_artifact_and_free_text(self):
        rid,p=self.run_record()
        self.put(p/'job.json',{'job_id':'job1','observation':{'password':'do-not-leak','api_key':'secret-api'}})
        record(p,'agent.review',{'summary':'Bearer secret-access-token','evidence':[]})
        d=json.dumps(self.store.detail(rid));self.assertNotIn('do-not-leak',d);self.assertNotIn('secret-api',d);self.assertNotIn('secret-access-token',d)
        a=self.store.artifact(rid,'job.json');self.assertNotIn('do-not-leak',a['text']);self.assertEqual(len(a['sha256']),64)
        with patch.dict(os.environ,{'API_KEY':'known-value-1234'}):
            self.assertNotIn('known-value-1234',self.store.public('url?key=known-value-1234'))
        metadata=self.store.public({'authorization':{'permission':'env_read','mode':'allow','autonomy':'L1'},'headers':{'Authorization':'Bearer secret-token'}})
        self.assertEqual(metadata['authorization']['mode'],'allow')
        self.assertEqual(metadata['headers']['Authorization'],'[REDACTED]')
        self.assertNotIn('quoted secret',scrub('partial JSON: {"password": "quoted secret",'))
    def test_host_manifest_mapping_and_path_escape_rejected(self):
        rid,p=self.run_record()
        dest=self.root/'runtimes/codex/versions/worker/v1';dest.mkdir(parents=True)
        self.put(dest/'manifest.json',{'policy':{'constrain':{'autonomy':'L1','permission':{'env_read':'allow'}}}})
        self.put(p/'loaded-harness.json',{'manifest':'/old/host/agents/runtimes/codex/versions/worker/v1/manifest.json'})
        self.assertEqual(self.store.detail(rid)['policy']['effective']['autonomy'],'L1')
        outside=pathlib.Path(self.temp.name)/'outside';outside.write_text('SECRET')
        (p/'finding-aaa.md').symlink_to(outside)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):self.store.artifact(rid,'finding-aaa.md')
        with self.assertRaises(HTTPException):self.store.artifact(rid,'../../outside')
        self.assertIsNone(self.store.mapped('/host/agents/../../outside'))
    def test_partial_jsonl_marks_collection_gap(self):
        rid,p=self.run_record()
        (p/'tools.jsonl').write_text('{"tool":"env_read","result":{}}\n{"incomplete":')
        d=self.store.detail(rid)
        self.assertIn('incomplete_or_invalid_record',d['coverage']['issues'])
        self.assertIn('collection_gap',[x['rule'] for x in d['run']['findings']])
    def engine_state(self,active=(),status='running',age=0):
        self.put(self.root/'tickets/loop-engine-status.json',{'at':time.time()-age,'status':status,'active_workers':list(active),'error':None})
    def worker_state(self,worker='soc-analyst'):
        return next(w for w in self.store.worker_states()['items'] if w['worker']==worker)
    def test_worker_state_idle_and_old_failure_does_not_latch(self):
        self.run_record(job='old',age=90,status='failed',result=False)
        self.run_record(job='new',age=10)
        self.engine_state()
        self.assertEqual(self.worker_state()['state'],'idle')
        self.assertFalse(self.worker_state()['latest_failed'])
    def test_worker_review_backlog_is_preserved_while_active(self):
        self.run_record(job='review',age=90,status='needs_review')
        self.run_record(job='new',age=10)
        self.engine_state()
        self.assertEqual(self.worker_state()['state'],'attention')
        self.engine_state(active=['soc-analyst'])
        data=self.worker_state()
        self.assertEqual(data['state'],'working');self.assertEqual(data['counts']['needs_review'],1)
    def test_worker_queue_retry_capacity_and_latest_failure(self):
        self.run_record(job='job',status='queued',result=False)
        self.engine_state()
        self.assertEqual(self.worker_state()['state'],'idle')
        self.assertEqual(self.worker_state()['counts']['queued'],1)
        for status,expected in [('retry','waiting'),('waiting_capacity','waiting'),('failed','attention'),('running','unknown')]:
            with sqlite3.connect(self.root/'tickets/loop-engine.sqlite3') as db:
                db.execute('UPDATE jobs SET status=? WHERE id=?',(status,'job'))
            self.assertEqual(self.worker_state()['state'],expected)
    def test_worker_stale_missing_or_broken_engine_is_never_green(self):
        self.run_record(status='running',result=False)
        self.engine_state(active=['soc-analyst'],age=65)
        self.assertEqual(self.worker_state()['state'],'unknown')
        self.engine_state(active=['soc-analyst'],age=-90)
        self.assertEqual(self.worker_state()['state'],'unknown')
        self.put(self.root/'tickets/loop-engine-status.json',{'at':time.time(),'status':'running','active_workers':'soc-analyst'})
        self.assertEqual(self.worker_state()['state'],'unknown')
        self.engine_state(status='stopped')
        self.assertEqual(self.worker_state()['state'],'stopped')
        (self.root/'tickets/loop-engine-status.json').unlink()
        self.assertEqual(self.worker_state()['state'],'unknown')
    def test_worker_missing_db_no_creation_and_no_evidence_scan(self):
        self.engine_state(active=['soc-analyst'])
        path=self.root/'tickets/loop-engine.sqlite3';path.unlink()
        with patch.object(self.store,'refresh',side_effect=AssertionError('no evidence scan')):
            data=self.store.worker_states()
        self.assertEqual(data['items'][0]['state'],'unknown')
        self.assertEqual(data['source']['status'],'partial');self.assertFalse(path.exists())
    def test_http_contract_and_bounds(self):
        rid,_=self.run_record()
        app=FastAPI();app.include_router(router(self.root));client=TestClient(app)
        self.assertEqual(client.get('/api/agent-control/schema').json()['mode'],'read_only')
        self.assertEqual(client.get('/api/agent-control/runs?limit=0').status_code,422)
        self.assertEqual(client.get('/api/agent-control/runs?cursor=bad').status_code,400)
        self.assertEqual(client.get('/api/agent-control/runs/missing').status_code,404)
        self.assertEqual(client.post('/api/agent-control/runs').status_code,405)
        self.engine_state()
        self.assertEqual(client.get('/api/agent-control/workers').json()['items'][0]['state'],'idle')
        self.assertEqual(client.post('/api/agent-control/workers').status_code,405)
        d=client.get('/api/agent-control/runs/'+rid+'?compact=true').json()
        self.assertNotIn('context',d);self.assertIn('coverage',d)
    def test_missing_source_is_visible_and_no_db_is_created(self):
        path=self.root/'tickets/loop-engine.sqlite3';path.unlink()
        data=self.store.list(hours=0)
        self.assertEqual(data['source']['status'],'partial');self.assertFalse(path.exists())

if __name__=='__main__':unittest.main()
