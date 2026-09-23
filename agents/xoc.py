"""xOC: 결정론 탐지·증거 연결·기한부 실행 보류. 모델의 주장은 조치 권한이 아니다."""
from contextlib import contextmanager
from pathlib import Path
import collections
import fcntl
import hashlib
import json
import re
import time
import uuid

import yaml
from activity_audit import scrub

SLUG = re.compile(r"[a-zA-Z0-9_-]{1,180}\Z")
INJECTION = re.compile(r"ignore (?:all |previous |prior )?instructions|\[\[SYSTEM:|이전\s*지시.*무시|시스템\s*프롬프트.*공개", re.I)
SECRET = re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,})\b")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path, default=None, limit=4_000_000):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def lines(path):
    path = Path(path)
    if path.is_symlink() or not path.exists() or path.stat().st_size > 8_000_000:
        return [], False
    rows = []
    try:
        raw = path.read_text().splitlines()
        for line in raw[:500]:
            item = json.loads(line)
            if not isinstance(item, dict):
                return rows, False
            rows.append(item)
        return rows, len(raw) <= 500
    except (OSError, ValueError):
        return rows, False


@contextmanager
def state_lock(root):
    folder = Path(root) / 'tickets/xoc'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = stored_state(root)
        yield state
        tmp = folder / ('.state-' + uuid.uuid4().hex)
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.replace(folder / 'state.json')


def stored_state(root):
    path = Path(root) / 'tickets/xoc/state.json'
    if not path.exists():
        return {'findings': {}, 'holds': {}, 'seen': {}, 'history': []}
    state = read(path, limit=50_000_000)
    if not isinstance(state, dict) or not all(k in state for k in ('findings', 'holds', 'seen', 'history')):
        raise ValueError('xOC 상태를 읽을 수 없습니다. 기존 증적·보류를 초기화하지 않았습니다')
    return state


def config(root):
    data = yaml.safe_load((Path(root) / 'xoc/rules.yaml').read_text())
    if not isinstance(data, dict) or not isinstance(data.get('rules'), dict):
        raise ValueError('xOC 규칙 파일 형식을 확인하세요')
    for name, maximum in [('lookback_hours', 720), ('max_runs_per_scan', 1000)]:
        if type(data.get(name)) is not int or not 1 <= data[name] <= maximum:
            raise ValueError('xOC ' + name + '의 허용 범위를 확인하세요')
    for rule in data['rules'].values():
        for field in ('likelihood', 'impact'):
            if type(rule.get(field)) is not int or not 1 <= rule[field] <= 5:
                raise ValueError('위험도 가능성·영향은 1~5 정수입니다')
        if 'threshold' in rule and (type(rule['threshold']) is not int or rule['threshold'] < 1):
            raise ValueError('탐지 임계값은 양의 정수입니다')
    return data


def record_history(state, action, actor, data):
    state['history'].append({'at': time.time(), 'action': action, 'actor': actor, **scrub(data)})
    state['history'] = state['history'][-1000:]


def token_total(result):
    usage = result.get('usage', {})
    if not isinstance(usage, dict) or not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
        return None
    total = usage['input_tokens'] + usage['output_tokens']
    if result.get('runtime') == 'claude':
        total += usage.get('cache_read_input_tokens', 0) + usage.get('cache_creation_input_tokens', 0)
    return total


