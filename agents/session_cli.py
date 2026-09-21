"""Fresh subscription-authenticated worker sessions. No model HTTP client."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid
import yaml
from activity_audit import record, scrub


class SessionError(RuntimeError):
    pass


def session_failure(text, default):
    lowered=str(text).lower()
    if any(term in lowered for term in ("hit your limit", "usage limit", "rate limit", "quota exceeded")):
        return SessionError("subscription_usage_limit")
    return SessionError(default)


def clean_env():
    # Credentials stay in the CLI's existing account store. Never inherit API
    # keys, provider redirects, OAuth overrides, proxies or nested-session flags.
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR",
               "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS"}
    return {k: v for k, v in os.environ.items() if k in allowed}


def executable(runtime):
    if runtime not in ("claude", "codex"):
        raise SessionError("unsupported_session_runtime")
    cli = shutil.which(runtime)
    if not cli:
        raise SessionError("cli_not_installed")
    return cli


def authenticated(runtime, cli, env):
    if runtime == "claude":
        cmd = [cli, "--setting-sources", "", "--settings",
               '{"disableAllHooks":true}', "auth", "status"]
    else:
        cmd = [cli, "login", "status"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
    if p.returncode:
        raise SessionError("subscription_login_required")
    if runtime == "claude":
        try:
            data = json.loads(p.stdout)
        except ValueError:
            raise SessionError("unrecognized_auth_status")
        if not isinstance(data, dict) or not data.get("loggedIn") or data.get("authMethod") != "claude.ai":
            raise SessionError("subscription_login_required")
        return {"method": "claude.ai", "subscription": data.get("subscriptionType")}
    if "Logged in using ChatGPT" not in (p.stdout + p.stderr):
        raise SessionError("chatgpt_login_required")
    return {"method": "chatgpt"}


def run(runtime, model, prompt, timeout=180, schema=None, harness=None, evidence_dir=None):
    record(evidence_dir, "request", {"runtime": runtime, "model": model, "prompt": prompt,
                                    "timeout_seconds": timeout, "source": "session_cli"})
    try:
        result = _run(runtime, model, prompt, timeout, schema, harness, evidence_dir)
    except Exception as exc:
        failure = {"status":"failed", "runtime":runtime, "error":str(exc) if isinstance(exc, SessionError) else type(exc).__name__}
        record(evidence_dir, "session.failed", failure)
        if evidence_dir is not None:
            (pathlib.Path(evidence_dir) / "failure.json").write_text(json.dumps(failure))
        raise
    record(evidence_dir, "session.completed", {"session_id": result["session_id"],
           "usage": result.get("usage", {}), "outcome": result["body"]})
    if evidence_dir is not None:
        # Also covers cc-runner sessions, whose previous log only stored usage.
        (pathlib.Path(evidence_dir) / "session-result.json").write_text(json.dumps(scrub(result), ensure_ascii=False))
    return result


def _run(runtime, model, prompt, timeout=180, schema=None, harness=None, evidence_dir=None):
    cli, env = executable(runtime), clean_env()
    auth = authenticated(runtime, cli, env)
    with tempfile.TemporaryDirectory(prefix="kt66-worker-session-") as td:
        session_cwd = td
        if harness is not None:
            harness = pathlib.Path(harness)
            manifest = json.loads((harness / "manifest.json").read_text())
            evidence_dir = pathlib.Path(evidence_dir)
            evidence_dir.mkdir(parents=True, exist_ok=True)
            session_cwd = str(harness)
            instructions = (harness / ("AGENTS.md" if manifest.get("native") else "HARNESS.md")).read_text()
            (evidence_dir / "manifest-snapshot.json").write_text(json.dumps(scrub(manifest), ensure_ascii=False))
            mcp = {"mcpServers": {"kt66": {"command": "/usr/bin/python3", "args": [
                str(pathlib.Path(__file__).with_name("harness_tools.py")),
                str(harness / "manifest.json"), str(evidence_dir)]}}}
            mcp_file = pathlib.Path(td) / "mcp.json"
            mcp_file.write_text(json.dumps(mcp))
            (evidence_dir / "loaded-harness.json").write_text(json.dumps({
                "version": manifest["version"], "manifest": str(harness / "manifest.json"),
                "cwd": session_cwd, "instructions_sha256": __import__("hashlib").sha256(instructions.encode()).hexdigest(),
                "instruction_delivery": "generated instructions via CLI system/developer config",
                "native": manifest.get("native"), "mcp_server": "kt66"}))

        if runtime == "claude":
            sid = str(uuid.uuid4())
            cmd = [cli, "-p", "--model", model, "--session-id", sid,
                   "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                   "--settings", '{"disableAllHooks":true}',
                   "--no-session-persistence", "--output-format", "json"]
            if harness is not None:
                cmd += ["--mcp-config", str(mcp_file), "--allowedTools", "mcp__kt66__*"]
                if not manifest.get("native"):
                    cmd += ["--append-system-prompt", instructions]
            if harness is not None and manifest.get("native"):
                name = manifest["native"]["agent"]
                _, header, body = (harness / ".claude" / "agents" / (name + ".md")).read_text().split('---', 2)
                role = yaml.safe_load(header)
                cmd += ["--agents", json.dumps({name: {"description": role["description"], "prompt": body,
                         "tools": role["tools"], "model": role["model"]}}, ensure_ascii=False), "--agent", name]
            if schema is not None:
                cmd += ["--json-schema", json.dumps(schema)]
            p = subprocess.run(cmd, input=prompt, cwd=session_cwd, env=env,
                               capture_output=True, text=True, timeout=timeout)
            if p.returncode:
                raise session_failure(p.stdout + p.stderr, "session_exit_failed")
            try:
                data = json.loads(p.stdout)
            except ValueError:
                raise SessionError("invalid_session_output")
            if not isinstance(data, dict) or data.get("is_error"):
                raise session_failure(p.stdout, "session_error")
            if data.get("session_id") != sid:
                raise SessionError("session_identity_missing")
            result = {"body": (json.dumps(data.get("structured_output"), ensure_ascii=False)
                               if schema is not None and isinstance(data.get("structured_output"), dict)
                               else data.get("result")), "session_id": sid,
                      "usage": data.get("usage", {}),
                      "model_usage": data.get("modelUsage", {})}
        else:
            final = pathlib.Path(td) / "result.txt"
            cmd = [cli, "-a", "never", "exec", "--ignore-user-config",
                   "--ignore-rules", "--skip-git-repo-check", "--ephemeral",
                   "--sandbox", "read-only", "--json",
                   "-c", 'forced_login_method="chatgpt"',
                   "-c", 'model_provider="openai"',
                   "-c", 'web_search="disabled"',
                   "--disable", "shell_tool", "--disable", "shell_snapshot",
                   "--disable", "hooks", "--disable", "multi_agent",
                   "-o", str(final)]
            if harness is not None:
                cmd += ["-c", "developer_instructions=" + json.dumps(instructions),
                        "-c", 'mcp_servers.kt66.command="/usr/bin/python3"',
                        "-c", "mcp_servers.kt66.args=" + json.dumps(mcp["mcpServers"]["kt66"]["args"]),
                        "-c", 'mcp_servers.kt66.default_tools_approval_mode="approve"',
                        "-c", "mcp_servers.kt66.startup_timeout_sec=15",
                        "-c", "mcp_servers.kt66.tool_timeout_sec=90"]
            if schema is not None:
                schema_file = pathlib.Path(td) / "output-schema.json"
                schema_file.write_text(json.dumps(schema))
                cmd += ["--output-schema", str(schema_file)]
            if model and model != "default":
                cmd += ["--model", model]
            cmd += ["-"]
            p = subprocess.run(cmd, input=prompt, cwd=session_cwd, env=env,
                               capture_output=True, text=True, timeout=timeout)
            if p.returncode:
                raise session_failure(p.stdout + p.stderr, "session_exit_failed")
            try:
                events = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
            except ValueError:
                raise SessionError("invalid_session_output")
            if not all(isinstance(e, dict) for e in events):
                raise SessionError("invalid_session_output")
            if any(e.get("type") in ("turn.failed", "error") for e in events):
                raise session_failure(p.stdout, "session_error")
            sid = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
            complete = next((e for e in events if e.get("type") == "turn.completed"), None)
            if not sid or complete is None or not final.is_file():
                raise SessionError("session_identity_or_completion_missing")
            result = {"body": final.read_text(), "session_id": sid,
                      "usage": complete.get("usage", {})}
    if not isinstance(result["body"], str) or not result["body"].strip():
        raise SessionError("empty_response")
    result["body"] = result["body"].strip()
    result.update(runtime=runtime, model=model, auth=auth, fresh_session=True)
    if harness is not None:
        result.update(harness_version=manifest["version"], evidence_dir=str(evidence_dir))
    return result
