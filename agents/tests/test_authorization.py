"""직무·대상·승인·임시 역할 경계를 실제 컴파일 및 도구 중개 경로로 검증한다."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import authorization as auth
import harness_compiler
import harness_tools as tools
import request_runtime
import request_tools
import storage_probe
import tool_approvals
import work_requests
import request_changes


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'agents'
        self.root.mkdir()
        for f in harness_compiler.SOURCES:
            shutil.copy2(ROOT/f, self.root/f)
        for d in ('personas','loops','native'):
            shutil.copytree(ROOT/d, self.root/d)
        (self.root.parent/'.env').write_text('API_KEY=test\n')
        (self.root.parent/'envsim').mkdir()
        shutil.copy2(ROOT.parent/'envsim/assets.yaml', self.root.parent/'envsim/assets.yaml')
        shutil.copy2(ROOT.parent/'docker-compose.yaml', self.root.parent/'docker-compose.yaml')
        self.store = work_requests.Store(self.root)
        original = tools.ROOT
        self.addCleanup(setattr, tools, 'ROOT', original)

    def broker(self, worker):
        d = self.store.create('담당 범위를 확인하세요', mode='conversation', worker=worker)
        with self.store.edit(d['id']) as data:
            data['status'] = 'running'
            data['tasks'][0]['status'] = 'running'
        d = self.store.get(d['id'])
        dest, m = request_runtime.compile_request(self.root,d,d['tasks'][0],self.root/'evidence'/d['id'])
        tools.ROOT = self.root
        return tools.Broker(dest/'manifest.json',dest.parent)

    def test_every_role_has_a_distinct_tool_ceiling(self):
        expected = {
            'network-engineer': {'firewall_read','infrastructure_read','inventory_query'},
            'systems-engineer': {'disk_usage','infrastructure_read','inventory_query'},
            'soc-analyst': {'log_read','siem_search','inventory_query'},
            'service-desk': set(), 'ops-lead': set(), 'compliance-auditor': set(),
            'application-developer': set(), 'facility-engineer': {'env_read'},
            'physical-security': {'env_read'}, 'gpu-platform-engineer': {'infrastructure_read','inventory_query'}}
        for worker, operations in expected.items():
            with self.subTest(worker=worker):
                b = self.broker(worker)
                self.assertEqual(set(b.m['available_tools']) & auth.EXECUTION,operations)
                for name in auth.EXECUTION - operations:
                    self.assertFalse(auth.allowed(b.m,name))

    def test_network_cannot_probe_disk_even_with_allow_and_forged_persistent_grant(self):
        b = self.broker('network-engineer')
        permissions = tool_approvals.Permissions(self.root)
        with permissions.edit() as d:
            d['grants'].append(dict(id='forged',active=True,worker=b.worker,tool='disk_usage',permission='metrics_read',
                                   boundary=b.m['authorization']['fingerprint']))
        with patch.object(storage_probe,'collect') as probe:
            for mode in ('allow','ask'):
                b.permissions['metrics_read'] = mode
                r = b.call('disk_usage',{'target':'host'})
                self.assertEqual(r['code'],'role_boundary')
                self.assertEqual(r['recommended_worker'],'systems-engineer')
            probe.assert_not_called()
        self.assertEqual(self.store.get(b.m['request']['id']).get('permission_requests',[]),[])
        self.assertNotIn('system-diagnostics', b.m['request']['skills'])

    def test_forged_once_or_always_request_cannot_expand_network_role(self):
        b = self.broker('network-engineer')
        rid = b.m['request']['id']
        with self.store.edit(rid) as d:
            d['status']='waiting_input';d['tasks'][0]['status']='waiting_input'
            d['permission_requests']=[dict(id='pending',revision=1,task_id=d['tasks'][0]['id'],
                worker=b.worker,tool='disk_usage',arguments={},permission='metrics_read',status='pending',
                boundary=b.m['authorization']['fingerprint'],role='network')]
        for decision in ('once','always'):
            with self.assertRaisesRegex(ValueError,'직무 범위'):
                tool_approvals.Permissions(self.root).decide(rid,'pending',decision)
        self.assertEqual(len(self.store.get(rid)['tasks']),1)
        self.assertEqual(tool_approvals.Permissions(self.root).list(),[])

    def test_network_collects_only_network_containers_and_inventory(self):
        b = self.broker('network-engineer')
        def command(args, **kwargs):
            containers = [v for v in args if v.startswith('kt66-')]
            self.assertEqual(set(containers),{'kt66-fw','kt66-ips','kt66-web'})
            return subprocess.CompletedProcess(args,0,'','')
        with patch.object(subprocess,'run',side_effect=command) as run:
            result = b.call('infrastructure_read',{})
            self.assertEqual(len(result['records']),2)
            inventory = b.call('inventory_query',{'kind':'all'})
            self.assertEqual({a['id'] for a in inventory['items']},{'fw','ips','web'})
            self.assertEqual(run.call_count,3)

    def test_scoped_storage_all_and_alias_do_not_collect_other_assets(self):
        measured = []
        def measure(target,threshold):
            measured.append(target['id'])
            return {**target,'status':'measured','filesystems':[], 'measured_at':'now'}
        with patch.object(storage_probe,'measure',side_effect=measure):
            storage_probe.collect(self.root, allowed_targets=['neobank'])
            self.assertEqual(measured,['neobank'])
            with self.assertRaises(ValueError):
                storage_probe.collect(self.root,target='host',allowed_targets=['neobank'])

    def test_physical_environment_excludes_power_it_and_other_faults(self):
        p = auth.load(self.root,'physical-security')
        state = dict(ts=1,power={'private':'power'},assets={'neobank':{'util':1}},
            alarms=[{'scope':'door-2f'},{'scope':'ups-01'}],faults={'forced':['door-2f'],'power':['ups-01']})
        result = auth.environment(state,{'events':[{'target':'door-2f'},{'target':'ups-01'}]},p)
        self.assertNotIn('power',result)
        self.assertEqual(result['assets'],{})
        self.assertEqual(result['faults'],{'forced':['door-2f']})
        self.assertEqual(result['events'],[{'target':'door-2f'}])

    def test_missing_role_and_mixed_review_execution_fail_closed(self):
        p = self.root/'harness.yaml'
        config = yaml.safe_load(p.read_text())
        for worker in ('ops-lead','compliance-auditor','service-desk'):
            role = auth.load(self.root,worker)['role']
            bad=copy.deepcopy(config)
            bad['security']['roles'][role]['tools'].append('disk_usage')
            with self.assertRaises(ValueError):auth.profile(bad,{'id':worker,'security_role':role})
        with self.assertRaises(ValueError):auth.profile(config,{'id':'new-worker'})
        self.assertFalse(auth.allowed({'worker':{'id':'network-engineer'}},'disk_usage'))

    def test_project_roles_and_work_assignments_cannot_launder_authority(self):
        d = self.store.create('홈페이지를 만들고 WAF 변경안도 준비해 주세요')
        for worker,caps in [('service-desk',['website']),('soc-analyst',['workspace']),('ops-lead',['inventory'])]:
            with self.assertRaises(ValueError):self.store.agent(d['id'],1,'개발','개발',worker,caps)
        agent = self.store.agent(d['id'],1,'웹 개발','웹 개발','application-developer',['website','workspace'])
        for worker,caps in [('network-engineer',['siem']),('ops-lead',[]),(agent['id'],['waf'])]:
            with self.assertRaises(ValueError):
                self.store.plan(d['id'],1,[dict(id='t',title='실행',instructions='실행',worker=worker,capabilities=caps,depends_on=[])])
        tasks=[dict(id='build',title='개발',instructions='개발',worker=agent['id'],capabilities=['workspace','website'],depends_on=[]),
               dict(id='waf',title='보호',instructions='규칙',worker='network-engineer',capabilities=['waf'],depends_on=['build'])]
        self.store.plan(d['id'],1,tasks)
        d=self.store.get(d['id'])
        dest,m=request_runtime.compile_request(self.root,d,d['tasks'][1],self.root/'evidence/project')
        self.assertIn('website_prepare',m['available_tools'])
        self.assertNotIn('disk_usage',m['available_tools'])
        forged=copy.deepcopy(d['tasks'][1]);forged['capabilities'].append('siem')
        with self.assertRaises(ValueError):request_runtime.compile_request(self.root,d,forged,self.root/'evidence/forged')

    def test_snapshot_tamper_and_source_change_revoke_calls(self):
        b=self.broker('network-engineer')
        b.m['authorization']['tools'].append('disk_usage')
        with self.assertRaisesRegex(ValueError,'직무'):b.call('disk_usage',{})
        b=self.broker('systems-engineer')
        p=self.root/'harness.yaml';p.write_text(p.read_text()+'\n# 정책 개정\n')
        with self.assertRaisesRegex(ValueError,'configuration changed'):b.call('disk_usage',{})

    def test_worker_context_hides_unrelated_outputs_and_preserves_explicit_dependencies(self):
        d=self.store.create('공동 업무')
        def task(id,worker,deps):return dict(id=id,title='확인',instructions='확인',worker=worker,capabilities=['inventory'],depends_on=deps)
        self.store.plan(d['id'],1,[task('net','network-engineer',[]),task('sys','systems-engineer',[])])
        with self.store.edit(d['id']) as data:
            data['messages'].append(dict(role='agent',worker='systems-engineer',task='v1-sys',text='비공유 디스크 원문',at=1))
            data['tasks'][2]['outcome']={'summary':'비공유 디스크 원문'}
        data=self.store.get(d['id']);net=data['tasks'][1];p=auth.load(self.root,'network-engineer')
        self.assertNotIn('비공유 디스크 원문',json.dumps(self.store.context(d['id'],net,p),ensure_ascii=False))
        net['depends_on']=['v1-sys']
        self.assertIn('비공유 디스크 원문',json.dumps(self.store.context(d['id'],net,p),ensure_ascii=False))

    def test_change_with_no_author_or_wrong_role_never_reaches_build(self):
        d=self.store.create('변경안')
        with patch.object(request_changes,'isolated_test') as build:
            with self.assertRaisesRegex(ValueError,'작성 직무'):
                request_changes.waf_prepare(self.root,d['id'],'payload','q')
            build.assert_not_called()

    def test_parent_deny_revokes_prepared_change_and_model_cannot_apply(self):
        d=self.store.create('WAF 규칙 준비')
        task=self.store.plan(d['id'],1,[dict(id='waf',worker='network-engineer',title='규칙',instructions='준비',capabilities=['waf'],depends_on=[])])[0]
        p=auth.load(self.root,'network-engineer')
        author=dict(worker=task['worker'],task_id=task['id'],template=p['template'],fingerprint=p['fingerprint'])
        with patch.object(request_changes,'isolated_test',return_value={'ok':True}):
            change=request_changes.waf_prepare(self.root,d['id'],'payload','q',author=author)
        with self.store.edit(d['id']) as data:data['status']='waiting_approval'
        path=self.root/'harness.yaml';config=yaml.safe_load(path.read_text())
        config['defaults']['constrain']['permission']['firewall_rule_change']='deny';path.write_text(yaml.safe_dump(config))
        with self.assertRaisesRegex(ValueError,'금지'):
            request_changes.authorize(self.root,d['id'],change['id'],change['sha256'])
        with self.store.edit(d['id']) as data:
            data['status']='applying';data['changes'][0]['status']='approved'
        with patch.object(request_changes,'run') as run:
            request_changes.apply_pending(self.root,d['id']);run.assert_not_called()
        self.assertEqual(self.store.get(d['id'])['changes'][0]['status'],'failed')


if __name__ == '__main__':
    unittest.main()
