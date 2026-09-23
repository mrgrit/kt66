"""런타임에 독립적인 관제 문서. 원문 프롬프트·내부 추론·전체 도구 결과는 전송하지 않는다."""
import datetime as dt
import hashlib
import json
import math
import re
from activity_audit import scrub

EVENTS = 'kt66-agent-events-'
FINDINGS = 'kt66-agent-findings-v1'
TICKETS = 'kt66-agent-tickets-v1'
RUN = re.compile(r'(?:loop|session)-[a-zA-Z0-9_-]{1,170}\Z')


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def stamp(value, fallback):
    try:
        result = float(value) if isinstance(value, (int, float)) else dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        if math.isfinite(result) and 0 < result < 32_503_680_000:
            return result
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    return fallback


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def obj(value):
    return value if isinstance(value, dict) else {}


def text(value, limit=512):
    return value[:limit] if isinstance(value, str) else ''


def domain(worker, role=''):
    if worker == 'soc-analyst' or role == 'soc':
        return 'soc'
    if worker == 'agent-supervisor' or role == 'supervision':
        return 'xoc'
    return 'operations'


def outcome(name, result):
    status, code = result.get('status'), result.get('code')
    if code == 'xoc_hold' or status in ('held', 'paused'):
        return 'held'
    if status == 'denied' or code in ('role_boundary', 'asset_boundary'):
        return 'denied'
    if status in ('approval_pending', 'approval_required', 'pending_approval'):
        return 'pending'
    if name.startswith('error:') or status in ('failed', 'error', 'unavailable', 'execution_failed'):
        return 'error'
    return 'observed'


def document(relative, raw, meta, mtime, cfg, line=None, source_bytes=None):
    """허용 필드만 내보낸다. 영수증 관측과 에이전트 자기 보고를 구별한다."""
    name = relative.rsplit('/', 1)[-1]
    run = meta.get('run_id', '')
    record = obj(raw)
    worker = text(meta.get('worker') or record.get('worker')) or 'unknown'
    timestamp = stamp(record.get('at'), meta.get('started_at', mtime) if name.endswith('.jsonl') else mtime)
    doc = {'schema_version': 'kt66.siem.v1', '@timestamp': iso(timestamp),
           'worker': worker, 'role': text(meta.get('role')), 'run_id': run,
           'trigger': text(meta.get('trigger')), 'domain': domain(worker, meta.get('role')),
           'evidence_ref': relative + (f'#L{line}' if line is not None else ''),
           'time_basis': 'record' if record.get('at') else 'run_start' if name.endswith('.jsonl') and meta.get('started_at') else 'file_mtime'}
    identity = relative + ':' + str(record.get('id') or line or '')
    index = EVENTS + dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime('%Y.%m.%d')
    if name == 'tools.jsonl':
        tool = text(record.get('tool'))
        result, args, permission = obj(record.get('result')), obj(record.get('arguments')), obj(record.get('authorization'))
        state = outcome(tool, result)
        spec = obj(obj(cfg.get('tools')).get(tool.removeprefix('error:')))
        zone = {'denied': 'Z04', 'pending': 'Z05', 'held': 'Z11'}.get(state, spec.get('primary', 'Z09'))
        doc.update(kind='tool', name=tool.removeprefix('error:'), outcome=state, assertion='broker_receipt',
                   zone=zone, axes=list(dict.fromkeys([*spec.get('axes', []), zone])),
                   permission_mode=text(permission.get('mode')) or 'unknown', permission_name=text(permission.get('permission')),
                   autonomy=text(permission.get('autonomy')), boundary=text(permission.get('boundary')),
                   targets=[text(args.get(k), 180) for k in ('target', 'asset', 'target_worker', 'path') if isinstance(args.get(k), str)],
                   approval_refs=[text(d.get('request') or d.get('grant')) for d in permission.get('user_decisions', []) if isinstance(d, dict)],
                   summary={'denied': '직무·권한 접근 거절', 'pending': '승인 대기', 'held': '실행 보류', 'error': '호출 오류', 'observed': '도구 영수증 수집'}[state])
        if isinstance(spec.get('exposure'), int):
            doc['exposure'] = spec['exposure']
        doc['role'] = text(permission.get('role')) or doc['role']
    elif name == 'activity.jsonl':
        kind, data = text(record.get('type')), obj(record.get('data'))
        doc.update(kind='activity', name=kind, outcome='error' if kind == 'session.failed' else 'reported',
                   assertion='agent_declared' if kind.startswith('agent.') else 'runtime_record',
                   runtime=text(data.get('runtime')), model=text(data.get('model')), summary=kind)
        # 자기 보고의 요약만. prompt/thinking/reasoning 필드는 복제하지 않는다.
        if kind in ('agent.situation', 'agent.plan', 'agent.decision', 'agent.review'):
            doc['summary'] = text(data.get('summary'), 800) or kind
    elif name in ('result.json', 'session-result.json', 'failure.json'):
        # session-result와 최종 result는 같은 실행의 한 결과를 갱신한다(토큰 이중 계산 방지).
        identity = run + ':result'
        timestamp = meta.get('started_at', mtime)
        doc['@timestamp'], doc['time_basis'] = iso(timestamp), 'run_start'
        index = EVENTS + dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime('%Y.%m.%d')
        usage = obj(record.get('usage'))
        known = all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens'))
        doc.update(kind='session', name='session.result', outcome='error' if name == 'failure.json' else 'completed',
                   assertion='runtime_record', runtime=text(record.get('runtime')), model=text(record.get('model')), usage_known=known,
                   summary='실행 실패' if name == 'failure.json' else '세션 결과 기록 · 업무 성공 판정은 증적 확인 필요')
        if known:
            total = usage['input_tokens'] + usage['output_tokens']
            if record.get('runtime') == 'claude':
                total += sum(v for k in ('cache_read_input_tokens', 'cache_creation_input_tokens') if type(v := usage.get(k)) is int and v >= 0)
            doc['tokens_total'] = total
    elif name.endswith('.md'):
        body = text(raw, 40_000)
        title = next((x.lstrip('# ').strip() for x in body.splitlines() if x.strip()), name)
        doc.update(kind='ticket', name='report', title=title[:300], body=body, status='reference', assertion='agent_declared',
                   summary='참고 기록 · 원문에는 표준 사건 상태가 없어 미종결 경보로 집계하지 않습니다')
        index = TICKETS
    else:
        return None
    doc['event_id'] = digest(identity)
    doc['evidence_sha256'] = digest(source_bytes if source_bytes is not None else json.dumps(raw, sort_keys=True, ensure_ascii=False))
    return index, doc['event_id'], doc


