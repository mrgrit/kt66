"""모델의 완료 주장과 독립적으로 도구 영수증에서 확인 가능한 작업을 요약한다."""
OBSERVATIONS = {'network_probe', 'siem_search', 'log_read', 'firewall_read', 'infrastructure_read',
                'disk_usage', 'inventory_query', 'env_read', 'xoc_read', 'lab_read',
                'compliance_read', 'agent_activity'}
FAILED = {'denied', 'failed', 'unavailable', 'approval_required', 'approval_pending', 'blocked'}
REQUIRED_SKILLS = {'network_probe': 'network-diagnosis', 'siem_search': 'siem-period-analysis'}


def successful(row):
    result = row.get('result', {})
    if row.get('tool', '').startswith('error:') or not isinstance(result, dict) or result.get('status') in FAILED:
        return False
    if row.get('tool') in ('infrastructure_read', 'firewall_read'):
        return any(r.get('returncode') == 0 and r.get('output') for r in result.get('records', []))
    if row.get('tool') == 'network_probe':
        return any(r.get('status') in ('pass', 'fail') for r in result.get('checks', []))
    if row.get('tool') == 'disk_usage':
        return result.get('measured_targets', 0) > 0
    return True


def summarize(receipts):
    observations, skills, checks, errors = [], [], [], []
    for number, row in enumerate(receipts, 1):
        tool, result = row.get('tool', ''), row.get('result', {})
        result = result if isinstance(result, dict) else {}
        ref = 'tools.jsonl#L' + str(number)
        if not successful(row):
            errors.append({'tool': tool, 'status': result.get('status', 'error'), 'code': result.get('code'), 'reference': ref})
            continue
        if tool == 'skill_read' and result.get('sha256'):
            skills.append({'name': row.get('arguments', {}).get('name'), 'resource': result.get('resource', 'SKILL.md'),
                           'sha256': result['sha256'], 'reference': ref})
        if tool in OBSERVATIONS:
            observations.append({'tool': tool, 'reference': ref, 'snapshot': result.get('snapshot'),
                                 'status': result.get('status', 'observed')})
        if tool == 'network_probe':
            checks.append({'decision': result.get('decision'), 'scope': result.get('scope'),
                           'passed': sum(c.get('status') == 'pass' for c in result.get('checks', [])),
                           'failed': result.get('failed', []), 'unknown': result.get('unknown', []), 'reference': ref})
    loaded = {s['name'] for s in skills if s['resource'] == 'SKILL.md'}
    missing = sorted({REQUIRED_SKILLS[o['tool']] for o in observations if o['tool'] in REQUIRED_SKILLS} - loaded)
    waiting = any(e['status'] in ('approval_required', 'approval_pending') for e in errors)
    return {'state': 'observed' if observations else 'awaiting_permission' if waiting else 'failed' if errors else 'unverified',
            'observations': observations, 'skills': skills, 'checks': checks, 'errors': errors,
            'missing_skill_receipts': missing,
            'note': '도구 실행·자료 조회 근거입니다. 스킬 로드는 절차 준수 전체나 장애 해결을 보증하지 않습니다.'}
