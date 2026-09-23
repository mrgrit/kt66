"""신규 실물은 포트 응답·등록 관측·미수집 전력을 구분한다."""
import asyncio
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock
import httpx
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'noc'))
from remote_hosts import RemoteHosts


class RemoteHostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assets=[
            dict(id='dgx-01',ip='211.170.162.110',availability=dict(kind='ssh_tcp',port=8022)),
            dict(id='thor-02',ip='211.170.162.120',availability=dict(kind='ssh_tcp',port=8022))]
        self.now=1000
        self.connect=AsyncMock(side_effect=[True,False,False,True])
        self.probe=RemoteHosts(ttl=60,connect=self.connect,clock=lambda:self.now)

    async def test_cache_rate_limits_all_browser_polls_and_marks_response_semantics(self):
        first=await self.probe.collect(self.assets)
        self.assertTrue(first['dgx-01']['reachable'])
        self.assertFalse(first['thor-02']['reachable'])
        self.assertEqual(first['dgx-01']['source'],'SSH TCP 접속성')
        self.assertEqual(self.connect.await_count,2)
        self.now+=59
        await asyncio.gather(*(self.probe.collect(self.assets) for _ in range(10)))
        self.assertEqual(self.connect.await_count,2)
        self.now+=2
        result=await self.probe.collect(self.assets)
        self.assertFalse(result['dgx-01']['reachable'])
        self.assertTrue(result['thor-02']['reachable'])
        self.assertEqual(self.connect.await_count,4)

    async def test_endpoint_changes_invalidate_cache_and_removed_assets_disappear(self):
        await self.probe.collect(self.assets)
        self.assets[0]['ip']='211.170.162.111'
        result=await self.probe.collect(self.assets[:1])
        self.assertEqual(set(result),{'dgx-01'})
        self.assertEqual(self.connect.await_count,3)
        self.connect.assert_awaited_with('211.170.162.111',8022)

    async def test_unregistered_or_invalid_endpoints_never_connect(self):
        bad=[dict(id='not-enabled',ip='211.170.162.110'),
             dict(id='bad-port',ip='211.170.162.110',availability=dict(kind='ssh_tcp',port=True)),
             dict(id='bad-host',ip='host/commands',availability=dict(kind='ssh_tcp',port=8022)),
             dict(id='bad-kind',ip='211.170.162.110',availability=dict(kind='http',port=8022))]
        self.assertEqual(await self.probe.collect(bad),{})
        self.connect.assert_not_awaited()

    async def test_unmeasured_gpu_nodes_never_receive_ollama_polling(self):
        # Load only the real collector function: no live simulator or startup tasks.
        tree=ast.parse((ROOT/'envsim/app.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='collect_gpu_util')
        namespace={'httpx':httpx,'assets':{'it_assets':[
            {'id':'new','gpu':True,'remote':'211.170.162.110','telemetry':{'mode':'inventory'}}]}}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'collector-test','exec'),namespace)
        from unittest.mock import patch
        with patch.object(httpx.AsyncClient,'get',new_callable=AsyncMock) as get:
            self.assertEqual(await namespace['collect_gpu_util'](),{})
            get.assert_not_awaited()


class InventoryTests(unittest.TestCase):
    def test_seven_nvidia_devices_and_six_explicit_external_management_endpoints(self):
        layout=yaml.safe_load((ROOT/'envsim/assets.yaml').read_text())
        devices=[a for a in layout['it_assets'] if a.get('vendor')=='NVIDIA']
        self.assertEqual(len(devices),7)
        self.assertEqual(sum(a['form_factor']=='dgx-spark' for a in devices),5)
        self.assertEqual(sum(a['form_factor']=='jetson-thor' for a in devices),2)
        self.assertEqual(len({a['id'] for a in layout['it_assets']}),len(layout['it_assets']))
        new=[a for a in devices if a['id']!='dgx-spark-01']
        self.assertEqual({a['ip'] for a in new},{'211.170.162.'+str(i) for i in (110,111,112,113,120,121)})
        for a in new:
            self.assertEqual((a['floor'],a['rack'],a['zone']),('3F','R-3F-01','gpu-external'))
            self.assertEqual(a['availability'],dict(kind='ssh_tcp',port=8022))
            self.assertEqual(a['idle_kw']+a['rated_kw'],0)
            self.assertEqual(a['telemetry']['mode'],'inventory')
            self.assertIn('checked_at',a['discovery'])
            self.assertNotIn('password',a)


if __name__=='__main__':unittest.main()
