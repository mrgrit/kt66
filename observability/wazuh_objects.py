"""Wazuh 4.10 / OpenSearch Dashboards용 저장 검색·대시보드 배포 파일 생성."""
import json
from pathlib import Path
from schema import mappings

OUT = Path(__file__).resolve().parents[1] / 'docs/wazuh/kt66-agent-observatory.ndjson'


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def objects():
    rows = []
    def add(kind, key, attributes, references=None):
        rows.append(dict(type=kind, id='kt66-' + key + '-v2', attributes=attributes, references=references or []))
        return rows[-1]['id']

    def mapped_fields(props, prefix='', nested=None):
        result = []
        for key, spec in props.items():
            path = prefix + key
            if 'properties' in spec:
                result += mapped_fields(spec['properties'], path + '.', path if spec['type'] == 'nested' else nested)
                continue
            field = dict(name=path, type={'long':'number','keyword':'string','text':'string'}.get(spec['type'],spec['type']),
                esTypes=[spec['type']], searchable=spec.get('index', True),
                aggregatable=spec['type'] != 'text', readFromDocValues=spec['type'] != 'text', count=0, scripted=False)
            if nested:
                field['subType'] = {'nested': {'path': nested}}
            result.append(field)
        return result
    field_list = mapped_fields(mappings()['properties'])
    events = add('index-pattern', 'agent-events', dict(title='kt66-agent-events-*', timeFieldName='@timestamp', fields=js(field_list)))
    findings = add('index-pattern', 'agent-findings', dict(title='kt66-agent-findings-v1', fields=js(field_list)))
    tickets = add('index-pattern', 'agent-tickets', dict(title='kt66-agent-tickets-v1', timeFieldName='@timestamp', fields=js(field_list)))

    def source(index, query):
        return {'searchSourceJSON':js(dict(query={'language':'lucene','query':query}, filter=[], indexRefName='kibanaSavedObjectMeta.searchSourceJSON.index'))}
    def refs(index):
        return [dict(type='index-pattern', id=index, name='kibanaSavedObjectMeta.searchSourceJSON.index')]
    def search(key, title, query, columns, index=events):
        return add('search', key, dict(title=title, description='KT66 정밀 관제 v2 · 매뉴얼 docs/WAZUH-AGENT-MONITORING.ko.md',
            columns=columns, sort=[['risk','desc'],['@timestamp','desc']] if index == findings else [['@timestamp','desc']],
            kibanaSavedObjectMeta=source(index,query)), refs(index))
    investigations = search('agent-tool-investigation', 'KT66 · 도구·권한 조사', 'kind:tool',
        ['worker','tool.name','outcome','access_policy.configured_mode','approval_decisions','correlation.request_id','run_id','tool.call_id'])
    alerts = search('agent-active-findings', 'KT66 · 현재 미종결 발견 (전체 기간)', 'status:(open OR acknowledged)',
        ['risk','worker','rule','title','status','correlation.request_id','run_id'], findings)
    search('agent-approvals', 'KT66 · 승인·회수 이력', 'kind:approval',
        ['name','actor.id','worker','approval_decisions','approval_refs','correlation.request_id','status'])
    search('agent-explanations', 'KT66 · 상황·계획·판단·재작업', 'kind:activity AND name:agent.*',
        ['worker','explanation.stage','explanation.summary','explanation.rework_cause','correlation.request_id','run_id'])
    search('agent-sessions', 'KT66 · 세션 품질·사용량', 'kind:session',
        ['worker','model','execution.duration_ms','usage.total_tokens','quality.task_status','quality.observed_live_evidence','config.harness_version','coverage.missing','run_id'])
    search('agent-reports', 'KT66 · 업무 보고서', 'kind:ticket', ['title','worker','correlation.request_id','run_id'], tickets)

    def table(key, title, query, metrics, buckets, index=events):
        aggs = []
        for typ, field, label in metrics:
            aggs.append(dict(id=str(len(aggs)+1),enabled=True,type=typ,schema='metric',params={**({'field':field} if field else {}),'customLabel':label}))
        for field, label in buckets:
            aggs.append(dict(id=str(len(aggs)+1),enabled=True,type='terms',schema='bucket',params=dict(field=field,size=20,order='desc',orderBy='1',customLabel=label)))
        return add('visualization', key, dict(title=title, description='선택 기간의 기록 기준. 미측정 값은 별도 누락 검색으로 확인.',
            visState=js(dict(title=title,type='table',params=dict(perPage=10,showPartialRows=False,showMetricsAtAllLevels=False,sort={'columnIndex':None,'direction':None},showTotal=False),aggs=aggs)),
            uiStateJSON='{}', kibanaSavedObjectMeta=source(index,query)),refs(index))

    calls = table('agent-tool-outcomes', 'KT66 · 도구별 실행·거절·오류', 'kind:tool', [('count',None,'호출 수')], [('tool.name','도구'),('outcome','상태')])
    usage = table('agent-token-cost', 'KT66 · 실측 토큰 (세션 기준)', 'kind:session AND usage_known:true',
        [('sum','usage.total_tokens','총 토큰'),('sum','usage.input_tokens','입력'),('sum','usage.output_tokens','출력'),('sum','usage.cache_read_tokens','캐시 읽기'),('count',None,'측정 세션')], [('worker','근무자'),('model','모델')])
    gaps = table('agent-missing', 'KT66 · 세션 관측 누락', 'kind:session AND coverage.missing:*', [('count',None,'누락 세션')], [('coverage.missing','누락 필드')])
    versions = table('agent-config-quality', 'KT66 · 설정별 실행 품질', 'kind:session', [('count',None,'세션 수'),('avg','execution.duration_ms','실측 평균 ms')],
        [('worker','근무자'),('config.harness_version','설정 버전'),('outcome','실행 종료 상태')])
    note = add('visualization','agent-guide',dict(title='KT66 · 관제 해석 기준',visState=js(dict(type='markdown',title='KT66 · 관제 해석 기준',aggs=[],params=dict(fontSize=13,openLinksInNewTab=False,markdown=
        '### AI 에이전트 정밀 관제\n도구 관측 ≠ 업무 성공 · 발견 ≠ 확정 침해 · 미측정 ≠ 0\n\n도구/토큰은 **선택 기간**, 미종결 발견은 **전체 기간**입니다. 토큰 표의 캐시 읽기는 Codex 입력에 포함되어 있으므로 더하지 않습니다. 설정별 평균 시간은 실측 세션만 포함합니다.\n\n`correlation.request_id → run_id → tool.call_id` 순서로 Discover에서 조사하세요. 전체 절차: 저장소 `docs/WAZUH-AGENT-MONITORING.ko.md`'))),uiStateJSON='{}',kibanaSavedObjectMeta={'searchSourceJSON':'{"query":{"language":"lucene","query":""},"filter":[]}'}))
    panels, references = [], []
    for n, (kind, identifier, x, y, w, h) in enumerate([
            ('visualization',note,0,0,48,11), ('search',alerts,0,11,48,12),
            ('visualization',calls,0,23,24,12), ('visualization',usage,24,23,24,12),
            ('visualization',gaps,0,35,24,12), ('visualization',versions,24,35,24,12),
            ('search',investigations,0,47,48,14)]):
        ref = 'panel_' + str(n)
        references.append(dict(name=ref,type=kind,id=identifier))
        panels.append(dict(panelIndex=str(n),type=kind,panelRefName=ref,version='2.16.0',embeddableConfig={},gridData=dict(x=x,y=y,w=w,h=h,i=str(n))))
    add('dashboard','agent-observatory',dict(title='KT66 · AI 에이전트 정밀 관제',description='권한·승인·설정·사용량·관측 누락을 함께 조사합니다.',
        panelsJSON=js(panels),optionsJSON=js(dict(useMargins=True,hidePanelTitles=False)),version=1,timeRestore=True,
        timeFrom='now-24h',timeTo='now',refreshInterval={'pause':True,'value':30000},
        kibanaSavedObjectMeta={'searchSourceJSON':'{"query":{"language":"lucene","query":""},"filter":[]}'}),references)
    return rows


if __name__ == '__main__':
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(js(row) for row in objects()) + '\n')
    print(f'{len(objects())}개 저장 객체 생성: {OUT.name}')
