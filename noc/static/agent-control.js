/* All evidence is untrusted text. Never render logs, prompts or model prose as HTML. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const json=v=>esc(JSON.stringify(v,null,2));
  const num=n=>n==null?'미수집':Number(n).toLocaleString('ko-KR');
  const short=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(2)+'M':n>=1000?(n/1000).toFixed(1)+'K':num(n);
  const date=v=>v?new Date(typeof v==='number'?v*1000:v).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'시각 미수집';
  const statuses={completed:'완료',failed:'실패',needs_review:'근거 검토',running:'실행 중',queued:'대기',retry:'재시도 대기',waiting_capacity:'한도 대기',superseded:'점검 정책 전환',unknown:'종료 미확인'};
  const triggers={user_request:'사용자 업무',periodic:'정기 점검',event:'이슈 발생',approval:'승인 요청',delegation:'위임 작업',review:'조치 후 검증',manual:'개별 실행'};
  const stages={'request':'요청 수신','agent.situation':'상황 인식','agent.plan':'작업 계획','agent.decision':'판단 요약','agent.review':'결과 검토','session.completed':'세션 종료','session.failed':'세션 실패'};
  const badge=(value,label)=>`<span class="badge ${esc(value)}">${esc(label||statuses[value]||value)}</span>`;
  const raw=(label,value)=>`<details><summary>${esc(label)}</summary><pre>${typeof value==='string'?esc(value):json(value)}</pre></details>`;
  const section=(title,body)=>`<section><h3>${esc(title)}</h3>${body}</section>`;
  const empty=text=>`<div class="unknown-note">${esc(text)}</div>`;
  let roster=[],items=[],cursor=null,selected=null,current=null,tab='overview',paused=false,listVersion=0,detailVersion=0,loading=false;
  let filterTimer;
  const params=new URLSearchParams(location.search);
  const initialWorker=params.get('worker')||'';
  const initialRun=params.get('run');
  let initialSelection=initialRun;
  const workerName=id=>roster.find(w=>w.id===id)?.name||id;
  async function api(url) {
    const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw new Error(`기록 조회 실패 (${response.status})`);
    return response.json();
  }
  function error(message){$('#error').hidden=!message;$('#error').textContent=message||'';}
  function markCrew(){document.querySelectorAll('[data-worker-filter]').forEach(b=>{const on=b.dataset.workerFilter===$('#worker').value;b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on))});}
  function renderMonitoring(engine){
    const policy=engine.monitoring;
    $('#monitor-policy').hidden=!policy?.enabled;
    if(!policy?.enabled)return;
    const rows=(policy.loops||[]).filter(m=>!$('#worker').value||m.worker===$('#worker').value);
    const skipped=rows.reduce((n,m)=>n+(m.skipped_model_calls||0),0);
    const reasons={baseline:'정상 기준 수집',unchanged:'변경 없음',state_changed:'상태 변경',problem_detected:'문제 발견',incident_review:'지속 장애 재검토'};
    const decisions={enqueued:'AI 작업 요청',observe_only:'AI 호출 생략',worker_busy:'기존 작업 완료 대기',covered_by_event:'사건 작업으로 처리'};
    $('#monitor-summary').textContent=`${engine.stale?'실행기 갱신 지연 · ':''}적응형 점검 · 평시 10분 → 이상 시 5·2·1분 · AI 호출 생략 ${num(skipped)}회`;
    $('#monitor-loops').innerHTML=rows.length?rows.map(m=>`<div class="monitor-loop"><b>${esc(workerName(m.worker))}</b><span>${esc(m.loop)}</span><strong>${num(m.interval_sec/60)}분 간격</strong><small>다음 점검 ${esc(date(m.next_check_at))}</small><p>${esc(reasons[m.reason]||m.reason)} · ${esc(decisions[m.decision]||m.decision)}</p><small>코드 점검 ${num(m.checks)}회 · AI 작업 요청 ${num(m.model_jobs||0)}회</small></div>`).join(''):'<p>이 근무자는 별도 일정 또는 사건 요청에 따라 작업합니다.</p>';
  }
  function renderList(){
    $('#run-list').innerHTML=items.length?items.map(r=>`<button role="listitem" class="run-item ${selected===r.id?'selected':''}" data-run="${esc(r.id)}" aria-label="${esc(workerName(r.worker))}, ${esc(triggers[r.trigger])}, ${esc(statuses[r.status])}, ${esc(date(r.started))}" aria-current="${selected===r.id?'true':'false'}"><span class="run-top"><b>${esc(workerName(r.worker))}</b>${badge(r.status)}</span><span class="run-kind">${esc(triggers[r.trigger])} · ${esc(r.kind.replace('periodic:',''))}</span><span class="run-meta"><time>${esc(date(r.started))}</time><span>${short(r.usage.total)} TOK · ${r.attempt}회차</span></span>${r.findings.length?`<span class="run-alert">△ ${esc(r.findings[0].title)}${r.findings.length>1?` 외 ${r.findings.length-1}건`:''}</span>`:''}</button>`).join(''):'<div class="empty">조건에 맞는 실행이 없습니다.<br>기간이나 필터를 변경해 보세요.</div>';
    $('#more').hidden=!cursor;
    $('#more').disabled=loading;
  }
  async function load(append=false){
    const version=++listVersion;loading=true;$('#refresh').disabled=true;
    const query=new URLSearchParams({worker:$('#worker').value,trigger:$('#trigger').value,status:$('#status').value,hours:$('#hours').value,q:$('#search').value,limit:40});
    if(append&&cursor)query.set('cursor',cursor);
    try{
      const data=await api('/api/agent-control/runs?'+query);
      if(version!==listVersion)return;
      items=append?[...items,...data.items.filter(r=>!items.some(x=>x.id===r.id))]:data.items;cursor=data.next_cursor;
      const s=data.summary;
      $('#stat-runs').textContent=num(s.runs);
      $('#stat-scope').textContent=($('#hours').selectedOptions[0].textContent)+' · '+($('#worker').value?workerName($('#worker').value):'모든 에이전트');
      $('#stat-active').textContent=`${num(s.statuses.running||0)} / ${num((s.statuses.queued||0)+(s.statuses.retry||0)+(s.statuses.waiting_capacity||0))}`;
      $('#stat-review').textContent=num((s.statuses.failed||0)+(s.statuses.needs_review||0)+(s.statuses.unknown||0));
      $('#stat-tokens').textContent=s.usage_known?short(s.tokens):'미수집';$('#stat-tokens').title=num(s.tokens)+' tokens';
      $('#stat-coverage').textContent=`${num(s.usage_known)}건 수집 · ${num(s.usage_unknown)}건 미수집 · 구독 차감액 아님`;
      $('#engine').textContent=data.engine.stale?'실행기 갱신 지연 · 상태 확인 필요':data.engine.status==='running'?'실행기 가동 · 실시간 기록 조회':'실행기 '+(data.engine.status||'미확인');
      renderMonitoring(data.engine);
      $('#case-count').textContent=num(data.total)+'건';
      $('#source-count').textContent=`원천 작업 ${num(data.source.job_count)} · 세션 ${num(data.source.run_count)} · 10초 수집 캐시`;
      $('#sync').textContent='갱신 '+new Date().toLocaleTimeString('ko-KR',{hour12:false});
      error(data.source.status==='ok'?'':data.source.errors.join(' · '));
      renderList();markCrew();
      if(!selected&&(initialSelection||items[0])){const id=initialSelection||items[0].id;initialSelection=null;await openRun(id,false);}
      else if(current){const row=items.find(r=>r.id===selected);if(row&&row.updated!==current.run.updated)await openRun(selected,false);}
    }catch(e){if(version===listVersion){error(e.message+' · 마지막 수집값을 유지합니다.');$('#sync').textContent='연결 끊김';}}
    finally{if(version===listVersion){loading=false;$('#refresh').disabled=false;$('#more').disabled=false;}}
  }
  function notes(data,types){
    const rows=data.declared.filter(e=>types.includes(e.type));
    return rows.length?rows.map(e=>`<div class="declared-note">${badge('','에이전트 보고')} <b>${esc(stages[e.type])}</b><p>${esc(e.data.summary)}</p>${e.data.steps?.length?`<ol>${e.data.steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol>`:''}${e.data.rework_cause?`<p>재작업 원인: ${esc(e.data.rework_cause)}</p>`:''}<span class="source-label">${esc(date(e.at))} · 근거 ${esc((e.data.evidence||[]).join(' · ')||'명시 없음')}</span></div>`).join(''):empty('별도의 판단·계획 기록이 없습니다. 결과나 도구 순서를 과거의 추론·계획으로 간주하지 않습니다. 신규 세션은 activity_note로 판단 요약과 근거를 남깁니다.');
  }
  function notices(data){return data.run.findings.length?`<div class="notice-list">${data.run.findings.map(f=>`<button class="notice" data-evidence-ref="${esc(f.evidence_refs[0])}">${badge(f.severity,{high:'우선 검토',medium:'검토',info:'수집 안내'}[f.severity])}${esc(f.title)}<span>근거 ↗</span></button>`).join('')}</div>`:'';}
  function overview(data){
    const r=data.run,request=data.request,p=request.trigger_payload||{};
    return (r.kind==='user_request'&&/^req-[a-f0-9]{16}$/.test(p.request_id||'')?`<p><a href="${dcURL('requests')}#${esc(p.request_id)}">연결된 업무 요청 열기 ↗</a></p>`:'')+notices(data)+section('01 · 요청과 트리거',`<dl class="facts"><dt>실행 이유</dt><dd>${esc(triggers[r.trigger])} · ${esc(r.kind)}</dd><dt>요청 시각</dt><dd>${esc(date(request.requested_at))}</dd><dt>실행 시작</dt><dd>${esc(date(r.started))}</dd><dt>작업 ID</dt><dd>${esc(r.job_id||'개별 CLI 세션')}</dd><dt>실행 주체</dt><dd>${esc(r.runtime||'미수집')} / ${esc(r.model||'미수집')} · ${r.attempt}번째 시도</dd></dl>${raw('트리거 원본 · '+request.source,p)}${request.captured?raw('에이전트에 전달된 요청 원문',request.record.prompt):empty('이전 실행은 요청 원문이 저장되지 않았습니다. 당시 트리거 데이터와 로드된 정책을 근거로 확인할 수 있습니다.')}`)
      +(data.relations?.length?section('연관 승인 요청',data.relations.map(r=>raw(r.id+' · '+(r.record.status||'미수집'),r.record)+r.runs.map(x=>`<button class="related-run" data-run="${esc(x.id)}">${esc(workerName(x.worker))} · ${esc(x.kind)} ↗</button>`).join('')).join('')):'')
      +section('02 · 인식한 상황과 작업 계획',notes(data,['agent.situation','agent.plan']))
      +section('03 · 판단 근거와 검토',notes(data,['agent.decision','agent.review']))
      +section('04 · 작업 결과',data.outcome.body?`${badge('','에이전트 최종 보고')}<pre class="report">${esc(data.outcome.body)}</pre>${raw('실행기 검증 기록 (문제 해결의 완전한 보증은 아님)',data.outcome.verification||'검증 기록 없음')}`:empty(r.status==='running'?'실행 중입니다. 도구 타임라인을 확인하세요.':'최종 결과가 수집되지 않았습니다. 실패 원인과 도구 기록을 확인하세요.'))
      +section('수집 범위',`<div class="info-block">도구가 기록한 관측·파일·권한과 에이전트가 보고한 판단 요약을 구분합니다. 비공개 내부 추론과 OS 전체 파일 접근은 수집하지 않습니다.${data.coverage.issues.length?`<p>수집 주의: ${esc(data.coverage.issues.join(', '))}</p>`:''}</div>`);
  }
  function timeline(data){
    if(!data.timeline.length)return empty('이 세션에는 수집된 도구 또는 활동 이벤트가 없습니다.');
    return `<p class="muted" style="font-size:10px;margin-bottom:15px">시간순 ${data.timeline.length}개 이벤트 · 각 항목을 펼치면 입력과 결과를 확인할 수 있습니다.</p>`+data.timeline.map(e=>{
      const tool=e.type==='tool',name=tool?e.name:stages[e.type]||e.type;
      const failed=tool&&(e.name.startsWith('error:')||['failed','denied'].includes(e.result?.status));
      return `<div class="timeline-event" data-source="${esc(e.source)}"><div><span>${tool?'도구 실행 기록':e.assertion==='agent_declared'?'에이전트 보고':'런타임 기록'}</span><time>${esc(date(e.at))}</time></div><details><summary><b>${esc(name)}</b>${tool?badge(failed?'failed':e.authorization.mode,e.result?.status||e.authorization.mode):''}</summary>${tool?`<pre>${json({arguments:e.arguments,authorization:e.authorization,result:e.result})}</pre>`:`<pre>${json(e.data)}</pre>`}</details><span class="source-label">${esc(e.source)}</span></div>`;
    }).join('');
  }
  function files(data){
    const policy=data.policy.effective||{},perms=policy.permission||{};
    return section('실행 당시 권한',`<dl class="facts"><dt>자율 등급</dt><dd>${esc(policy.autonomy||'미수집')}</dd><dt>하네스 버전</dt><dd>${esc(data.policy.version||'미수집')}</dd><dt>정책 출처</dt><dd>${esc(data.policy.source)}</dd></dl><div class="permissions" style="margin-top:14px">${Object.entries(perms).map(([name,mode])=>`<div class="permission"><code>${esc(name)}</code>${badge(mode)}</div>`).join('')||empty('정책 스냅샷 없음')}</div>${raw('샌드박스 범위',policy.sandbox||{})}`)
      +section('기록으로 확인되는 파일 접근',`<div class="unknown-note">도구 영수증에 기록된 읽기·쓰기와 브로커가 사용하는 상태 파일입니다. 정책에 나열된 소스는 접근 이력이 아닙니다. 전체 파일시스템 접근 감사는 별도 수집이 필요합니다.</div>${data.accesses.map(a=>`<div class="access-row">${badge('',a.operation==='read'?'읽기':'쓰기')} ${esc(a.tool)}<code>${esc(a.path)}</code><span class="source-label">${esc(a.evidence_ref)} · ${esc(a.basis)}${a.sha256?'<br>SHA-256 '+esc(a.sha256):''}</span></div>`).join('')||empty('확인 가능한 파일 접근 기록 없음')}`)
      +section('증거 파일',`<div class="file-list">${data.artifacts.map(f=>`<button class="file" data-artifact="${esc(f.name)}"><span>▤ ${esc(f.name)}</span><small>${short(f.bytes)} B ↗</small></button>`).join('')||empty('파일 없음')}</div>`)
      +section('정책 구성 소스 (접근 이력과 별개)',raw('소스별 버전 해시',data.policy.source_files));
  }
  function rework(data){
    const r=data.run,u=r.usage;
    const retry=data.context.retry_reason;
    return section('동일 작업의 시도 이력',data.attempts.length?data.attempts.slice().reverse().map(a=>`<button class="related-run" data-run="${esc(a.id)}">${badge(a.status)} ${a.attempt}번째 시도 · ${esc(date(a.started))}<small>${esc(a.id)}${a.error?' · '+esc(a.error):''}</small></button>`).join(''):empty('연결된 실행 시도 없음'))
      +(r.attempt>1?section('재작업 원인',retry?raw('이전 실행의 결과·실패 원인',retry):empty('과거 재시도의 원인이 별도 저장되지 않았습니다. 이전 시도의 실패 기록을 열어 확인하세요.')):'')
      +section('이전 정기 작업과의 연결',data.previous_cycle?`<button class="related-run" data-run="${esc(data.previous_cycle)}">직전 작업의 인계 기록 열기 ↗<small>${esc(data.previous_cycle)}</small></button><p class="source-label">정기적인 다음 회차는 동일 작업의 재시도와 구분합니다.</p>`:empty('이전 회차의 인계 기록 없음'))
      +section('이번 실행의 토큰',u.known?`<div class="info-block"><b>${num(u.total)} tokens</b> · ${esc(r.runtime)} CLI 관측값<p>캐시를 중복 합산하지 않은 처리량입니다. 구독 사용 한도·차감률·청구 금액을 의미하지 않습니다.</p></div><div class="token-bars">${[['신규 입력','input'],['캐시 읽기','cache_read'],['캐시 생성','cache_write'],['출력','output']].map(([label,key])=>`<div class="token-line"><span>${label}</span><div><i style="width:${u.total?Math.max(0,u[key]/u.total*100):0}%"></i></div><b>${num(u[key])}</b></div>`).join('')}</div><p class="source-label">출력에 포함된 추론 토큰 ${num(u.reasoning_subset)} · 별도 가산하지 않음</p>`:empty('CLI 사용량이 수집되지 않았습니다. 0으로 합산하지 않습니다.'))
      +section('판단 변경·재작업 보고',notes(data,['agent.decision','agent.review']));
  }
  function renderDetail(data){
    const openSources=[...document.querySelectorAll('[data-source] details[open]')].map(e=>e.parentElement.dataset.source);
    const oldScroll=$('.detail-body')?.scrollTop||0;
    const r=data.run;
    const has=types=>data.declared.some(e=>types.includes(e.type));
    const flow=[['요청',data.request.captured?'원문 수집':'트리거 수집',true],['상황',has(['agent.situation'])?'판단 요약':'미수집',has(['agent.situation'])],['계획',has(['agent.plan'])?'기록 확인':'미수집',has(['agent.plan'])],['도구',`${data.timeline.filter(e=>e.type==='tool').length}건`,data.timeline.some(e=>e.type==='tool')],['검토',has(['agent.review'])?'검토 기록':'미수집',has(['agent.review'])],['결과',data.outcome.body?'보고 수집':'미수집',!!data.outcome.body]];
    $('#detail').innerHTML=`<div class="case-head"><div class="case-heading"><div><div class="eyebrow">EXECUTION INVESTIGATION</div><h2>${esc(workerName(r.worker))} <small>/ ${esc(triggers[r.trigger])}</small></h2></div><div class="detail-actions">${badge(r.status)}<button id="export">증거 JSON ↓</button></div></div><div class="identity">${esc(r.id)}<br>SESSION ${esc(r.session_id||'미수집')}</div><div class="flow">${flow.map(([a,b,c])=>`<div class="${c?'':'missing'}">${a}<small>${b}</small></div>`).join('')}</div></div><nav class="detail-tabs" aria-label="실행 조사 항목">${[['overview','조사 개요'],['timeline','도구·활동 타임라인'],['files','파일·권한'],['rework','재작업·토큰']].map(([id,name])=>`<button data-detail-tab="${id}" class="${tab===id?'active':''}" aria-pressed="${tab===id}">${name}</button>`).join('')}</nav><div class="detail-body">${({overview,timeline,files,rework}[tab])(data)}</div>`;
    document.querySelectorAll('[data-source]').forEach(e=>{if(openSources.includes(e.dataset.source))e.querySelector('details').open=true});
    $('.detail-body').scrollTop=oldScroll;
  }
  async function openRun(id,scroll=true){
    const version=++detailVersion;
    const switched=selected!==id;selected=id;renderList();
    if(switched){tab='overview';$('#detail').innerHTML='<div class="empty detail-empty">실행 증거를 읽고 있습니다…</div>';}
    try{
      const data=await api('/api/agent-control/runs/'+encodeURIComponent(id));
      if(version!==detailVersion)return;
      current=data;renderDetail(data);
      const query=new URLSearchParams(location.search);query.set('run',id);history.replaceState(null,'','?'+query);
      if(scroll&&innerWidth<=760)$('#detail').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(e){if(version===detailVersion){current=null;$('#detail').innerHTML=`<div class="empty detail-empty">${esc(e.message)}<button id="retry-detail">다시 읽기</button></div>`;}}
  }
  async function artifact(name){
    const id=selected;$('#evidence-title').textContent=name;$('#evidence-content').textContent='증거 파일을 읽고 있습니다…';$('#evidence-meta').textContent='';$('#evidence-dialog').showModal();
    try{
      const data=await api('/api/agent-control/runs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(name));
      $('#evidence-content').textContent=data.text;
      $('#evidence-meta').textContent=(data.truncated?'256 KB 미리보기 · 일부 생략':'원본 바이트 SHA-256 '+(data.sha256||'미수집'))+' · 표시 내용은 자격증명 마스킹 적용';
    }catch(e){$('#evidence-content').textContent=e.message;}
  }
  document.addEventListener('click',e=>{
    const run=e.target.closest('[data-run]');if(run){openRun(run.dataset.run);return;}
    const t=e.target.closest('[data-detail-tab]');if(t){tab=t.dataset.detailTab;renderDetail(current);$('.detail-body').scrollTop=0;return;}
    const f=e.target.closest('[data-artifact]');if(f){artifact(f.dataset.artifact);return;}
    const close=e.target.closest('[data-close]');if(close){$('#'+close.dataset.close).close();return;}
    const crew=e.target.closest('[data-worker-filter]');if(crew){$('#worker').value=$('#worker').value===crew.dataset.workerFilter?'':crew.dataset.workerFilter;filterChanged();return;}
    const ref=e.target.closest('[data-evidence-ref]');if(ref){
      const source=ref.dataset.evidenceRef;
      if(source.startsWith('tools.jsonl#')){tab='timeline';renderDetail(current);const node=[...document.querySelectorAll('[data-source]')].find(n=>n.dataset.source===source);if(node){node.querySelector('details').open=true;node.scrollIntoView({block:'nearest'});}}
      else if(current.artifacts.some(f=>f.name===source.split('#')[0]))artifact(source.split('#')[0]);
      else{tab='rework';renderDetail(current);}
      return;
    }
    if(e.target.closest('#export')&&current){const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=current.run.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    if(e.target.closest('#retry-detail'))openRun(selected,false);
  });
  function filterChanged(){
    const query=new URLSearchParams(location.search);if($('#worker').value)query.set('worker',$('#worker').value);else query.delete('worker');query.delete('run');history.replaceState(null,'','?'+query);
    initialSelection=null;selected=null;current=null;detailVersion++;
    $('#detail').innerHTML='<div class="empty detail-empty">필터에 맞는 실행을 선택하면 증거를 표시합니다.</div>';
    load();
  }
  $('#filters').onsubmit=e=>{e.preventDefault();filterChanged()};
  document.querySelectorAll('.filters select').forEach(s=>s.onchange=filterChanged);
  $('#search').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(filterChanged,300)};
  $('#refresh').onclick=()=>load();$('#more').onclick=()=>load(true);
  $('#pause').onclick=()=>{paused=!paused;$('#pause').setAttribute('aria-pressed',String(paused));$('#pause').textContent=paused?'자동 갱신 멈춤':'자동 갱신 켜짐'};
  $('#integration').onclick=()=>$('#integration-dialog').showModal();
  async function init(){
    try{const data=await api('/api/roster');roster=data.workers||[];
      $('#worker').innerHTML='<option value="">모든 에이전트</option>'+roster.map(w=>`<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');
      if(roster.some(w=>w.id===initialWorker))$('#worker').value=initialWorker;
      $('#crew-strip').innerHTML=roster.map(w=>`<button data-worker-filter="${esc(w.id)}" aria-pressed="false" title="${esc(w.name)} 기록"><span class="sprite" data-sprite="${esc(w.id)}"></span><span><b>${esc(w.name)}</b><small>${esc(w.runtime)} · ${esc(w.floor)}</small></span></button>`).join('');
      document.querySelectorAll('[data-sprite]').forEach(n=>n.append(createAgentSprite(roster.find(w=>w.id===n.dataset.sprite))));
    }catch(e){error('에이전트 명단을 읽지 못했습니다. 실행 ID로 기록을 표시합니다.');}
    await load();
    setInterval(()=>{if(!paused&&!document.hidden&&!loading)load()},15000);
  }
  init();
})();
