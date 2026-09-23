import asyncio
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'noc'))
from envsim.service_inventory import AIServiceInventory


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.calls = []
        self.asset = dict(id='gpu', ip='192.0.2.10', serving=[
            dict(id='ollama', platform='ollama', port=11434)])

    def inventory(self, handler):
        def request(req):
            self.calls.append((req.method, str(req.url)))
            self.assertEqual(req.method, 'GET')
            return handler(req)
        return AIServiceInventory(transport=httpx.MockTransport(request), clock=lambda: self.now)

    async def test_catalog_and_resident_models_differ_and_are_cached_across_clients(self):
        def response(req):
            payload = {'/api/tags': {'models': [{'name': 'a:7b'}, {'name': 'z:32b'}]},
                       '/api/ps': {'models': [{'name': 'z:32b', 'size': 40, 'size_vram': 30, 'context_length': 8192}]},
                       '/api/version': {'version': '1.2.3'}}[req.url.path]
            return httpx.Response(200, json=payload)
        inv = self.inventory(response)
        answers = await asyncio.gather(*(inv.collect(self.asset) for _ in range(15)))
        self.assertEqual(len(self.calls), 3)
        s = answers[0]['services'][0]
        self.assertEqual(s['model_count'], 2)
        self.assertEqual(s['loaded_count'], 1)
        self.assertEqual(s['models'][0]['name'], 'z:32b')
        self.assertEqual(s['loaded'][0]['size_vram'], 30)
        self.now += 61
        await inv.collect(self.asset)
        self.assertEqual(len(self.calls), 6)

    async def test_empty_loaded_list_is_distinct_from_authentication_or_malformed_response(self):
        inv = self.inventory(lambda req: httpx.Response(200, json={'models': []}))
        empty = (await inv.collect(self.asset))['services'][0]
        self.assertEqual(empty['loaded'], [])
        for payload, status in [(None, 401), ({'detail': 'not a model list'}, 200), ({'models': [None]}, 200)]:
            with self.subTest(status=status, payload=payload):
                inv = self.inventory(lambda req: httpx.Response(status, json=payload))
                failed = (await inv.collect(self.asset))['services'][0]
                self.assertIsNone(failed['loaded'])
                self.assertIsNone(failed['models'])
                self.assertIn('loaded_error', failed)

    async def test_vllm_and_llamacpp_catalog_does_not_claim_memory_residency(self):
        for platform in ['vllm', 'llama.cpp']:
            self.asset['serving'] = [dict(id=platform, platform=platform, port=8001)]
            inv = self.inventory(lambda req: httpx.Response(200, json={'data': [
                dict(id='writer', root='/models/actual-model', max_model_len=16384)]}))
            service = (await inv.collect(self.asset))['services'][0]
            self.assertIsNone(service['loaded'])
            self.assertEqual(service['models'][0]['base_model'], 'actual-model')
            self.assertEqual(self.calls[-1][1], 'http://192.0.2.10:8001/v1/models')

    async def test_redirects_invalid_endpoints_and_unregistered_services_do_not_expand_targets(self):
        inv = self.inventory(lambda req: httpx.Response(302, headers={'Location': 'http://127.0.0.1/secret'}))
        result = await inv.collect(self.asset)
        self.assertEqual(len(self.calls), 3)
        self.assertIn('302', result['services'][0]['status'])
        for config in [dict(platform='shell', port=22), dict(platform='ollama', port=True)]:
            bad = copy.deepcopy(self.asset); bad['serving'] = [config]
            await inv.collect(bad)
        self.assertEqual(len(self.calls), 3)
        bad = dict(self.asset, ip='example.com/path', serving=[])
        self.assertIn('error', await inv.collect(bad))
        self.assertEqual(len(self.calls), 3)

    async def test_config_change_refreshes_cache_and_network_failure_clears_current_models(self):
        inv = self.inventory(lambda req: httpx.Response(200, json={'models': [{'name': 'old'}]}))
        await inv.collect(self.asset)
        self.asset['serving'][0]['port'] = 11435
        await inv.collect(self.asset)
        self.assertEqual(len(self.calls), 6)
        def fail(req): raise httpx.ConnectError('credentials must not leak', request=req)
        inv.transport = httpx.MockTransport(fail)
        self.now += 61
        result = (await inv.collect(self.asset))['services'][0]
        self.assertIsNone(result['models'])
        self.assertNotIn('credentials', str(result))

    async def test_custom_health_model_and_large_catalog_are_bounded(self):
        self.asset['serving'] = [dict(id='embed', platform='health', port=8016)]
        inv = self.inventory(lambda req: httpx.Response(200, json={'ok': True, 'model': '/models/embed-v2'}))
        s = (await inv.collect(self.asset))['services'][0]
        self.assertEqual(s['models'], [{'name': 'embed-v2'}])
        self.assertIsNone(s['loaded'])
        self.asset['serving'] = [dict(id='vllm', platform='vllm', port=8001)]
        inv = self.inventory(lambda req: httpx.Response(200, json={'data': [{'id': str(n)} for n in range(350)]}))
        s = (await inv.collect(self.asset))['services'][0]
        self.assertEqual(s['model_count'], 350)
        self.assertEqual(len(s['models']), 200)


