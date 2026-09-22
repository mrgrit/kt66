"""대화의 도구별 승인. 한 번 허용은 정확한 호출, 항상 허용은 근무자·도구 단위다."""
import contextlib
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import time
import uuid

from work_requests import Store, OPEN, write_json
import authorization


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Permissions:
    def __init__(self, root):
        self.store = Store(root)
        self.path = Path(root)/'tickets/tool-permissions.json'

    @contextlib.contextmanager
    def edit(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {'grants': [], 'events': []}
            before = copy.deepcopy(data)
            yield data
            if before != data:
                write_json(self.path, data)

    def list(self):
        with self.edit() as data:
            result = []
            for g in data['grants']:
                if not g['active']:
                    continue
                try:
                    current = authorization.load(self.store.root,g.get('template',g['worker']))
                    valid = g.get('boundary') == current['fingerprint']
                except (ValueError,OSError):
                    valid = False
                result.append({**g,'policy_current':valid})
            return result

    def revoke(self, gid):
        with self.edit() as data:
            grant = next((g for g in data['grants'] if g['id'] == gid and g['active']), None)
            if not grant:
                raise ValueError('현재 허용된 권한을 찾을 수 없습니다')
            grant.update(active=False, revoked_at=time.time())
            data['events'].append(dict(at=time.time(), actor='instructor', kind='revoked', grant=gid))
        return {'revoked': gid}

    def check(self, broker, tool, arguments, permission):
        """상위 정책의 ask일 때만 호출한다. deny는 이 경로로 들어오지 않는다."""
        context = broker.m.get('request')
        denied = authorization.require(broker.m,tool,arguments)
        if denied:
            return denied
        if not context:
            return {'status': 'approval_required', 'permission': permission}
        defaults = {'disk_usage': {'target': 'all', 'threshold_pct': 80},
                    'inventory_query': {'kind': 'all'}, 'siem_search': {'cursor': '', 'limit': 100},
                    'log_read': {'limit': 30}}
        arguments = {**defaults.get(tool, {}), **arguments}
        boundary = broker.m['authorization']['fingerprint']
        fingerprint = digest(dict(worker=broker.worker, tool=tool, arguments=arguments, permission=permission, boundary=boundary))
        with self.edit() as grants:
            grant = next((g for g in grants['grants'] if g['active'] and g['worker'] == broker.worker
                          and g['tool'] == tool and g['permission'] == permission and g.get('boundary') == boundary), None)
            if grant:
                broker._grant_checks.append(dict(grant=grant['id'], decision='always', permission=permission))
                broker.access(self.path, 'read')
                return None
        with self.store.edit(context['id']) as data:
            if data['revision'] != context['revision'] or data['status'] not in OPEN:
                raise ValueError('현재 회차가 종료되어 권한을 요청할 수 없습니다')
            requests = data.setdefault('permission_requests', [])
            row = next((p for p in requests if p['task_id'] == context['task_id']
                        and p['revision'] == context['revision'] and p['fingerprint'] == fingerprint
                        and p['status'] in ('pending', 'allowed_once')), None)
            if row and row['status'] == 'allowed_once':
                broker._grant_checks.append(dict(request=row['id'], decision='once', permission=permission))
                return None
            if row is None:
                row = dict(id='permission-'+uuid.uuid4().hex[:16], worker=broker.worker,
                           boundary=boundary, role=broker.m['authorization']['role'],
                           tool=tool, permission=permission, arguments=arguments,
                           fingerprint=fingerprint, task_id=context['task_id'], revision=context['revision'],
                           status='pending', created=time.time())
                requests.append(row)
                data['events'].append(dict(at=time.time(), kind='permission_requested', permission_request=row['id'], tool=tool))
            return dict(status='approval_required', permission=permission, permission_request=row['id'],
                        instruction='사용자가 대화에서 이번만 허용·항상 허용·요청 보류를 선택합니다. 도구 재호출을 멈추고 request_finish에 waiting_input과 승인 안내 question을 기록하세요.')

    def consume(self, broker):
        """호출에 필요한 승인이 모두 모인 뒤 한 번 허용을 소진한다."""
        once = [g['request'] for g in broker._grant_checks if g['decision'] == 'once']
        if not once:
            return
        context = broker.m['request']
        with self.store.edit(context['id']) as data:
            if data['revision'] != context['revision'] or data['status'] not in OPEN:
                raise ValueError('이 작업의 승인이 더 이상 유효하지 않습니다')
            rows = [p for p in data.get('permission_requests', []) if p['id'] in once
                    and p['task_id'] == context['task_id'] and p['status'] == 'allowed_once']
            if len(rows) != len(once):
                raise ValueError('한 번 허용된 호출을 이미 사용했거나 승인이 변경되었습니다')
            for row in rows:
                row.update(status='consumed', consumed_at=time.time())
                data['events'].append(dict(at=time.time(), kind='permission_consumed', permission_request=row['id'], tool=row['tool']))

    def decide(self, rid, pid, decision):
        if decision not in ('once', 'always', 'defer'):
            raise ValueError('이번만 허용, 항상 허용, 요청 보류 중 선택하세요')
        with self.store.edit(rid) as data:
            row = next((p for p in data.get('permission_requests', []) if p['id'] == pid), None)
            if not row or row['revision'] != data['revision'] or row['status'] not in ('pending', 'deferred'):
                raise ValueError('현재 유효한 권한 요청이 아닙니다')
            if data['status'] in OPEN | {'applying', 'cancelled'}:
                raise ValueError('담당자의 답변 정리가 끝난 뒤 선택하세요. 중지된 업무에는 허용할 수 없습니다')
            task = next((t for t in data['tasks'] if t['id'] == row['task_id']), None)
            if not task or task['status'] not in ('waiting_input', 'blocked'):
                raise ValueError('승인을 기다리는 작업을 찾을 수 없습니다')
            row.update(decision=decision, decided_at=time.time(), actor='instructor')
            data['events'].append(dict(at=time.time(), kind='permission_decision', permission_request=pid,
                                       decision=decision, worker=row['worker'], tool=row['tool'], actor='instructor'))
            if decision == 'defer':
                row['status'] = 'deferred'
                return {'status': 'deferred'}
            p = authorization.task_profile(self.store.root,data,task)
            if row['worker'] != task['worker'] or row.get('boundary') != p['fingerprint']:
                raise ValueError('직무 정책이 변경되었습니다. 현재 담당자에게 다시 요청하세요')
            manifest = dict(authorization=p,request=dict(phase=task['phase'],capabilities=task['capabilities']))
            if authorization.require(manifest,row['tool'],row['arguments']):
                raise ValueError('직무 범위 밖의 도구는 이번만/항상 허용할 수 없습니다')
            # 오래된 요청으로 현재 deny를 승인하지 않는다. 전역·부서·팀의 deny도 포함한다.
            import harness_compiler
            _, current = harness_compiler.compile_worker(p['template'],self.store.root)
            if current['policy']['constrain']['permission'].get(row['permission'],'deny') == 'deny':
                raise ValueError('현재 정책에서 금지한 권한입니다')
            # 이전 답변·실행 증거는 보존하고 같은 회차의 해당 작업만 이어서 실행한다.
            resumed = {k: copy.deepcopy(task[k]) for k in ('phase', 'revision', 'title', 'worker', 'instructions', 'depends_on', 'capabilities')}
            resumed.update(id=task['id']+'-r'+uuid.uuid4().hex[:6], status='queued')
            resumed['instructions'] += '\n도구 사용 승인이 반영되었습니다. 이전 대화와 승인 기록을 확인하여 중단된 작업을 이어가세요.'
            task['status'] = 'superseded'
            data['tasks'].append(resumed)
            for other in data['tasks']:
                other['depends_on'] = [resumed['id'] if dep == task['id'] else dep for dep in other['depends_on']]
            for pending in data.get('permission_requests', []):
                if pending['task_id'] == task['id'] and pending['status'] in ('pending', 'deferred', 'allowed_once'):
                    pending['task_id'] = resumed['id']
            row['status'] = 'allowed_once' if decision == 'once' else 'allowed_always'
            if decision == 'always':
                with self.edit() as grants:
                    existing = next((g for g in grants['grants'] if g['active'] and g['worker'] == row['worker']
                                     and g['tool'] == row['tool'] and g['permission'] == row['permission']
                                     and g.get('boundary') == row['boundary']), None)
                    gid = existing['id'] if existing else 'grant-'+uuid.uuid4().hex[:16]
                    if not existing:
                        grants['grants'].append(dict(id=gid, active=True, worker=row['worker'], tool=row['tool'],
                            permission=row['permission'], boundary=row['boundary'], role=row['role'], template=p['template'],
                            scope='user_requests', created=time.time(), actor='instructor', source_request=rid))
                    grants['events'].append(dict(at=time.time(), actor='instructor', kind='allowed_always', grant=gid, request=rid))
            data.update(status='queued', result=None)
        return {'status': 'queued'}
