"""Deterministic, durable observation gates. This module never calls a model."""
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import time

ACTIVE = ('queued', 'retry', 'waiting_capacity', 'running')
DEFAULT_INTERVALS = (600, 300, 120, 60)


def settings(cfg):
    value = cfg.get('adaptive', {})
    intervals = value.get('intervals_sec', DEFAULT_INTERVALS)
    if (not isinstance(intervals, (list, tuple)) or not intervals
            or any(type(n) is not int or n < 60 for n in intervals)
            or list(intervals) != sorted(set(intervals), reverse=True)):
        raise ValueError('adaptive intervals must be unique descending seconds >= 60')
    return {**value, 'intervals_sec': list(intervals),
            'stable_samples': max(1, int(value.get('stable_samples', 2))),
            'model_cooldown_sec': max(60, int(value.get('model_cooldown_sec', 600))),
            'incident_review_sec': max(600, int(value.get('incident_review_sec', 1800)))}


def enabled(loop, cfg):
    return cfg.get('adaptive', {}).get('enabled', False) and loop.get('monitor', {}).get('mode') == 'adaptive'


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS monitor_state(loop TEXT PRIMARY KEY, worker TEXT, data TEXT)')
    db.commit()


def load(db, lid):
    row = db.execute('SELECT data FROM monitor_state WHERE loop=?', (lid,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(db, loop, data):
    db.execute('INSERT OR REPLACE INTO monitor_state VALUES(?,?,?)',
               (loop['id'], loop['owner'], json.dumps(data, ensure_ascii=False)))
    db.commit()


def changed(before, after):
    """Ignore clock/counter noise; compare numeric drift against a durable baseline."""
    if before is None:
        return []
    out = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if type(a) in (int, float) and type(b) in (int, float):
            tolerance = 2 if key.startswith(('temp:', 'coldplate:')) else 10 if key.startswith('humidity:') else 5 if key.startswith('disk:') else 1
            different = abs(a-b) >= tolerance
        else:
            different = a != b
        if different:
            out.append(key)
    return out


def advance(previous, observation, now, cfg):
    """Pure interval and invocation decision; pending changes survive busy workers."""
    opts = settings(cfg)
    intervals = opts['intervals_sec']
    old = dict(previous)
    signals = json.loads(json.dumps(observation['signals']))
    issues = sorted(set(observation.get('problems', [])))
    delta = changed(old.get('baseline'), signals)
    issue_change = issues != old.get('problems', [])
    meaningful = bool(delta or issue_change)
    first = 'baseline' not in old
    level = min(old.get('level', 0), len(intervals)-1)
    stable = old.get('stable_samples', 0)
    if issues or meaningful:
        level = min(level+1, len(intervals)-1)
        stable = 0
    else:
        stable += 1
        if stable >= opts['stable_samples']:
            level = max(0, level-1)
            stable = 0
    baseline = signals if first or meaningful else old['baseline']
    fingerprint = hashlib.sha256(json.dumps({'signals':baseline,'problems':issues}, sort_keys=True).encode()).hexdigest()[:24]
    # A healthy first observation establishes a baseline without invoking AI.
    pending = old.get('pending', False) or meaningful or bool(first and issues)
    review_due = bool(issues and now-old.get('last_ai_at', 0) >= opts['incident_review_sec'])
    should_call = (pending or review_due) and now-old.get('last_ai_at', 0) >= opts['model_cooldown_sec']
    reason = 'problem_detected' if first and issues else 'state_changed' if pending else 'incident_review' if review_due else 'baseline' if first else 'unchanged'
    return {**old, 'baseline':baseline, 'signals':signals, 'problems':issues,
            'fingerprint':fingerprint, 'pending':pending, 'changed_keys':(delta or old.get('changed_keys', [])) if pending else [],
            'level':level, 'stable_samples':stable, 'interval_sec':intervals[level],
            'checked_at':now, 'next_check_at':now+intervals[level],
            'checks':old.get('checks', 0)+1, 'skipped_model_calls':old.get('skipped_model_calls', 0),
            'reason':reason, 'decision':'invoke' if should_call else 'observe_only',
            'sources':observation.get('sources', [])}, should_call


class Probes:
    """Small read-only projections. Never fingerprint AI's own reports or usage."""
    def __init__(self, root, host, observe, now):
        self.root, self.host, self.observe, self.now = pathlib.Path(root), host, observe, now
        self.cache = {}

    def get(self, path):
        if path not in self.cache:
            self.cache[path] = self.observe('http://'+self.host+':8020'+path)
        return self.cache[path]

    def collect(self, loop):
        kind = loop['monitor']['probe']
        signals, problems, sources = {}, [], []
        try:
            state = self.get('/api/state')
            if not isinstance(state.get('alarms'), list) or not isinstance(state.get('containers'), dict):
                raise ValueError('invalid operational state')
            sources.append('noc:/api/state')
            floors = {'environment':['2F','3F','4F'], 'inference':['3F']}.get(kind, [])
            for floor in floors:
                values = state.get('floors', {}).get(floor, {})
                for key, prefix in [('temp_c','temp'), ('humidity_pct','humidity')]:
                    n = values.get(key)
                    if not isinstance(n, (int,float)):
                        raise ValueError('missing environmental sensor')
                    signals[prefix+':'+floor] = n
                if values['temp_c'] >= 27:
                    problems.append('temperature:'+floor)
            if kind == 'environment':
                power = state['power']
                for key in ('utility_ok','on_battery','generator_failed','generator_running'):
                    signals['power:'+key] = power[key]
                if not power['utility_ok'] or power['generator_failed']:
                    problems.append('power')
                signals['faults'] = state.get('faults', {})
            scopes = {'inference':['kt66-gpu-gw'], 'security':['kt66-siem','kt66-wazuh-indexer','kt66-wazuh-dashboard'],
                      'storage':['kt66-neobank','kt66-govportal','kt66-mediforum','kt66-adminconsole','kt66-aicompanion']}
            names = scopes.get(kind, [])
            if kind == 'tickets':
                # Exclude intentional one-shot/test containers and all queue/report timestamps.
                names = [n for n in state['containers'] if n.startswith('kt66-') and n != 'kt66-netglue']
                signals['service_path'] = state.get('netglue')
                if signals['service_path'] is not True:
                    problems.append('service_path_unavailable')
            for name in names:
                row = state['containers'].get(name, {})
                signals['service:'+name] = [row.get('state'), row.get('health')]
                if row.get('state') != 'running' or row.get('health') is False:
                    problems.append('service:'+name)
            if kind in ('environment','tickets','inference'):
                alarms = [a for a in state['alarms'] if kind != 'inference' or str(a.get('scope','')) in ('3F','B')]
                signals['alarms'] = sorted((str(a.get('id')),str(a.get('scope')),str(a.get('level'))) for a in alarms)
                problems.extend('alarm:'+str(a.get('id'))+':'+str(a.get('scope')) for a in alarms)
            if kind == 'storage':
                disk = shutil.disk_usage(self.root)
                percent = round(disk.used/disk.total*100, 1)
                signals['disk:host_workspace'] = percent
                sources.append('host filesystem:'+str(self.root))
                if percent >= 80:
                    problems.append('disk:host_workspace')
            if kind == 'inference':
                import yaml
                layout = yaml.safe_load((self.root.parent/'envsim/assets.yaml').read_text())
                remote = next(a for a in layout['it_assets'] if a.get('gpu') and a.get('remote'))
                try:
                    models = self.inference_models(remote['remote'])
                    if not isinstance(models.get('models'), list):
                        raise ValueError('invalid model inventory')
                    signals['inference:reachable'] = True
                    signals['inference:models'] = sorted(str(m.get('name', m.get('model',''))) for m in models['models'])
                except Exception:
                    signals['inference:reachable'] = False
                    problems.append('inference:unreachable')
                sources.append('ollama:/api/ps (read only; no inference request)')
            if kind == 'security':
                signals['siem:alerts'], truncated = self.security_signals()
                if signals['siem:alerts']:
                    problems.append('siem:relevant_alerts')
                if truncated:
                    problems.append('siem:observation_window_truncated')
                sources.append('kt66-siem:alerts.json (bounded recent rule/agent/source signatures)')
            if kind not in ('environment','inference','security','storage','tickets'):
                raise ValueError('unknown monitor probe')
        except Exception as exc:
            # Failed collection is evidence, never a clean empty observation.
            signals['collection_error'] = type(exc).__name__
            problems.append('observation_unavailable')
        return {'signals':signals, 'problems':problems, 'sources':sources}

    def inference_models(self, remote):
        # Use the existing observer's routed network path through IPS/gpu-gw.
        # The host itself has no direct route to the remote WireGuard peer.
        import ipaddress
        address = str(ipaddress.ip_address(remote))
        p = subprocess.run(['docker','exec','kt66-envsim','python','-c',
            'import sys,urllib.request; print(urllib.request.urlopen(sys.argv[1],timeout=5).read().decode())',
            'http://'+address+':11434/api/ps'], capture_output=True, text=True, timeout=8)
        if p.returncode:
            raise OSError('inference inventory unavailable')
        return json.loads(p.stdout)

    def security_signals(self):
        p = subprocess.run(['docker','exec','kt66-siem','tail','-n','200','/var/ossec/logs/alerts/alerts.json'],
                           capture_output=True, text=True, timeout=10)
        if p.returncode:
            raise OSError('SIEM log unavailable')
        rows = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        signatures = set()
        cutoff = self.now-600
        timestamps = []
        for row in rows:
            text = re.sub(r'([+-]\d{2})(\d{2})$',r'\1:\2',row['timestamp'].replace('Z','+00:00'))
            stamp = datetime.datetime.fromisoformat(text).timestamp()
            timestamps.append(stamp)
            if stamp < cutoff or int(row.get('rule', {}).get('level', 0)) < 7:
                continue
            signatures.add((str(row['rule'].get('id')), str(row.get('agent', {}).get('id')),
                            str(row.get('data', {}).get('srcip', ''))))
        return sorted(signatures), len(rows) >= 200 and min(timestamps) > cutoff


def poll(db, loops, cfg, probes, enqueue, now=None, notified_workers=()):
    now = time.time() if now is None else now
    install(db)
    for loop in loops:
        if not enabled(loop, cfg):
            continue
        lid, worker = loop['id'], loop['owner']
        old = load(db, lid)
        if not old:
            # Preserve obsolete scheduled jobs as history, without replaying the old 5-minute queue.
            db.execute("UPDATE jobs SET status='superseded',updated=?,result=? WHERE kind=? AND status IN ('queued','retry','waiting_capacity')",
                       (now,json.dumps({'reason':'adaptive_monitor_enabled'}),'periodic:'+lid))
            db.commit()
        notified = worker in notified_workers
        if not notified and now < old.get('next_check_at', 0):
            continue
        observation = probes.collect(loop)
        data, invoke = advance(old, observation, now, cfg)
        busy = db.execute("SELECT 1 FROM jobs WHERE worker=? AND status IN ('queued','retry','waiting_capacity','running')", (worker,)).fetchone()
        if notified:
            # The newly enqueued event already wakes this owner; do not duplicate it.
            data.update(last_ai_at=now, pending=False, decision='covered_by_event')
            data['skipped_model_calls'] += 1
        elif invoke and not busy:
            payload = {'loop':lid, 'monitor':{'reason':data['reason'], 'changed_keys':data['changed_keys'],
                        'problems':data['problems'], 'fingerprint':data['fingerprint'],
                        'checked_at':now, 'interval_sec':data['interval_sec'], 'sources':data['sources'],
                        'signals':data['signals']}}
            enqueue(db, 'adaptive:'+lid+':'+str(int(now*1000)), worker, 'periodic:'+lid, payload)
            data.update(last_ai_at=now, pending=False, model_jobs=old.get('model_jobs',0)+1, decision='enqueued')
        else:
            data['skipped_model_calls'] += 1
            data['decision'] = 'worker_busy' if invoke and busy else 'observe_only'
        save(db, loop, data)


def summary(db):
    return [{'loop':r[0], 'worker':r[1], **{k:v for k,v in json.loads(r[2]).items()
            if k not in ('baseline','signals','fingerprint')}} for r in db.execute('SELECT loop,worker,data FROM monitor_state ORDER BY loop')]
