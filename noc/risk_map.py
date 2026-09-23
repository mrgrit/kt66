"""도구 영수증을 리스크 공간에 투영한다. 모델·탐지기·조치를 실행하지 않는다.

위치는 관측된 활동의 주 평가축이며 실제 네트워크 위치가 아니다. 접근 거절은
경계에, 승인 대기는 게이트에 둔다. 캐릭터의 이동은 증거 사이를 잇는 시각 표현이다.
"""
from __future__ import annotations

import math
import re
import threading
import time

import yaml
from fastapi import HTTPException

ZONE_IDS = {f"Z{i:02}" for i in range(1, 13)}
RUN_ID = re.compile(r"(?:loop|session)-[a-zA-Z0-9_-]{1,170}\Z")
EXPOSURES = [
    {"level": 0, "label": "활동 없음", "color": "#81939c"},
    {"level": 1, "label": "기본 조회·기록", "color": "#a9d79b"},
    {"level": 2, "label": "민감 조회·산출물", "color": "#e0c17f"},
    {"level": 3, "label": "상태·구성 변경", "color": "#e7a277"},
    {"level": 4, "label": "승인·실행 통제", "color": "#d9a1c4"},
]


def timestamp(value):
    from agent_control import stamp
    result = stamp(value)
    return result if math.isfinite(result) else 0


def mapping(value):
    return value if isinstance(value, dict) else {}


