"""Policy-enforced MCP stdio tools. No shell or arbitrary URL tool is exposed."""
import datetime, hashlib, json, os, pathlib, sys, time, urllib.parse, urllib.request, uuid
ROOT = pathlib.Path(__file__).resolve().parent

def schema(properties, required=()):
    return {"type":"object","properties":properties,"required":list(required),"additionalProperties":False}
S={"type":"string"}
TOOLS=[
 ("work_status","Read current work queue, recent findings and pending approvals to avoid duplicate work and track follow-up.",schema({}),"cmdb_read"),
 ("infrastructure_read","Read actual Docker service state and resource usage using fixed read-only commands.",schema({}),"metrics_read"),
 ("firewall_read","Read the actual laboratory firewall ruleset and counters. No changes are made.",schema({}),"metrics_read"),
 ("cycle_state","Read or save this worker's declared loop state for the next cycle. Update only state keys declared in the loaded loops.",schema({"values":{"type":"object"}}),"ticket_update"),
 ("delegate_work","Assign observed evidence to another configured worker based on organizational responsibilities. This does not grant new permissions.",schema({"worker":S,"reason":S,"evidence":S},["worker","reason","evidence"]),"delegate_work"),
 ("harness_identity","Read the version and effective organizational policy loaded for this session.",schema({}),None),
 ("env_read","Read live virtual facility state, alarms, faults, assets and recent events.",schema({}),"env_read"),
 ("log_read","Read bounded actual Wazuh alert records; returns an immutable evidence snapshot and hash.",schema({"limit":{"type":"integer","minimum":1,"maximum":100}}),"log_read"),
 ("ticket_create","Preserve findings, evidence and requested follow-up as an auditable local ticket.",schema({"title":S,"body":S},["title","body"]),"ticket_create"),
 ("simulator_control","Clear one currently active virtual facility fault. This changes only the simulator. Choose fault/target from live observations; provide rationale, evidence and rollback. Subject to role and approval.",schema({"fault":S,"target":S,"reason":S,"evidence":{"type":"array","items":S},"rollback":S},["fault","target","reason","evidence","rollback"]),"simulation_control"),
 ("approval_inbox","Read action requests awaiting this worker's decision.",schema({}),"approve_request"),
 ("approve_request","Approve or reject a specific pending request after independently reviewing evidence and organizational policy.",schema({"request_id":S,"approve":{"type":"boolean"},"reason":S},["request_id","approve","reason"]),"approve_request"),
]
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name("."+path.name+"-"+uuid.uuid4().hex)
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)); os.replace(temp,path)

