"""고정된 KT66 네트워크 경로의 읽기 전용 수집·판정. 명령/대상을 입력받지 않는다."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

ASSETS = ('fw', 'ips', 'web', 'attacker')


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def structural(value):
    if isinstance(value, list):
        return [structural(v) for v in value]
    if isinstance(value, dict):
        return {k: structural({field: amount for field, amount in v.items() if field not in ('bytes', 'packets')}
                              if k == 'counter' and isinstance(v, dict) else v)
                for k, v in value.items() if k not in ('handle', 'metainfo', 'index')}
    return value


def assess(observations, baseline):
    checks = []
    def check(name, passed, observed, expected):
        checks.append(dict(id=name, status='unknown' if passed is None else 'pass' if passed else 'fail',
                           observed=observed, expected=expected))
    for asset in ASSETS:
        state = observations.get(asset, {}).get('state')
        check(asset + ':running', None if state is None else state == 'running', state, 'running')
    bridge = observations.get('host_bridge_filter')
    check('host:bridge-filter', None if bridge is None else bridge == '0', bridge, '0 (KT66 출처 보존 설정)')
    for asset, dest, gateway in [('fw', '10.20.32.0/24', '10.20.31.2'), ('ips', 'default', '10.20.31.1'),
                                  ('web', 'default', '10.20.32.1'), ('attacker', 'default', '10.20.30.1')]:
        rows = observations.get(asset, {}).get('routes')
        actual = [r.get('gateway') for r in rows if r.get('dst') == dest] if isinstance(rows, list) else None
        check(asset + ':route:' + dest, None if actual is None else gateway in actual, actual, gateway)
    nft = observations.get('fw', {}).get('nft')
    entries = nft.get('nftables', []) if isinstance(nft, dict) else None
    policies = baseline.get('chain_policies', {})
    if not policies:
        check('baseline:chain-policies', None, None, '저장소 체인 정책')
    for name, policy in policies.items():
        rows = [r['chain'] for r in entries or [] if 'chain' in r and r['chain'].get('table') == 'six_filter' and r['chain'].get('name') == name]
        actual = rows[0].get('policy') if rows else None
        check('fw:policy:' + name, None if entries is None else actual == policy, actual, policy)
    for port in (80, 443, *range(8001, 8008)):
        targets = []
        for row in entries or []:
            rule = row.get('rule', {})
            if rule.get('table') != 'six_nat' or rule.get('chain') != 'prerouting':
                continue
            expr = rule.get('expr', [])
            matching = any(e.get('match', {}).get('left') == {'payload': {'protocol': 'tcp', 'field': 'dport'}}
                           and e['match'].get('right') == port and e['match'].get('op') == '==' for e in expr)
            scoped = any(e.get('match', {}).get('left') == {'payload': {'protocol': 'ip', 'field': 'daddr'}}
                         and e['match'].get('right') == '10.20.30.1' and e['match'].get('op') == '==' for e in expr)
            if matching and scoped:
                targets += [e['dnat'] for e in expr if 'dnat' in e]
        expected = {'addr': baseline.get('web_ip', '10.20.32.80'), 'port': port, 'match_destination': '10.20.30.1'}
        ok = any(t.get('addr') == expected['addr'] and t.get('port') == port for t in targets)
        check('fw:dnat:' + str(port), None if entries is None else ok, targets, expected)
    http = observations.get('http', {})
    code = http.get('code')
    # 쿼리 자체가 실행됐고 시간 초과면 재현된 증상, 수집기 실행 불가는 미측정이다.
    check('path:neobank-http', None if not http.get('attempted') else code == 200, http, {'code': 200})
    failed = [c['id'] for c in checks if c['status'] == 'fail']
    unknown = [c['id'] for c in checks if c['status'] == 'unknown']
    return {'decision': 'fault_observed' if failed else 'insufficient_evidence' if unknown else 'no_fault',
            'checks': checks, 'failed': failed, 'unknown': unknown,
            'scope': '현재 FW→IPS→WAF→NeoBank, 핵심 라우트·실행 상태·저장소 기본 체인 정책·웹 DNAT',
            'limitations': ['신고 시점 과거 상태는 별도 증거 필요', '물리 광모듈·BGP/OSPF·무선은 KT66에 없음',
                           '전체 룰 의미 동등성·성능·모든 차단 정책은 별도 검증 필요', '실패한 검사는 증상이며 단독으로 근본원인을 확정하지 않음']}


def collect(root, directory):
    root, directory = Path(root), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw, observations, errors = {}, {}, []
    try:
        observations['host_bridge_filter'] = Path('/proc/sys/net/bridge/bridge-nf-call-iptables').read_text().strip()
    except OSError:
        observations['host_bridge_filter'] = None
    def command(label, argv, parse=True):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            raw[label] = {'command': argv, 'returncode': result.returncode, 'output': result.stdout[:150000]}
            if result.returncode or len(result.stdout) > 150000:
                errors.append(label + ': query_failed_or_truncated'); return None
            return json.loads(result.stdout) if parse else result.stdout
        except (subprocess.SubprocessError, OSError, ValueError):
            errors.append(label + ': unavailable'); return None
    for asset in ASSETS:
        status = command(asset + ':state', ['docker', 'inspect', '--format', '{{json .State.Status}}', 'kt66-' + asset])
        routes = command(asset + ':routes', ['docker', 'exec', 'kt66-' + asset, 'ip', '-j', 'route', 'show'])
        links = command(asset + ':links', ['docker', 'exec', 'kt66-' + asset, 'ip', '-j', '-s', 'link', 'show'])
        observations[asset] = {'state': status, 'routes': routes, 'links': links}
    for asset in ('fw', 'ips'):
        observations[asset]['nft'] = command(asset + ':nft', ['docker', 'exec', 'kt66-' + asset, 'nft', '-j', 'list', 'ruleset'])
    response = command('path:http', ['docker', 'exec', 'kt66-attacker', 'curl', '--noproxy', '*', '-sS',
                       '--max-time', '6', '-o', '/dev/null', '-w', '%{http_code}', 'http://10.20.30.1:8003/'], False)
    http = raw.get('path:http', {})
    observations['http'] = {'code': int(response) if response and response.isdigit() else None,
                            'attempted': http.get('returncode') in (0, 7, 28, 52, 56), 'exit_code': http.get('returncode')}
    config = (root.parent / 'fw/nftables.conf').read_text()
    policies = dict(re.findall(r'chain\s+(\w+)\s*\{[^}]*?policy\s+(\w+)', config, flags=re.S))
    policies = {k: v for k, v in policies.items() if k in ('input', 'forward', 'output')}
    baseline = {'chain_policies': policies, 'web_ip': '10.20.32.80', 'files': [
        {'path': p, 'sha256': sha((root.parent / p).read_text())} for p in ('fw/nftables.conf', 'fw/entrypoint.sh', 'ips/entrypoint.sh')]}
    result = assess(observations, baseline)
    result.update(observed_at=time.time(), baseline=baseline, errors=errors,
                  status='partial' if errors else 'observed',
                  policy_hashes={a: sha(json.dumps(structural(observations[a].get('nft')), sort_keys=True))
                                 for a in ('fw', 'ips') if observations[a].get('nft')})
    text = json.dumps({'observations': observations, 'baseline': baseline, 'commands': raw}, ensure_ascii=False)
    dest = directory / ('network-' + uuid.uuid4().hex + '.json')
    dest.write_text(text)
    result.update(snapshot=str(dest), sha256=sha(text))
    return result
