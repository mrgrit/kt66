"""Render project workers into native role and skill files without changing the roster."""
import copy
import hashlib
import json
from pathlib import Path

import yaml

from work_requests import write_json

SKILLS = {'inventory': 'inventory-report', 'siem': 'siem-period-analysis',
          'workspace': 'project-development', 'website': 'project-development', 'waf': 'waf-change'}


def compile_request(root, request, task, evidence):
    import harness_compiler
    root = Path(root)
    project_agent = next((a for a in request['agents'] if a['id'] == task['worker']), None)
    template = project_agent['template'] if project_agent else task['worker']
    base_dest, base = harness_compiler.compile_worker(template, root)
    manifest = copy.deepcopy(base)
    import authorization
    manifest['authorization'] = authorization.task_profile(root, request, task)
    if project_agent:
        manifest['worker'].update(id=project_agent['id'], name=project_agent['name'],
                                  runtime=project_agent['runtime'], model=project_agent['model'])
        manifest['persona'] = project_agent['mission']
    manifest['worker']['loops'] = []
    manifest['loops'] = []
    permissions = manifest['policy']['constrain']['permission']
    # Operational changes for a user request go through concrete change artifacts,
    # never through the routine simulator remediation path.
    for name in permissions:
        if name not in ('env_read', 'metrics_read', 'log_read', 'cmdb_read', 'xoc_read', 'research_lab'):
            permissions[name] = 'deny'
    permissions['user_request'] = base['policy']['constrain']['permission'].get('user_request', 'allow')
    manifest['policy']['constrain']['autonomy'] = 'L1'
    caps = list(task['capabilities'])
    if project_agent:
        caps = [c for c in caps if c in project_agent['capabilities']]
    if request['scope'] == 'read':
        caps = [c for c in caps if c in ('inventory', 'siem')]
    manifest['request'] = dict(id=request['id'], task_id=task['id'], revision=request['revision'],
        mode=request.get('mode', 'request'), worker=request.get('worker'), timezone=request.get('timezone', 'UTC'),
        phase=task['phase'], capabilities=caps, scope=request['scope'], max_tool_calls=60,
        source_permissions=base['policy']['constrain']['permission'])
    manifest['request'].update(depends_on=task.get('depends_on', []),
        resumed_from_task_id=task.get('resumed_from_task_id'),
        parent_run_id=task.get('parent_run_id'), parent_call_id=task.get('parent_call_id'))
    role_skills = manifest.get('role_skills', {})
    skills = sorted(set(role_skills) | {SKILLS[c] for c in caps} | ({'request-coordination'} if task['phase'] in ('plan', 'review') else set()))
    if authorization.allowed(manifest, 'disk_usage') and permissions.get('metrics_read') != 'deny':
        skills.append('system-diagnostics')
    library = root / 'native'
    skill_sources = {}
    for name in skills:
        p = library / '.agents' / 'skills' / name / 'SKILL.md'
        if not p.is_file():
            raise ValueError('필수 스킬 파일이 없습니다: ' + name)
        skill_sources[name] = (base_dest / '.agents' / 'skills' / name / 'SKILL.md').read_text() if name in role_skills else p.read_text()
    manifest['request']['skills'] = skills
    common = (library / 'AGENTS.md').read_text()
    role_source = (library / '.claude' / 'agents' / 'kt66-request-worker.md').read_text()
    version = hashlib.sha256(json.dumps(dict(manifest=manifest, skills=skill_sources, common=common, role=role_source), sort_keys=True).encode()).hexdigest()
    manifest['version'] = version
    dest = Path(evidence) / 'harness'
    dest.mkdir(parents=True, exist_ok=True)
    role = (f'# {manifest["worker"]["name"]}\n\n' + manifest['persona'] + '\n\n'
            + '## 이번에 배정된 작업\n' + task['instructions'] + '\n\n'
            + '## 현재 실행 범위와 정책\n' + json.dumps(dict(
                request=manifest['request'], company=manifest['company'],
                policy=manifest['policy'], authorization=manifest['authorization'], task=task), ensure_ascii=False, indent=2))
    if role_skills:
        role += '\n\n## 배정된 역할 스킬\n실제 해당 업무를 시작할 때 skill_read로 본문을 한 번 읽고 적용하세요. 인사·설명에는 불필요한 스킬 조회를 생략하세요.\n'
        role += json.dumps(role_skills, ensure_ascii=False, indent=2)
    (dest / 'AGENTS.md').write_text(role_source.split('---', 2)[2] + '\n' + common + '\n\n' + role)
    (dest / 'CLAUDE.md').write_text('@AGENTS.md\n')
    # The launch adapter reads these native files, not a second persona format.
    name = 'kt66-request-worker'
    native_dir = dest / '.claude' / 'agents'
    native_dir.mkdir(parents=True)
    _, front, native_body = role_source.split('---', 2)
    fm = yaml.safe_load(front)
    fm.update(name=name, model=manifest['model']['name'] if manifest['worker']['runtime'] == 'claude' else 'inherit',
              tools=['mcp__kt66__*'], skills=skills, maxTurns=35)
    (native_dir / (name + '.md')).write_text('---\n' + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + '---\n' + native_body + '\n' + common + '\n' + role)
    codex_dir = dest / '.codex' / 'agents'
    codex_dir.mkdir(parents=True)
    (codex_dir / (name + '.toml')).write_text(
        'name = "kt66-request-worker"\ndescription = "배정된 사용자 업무를 처리하는 프로젝트 근무자"\n'
        'sandbox_mode = "read-only"\ndeveloper_instructions = ' + json.dumps(native_body + '\n' + common + '\n\n' + role, ensure_ascii=False) + '\n')
    for skill, body in skill_sources.items():
        for prefix in ('.agents', '.claude'):
            p = dest / prefix / 'skills' / skill / 'SKILL.md'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
    import harness_tools
    manifest['available_tools'] = authorization.visible(manifest, harness_tools.TOOLS)
    manifest['native'] = dict(agent=name, skills=skills, source_hashes={
        'AGENTS.md': hashlib.sha256(common.encode()).hexdigest(),
        '.claude/agents/kt66-request-worker.md': hashlib.sha256(role_source.encode()).hexdigest(),
        **{'.agents/skills/' + k + '/SKILL.md': hashlib.sha256(v.encode()).hexdigest() for k,v in skill_sources.items()}}, role_files=[
        '.claude/agents/' + name + '.md', '.codex/agents/' + name + '.toml'])
    (dest / 'HARNESS.md').write_text(common + '\n\n' + role)
    write_json(dest / 'manifest.json', manifest)
    return dest, manifest