class RiskMap:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.cache = None
        self.cached_at = 0

    def yaml(self, name):
        try:
            path = self.store.safe(self.store.root / name)
            if path.stat().st_size > 2_000_000:
                return {}
            return mapping(yaml.safe_load(path.read_text()))
        except (OSError, ValueError, yaml.YAMLError):
            return {}

    def config(self):
        cfg = self.yaml('xoc/risk-zones.yaml')
        zones = cfg.get('zones', [])
        if (not isinstance(zones, list) or len(zones) != 12
                or any(not isinstance(z, dict) for z in zones)
                or {z.get('id') for z in zones} != ZONE_IDS):
            raise HTTPException(503, '리스크 구역 설정을 읽을 수 없습니다.')
        for zone in zones:
            if any(not isinstance(zone.get(k), str) for k in ('name', 'subtitle', 'description', 'question', 'color')):
                raise HTTPException(503, '리스크 구역 설명 형식을 확인하세요.')
            if not re.fullmatch(r'#[a-fA-F0-9]{6}', zone['color']):
                raise HTTPException(503, '리스크 구역 색상을 확인하세요.')
        for spec in mapping(cfg.get('tools')).values():
            if (not isinstance(spec, dict) or spec.get('primary') not in ZONE_IDS
                    or not isinstance(spec.get('axes'), list) or any(a not in ZONE_IDS for a in spec['axes'])
                    or type(spec.get('exposure')) is not int or not 1 <= spec['exposure'] <= 4):
                raise HTTPException(503, '도구의 리스크 구역 연결을 확인하세요.')
        return cfg

    def catalogue(self):
        cfg = self.config()
        return {'version': cfg['version'], 'zones': cfg['zones'], 'exposures': EXPOSURES}

    def tool_event(self, row, receipt, line, cfg):
        raw_name = str(receipt.get('tool', 'unknown'))
        name = raw_name.removeprefix('error:')
        args, result, auth = (mapping(receipt.get(k)) for k in ('arguments', 'result', 'authorization'))
        spec = cfg.get('tools', {}).get(name, {})
        zone = spec.get('primary', 'Z09')
        attempted_zone = zone
        axes = list(spec.get('axes', ['Z09']))
        status, code = result.get('status'), result.get('code')
        outcome = 'observed'
        if status in ('held', 'paused') or code == 'xoc_hold':
            zone, outcome = 'Z11', 'held'
        elif status == 'denied' or code in ('role_boundary', 'asset_boundary'):
            zone, outcome = 'Z04', 'denied'
        elif status in ('approval_required', 'pending_approval', 'approval_pending'):
            zone, outcome = 'Z05', 'pending'
        elif raw_name.startswith('error:') or status in ('failed', 'unavailable', 'execution_failed', 'error'):
            outcome = 'error'
        if zone not in axes:
            axes.append(zone)
        decisions = auth.get('user_decisions')
        decisions = decisions if isinstance(decisions, list) else []
        permission = {k: auth.get(k) for k in ('role', 'permission', 'mode', 'autonomy', 'boundary')}
        permission['decisions'] = [dict(decision=d.get('decision'), reference=d.get('request') or d.get('grant'))
                                   for d in decisions if isinstance(d, dict)][:5]
        targets = []
        for key in ('target', 'asset', 'worker', 'target_worker', 'path', 'candidate_id', 'name'):
            if isinstance(args.get(key), str):
                targets.append(args[key][:180])
        summary = {'denied': '접근 거절 · 실행 진입 안 함', 'pending': '승인 대기 · 실행 진입 안 함',
                   'error': '호출 오류 · 실행 결과 확인 필요', 'held': '실행 보류',
                   'observed': '도구 호출 기록 수신'}[outcome]
        if name in ('waf_prepare', 'website_prepare') and outcome == 'observed':
            summary = '변경안 준비 · 운영 적용 기록 아님'
        if name in ('simulator_control', 'approved_action'):
            targets = ['가상 시설 시뮬레이터'] + targets
        return {'id': row['id'] + ':tool:' + str(line), 'at': timestamp(receipt.get('at')),
                'worker': row['worker'], 'run_id': row['id'], 'kind': 'tool', 'name': name,
                'zone': zone, 'attempted_zone': attempted_zone, 'axes': axes,
                'exposure': spec.get('exposure'), 'outcome': outcome, 'summary': summary,
                'targets': targets, 'permission': permission, 'source': f'tools.jsonl#L{line}',
                'assertion': 'tool_receipt', 'trigger': row.get('trigger'),
                'reason': str(result.get('reason') or result.get('error') or '')[:320]}

    def events_for(self, row, cfg, now):
        if not RUN_ID.fullmatch(row['id']):
            return [], ['실행 영수증 디렉터리 미수집']
        directory = self.store.evidence / row['id']
        tools, issues = self.store.lines(directory / 'tools.jsonl')
        activity, gaps = self.store.lines(directory / 'activity.jsonl')
        events = [self.tool_event(row, r, i + 1, cfg) for i, r in enumerate(tools)]
        for i, r in enumerate(activity):
            kind, data = r.get('type', ''), mapping(r.get('data'))
            if kind == 'request':
                zone, outcome, title = 'Z01', 'started', '작업 요청 수신'
            elif kind in ('session.completed', 'session.failed'):
                zone, outcome, title = 'Z11', 'completed' if kind.endswith('completed') else 'error', '세션 종료' if kind.endswith('completed') else '세션 실패'
            elif kind.startswith('agent.'):
                zone, outcome, title = 'Z08', 'declared', {'agent.plan': '작업 계획', 'agent.situation': '상황 인식', 'agent.decision': '판단 요약', 'agent.review': '결과 검토'}.get(kind, '업무 보고')
            else:
                continue
            events.append({'id': row['id'] + ':activity:' + str(i + 1), 'at': timestamp(r.get('at')),
                           'worker': row['worker'], 'run_id': row['id'], 'kind': kind, 'name': title,
                           'zone': zone, 'attempted_zone': zone, 'axes': [zone], 'exposure': 1,
                           'outcome': outcome, 'summary': str(data.get('summary') or title)[:220],
                           'targets': [], 'permission': {}, 'source': f'activity.jsonl#L{i+1}',
                           'assertion': 'agent_declared' if outcome == 'declared' else 'runtime_record',
                           'trigger': row.get('trigger'), 'reason': ''})
        valid = [e for e in events if 0 < e['at'] <= now + 5]
        if len(valid) != len(events):
            issues.append('증거 시각 미수집 또는 미래 시각')
        return valid, sorted(set(issues + gaps))

    def snapshot(self):
        with self.lock:
            if self.cache and time.monotonic() - self.cached_at < 3:
                return self.cache
            self.cache = self.build()
            self.cached_at = time.monotonic()
            return self.cache

    def build(self):
        now, cfg = time.time(), self.config()
        store = self.store
        store.refresh()
        with store.lock:
            rows, index_health = list(store.rows), dict(store.health)
        states = store.worker_states()
        state_by = {s['worker']: s for s in states['items']}
        roster = self.yaml('roster.yaml')
        harness = self.yaml('harness.yaml')
        roles = mapping(mapping(harness.get('security')).get('roles'))
        errors = list(index_health.get('errors', [])) + states['source'].get('errors', [])
        if not roster.get('workers'):
            errors.append('에이전트 명단 미수집')
        # 최근 24시간의 증거를 재생하되, 오래 실행 중인 세션도 포함한다.
        candidates = [r for r in rows if r['updated'] >= now - 86400 or r['status'] == 'running']
        candidates.sort(key=lambda r: (r['status'] == 'running', r['updated']), reverse=True)
        selected, events, issues = candidates[:120], [], []
        for row in selected:
            additions, gaps = self.events_for(row, cfg, now)
            events.extend(e for e in additions if e['at'] >= now - 86400)
            if gaps:
                issues.append({'run_id': row['id'], 'issues': gaps})
        events.sort(key=lambda e: (e['at'], e['id']))
        # 공통 마스킹의 500개 배열 한도 안에서 최신 기록을 보존한다.
        event_total = len(events)
        events = events[-480:]
        latest_by = {}
        for e in events:
            latest_by[e['worker']] = e
        xoc = store.read(store.root / 'tickets/xoc/state.json', limit=50_000_000)
        findings = []
        for finding in mapping(xoc.get('findings')).values():
            if not isinstance(finding, dict) or finding.get('status') not in ('open', 'acknowledged'):
                continue
            item = {k: finding.get(k) for k in ('id', 'worker', 'run_id', 'rule', 'title', 'created_at', 'status', 'risk', 'likelihood', 'impact', 'confidence', 'detail')}
            item['zone'] = cfg.get('findings', {}).get(item['rule'], 'Z12')
            findings.append(item)
        findings.sort(key=lambda f: (f.get('risk') or 0, f.get('created_at') or 0), reverse=True)
        all_findings = len(findings)
        findings = findings[:200]
        holds = [h for h in mapping(xoc.get('holds')).values() if isinstance(h, dict) and timestamp(h.get('until')) > now]
        worker_ids = set()
        workers = []
        for worker in roster.get('workers', []):
            if not isinstance(worker, dict) or not isinstance(worker.get('id'), str):
                continue
            worker_ids.add(worker['id'])
            workers.append({k: worker.get(k) for k in ('id', 'name', 'runtime', 'model', 'security_role', 'autonomy')})
        # 임시 역할도 증거가 있으면 빠뜨리지 않는다. 이름·권한을 임의로 추정하지 않는다.
        for wid in sorted(set(latest_by) | set(state_by)):
            if wid not in worker_ids:
                workers.append({'id': wid, 'name': wid, 'temporary_or_unlisted': True})
        engine = store.read(store.root / 'tickets/loop-engine-status.json')
        healthy_engine = (states['source']['status'] == 'ok' and states.get('engine_at')
                          and 0 <= now - states['engine_at'] <= 60 and engine.get('status') == 'running'
                          and isinstance(engine.get('active_workers'), list) and not engine.get('error'))
        for w in workers:
            wid = w['id']
            state = state_by.get(wid, {})
            w['state'] = state.get('state', 'idle' if healthy_engine else 'unknown')
            w['state_reason'] = state.get('reason', '자동 실행 기록 없음' if healthy_engine else '실행기 상태 미확인')
            w['last_event'] = latest_by.get(wid)
            w['findings'] = [f for f in findings if f['worker'] == wid]
            w['hold'] = next((h for h in holds if h.get('worker') == wid), None)
            w['role_tools'] = mapping(roles.get(w.get('security_role'))).get('tools', [])
            w['capability_zones'] = sorted({cfg['tools'][t]['primary'] for t in w['role_tools'] if t in cfg.get('tools', {})})
            # 대화·독립 세션의 현재 실행 여부는 heartbeat만으로 알 수 없다.
            # 최신 관측 위치는 마지막 활동 표시로만 사용하고 종료/노후화를 명시한다.
            last = w['last_event']
            if w['hold']:
                w['location'], w['presence'] = 'HOLD', 'held'
            elif w['state'] in ('unknown', 'stopped'):
                w['location'], w['presence'] = 'DOCK', w['state']
            elif w['state'] == 'working':
                current = next((r for r in selected if r['worker'] == wid and r['status'] == 'running'), None)
                current_last = last if current and last and current['id'] == last['run_id'] else None
                w['location'] = current_last['zone'] if current_last else 'Z01'
                w['presence'] = 'active' if current_last and now - current_last['at'] <= 60 else 'awaiting_evidence'
            elif last and now - last['at'] <= 45 and last['outcome'] != 'completed':
                w['location'], w['presence'] = last['zone'], 'recent'
            else:
                w['location'], w['presence'] = 'DOCK', 'idle'
        summary = {'agents': len(workers), 'active': sum(w['state'] == 'working' for w in workers),
                   'unknown': sum(w['state'] == 'unknown' for w in workers),
                   'open_findings': all_findings, 'holds': len(holds),
                   'denied': sum(e['outcome'] == 'denied' for e in events),
                   'pending': sum(w['last_event'] is not None and w['last_event']['outcome'] == 'pending'
                                  and w['presence'] in ('active', 'recent') for w in workers),
                   'events': len(events)}
        coverage = {'status': 'partial' if errors or issues or not xoc else 'ok',
                    'errors': sorted(set(errors)), 'issues': issues[:50], 'index_at': index_health.get('at'),
                    'engine_at': states.get('engine_at'), 'xoc_checked_at': xoc.get('checked_at'),
                    'xoc_available': bool(xoc), 'run_limit': 120, 'selected_runs': len(selected),
                    'available_runs': len(candidates), 'event_limit': 480, 'available_events': event_total,
                    'truncated': len(candidates) > 120 or event_total > 480 or bool(issues) or all_findings > 200,
                    'scope': '브로커 도구·업무 보고·실행기·xOC 저장 기록. OS 전체 접근·CLI 내부 도구는 미수집.'}
        result = {'schema': 'kt66.risk-map.v1', 'collected_at': now, 'poll_seconds': 5,
                  'catalogue': {'version': cfg['version'], 'zones': cfg['zones'], 'exposures': EXPOSURES},
                  'workers': workers, 'events': events, 'findings': findings, 'summary': summary,
                  'coverage': coverage, 'range': {'start': max(now - 86400, events[0]['at']) if events else now - 86400, 'end': now},
                  'read_only': True}
        # 감지·승인·모델 호출을 수행하지 않는 순수 투영. 원천 비밀은 응답 경계에서 마스킹한다.
        return store.public(result)
