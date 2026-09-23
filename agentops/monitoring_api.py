"""xOC/SOC 요약과 사건 검색. 조회는 모델·탐지·티켓 생성을 유발하지 않는다."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
import yaml
from fastapi import HTTPException, Query, Request, Response
from activity_audit import scrub
from observability.client import IndexClient

EVENTS, TICKETS, WAZUH = 'kt66-agent-events-*', 'kt66-agent-tickets-v1', 'wazuh-alerts-*'
ACTIVE = ('open', 'acknowledged')


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def count(result):
    total = result.get('hits', {}).get('total', 0)
    return total.get('value', 0) if isinstance(total, dict) else total


def buckets(agg):
    return [{'key': row['key'], 'count': row['doc_count']} for row in agg.get('buckets', [])]


def grouped_findings(findings):
    groups = {}
    for row in findings:
        if row.get('status') not in ACTIVE:
            continue
        key = (row.get('worker', 'unknown'), row.get('rule', 'unknown'))
        group = groups.setdefault(key, {'worker': key[0], 'rule': key[1], 'title': row.get('title'), 'count': 0,
                                         'risk': 0, 'first_at': row.get('created_at', 0), 'latest_at': 0, 'finding_id': row.get('id')})
        group['count'] += 1
        group['risk'] = max(group['risk'], row.get('risk', 0))
        group['first_at'] = min(group['first_at'], row.get('created_at', 0))
        if row.get('created_at', 0) >= group['latest_at']:
            group.update(latest_at=row.get('created_at', 0), finding_id=row.get('id'), run_id=row.get('run_id'),
                         response=row.get('response'), detail=row.get('detail'))
    return sorted(groups.values(), key=lambda g: (-g['risk'], -g['latest_at'], g['worker']))


class Monitor:
    def __init__(self, root, client_factory=IndexClient):
        self.root, self.client_factory = Path(root), client_factory
        self.cache, self.lock = {}, threading.RLock()

    def local(self):
        import xoc
        path = self.root / 'tickets/xoc/state.json'
        if not path.exists():
            return {'available': False, 'error': 'xOC 탐지 기록 미수집', 'groups': [], 'totals': None, 'holds': []}
        try:
            state = xoc.stored_state(self.root)
            rows = list(state['findings'].values())
            active = [f for f in rows if f.get('status') in ACTIVE]
            groups = grouped_findings(rows)
            holds = [h for h in state['holds'].values() if h.get('until', 0) > time.time()]
            return {'available': True, 'checked_at': state.get('checked_at'), 'coverage': state.get('coverage'),
                    'configuration_errors': state.get('configuration_errors', []), 'groups': groups[:8], 'group_total': len(groups),
                    'totals': {'open': len(active), 'high': sum(f.get('risk', 0) >= 12 for f in active),
                               'held': len(holds), 'workers': len({f.get('worker') for f in active}),
                               'closed': sum(f.get('status') in ('resolved', 'false_positive') for f in rows)},
                    'holds': holds, 'rules': xoc.config(self.root), 'status_counts': dict(Counter(f.get('status') for f in rows))}
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            return {'available': False, 'error': 'xOC 상태 읽기 실패', 'groups': [], 'totals': None, 'holds': []}

    def collector(self):
        try:
            result = json.loads(Path(os.environ.get('OBS_COLLECTOR_STATUS', '/siem-state/status.json')).read_text())
            result['fresh'] = time.time() - result.get('heartbeat', 0) < 150
            return result
        except (OSError, ValueError):
            return {'state': 'unavailable', 'fresh': False, 'error': 'SIEM 수집기 상태 미수집'}

    def query(self, kind, start, end, hours):
        client = self.client_factory()
        field = 'timestamp' if kind == 'soc' else '@timestamp'
        bounds = {'gte': iso(start), 'lte': iso(end)}
        interval = '5m' if hours == 1 else '1h' if hours == 24 else '1d'
        trend = {'date_histogram': {'field': field, 'fixed_interval': interval, 'min_doc_count': 0,
                                   'extended_bounds': {'min': iso(start), 'max': iso(end)}}}
        query = {'size': 0, 'track_total_hits': True, 'query': {'range': {field: bounds}}}
        if kind == 'soc':
            query['aggs'] = {'trend': trend, 'high': {'filter': {'range': {'rule.level': {'gte': 12}}}},
                             'sources': {'cardinality': {'field': 'data.srcip', 'precision_threshold': 40000}},
                             'assets': {'cardinality': {'field': 'agent.id'}},
                             'origins': {'terms': {'field': 'data.srcip', 'size': 6}},
                             'rules': {'terms': {'field': 'rule.id', 'size': 6}, 'aggs': {'sample': {'top_hits': {'size': 1, '_source': ['rule.description', 'rule.level']}}}},
                             'priority': {'filter': {'range': {'rule.level': {'gte': 12}}}, 'aggs': {'groups': {
                                 'terms': {'field': 'rule.id', 'size': 8, 'order': [{'severity': 'desc'}, {'last': 'desc'}]},
                                 'aggs': {'severity': {'max': {'field': 'rule.level'}}, 'last': {'max': {'field': 'timestamp'}},
                                          'rows': {'top_hits': {'size': 1, 'sort': [{'rule.level': 'desc'}, {'timestamp': 'desc'}],
                                          '_source': ['timestamp', 'rule', 'agent', 'data.srcip', 'data.dstip', 'location']}}}}}},
                             'latest': {'max': {'field': 'timestamp'}}}
            trend['aggs'] = {'high': {'filter': {'range': {'rule.level': {'gte': 12}}}}}
            result = client.search(WAZUH, query)
            if not result.get('_shards', {}).get('total'):
                return {'available': False, 'error': 'Wazuh 경보 인덱스 미생성 · 수집 상태 확인 필요'}
            a = result.get('aggregations', {})
            return {'available': True, 'total': count(result), 'high': a.get('high', {}).get('doc_count', 0),
                    'sources': a.get('sources', {}).get('value', 0), 'assets': a.get('assets', {}).get('value', 0),
                    'trend': [{'at': b['key'] / 1000, 'count': b['doc_count'], 'high': b['high']['doc_count']} for b in a.get('trend', {}).get('buckets', [])],
                    'origins': buckets(a.get('origins', {})),
                    'rules': [{'key': b['key'], 'count': b['doc_count'], 'description': b['sample']['hits']['hits'][0]['_source'].get('rule', {}).get('description')}
                              for b in a.get('rules', {}).get('buckets', [])],
                    'priority': [{'id': h['_id'], 'index': h['_index'], 'count': group['doc_count'], **h['_source']}
                                 for group in a.get('priority', {}).get('groups', {}).get('buckets', []) for h in group['rows']['hits']['hits']],
                    'latest': a.get('latest', {}).get('value_as_string')}
        query['aggs'] = {'trend': trend, 'outcomes': {'terms': {'field': 'outcome', 'size': 12}},
                         'tools': {'filter': {'term': {'kind': 'tool'}}},
                         'sessions': {'filter': {'term': {'kind': 'session'}}, 'aggs': {'known': {'filter': {'term': {'usage_known': True}}}, 'tokens': {'sum': {'field': 'tokens_total'}}}},
                         'workers': {'terms': {'field': 'worker', 'size': 8}}, 'names': {'filter': {'term': {'kind': 'tool'}}, 'aggs': {'rows': {'terms': {'field': 'name', 'size': 6}}}},
                         'latest': {'max': {'field': '@timestamp'}}}
        result = client.search(EVENTS, query)
        if not result.get('_shards', {}).get('total'):
            return {'available': False, 'error': '에이전트 인덱스 미생성 · 초기화 및 수집기 확인 필요'}
        a = result.get('aggregations', {})
        sessions = a.get('sessions', {})
        return {'available': True, 'total': count(result), 'tools': a.get('tools', {}).get('doc_count', 0),
                'outcomes': {b['key']: b['doc_count'] for b in a.get('outcomes', {}).get('buckets', [])},
                'sessions': sessions.get('doc_count', 0), 'measured_sessions': sessions.get('known', {}).get('doc_count', 0),
                'tokens': sessions.get('tokens', {}).get('value', 0), 'workers': buckets(a.get('workers', {})),
                'tool_names': buckets(a.get('names', {}).get('rows', {})),
                'trend': [{'at': b['key'] / 1000, 'count': b['doc_count']} for b in a.get('trend', {}).get('buckets', [])],
                'latest': a.get('latest', {}).get('value_as_string')}

    def snapshot(self, hours):
        with self.lock:
            cached = self.cache.get(hours)
            if cached and time.time() - cached['generated_at'] < 15:
                return cached
            end, result = time.time(), {}
            start = end - hours * 3600
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {kind: pool.submit(self.query, kind, start, end, hours) for kind in ('agent', 'soc')}
                for kind, future in futures.items():
                    try:
                        result[kind] = future.result()
                    except Exception as error:
                        result[kind] = {'available': False, 'error': 'SIEM 조회 불가 · ' + type(error).__name__}
            result.update(generated_at=end, start=start, end=end, hours=hours, xoc=self.local(), collector=self.collector())
            self.cache[hours] = result
            return result

    def cases(self, kind, status, domain, query, page):
        if kind == 'xoc':
            import xoc
            rows = list(xoc.stored_state(self.root)['findings'].values())
            rows = [f for f in rows if status == 'all' or (f.get('status') in ACTIVE if status == 'active' else f.get('status') == status)]
            needle = query.casefold()
            rows = [f for f in rows if not needle or needle in ' '.join(str(f.get(k, '')) for k in ('worker', 'title', 'rule', 'detail')).casefold()]
            rows.sort(key=lambda f: (f.get('status') not in ACTIVE, -f.get('risk', 0), -f.get('created_at', 0)))
            return {'total': len(rows), 'page': page, 'size': 25, 'rows': rows[page * 25:(page + 1) * 25], 'source': 'xOC 현재 판정 원장'}
        filters = []
        if domain != 'all':
            filters.append({'term': {'domain': domain}})
        if query:
            filters.append({'multi_match': {'query': query, 'fields': ['title', 'worker', 'summary'], 'operator': 'and'}})
        result = self.client_factory().search(TICKETS, {'size': 25, 'from': page * 25, 'track_total_hits': True,
            'query': {'bool': {'filter': filters}}, 'sort': [{'@timestamp': 'desc'}, {'event_id': 'asc'}], '_source': {'excludes': ['body']}})
        return {'total': count(result), 'page': page, 'size': 25, 'rows': [h['_source'] for h in result['hits']['hits']], 'source': 'SIEM 업무 보고서 · 표준 사건 상태 없는 참고 기록'}


def install(app, root, key, templates):
    monitor = Monitor(root)
    app.state.monitor = monitor
    secrets = [key, *[v for k, v in os.environ.items() if any(s in k.lower() for s in ('password', 'secret', 'token'))]]

    def auth(request, response):
        response.headers['Cache-Control'] = 'private, no-store'
        if not key or not hmac.compare_digest(request.headers.get('x-api-key', ''), key):
            raise HTTPException(401, '강사 키가 필요합니다', headers={'Cache-Control': 'private, no-store'})

    @app.get('/soc', include_in_schema=False)
    def page(request: Request):
        return templates.TemplateResponse('operations-center.html', {'request': request, 'initial': 'soc'})

    @app.get('/api/monitoring/summary')
    def summary(request: Request, response: Response, hours: int = Query(24)):
        auth(request, response)
        if hours not in (1, 24, 168, 720):
            raise HTTPException(400, '기간은 1시간·24시간·7일·30일 중 선택하세요')
        return scrub(monitor.snapshot(hours), secrets)

    @app.get('/api/monitoring/cases')
    def cases(request: Request, response: Response, kind: str = 'xoc', status: str = 'active', domain: str = 'all',
              q: str = Query('', max_length=120), page: int = Query(0, ge=0, le=399)):
        auth(request, response)
        if kind not in ('xoc', 'tickets') or status not in ('all', 'active', 'open', 'acknowledged', 'resolved', 'false_positive') or domain not in ('all', 'xoc', 'soc', 'operations'):
            raise HTTPException(400, '사건 필터를 확인하세요')
        try:
            return scrub(monitor.cases(kind, status, domain, q, page), secrets)
        except Exception:
            raise HTTPException(503, '사건 저장소 조회 실패 · 잠시 후 다시 시도하세요') from None

    @app.get('/api/monitoring/detail')
    def detail(request: Request, response: Response, kind: str, id: str = Query(..., min_length=1, max_length=200), index: str = ''):
        auth(request, response)
        if kind == 'xoc':
            import xoc
            result = xoc.stored_state(root)['findings'].get(id)
        elif kind in ('ticket', 'soc'):
            if kind == 'soc' and not re.fullmatch(r'wazuh-alerts-[a-zA-Z0-9_.-]{1,100}', index):
                raise HTTPException(400, 'SIEM 경보 인덱스 범위가 아닙니다')
            try:
                result = monitor.client_factory().search(TICKETS if kind == 'ticket' else index,
                    {'size': 1, 'query': {'ids': {'values': [id]}}})['hits']['hits']
                result = result[0]['_source'] if result else None
                if result and kind == 'soc':
                    result = {k: v for k, v in result.items() if k in ('timestamp', 'rule', 'agent', 'data', 'location', 'decoder', 'full_log')}
            except Exception:
                raise HTTPException(503, 'SIEM 상세 조회 실패') from None
        else:
            raise HTTPException(400, '상세 유형을 확인하세요')
        if result is None:
            raise HTTPException(404, '사건이 없습니다')
        return scrub(result, secrets)

    return monitor
