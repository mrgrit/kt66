"""동일 직무를 공식 Claude Markdown/Codex TOML 실행 프로필로 렌더링한다."""
import json
from pathlib import Path
import yaml


def render(directory, manifest, instructions):
    directory = Path(directory)
    worker = manifest['worker']
    name = worker['id']
    description = 'KT66 ' + worker.get('name', name) + ' · MCP 직무 경계 안에서 배정된 작업을 수행한다'
    claude = directory / '.claude/agents' / (name + '.md')
    codex = directory / '.codex/agents' / (name + '.toml')
    for path in (claude, codex):
        path.parent.mkdir(parents=True, exist_ok=True)
    fm = {'name': name, 'description': description,
          'model': manifest['model']['name'] if worker['runtime'] == 'claude' else 'inherit',
          'tools': ['mcp__kt66__' + tool for tool in manifest['available_tools']]}
    # skills 필드의 시작 시 본문 주입 대신 MCP skill_read를 통해 필요할 때 읽는다.
    claude.write_text('---\n' + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + '---\n\n' + instructions)
    fields = {'name': name, 'description': description, 'sandbox_mode': 'read-only', 'developer_instructions': instructions}
    if worker['runtime'] == 'codex' and manifest['model']['name'] != 'default':
        fields['model'] = manifest['model']['name']
    if manifest['model'].get('reasoning_effort'):
        fields['model_reasoning_effort'] = manifest['model']['reasoning_effort']
    codex.write_text('\n'.join(key + ' = ' + json.dumps(value, ensure_ascii=False) for key, value in fields.items()) + '\n')
    return {'agent': name, 'skills': list(manifest.get('role_skills', {})),
            'role_files': [str(path.relative_to(directory)) for path in (claude, codex)],
            'source_role': 'native/.claude/agents/' + name + '.md'}
