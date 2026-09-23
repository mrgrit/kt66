"""관제 질문에 필요한 값만 허용 목록으로 추출한다. 원문 전체를 펼치지 않는다."""
import json
from pathlib import PurePosixPath
from projection import digest, text, obj, stamp, iso, EVENTS
from schema import VERSION


def strings(values, limit=100):
    return [text(x, 2048) for x in values[:limit] if isinstance(x, str) and x] if isinstance(values, list) else []


def clean(values):
    return {k: v for k, v in values.items() if v is not None and v != '' and v != [] and v != {}}


def hashed(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True)) if value else ''


def number(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def date(value):
    parsed = stamp(value, None)
    return iso(parsed) if parsed else None


def run_name(value):
    return PurePosixPath(value).name if isinstance(value, str) and value else ''


def common(doc, meta):
    job, manifest, loaded = (obj(meta.get(k)) for k in ('job', 'manifest', 'loaded'))
    req = obj(manifest.get('request'))
    observation = obj(job.get('observation')) or obj(job.get('payload'))
    rid = text(req.get('id') or observation.get('request_id'))
    jid = str(job.get('job_id') or job.get('id') or '')
    trace = rid or text(observation.get('trace_id')) or jid or doc.get('run_id', '')
    correlation = clean(dict(trace_id=trace,
        trace_basis='request' if rid else 'delegation' if observation.get('trace_id') else 'job' if jid else 'run',
        request_id=rid, task_id=text(req.get('task_id') or observation.get('task_id')), job_id=jid,
        request_revision=number(req.get('revision', observation.get('revision'))),
        attempt=number(job.get('attempt', job.get('attempts') + 1 if type(job.get('attempts')) is int else None)),
        parent_run_id=text(observation.get('parent_run_id') or req.get('parent_run_id')),
        parent_call_id=text(observation.get('parent_call_id') or req.get('parent_call_id')),
        retry_of_run_id=run_name(job.get('retry_of')),
        resumed_from_task_id=text(req.get('resumed_from_task_id')), depends_on_task_ids=strings(req.get('depends_on'))))
    doc.update(schema_version=VERSION, correlation=correlation)
    doc['execution'] = clean(dict(requested_at=date(job.get('requested_at') or job.get('created')),
        phase=text(req.get('phase')), trigger_source=text(observation.get('source') or observation.get('from')),
        trigger_event_id=text(obj(observation.get('event')).get('id')),
        trigger_reason=text(observation.get('reason'), 1500),
        retry_reason=text(obj(job.get('retry_reason')).get('error') or obj(job.get('retry_reason')).get('status'), 1500)))
    native = obj(manifest.get('native'))
    doc['config'] = clean(dict(harness_version=text(manifest.get('version')),
        policy_sha256=hashed({'policy': manifest['policy'], 'authorization': manifest.get('authorization')}) if manifest.get('policy') else '',
        persona_sha256=digest(manifest['persona']) if isinstance(manifest.get('persona'), str) else '',
        instructions_sha256=text(loaded.get('instructions_sha256')),
        native_sources_sha256=hashed(native.get('source_hashes')),
        implementation_sha256=hashed(manifest.get('implementation_hashes')),
        assigned_skills=sorted(set(strings(req.get('skills')) + list(obj(manifest.get('role_skills'))))),
        reasoning_effort=text(obj(manifest.get('model')).get('reasoning_effort'))))
    for key, value in [('runtime', obj(manifest.get('worker')).get('runtime')), ('model', obj(manifest.get('model')).get('name'))]:
        if not doc.get(key) and isinstance(value, str):
            doc[key] = text(value)
    doc['request'] = clean(dict(mode=text(req.get('mode')), scope=text(req.get('scope'))))
    return doc


def enrich(doc, record, meta):
    common(doc, meta)
    missing = []
    if not doc.get('run_id'):
        missing.append('run_id')
    if doc['kind'] == 'tool':
        args, result, auth = (obj(record.get(k)) for k in ('arguments', 'result', 'authorization'))
        toolname = doc['name']
        doc['tool'] = clean(dict(call_id=text(record.get('id')), name=toolname,
            result_status=text(result.get('status')), argument_names=sorted(args)[:100], arguments_sha256=hashed(args),
            started_at=date(record.get('started_at')), ended_at=date(record.get('at')),
            duration_ms=number(record.get('duration_ms'))))
        if 'duration_ms' not in doc['tool']:
            missing.append('tool.duration_ms')
        doc['config']['harness_version'] = text(record.get('harness_version')) or doc['config'].get('harness_version', '')
        accesses = record.get('accesses', []) if isinstance(record.get('accesses'), list) else []
        resources = [clean(dict(type='file' if str(a.get('path', '')).startswith('/') else 'reference',
                               id=text(a.get('path'), 2048), operation=text(a.get('operation')), purpose=text(a.get('purpose'))))
                     for a in accesses[:200] if isinstance(a, dict) and isinstance(a.get('path'), str)]
        resources += [dict(type=k, id=text(args[k], 2048), operation='requested')
                      for k in ('target', 'asset', 'target_worker', 'path') if isinstance(args.get(k), str)]
        doc['resource'] = resources
        doc['resource_paths'] = list(dict.fromkeys(r['id'] for r in resources if r.get('type') == 'file'))
        doc['resource_operations'] = list(dict.fromkeys(r['operation'] for r in resources if r.get('operation')))
        if 'accesses' not in record:
            missing.append('resource')
        doc['access_policy'] = clean(dict(required_permission=text(auth.get('permission')),
            configured_mode=doc['permission_mode'], effective_decision=doc['outcome'], role=doc['role'],
            boundary=doc['boundary'], autonomy=doc['autonomy']))
        decisions = auth.get('user_decisions', []) if isinstance(auth.get('user_decisions'), list) else []
        doc['approval'] = [clean(dict(request_id=text(d.get('request')), grant_id=text(d.get('grant')),
            decision=text(d.get('decision')), permission=text(d.get('permission')), actor=text(d.get('actor')),
            decided_at=date(d.get('decided_at')), boundary=text(d.get('boundary')))) for d in decisions[:50] if isinstance(d, dict)]
        pending_id = result.get('permission_request') or (result.get('request_id') if doc['outcome'] == 'pending' else None)
        if pending_id:
            doc['approval'].append(dict(request_id=text(pending_id), decision='pending'))
            doc['approval_refs'].append(text(pending_id))
        change_id = result.get('authorization_request') or (args.get('request_id') if toolname == 'approve_request' else None)
        if change_id:
            doc['approval_refs'].append(text(change_id))
            decision = obj(result.get('decision'))
            if type(decision.get('approve')) is bool:
                doc['approval'].append(clean(dict(request_id=text(change_id), decision='approved' if decision['approve'] else 'denied',
                    actor=text(decision.get('worker')), decided_at=date(decision.get('at')))))
        doc['approval_decisions'] = sorted({a['decision'] for a in doc['approval'] if a.get('decision')})
        if doc['outcome'] in ('error', 'denied', 'held'):
            doc['error'] = clean(dict(code=text(result.get('code')), type=text(result.get('type')),
                                      message=text(result.get('error') or result.get('reason'), 1500)))
        if toolname == 'skill_read' and doc['outcome'] == 'observed':
            doc['tool'].update(clean(dict(skill_name=text(result.get('name')), skill_sha256=text(result.get('sha256')))))
        if toolname == 'delegate_work' and result.get('delegation_id'):
            doc['delegation'] = dict(id=text(result['delegation_id']), from_worker=doc['worker'],
                to_worker=text(args.get('worker')), parent_run_id=doc['run_id'], parent_call_id=text(record.get('id')))
            doc['correlation']['delegation_id'] = text(result['delegation_id'])
        if args.get('finding_id'):
            doc['correlation']['finding_id'] = text(args['finding_id'])
        doc['coverage'] = dict(resource_truncated=len(accesses) > 200, approval_truncated=len(decisions) > 50)
    elif doc['kind'] == 'activity':
        data = obj(record.get('data'))
        if data.get('session_id'):
            doc['correlation']['session_id'] = text(data['session_id'])
        if doc['name'].startswith('agent.'):
            doc['explanation'] = clean(dict(stage=doc['name'].split('.', 1)[1], summary=text(data.get('summary'), 1500),
                steps=strings(data.get('steps')), evidence_refs=strings(data.get('evidence')),
                rework_cause=text(data.get('rework_cause'), 1500)))
            doc['quality'] = {'rework_reported': bool(data.get('rework_cause'))}
            if data.get('tool_call_id'):
                doc['tool'] = {'call_id': text(data['tool_call_id'])}
    elif doc['kind'] == 'session':
        if meta.get('result_evidence'):
            doc['evidence_items'] = [meta['result_evidence']]
        if record.get('session_id'):
            doc['correlation']['session_id'] = text(record['session_id'])
        else:
            missing.append('correlation.session_id')
        timing = {k: date(record.get(k)) for k in ('started_at', 'ended_at')}
        doc['execution'].update(clean({**timing, 'duration_ms': number(record.get('duration_ms')),
            'timing_basis': 'runtime_monotonic' if number(record.get('duration_ms')) is not None else None}))
        if 'duration_ms' not in doc['execution']:
            missing.append('execution.duration_ms')
        if record.get('harness_version'):
            doc['config']['harness_version'] = text(record['harness_version'])
        usage = obj(record.get('usage'))
        doc['usage'] = {'measurement_scope': 'session_result', 'cost_status': 'not_measured',
                        'input_semantics': 'uncached' if doc.get('runtime') == 'claude' else 'includes_cache' if doc.get('runtime') == 'codex' else 'unknown'}
        for dest, source in [('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
                             ('cache_read_tokens', 'cache_read_input_tokens' if doc.get('runtime') == 'claude' else 'cached_input_tokens'),
                             ('cache_creation_tokens', 'cache_creation_input_tokens')]:
            val = number(usage.get(source))
            if val is not None:
                doc['usage'][dest] = val
        if doc.get('usage_known'):
            doc['usage']['total_tokens'] = doc['tokens_total']
        else:
            missing.append('usage.total_tokens')
        verification, outcome = obj(record.get('verification')), obj(record.get('request_outcome'))
        doc['quality'] = clean(dict(task_status=text(outcome.get('status')), response_kind=text(outcome.get('response_kind')),
            verification_basis='receipt_presence' if verification else 'not_measured',
            tool_calls=number(verification.get('tool_calls')), artifacts=strings(outcome.get('artifacts')),
            observed_live_evidence=verification.get('observed_live_evidence') if type(verification.get('observed_live_evidence')) is bool else None))
        if not verification:
            missing.append('quality.observed_live_evidence')
        if doc['outcome'] == 'error':
            doc['error'] = clean(dict(type=text(record.get('type')), message=text(record.get('error'), 1500)))
    if not doc['config'].get('harness_version'):
        missing.append('config.harness_version')
    doc.setdefault('coverage', {})['missing'] = missing
    return doc


def base_event(identity, at, kind, name, reference, row):
    doc = dict(schema_version=VERSION, event_id=digest(identity), kind=kind, name=name,
               domain='xoc', outcome='recorded', assertion='control_record', time_basis='record',
               evidence_ref=reference, evidence_sha256=hashed(row))
    doc['@timestamp'] = iso(stamp(at, 0))
    return doc


def item(doc):
    return EVENTS + doc['@timestamp'][:10].replace('-', '.'), doc['event_id'], doc


def request_documents(data, reference):
    """요청의 현재 상태와 승인 결정 이력. 대화·프롬프트 본문은 내보내지 않는다."""
    rid = text(data.get('id'))
    if not rid or not date(data.get('created')):
        return
    doc = base_event(rid, data['created'], 'request', 'request.state', reference, data)
    doc.update(worker=text(data.get('worker')) or 'service-desk', title=text(data.get('title'), 300),
               status=text(data.get('status')), summary='업무 요청 현재 상태 · 대화 원문은 업무 화면에서 확인')
    doc['correlation'] = dict(request_id=rid, trace_id=rid, trace_basis='request', request_revision=data.get('revision'))
    doc['request'] = clean(dict(title=doc['title'], status=doc['status'], mode=text(data.get('mode')), scope=text(data.get('scope'))))
    yield item(doc)
    for p in data.get('permission_requests', []):
        if not isinstance(p, dict) or not date(p.get('created')):
            continue
        doc = base_event(rid + ':' + text(p.get('id')), p['created'], 'approval', 'approval.state', reference, p)
        doc.update(worker=text(p.get('worker')), role=text(p.get('role')), status=text(p.get('status')), summary='도구 승인 현재 상태')
        doc['correlation'] = clean(dict(request_id=rid, trace_id=rid, trace_basis='request', task_id=text(p.get('task_id')),
                                        request_revision=number(p.get('revision'))))
        doc['approval'] = [clean(dict(request_id=text(p.get('id')), decision=text(p.get('decision')) or 'pending',
            actor=text(p.get('actor')), decided_at=date(p.get('decided_at')), permission=text(p.get('permission')),
            boundary=text(p.get('boundary')), consumed=p.get('status') == 'consumed'))]
        doc['approval_decisions'] = [doc['approval'][0]['decision']]
        doc['tool'] = {'name': text(p.get('tool'))}
        yield item(doc)
    for row in data.get('events', []):
        if not isinstance(row, dict) or not text(row.get('kind')).startswith('permission_') or not date(row.get('at')):
            continue
        doc = base_event(rid + ':' + hashed(row), row['at'], 'approval', text(row['kind']), reference + '#events', row)
        doc.update(worker=text(row.get('worker')), summary='업무 도구 승인 이력', approval_refs=[text(row.get('permission_request'))])
        doc['correlation'] = dict(request_id=rid, trace_id=rid, trace_basis='request')
        doc['actor'] = clean(dict(id=text(row.get('actor')), type='instructor' if row.get('actor') == 'instructor' else 'unknown'))
        doc['approval_decisions'] = [text(row['decision'])] if row.get('decision') else []
        yield item(doc)


def grant_documents(data, reference):
    for row in data.get('events', []):
        if not isinstance(row, dict) or not date(row.get('at')):
            continue
        doc = base_event(reference + ':' + hashed(row), row['at'], 'approval', 'grant.' + text(row.get('kind')), reference + '#events', row)
        doc.update(summary='상시 허용·회수 이력', approval_refs=[text(row.get('grant'))])
        doc['actor'] = clean(dict(id=text(row.get('actor')), type='instructor' if row.get('actor') == 'instructor' else 'unknown'))
        doc['correlation'] = clean(dict(request_id=text(row.get('request'))))
        doc['approval_decisions'] = [text(row.get('kind'))]
        yield item(doc)


def change_approval_documents(row, reference):
    """시설 변경의 독립 승인 원장. 도구 사용 허용 원장과 별도 사건이다."""
    if not row.get('id') or not date(row.get('created')):
        return
    decision = obj(row.get('decision'))
    doc = base_event(reference, row['created'], 'approval', 'change_approval.state', reference, row)
    doc.update(worker=text(row.get('worker')), run_id=run_name(row.get('session_dir')), status=text(row.get('status')),
               approval_refs=[text(row['id'])], summary='시설 변경 독립 승인 현재 상태')
    doc['subject'] = clean(dict(worker=doc['worker'], run_id=doc['run_id']))
    doc['actor'] = clean(dict(id=text(decision.get('worker')), type='agent' if decision.get('worker') else 'unknown'))
    doc['approval'] = [clean(dict(request_id=text(row['id']),
        decision=('approved' if decision['approve'] else 'denied') if type(decision.get('approve')) is bool else 'pending',
        actor=text(decision.get('worker')), decided_at=date(decision.get('at'))))]
    doc['approval_decisions'] = [doc['approval'][0]['decision']]
    doc['tool'] = {'name': text(row.get('action'))}
    yield item(doc)
