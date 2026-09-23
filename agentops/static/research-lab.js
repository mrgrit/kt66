/* 전문가 업무 방식 실험실. GET/화면 전환은 모델을 호출하지 않는다. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stateLabel = s => ({draft:'초안',queued:'평가 대기',evaluating:'평가 중',evaluated:'평가 완료',evaluation_failed:'평가 실패',applied:'운영 적용',rolled_back:'복구 완료',archived:'보관'}[s] || s);
  const decision = s => ({benign:'정상·집계 완료',investigate:'정밀 조사',unknown:'판단 보류',refer:'담당자 이관',refuse:'부당한 실행 거절'}[s] || s || '미측정');
  const split = s => ({practice:'연습',holdout:'별도 검증',safety:'공통 안전'}[s] || s);
  const when = v => v ? new Date(v * 1000).toLocaleString('ko-KR') : '미측정';
  const fmt = v => v === null || v === undefined ? '미측정' : typeof v === 'number' ? v.toLocaleString('ko-KR') : String(v);
  const badge = (s, cls='') => `<span class="badge ${cls}">${esc(s)}</span>`;
  const note = text => `<div class="notice">${esc(text)}</div>`;
  const pre = text => `<pre class="source-block">${esc(typeof text === 'string' ? text : JSON.stringify(text,null,2))}</pre>`;
  const markdown = text => window.RequestMarkdown.render(text || '아직 연결된 비교 대상 스킬이 없습니다. 역할과 나머지 연결 스킬은 두 방법에 동일하게 제공됩니다.');
  let key = '', data = null, epoch = 0, loading = false, activeReport = null;
  const controllers = new Set();
  const message = (value, bad=false) => {$('#message').textContent=value;$('#message').classList.toggle('error',bad)};
  function lock() {
    epoch++; key='';data=null;activeReport=null;
    controllers.forEach(c=>c.abort());controllers.clear();
    $('#center-key').value='';$('#workspace').hidden=true;$('#lock').hidden=true;
    $('#example').disabled=$('#create').disabled=true;
    $$('dialog').forEach(d=>d.close());$('#candidate-form').reset();
    for(const id of ['cards','metrics','suites','detail-body','suite-body'])$('#'+id).replaceChildren();
    message('잠겼습니다. 강사 인증 후 실험 기록을 볼 수 있습니다.');
  }
  async function api(path, body) {
    const generation=epoch, controller=new AbortController();controllers.add(controller);
    const timer=setTimeout(()=>controller.abort(),20000);
    try {
      const response=await fetch('/api/research-lab'+path,{method:body===undefined?'GET':'POST',headers:{'x-api-key':key,'content-type':'application/json'},signal:controller.signal,...(body===undefined?{}:{body:JSON.stringify(body)})});
      const value=await response.json();
      if(generation!==epoch)throw new Error('잠금 상태가 변경되었습니다');
      if(!response.ok){if(response.status===401)lock();throw new Error(typeof value.detail==='string'?value.detail:'입력 내용을 확인하세요')}
      return value;
    } finally {clearTimeout(timer);controllers.delete(controller)}
  }
  const action = fn => async event => {try{await fn(event)}catch(e){const dialog=$('dialog[open]');if(dialog)dialog.querySelector('.form-error').textContent=e.message;message(e.message,true)}};
  function open(id){const d=$('#'+id);d.querySelector('.form-error').textContent='';if(!d.open)d.showModal()}
  $$('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());
  $('#lock').onclick=lock;
  $('#unlock').onsubmit=action(async e=>{e.preventDefault();epoch++;key=$('#center-key').value;$('#center-key').value='';await load();$('#lock').hidden=false});
  async function load(quiet=false) {
    if(loading)return;
    loading=true;
    try {data=await api('');render();if(!quiet)message('인증되었습니다. 평가 요청 시에만 격리 모델 세션 2회가 실행됩니다.')}finally{loading=false}
  }
  function render() {
    $('#workspace').hidden=false;$('#example').disabled=$('#create').disabled=false;
    const rows=data.candidates, selected=$('#filter').value;
    $('#filter').innerHTML='<option value="all">전체 상태</option>'+[...new Set(rows.map(c=>c.status))].map(s=>`<option value="${esc(s)}">${esc(stateLabel(s))}</option>`).join('');
    if([...$('#filter').options].some(o=>o.value===selected))$('#filter').value=selected;
    const metric=(name,value)=>`<div class="metric"><span>${esc(name)}</span><strong>${esc(value)}</strong></div>`;
    $('#metrics').innerHTML=metric('등록된 비교 실험',rows.length)+metric('평가 대기 · 진행',rows.filter(c=>['queued','evaluating'].includes(c.status)).length)+metric('검토 기준 통과',rows.filter(c=>c.evaluation?.gate_passed).length)+metric('현재 운영 적용',rows.filter(c=>c.status==='applied').length);
    $('#coverage').textContent=data.cost_note;$('#experiment-count').textContent=rows.length;
    const visible=rows.filter(c=>$('#filter').value==='all'||c.status===$('#filter').value);
    const worker=id=>data.workers.find(w=>w.id===id)?.name || id;
    $('#cards').innerHTML=visible.map(c=>{
      const ev=c.evaluation,a=ev?.baseline,b=ev?.candidate;
      return `<article class="experiment-card"><div>${badge(stateLabel(c.status))} ${ev?.gate_passed?badge('검토 기준 통과','pass'):''}</div><h3>${esc(c.name)}</h3><p>${esc(c.hypothesis)}</p><p class="card-foot">${esc(worker(c.target_worker))} · ${esc(c.suite_title||'기본 안전 회귀')}</p>${a&&b?`<div class="card-score"><div><small>기존 통과</small><br><strong>${a.grade.passed}/${a.grade.total}</strong></div><span>→</span><div><small>후보 통과</small><br><strong>${b.grade.passed}/${b.grade.total}</strong></div><div><small>후보 토큰</small><br><b>${esc(fmt(b.tokens))}</b></div></div>`:''}<div class="actions"><button data-report="${esc(c.id)}">${a&&b?'결과 비교 · 적용 검토':'실험 열기'}</button><small class="muted">${esc(when(c.created_at))}</small></div></article>`;
    }).join('') || '<div class="empty"><h3>첫 번째 업무 방식 실험을 시작하세요.</h3><p>SOC 예제에는 요청, 합성 로그, 평가 기준과 후보 스킬이 준비되어 있습니다.</p></div>';
    $$('[data-report]').forEach(b=>b.onclick=action(()=>report(b.dataset.report)));
    $('#suites').innerHTML=data.suites.map(s=>`<article class="experiment-card"><div>${badge(s.builtin?'기본 제공':'수업별 작성')} ${badge(s.role==='*'?'공통 안전':s.role)}</div><h3>${esc(s.title)}</h3><p>${esc(s.description)}</p><p class="card-foot">${s.case_count===null?'직무별 공통 회귀':`${s.case_count}개 직무 사례 · 별도 검증 ${s.holdout_count}개 · 공통 안전 추가`}</p><div class="actions">${s.id==='default'?'<span class="muted">모든 실험에 공통 안전 사례가 포함됩니다.</span>':`<button data-suite="${esc(s.id)}">사례 보기 · ${s.builtin?'복제':'편집'}</button>`}</div></article>`).join('');
    $$('[data-suite]').forEach(b=>b.onclick=action(()=>suite(b.dataset.suite)));
  }
  $('#filter').onchange=render;$('#refresh').onclick=action(()=>load());
  $$('[data-tab]').forEach(b=>b.onclick=()=>{
    $$('[data-tab]').forEach(t=>{const on=t===b;t.setAttribute('aria-selected',on);$('#'+t.dataset.tab+'-panel').hidden=!on});
  });
  function selectSuites(wanted) {
    const worker=data.workers.find(w=>w.id===$('#candidate-worker').value);
    const suites=data.suites.filter(s=>s.role==='*'||s.role===worker?.security_role);
    $('#candidate-suite').innerHTML=suites.map(s=>`<option value="${esc(s.id)}">${esc(s.title)}</option>`).join('');
    if(suites.some(s=>s.id===wanted))$('#candidate-suite').value=wanted;
    else if(suites.length>1)$('#candidate-suite').value=suites[1].id;
    suiteHint();
  }
  function suiteHint(){const s=data.suites.find(s=>s.id===$('#candidate-suite').value);$('#suite-hint').textContent=s?.description||''}
  $('#candidate-worker').onchange=()=>selectSuites();$('#candidate-suite').onchange=suiteHint;
  function candidate(values={}) {
    $('#candidate-form').reset();
    $('#candidate-worker').innerHTML=data.workers.map(w=>`<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');
    const form=$('#candidate-form');form.elements.target_worker.value=values.target_worker||'soc-analyst';
    selectSuites(values.suite_id);
    for(const field of ['name','hypothesis','content','sources'])form.elements[field].value=field==='sources'?(values.sources||[]).join('\n'):values[field]||'';
    if(!values.content)form.elements.content.value='---\nname: my-workflow\ndescription: 이 방법이 필요한 업무와 적용 조건\n---\n\n## 업무 절차\n\n## 판단 기준과 예외\n\n## 검증과 종료 조건\n';
    open('candidate-dialog');
  }
  $('#example').onclick=action(async()=>candidate(await api('/example')));$('#create').onclick=()=>candidate();
  $('#candidate-form').onsubmit=action(async e=>{
    e.preventDefault();const button=e.target.querySelector('[type=submit]');button.disabled=true;
    try {const values=Object.fromEntries(new FormData(e.target));values.sources=values.sources.split('\n').map(s=>s.trim()).filter(Boolean);const row=await api('/candidates',values);$('#candidate-dialog').close();await load(true);await report(row.id);message('실험 초안을 저장했습니다. 비교 기준이 고정되었으며 아직 모델을 호출하지 않았습니다.')}finally{button.disabled=false}
  });
  function answer(a) {
    if(!a)return '<p class="muted">평가 결과가 없습니다.</p>';
    const r=a.answer||{};
    const checks={decision:'판정',evidence:'근거 유효성',boundary:'권한 경계',findings:'대상 선정',measurements:'수치 집계'};
    return `<h4>${esc(decision(r.decision))} ${badge(a.passed?'통과':'검토 필요',a.passed?'pass':'fail')}</h4><p>${esc(r.summary||'이전 형식 결과에는 요약이 없습니다.')}</p><dl><dt>근거</dt><dd>${esc((r.evidence||[]).join(', ')||'없음')}</dd><dt>선정 대상</dt><dd>${esc((r.findings||[]).join(', ')||'없음')}</dd><dt>집계</dt><dd>${esc((r.measurements||[]).map(m=>m.name+' = '+fmt(m.value)).join(' · ')||'요청 없음·미측정')}</dd></dl><p class="checks">${Object.entries(a.checks||{}).map(([k,v])=>`${v?'✓':'✕'} ${esc(checks[k]||k)}`).join(' · ')}</p>`;
  }
  function compare(ev) {
    const a=ev?.baseline,b=ev?.candidate;if(!a||!b)return note(ev?.error||'평가 요청 후 이곳에서 기존·후보의 실제 결과를 비교할 수 있습니다.');
    const rows=[['사례 통과',`${a.grade.passed}/${a.grade.total}`,`${b.grade.passed}/${b.grade.total}`],
      ...[['판정 정확도','decision_accuracy'],['필수 근거 포함률','evidence_coverage'],['대상 선정 정밀도','finding_precision'],['대상 선정 재현율','finding_recall'],['집계 정확도','measurement_accuracy'],['별도 검증 통과율','holdout_pass_rate']].map(([label,k])=>[label,...[a,b].map(v=>v.grade.metrics?.[k]==null?'미측정':fmt(v.grade.metrics[k])+'%')]),
      ...[['오탐 대상 수','false_positives'],['누락 대상 수','missed_findings'],['금지 행동 선택','boundary_violations']].map(([label,k])=>[label,fmt(a.grade.metrics?.[k]),fmt(b.grade.metrics?.[k])]),
      ['실측 총 토큰',fmt(a.tokens),fmt(b.tokens)],['입력 캐시 토큰',fmt(a.usage?.cached_input_tokens),fmt(b.usage?.cached_input_tokens)],['소요 시간(초)',fmt(a.seconds),fmt(b.seconds)]];
    let html=`<div class="table-wrap"><table><thead><tr><th>비교 지표</th><th>기존 방법</th><th>후보 방법</th></tr></thead><tbody>${rows.map(r=>'<tr>'+r.map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('')}</tbody></table>`;
    html+=`<p>${badge(ev.gate_passed?'검토 기준 통과':'검토 기준 미통과',ev.gate_passed?'pass':'fail')} <span class="muted">${esc(ev.scope)}</span></p>`;
    if(ev.gate_reasons?.length)html+=`<ul>${ev.gate_reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`;
    html+='<h3>사례별로 달라진 판단</h3><div class="case-results">';
    const before=new Map((a.grade.cases||[]).map(c=>[c.id,c]));
    html+=(b.grade.cases||[]).map(c=>{const old=before.get(c.id);const label=old?.passed===c.passed?(c.passed?'통과 유지':'실패 유지'):c.passed?'개선':'회귀';return `<details class="case-result" ${label==='회귀'?'open':''}><summary><span class="split">${esc(split(c.split||'safety'))}</span><strong>${esc(c.title||c.id)}</strong>${badge(label,label==='개선'?'pass':label==='회귀'?'fail':'')}</summary><div class="answer-grid"><article><p class="eyebrow">기존 방법</p>${answer(old)}</article><article><p class="eyebrow">후보 방법</p>${answer(c)}</article></div></details>`}).join('');
    return html+'</div>';
  }
  async function report(cid) {
    const row=await api('/candidates/'+encodeURIComponent(cid));activeReport=row;
    $('#detail-title').textContent=row.name;
    const ev=row.evaluation;
    const stale=!row.configuration_current;
    $('#detail-body').innerHTML=`<div class="report-intro"><div><p>${badge(stateLabel(row.status))} ${badge(row.suite_title||'기본 안전 회귀')} ${stale?badge('현재 설정과 다름','fail'):badge('비교 기준 일치')}</p><p>${esc(row.hypothesis)}</p></div><div class="actions"><button id="clone-candidate">수정해 새 실험</button><button id="export-result">실험 기록 저장</button></div></div>${stale&&row.status!=='applied'?note('설정 또는 평가 세트가 변경되었습니다. 당시의 결과는 보존되며 새 실험으로 다시 검증해야 합니다.'):''}${compare(ev)}
      <details><summary>어떤 업무 절차를 바꿨나요?</summary><div class="method-grid"><article><h3>기존 비교 대상 스킬</h3>${markdown(row.baseline.content)}</article><article><h3>후보 스킬</h3>${markdown(row.content)}</article></div></details>
      <details><summary>고정 입력 · 기준 버전 · 당시 결과</summary><p class="muted">정답은 평가 모델에 전달하지 않습니다. 이 인증된 강사 화면과 다운로드에는 정답이 포함됩니다.</p><p class="hash">사례 ${esc(row.suite_snapshot?.sha256||'이전 기록')}<br>후보 ${esc(row.sha256)}<br>모델 ${esc(row.protocol?.model?.name||'이전 기록')} · ${esc(row.protocol?.model?.reasoning_effort||'기본 추론 등급')}</p>${pre(row.suite_snapshot?.cases||[])}<details><summary>공통 역할·연결 스킬 사본</summary>${pre({persona:row.baseline.persona,skills:row.baseline.context_skills||{}})}</details>${ev?`<details><summary>구조화된 원본 결과</summary>${pre(ev)}</details>`:''}</details>
      <div class="review-area"><h3>다음 단계</h3><p class="muted">평가 요청은 고정된 모델로 2개 세션을 실행합니다. 실제 환경의 업무 성공·재작업률·청구액은 별도 검증 대상입니다.</p><div class="actions"><button id="evaluate" class="primary" ${['draft','evaluation_failed'].includes(row.status)&&!stale?'':'disabled'}>격리 A/B 평가 요청 · 모델 2회</button>${['queued','evaluating'].includes(row.status)?'<button id="reload-report">진행 상태 새로고침</button>':''}</div>
      <label style="margin-top:16px">운영 적용·복구 검토 근거<textarea id="reason" rows="3" minlength="10" placeholder="사례별 결과, 운영 영향과 복구 기준을 10자 이상 기록하세요."></textarea></label><div class="actions"><button id="apply" ${row.status==='evaluated'&&ev?.gate_passed&&!stale?'':'disabled'}>강사 검토 후 운영 적용</button><button id="rollback" ${row.status==='applied'?'':'disabled'}>기존 스킬로 복구</button></div></div><details><summary>실험 이력</summary>${pre(row.history)}</details>`;
    if(['draft','evaluated','evaluation_failed','rolled_back'].includes(row.status)){
      const archive=document.createElement('button');archive.textContent='실험 보관';archive.id='archive';
      archive.onclick=action(async()=>{await api('/candidates/'+cid+'/archive',{});await load(true);await report(cid)});
      $('#detail-body .report-intro .actions').append(archive);
    }
    $('#clone-candidate').onclick=()=>{ $('#detail').close();candidate(row) };
    $('#export-result').onclick=()=>{const blob=new Blob([JSON.stringify(activeReport,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`kt66-experiment-${row.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
    if($('#reload-report'))$('#reload-report').onclick=action(()=>report(cid));
    for(const operation of ['evaluate','apply','rollback'])$('#'+operation).onclick=action(async()=>{
      const button=$('#'+operation);button.disabled=true;
      try {await api('/candidates/'+cid+'/'+operation,{reason:$('#reason').value});await load(true);await report(cid);message(operation==='evaluate'?'평가를 대기열에 등록했습니다. 실행기가 처리하며 진행 상태 새로고침으로 확인할 수 있습니다.':'변경 처리 결과를 저장했습니다.')}catch(e){button.disabled=false;throw e}
    });
    open('detail');
  }
  async function suite(sid) {
    const row=await api('/suites/'+encodeURIComponent(sid));$('#suite-title').textContent=row.title;
    $('#suite-body').innerHTML=`<p>${esc(row.description)}</p><p>${badge(row.builtin?'기본 제공 · 복제해서 편집':'수업별 평가 세트')} ${badge('별도 검증 '+row.holdout_count+'개')}</p><details open><summary>자연어 요청과 관찰 자료</summary>${row.config.cases.map(c=>`<article class="suite-case"><h3>${esc(c.title)} <span class="split">${esc(split(c.split))}</span></h3><p>${esc(c.input)}</p><ul>${c.observations.map(o=>`<li><b>${esc(o.id)}</b> · ${esc(o.result)}</li>`).join('')}</ul><details><summary>강사용 판정 기준</summary><p>판정: ${esc(decision(c.expected))} · 필수 근거: ${esc(c.required_evidence.join(', '))}</p>${pre({대상:c.expected_findings??'미평가',수치:c.expected_values??'미평가'})}</details></article>`).join('')}</details><form id="suite-form"><h3>${row.builtin?'복제해 수업에 맞게 바꾸기':'평가 세트 편집'}</h3><label>평가 세트 ID<input id="suite-id" name="id" pattern="[a-z][a-z0-9-]{0,63}" value="${esc(row.builtin?row.id+'-class':row.id)}" required></label><label>요청 · 관찰 자료 · 정답 YAML<textarea class="suite-editor" name="yaml" spellcheck="false" required>${esc(row.yaml)}</textarea></label><p class="muted">연습과 별도 검증 사례를 모두 넣으세요. 저장 이후의 새 실험에 반영되며, 기존 실험의 입력 사본은 유지됩니다.</p><div class="actions"><button type="submit" class="primary">${row.builtin?'새 평가 세트로 저장':'변경 저장'}</button>${row.builtin?'':'<button type="button" id="delete-suite">평가 세트 삭제</button>'}</div></form>`;
    $('#suite-form').onsubmit=action(async e=>{e.preventDefault();const button=e.target.querySelector('[type=submit]');button.disabled=true;try{const body=Object.fromEntries(new FormData(e.target));await api('/suites/'+encodeURIComponent(body.id),{yaml:body.yaml,revision:!row.builtin&&row.id===body.id?row.revision:null});$('#suite-dialog').close();await load(true);message('평가 세트를 저장했습니다. 새 실험에서 선택할 수 있습니다.')}finally{button.disabled=false}});
    if($('#delete-suite'))$('#delete-suite').onclick=action(async()=>{if(!confirm('이 평가 세트를 삭제할까요? 기존 실험의 입력 사본과 결과는 보존됩니다.'))return;await api('/suites/'+sid+'/delete',{revision:row.revision});$('#suite-dialog').close();await load(true);message('평가 세트를 삭제했습니다. 기존 실험 기록은 보존되었습니다.')});
    open('suite-dialog');
  }
  $('#research-chat').href=dcURL('agentops')+'requests?worker=skill-researcher';$('#evaluator-chat').href=dcURL('agentops')+'requests?worker=skill-evaluator';
  setInterval(()=>{if(key&&!document.hidden&&!$('dialog[open]')&&data?.candidates.some(c=>['queued','evaluating'].includes(c.status)))load(true).catch(e=>message(e.message,true))},15000);
})();
