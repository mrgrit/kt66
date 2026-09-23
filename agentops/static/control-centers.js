/* 키는 메모리에만 두며 모든 민감 조회·변경은 서버에서 다시 인증한다. */
(() => {
  const $ = s => document.querySelector(s), center = document.body.dataset.console;
  const xoc = center === 'xoc', base = xoc ? '/api/xoc' : '/api/research-lab';
  let data = null, key = '';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const when = v => v ? new Date(v * 1000).toLocaleString('ko-KR') : '아직 수집 전';
  const status = s => ({open:'신규',acknowledged:'검토 중',resolved:'종결',false_positive:'오탐',draft:'초안',queued:'평가 대기',evaluating:'평가 중',evaluated:'평가 완료',evaluation_failed:'평가 실패',applied:'운영 적용',rolled_back:'복구 완료'}[s] || s);
  const message = (text, bad = false) => {$('#message').textContent = text;$('#message').classList.toggle('error',bad)};
  async function api(path, body) {
    const response = await fetch(path, {method:body ? 'POST' : 'GET', headers:{'x-api-key':key,'content-type':'application/json'}, ...(body ? {body:JSON.stringify(body)} : {})});
    const result = await response.json();
    if (!response.ok) {if(response.status===401){data=null;$('#workspace').hidden=true;$('#detail').close()}throw new Error(result.detail || '요청 실패')}
    return result;
  }
  const act = fn => async () => {try {await fn()} catch(e){message(e.message,true)}};
  const metric = (title,value) => '<div class="metric">'+esc(title)+'<b>'+esc(value)+'</b></div>';
  const link = (url,label) => /^https:\/\//.test(url) ? '<a target="_blank" rel="noopener noreferrer" href="'+esc(url)+'">'+esc(label)+'</a>' : esc(label);
  const pre = obj => '<pre>'+esc(typeof obj==='string'?obj:JSON.stringify(obj,null,2))+'</pre>';
  function items() {return xoc ? data.findings : data.candidates}
  function render() {
    $('#workspace').hidden=false;$('#list-title').textContent=xoc?'탐지 사건':'스킬 후보 · 평가 · 적용';
    const rows=items(), chosen=$('#filter').value;
    $('#filter').innerHTML='<option value="all">전체 상태</option>'+[...new Set(rows.map(r=>r.status))].map(s=>'<option value="'+esc(s)+'">'+esc(status(s))+'</option>').join('');
    if([...$('#filter').options].some(o=>o.value===chosen))$('#filter').value=chosen;
    const visible=rows.filter(r=>$('#filter').value==='all'||r.status===$('#filter').value);
    if(xoc) {
      $('#metrics').innerHTML=metric('미종결',rows.filter(f=>['open','acknowledged'].includes(f.status)).length)+metric('높은 위험 · 12 이상',rows.filter(f=>f.risk>=12&&['open','acknowledged'].includes(f.status)).length)+metric('기한부 보류',data.holds.length)+metric('검사한 실행',data.coverage?.checked_runs??'—');
      $('#coverage').textContent='최근 검사: '+when(data.checked_at)+' · 최근 '+(data.coverage?.lookback_hours??72)+'시간 / 최대 '+(data.coverage?.scan_limit??300)+'개 실행. '+(data.coverage?.limited?'검사 상한 밖의 실행이 있습니다. ':'')+'발견 '+data.findings_total+'건 중 최대 '+data.display_limit+'건 표시. 위험도는 가능성 × 영향(1~25)이며 침해 확정 점수가 아닙니다.';
      $('#holds').innerHTML=data.holds.map(h=>'<div class="hold">'+esc(h.worker)+' · '+esc(when(h.until))+'까지 보류 · '+esc(h.reason)+'<button data-release="'+esc(h.worker)+'">보류 해제</button></div>').join('');
      $('#cards').innerHTML=visible.map(f=>'<article class="card"><span class="badge '+(f.risk>=12?'high':'')+'">위험 '+f.risk+'</span><span class="badge">'+esc(status(f.status))+'</span><h3>'+esc(f.title)+'</h3><p>'+esc(f.worker)+' · '+esc(f.rule)+'</p><p>'+esc(f.detail)+'</p><button data-detail="'+esc(f.id)+'">근거·판정·조치</button></article>').join('');
      $('#policy').innerHTML='<table><thead><tr><th>규칙</th><th>가능성 × 영향</th><th>대응 원칙</th></tr></thead><tbody>'+Object.entries(data.rules.rules).map(([id,r])=>'<tr><td>'+esc(id)+'<br>'+esc(r.title)+'</td><td>'+r.likelihood+' × '+r.impact+'</td><td>'+esc(r.response)+'</td></tr>').join('')+'</tbody></table>';
      document.querySelectorAll('[data-release]').forEach(b=>b.onclick=act(async()=>{const reason=prompt('보류 해제의 검증 근거 (10자 이상)');if(reason){await api('/api/xoc/containment',{worker:b.dataset.release,minutes:0,reason});await load()}}));
    } else {
      $('#metrics').innerHTML=metric('후보',rows.length)+metric('평가 대기·진행',rows.filter(c=>['queued','evaluating'].includes(c.status)).length)+metric('게이트 통과',rows.filter(c=>c.evaluation?.gate_passed).length)+metric('운영 적용',rows.filter(c=>c.status==='applied').length);
      $('#coverage').textContent=data.cost_note+' '+data.evaluation_note;
      $('#cards').innerHTML=visible.map(c=>'<article class="card"><span class="badge">'+esc(status(c.status))+'</span><h3>'+esc(c.name)+'</h3><p>'+esc(c.target_worker)+' · '+esc(when(c.created_at))+'</p><p>'+esc(c.hypothesis)+'</p><button data-detail="'+esc(c.id)+'">초안·평가·적용 검토</button></article>').join('');
      $('#candidate-worker').innerHTML=data.workers.map(w=>'<option value="'+esc(w.id)+'">'+esc(w.name)+'</option>').join('');
      $('#policy').innerHTML='<p>'+esc(data.sources.purpose)+'</p>'+data.sources.sources.map(s=>'<p>'+link(s.url,s.id)+' · '+esc(s.focus)+'</p>').join('')+'<ul>'+data.sources.rules.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>';
    }
    if(!visible.length)$('#cards').innerHTML='<p class="empty">'+(xoc?'이 범위에 표시할 탐지 사건이 없습니다. 수집 범위·최근 검사 시각을 함께 확인하세요.':'아직 후보가 없습니다. 연구원에게 개선을 요청하거나 초안을 등록하세요.')+'</p>';
    document.querySelectorAll('[data-detail]').forEach(b=>b.onclick=()=>detail(rows.find(r=>r.id===b.dataset.detail)));
  }
  function detail(row) {
    $('#detail-title').textContent=xoc?row.title:row.name;
    if(xoc) {
      $('#detail-body').innerHTML='<p><span class="badge">'+esc(status(row.status))+'</span>'+esc(row.worker)+' · 위험 '+row.likelihood+' × '+row.impact+'</p><p>'+esc(row.detail)+'</p><p>'+esc(row.response)+'</p><p><a href="'+esc(row.run_id?dcURL('agentcontrol')+'?run='+encodeURIComponent(row.run_id):dcURL('agentops'))+'">실행 증적 관제 화면 →</a></p><details><summary>증거 파일 SHA-256 · 출처</summary>'+pre(row.evidence)+'<p>'+esc(row.source)+' / 규칙 v'+esc(row.rule_version)+'</p></details>'+(row.reason?'<p>이전 판정: '+esc(row.reason)+'</p>':'')+'<label>판정·조치 근거<textarea id="reason" minlength="10" rows="3" placeholder="확인한 증거와 판단 이유, 복구 조건을 적어 주세요."></textarea></label><div class="actions"><button data-review="acknowledged">검토 중</button><button data-review="resolved">근거 확인 후 종결</button><button data-review="false_positive">오탐 판정</button><button id="hold-worker">대상 15분 보류</button></div><p class="muted">보류는 새 세션·다음 도구 호출에 적용됩니다. 진행 중 추론·원격 작업을 강제 종료하지 않습니다.</p>';
      document.querySelectorAll('[data-review]').forEach(b=>b.onclick=act(async()=>{await api('/api/xoc/review',{finding_id:row.id,status:b.dataset.review,reason:$('#reason').value});$('#detail').close();await load()}));
      $('#hold-worker').onclick=act(async()=>{await api('/api/xoc/containment',{worker:row.worker,minutes:15,reason:$('#reason').value,finding_id:row.id});$('#detail').close();await load()});
    } else {
      const evaluation=row.evaluation, results=evaluation?.baseline&&evaluation?.candidate;
      let report=results?'<table><thead><tr><th>실측</th><th>기존</th><th>후보</th></tr></thead><tbody>'+[['사례 통과',evaluation.baseline.grade.passed+'/'+evaluation.baseline.grade.total,evaluation.candidate.grade.passed+'/'+evaluation.candidate.grade.total],['총 토큰',evaluation.baseline.tokens,evaluation.candidate.tokens],['입력 캐시',evaluation.baseline.usage?.cached_input_tokens,evaluation.candidate.usage?.cached_input_tokens],['소요 초',evaluation.baseline.seconds,evaluation.candidate.seconds]].map(r=>'<tr>'+r.map(v=>'<td>'+esc(v??'미측정')+'</td>').join('')+'</tr>').join('')+'</tbody></table><p>적용 게이트: '+(evaluation.gate_passed?'통과':'미통과')+'</p><details><summary>사례별 결과·모델·실측 원문</summary>'+pre(evaluation)+'</details>':'<p class="muted">'+esc(evaluation?.error||'아직 평가 결과가 없습니다. 평가 요청 시 Codex가 두 번 실행됩니다.')+'</p>';
      $('#detail-body').innerHTML='<p>'+esc(row.hypothesis)+'</p><p>작성: '+esc(row.author)+' · 대상: '+esc(row.target_worker)+'</p><p>'+row.sources.map(s=>link(s,s)).join('<br>')+'</p><details><summary>후보 SKILL.md · '+esc(row.sha256.slice(0,12))+'</summary>'+pre(row.content)+'</details><details><summary>기존 스킬과 역할</summary>'+pre(row.baseline.content||'신규 스킬')+pre(row.baseline.persona)+'</details>'+report+'<label>적용/복구 검토 이유<textarea id="reason" minlength="10" rows="3"></textarea></label><div class="actions"><button id="evaluate" '+(!['draft','evaluation_failed'].includes(row.status)?'disabled':'')+'>격리 A/B 평가 요청 · 2회</button><button id="apply" '+(!(row.status==='evaluated'&&evaluation?.gate_passed)?'disabled':'')+'>검토한 후보 적용</button><button id="rollback" '+(row.status!=='applied'?'disabled':'')+'>적용 전 상태로 복구</button></div><details><summary>변경 이력</summary>'+pre(row.history)+'</details>';
      ['evaluate','apply','rollback'].forEach(action=>$('#'+action).onclick=act(async()=>{const b=$('#'+action);b.disabled=true;try{await api('/api/research-lab/candidates/'+row.id+'/'+action,{reason:$('#reason').value});$('#detail').close();await load()}finally{b.disabled=false}}));
    }
    $('#detail').showModal();
  }
  async function load(){data=await api(base);render();message('운영 기록을 불러왔습니다. 화면 조회로 AI 세션이 실행되지는 않습니다.')}
  $('#unlock').onsubmit=async e=>{e.preventDefault();key=$('#center-key').value;try{await load()}catch(err){message(err.message,true)}};
  $('#refresh').onclick=act(load);$('#filter').onchange=render;$('#close').onclick=()=>$('#detail').close();
  $('#create').onclick=()=>$('#candidate-dialog').showModal();$('#candidate-close').onclick=()=>$('#candidate-dialog').close();
  $('#candidate-form').onsubmit=async e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.target));values.sources=values.sources.split('\n').map(s=>s.trim()).filter(Boolean);try{await api('/api/research-lab/candidates',values);$('#candidate-dialog').close();e.target.reset();await load()}catch(err){message(err.message,true)}};
  if($('#soc-chat'))$('#soc-chat').href=dcURL('requests')+'?worker=soc-analyst';
  if($('#research-chat'))$('#research-chat').href=dcURL('requests')+'?worker=skill-researcher';
})();