def finding_document(row):
    created = stamp(row.get('created_at'), 0)
    doc = {k: row.get(k) for k in ('worker', 'run_id', 'title', 'rule', 'status', 'risk', 'likelihood', 'impact', 'confidence')}
    doc.update(schema_version='kt66.siem.v1', event_id=text(row.get('id')), kind='finding', domain='xoc',
               summary=text(row.get('detail'), 1500), assertion='detector', evidence_ref='tickets/xoc/state.json',
               evidence_sha256=digest(json.dumps(row.get('evidence'), sort_keys=True)),
               reviewed_by=text(row.get('reviewed_by')), review_reason=text(row.get('reason'), 3000))
    doc['@timestamp'] = iso(created)
    if row.get('reviewed_at'):
        doc['reviewed_at'] = iso(stamp(row['reviewed_at'], created))
    return FINDINGS, doc['event_id'], doc


def control_document(row):
    """강사·관제원의 판정/보류 이력은 현재 사건 스냅샷과 별도 이벤트로 남긴다."""
    timestamp = stamp(row.get('at'), 0)
    action = text(row.get('action'))
    doc = {'schema_version': 'kt66.siem.v1', '@timestamp': iso(timestamp), 'time_basis': 'record',
           'event_id': digest(json.dumps(row, sort_keys=True, ensure_ascii=False)), 'kind': 'control', 'name': 'xoc.' + action,
           'domain': 'xoc', 'worker': text(row.get('actor')) or 'unknown', 'assertion': 'control_record',
           'outcome': 'recorded', 'status': text(row.get('status')), 'targets': [text(row.get('worker'))] if row.get('worker') else [],
           'review_reason': text(row.get('reason'), 3000), 'summary': 'xOC 판정·통제 기록 · ' + action,
           'evidence_ref': 'tickets/xoc/state.json#history', 'evidence_sha256': digest(json.dumps(row, sort_keys=True, ensure_ascii=False))}
    return EVENTS + dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime('%Y.%m.%d'), doc['event_id'], doc


def safe_document(doc, secrets=()):
    return scrub(doc, secrets)
