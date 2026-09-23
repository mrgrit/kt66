"""설정 사본에서 UI API → 원본 → 실제 Claude/Codex 하네스 연결을 검증한다."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agentops"), str(ROOT / "agents")]
import configuration
import harness_compiler


def fixture(target):
    target.mkdir(parents=True)
    for name in configuration.CORE:
        shutil.copy2(ROOT / "agents" / name, target / name)
    for name in ("personas", "loops", "native"):
        shutil.copytree(ROOT / "agents" / name, target / name)


class ConfigurationApi(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "agents"
        fixture(self.root)
        with patch.dict(os.environ, {"AGENTS_DIR": str(self.root), "API_KEY": "test-key"}):
            spec = importlib.util.spec_from_file_location("settings_app", ROOT / "agentops/app.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.client = TestClient(self.module.app)
        self.headers = {"X-API-Key": "test-key"}

    def call(self, method, path, body=None, status=200):
        response = self.client.request(method, path, headers=self.headers, json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def create_skill(self):
        content = "---\nname: training-check\ndescription: 교육용 사실 확인 절차\n---\n\n# 절차\n근거와 미확인을 구분한다.\n"
        self.call("POST", "/api/skills", {"name": "training-check", "content": content})
        return content

    def detail(self, wid="soc-analyst"):
        return self.call("GET", "/api/assignments/" + wid)

    def assignment_body(self, d):
        return {k: d[k] for k in ("sha256", "description", "instructions", "skills")} | {
            k: d["worker"].get(k, []) for k in ("assets", "loops")}

    def manifest(self, wid):
        worker = next(w for w in yaml.safe_load((self.root / "roster.yaml").read_text())["workers"] if w["id"] == wid)
        return json.loads((self.root / "runtimes" / worker["runtime"] / "rendered" / wid / "manifest.json").read_text())

    def test_defaults_and_effective_role_are_consistent(self):
        org = self.call("GET", "/api/org")
        self.assertEqual(org["errors"], [])
        self.assertEqual(len(org["roster"]["workers"]), 13)
        self.assertEqual(len(org["loop_details"]), 15)
        detail = self.detail("network-engineer")
        self.assertNotIn("disk_usage", str(detail["available_tools"]))
        self.assertIn("network", str(detail["authorization"]))
        self.assertEqual(self.call("GET", "/api/config-audit")["errors"], [])

    def test_sensitive_endpoints_require_header_key(self):
        for method, path in [
            ("GET", "/api/assignments/soc-analyst"), ("PUT", "/api/assignments/soc-analyst"),
            ("GET", "/api/skills/inventory-report"), ("PUT", "/api/skills/inventory-report"),
            ("DELETE", "/api/skills/inventory-report"), ("POST", "/api/skills")]:
            for suffix in ("", "?key=test-key"):
                with self.subTest(method=method, path=path + suffix):
                    r = self.client.request(method, path + suffix, json={})
                    self.assertEqual(r.status_code, 401, r.text)

    def test_skill_roundtrip_two_runtimes_preserves_authority_and_immutable_history(self):
        content = self.create_skill()
        previous = {}
        for wid in ("soc-analyst", "network-engineer"):
            d = self.detail(wid)
            previous[wid] = d
            body = self.assignment_body(d)
            body["skills"].append("training-check")
            body["instructions"] += "\n교육용 스킬로 근거를 확인하세요.\n"
            saved = self.call("PUT", "/api/assignments/" + wid, body)
            self.assertEqual(saved["authorization"], d["authorization"])
            self.assertEqual(set(saved["available_tools"]) - {"skill_read"}, set(d["available_tools"]) - {"skill_read"})
            self.assertIn("training-check", saved["skills"])
            self.assertIn("training-check", self.manifest(wid)["role_skills"])
        catalog = self.call("GET", "/api/skills")
        row = next(s for s in catalog["skills"] if s["name"] == "training-check")
        self.assertEqual({w["id"] for w in row["workers"]}, set(previous))
        self.call("DELETE", "/api/skills/training-check", {"sha256": row["sha256"]}, 409)
        old_manifest = self.manifest("soc-analyst")
        old_version = old_manifest["version"]
        changed = content + "\n판단 근거를 두 개 이상 제시하세요.\n"
        self.call("PUT", "/api/skills/training-check", {"sha256": row["sha256"], "content": changed})
        new_manifest = self.manifest("soc-analyst")
        self.assertNotEqual(new_manifest["version"], old_version)
        self.assertNotEqual(new_manifest["role_skills"], old_manifest["role_skills"])
        self.assertTrue(all(w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))
        self.call("PUT", "/api/skills/training-check", {"sha256": row["sha256"], "content": content}, 409)
        for wid in previous:
            d = self.detail(wid); body = self.assignment_body(d)
            body["skills"].remove("training-check")
            self.call("PUT", "/api/assignments/" + wid, body)
        current = self.call("GET", "/api/skills/training-check")
        self.call("DELETE", "/api/skills/training-check", {"sha256": current["sha256"]})
        path = configuration.skill_path(self.root, "training-check")
        self.assertFalse(path.exists())
        backups = sorted((self.root / ".bak").glob("native__.agents__skills__training-check__SKILL.md.*"))
        self.assertTrue(backups)
        restored = self.call("POST", "/api/restore?key=test-key&name=" + backups[-1].name)
        self.assertEqual(restored["restored"], "native/.agents/skills/training-check/SKILL.md")
        self.assertEqual(path.read_text(), changed)

    def test_invalid_assignment_does_not_change_any_source(self):
        d = self.detail()
        original = configuration.source_documents(self.root)
        for patch_body in ({"skills": ["missing-skill"]}, {"loops": ["missing-loop"]},
                           {"loops": ["network-health"]}, {"assets": ["fw", "fw"]},
                           {"security_role": "network"}):
            body = self.assignment_body(d) | patch_body
            self.call("PUT", "/api/assignments/soc-analyst", body, 400)
            self.assertEqual(configuration.source_documents(self.root), original)

    def test_stale_assignment_conflicts_and_legacy_persona_is_editable(self):
        d = self.detail("application-developer")
        body = self.assignment_body(d)
        body["instructions"] += "\n한글 교육용 역할 설명.\n"
        self.call("PUT", "/api/assignments/application-developer", body)
        self.call("PUT", "/api/assignments/application-developer", body, 409)
        original = (ROOT / "agents/personas/application-developer.md").read_text()
        self.call("POST", "/api/file/persona:application-developer?key=test-key", {"text": original})

    def test_required_skill_and_retained_persona_references_block_delete(self):
        d = self.call("GET", "/api/skills/inventory-report")
        self.call("DELETE", "/api/skills/inventory-report", {"sha256": d["sha256"]}, 409)
        self.create_skill()
        (self.root / "personas/retained-worker.md").write_text(
            "---\ndescription: 보관 역할\nskills: [training-check]\n---\n지침\n")
        d = self.call("GET", "/api/skills/training-check")
        self.call("DELETE", "/api/skills/training-check", {"sha256": d["sha256"]}, 409)

    def test_invalid_metadata_path_and_symlinks_are_rejected(self):
        for name, content in [
            ("../outside", "abc"),
            ("bad-name", "---\nname: different\ndescription: 잘못된 이름\n---\n본문"),
            ("bad-name", "---\nname: bad-name\ndescription: []\n---\n본문"),
            ("bad-name", "---\nname: [\n---\n본문")]:
            self.call("POST", "/api/skills", {"name": name, "content": content}, 400)
        self.create_skill()
        path = configuration.skill_path(self.root, "training-check")
        outside = self.root.parent / "outside.md"
        outside.write_text("외부 파일"); path.unlink(); path.symlink_to(outside)
        self.call("GET", "/api/skills/training-check", status=400)
        self.assertEqual(outside.read_text(), "외부 파일")

    def test_legacy_persona_and_loop_edits_validate_before_write_and_restore_exact_path(self):
        persona = self.root / "personas/soc-analyst.md"
        original = persona.read_text()
        broken = original.replace("skills:", "skills: [missing-skill]\nprevious_skills:", 1)
        self.call("POST", "/api/file/persona:soc-analyst?key=test-key", {"text": broken}, 400)
        self.assertEqual(persona.read_text(), original)
        changed = original + "\n원문 편집 실습.\n"
        self.call("POST", "/api/file/persona:soc-analyst?key=test-key", {"text": changed})
        backup = next((self.root / ".bak").glob("personas__soc-analyst.md.*"))
        self.call("POST", "/api/restore?key=test-key&name=" + backup.name)
        self.assertEqual(persona.read_text(), original)
        self.assertFalse((self.root / "personas/personas__soc-analyst.md").exists())
        loop_id = self.detail()["worker"]["loops"][0]
        loop_path = self.root / "loops" / (loop_id + ".yaml")
        original_loop = loop_path.read_text()
        for key, value in (("cadence", "99 * * * *"), ("owner", "network-engineer")):
            loop = yaml.safe_load(original_loop); loop[key] = value
            self.call("POST", "/api/file/loop:" + loop_id + "?key=test-key", {"text": yaml.safe_dump(loop)}, 400)
            self.assertEqual(loop_path.read_text(), original_loop)

    def test_runtime_switch_is_atomic_and_new_worker_uses_real_model(self):
        roster = (self.root / "roster.yaml").read_text()
        self.call("PATCH", "/api/worker/soc-analyst?key=test-key", {"runtime": "codex"}, 400)
        self.assertEqual((self.root / "roster.yaml").read_text(), roster)
        models = yaml.safe_load(roster)["models"]
        codex_model = next(k for k, v in models.items() if v["endpoint"] == "codex-cli")
        self.call("PATCH", "/api/worker/soc-analyst?key=test-key", {"runtime": "codex", "model": codex_model})
        self.assertEqual(self.manifest("soc-analyst")["worker"]["runtime"], "codex")
        team = self.detail()["worker"]["team"]
        self.call("POST", "/api/worker?key=test-key", {"id": "training-worker", "name": "교육 담당", "team": team, "security_role": "soc"})
        self.assertEqual(self.detail("training-worker")["skills"], [])
        self.call("DELETE", "/api/worker/training-worker?key=test-key&keep_persona=false")
        self.assertFalse((self.root / "personas/training-worker.md").exists())

    def test_guide_editor_uses_same_source_and_activation(self):
        path = "/api/request-guides/.agents/skills/siem-period-analysis/SKILL.md"
        d = self.call("GET", path)
        self.call("PUT", path, {"sha256": d["sha256"], "content": d["content"] + "\n교육용 출력 기준.\n"})
        current = self.call("GET", "/api/skills/siem-period-analysis")
        self.assertIn("교육용 출력 기준", current["content"])
        self.assertTrue(all(w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))
        self.call("PUT", path, {"sha256": current["sha256"], "content": "---\nname: [\n---\n본문"}, 400)

    def test_explicit_render_activates_and_restored_loop_keeps_original_location(self):
        self.call("POST", "/api/render?key=test-key")
        self.assertTrue(all(w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))
        loop_id = self.detail()["worker"]["loops"][0]
        path = self.root / "loops" / (loop_id + ".yaml")
        original = path.read_text()
        self.call("POST", "/api/file/loop:" + loop_id + "?key=test-key", {"text": original + "\n# 교육용 수정\n"})
        backup = next((self.root / ".bak").glob("loops__" + loop_id + ".yaml.*"))
        self.call("POST", "/api/restore?key=test-key&name=" + backup.name)
        self.assertEqual(path.read_text(), original)
        self.assertTrue(all(w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))

    def test_audit_detects_external_changes(self):
        harness_compiler.compile_all(self.root)
        self.assertTrue(all(w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))
        path = self.root / "personas/soc-analyst.md"
        path.write_text(path.read_text() + "\n외부 수정\n")
        self.assertTrue(all(not w["current"] for w in self.call("GET", "/api/config-audit")["workers"]))


if __name__ == "__main__":
    unittest.main()
