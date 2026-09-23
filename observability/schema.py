"""에이전트 관제 필드 계약. v1 필드는 유지하고 v2 조사 필드를 추가한다."""
VERSION = 'kt66.siem.v2'
PROJECTION_VERSION = 5


def fields(keyword='', date='', long='', boolean='', text=''):
    result = {k: {'type': 'keyword', 'ignore_above': 2048} for k in keyword.split()}
    for kind, names in [('date', date), ('long', long), ('boolean', boolean), ('text', text)]:
        result.update({k: {'type': kind} for k in names.split()})
    return result


def group(properties, nested=False):
    return {'type': 'nested' if nested else 'object', 'dynamic': 'strict', 'properties': properties}


def mappings():
    props = fields(
        keyword='schema_version event_id kind name outcome worker role run_id trigger runtime model zone axes '
                'permission_mode permission_name autonomy boundary status rule domain evidence_ref evidence_sha256 '
                'targets approval_refs assertion time_basis confidence reviewed_by resource_paths resource_operations',
        date='@timestamp ingested_at reviewed_at', long='tokens_total risk likelihood impact exposure',
        boolean='usage_known', text='title summary')
    props.update({k: {'type': 'text', 'index': False} for k in ('body', 'review_reason')})
    props.update({
        'correlation': group(fields(keyword='trace_id trace_basis request_id task_id job_id session_id parent_run_id '
                                   'parent_call_id retry_of_run_id resumed_from_task_id depends_on_task_ids finding_id delegation_id',
                                   long='request_revision attempt')),
        'execution': group(fields(date='started_at ended_at requested_at', long='duration_ms',
                                 keyword='timing_basis phase trigger_source trigger_event_id', text='trigger_reason retry_reason')),
        'config': group(fields(keyword='harness_version policy_sha256 persona_sha256 instructions_sha256 '
                              'native_sources_sha256 implementation_sha256 assigned_skills reasoning_effort')),
        'tool': group(fields(keyword='call_id name result_status skill_name skill_sha256 argument_names arguments_sha256',
                            date='started_at ended_at', long='duration_ms')),
        'resource': group(fields(keyword='type id operation purpose'), nested=True),
        'access_policy': group(fields(keyword='required_permission configured_mode effective_decision role boundary autonomy')),
        'approval': group(fields(keyword='request_id grant_id decision permission actor boundary',
                                date='decided_at', boolean='consumed'), nested=True),
        'approval_decisions': {'type': 'keyword'},
        'explanation': group(fields(keyword='stage evidence_refs', text='summary steps rework_cause')),
        'quality': group(fields(keyword='task_status response_kind verification_basis artifacts',
                               boolean='observed_live_evidence rework_reported', long='tool_calls')),
        'usage': group(fields(keyword='measurement_scope input_semantics cost_status',
                             long='input_tokens output_tokens cache_read_tokens cache_creation_tokens total_tokens')),
        'error': group(fields(keyword='code type', text='message')),
        'delegation': group(fields(keyword='id from_worker to_worker parent_run_id parent_call_id')),
        'request': group(fields(keyword='mode scope status', text='title')),
        'actor': group(fields(keyword='id type')),
        'subject': group(fields(keyword='worker run_id')),
        'detection': group(fields(keyword='rule_id rule_version response source', long='risk likelihood impact')),
        'evidence_items': group(fields(keyword='ref sha256'), nested=True),
        'coverage': group(fields(keyword='missing', boolean='resource_truncated approval_truncated')),
    })
    return {'dynamic': 'strict', 'properties': props}