def inspect_run(directory, cfg):
    """검증 가능한 신호만 탐지한다. 인젝션·품질은 확정 판정이 아닌 조사 후보다."""
    job = read(directory / 'job.json', {})
    result = read(directory / 'result.json', {}) or read(directory / 'session-result.json', {})
    failure = read(directory / 'failure.json', {})
    manifest = read(directory / 'manifest-snapshot.json', {})
    receipts, valid = lines(directory / 'tools.jsonl')
    worker = job.get('worker') or manifest.get('worker', {}).get('id')
    if not worker:
        return None
    hits = {}
    def hit(rule, detail):
        hits[rule] = detail
    import authorization
    denied, repeated = 0, collections.Counter()
    for receipt in receipts:
        name = str(receipt.get('tool', ''))
        outcome = receipt.get('result', {})
        if not isinstance(outcome, dict):
            continue
        blocked = outcome.get('status') in ('denied', 'approval_required', 'failed', 'unavailable') or name.startswith('error:')
        if name.startswith('error:') or outcome.get('status') in ('failed', 'unavailable'):
            hit('XOC-005', '도구 오류 또는 자료 수집 실패가 있습니다.')
        if outcome.get('code') in ('role_boundary', 'asset_boundary'):
            denied += 1
        # 독립 승인자가 실행한 기존 시설 조치의 영수증 이름을 실제 도구로 대조한다.
        checked_name = name
        if name == 'approved_action':
            rid = outcome.get('authorization_request', '')
            approval = read(directory.parent.parent / 'tickets/approvals' / (rid + '.json'), {}) if isinstance(rid, str) and re.fullmatch(r'[a-f0-9]{32}', rid) else {}
            if (approval.get('worker') == worker and approval.get('decision', {}).get('approve') is True
                and approval.get('approver') != worker and approval.get('status') in ('approved', 'verified', 'verification_failed')
                and outcome.get('executor_worker') == worker and outcome.get('action') == 'simulator_control'):
                checked_name = 'simulator_control'
            else:
                hit('XOC-009', '승인 후 실행 영수증의 독립 승인 자료를 대조할 수 없습니다.')
                blocked = True  # 근거 부족을 검증된 권한 탈출로 승격하지 않는다.
        if not blocked and isinstance(manifest.get('authorization'), dict) and not authorization.allowed(manifest, checked_name):
            hit('XOC-001', '실행 사본의 직무 상한 밖 도구 성공 영수증: ' + name)
        repeated[(name, digest(receipt.get('arguments', {})))] += 1
        if name in ('log_read', 'siem_search', 'request_context', 'agent_activity', 'lab_read') and INJECTION.search(json.dumps(outcome, ensure_ascii=False)):
            hit('XOC-007', '외부 자료/요청에 지시 전환 패턴이 있습니다. 인용·교육 문맥인지 검토하세요.')
    if denied >= cfg['rules']['XOC-002']['threshold']:
        hit('XOC-002', f'직무/자산 경계 거절 {denied}회')
    if repeated and max(repeated.values()) >= cfg['rules']['XOC-003']['threshold']:
        hit('XOC-003', f'동일 도구·동일 인자 최대 {max(repeated.values())}회 반복')
    if failure:
        hit('XOC-005', '세션 실패: ' + str(failure.get('error', 'unknown'))[:120])
    if result.get('verification', {}).get('observed_live_evidence') is False:
        hit('XOC-006', '업무 결과에 성공한 관측 도구 영수증이 없습니다. 설명형 대화는 별도 판정합니다.')
    total = token_total(result)
    budgets = [lp.get('budget', {}).get('max_tokens', cfg['rules']['XOC-004']['threshold'])
               for lp in manifest.get('loops', []) if job.get('kind') == 'periodic:' + lp.get('id', '')]
    budget = min(budgets) if budgets else cfg['rules']['XOC-004']['threshold']
    if total is not None and total > budget:
        hit('XOC-004', f'입력·캐시·출력 합계 {total:,} 토큰 / 관측 예산 {budget:,} 토큰 초과')
    if result and total is None:
        hit('XOC-010', 'CLI 사용량 누락: 0 토큰으로 계산하지 않습니다.')
    if SECRET.search(str(result.get('body', ''))) or '[REDACTED]' in str(result.get('body', '')):
        hit('XOC-008', '결과에 자격증명 형식 문자열이 있습니다. 원문은 이 경보에 복사하지 않습니다.')
    if result and job.get('kind') != 'lab_evaluation' and (not manifest or not valid):
        hit('XOC-009', '실행 사본 또는 도구 증적을 검증할 수 없습니다.')
    files = {}
    for name in ('job.json', 'result.json', 'session-result.json', 'failure.json', 'manifest-snapshot.json', 'tools.jsonl'):
        path = directory / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8_000_000:
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'worker': worker, 'hits': hits, 'files': files, 'tokens': total}


