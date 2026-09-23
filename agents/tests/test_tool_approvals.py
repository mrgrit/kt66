"""승인 범위·철회·보류·재개·복수 정책을 실제 브로커 경로로 검사한다."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import harness_tools as tools
import request_runtime
import work_requests as wr
import loop_engine
import tool_approvals
import storage_probe
import request_tools

MEASURED={'status':'ok','measured_targets':1,'targets':[],'matches':[]}

class Approvals(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'agents';self.root.mkdir()
        for name in ('company.yaml','departments.yaml','teams.yaml','roster.yaml','harness.yaml'):
            shutil.copy2(ROOT/name,self.root/name)
        for name in ('personas','loops','native'):
            shutil.copytree(ROOT/name,self.root/name,ignore=shutil.ignore_patterns('.edit.lock'))
        (self.root.parent/'.env').write_text('API_KEY=test-key\nINT_HOST_IP=127.0.0.1\n')
        config=yaml.safe_load((self.root/'harness.yaml').read_text())
        config['defaults']['constrain']['permission'].update(metrics_read='ask',cmdb_read='ask',user_request='ask')
        (self.root/'harness.yaml').write_text(yaml.safe_dump(config))
        self.rootpatch=patch.object(tools,'ROOT',self.root);self.rootpatch.start();self.addCleanup(self.rootpatch.stop)
        self.store=wr.Store(self.root);self.permissions=tool_approvals.Permissions(self.root)
        with patch.object(loop_engine,'ROOT',self.root):self.db=loop_engine.db_open()
        self.addCleanup(self.db.close)
        self.new()

    def new(self,worker='systems-engineer'):
        self.d=self.store.create('80% 이상 디스크 확인',mode='conversation',worker=worker)
        self.rid=self.d['id'];self.poll();return self.broker()

    def poll(self):wr.poll(self.root,self.db,loop_engine.enqueue)

    def broker(self):
        d=self.store.get(self.rid);task=next(t for t in reversed(d['tasks']) if t['status']=='running')
        self.tid=task['id']
        dest,m=request_runtime.compile_request(self.root,d,task,self.root/'evidence'/('test-'+uuid.uuid4().hex))
        tools.ROOT=self.root
        return tools.Broker(dest/'manifest.json',dest.parent)

    def finish(self,status='waiting_input'):
        t=next(t for t in self.store.get(self.rid)['tasks'] if t['id']==self.tid)
        result={'request_outcome':{'status':status,'summary':'승인 안내','question':'허용하시겠습니까?' if status=='waiting_input' else '', 'artifacts':[]}}
        self.db.execute('UPDATE jobs SET status=?, result=? WHERE id=?',('completed',json.dumps(result),t['job_id']));self.db.commit();self.poll()

    def approve(self,request,decision='once'):
        self.finish();self.permissions.decide(self.rid,request['permission_request'],decision)
        self.poll();return self.broker()

    def test_pending_is_idempotent_and_does_not_run_tool_or_allow_early_decision(self):
        b=self.broker()
        with patch.object(storage_probe,'collect') as collect:
            a=b.call('disk_usage',{'threshold_pct':80});c=b.call('disk_usage',{'threshold_pct':80})
            self.assertEqual(a['permission_request'],c['permission_request']);collect.assert_not_called()
        with self.assertRaises(ValueError):self.permissions.decide(self.rid,a['permission_request'],'once')
        # 승인 안내와 종료 보고 자체가 다시 승인을 요구해서는 안 된다.
        self.assertIn('disk_usage',b.call('request_context',{})['available_tools'])
        self.assertTrue(b.call('request_finish',{'status':'waiting_input','summary':'권한 필요','question':'허용 선택','artifacts':[],'response_kind':'reply'})['recorded'])

    def test_once_is_exact_call_and_consumed_only_when_executing(self):
        b=self.broker();a=b.call('disk_usage',{'threshold_pct':80});b=self.approve(a)
        with patch.object(storage_probe,'collect',return_value=MEASURED.copy()) as collect:
            other=b.call('disk_usage',{'threshold_pct':90});self.assertEqual(other['status'],'approval_required')
            self.assertEqual(b.call('disk_usage',{'threshold_pct':80})['status'],'ok')
            self.assertEqual(collect.call_count,1)
            self.assertEqual(b.call('disk_usage',{'threshold_pct':80})['status'],'approval_required')
        self.assertTrue(any(e['kind']=='permission_consumed' for e in self.store.get(self.rid)['events']))
        receipts=[json.loads(l) for l in (b.session/'tools.jsonl').read_text().splitlines()]
        executed=next(r for r in receipts if r['result']['status']=='ok')
        self.assertEqual(executed['authorization']['user_decisions'][0]['decision'],'once')
        self.assertEqual(executed['authorization']['user_decisions'][0]['actor'],'instructor')
        self.assertGreater(executed['authorization']['user_decisions'][0]['decided_at'],0)
        self.assertEqual(b.m['request']['resumed_from_task_id'],'conversation-1')

    def test_always_is_worker_and_tool_scoped_and_revocable_and_cannot_override_deny(self):
        b=self.broker();a=b.call('disk_usage',{});b=self.approve(a,'always')
        with patch.object(storage_probe,'collect',return_value=MEASURED.copy()):
            self.assertEqual(b.call('disk_usage',{})['status'],'ok')
            self.finish('completed');self.assertEqual(self.store.get(self.rid)['status'],'completed')
            b=self.new();self.assertEqual(b.call('disk_usage',{})['status'],'ok')
            self.assertEqual(b.call('infrastructure_read',{})['status'],'approval_required')
            b.permissions['metrics_read']='deny';self.assertEqual(b.call('disk_usage',{})['status'],'denied')
            b.permissions['metrics_read']='ask';gid=self.permissions.list()[0]['id'];self.permissions.revoke(gid)
            self.assertEqual(b.call('disk_usage',{})['status'],'approval_required')
        self.assertEqual(self.permissions.list(),[])

    def test_always_does_not_authorize_another_worker(self):
        b=self.broker();a=b.call('disk_usage',{});self.approve(a,'always')
        b=self.new('soc-analyst');self.assertEqual(b.call('disk_usage',{})['status'],'denied')
        self.assertEqual(self.store.get(self.rid).get('permission_requests',[]),[])

    def test_defer_does_not_start_sessions_and_reply_invalidates_old_approval(self):
        b=self.broker();a=b.call('disk_usage',{});self.finish()
        self.permissions.decide(self.rid,a['permission_request'],'defer')
        for _ in range(3):self.poll()
        self.assertEqual(self.store.get(self.rid)['sessions'],1)
        self.store.reply(self.rid,'다른 업무')
        with self.assertRaises(ValueError):self.permissions.decide(self.rid,a['permission_request'],'always')
        self.assertEqual(self.permissions.list(),[])

    def test_two_required_approvals_do_not_consume_each_other_and_resume_completes(self):
        b=self.broker();first=b.call('inventory_query',{'kind':'all'});b=self.approve(first)
        second=b.call('inventory_query',{'kind':'all'})
        self.assertEqual(second['permission'],'cmdb_read')
        self.assertEqual(self.store.get(self.rid)['permission_requests'][0]['status'],'allowed_once')
        b=self.approve(second)
        with patch.object(request_tools,'inventory',return_value={'items':[]}):
            self.assertEqual(b.call('inventory_query',{'kind':'all'})['items'],[])
        self.assertEqual([p['status'] for p in self.store.get(self.rid)['permission_requests']],['consumed','consumed'])
        self.finish('completed');d=self.store.get(self.rid)
        self.assertEqual(d['status'],'completed');self.assertEqual(d['sessions'],3)
        self.assertEqual(len(d['messages']),4)

    def test_cancelled_request_cannot_be_authorized(self):
        b=self.broker();a=b.call('disk_usage',{});self.finish();self.store.cancel(self.rid)
        with self.assertRaises(ValueError):self.permissions.decide(self.rid,a['permission_request'],'once')

    def test_changed_asset_boundary_invalidates_persistent_grant(self):
        b=self.broker();a=b.call('disk_usage',{});self.approve(a,'always');self.finish('completed')
        p=self.root/'harness.yaml';config=yaml.safe_load(p.read_text())
        config['security']['roles']['systems']['disk_targets']=['host']
        p.write_text(yaml.safe_dump(config))
        self.assertFalse(self.permissions.list()[0]['policy_current'])
        b=self.new()
        with patch.object(storage_probe,'collect') as collect:
            self.assertEqual(b.call('disk_usage',{})['status'],'approval_required')
            self.assertEqual(b.call('disk_usage',{'target':'kt66-fw'})['code'],'asset_boundary')
            collect.assert_not_called()

    def test_changed_role_or_parent_deny_blocks_pending_approval(self):
        b=self.broker();a=b.call('disk_usage',{});self.finish()
        p=self.root/'harness.yaml';original=p.read_text();config=yaml.safe_load(original)
        config['defaults']['constrain']['permission']['metrics_read']='deny';p.write_text(yaml.safe_dump(config))
        with self.assertRaisesRegex(ValueError,'금지'):self.permissions.decide(self.rid,a['permission_request'],'always')
        config=yaml.safe_load(original);config['security']['roles']['systems']['disk_targets']=['host'];p.write_text(yaml.safe_dump(config))
        with self.assertRaisesRegex(ValueError,'직무 정책'):self.permissions.decide(self.rid,a['permission_request'],'once')
        self.assertEqual(self.permissions.list(),[])

if __name__=='__main__':unittest.main()
