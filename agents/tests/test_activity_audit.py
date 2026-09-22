import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import session_cli
import harness_tools
from activity_audit import record

class AuditTests(unittest.TestCase):
    def test_request_completion_and_output_saved_without_extra_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)
            with patch.object(session_cli,'_run',return_value={'body':'report','session_id':'s','runtime':'codex','usage':{'output_tokens':5}}) as model:
                session_cli.run('codex','default','password=example-secret',evidence_dir=p)
            self.assertEqual(model.call_count,1)
            events=[json.loads(x) for x in (p/'activity.jsonl').read_text().splitlines()]
            self.assertEqual([e['type'] for e in events],['request','session.completed'])
            self.assertNotIn('example-secret',(p/'activity.jsonl').read_text())
            self.assertEqual(json.loads((p/'session-result.json').read_text())['body'],'report')
    def test_failure_keeps_request_and_classified_error(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)
            with patch.object(session_cli,'_run',side_effect=session_cli.SessionError('subscription_usage_limit')):
                with self.assertRaises(session_cli.SessionError):session_cli.run('claude','haiku','task',evidence_dir=p)
            self.assertEqual(json.loads((p/'failure.json').read_text())['error'],'subscription_usage_limit')
            self.assertEqual(json.loads((p/'activity.jsonl').read_text().splitlines()[-1])['type'],'session.failed')
    def test_plan_note_is_declared_and_subject_to_tool_budget(self):
        with tempfile.TemporaryDirectory() as td:
            b=harness_tools.Broker.__new__(harness_tools.Broker)
            b.session=pathlib.Path(td);b.worker='w';b.autonomy='L1';b.permissions={}
            b.current=lambda:None;b.m={'version':'v','loops':[{'budget':{'max_tool_calls':1}}], 'authorization':{'role':'test','duty':'operate','tools':[]}}
            args={'stage':'plan','summary':'Check alarms','evidence':['job.json'],'steps':['read','review']}
            self.assertEqual(b.call('activity_note',args)['assertion'],'agent_declared')
            event=json.loads((b.session/'activity.jsonl').read_text())
            self.assertEqual(event['type'],'agent.plan')
            with self.assertRaisesRegex(ValueError,'budget'):b.call('activity_note',args)
    def test_monitor_tool_obeys_existing_permission_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            b=harness_tools.Broker.__new__(harness_tools.Broker)
            b.session=pathlib.Path(td);b.worker='w';b.autonomy='L1';b.permissions={'cmdb_read':'deny'}
            b.current=lambda:None;b.m={'version':'v','loops':[]}
            with patch.object(harness_tools.urllib.request,'urlopen') as network:
                self.assertEqual(b.call('agent_activity',{})['status'],'denied');network.assert_not_called()
if __name__=='__main__':unittest.main()
