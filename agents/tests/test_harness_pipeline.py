import copy, datetime, json, pathlib, shutil, sys, tempfile, unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import harness_compiler as compiler
import harness_tools as tools
import loop_engine as engine
import yaml

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.temp.name)/"agents";self.root.mkdir()
        for f in compiler.SOURCES:shutil.copy2(ROOT/f,self.root/f)
        for d in ("personas","loops"):shutil.copytree(ROOT/d,self.root/d)
    def tearDown(self):self.temp.cleanup()
    def test_actual_organization_and_permissions_propagate(self):
        dest,before=compiler.compile_worker("facility-engineer",self.root)
        cases=[("company.yaml",["company","vision"],"audit vision"),
               ("departments.yaml",["departments",0,"mission"],"audit mission"),
               ("teams.yaml",["teams",1,"kpi",0,"target"],"audit target"),
               ("harness.yaml",["defaults","constrain","permission","env_read"],"deny")]
        for file,path,value in cases:
            p=self.root/file;original=p.read_text();data=yaml.safe_load(original);node=data
            for key in path[:-1]:node=node[key]
            node[path[-1]]=value;p.write_text(yaml.safe_dump(data,allow_unicode=True))
            generated,after=compiler.compile_worker("facility-engineer",self.root)
            self.assertNotEqual(before["version"],after["version"])
            self.assertIn(value,(generated/"HARNESS.md").read_text())
            self.assertEqual((dest/"manifest.json").read_text(),json.dumps(before,ensure_ascii=False,indent=2))
            p.write_text(original)
    def test_inherited_deny_cannot_be_relaxed(self):
        p=self.root/"harness.yaml";data=yaml.safe_load(p.read_text())
        data["defaults"]["constrain"]["permission"]["env_read"]="deny"
        data["workers"]["facility-engineer"]["constrain"]["permission"]["env_read"]="allow"
        p.write_text(yaml.safe_dump(data));_,m=compiler.compile_worker("facility-engineer",self.root)
        self.assertEqual(m["policy"]["constrain"]["permission"]["env_read"],"deny")
    def test_generated_pointer_is_relative_and_complete(self):
        dest,m=compiler.compile_worker("network-engineer",self.root)
        link=self.root/"runtimes/codex/rendered/network-engineer"
        self.assertTrue(link.is_symlink());self.assertFalse(pathlib.Path(__import__("os").readlink(link)).is_absolute())
        self.assertEqual(link.resolve(),dest.resolve())
        text=(dest/"AGENTS.md").read_text()
        self.assertIn(m["team"]["id"],text);self.assertIn("firewall-drift-check",text)

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.b=tools.Broker.__new__(tools.Broker);self.b.worker="worker";self.b.session=self.root/"session";self.b.session.mkdir()
        self.b.path=self.root/"manifest.json"
        self.b.m={"worker":{"assets":["crac"]},"version":"v1","loops":[]}
        self.b.policy={"escalate":{"to":"approver"}}
        self.b.permissions={"simulation_control":"ask","ticket_create":"allow"}
        self.b.autonomy="L1";self.b.current=lambda:None
        self.rootpatch=patch.object(tools,"ROOT",self.root);self.rootpatch.start()
    def tearDown(self):self.rootpatch.stop();self.temp.cleanup()
    def args(self):return {"fault":"crac_fail","target":"crac-01","reason":"r","evidence":["a","b"],"rollback":"restore baseline"}
    def test_l1_cannot_change_state(self):
        with patch.object(self.b,"clear_fault") as execute:
            self.assertEqual(self.b.call("simulator_control",self.args())["status"],"denied");execute.assert_not_called()
    def test_l2_requires_concrete_approval(self):
        self.b.autonomy="L2"
        with patch.object(self.b,"clear_fault") as execute:
            result=self.b.call("simulator_control",self.args())
            self.assertEqual(result["status"],"approval_pending");execute.assert_not_called()
            record=json.loads(next((self.root/"tickets/approvals").glob("*.json")).read_text())
            self.assertEqual(record["version"],"v1");self.assertEqual(record["approver"],"approver")
    def test_scope_enforced_before_approval(self):
        self.b.autonomy="L2";args=self.args();args["target"]="cctv-01"
        self.assertEqual(self.b.call("simulator_control",args)["status"],"denied")
    def test_malformed_tool_input_rejected(self):
        with self.assertRaises(ValueError):self.b.call("ticket_create",{"title":"x","body":"x","command":"rm"})
        with self.assertRaises(ValueError):self.b.call("simulator_control",{"fault":"x"})
    def test_missing_live_fault_does_not_mutate(self):
        self.b.url="http://unused";self.b.key="unused"
        with patch.object(self.b,"get",side_effect=[{},{"active":{}}]),patch("urllib.request.urlopen") as http:
            with self.assertRaises(ValueError):self.b.clear_fault(self.args())
            http.assert_not_called()

class EngineTests(unittest.TestCase):
    def test_cron_and_day_or(self):
        when=datetime.datetime(2026,9,15,12,30)
        self.assertTrue(engine.cron_matches("*/15 * * * *",when))
        self.assertFalse(engine.cron_matches("0 3 * * *",when))
        with self.assertRaises(ValueError):engine.cron_matches("*/0 * * * *",when)
    def test_queue_idempotence_and_restart(self):
        with tempfile.TemporaryDirectory() as td,patch.object(engine,"ROOT",pathlib.Path(td)):
            (pathlib.Path(td)/"tickets").mkdir()
            db=engine.db_open()
            engine.enqueue(db,"one","w","event",{})
            engine.enqueue(db,"one","w","event",{})
            self.assertEqual(db.execute("select count(*) from jobs").fetchone()[0],1)
            db.execute("update jobs set status='running',attempts=1");db.commit();db.close()
            db=engine.db_open();r=db.execute("select * from jobs").fetchone()
            self.assertEqual(r["status"],"retry");self.assertEqual(r["attempts"],1);db.close()
if __name__=="__main__":unittest.main()
