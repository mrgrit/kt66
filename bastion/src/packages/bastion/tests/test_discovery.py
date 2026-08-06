"""Phase B 단위 테스트 — targets resolver(무회귀) + discovery 추론/파싱.

핵심: discovery 미적용 시 resolver 가 기존 kt66 컨테이너 이름을 그대로 반환해야 한다
(무회귀). discovery 적용 시 발견된 이름으로 적응.
실행: PYTHONPATH=/opt/ccc-src:/opt/ccc-src/packages python3 -m unittest bastion.tests.test_discovery
"""
from __future__ import annotations
import os
import unittest

from bastion import targets
from bastion import discovery


class TestTargetsRegression(unittest.TestCase):
    """discovery off → 정적 kt66 폴백 = 기존 동작 100% 동일."""

    def setUp(self):
        os.environ.pop("BASTION_DISCOVERY", None)
        discovery._DISCOVERED.clear()

    def test_static_fallback_kt66(self):
        self.assertEqual(targets.container_for("attacker"), "kt66-attacker")
        self.assertEqual(targets.container_for("ids"), "kt66-ips")
        self.assertEqual(targets.container_for("siem"), "kt66-siem")
        self.assertEqual(targets.container_for("web"), "kt66-web")
        self.assertEqual(targets.container_for("fw"), "kt66-fw")

    def test_wrap_docker_exec(self):
        et = targets.resolve_target("ids", {"bastion": "127.0.0.1"})
        ip, script = et.wrap("pgrep suricata")
        self.assertEqual(ip, "127.0.0.1")
        self.assertEqual(script, 'docker exec kt66-ips sh -c "pgrep suricata"')

    def test_unknown_role_fallback(self):
        # 미지 역할은 kt66-<role> 로 폴백(정적 안전망)
        self.assertEqual(targets.container_for("portal"), "kt66-portal")


class TestTargetsDiscovery(unittest.TestCase):
    """discovery on → 발견 매핑 사용. off 면 무시."""

    def tearDown(self):
        os.environ.pop("BASTION_DISCOVERY", None)
        discovery._DISCOVERED.clear()

    def test_discovered_used_when_enabled(self):
        discovery._DISCOVERED.update({"ids": "soc-suricata", "siem": "soc-wazuh"})
        os.environ["BASTION_DISCOVERY"] = "1"
        self.assertEqual(targets.container_for("ids"), "soc-suricata")
        self.assertEqual(targets.container_for("siem"), "soc-wazuh")
        # 발견 안 된 역할은 정적 폴백
        self.assertEqual(targets.container_for("fw"), "kt66-fw")

    def test_discovered_ignored_when_disabled(self):
        discovery._DISCOVERED.update({"ids": "soc-suricata"})
        os.environ.pop("BASTION_DISCOVERY", None)
        self.assertEqual(targets.container_for("ids"), "kt66-ips")  # 정적


class TestInferRole(unittest.TestCase):
    def test_kt66_names(self):
        cases = {
            "kt66-ips": "ids", "kt66-siem": "siem", "kt66-web": "web",
            "kt66-fw": "fw", "kt66-attacker": "attacker",
            "kt66-wazuh-indexer": "indexer", "kt66-wazuh-dashboard": "dashboard",
            "kt66-portal": "portal",
        }
        for name, expected in cases.items():
            self.assertEqual(discovery.infer_role(name, name), expected,
                             f"{name} → {expected}")

    def test_image_based(self):
        self.assertEqual(discovery.infer_role("c1", "owasp/modsecurity-crs"), "web")
        self.assertEqual(discovery.infer_role("x", "ollama/ollama"), "ai-model")

    def test_unknown(self):
        self.assertIsNone(discovery.infer_role("random-thing", "alpine"))


class TestDiscoverInfra(unittest.TestCase):
    def setUp(self):
        self._orig = discovery.run_command
        fake_ps = ("kt66-ips|kt66-ips|Up 1h|\n"
                   "kt66-siem|kt66-siem:custom|Up 1h (healthy)|\n"
                   "kt66-web|kt66-web|Up 1h|0.0.0.0:80->80/tcp\n"
                   "kt66-fw|kt66-fw|Up 1h|\n"
                   "kt66-attacker|kt66-attacker|Up 1h|\n"
                   "kt66-wazuh-indexer|wazuh/wazuh-indexer|Up 1h|\n")
        discovery.run_command = lambda ip, script, timeout=20: {"stdout": fake_ps, "exit_code": 0}

    def tearDown(self):
        discovery.run_command = self._orig
        discovery._DISCOVERED.clear()

    def test_role_map_built(self):
        d = discovery.discover_infra({"bastion": "127.0.0.1"}, register_assets=False)
        self.assertEqual(d["count"], 6)
        rm = d["role_map"]
        self.assertEqual(rm.get("ids"), "kt66-ips")
        self.assertEqual(rm.get("siem"), "kt66-siem")
        self.assertEqual(rm.get("web"), "kt66-web")
        self.assertEqual(rm.get("fw"), "kt66-fw")
        self.assertEqual(rm.get("attacker"), "kt66-attacker")
        self.assertEqual(rm.get("indexer"), "kt66-wazuh-indexer")
        # 발견 후 캐시 조회
        self.assertEqual(discovery.get_discovered_container("ids"), "kt66-ips")


if __name__ == "__main__":
    unittest.main(verbosity=2)
