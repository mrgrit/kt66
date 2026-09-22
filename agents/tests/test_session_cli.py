import importlib.machinery
import json
import os
import pathlib
import subprocess
import unittest
from unittest.mock import patch
m = importlib.machinery.SourceFileLoader("session_cli_test", str(pathlib.Path(__file__).resolve().parents[1] / "session_cli.py")).load_module()


class SessionTests(unittest.TestCase):
    def success(self, cmd, **kw):
        if "auth" in cmd:
            return subprocess.CompletedProcess(cmd, 0, '{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":"max"}', "")
        if "login" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "Logged in using ChatGPT")
        if "--session-id" in cmd:
            out = {"session_id":cmd[cmd.index("--session-id")+1], "result":"completed review", "usage":{"output_tokens":3}}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
        path = pathlib.Path(cmd[cmd.index("-o")+1])
        path.write_text("completed review")
        return subprocess.CompletedProcess(cmd, 0, '\n'.join([
            '{"type":"thread.started","thread_id":"new-thread"}',
            '{"type":"turn.completed","usage":{"output_tokens":3}}']), "")

    def run_ok(self, rt):
        with patch.object(m.shutil, "which", return_value="/bin/"+rt), \
             patch.object(m.subprocess, "run", side_effect=self.success) as call:
            data=m.run(rt, "haiku" if rt=="claude" else "default", "private task")
        return data,call

    def test_claude_new_session_has_identity_and_subscription(self):
        data,call=self.run_ok("claude")
        self.assertTrue(data["fresh_session"])
        self.assertEqual(data["auth"]["method"],"claude.ai")
        self.assertEqual(len(data["session_id"]),36)
        cmd=call.call_args.args[0]
        self.assertIn("--session-id",cmd)
        self.assertIn("--no-session-persistence",cmd)
        self.assertNotIn("private task",cmd)
        self.assertEqual(call.call_args.kwargs["input"],"private task")
        self.assertEqual(cmd[cmd.index("--tools")+1],"")

    def test_claude_consecutive_jobs_use_different_session_ids(self):
        first,_=self.run_ok("claude")
        second,_=self.run_ok("claude")
        self.assertNotEqual(first["session_id"],second["session_id"])

    def test_codex_new_exec_never_resumes_and_forces_chatgpt(self):
        data,call=self.run_ok("codex")
        cmd=call.call_args.args[0]
        self.assertIn("exec",cmd)
        self.assertNotIn("resume",cmd)
        self.assertNotIn("fork",cmd)
        self.assertIn('forced_login_method="chatgpt"',cmd)
        self.assertIn("--ignore-user-config",cmd)
        self.assertIn("--ignore-rules",cmd)
        self.assertIn("read-only",cmd)
        self.assertIn("shell_tool",cmd)
        self.assertIn("hooks",cmd)
        self.assertNotIn("--oss",cmd)
        self.assertNotIn("--model",cmd)
        self.assertEqual(data["session_id"],"new-thread")

    def test_environment_does_not_pass_api_keys_or_provider_redirects(self):
        leaked={"OPENAI_API_KEY":"test","CODEX_API_KEY":"test","ANTHROPIC_API_KEY":"test",
                "ANTHROPIC_BASE_URL":"test","OPENAI_BASE_URL":"test","CLAUDE_CODE_OAUTH_TOKEN":"test",
                "HTTP_PROXY":"test","CODEX_HOME":"/different-account"}
        with patch.dict(os.environ,leaked):
            clean=m.clean_env()
        self.assertFalse(set(clean)&set(leaked))

    def test_api_authenticated_codex_is_rejected_before_inference(self):
        with patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"","Logged in using an API key")) as call:
            with self.assertRaises(m.SessionError):
                m.authenticated("codex","codex",{})
        self.assertEqual(call.call_count,1)

    def test_api_authenticated_claude_is_rejected(self):
        for data in ({"loggedIn":True,"authMethod":"api_key"}, {"loggedIn":False}, {}):
            with self.subTest(data=data), patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],0,json.dumps(data),"")):
                with self.assertRaises(m.SessionError):
                    m.authenticated("claude","claude",{})

    def test_unsupported_runtime_cannot_fall_back(self):
        for rt in ("bastion","hermes","openai","ollama"):
            with self.assertRaises(m.SessionError):
                m.executable(rt)

    def test_missing_cli_is_error(self):
        with patch.object(m.shutil,"which",return_value=None):
            with self.assertRaises(m.SessionError): m.executable("codex")

    def test_session_nonzero_exit_does_not_return_stderr(self):
        with patch.object(m,"executable",return_value="claude"), \
             patch.object(m,"authenticated",return_value={"method":"claude.ai"}), \
             patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],1,"","private value")):
            with self.assertRaisesRegex(m.SessionError,"session_exit_failed") as ctx:
                m.run("claude","haiku","task")
        self.assertNotIn("private value",str(ctx.exception))

    def test_claude_missing_or_wrong_session_id_is_error(self):
        with patch.object(m,"executable",return_value="claude"), \
             patch.object(m,"authenticated",return_value={"method":"claude.ai"}), \
             patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],0,'{"result":"ok"}',"")):
            with self.assertRaisesRegex(m.SessionError,"session_identity_missing"):
                m.run("claude","haiku","task")

    def test_model_errors_and_bad_json_are_errors(self):
        for output in ('[]','bad','{"is_error":true}'):
            with self.subTest(output=output), patch.object(m,"executable",return_value="claude"), \
                 patch.object(m,"authenticated",return_value={"method":"claude.ai"}), \
                 patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],0,output,"")):
                with self.assertRaises(m.SessionError): m.run("claude","haiku","task")

    def test_codex_missing_completion_is_error(self):
        with patch.object(m,"executable",return_value="codex"), \
             patch.object(m,"authenticated",return_value={"method":"chatgpt"}), \
             patch.object(m.subprocess,"run",return_value=subprocess.CompletedProcess([],0,'{"type":"thread.started","thread_id":"abc"}',"")):
            with self.assertRaises(m.SessionError): m.run("codex","default","task")

    def test_timeout_never_retries_or_falls_back(self):
        with patch.object(m,"executable",return_value="codex"), \
             patch.object(m,"authenticated",return_value={"method":"chatgpt"}), \
             patch.object(m.subprocess,"run",side_effect=subprocess.TimeoutExpired("codex",1)) as call:
            with self.assertRaises(subprocess.TimeoutExpired): m.run("codex","default","task",timeout=1)
        self.assertEqual(call.call_count,1)

    def test_claude_schema_output_becomes_machine_readable_body(self):
        def process(cmd, **kw):
            if "auth" in cmd:
                return self.success(cmd, **kw)
            data={"session_id":cmd[cmd.index("--session-id")+1],
                  "result":"", "structured_output":{"alarm_count":0}}
            return subprocess.CompletedProcess(cmd,0,json.dumps(data),"")
        with patch.object(m.shutil,"which",return_value="claude"), \
             patch.object(m.subprocess,"run",side_effect=process) as call:
            result=m.run("claude","haiku","task",schema={"type":"object"})
        self.assertEqual(json.loads(result["body"]),{"alarm_count":0})
        self.assertIn("--json-schema",call.call_args.args[0])

    def test_codex_schema_passed_as_local_file(self):
        def process(cmd, **kw):
            if "exec" in cmd:
                self.assertIn("--output-schema",cmd)
                self.assertEqual(json.loads(pathlib.Path(cmd[cmd.index("--output-schema")+1]).read_text()),{"type":"object"})
            return self.success(cmd, **kw)
        with patch.object(m.shutil,"which",return_value="codex"), \
             patch.object(m.subprocess,"run",side_effect=process):
            m.run("codex","default","task",schema={"type":"object"})

    def test_roster_retains_operational_workers_and_separate_developer_and_only_session_endpoints(self):
        import yaml
        data=yaml.safe_load((pathlib.Path(__file__).resolve().parents[1]/"roster.yaml").read_text())
        self.assertEqual(len(data["workers"]),10)
        self.assertEqual(len({w["id"] for w in data["workers"]}),10)
        self.assertEqual(sum(len(w.get("loops",[])) for w in data["workers"]),11)
        for w in data["workers"]:
            self.assertIn(w["runtime"],("claude","codex"))
            model=data["models"][w["model"]]
            self.assertEqual(model["endpoint"],{"claude":"claude-code","codex":"codex-cli"}[w["runtime"]])
        for endpoint in data["endpoints"].values():
            self.assertEqual(endpoint["via"],"cli")
            self.assertNotIn("base_url",endpoint)

    def test_paid_model_mapping_fails_before_starting_any_session(self):
        import importlib.machinery,tempfile
        runner=importlib.machinery.SourceFileLoader("policy_runner",str(pathlib.Path(__file__).resolve().parents[1]/"cc-runner")).load_module()
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner,"TICKETS",pathlib.Path(td)), \
             patch.object(runner.agentctl,"load_models",return_value={"bad":{"endpoint":"api","name":"x"}}), \
             patch.object(runner.session_cli,"run") as call:
            alias,body=runner.run_session({"id":"test","runtime":"codex","model":"bad"},"task",False,"test")
        self.assertIsNone(body)
        call.assert_not_called()

if __name__=="__main__": unittest.main()
