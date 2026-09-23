"""Persistent user requests. The existing runner owns execution and global capacity."""
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ID = re.compile(r"[a-z0-9][a-z0-9-]{1,70}\Z")
OPEN = {"queued", "running", "waiting_tasks"}
JOB_PENDING = {"queued", "retry", "waiting_capacity", "running"}
CAPABILITIES = {
    "inventory": "자산 대장과 실제 Docker 네트워크 조회",
    "siem": "시작·종료 시각을 지정한 SIEM 경보 검색",
    "workspace": "요청 전용 작업 공간의 파일 읽기·작성",
    "website": "정적 홈페이지 검증 및 WAF 경유 배포안 작성",
    "waf": "요청 페이로드에 대한 ModSecurity 차단 규칙 작성·검증",
}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def text(value, maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'내용은 1~{maximum}자여야 합니다')
    return value.strip()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.base = self.root / 'tickets' / 'requests'

    def directory(self, rid):
        if not isinstance(rid, str) or not ID.fullmatch(rid):
            raise ValueError('잘못된 요청 ID')
        return self.base / rid

    def get(self, rid):
        return json.loads((self.directory(rid) / 'request.json').read_text())

    @contextlib.contextmanager
    def edit(self, rid):
        directory = self.directory(rid)
        if not (directory / 'request.json').is_file():
            raise FileNotFoundError(rid)
        with (directory / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.get(rid)
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                data['updated'] = time.time()
                write_json(directory / 'request.json', data)

    def list(self, limit=200):
        rows = []
        for p in self.base.glob('*/request.json'):
            try:
                d = json.loads(p.read_text())
                rows.append({**{k: d[k] for k in ('id', 'title', 'status', 'created', 'updated', 'sessions', 'budget', 'scope')},
                             'mode': d.get('mode', 'request'), 'worker': d.get('worker')})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(rows, key=lambda r: r['updated'], reverse=True)[:limit]

    def workers(self):
        import authorization
        return authorization.directory(self.root)

    def create(self, prompt, title='', scope=None, budget=8, max_agents=None,
               mode='request', worker=None, timezone='UTC'):
        prompt = text(prompt)
        if mode not in ('request', 'conversation'):
            raise ValueError('업무 요청 또는 근무자 대화를 선택하세요')
        if mode == 'conversation':
            if worker not in {w['id'] for w in self.workers()}:
                raise ValueError('대화할 근무자를 선택하세요')
            if scope not in (None, 'read') or max_agents not in (None, 0):
                raise ValueError('근무자 대화는 조회 전용입니다. 변경·협업은 업무 요청을 이용하세요')
        elif worker is not None:
            raise ValueError('담당자 지정은 근무자 대화에서 사용하세요')
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError('올바른 시간대를 지정하세요') from None
        scope = scope or ('read' if mode == 'conversation' else 'prepare')
        max_agents = max_agents if max_agents is not None else (0 if mode == 'conversation' else 3)
        if scope not in ('read', 'prepare'):
            raise ValueError('실행 범위는 read 또는 prepare입니다')
        if type(budget) is not int or not 1 <= budget <= 30 or type(max_agents) is not int or not 0 <= max_agents <= 6:
            raise ValueError('세션 예산 1~30, 신규 에이전트 수 0~6 범위입니다')
        rid = 'req-' + uuid.uuid4().hex[:16]
        now = time.time()
        data = dict(id=rid, title=text(title or prompt[:100], 150), scope=scope,
                    mode=mode, worker=worker, timezone=timezone,
                    created=now, updated=now, status='queued', revision=1, sessions=0,
                    budget=budget, max_agents=max_agents, agents=[], tasks=[], changes=[],
                    messages=[dict(role='user', text=prompt, at=now)], events=[], result=None)
        self._coordinator(data, 'plan')
        write_json(self.directory(rid) / 'request.json', data)
        (self.directory(rid) / 'workspace').mkdir()
        return data

    def _coordinator(self, data, phase):
        if data.get('mode') == 'conversation':
            phase = 'conversation'
        tid = f'{phase}-{data["revision"]}'
        if any(t['id'] == tid for t in data['tasks']):
            return
        import authorization
        worker = data['worker'] if phase == 'conversation' else ('service-desk' if phase == 'plan' else 'ops-lead')
        caps = authorization.load(self.root, worker).get('capabilities', [])
        data['tasks'].append(dict(id=tid, phase=phase, revision=data['revision'],
            title='대화 답변·조사' if phase == 'conversation' else ('요청 검토·계획' if phase == 'plan' else '결과 통합·검증'),
            worker=worker,
            instructions='사용자 요청과 실제 도구에 근거하여 처리하고 결과를 반환하세요.',
            depends_on=[], capabilities=[c for c in caps if c in ('inventory', 'siem')] if phase == 'conversation' else [], status='queued'))

    def reply(self, rid, message):
        with self.edit(rid) as d:
            if d['status'] == 'applying':
                raise ValueError('변경 적용이 끝난 뒤 후속 요청을 보내세요')
            if d.get('mode') == 'conversation' and d['status'] in OPEN:
                raise ValueError('담당자가 답변 중입니다. 답변을 받은 뒤 이어서 보내세요')
            d['messages'].append(dict(role='user', text=text(message), at=time.time()))
            d['revision'] += 1
            d['status'] = 'queued'
            d['result'] = None
            for task in d['tasks']:
                if task['status'] in ('queued', 'running'):
                    task['status'] = 'superseded'
            for change in d['changes']:
                if change['status'] == 'proposed':
                    change['status'] = 'superseded'
            for permission in d.get('permission_requests', []):
                if permission['status'] in ('pending', 'deferred', 'allowed_once'):
                    permission['status'] = 'superseded'
            for agent in d['agents']:
                agent['state'] = 'active'
            self._coordinator(d, 'plan')
        return self.get(rid)

    def cancel(self, rid):
        with self.edit(rid) as d:
            if d['status'] == 'applying':
                raise ValueError('변경 적용이 끝난 뒤 중지할 수 있습니다')
            d['status'] = 'cancelled'
            for task in d['tasks']:
                if task['status'] in ('queued', 'running'):
                    task['status'] = 'cancelled'
            for agent in d['agents']:
                agent['state'] = 'archived'
            for permission in d.get('permission_requests', []):
                if permission['status'] in ('pending', 'deferred', 'allowed_once'):
                    permission['status'] = 'cancelled'
            d['events'].append(dict(at=time.time(), kind='cancelled', detail='사용자가 중지했습니다'))
        return self.get(rid)

    def check(self, rid, task_id, revision):
        d = self.get(rid)
        task = next((t for t in d['tasks'] if t['id'] == task_id), None)
        if d['revision'] != revision or d['status'] not in OPEN or not task or task['status'] != 'running':
            raise ValueError('요청이 변경·중지됐거나 이 작업의 실행이 종료됐습니다')
        return d, task

    def agent(self, rid, revision, name, mission, template, capabilities):
        roster = yaml.safe_load((self.root / 'roster.yaml').read_text())
        base = next((w for w in roster['workers'] if w['id'] == template), None)
        if not base:
            raise ValueError('등록된 근무자를 실행 템플릿으로 지정하세요')
        if not isinstance(capabilities, list) or not capabilities or set(capabilities) - CAPABILITIES.keys():
            raise ValueError('지원하지 않는 실행 기능입니다. 도구 구현이 먼저 필요합니다')
        import authorization
        p = authorization.load(self.root, template)
        if not p.get('temporary') or set(capabilities) - set(p.get('capabilities', [])):
            raise ValueError('임시 역할은 실행 직무의 허용된 기능만 상속할 수 있습니다')
        name, mission = text(name, 100), text(mission, 5000)
        with self.edit(rid) as d:
            if d['revision'] != revision or d['status'] not in OPEN:
                raise ValueError('이전 요청 버전입니다')
            for a in d['agents']:
                if a['name'] == name and a['mission'] == mission and a['template'] == template:
                    return a
            if len(d['agents']) >= d['max_agents']:
                raise ValueError('요청의 신규 에이전트 수 한도에 도달했습니다')
            if d['scope'] == 'read' and set(capabilities) - {'inventory', 'siem'}:
                raise ValueError('이 요청은 조회만 허용합니다')
            aid = 'project-' + uuid.uuid4().hex[:12]
            a = dict(id=aid, name=name, mission=mission, template=template,
                     runtime=base['runtime'], model=base['model'], capabilities=capabilities,
                     state='active', lifetime='request', created=time.time())
            d['agents'].append(a)
            d['events'].append(dict(at=time.time(), kind='agent_created', detail=name, agent=aid))
        return a

    def plan(self, rid, revision, tasks):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 12:
            raise ValueError('작업은 1~12개로 나눠 주세요')
        with self.edit(rid) as d:
            if d['revision'] != revision or d['status'] not in OPEN:
                raise ValueError('이전 요청 버전입니다')
            if any(t['phase'] == 'work' and t['revision'] == revision for t in d['tasks']):
                raise ValueError('이 회차의 계획은 이미 등록되었습니다')
            roster = yaml.safe_load((self.root / 'roster.yaml').read_text())
            workers = {w['id'] for w in roster['workers']} | {a['id'] for a in d['agents'] if a['state'] == 'active'}
            ids = [t.get('id') for t in tasks]
            if any(not isinstance(i, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,35}', i) for i in ids) or len(set(ids)) != len(ids):
                raise ValueError('작업 ID는 중복 없는 영문 소문자·숫자·하이픈입니다')
            normalized = []
            for t in tasks:
                if t.get('worker') not in workers:
                    raise ValueError('없는 담당자입니다. 필요한 역할을 먼저 생성하세요')
                deps = t.get('depends_on', [])
                if not isinstance(deps, list) or set(deps) - set(ids) or t['id'] in deps:
                    raise ValueError('잘못된 작업 의존 관계')
                caps = t.get('capabilities', [])
                if not isinstance(caps, list) or set(caps) - CAPABILITIES.keys():
                    raise ValueError('제공되지 않은 실행 기능')
                if d['scope'] == 'read' and set(caps) - {'inventory', 'siem'}:
                    raise ValueError('조회 전용 요청에 파일 작성·변경 작업을 배정할 수 없습니다')
                project = next((a for a in d['agents'] if a['id'] == t['worker']), None)
                if project and set(caps) - set(project['capabilities']):
                    raise ValueError('담당 프로젝트 역할에 없는 실행 기능입니다')
                import authorization
                authorization.task_profile(self.root, d, dict(worker=t['worker'], capabilities=caps, phase='work'))
                normalized.append(dict(id=f'v{revision}-{t["id"]}', revision=revision, phase='work',
                    title=text(t.get('title'), 180), instructions=text(t.get('instructions'), 6000),
                    worker=t['worker'], capabilities=caps,
                    depends_on=[f'v{revision}-{dep}' for dep in deps], status='queued'))
            remaining = {t['id']: set(t['depends_on']) for t in normalized}
            while remaining:
                ready = [tid for tid, deps in remaining.items() if not deps]
                if not ready:
                    raise ValueError('작업 의존 관계가 순환합니다')
                remaining = {tid: deps - set(ready) for tid, deps in remaining.items() if tid not in ready}
            if len(normalized) + d['sessions'] + 1 > d['budget']:
                raise ValueError('작업 수와 최종 검토가 세션 예산을 초과합니다')
            d['tasks'].extend(normalized)
            d['events'].append(dict(at=time.time(), kind='plan_created', detail=f'{len(normalized)}개 작업'))
        return normalized

    def detail(self, rid):
        data = self.get(rid)
        # 최종 상태 보고와 사용자에게 전달한 답변 본문을 함께 제공한다.
        # 이전 실행도 당시 보존된 본문을 그대로 읽어 복원할 수 있다.
        for task in data['tasks']:
            outcome = task.get('outcome')
            name = Path(task.get('evidence') or '').name
            if not isinstance(outcome, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', name):
                continue
            path = self.root / 'evidence' / name / 'session-result.json'
            try:
                body = json.loads(path.read_text()).get('body', '')
                try:
                    structured = json.loads(body)
                    body = structured.get('summary', body) if isinstance(structured, dict) else body
                except (ValueError, TypeError):
                    pass
                if isinstance(body, str) and body.strip():
                    outcome['answer'] = body
                    for message in data['messages']:
                        if message.get('task') == task['id'] and message.get('role') == 'agent':
                            message['text'] = body
            except (OSError, ValueError, TypeError):
                pass
        data['artifacts'] = self.artifacts(rid)
        return data

    def context(self, rid, task=None, authorization=None):
        d = self.detail(rid)
        if task and authorization:
            # 검토자만 요청 전체 결과를 통합한다. 실행자는 자기 기록과 명시적 선행 결과만 받는다.
            visible = {task['id'], *task.get('depends_on', [])}
            visible.update(t['id'] for t in d['tasks'] if t['worker'] == task['worker'])
            if authorization['duty'] != 'review':
                d['messages'] = [m for m in d['messages'] if m['role'] == 'user' or m.get('task') in visible]
                d['tasks'] = [t if t['id'] in visible else {k: t[k] for k in ('id','worker','phase','title','status','depends_on')}
                              for t in d['tasks']]
                d['changes'] = []
                d['events'] = []
                d['result'] = None
                d['permission_requests'] = [p for p in d.get('permission_requests', []) if p['worker'] == task['worker']]
                if not set(task['capabilities']) & {'workspace','website','waf'}:
                    d['artifacts'] = []
        sent = next(m['at'] for m in reversed(d['messages']) if m['role'] == 'user')
        at = datetime.datetime.fromtimestamp(sent, datetime.timezone.utc)
        turn = dict(sent_at=at.isoformat(), timezone=d.get('timezone', 'UTC'),
                    local_sent_at=at.astimezone(ZoneInfo(d.get('timezone', 'UTC'))).isoformat(),
                    last_hour={'start': (at - datetime.timedelta(hours=1)).isoformat(), 'end': at.isoformat()})
        return dict(request=d, workers=self.workers(),
                    turn=turn,
                    capabilities=CAPABILITIES,
                    limitations=['website: 정적 HTML/CSS/JS 사이트만 배포 지원; 결제·DB·서버 애플리케이션은 별도 실행 환경 필요',
                                 'waf: 지정된 요청 값의 리터럴 차단 규칙. 범용 SQLi 탐지 보장 아님',
                                 'SIEM 검색은 보관된 경보 범위이며 원시 네트워크 트래픽 전체가 아님'])

    def artifacts(self, rid):
        directory = self.directory(rid) / 'workspace'
        return [dict(path=str(p.relative_to(directory)), size=p.stat().st_size)
                for p in sorted(directory.rglob('*')) if p.is_file() and not p.is_symlink()][:300]


def poll(root, db, enqueue):
    """Called without model usage, including when facility collection is unavailable."""
    store = Store(root)
    for row in store.list(limit=None):
        rid = row['id']
        if row['status'] == 'applying':
            from request_changes import apply_pending
            apply_pending(root, rid)
        with store.edit(rid) as d:
            for task in d['tasks']:
                job = db.execute('SELECT * FROM jobs WHERE id=?', (task.get('job_id', ''),)).fetchone()
                if task['status'] in ('cancelled', 'superseded'):
                    if job and job['status'] in JOB_PENDING - {'running'}:
                        db.execute("UPDATE jobs SET status=?,updated=? WHERE id=?", (task['status'], time.time(), job['id']))
                        db.commit()
                    continue
                # 이전 CLI가 최종 도구 보고를 남긴 뒤 JSON 포장에 실패한 경우,
                # 저장된 구독 세션·도구 증거로 복구한다. 모델은 다시 호출하지 않는다.
                if job and job['status'] == 'failed' and task['revision'] == d['revision'] and d['status'] in OPEN | {'blocked'}:
                    failed_result = json.loads(job['result'] or '{}')
                    evidence = Path(job['evidence']) if job['evidence'] else None
                    if failed_result.get('type') == 'JSONDecodeError' and evidence and (evidence / 'request-outcome.json').is_file() and (evidence / 'session-result.json').is_file():
                        outcome = json.loads((evidence / 'request-outcome.json').read_text())
                        receipts = [json.loads(line) for line in (evidence / 'tools.jsonl').read_text().splitlines()]
                        observed = task['phase'] == 'review' or any(r['tool'] in ('request_plan', 'inventory_query', 'siem_search', 'workspace_read', 'workspace_write', 'website_validate', 'website_prepare', 'waf_prepare') and r.get('result', {}).get('status') not in ('denied', 'approval_required') for r in receipts)
                        if outcome.get('status') == 'completed' and not observed:
                            outcome.update(status='blocked', summary='실행 근거가 없어 완료 판정을 보류했습니다.')
                        if outcome.get('status') in ('completed', 'waiting_input', 'blocked') and any(r['tool'] == 'request_finish' and r.get('result', {}).get('recorded') for r in receipts):
                            result = json.loads((evidence / 'session-result.json').read_text())
                            result.update(status='completed', request_outcome=outcome, recovered_completion=True,
                                          verification={'tool_calls': len(receipts), 'observed_live_evidence': observed})
                            write_json(evidence / 'result.json', result)
                            db.execute("UPDATE jobs SET status='completed',result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), job['id']))
                            db.commit()
                            job = db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
                            task['status'] = 'running'
                            d['status'] = 'running'
                            d['result'] = None
                            d['events'].append(dict(at=time.time(), kind='result_recovered', task=task['id'], detail='최종 도구 보고를 사용하여 재호출 없이 결과 복구'))
                if task['status'] != 'running' or not job or job['status'] in JOB_PENDING:
                    continue
                result = json.loads(job['result'] or '{}')
                task['evidence'] = job['evidence']
                task['outcome'] = result.get('request_outcome', {'status': 'blocked', 'summary': result.get('error', '실행 결과를 확인하지 못했습니다')})
                task['verification'] = result.get('verification', {})
                task['status'] = task['outcome']['status']
                d['messages'].append(dict(role='agent', worker=task['worker'], task=task['id'],
                                          text=task['outcome'].get('summary', ''), question=task['outcome'].get('question', ''),
                                          verification=task['verification'], at=time.time()))
            if d['status'] not in OPEN:
                continue
            current = [t for t in d['tasks'] if t['revision'] == d['revision'] and t['status'] != 'superseded']
            running = [t for t in current if t['status'] == 'running']
            failed = [t for t in current if t['status'] in ('blocked', 'waiting_input')]
            if failed and not running:
                d['status'] = 'waiting_input' if any(t['status'] == 'waiting_input' for t in failed) else 'blocked'
                d['result'] = failed[-1].get('outcome')
                continue
            work = [t for t in current if t['phase'] == 'work']
            review = [t for t in current if t['phase'] == 'review']
            if work and all(t['status'] == 'completed' for t in work) and not review and not running:
                store._coordinator(d, 'review')
                current = [t for t in d['tasks'] if t['revision'] == d['revision'] and t['status'] != 'superseded']
            if current and all(t['status'] == 'completed' for t in current):
                pending_changes = any(c['status'] == 'proposed' for c in d['changes'])
                d['status'] = 'waiting_approval' if pending_changes else 'completed'
                d['result'] = current[-1].get('outcome')
                if not pending_changes:
                    for agent in d['agents']:
                        agent['state'] = 'archived'
                continue
            completed = {t['id'] for t in current if t['status'] == 'completed'}
            for task in current:
                if task['status'] != 'queued' or not set(task['depends_on']) <= completed:
                    continue
                if task['phase'] == 'work' and any(t['phase'] == 'plan' and t['status'] != 'completed' for t in current):
                    continue
                if d['sessions'] >= d['budget']:
                    if not running:
                        d['status'] = 'blocked'
                        d['result'] = dict(status='blocked', reason='budget', summary='요청 세션 예산을 모두 사용했습니다. 예산을 늘린 뒤 이어갈 수 있습니다.')
                    break
                jid = f'request:{rid}:{task["id"]}'
                payload = dict(request_id=rid, task_id=task['id'], revision=d['revision'])
                enqueue(db, jid, task['worker'], 'user_request', payload)
                d['sessions'] += 1
                task.update(status='running', job_id=jid)
                d['status'] = 'running'


OUTCOME_SCHEMA = {'type': 'object', 'properties': {
    'status': {'type': 'string', 'enum': ['completed', 'waiting_input', 'blocked']},
    'summary': {'type': 'string'}, 'question': {'type': 'string'},
    'artifacts': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['status', 'summary', 'question', 'artifacts'], 'additionalProperties': False}


def execute(root, job):
    import session_cli
    from request_runtime import compile_request
    root = Path(root)
    store = Store(root)
    payload = json.loads(job['payload'])
    rid, tid, revision = payload['request_id'], payload['task_id'], payload['revision']
    evidence = root / 'evidence' / ('loop-' + str(time.time_ns()) + '-' + job['worker'])
    evidence.mkdir(parents=True)
    try:
        d, task = store.check(rid, tid, revision)
        dest, manifest = compile_request(root, d, task, evidence)
        write_json(evidence / 'job.json', {**job, 'payload': payload, 'observation': payload})
        instructions = ('배정된 사용자 업무를 처리하세요. 먼저 request_context로 대화와 현재 상태를 확인하고 '
            '관련 SKILL.md를 skill_read로 읽으세요. 여러 작업이 필요한 경우 기존 담당자를 우선 활용하고, '
            '부족한 역할만 request_agent_create로 구성한 다음 request_plan에 작업과 선후 관계를 등록하세요. '
            '계획을 저장했다면 본인의 계획 단계만 completed로 마치세요. 러너가 담당자를 실행하고 결과를 검토 단계로 전달합니다. '
            '담당 작업의 범위와 예산을 지키고 검증 자료를 남기세요. 준비된 변경안은 적용 대기이며 배포 완료가 아닙니다. '
            '종료 전에 반드시 request_finish를 호출하여 status(completed/waiting_input/blocked), summary(결과), question(필요한 질문 또는 빈 문자열), artifacts(실제 산출물 경로 목록)를 기록하세요. '
            '부족한 도구·로그·검증 결과를 추측하지 마세요. 필요한 정보만 질문하고 지정된 JSON 형식으로 한국어로 답하세요.\n'
            + json.dumps(dict(task=task, request_id=rid, request_revision=revision), ensure_ascii=False))
        if task['phase'] == 'conversation':
            instructions = ('선택된 근무자로서 사용자와 직접 대화하세요. request_context로 이전 대화와 turn의 요청 시각을 확인하세요. '
                '이번 메시지에 답하는 한 번의 세션입니다. 인사·설명·이전 결과에 대한 질문은 간결하게 답하고, '
                '조사가 요청되면 관련 skill_read와 실제 조회 도구를 사용하세요. 모호한 증상은 조회 가능한 범위부터 확인하고 필요한 질문을 하세요. '
                '상대 시간은 turn.sent_at 기준이며 최근 1시간은 turn.last_hour를 그대로 사용하세요. '
                '기존 담당자의 역할과 조회 권한을 지키세요. 업무 배정이나 별도 검토 세션은 생성하지 마세요. '
                'request_finish에 status, summary, question, artifacts와 response_kind를 기록하세요. '
                'response_kind=reply는 인사·설명·기존 자료에 대한 답변, investigation은 이번에 실제 조사한 결과입니다. '
                '조회 없이 새로운 점검·분석을 완료했다고 말하지 마세요. 조사 도구가 실패했으면 blocked 또는 waiting_input입니다. '
                '결과·근거·한계를 한국어로 답하고 세션을 끝내세요.\n'
                + json.dumps(dict(task=task, request_id=rid), ensure_ascii=False))
        schema = OUTCOME_SCHEMA
        if task['phase'] == 'conversation':
            schema = {**schema, 'properties': {**schema['properties'],
                'response_kind': {'type': 'string', 'enum': ['reply', 'investigation']}},
                'required': [*schema['required'], 'response_kind']}
        result = session_cli.run(manifest['worker']['runtime'], manifest['model']['name'], instructions,
            timeout=300, schema=schema, harness=dest, evidence_dir=evidence)
        outcome_file = evidence / 'request-outcome.json'
        try:
            outcome = json.loads(outcome_file.read_text()) if outcome_file.is_file() else json.loads(result['body'])
        except ValueError:
            raise ValueError('조회 자료와 답변은 실행 증거에 보존했으나 종료 상태가 기록되지 않았습니다. request_finish로 결과를 보고해야 합니다.')
        if not isinstance(outcome, dict) or outcome.get('status') not in ('completed', 'waiting_input', 'blocked') or not outcome.get('summary'):
            raise ValueError('작업 결과 형식이 잘못됐습니다')
        receipts = [json.loads(line) for line in (evidence / 'tools.jsonl').read_text().splitlines()] if (evidence / 'tools.jsonl').exists() else []
        meaningful = {'inventory_query', 'siem_search', 'workspace_read', 'workspace_write', 'website_validate', 'website_prepare', 'waf_prepare', 'request_plan',
                      'env_read', 'log_read', 'infrastructure_read', 'firewall_read', 'agent_activity', 'disk_usage',
                      'xoc_read', 'compliance_read', 'lab_read', 'lab_propose', 'lab_evaluate'}
        if task['phase'] == 'review':
            meaningful.add('request_context')
        def observed_receipt(r):
            res = r.get('result', {})
            if r['tool'] not in meaningful or res.get('status') in ('denied', 'approval_required', 'failed', 'unavailable'):
                return False
            if r['tool'] == 'disk_usage':
                return res.get('measured_targets', 0) > 0
            if r['tool'] in ('infrastructure_read', 'firewall_read'):
                return any(row.get('returncode') == 0 and row.get('output') for row in res.get('records', []))
            return True
        observed = any(observed_receipt(r) for r in receipts)
        pending = [p for p in store.get(rid).get('permission_requests', []) if p['task_id'] == tid and p['revision'] == revision and p['status'] in ('pending', 'deferred')]
        if pending:
            outcome.update(status='waiting_input', question='대화의 권한 요청에서 이번만 허용·항상 허용·요청 보류 중 선택해 주세요.')
        conversational_reply = task['phase'] == 'conversation' and outcome.get('response_kind') == 'reply'
        if outcome['status'] == 'completed' and not observed and not conversational_reply:
            outcome.update(status='blocked', summary='실행 근거가 없어 완료 판정을 보류했습니다. ' + outcome['summary'])
        store.check(rid, tid, revision)
        result['verification'] = dict(tool_calls=len(receipts), observed_live_evidence=observed,
            tools=list(dict.fromkeys(r['tool'] for r in receipts if observed_receipt(r))),
            queries=[r['arguments'] for r in receipts if r['tool'] == 'siem_search'])
        result['request_outcome'] = outcome
        write_json(evidence / 'result.json', result)
        return dict(status='completed', evidence=str(evidence), session_id=result['session_id'],
                    request_outcome=outcome, verification=result['verification'])
    except Exception as exc:
        error = dict(status='failed', runtime=locals().get('manifest', {}).get('worker', {}).get('runtime', ''), error=str(exc)[:300], type=type(exc).__name__, evidence=str(evidence))
        write_json(evidence / 'failure.json', error)
        return error
