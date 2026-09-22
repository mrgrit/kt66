"""직무 상한: 모델·업무 배정·대화 승인은 이 경계를 넓힐 수 없다."""
import copy
import hashlib
import json
from pathlib import Path

import yaml

COMMON = {'activity_note', 'harness_identity', 'work_status', 'cycle_state', 'ticket_create',
          'request_context', 'skill_read', 'request_finish'}
REQUEST = {'request_context', 'skill_read', 'request_finish', 'request_plan', 'request_agent_create',
           'inventory_query', 'siem_search', 'workspace_list', 'workspace_read', 'workspace_write',
           'website_validate', 'website_prepare', 'waf_prepare'}
CAPS = {'inventory_query': 'inventory', 'siem_search': 'siem', 'workspace_write': 'workspace',
        'website_validate': 'website', 'website_prepare': 'website', 'waf_prepare': 'waf'}
EXECUTION = {'env_read', 'infrastructure_read', 'disk_usage', 'firewall_read', 'log_read',
             'simulator_control', 'inventory_query', 'siem_search', 'workspace_write',
             'website_validate', 'website_prepare', 'waf_prepare'}
REFERRALS = {'disk_usage': 'systems-engineer', 'firewall_read': 'network-engineer',
             'waf_prepare': 'network-engineer', 'siem_search': 'soc-analyst', 'log_read': 'soc-analyst',
             'website_prepare': 'application-developer', 'workspace_write': 'application-developer',
             'env_read': 'facility-engineer', 'approve_request': 'ops-lead'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def profile(config, worker):
    role = worker.get('security_role')
    p = copy.deepcopy(config.get('security', {}).get('roles', {}).get(role))
    if not isinstance(p, dict):
        raise ValueError('직무 정책이 없는 근무자는 실행할 수 없습니다: ' + worker['id'])
    tools = set(p.get('tools', []))
    duty = p.get('duty')
    if duty not in ('operate', 'develop', 'coordinate', 'review', 'audit'):
        raise ValueError('알 수 없는 직무 유형: ' + str(duty))
    if duty in ('coordinate', 'review', 'audit') and tools & EXECUTION:
        raise ValueError('분배·검토·감사 직무에는 운영/개발 실행 도구를 부여할 수 없습니다')
    if duty != 'review' and tools & {'approve_request', 'approval_inbox'}:
        raise ValueError('독립 검토 직무만 승인할 수 있습니다')
    if duty != 'coordinate' and tools & {'delegate_work', 'request_plan', 'request_agent_create'}:
        raise ValueError('업무 분배 직무만 작업과 임시 역할을 배정할 수 있습니다')
    if duty in ('coordinate', 'review', 'audit') and p.get('capabilities'):
        raise ValueError('분배·검토·감사 직무에는 실행 기능을 배정할 수 없습니다')
    if set(p.get('capabilities', [])) - set(CAPS.values()):
        raise ValueError('지원하지 않는 직무 실행 기능입니다')
    if p.get('temporary') and duty not in ('operate', 'develop'):
        raise ValueError('분배·승인·감사 직무는 임시 실행자로 복제할 수 없습니다')
    p.update(role=role, template=worker['id'])
    p['fingerprint'] = fingerprint(p)
    return p


def load(root, worker_id):
    root = Path(root)
    roster = yaml.safe_load((root/'roster.yaml').read_text())
    worker = next((w for w in roster['workers'] if w['id'] == worker_id), None)
    if worker is None:
        raise ValueError('등록된 직무의 근무자가 아닙니다')
    return profile(yaml.safe_load((root/'harness.yaml').read_text()), worker)


def validate(config, workers, known_tools):
    errors = []
    for worker in workers:
        try:
            p = profile(config, worker)
            if set(p['tools']) - set(known_tools):
                raise ValueError('직무에 구현되지 않은 도구가 있습니다')
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f'{worker.get("id")}: {error}')
    return errors