def scan(root, now=None):
    root, now = Path(root), time.time() if now is None else now
    cfg = config(root)
    detector_version = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    directories = sorted((root / 'evidence').glob('*/'), key=lambda p: p.stat().st_mtime, reverse=True)
    directories = [p for p in directories if not p.is_symlink() and p.resolve().is_relative_to((root / 'evidence').resolve())]
    with state_lock(root) as state:
        checked = 0
        for directory in directories[:cfg['max_runs_per_scan']]:
            if not SLUG.fullmatch(directory.name) or directory.stat().st_mtime < now - cfg['lookback_hours'] * 3600:
                continue
            fingerprint = digest([cfg, detector_version, sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in directory.iterdir() if p.is_file() and not p.is_symlink())])
            checked += 1
            if state['seen'].get(directory.name) == fingerprint:
                continue
            run = inspect_run(directory, cfg)
            if not run:
                continue
            state['seen'][directory.name] = fingerprint
            for rule, detail in run['hits'].items():
                spec = cfg['rules'].get(rule, {})
                if not spec.get('enabled', True):
                    continue
                fid = digest([rule, directory.name])[:24]
                if fid in state['findings']:
                    continue
                score = spec['likelihood'] * spec['impact']
                state['findings'][fid] = {'id': fid, 'rule': rule, 'title': spec['title'], 'worker': run['worker'],
                    'run_id': directory.name, 'created_at': now, 'status': 'open', 'risk': score,
                    'likelihood': spec['likelihood'], 'impact': spec['impact'], 'detail': detail,
                    'evidence': run['files'], 'tokens': run['tokens'], 'response': spec['response'],
                    'confidence': 'receipt_verified' if rule == 'XOC-001' else 'requires_review',
                    'source': spec['source'], 'rule_version': cfg['version']}
        # 설정된 권한 상한 자체가 깨지면 모델 실행 전에 발견한다.
        import authorization
        import harness_tools
        roster = yaml.safe_load((root / 'roster.yaml').read_text())['workers']
        errors = authorization.validate(yaml.safe_load((root / 'harness.yaml').read_text()), roster, [t[0] for t in harness_tools.TOOLS])
        policies = yaml.safe_load((root / 'harness.yaml').read_text())
        for worker in roster:
            role = worker.get('security_role')
            configured = set(policies.get('security', {}).get('roles', {}).get(role, {}).get('tools', []))
            baseline = cfg.get('role_tool_baselines', {}).get(role)
            extras = sorted(configured - set(baseline or []))
            if (extras or baseline is None) and cfg['rules']['XOC-011'].get('enabled', True):
                fid = digest(['XOC-011', worker['id'], extras, role])[:24]
                if fid not in state['findings']:
                    spec = cfg['rules']['XOC-011']
                    state['findings'][fid] = {'id': fid, 'rule': 'XOC-011', 'title': spec['title'], 'worker': worker['id'],
                        'run_id': None, 'created_at': now, 'status': 'open', 'risk': spec['likelihood'] * spec['impact'],
                        'likelihood': spec['likelihood'], 'impact': spec['impact'],
                        'detail': '기준선 미등록 직무' if baseline is None else '추가 도구: ' + ', '.join(extras),
                        'evidence': {'harness.yaml': hashlib.sha256((root / 'harness.yaml').read_bytes()).hexdigest()},
                        'tokens': None, 'response': spec['response'], 'confidence': 'requires_review',
                        'source': spec['source'], 'rule_version': cfg['version']}
        state['configuration_errors'] = errors
        state['checked_at'] = now
        state['coverage'] = {'checked_runs': checked, 'available_runs': len(directories),
                             'lookback_hours': cfg['lookback_hours'], 'scan_limit': cfg['max_runs_per_scan'],
                             'limited': len(directories) > cfg['max_runs_per_scan']}
        if len(state['seen']) > 3000:
            state['seen'] = dict(list(state['seen'].items())[-2000:])
        # 발견은 지우지 않는다. 같은 증거를 다시 읽어도 동일 ID로 유지한다.
    return snapshot(root)


def snapshot(root):
    state = stored_state(root)
    return scrub({'checked_at': state.get('checked_at'), 'coverage': state.get('coverage'),
                  'configuration_errors': state.get('configuration_errors', []),
                  'findings': sorted(state.get('findings', {}).values(), key=lambda f: (f['status'] not in ('open', 'acknowledged'), -f['risk'], -f['created_at']))[:500],
                  'findings_total': len(state.get('findings', {})), 'display_limit': 500,
                  'holds': [h for h in state.get('holds', {}).values() if h['until'] > time.time()],
                  'history': state.get('history', [])[-100:], 'rules': config(root)})


def probe(root):
    data = scan(root)
    # 관제 결과·상태 변경·관제원 자신의 실행으로 다시 자신을 깨우지 않는다.
    relevant = [f for f in stored_state(root)['findings'].values() if f['worker'] != 'agent-supervisor']
    return {'signals': {'findings': sorted(f['id'] for f in relevant), 'configuration_errors': data['configuration_errors']},
            'problems': [f['id'] for f in relevant if f['risk'] >= 12 and f['status'] == 'open'] + data['configuration_errors'],
            'sources': ['local:agents/evidence', 'local:agents/xoc/rules.yaml']}


