import copy
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import adaptive_monitor as monitor
import loop_engine as engine

CFG={'adaptive':{'enabled':True,'intervals_sec':[600,300,120,60], 'stable_samples':2,
                 'model_cooldown_sec':600,'incident_review_sec':1800}}
LOOP={'id':'test-loop','owner':'worker','cadence':'*/10 * * * *','monitor':{'mode':'adaptive','probe':'environment'}}
GOOD={'signals':{'temp:2F':23,'service':'up','alarms':[]},'problems':[],'sources':['test']}
BAD={'signals':{'temp:2F':29,'service':'down','alarms':[['HOT','2F','4']]},'problems':['temperature'],'sources':['test']}

class DecisionTests(unittest.TestCase):
    def test_healthy_baseline_and_unchanged_checks_never_invoke_model(self):
        state={}
        for n in range(12):
            state,invoke=monitor.advance(state,GOOD,10000+n*600,CFG)
            self.assertFalse(invoke);self.assertEqual(state['interval_sec'],600)
    def test_escalates_600_300_120_60_and_recovers_with_hysteresis(self):
        state,_=monitor.advance({},GOOD,10000,CFG)
        for expected in (300,120,60,60):
            state,invoke=monitor.advance(state,BAD,state['next_check_at'],CFG)
            self.assertEqual(state['interval_sec'],expected)
            self.assertTrue(invoke)
        observed=[]
        for _ in range(7):
            state,_=monitor.advance(state,GOOD,state['next_check_at'],CFG)
            observed.append(state['interval_sec'])
        self.assertEqual(observed,[60,60,120,120,300,300,600])
    def test_same_incident_only_reinvokes_after_review_interval(self):
        state,_=monitor.advance({},BAD,10000,CFG)
        state.update(last_ai_at=10000,pending=False)
        for at in (10300,10420,11000,11799):
            state,invoke=monitor.advance(state,BAD,at,CFG)
            self.assertFalse(invoke)
        _,invoke=monitor.advance(state,BAD,11800,CFG);self.assertTrue(invoke)
    def test_new_change_can_wake_ai_at_the_shorter_interval(self):
        cfg=copy.deepcopy(CFG);cfg['adaptive']['model_cooldown_sec']=60
        state,_=monitor.advance({},GOOD,10000,cfg)
        state,invoke=monitor.advance(state,BAD,10600,cfg)
        self.assertTrue(invoke);state.update(last_ai_at=10600,pending=False)
        changed=copy.deepcopy(BAD);changed['signals']['temp:2F']=32
        state,invoke=monitor.advance(state,changed,10900,cfg)
        self.assertTrue(invoke);self.assertEqual(state['interval_sec'],120)
    def test_pending_change_survives_cooldown_and_noisy_values(self):
        state,_=monitor.advance({},GOOD,10000,CFG)
        state['last_ai_at']=10000
        noisy=copy.deepcopy(GOOD);noisy['signals']['temp:2F']=23.3
        state,invoke=monitor.advance(state,noisy,10060,CFG);self.assertFalse(invoke)
        altered=copy.deepcopy(GOOD);altered['signals']['temp:2F']=25.2
        state,invoke=monitor.advance(state,altered,10300,CFG)
        self.assertFalse(invoke);self.assertTrue(state['pending'])
        state,invoke=monitor.advance(state,altered,10600,CFG);self.assertTrue(invoke)
    def test_json_restart_does_not_turn_tuples_into_false_changes(self):
        obs={'signals':{'alarms':[('x','scope','3')]},'problems':['alarm']}
        state,_=monitor.advance({},obs,10000,CFG)
        state.update(last_ai_at=10000,pending=False)
        state=json.loads(json.dumps(state))
        state,invoke=monitor.advance(state,obs,10300,CFG)
        self.assertFalse(invoke);self.assertEqual(state['changed_keys'],[])
    def test_bad_configuration_is_rejected(self):
        for value in ([60,600],[600,600],[600,0],[600,True],[]):
            with self.subTest(value=value),self.assertRaises(ValueError):
                monitor.settings({'adaptive':{'intervals_sec':value}})

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)/'agents';self.root.mkdir()
        self.patch=patch.object(engine,'ROOT',self.root);self.patch.start()
        self.db=engine.db_open();self.probes=Mock();self.probes.collect.return_value=GOOD
    def tearDown(self):self.db.close();self.patch.stop();self.temp.cleanup()
    def poll(self,at=10000,**extra):
        monitor.poll(self.db,[LOOP],CFG,self.probes,engine.enqueue,now=at,**extra)
    def test_zero_ai_jobs_for_a_day_of_healthy_checks(self):
        for n in range(144):self.poll(10000+n*600)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
        self.assertEqual(monitor.load(self.db,'test-loop')['skipped_model_calls'],144)
        self.assertEqual(self.probes.collect.call_count,144)
    def test_due_time_survives_restart_without_probe_or_model(self):
        self.poll();self.db.close();self.db=engine.db_open();self.poll(10005)
        self.assertEqual(self.probes.collect.call_count,1)
        self.assertEqual(monitor.load(self.db,'test-loop')['next_check_at'],10600)
    def test_old_queue_is_superseded_but_event_approval_jobs_preserved(self):
        engine.enqueue(self.db,'legacy','worker','periodic:test-loop',{})
        engine.enqueue(self.db,'event','worker','event',{})
        engine.enqueue(self.db,'approval','worker','approval',{})
        self.poll()
        rows=dict(self.db.execute('SELECT id,status FROM jobs').fetchall())
        self.assertEqual(rows,{'legacy':'superseded','event':'queued','approval':'queued'})
    def test_busy_worker_does_not_lose_pending_change(self):
        self.poll();engine.enqueue(self.db,'busy','worker','approval',{})
        self.probes.collect.return_value=BAD;self.poll(10600)
        self.assertEqual(monitor.load(self.db,'test-loop')['decision'],'worker_busy')
        self.db.execute("UPDATE jobs SET status='completed'");self.db.commit();self.poll(10900)
        rows=self.db.execute("SELECT * FROM jobs WHERE status='queued'").fetchall()
        self.assertEqual(len(rows),1)
        payload=json.loads(rows[0]['payload']);self.assertIn('temperature',payload['monitor']['problems'])
        self.poll(10905);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0],1)
    def test_new_event_wakes_monitor_early_without_duplicate_ai_job(self):
        self.poll();self.probes.collect.return_value=BAD
        engine.enqueue(self.db,'event','worker','event',{})
        self.poll(10030,notified_workers={'worker'})
        data=monitor.load(self.db,'test-loop')
        self.assertEqual(data['interval_sec'],300);self.assertEqual(data['decision'],'covered_by_event')
        self.assertFalse(data['pending']);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
    def test_monitor_failure_is_persistent_problem_not_clean_baseline(self):
        self.probes.collect.return_value={'signals':{'collection_error':'OSError'},'problems':['observation_unavailable']}
        self.poll();self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
    def test_cron_only_loop_does_not_enter_adaptive_monitor(self):
        loop={**LOOP,'monitor':{}}
        monitor.poll(self.db,[loop],CFG,self.probes,engine.enqueue,now=10000)
        self.probes.collect.assert_not_called()
    def test_engine_bypasses_periodic_ai_but_retains_calendar_and_event_jobs(self):
        (self.root/'loops').mkdir()
        (self.root.parent/'.env').write_text('INT_HOST_IP=127.0.0.1\n')
        routine={**LOOP,'triggers':{'alarms':['HOT']}}
        audit={'id':'audit','owner':'worker','cadence':'* * * * *'}
        (self.root/'roster.yaml').write_text(json.dumps({'workers':[{'id':'worker','loops':['test-loop','audit']}]}))
        for loop in (routine,audit):(self.root/'loops'/(loop['id']+'.yaml')).write_text(json.dumps(loop))
        live={'alarms':[]}
        def observed(url):
            return {'active':live['alarms']} if url.endswith('/alarms') else {'active':[]}
        with patch.object(engine,'observe',side_effect=observed),patch.object(monitor.Probes,'collect',return_value=GOOD):
            engine.poll(self.db,CFG)
            kinds=[r[0] for r in self.db.execute('SELECT kind FROM jobs')]
            self.assertEqual(kinds,['periodic:audit'])
            live['alarms']=[{'id':'HOT','scope':'2F','level':4}]
            engine.poll(self.db,CFG)
            kinds=[r[0] for r in self.db.execute('SELECT kind FROM jobs')]
            self.assertCountEqual(kinds,['periodic:audit','event'])
            engine.poll(self.db,CFG)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)