def task_profile(root, data, task):
    agent = next((a for a in data['agents'] if a['id'] == task['worker']), None)
    p = load(root, agent['template'] if agent else task['worker'])
    caps = set(task['capabilities'])
    if caps - set(p.get('capabilities', [])):
        raise ValueError('담당 직무 밖의 실행 기능은 배정할 수 없습니다')
    if agent and (not p.get('temporary') or caps - set(agent['capabilities'])):
        raise ValueError('임시 에이전트가 상속할 수 없는 직무/기능입니다')
    if task['phase'] == 'plan' and p['duty'] != 'coordinate':
        raise ValueError('업무 분배 직무가 필요합니다')
    if task['phase'] == 'review' and p['duty'] != 'review':
        raise ValueError('독립 검토 직무가 필요합니다')
    if task['phase'] == 'work' and p['duty'] in ('coordinate', 'review'):
        raise ValueError('분배자·검토자를 실행 작업에 배정할 수 없습니다')
    return p


def allowed(manifest, name):
    p = manifest.get('authorization', {})
    if not p or name not in COMMON | set(p.get('tools', [])):
        return False
    context = manifest.get('request')
    if name in REQUEST and not context:
        return False
    if context:
        if name in ('request_plan', 'request_agent_create') and context['phase'] != 'plan':
            return False
        if name == 'simulator_control' or name in ('approve_request', 'delegate_work'):
            return False
        if name in CAPS and CAPS[name] not in context.get('capabilities', []):
            return False
        if name in ('workspace_list', 'workspace_read') and not (
                p['duty'] == 'review' or set(context.get('capabilities', [])) & {'workspace', 'website', 'waf'}):
            return False
    return True


def visible(manifest, definitions):
    perms = manifest['policy']['constrain'].get('permission', {})
    return [name for name, _, _, perm in definitions if allowed(manifest, name)
            and (not perm or perms.get(perm, 'deny') != 'deny')
            and perms.get({'inventory_query': 'cmdb_read', 'siem_search': 'log_read'}.get(name), 'allow') != 'deny']


def matches(value, selectors):
    return isinstance(value, str) and any(s == '*' or value == s or
        (s.endswith('-*') and value.startswith(s[:-1])) for s in selectors)


def require(manifest, name, args):
    p = manifest.get('authorization', {})
    if not allowed(manifest, name):
        return dict(status='denied', reason='이 도구는 담당 직무의 권한 범위 밖입니다. 대화 승인으로 확대할 수 없습니다.',
                    code='role_boundary', recommended_worker=REFERRALS.get(name, 'service-desk'))
    if name == 'disk_usage' and args.get('target', 'all') != 'all' and not matches(args['target'], p.get('disk_targets', [])):
        return dict(status='denied', code='asset_boundary', reason='담당 저장 자산 범위 밖입니다.', recommended_worker='systems-engineer')
    return None


def directory(root):
    rows = yaml.safe_load((Path(root)/'roster.yaml').read_text())['workers']
    config = yaml.safe_load((Path(root)/'harness.yaml').read_text())
    return [{**{k: w.get(k) for k in ('id', 'name', 'floor', 'zone', 'assets', 'runtime')},
             'security': profile(config, w)} for w in rows]


def environment(state, events, p):
    """시설 전체 응답에서 허용한 계통만 모델에 전달한다. 알 수 없는 필드는 제외한다."""
    selectors = p.get('env_assets', [])
    result = {k: v for k, v in state.items() if k in {'ts', 'time_scale'} | set(p.get('env_sections', []))}
    result['alarms'] = [a for a in state.get('alarms', []) if matches(a.get('scope'), selectors)]
    result['faults'] = {k: [v for v in values if matches(v, selectors)]
                        for k, values in state.get('faults', {}).items() if isinstance(values, list)
                        and any(matches(v, selectors) for v in values)}
    assets = state.get('assets', {})
    result['assets'] = ({k: v for k, v in assets.items() if matches(k, selectors)} if isinstance(assets, dict)
                        else [a for a in assets if matches(a.get('id'), selectors)])
    rows = events.get('events', []) if isinstance(events, dict) else events
    result['events'] = [e for e in rows if matches(e.get('scope') or e.get('target'), selectors)]
    result['scope'] = dict(role=p['role'], sections=p.get('env_sections', []), assets=selectors)
    return result