def held(root, worker):
    item = stored_state(root).get('holds', {}).get(worker)
    return item if item and item['until'] > time.time() else None


def review(root, finding_id, status, reason, actor):
    if status not in ('acknowledged', 'resolved', 'false_positive') or not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 3000:
        raise ValueError('판정과 10~3000자의 근거가 필요합니다')
    with state_lock(root) as state:
        finding = state['findings'].get(finding_id)
        if not finding:
            raise ValueError('발견 항목이 없습니다')
        if actor == finding['worker']:
            raise ValueError('자신의 발견은 독립 검토자가 판정해야 합니다')
        finding.update(status=status, reviewed_by=actor, reviewed_at=time.time(), reason=scrub(reason))
        record_history(state, 'review', actor, {'finding_id': finding_id, 'status': status, 'reason': reason})
    return {'status': status, 'finding_id': finding_id}


def containment(root, worker, minutes, reason, actor, finding_id=None):
    workers = {w['id']: w for w in yaml.safe_load((Path(root) / 'roster.yaml').read_text())['workers']}
    if worker not in workers or type(minutes) is not int or not 0 <= minutes <= 60 or not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 3000:
        raise ValueError('등록된 담당자, 0~60분, 구체적인 사유가 필요합니다')
    with state_lock(root) as state:
        if actor != 'instructor':
            finding = state['findings'].get(finding_id, {})
            if (actor != 'agent-supervisor' or worker == actor or workers[worker]['security_role'] in ('reviewer', 'auditor')
                or minutes < 1 or minutes > 15 or finding.get('worker') != worker or finding.get('rule') != 'XOC-001'
                or finding.get('status') not in ('open', 'acknowledged') or finding.get('confidence') != 'receipt_verified'):
                raise ValueError('자동 보류는 검증된 직무 경계 위반에 한해 15분 이내입니다. 해제·승인자 보류는 강사가 결정합니다')
            if finding.get('automatic_hold_at'):
                raise ValueError('같은 사건의 자동 보류를 반복 연장할 수 없습니다. 강사에게 검토를 요청하세요')
            finding['automatic_hold_at'] = time.time()
        if minutes:
            state['holds'][worker] = {'worker': worker, 'until': time.time() + minutes * 60, 'actor': actor,
                                      'reason': scrub(reason), 'finding_id': finding_id}
        else:
            state['holds'].pop(worker, None)
        record_history(state, 'hold' if minutes else 'release', actor, {'worker': worker, 'minutes': minutes, 'reason': reason, 'finding_id': finding_id})
    return {'status': 'held' if minutes else 'released', 'worker': worker, 'minutes': minutes,
            'scope': '새 세션·다음 도구 호출 보류. 이미 진행 중인 모델 추론이나 원격 작업은 강제 종료하지 않음'}


def compliance(root):
    """교육용 내부 통제 증적. 인증·법적 적합성을 자동 판정하지 않는다."""
    root = Path(root)
    state = snapshot(root)
    docs = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in ('roster.yaml', 'harness.yaml', 'xoc/rules.yaml')}
    return {'observed_at': time.time(), 'source_hashes': docs, 'controls': [
        {'id': 'AC-01', 'title': '최소 권한·직무 분리', 'status': 'gap' if state['configuration_errors'] else 'configuration_verified',
         'evidence': ['harness.yaml', 'roster.yaml'], 'exceptions': state['configuration_errors']},
        {'id': 'AU-01', 'title': '실행 추적·증적 완전성', 'status': 'review_required', 'evidence': state['coverage'],
         'exceptions': [f['id'] for f in state['findings'] if f['rule'] in ('XOC-006', 'XOC-009', 'XOC-010') and f['status'] != 'resolved']},
        {'id': 'CH-01', 'title': '스킬 변경 승인·평가·복구', 'status': 'review_required', 'evidence': 'research-lab 후보별 평가·배포 이력'},
        {'id': 'IR-01', 'title': '사고 처리·보류·복구', 'status': 'review_required', 'evidence': state['history']},
        {'id': 'DP-01', 'title': '개인정보 보존·삭제·접근권한', 'status': 'not_measured', 'evidence': [], 'reason': '대상별 보존 정책과 실제 저장소 자료 필요'},
        {'id': 'SC-01', 'title': '공급망·의존성·취약점', 'status': 'not_measured', 'evidence': [], 'reason': 'SBOM과 버전별 취약점 검증 자료 필요'}],
        'limitation': '설정 검증과 운영 효과를 구분합니다. 미측정·표본 자료를 전체 준수로 간주하지 않습니다.'}
