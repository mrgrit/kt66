"""실행 증거 → 구역/경계/노출의 계약. 실제 모델·운영 도구는 호출하지 않는다."""
import json
import os
import pathlib
import shutil
import sys
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'noc'), str(ROOT/'agents'), str(ROOT/'noc/tests')]
import test_agent_control as control_tests
from risk_map import RiskMap
from agent_control import router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class RiskMapTests(unittest.TestCase):
    tearDown = control_tests.ControlTests.tearDown
    put = control_tests.ControlTests.put
    run_record = control_tests.ControlTests.run_record
    engine_state = control_tests.ControlTests.engine_state

    def setUp(self):
        control_tests.ControlTests.setUp(self)
        (self.root/'xoc').mkdir()
        (self.root/'tickets/xoc').mkdir()
        shutil.copy(ROOT/'agents/xoc/risk-zones.yaml', self.root/'xoc/risk-zones.yaml')
        (self.root/'roster.yaml').write_text('workers:\n  - {id: soc-analyst, name: SOC, security_role: soc, autonomy: L1}\n')
        (self.root/'harness.yaml').write_text('security:\n  roles:\n    soc:\n      tools: [siem_search, log_read]\n')
        self.put(self.root/'tickets/xoc/state.json', {'findings': {}, 'holds': {}, 'checked_at': time.time()})
        self.engine_state()
        self.risk = RiskMap(self.store)

    def receipts(self, records, status='completed', result=True):
        rid, directory = self.run_record(status=status, result=result)
        (directory/'tools.jsonl').write_text(''.join(json.dumps(dict(at=time.time()-10+i, **r))+'\n' for i, r in enumerate(records)))
        return rid, directory

    def test_authenticated_feed_and_public_static_catalogue(self):
        self.receipts([dict(tool='siem_search', result={})])
        app=FastAPI();app.include_router(router(self.root, 'secret-map-key'))
        with TestClient(app) as client:
            path='/api/agent-control/risk-map'
            self.assertEqual(client.get(path).status_code,401)
            self.assertEqual(client.get(path+'?key=secret-map-key').status_code,401)
            response=client.get(path,headers={'x-api-key':'secret-map-key'})
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['cache-control'])
            self.assertTrue(response.json()['read_only'])
            self.assertEqual(client.post(path).status_code,405)
            catalogue=client.get(path+'/zones').json()
            self.assertEqual(len(catalogue['zones']),12)
            self.assertNotIn('events',catalogue)
        app=FastAPI();app.include_router(router(self.root,''))
        self.assertEqual(TestClient(app).get('/api/agent-control/risk-map').status_code,401)

    def test_attempt_denied_and_pending_do_not_enter_target_zone(self):
        self.receipts([
            dict(tool='disk_usage',arguments={'target':'host'},result={'status':'denied','code':'role_boundary'}),
            dict(tool='siem_search',result={'status':'approval_required'}),
            dict(tool='siem_search',result={'total':2},authorization={'mode':'ask','role':'soc','user_decisions':[{'decision':'once','request':'p1'}]})])
        data=self.risk.build();a,b,c=data['events']
        self.assertEqual((a['zone'],a['attempted_zone'],a['outcome']),('Z04','Z02','denied'))
        self.assertEqual((b['zone'],b['attempted_zone'],b['outcome']),('Z05','Z06','pending'))
        self.assertEqual((c['zone'],c['outcome'],c['exposure']),('Z06','observed',2))
        self.assertEqual(c['permission']['decisions'][0]['decision'],'once')
        self.assertEqual(data['summary']['denied'],1)

    def test_simulator_approval_pending_is_a_gate(self):
        self.receipts([dict(tool='simulator_control', result={'status':'approval_pending','request_id':'pending-change'})])
        event=self.risk.build()['events'][0]
        self.assertEqual((event['zone'],event['outcome']),('Z05','pending'))

    def test_exposure_does_not_create_incident_and_proposal_is_not_deployment(self):
        self.receipts([dict(tool='approve_request',result={}),dict(tool='waf_prepare',result={'status':'prepared'})])
        data=self.risk.build()
        self.assertEqual(data['events'][0]['exposure'],4)
        self.assertEqual(data['findings'],[])
        self.assertIn('운영 적용 기록 아님',data['events'][1]['summary'])

    def test_hold_is_separate_from_denial_and_expired_hold_is_ignored(self):
        self.receipts([dict(tool='disk_usage',result={'status':'denied','code':'xoc_hold'})])
        data=self.risk.build()
        self.assertEqual(data['events'][0]['outcome'],'held')
        self.assertEqual(data['events'][0]['zone'],'Z11')
        self.put(self.root/'tickets/xoc/state.json',{'findings':{},'holds':{'soc-analyst':{'worker':'soc-analyst','until':time.time()+60}}})
        self.assertEqual(self.risk.build()['workers'][0]['location'],'HOLD')
        self.put(self.root/'tickets/xoc/state.json',{'findings':{},'holds':{'soc-analyst':{'worker':'soc-analyst','until':time.time()-1}}})
        self.assertNotEqual(self.risk.build()['workers'][0]['location'],'HOLD')

    def test_engine_stale_never_presents_current_access(self):
        self.receipts([dict(tool='siem_search',result={})],status='running',result=False)
        self.engine_state(active=['soc-analyst'])
        w=self.risk.build()['workers'][0]
        self.assertEqual((w['location'],w['presence']),('Z06','active'))
        self.engine_state(active=['soc-analyst'],age=120)
        w=self.risk.build()['workers'][0]
        self.assertEqual((w['location'],w['presence']),('DOCK','unknown'))

    def test_old_session_does_not_place_new_job_in_previous_zone(self):
        self.receipts([dict(tool='siem_search',result={})])
        self.run_record(job='new',age=2,status='running',result=False)
        self.engine_state(active=['soc-analyst'])
        w=self.risk.build()['workers'][0]
        self.assertEqual((w['location'],w['presence']),('Z01','awaiting_evidence'))

    def test_unknown_tool_auth_and_partial_records_are_visible(self):
        _,directory=self.receipts([dict(tool='unregistered_tool',result={})])
        with (directory/'tools.jsonl').open('a') as f:f.write('{"incomplete":')
        data=self.risk.build();e=data['events'][0]
        self.assertIsNone(e['exposure']);self.assertIsNone(e['permission']['mode'])
        self.assertEqual(data['coverage']['status'],'partial')
        self.assertTrue(data['coverage']['issues'])

    def test_false_positive_findings_are_not_open_incidents(self):
        self.receipts([dict(tool='siem_search',result={})])
        base={'worker':'soc-analyst','rule':'XOC-008','title':'조사 후보','risk':15,'created_at':time.time()}
        self.put(self.root/'tickets/xoc/state.json',{'findings':{'a':{**base,'id':'a','status':'false_positive'},'b':{**base,'id':'b','status':'open'}},'holds':{}})
        data=self.risk.build();self.assertEqual(data['summary']['open_findings'],1)
        self.assertEqual(data['findings'][0]['zone'],'Z06')

    def test_no_mutation_or_secret_leak(self):
        self.receipts([dict(tool='workspace_read',arguments={'path':'api_key=private-value-123'},result={'password':'never-return-body'})])
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.dict(os.environ,{'API_KEY':'private-value-123'}):data=self.risk.build()
        text=json.dumps(data)
        self.assertNotIn('private-value-123',text);self.assertNotIn('never-return-body',text)
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_missing_xoc_and_invalid_config_are_not_green(self):
        (self.root/'tickets/xoc/state.json').unlink()
        data=self.risk.build();self.assertFalse(data['coverage']['xoc_available']);self.assertEqual(data['coverage']['status'],'partial')
        (self.root/'xoc/risk-zones.yaml').write_text('zones: []')
        with self.assertRaises(HTTPException):self.risk.build()

if __name__=='__main__':unittest.main()
