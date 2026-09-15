"""Only an explicitly registered, low-rate synthetic syslog test can be stopped."""
import json
import pathlib
import re
import subprocess
import time
import urllib.parse
import urllib.request


def process_evidence(handle):
    if not re.fullmatch(r"[0-9a-f]{8}", handle):
        raise ValueError("invalid test handle")
    code = """
import pathlib,os,json
rows={}
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  raw=(p/'cmdline').read_bytes().replace(bytes([0]),b' ').decode(errors='replace')
  parent=int((p/'stat').read_text().rsplit(')',1)[1].split()[1])
  rows[int(p.name)]=(raw,parent)
 except (OSError,ValueError):pass
marker='kt66inj_'+HANDLE
def owned(pid):
 seen=set()
 while pid in rows and pid not in seen:
  seen.add(pid)
  if marker in rows[pid][0]:return True
  pid=rows[pid][1]
 return False
skip={os.getpid(),os.getppid()}
mine=[pid for pid in rows if pid not in skip and owned(pid)]
other=[pid for pid,(cmd,parent) in rows.items() if pid not in skip and 'ncat -' in cmd and not owned(pid)]
print(json.dumps({'owned_pids':mine,'unrelated_ncat_count':len(other)}))
""".replace("HANDLE", repr(handle))
    p = subprocess.run(["docker", "exec", "kt66-attacker", "python3", "-c", code],
                       capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise OSError("cannot independently inspect test producer")
    return json.loads(p.stdout)


def eligible(root, injection):
    p = root / "tickets" / ".test-actions.json"
    if not p.exists():
        return False
    item = json.loads(p.read_text()).get(injection["handle"], {})
    return (item.get("expires", 0) > time.time()
            and item.get("action") == "stop_test_syslog"
            and injection.get("id") == "sec_alertstorm"
            and injection.get("target") == "kt66-attacker"
            and injection.get("params", {}).get("rate") == 1
            and item.get("target") == injection["target"])


def stop(root, noc_url, injection, decision):
    if not eligible(root, injection):
        raise ValueError("test action not authorized")
    handle = injection["handle"]
    if decision.get("decision") != "approve" or decision.get("handle") != handle:
        raise ValueError("specific approval missing")
    before = process_evidence(handle)
    # Legacy injector cleanup also matches ncat: refuse if another producer exists.
    if not before["owned_pids"]:
        raise ValueError("test producer is not independently observed running")
    if before["unrelated_ncat_count"]:
        raise ValueError("legacy cleanup could affect another ncat process")
    key = next(line.split("=", 1)[1].strip().strip("\"'")
               for line in (root.parent / ".env").read_text().splitlines()
               if line.startswith("API_KEY="))
    q = urllib.parse.urlencode({"handle": handle, "key": key})
    req = urllib.request.Request(noc_url.rstrip("/") + "/api/inj/clear?" + q,
                                 data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        receipt = json.load(response)
    after = process_evidence(handle)
    with urllib.request.urlopen(noc_url.rstrip("/") + "/api/inj/active", timeout=5) as response:
        active = json.load(response)["active"]
    verified = receipt.get("cleared") == handle and not after["owned_pids"] and not any(
        i["handle"] == handle for i in active)
    return {"action": "stop_test_syslog", "handle": handle,
            "before": before, "after": after,
            "verified": verified, "control_receipt": receipt}
