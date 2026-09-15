import importlib.machinery,json,pathlib,tempfile,time,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
ev=importlib.machinery.SourceFileLoader("test_instructor_source",str(ROOT/"instructor_events.py")).load_module()
defense=importlib.machinery.SourceFileLoader("test_scoped_defense",str(ROOT/"test_defense.py")).load_module()


class InstructorTests(unittest.TestCase):
    def injection(self, **extra):
        return {"handle":"abcd1234","id":"sec_alertstorm","domain":"security",
                "name":"test","target":"kt66-attacker","params":{"rate":1},
                "started":time.time(),**extra}

    def test_handles_are_distinct_and_synthetic_source_is_explicit(self):
        rows=ev.to_alarms([self.injection(),self.injection(handle="abcd5678")])
        self.assertNotEqual(rows[0]["id"],rows[1]["id"])
        self.assertEqual(rows[0]["source"],"instructor")
        self.assertEqual(rows[0]["owner"],"soc-analyst")

    def test_network_routes_to_network_worker(self):
        self.assertEqual(ev.to_alarms([self.injection(domain="network")])[0]["owner"],"network-engineer")

    def test_siem_reads_actual_fixed_log_and_filters_by_handle(self):
        import subprocess
        rows=[{"timestamp":"now","full_log":"kt66-injector abcd1234","rule":{"id":"119662"}},
              {"full_log":"unrelated event","rule":{"id":"999"}}]
        with patch.object(ev.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"\n".join(map(json.dumps,rows)),"")) as call:
            result=ev.siem_records(["abcd1234"])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["rule_id"],"119662")
        self.assertEqual(call.call_args.args[0][-1],"/var/ossec/logs/alerts/alerts.json")

    def test_log_read_failure_is_not_empty_success(self):
        import subprocess
        with patch.object(ev.subprocess,"run",return_value=subprocess.CompletedProcess([],1,"","failed")):
            with self.assertRaises(OSError):ev.siem_records(["abcd1234"])

    def registry(self, root, **extra):
        (root/"tickets").mkdir()
        (root/"tickets/.test-actions.json").write_text(json.dumps({"abcd1234":{
            "action":"stop_test_syslog","target":"kt66-attacker",
            "expires":time.time()+60,**extra}}))

    def test_no_registry_cannot_execute(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(defense.eligible(pathlib.Path(td),self.injection()))

    def test_expired_or_wrong_target_or_rate_cannot_execute(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);self.registry(root)
            self.assertTrue(defense.eligible(root,self.injection()))
            self.assertFalse(defense.eligible(root,self.injection(target="kt66-fw")))
            self.assertFalse(defense.eligible(root,self.injection(params={"rate":25})))
            self.assertFalse(defense.eligible(root,self.injection(id="sec_nftdrift")))
            p=root/"tickets/.test-actions.json";d=json.loads(p.read_text());d["abcd1234"]["expires"]=0;p.write_text(json.dumps(d))
            self.assertFalse(defense.eligible(root,self.injection()))

    def test_no_specific_approval_means_no_control_call(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);self.registry(root)
            with patch.object(defense.urllib.request,"urlopen") as call:
                for decision in ({"decision":"deny","handle":"abcd1234"},{"decision":"approve","handle":"wrong"}):
                    with self.assertRaises(ValueError):defense.stop(root,"http://noc",self.injection(),decision)
                call.assert_not_called()

    def test_unrelated_ncat_blocks_legacy_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);self.registry(root)
            with patch.object(defense,"process_evidence",return_value={"owned_pids":[10],"unrelated_ncat_count":1}),patch.object(defense.urllib.request,"urlopen") as call:
                with self.assertRaises(ValueError):defense.stop(root,"http://noc",self.injection(),{"decision":"approve","handle":"abcd1234"})
                call.assert_not_called()

    def test_executor_verifies_process_exit_and_active_handle_removal(self):
        import io
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)/"agents";root.mkdir();self.registry(root)
            (root.parent/".env").write_text("API_KEY=test-only\n")
            with patch.object(defense,"process_evidence",side_effect=[
                    {"owned_pids":[123],"unrelated_ncat_count":0},
                    {"owned_pids":[],"unrelated_ncat_count":0}]), \
                 patch.object(defense.urllib.request,"urlopen",side_effect=[
                    io.BytesIO(b'{"cleared":"abcd1234"}'),io.BytesIO(b'{"active":[]}')]):
                result=defense.stop(root,"http://noc",self.injection(),{"decision":"approve","handle":"abcd1234"})
            self.assertTrue(result["verified"])

    def test_remaining_process_cannot_be_reported_as_defense_success(self):
        import io
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)/"agents";root.mkdir();self.registry(root)
            (root.parent/".env").write_text("API_KEY=test-only\n")
            with patch.object(defense,"process_evidence",return_value={"owned_pids":[123],"unrelated_ncat_count":0}), \
                 patch.object(defense.urllib.request,"urlopen",side_effect=[
                    io.BytesIO(b'{"cleared":"abcd1234"}'),io.BytesIO(b'{"active":[]}')]):
                result=defense.stop(root,"http://noc",self.injection(),{"decision":"approve","handle":"abcd1234"})
            self.assertFalse(result["verified"])

if __name__=="__main__":unittest.main()
