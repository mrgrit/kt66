"""Read-only instructor injection and actual Wazuh evidence collection."""
import json
import subprocess
import urllib.request

OWNERS = {"security": "soc-analyst", "endpoint": "soc-analyst",
          "network": "network-engineer", "system": "systems-engineer",
          "storage": "systems-engineer", "load": "gpu-platform-engineer"}


def active(noc_url):
    with urllib.request.urlopen(noc_url.rstrip("/") + "/api/inj/active", timeout=5) as r:
        data = json.load(r)
    return data.get("active", [])


def to_alarms(injections):
    return [{"id": "INJ:" + i["handle"], "level": 5,
             "scope": i["target"], "msg": "Instructor injection: " + i["name"],
             "metric": "instructor_injection_active", "value": 1,
             "source": "instructor", "handle": i["handle"],
             "injection_id": i["id"], "domain": i["domain"],
             "owner": OWNERS.get(i["domain"], "service-desk"),
             "since": i["started"]}
            for i in injections]


def siem_records(handles=(), alarm_ids=()):
    # Fixed read-only command; model output never becomes a shell command.
    p = subprocess.run(["docker", "exec", "kt66-siem", "tail", "-n", "800",
                        "/var/ossec/logs/alerts/alerts.json"],
                       capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise OSError("SIEM alert log unavailable")
    out = []
    needles = list(handles) + list(alarm_ids)
    for line in p.stdout.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        raw = data.get("full_log", "")
        if any(n in raw for n in needles):
            out.append({"timestamp": data.get("timestamp"),
                        "rule_id": data.get("rule", {}).get("id"),
                        "rule_description": data.get("rule", {}).get("description"),
                        "location": data.get("location"), "full_log": raw})
    return out[-40:]
