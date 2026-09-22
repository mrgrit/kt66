"""Request-scoped tools; no arbitrary command or host filesystem access."""
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import yaml

from work_requests import Store

S = {'type': 'string'}


def schema(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)


TOOLS = [
    ('request_finish', '최종 상태와 결과를 기록합니다. 종료 전 필수. 근무자 대화에서는 response_kind를 reply(설명·기존 자료 답변) 또는 investigation(이번 조사)로 지정하세요. 조사 완료는 실제 조회 근거가 필요합니다.', schema({'status': {'type': 'string', 'enum': ['completed', 'waiting_input', 'blocked']}, 'summary': S, 'question': S, 'response_kind': {'type': 'string', 'enum': ['reply', 'investigation']}, 'artifacts': {'type': 'array', 'items': S}}, ['status', 'summary', 'question', 'artifacts'])),
    ('request_context', 'Read the user conversation, assigned tasks, project workers, real capabilities and budget.', schema({})),
    ('skill_read', 'Load an available standard SKILL.md by name for this task.', schema({'name': S}, ['name'])),
    ('request_plan', 'Coordinator only: register a bounded dependency plan using existing or created worker IDs.',
     schema({'tasks': {'type': 'array', 'items': {'type': 'object', 'properties': {
         'id': S, 'title': S, 'instructions': S, 'worker': S,
         'depends_on': {'type': 'array', 'items': S}, 'capabilities': {'type': 'array', 'items': S}},
         'required': ['id', 'title', 'instructions', 'worker', 'depends_on', 'capabilities'], 'additionalProperties': False}}}, ['tasks'])),
    ('request_agent_create', 'Coordinator only: create a request-lifetime worker for a missing role from a registered worker template.',
     schema({'name': S, 'mission': S, 'template': S, 'capabilities': {'type': 'array', 'items': S}}, ['name', 'mission', 'template', 'capabilities'])),
    ('inventory_query', 'Read configured assets and actual Docker network interface IPs. kind is security or all.', schema({'kind': {'type': 'string', 'enum': ['security', 'all']}})),
    ('siem_search', 'Search retained Wazuh alerts within an offset-aware ISO time range. Follow next_cursor for all pages; incomplete coverage is explicit.',
     schema({'start': S, 'end': S, 'cursor': S, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, ['start', 'end'])),
    ('workspace_list', 'List this request workspace artifacts.', schema({})),
    ('workspace_read', 'Read a text artifact within this request workspace.', schema({'path': S}, ['path'])),
    ('workspace_write', 'Write a text artifact within this request workspace; no live service is modified.', schema({'path': S, 'content': S}, ['path', 'content'])),
    ('website_validate', 'Validate site/index.html, static file paths and JavaScript syntax. Does not claim a visual browser test.', schema({})),
    ('website_prepare', 'Freeze validated site files into a deployment proposal behind the existing WAF.', schema({'slug': S}, ['slug'])),
    ('waf_prepare', 'Build and test an exact literal payload blocking rule in an isolated copy of the WAF image.',
     schema({'payload': S, 'parameter': S}, ['payload', 'parameter'])),
]

REQUIRED = {'inventory_query': 'inventory', 'siem_search': 'siem',
            'workspace_write': 'workspace', 'website_validate': 'website',
            'website_prepare': 'website', 'waf_prepare': 'waf'}


def safe_file(directory, name):
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError('잘못된 파일 경로')
    rel = Path(name)
    if rel.is_absolute() or any(p.startswith('.') or not re.fullmatch(r'[\w@() -]+(?:\.[\w-]+)*', p) for p in rel.parts):
        raise ValueError('작업 공간 밖 또는 숨김 경로는 사용할 수 없습니다')
    directory = Path(directory).resolve()
    dest = directory / rel
    if not dest.resolve().is_relative_to(directory) or any(p.is_symlink() for p in [dest, *dest.parents] if p != directory.parent):
        raise ValueError('심볼릭 링크 경로는 사용할 수 없습니다')
    return dest


def inventory(root, kind='all'):
    if kind not in ('security', 'all'):
        raise ValueError('kind는 security 또는 all입니다')
    root = Path(root)
    path = root.parent / 'envsim' / 'assets.yaml'
    assets = yaml.safe_load(path.read_text())['it_assets']
    security = {'fw', 'ips', 'web', 'siem', 'indexer', 'dashboard'}
    if kind == 'security':
        assets = [a for a in assets if a['id'] in security]
    names = [a['container'] for a in assets if a.get('container')]
    observations, errors = {}, []
    if names:
        p = subprocess.run(['docker', 'inspect', '--format', '{{json .Name}} {{json .NetworkSettings.Networks}}', *names],
                           capture_output=True, text=True, timeout=15)
        for line in p.stdout.splitlines():
            name, _, networks = line.partition(' ')
            try:
                observations[json.loads(name).lstrip('/')] = [dict(network=n, ip=v.get('IPAddress'), ipv6=v.get('GlobalIPv6Address')) for n, v in json.loads(networks).items()]
            except (ValueError, TypeError):
                errors.append('컨테이너 네트워크 출력 해석 실패')
        if p.returncode:
            errors.append('일부 컨테이너의 실행 정보를 조회하지 못했습니다')
    return dict(at=time.time(), source='envsim/assets.yaml + docker inspect NetworkSettings.Networks',
                configured_ip_meaning='대장의 대표 IP이며 전체 인터페이스 목록이 아닙니다', connectivity_checked=False,
                errors=errors, items=[dict(id=a['id'], name=a.get('name'), zone=a.get('zone'),
                    configured_ip=a.get('ip'), container=a.get('container'),
                    observed_interfaces=observations.get(a.get('container')),
                    configured_ip_present=(a.get('ip') in [i['ip'] for i in observations[a['container']]]) if a.get('container') in observations else None,
                    remote=a.get('remote')) for a in assets])


def siem_search(root, start, end, cursor='', limit=100):
    def stamp(s):
        d = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            raise ValueError('시작·종료 시각에 시간대가 필요합니다')
        return d.timestamp()
    lo, hi = stamp(start), stamp(end)
    if not 0 < hi - lo <= 7 * 86400 or hi > time.time() + 60:
        raise ValueError('검색 범위는 과거의 최대 7일입니다')
    if type(limit) is not int or not 1 <= limit <= 200 or len(cursor) > 2000:
        raise ValueError('검색 페이지 범위를 확인하세요')
    query = dict(start=lo, end=hi, cursor=cursor, limit=limit)
    source = (Path(root) / 'siem_query.py').read_text()
    program = 'QUERY = ' + repr(query) + '\n' + source
    process = subprocess.run(['docker', 'exec', '-i', 'kt66-siem', '/var/ossec/framework/python/bin/python3', '-'],
                             input=program, capture_output=True, text=True, timeout=35)
    if process.returncode:
        raise ValueError('SIEM 보관 로그 조회에 실패했습니다. 실행 환경과 로그 접근을 확인하세요')
    data = json.loads(process.stdout)
    data['requested_range'] = {'start': start, 'end': end}
    return data


def call(broker, root, name, args):
    context = broker.m.get('request')
    if not context:
        raise ValueError('사용자 요청에 배정된 세션만 사용할 수 있습니다')
    store = Store(root)
    rid = context['id']
    data, task = store.check(rid, context['task_id'], context['revision'])
    cap = REQUIRED.get(name)
    if cap and cap not in context['capabilities']:
        raise ValueError('이 작업에 해당 실행 기능이 배정되지 않았습니다: ' + cap)
    if name == 'inventory_query' and broker.permissions.get('cmdb_read') == 'deny':
        raise ValueError('상위 정책에서 자산 조회를 금지했습니다')
    if name == 'waf_prepare' and context['source_permissions'].get('firewall_rule_change') == 'deny':
        raise ValueError('상위 정책에서 방화벽 규칙 변경을 금지했습니다')
    if name == 'siem_search' and broker.permissions.get('log_read') == 'deny':
        raise ValueError('상위 정책에서 로그 조회를 금지했습니다')
    workspace = store.directory(rid) / 'workspace'
    if name == 'request_finish':
        if task['phase'] == 'conversation' and args.get('response_kind') not in ('reply', 'investigation'):
            raise ValueError('대화 답변은 reply, 이번 조사 결과는 investigation으로 response_kind를 지정하세요')
        if args['status'] not in ('completed', 'waiting_input', 'blocked') or not args['summary'].strip():
            raise ValueError('최종 상태와 결과가 필요합니다')
        for name in args['artifacts']:
            if not safe_file(workspace, name).is_file():
                raise ValueError('실제로 작성한 산출물 경로만 보고하세요')
        if args['status'] == 'waiting_input' and not args['question'].strip():
            raise ValueError('진행에 필요한 질문을 적으세요')
        from work_requests import write_json
        path = broker.session / 'request-outcome.json'
        write_json(path, args)
        broker.access(path, 'write')
        return {'recorded': True, 'status': args['status']}
    if name == 'request_context':
        return {**store.context(rid), 'available_tools': broker.m.get('available_tools', []),
                'current_time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'artifacts': store.artifacts(rid)}
    if name == 'skill_read':
        if args['name'] not in context['skills']:
            raise ValueError('이 작업에 배치된 스킬 이름을 사용하세요: ' + ', '.join(context['skills']))
        p = broker.path.parent / '.agents' / 'skills' / args['name'] / 'SKILL.md'
        broker.access(p, 'read')
        return {'name': args['name'], 'content': p.read_text(), 'source': str(p)}
    if name in ('request_agent_create', 'request_plan'):
        if task['phase'] != 'plan':
            raise ValueError('요청 계획 담당자만 역할·작업을 구성할 수 있습니다')
        if name == 'request_agent_create':
            return store.agent(rid, context['revision'], **args)
        return {'tasks': store.plan(rid, context['revision'], args['tasks'])}
    if name in ('inventory_query', 'siem_search'):
        result = inventory(root, **args) if name == 'inventory_query' else siem_search(root, **args)
        artifact = broker.session / (name + '-' + str(time.time_ns()) + '.json')
        artifact.write_text(json.dumps(result, ensure_ascii=False))
        broker.access(artifact, 'write')
        result['evidence'] = str(artifact)
        result['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return result
    if name == 'workspace_list':
        return {'files': store.artifacts(rid)}
    if name in ('workspace_read', 'workspace_write'):
        p = safe_file(workspace, args['path'])
        if name == 'workspace_read':
            if p.stat().st_size > 300000:
                raise ValueError('읽기 한도 300KB를 초과했습니다')
            broker.access(p, 'read')
            return {'path': args['path'], 'content': p.read_text()}
        if p.suffix.lower() not in ('.html', '.css', '.js', '.json', '.md', '.txt', '.csv', '.svg'):
            raise ValueError('지원되는 텍스트 산출물 형식을 사용하세요')
        if len(args['content'].encode()) > 256000:
            raise ValueError('파일당 256KB 한도입니다')
        files = store.artifacts(rid)
        if len(files) >= 200 and not p.exists() or sum(f['size'] for f in files) + len(args['content'].encode()) > 5000000:
            raise ValueError('요청 작업 공간 한도를 초과했습니다')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args['content'])
        broker.access(p, 'write')
        return {'path': args['path'], 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
    if name in ('website_validate', 'website_prepare', 'waf_prepare'):
        import request_changes
        return getattr(request_changes, name)(root, rid, **args)
    raise ValueError('알 수 없는 요청 도구')
