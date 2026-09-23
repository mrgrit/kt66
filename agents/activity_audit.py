"""Small, provider-neutral audit records. Deliberately excludes hidden model reasoning."""
import datetime
import json
import os
import pathlib
import re
import uuid

VERSION = "kt66.activity.v1"
SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)|^authorization$")


def scrub(value, secrets=(), depth=0):
    """Redact credentials at the display/export boundary as well as at capture."""
    if depth > 16:
        return "[depth limit]"
    if isinstance(value, dict):
        def policy_metadata(k,v):
            if str(k).lower() != 'authorization' or not isinstance(v, dict):
                return False
            if set(v) <= {'permission','mode','autonomy','source','role','boundary','user_decisions'} and v.get('mode') in ('allow','ask','deny','unknown','audit_only'):
                return True
            return bool(v.get('role') and v.get('fingerprint') and set(v) <= {
                'role','fingerprint','template','label','duty','temporary','tools','capabilities',
                'env_assets','env_sections','inventory_assets','disk_targets','web_research'})
        return {str(k): "[REDACTED]" if SECRET_KEY.search(str(k)) and not policy_metadata(k,v) else scrub(v, secrets, depth+1)
                for k, v in list(value.items())[:500]}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets, depth+1) for v in value[:500]]
    if isinstance(value, str):
        for secret in secrets:
            if secret and len(secret) >= 8:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(r'''(?i)((?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)["']?\s*[=:]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;&]+)''', r"\1[REDACTED]", value)
        value = re.sub(r"\b(?:sk-|sk-ant-)[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
        value = re.sub(r"\bghp_[A-Za-z0-9]{30,}\b", "[REDACTED]", value)
        return value[:48000] + ("\n[truncated]" if len(value) > 48000 else "")
    return value


def record(directory, event_type, data):
    if directory is None:
        return None
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    row = {"schema_version": VERSION, "id": uuid.uuid4().hex,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "type": event_type, "data": scrub(data, [v for k, v in os.environ.items() if SECRET_KEY.search(k)])}
    with (directory / "activity.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