class Broker:
    def __init__(self, manifest_path, session_dir):
        self.path=pathlib.Path(manifest_path); self.m=json.loads(self.path.read_text())
        self.session=pathlib.Path(session_dir); self.session.mkdir(parents=True,exist_ok=True)
        self.worker=self.m["worker"]["id"]
        self.policy=self.m["policy"]; self.permissions=self.policy["constrain"].get("permission",{})
        self.autonomy=self.policy["constrain"].get("autonomy","L1")
        self.active_loops=self.m["loops"]
        job=self.session/"job.json"
        if job.exists():
            context=json.loads(job.read_text());kind=context.get("kind","")
            if kind.startswith("periodic:"):
                self.active_loops=[lp for lp in self.m["loops"] if lp["id"]==kind.split(":",1)[1]]
            elif kind=="event":
                eid=context.get("observation",{}).get("event",{}).get("id")
                self.active_loops=[lp for lp in self.m["loops"] if eid in lp.get("triggers",{}).get("alarms",[])]
            levels={"L1":1,"L2":2,"L3":3}
            if self.autonomy in levels:
                effective=min([levels[self.autonomy]]+[levels[lp["autonomy"]] for lp in self.active_loops if lp.get("autonomy") in levels])
                self.autonomy="L"+str(effective)
        self.policy["constrain"]["autonomy"]=self.autonomy
        self.url="http://127.0.0.1:8010"
        envfile=ROOT.parent/".env"
        self.key=""
        for line in envfile.read_text().splitlines():
            k,_,v=line.partition("="); v=v.strip().strip("\"'")
            if k=="INT_HOST_IP":self.url="http://"+v+":8010"
            if k=="API_KEY":self.key=v
    def current(self):
        # Revoke stale tools immediately when source policy changes.
        for name,expected in self.m["source_hashes"].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
                raise ValueError("configuration changed; a fresh harness/session is required")
    def get(self,path):
        with urllib.request.urlopen(self.url+path,timeout=8) as r:return json.load(r)
    def receipt(self,name,args,result):
        if isinstance(result,dict): result.setdefault("observed_at",datetime.datetime.now(datetime.timezone.utc).isoformat())
        row={"at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"worker":self.worker,
             "harness_version":self.m["version"],"tool":name,"arguments":args,"result":result}
        with (self.session/"tools.jsonl").open("a") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
        return result
    def call(self,name,args):
        self.current()
        calls=self.session/"tools.jsonl"
        budgets=[lp.get("budget",{}).get("max_tool_calls",30) for lp in getattr(self,"active_loops",self.m["loops"])]
        limit=min(budgets) if budgets else 30
        if calls.exists() and len(calls.read_text().splitlines())>=limit:
            raise ValueError("configured tool-call budget exhausted; escalate")
        definition=next((t for t in TOOLS if t[0]==name),None)
        if definition is None:raise ValueError("unknown tool")
        specification=definition[2]
        if not isinstance(args,dict) or set(args)-set(specification["properties"]):raise ValueError("invalid tool arguments")
        if any(k not in args for k in specification["required"]):raise ValueError("required arguments missing")
        types={"string":str,"object":dict,"array":list,"boolean":bool,"integer":int}
        for key,value in args.items():
            expected=specification["properties"][key].get("type")
            if expected and (not isinstance(value,types[expected]) or expected=="integer" and isinstance(value,bool)):raise ValueError("argument type mismatch")
        if len(json.dumps(args))>50000:raise ValueError("tool argument budget exceeded")
        permitted=definition[3]
        if permitted and self.permissions.get(permitted,"deny")=="deny":
            return self.receipt(name,args,{"status":"denied","permission":permitted})
        if permitted and self.permissions.get(permitted)=="ask" and name!="simulator_control":
            return self.receipt(name,args,{"status":"approval_required","permission":permitted})
        if name=="work_status":
            import sqlite3
            path=ROOT/"tickets"/"loop-engine.sqlite3"
            jobs=[]
            if path.exists():
                db=sqlite3.connect("file:"+str(path)+"?mode=ro",uri=True);db.row_factory=sqlite3.Row
                jobs=[dict(r) for r in db.execute("SELECT id,worker,kind,status,updated,evidence FROM jobs ORDER BY updated DESC LIMIT 40")];db.close()
            files=sorted((ROOT/"evidence").glob("*/finding-*.md"),key=lambda p:p.stat().st_mtime,reverse=True)[:20]
            return self.receipt(name,args,{"jobs":jobs,"recent_findings":[{"path":str(p),"text":p.read_text()[:1000]} for p in files],
                "pending_approvals":[{"id":r["id"],"worker":r["worker"],"approver":r["approver"],"status":r["status"]} for r in
                    [json.loads(p.read_text()) for p in (ROOT/"tickets"/"approvals").glob("*.json")] if r["status"]=="pending"]})
        if name in ("infrastructure_read","firewall_read"):
            import subprocess
            commands=([["docker","ps","--format","{{json .}}"],["docker","stats","--no-stream","--format","{{json .}}"]]
                      if name=="infrastructure_read" else [["docker","exec","kt66-fw","nft","-j","list","ruleset"]])
            records=[]
            for command in commands:
                process=subprocess.run(command,capture_output=True,text=True,timeout=15)
                dest=self.session/("infrastructure-"+uuid.uuid4().hex+".txt");dest.write_text(process.stdout)
                records.append({"source_command":command,"returncode":process.returncode,"snapshot":str(dest),
                                "sha256":hashlib.sha256(process.stdout.encode()).hexdigest(),
                                "output":process.stdout[:24000],"truncated":len(process.stdout)>24000,
                                "error":process.stderr[:400] if process.returncode else None})
            return self.receipt(name,args,{"records":records})
        if name=="cycle_state":
            path=ROOT/"tickets"/"cycle-state"/(self.worker+".json")
            current=json.loads(path.read_text()) if path.exists() else {}
            if "values" in args:
                allowed={key for lp in self.m["loops"] for key in lp.get("state",[]) if isinstance(key,str)}
                if set(args["values"])-allowed:raise ValueError("state key not declared in loaded loops")
                current.update(args["values"]);atomic(path,current)
            return self.receipt(name,args,{"state":current})
        if name=="delegate_work":
            import yaml
            known={w["id"] for w in yaml.safe_load((ROOT/"roster.yaml").read_text())["workers"]}
            if args["worker"] not in known or args["worker"]==self.worker:raise ValueError("invalid delegate")
            rid=uuid.uuid4().hex;atomic(ROOT/"tickets"/"delegations"/(rid+".json"),{**args,"from":self.worker,"version":self.m["version"],"created":time.time()})
            return self.receipt(name,args,{"status":"queued","delegation_id":rid})
        if name=="harness_identity":
            return self.receipt(name,args,{"version":self.m["version"],"worker":self.worker,
                  "company":self.m["company"],"team":self.m["team"],"policy":self.policy})
        if name=="env_read":
            state=self.get("/state")
            result={k:v for k,v in state.items() if k not in ("building","floors","aisles","assets")}
            result["floors"]={f:{k:v for k,v in data.items() if k in ("temp_c","humidity_pct","it_kw","cooling_kw")} for f,data in state.get("floors",{}).items()}
            result["assets"]=[a for a in state.get("assets",[]) if any(str(a.get("id","")).startswith(x) for x in self.m["worker"].get("assets",[]))] if isinstance(state.get("assets"),list) else state.get("assets",{})
            result["events"]=self.get("/events?limit=12")
            result["interpretation_limits"]="Instantaneous readings and bounded logs cannot prove historical uptime or absence of all unauthorized access. Instructor inject/clear records describe deliberate simulations."
            return self.receipt(name,args,result)
        if name=="log_read":
            import subprocess
            limit=min(100,max(1,int(args.get("limit",30))))
            p=subprocess.run(["docker","exec","kt66-siem","tail","-n",str(limit),"/var/ossec/logs/alerts/alerts.json"],capture_output=True,text=True,timeout=12)
            if p.returncode:raise ValueError("actual SIEM log read failed")
            dest=self.session/("siem-"+uuid.uuid4().hex+".jsonl");dest.write_text(p.stdout)
            return self.receipt(name,args,{"source":"kt66-siem:/var/ossec/logs/alerts/alerts.json","snapshot":str(dest),"sha256":hashlib.sha256(p.stdout.encode()).hexdigest(),"records":[{k:v for k,v in json.loads(x).items() if k in ("timestamp","rule","full_log","location","agent","decoder")} for x in p.stdout.splitlines()]})
        if name=="ticket_create":
            text="# "+args["title"]+"\n\n"+args["body"]+"\n"
            if len(text)>40000:raise ValueError("ticket too large")
            dest=self.session/("finding-"+uuid.uuid4().hex+".md");dest.write_text(text)
            return self.receipt(name,args,{"status":"written","path":str(dest),"sha256":hashlib.sha256(dest.read_bytes()).hexdigest()})
        if name=="simulator_control":
            if self.autonomy not in ("L2","L3"):
                return self.receipt(name,args,{"status":"denied","reason":"role cannot change facility state"})
            if not args.get("reason") or not args.get("rollback") or len(args.get("evidence",[]))<2:
                raise ValueError("rationale, rollback and at least two evidence references required")
            target=args["target"]
            assets=self.m["worker"].get("assets",[])
            if not any(target==a or target.startswith(a+"-") for a in assets):
                return self.receipt(name,args,{"status":"denied","reason":"target outside assigned assets"})
            if self.permissions.get("simulation_control")=="allow" and self.autonomy=="L3":
                return self.receipt(name,args,self.clear_fault(args))
            req={"id":uuid.uuid4().hex,"worker":self.worker,"version":self.m["version"],
                 "manifest":str(self.path),"session_dir":str(self.session),"action":"simulator_control",
                 "arguments":args,"status":"pending","created":time.time(),
                 "approver":self.policy.get("escalate",{}).get("to","human")}
            atomic(ROOT/"tickets"/"approvals"/(req["id"]+".json"),req)
            return self.receipt(name,args,{"status":"approval_pending","request_id":req["id"],"approver":req["approver"]})
        if name=="approval_inbox":
            rows=[json.loads(p.read_text()) for p in (ROOT/"tickets"/"approvals").glob("*.json")]
            return self.receipt(name,args,{"requests":[r for r in rows if r["status"]=="pending" and r["approver"]==self.worker]})
        if name=="approve_request":
            if self.autonomy!="approver":raise ValueError("approver role required")
            rid=args["request_id"]
            if len(rid)!=32 or any(c not in "0123456789abcdef" for c in rid):raise ValueError("invalid request")
            path=ROOT/"tickets"/"approvals"/(rid+".json")
            import fcntl
            with path.with_suffix(".lock").open("a") as request_lock:
                fcntl.flock(request_lock,fcntl.LOCK_EX)
                req=json.loads(path.read_text())
                if req["status"]!="pending" or req["approver"]!=self.worker or req["worker"]==self.worker:
                    raise ValueError("request not assigned to this independent approver")
                if time.time()-req["created"]>600:raise ValueError("approval request expired")
                req["decision"]={"approve":args["approve"],"reason":args["reason"],"worker":self.worker,"version":self.m["version"]}
                req["status"]="approved" if args["approve"] else "denied"
                atomic(path,req)
                if args["approve"]:
                    try:
                        actor=Broker(req["manifest"],req["session_dir"]);actor.current()
                        if actor.autonomy not in ("L2","L3") or actor.permissions.get("simulation_control","deny")=="deny":
                            raise ValueError("requester no longer authorized")
                        req["execution"]=actor.clear_fault(req["arguments"])
                        req["execution"].update(executor_worker=actor.worker, authorization_request=rid,
                                                executor_harness_version=actor.m["version"])
                        actor.receipt("approved_action",req["arguments"],req["execution"])
                        req["status"]="verified" if req["execution"].get("verified") else "verification_failed"
                    except Exception as e:
                        req["status"]="execution_failed";req["error"]=str(e)
                    atomic(path,req)
                return self.receipt(name,args,req)

    def clear_fault(self,args):
        if not any(args["target"]==a or args["target"].startswith(a+"-") for a in self.m["worker"].get("assets",[])):
            raise ValueError("execution target outside assigned assets")
        before=self.get("/state")
        faults=before.get("faults",[])
        pair={"fault":args["fault"],"target":args["target"]}
        # State faults may be mapping-shaped; use /faults as independent source.
        current=self.get("/faults")
        rows=[{"fault": f, "target": t} for f, targets in current.get("active", {}).items() for t in targets]
        if not any(r.get("fault")==pair["fault"] and r.get("target")==pair["target"] for r in rows):
            raise ValueError("requested fault/target is not currently active")
        query=urllib.parse.urlencode({**pair,"clear":"true","key":self.key})
        prior_alarms=self.get("/alarms").get("active",[])
        prior_ids={(a["id"],a.get("scope")) for a in prior_alarms}
        request=urllib.request.Request(self.url+"/inject?"+query,data=b"",method="POST")
        with urllib.request.urlopen(request,timeout=8) as r:control=json.load(r)
        deadline=time.monotonic()+8
        while True:
            remaining=self.get("/alarms").get("active",[])
            if (not prior_ids or prior_ids-{(a["id"],a.get("scope")) for a in remaining}) or time.monotonic()>=deadline:
                break
            time.sleep(1)
        after=self.get("/faults"); rows=[{"fault": f, "target": t} for f, targets in after.get("active", {}).items() for t in targets]
        verified=not any(r.get("fault")==pair["fault"] and r.get("target")==pair["target"] for r in rows)
        return {"action":"simulator_control","pair":pair,"before":current,"control_receipt":control,
                "after":after,"alarms_before":prior_alarms,"alarms_after":remaining,"fault_cleared":verified,
                "resolved_alarm_scopes":[list(x) for x in prior_ids-{(a["id"],a.get("scope")) for a in remaining}],
                "verified":verified and (not prior_ids or bool(prior_ids-{(a["id"],a.get("scope")) for a in remaining}))}

def main():
    broker=Broker(sys.argv[1],sys.argv[2])
    for line in sys.stdin:
        try:
            request=json.loads(line); method=request.get("method"); rid=request.get("id")
            if rid is None:continue
            if method=="initialize":result={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"kt66-harness","version":"1.0"}}
            elif method=="ping":result={}
            elif method=="tools/list":result={"tools":[{"name":n,"description":d,"inputSchema":s} for n,d,s,p in TOOLS if p is None or broker.permissions.get(p,"deny")!="deny"]}
            elif method=="tools/call":
                try:
                    out=broker.call(request["params"]["name"],request["params"].get("arguments",{}))
                    result={"content":[{"type":"text","text":json.dumps(out,ensure_ascii=False)}]}
                except Exception as e:
                    broker.receipt("error:"+request["params"].get("name","unknown"),request["params"].get("arguments",{}),{"status":"failed","error":str(e)})
                    result={"content":[{"type":"text","text":str(e)}],"isError":True}
            else:
                print(json.dumps({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":"method not supported"}}),flush=True);continue
            print(json.dumps({"jsonrpc":"2.0","id":rid,"result":result},ensure_ascii=False),flush=True)
        except Exception:
            print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":"protocol error"}}),flush=True)
if __name__=="__main__":main()