class ProbeTests(unittest.TestCase):
    def test_collection_failure_is_not_reported_healthy(self):
        p=monitor.Probes(pathlib.Path('/tmp'),'host',Mock(side_effect=OSError()),10000)
        result=p.collect(LOOP)
        self.assertIn('observation_unavailable',result['problems'])
    def test_uptime_and_clock_changes_do_not_trigger(self):
        state={'alarms':[], 'containers':{'kt66-web':{'state':'running','health':True,'status':'Up 1 hour'}},'netglue':True,'ts':10}
        callback=Mock(return_value=state)
        loop={**LOOP,'monitor':{'mode':'adaptive','probe':'tickets'}}
        first=monitor.Probes(pathlib.Path('/tmp'),'host',callback,10000).collect(loop)
        state['ts']=20;state['containers']['kt66-web']['status']='Up 2 hours'
        second=monitor.Probes(pathlib.Path('/tmp'),'host',callback,10600).collect(loop)
        self.assertEqual(first,second)
    def test_siem_old_alerts_are_not_new_incidents_and_level_is_filtered(self):
        import subprocess
        rows=[{'timestamp':'1970-01-01T00:01:00+00:00','rule':{'id':'old','level':12}},
              {'timestamp':'1970-01-01T02:46:00+00:00','rule':{'id':'low','level':3}},
              {'timestamp':'1970-01-01T02:46:00.185+0000','rule':{'id':'new','level':8},'agent':{'id':'1'}}]
        p=monitor.Probes(pathlib.Path('/tmp'),'host',Mock(),10000)
        with patch.object(monitor.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'\n'.join(map(json.dumps,rows)),'')):
            signatures,truncated=p.security_signals()
        self.assertEqual(signatures,[('new','1','')]);self.assertFalse(truncated)
    def test_inference_probe_uses_existing_routed_observer_without_generation(self):
        import subprocess
        p=monitor.Probes(pathlib.Path('/tmp'),'host',Mock(),10000)
        with patch.object(monitor.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"models":[]}','')) as call:
            self.assertEqual(p.inference_models('10.20.50.10'),{'models':[]})
        command=call.call_args.args[0]
        self.assertEqual(command[:3],['docker','exec','kt66-envsim'])
        self.assertTrue(command[-1].endswith('/api/ps'))

if __name__=='__main__':unittest.main()
