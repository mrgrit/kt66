"""정상 대조군·오진 방지·실패 자료·권한 경계를 결정적으로 검증한다."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import network_probe as probe
import execution_evidence as evidence
import harness_compiler as compiler
import harness_tools as tools
from request_tools import localize_alerts


def healthy():
    observations = {a: {'state': 'running', 'routes': []} for a in probe.ASSETS}
    observations.update(host_bridge_filter='0', http={'attempted': True, 'code': 200})
    for a, dest, gw in [('fw','10.20.32.0/24','10.20.31.2'), ('ips','default','10.20.31.1'),
                        ('web','default','10.20.32.1'), ('attacker','default','10.20.30.1')]:
        observations[a]['routes'].append({'dst':dest,'gateway':gw})
    rows = [{'chain': {'table':'six_filter','name':name,'policy':'accept'}} for name in ('input','forward','output')]
    for port in (80,443,*range(8001,8008)):
        rows.append({'rule': {'table':'six_nat','chain':'prerouting','expr': [
            {'match': {'op':'==','left': {'payload': {'protocol':'ip','field':'daddr'}},'right':'10.20.30.1'}},
            {'match': {'op':'==','left': {'payload': {'protocol':'tcp','field':'dport'}},'right':port}},
            {'dnat': {'addr':'10.20.32.80','port':port}}]}})
    observations['fw']['nft'] = {'nftables':rows}
    return observations, {'chain_policies':{n:'accept' for n in ('input','forward','output')},'web_ip':'10.20.32.80'}


class Diagnosis(unittest.TestCase):
    def test_server_converts_siem_timestamp_without_model_arithmetic(self):
        result={'records':[{'timestamp':'2026-10-04T19:48:07.905+0000'}],
                'requested_range':{'start':'2026-10-04T19:22:00+00:00','end':'2026-10-04T20:22:00+00:00'}}
        localize_alerts(result,'Asia/Seoul')
        self.assertEqual(result['records'][0]['timestamp_local'],'2026-10-05T04:48:07.905000+09:00')
        self.assertEqual(result['requested_range_local']['end'],'2026-10-05T05:22:00+09:00')

    def test_normal_and_false_ticket_premises_do_not_create_faults(self):
        for claim in ('정기 점검', '방화벽이 고장났음', 'DNS가 원인임', 'ips를 재시작해야 함'):
            with self.subTest(claim=claim):
                observations, baseline = healthy()
                observations['ticket_claim'] = claim
                self.assertEqual(probe.assess(observations,baseline)['decision'],'no_fault')

    def test_faults_are_observations_not_assumed_root_causes(self):
        for field, value, failure in [('state','exited','fw:running'), ('routes',[],'fw:route:10.20.32.0/24')]:
            observations, baseline = healthy();observations['fw'][field] = value
            result = probe.assess(observations,baseline)
            self.assertEqual(result['decision'],'fault_observed');self.assertIn(failure,result['failed'])

    def test_bridge_regression_and_http_timeout_are_detected(self):
        observations, baseline = healthy();observations['host_bridge_filter']='1'
        self.assertIn('host:bridge-filter',probe.assess(observations,baseline)['failed'])
        observations, baseline = healthy();observations['http']={'attempted':True,'code':None,'exit_code':28}
        self.assertIn('path:neobank-http',probe.assess(observations,baseline)['failed'])

    def test_unavailable_evidence_is_not_healthy_or_a_fault(self):
        for key in ('state','routes','nft'):
            observations, baseline = healthy();observations['fw'][key]=None
            self.assertEqual(probe.assess(observations,baseline)['decision'],'insufficient_evidence')
        observations, baseline = healthy();observations['http']={'attempted':False}
        self.assertEqual(probe.assess(observations,baseline)['decision'],'insufficient_evidence')

    def test_counter_growth_does_not_create_configuration_drift(self):
        a={'rule':{'handle':1,'expr':[{'counter':{'packets':1,'bytes':60}}]}}
        b=copy.deepcopy(a);b['rule'].update(handle=99);b['rule']['expr'][0]['counter']['packets']=999
        self.assertEqual(probe.structural(a),probe.structural(b))
        self.assertNotEqual(probe.structural({'quota':{'bytes':1000}}),probe.structural({'quota':{'bytes':2000}}))

    def test_dnat_without_destination_scope_is_a_regression(self):
        observations, baseline = healthy()
        for row in observations['fw']['nft']['nftables']:
            if 'rule' in row:row['rule']['expr'].pop(0)
        self.assertEqual(len(probe.assess(observations,baseline)['failed']),9)

    def test_final_report_or_work_status_alone_is_not_an_observation(self):
        self.assertEqual(evidence.summarize([{'tool':'work_status','result':{}},
            {'tool':'request_finish','result':{'status':'completed'}}])['state'],'unverified')
        self.assertEqual(evidence.summarize([{'tool':'network_probe','result':{'status':'denied'}}])['state'],'failed')

    def test_proof_links_to_receipts_and_distinguishes_missing_skill(self):
        observations, baseline=healthy();result=probe.assess(observations,baseline)
        rows=[{'tool':'network_probe','result':result}]
        self.assertEqual(evidence.summarize(rows)['missing_skill_receipts'],['network-diagnosis'])
        rows.insert(0,{'tool':'skill_read','arguments':{'name':'network-diagnosis'},'result':{'sha256':'abc'}})
        proof=evidence.summarize(rows)
        self.assertEqual(proof['missing_skill_receipts'],[])
        self.assertEqual(proof['checks'][0]['reference'],'tools.jsonl#L2')


class SkillGateway(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'agents';self.root.mkdir()
        for source in compiler.SOURCES:shutil.copy2(ROOT/source,self.root/source)
        for name in ('native','loops'):shutil.copytree(ROOT/name,self.root/name)
        (self.root.parent/'.env').write_text('API_KEY=test\n')

    def test_skill_gate_is_enforced_before_collection_and_role_still_wins(self):
        dest, manifest=compiler.compile_worker('network-engineer',self.root)
        with patch.object(tools,'ROOT',self.root), patch.object(probe,'collect',return_value={'baseline':{'files':[]},'snapshot':'/tmp/test'}) as collect:
            broker=tools.Broker(dest/'manifest.json',self.root/'evidence/run')
            self.assertEqual(broker.call('network_probe',{})['code'],'skill_required');collect.assert_not_called()
            broker.call('skill_read',{'name':'network-diagnosis'})
            broker.call('network_probe',{});collect.assert_called_once()
            self.assertEqual(broker.call('disk_usage',{'target':'all'})['code'],'role_boundary')
            body=broker.call('skill_read',{'name':'network-diagnosis','resource':'references/fault-index.md'})
            self.assertIn('sha256',body)
            with self.assertRaises(ValueError):broker.call('skill_read',{'name':'network-diagnosis','resource':'../harness.yaml'})
        self.assertTrue((dest/'.codex/agents/network-engineer.toml').is_file())
        self.assertTrue((dest/'.claude/agents/network-engineer.md').is_file())


if __name__ == '__main__':unittest.main()