class FacilityLayoutTests(unittest.TestCase):
    def test_outdoor_equipment_has_no_floor_and_every_facility_has_a_guide(self):
        assets = yaml.safe_load((ROOT/'envsim/assets.yaml').read_text())
        guides = yaml.safe_load((ROOT/'envsim/facility-guide.yaml').read_text())['types']
        outside = set()
        for kind, rows in assets['facility'].items():
            for item in rows if isinstance(rows, list) else [rows]:
                key = item.get('kind',kind) if kind in ['microgrid','fire','security','automation'] else kind
                self.assertIn(key, guides, item['id'])
                if item.get('location') == 'outdoor':
                    outside.add(item['id'])
                    self.assertIsNone(item['floor'])
                    self.assertEqual(len(item['site_pos']), 2)
                else:
                    self.assertIn(item['floor'], ['1F','2F','3F','4F'])
        self.assertEqual(len(outside), 12)
        self.assertTrue({'ct-01','ct-02','gen-01','tank-fuel-01','wx-01','guard-01'} <= outside)
        self.assertNotIn('chiller-01', outside)

    def test_moving_facilities_keeps_power_cooling_and_fault_propagation(self):
        sys.path.insert(0, str(ROOT/'envsim'))
        from model import Simulator
        assets = yaml.safe_load((ROOT/'envsim/assets.yaml').read_text())
        indoor = copy.deepcopy(assets)
        for rows in indoor['facility'].values():
            for item in rows if isinstance(rows,list) else [rows]:
                if item.get('location') == 'outdoor': item['floor'] = '1F'
        with patch('model.time.time', return_value=1000):
            a, b = Simulator(assets), Simulator(indoor)
        for fault, target in [('cooling_tower_fail','*'), ('utility_fail','*'), ('generator_fail','*')]:
            a.inject(fault,target); b.inject(fault,target)
        with patch('model.time.time', return_value=1060):
            a.tick({}); b.tick({})
        for section in ['power','plant','aisles','fuel','battery']:
            self.assertEqual(a.state()[section], b.state()[section], section)


class ServiceRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_noc_uses_only_registered_device_and_explicit_existing_route(self):
        import app as noc
        direct = dict(id='external', ip='192.0.2.10', serving=[{'platform':'ollama','port':11434}])
        routed = dict(direct, id='internal', service_via='envsim')
        with patch.object(noc, 'asset_layout', AsyncMock(return_value={'it_assets':[direct,routed]})), \
             patch.object(noc._ai_services, 'collect', AsyncMock(return_value={'asset_id':'external'})) as collect, \
             patch.object(noc, '_env', AsyncMock(return_value=(200,{'asset_id':'internal'}))) as relay:
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                await noc.ai_services('unknown')
            collect.assert_not_awaited(); relay.assert_not_awaited()
            self.assertEqual((await noc.ai_services('external'))['asset_id'], 'external')
            collect.assert_awaited_once_with(direct); relay.assert_not_awaited()
            response = await noc.ai_services('internal')
            self.assertEqual(response.status_code,200)
            relay.assert_awaited_once_with('GET','/assets/internal/services')
            self.assertEqual(collect.await_count,1)

    async def test_envsim_proxy_rejects_unregistered_or_direct_only_targets(self):
        # Load the actual endpoint without starting live collectors or simulator I/O.
        tree=ast.parse((ROOT/'envsim/app.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_ai_services')
        fn.decorator_list=[]
        from fastapi import HTTPException
        collector=AsyncMock(); collector.collect.return_value={'asset_id':'internal'}
        allowed=dict(id='internal',service_via='envsim',serving=[{'platform':'ollama','port':11434}])
        namespace=dict(assets={'it_assets':[allowed,dict(id='external',serving=[{}])]},
                       service_inventory=collector,HTTPException=HTTPException)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'route-test','exec'),namespace)
        for name in ['unknown','external']:
            with self.assertRaises(HTTPException): await namespace['get_ai_services'](name)
        collector.collect.assert_not_awaited()
        response=await namespace['get_ai_services']('internal')
        self.assertEqual(response['asset_id'],'internal')
        collector.collect.assert_awaited_once_with(allowed)


if __name__ == '__main__': unittest.main()
