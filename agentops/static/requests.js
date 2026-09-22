(() => {
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const markdown = value => window.RequestMarkdown.render(value);
const labels = {queued:'접수 · 실행 대기',running:'진행 중',waiting_tasks:'협업 중',waiting_input:'답변 대기',waiting_approval:'변경 검토 대기',applying:'변경 적용 중',completed:'완료',blocked:'진행 보류',cancelled:'중지',superseded:'이전 요청 버전',proposed:'검토 대기',approved:'적용 대기',applied:'적용 완료',rollback_approved:'복구 대기',rolled_back:'복구 완료',failed:'실패'};
const badge = status => `<span class="status ${esc(status)}">${esc(labels[status] || status)}</span>`;
let key = '', current = null, signature = '', guide = null, refreshing = false, view = 'request', workers = [], rows = [], listMarkup = '';
const initialWorker = new URLSearchParams(location.search).get('worker');
const workerName = id => workers.find(w=>w.id===id)?.name || id || '에이전트';
const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const toolLabels = {disk_usage:'디스크 사용률 조회',infrastructure_read:'서비스·CPU·메모리 조회',firewall_read:'방화벽 조회',inventory_query:'자산 목록 조회',siem_search:'기간별 SIEM 경보 조회',env_read:'시설 상태 조회',log_read:'최근 보안 로그 조회',agent_activity:'에이전트 활동 조회',work_status:'업무 상태 조회'};
function notify(message, error=false) { $('notice').textContent=message; $('notice').className=error?'error':''; }
async function api(path, method='GET', body) {
 const response = await fetch('/api/'+path,{method,headers:{'X-API-Key':key,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 if(!response.ok){let data;try{data=await response.json()}catch{}throw new Error(data?.detail || `요청 실패 (${response.status})`)}
 return response.json();
}
async function action(fn, button) {if(button)button.disabled=true;try{await fn()}catch(e){notify(e.message,true)}finally{if(button)button.disabled=false}}
const time = stamp => stamp ? new Date(stamp*1000).toLocaleString('ko-KR') : '';
function showWork(mode=view){
 view=mode;$('work').hidden=false;$('guides').hidden=true;
 $('work-tab').classList.toggle('selected',view==='request');$('chat-tab').classList.toggle('selected',view==='conversation');$('guide-tab').classList.remove('selected');
 $('list-title').textContent=view==='conversation'?'대화 목록':'요청 목록';$('new-request').textContent=view==='conversation'?'＋ 새 대화':'＋ 새 요청';
 $('compose').hidden=!!current || view==='conversation';$('chat-compose').hidden=!!current || view!=='conversation';$('detail').hidden=!current;
 renderList();
}
function renderList(){
 const visible=rows.filter(r=>(r.mode||'request')===view);
 const html=visible.length?visible.map(r=>`<button class="request-item ${current===r.id?'active':''}" data-request="${esc(r.id)}">${r.worker?`<b>${esc(workerName(r.worker))}</b>`:''}${esc(r.title)}<small>${esc(labels[r.status]||r.status)} · ${r.sessions}/${r.budget} 세션</small></button>`).join(''):`<p class="empty">${view==='conversation'?'담당자에게 첫 메시지를 보내 보세요.':'첫 업무를 요청해 보세요.'}</p>`;
 if(html===listMarkup)return;listMarkup=html;$('list').innerHTML=html;
 $('list').querySelectorAll('[data-request]').forEach(b=>b.onclick=()=>openRequest(b.dataset.request));
}
function workerDescription(){const w=workers.find(w=>w.id===$('chat-worker').value);$('worker-description').textContent=w?`${w.floor} · ${w.security?.label||'담당 자산: '+(w.assets||[]).join(', ')} · 직무 밖 요청은 담당자를 안내합니다. 승인은 직무 안에서만 유효합니다.`:''}
function newRequest(mode=view){current=null;signature='';location.hash='';showWork(mode);action(refresh)}
function openRequest(id){current=id;signature='';$('detail').innerHTML='';showWork();$('compose').hidden=true;$('detail').hidden=false;location.hash=id;action(refresh);}
async function refresh(){
 if(!key || refreshing)return; refreshing=true;
 try{
  const data=await api('requests');rows=data.requests;
  const health=data.runner;
  $('runner').textContent=health && Date.now()/1000-health.at<30 && health.status==='running' ? `실행기 연결됨 · 작업 중 ${health.active_workers?.length||0}명` : '실행기 연결 확인 필요 · 접수한 업무는 보존됩니다';
  renderList();
  if(current&&$('guides').hidden){const id=current;const d=await api('requests/'+id);if(id!==current)return;const sig=JSON.stringify({...d,updated:0});if(sig!==signature){render(d);signature=sig}}
 }finally{refreshing=false}
}
function render(d){
 const chat=d.mode==='conversation';
 showWork(chat?'conversation':'request');
 const busy=chat&&['queued','running','waiting_tasks'].includes(d.status);
 const disabled=busy||d.status==='applying';
 const oldScroll=$('chat-messages')?.scrollTop||0;
 const stickToBottom=!$('chat-messages')||$('chat-messages').scrollHeight-$('chat-messages').clientHeight-oldScroll<60;
 const draft=$('reply-text')?.value||'';
 const hadFocus=document.activeElement?.id==='reply-text';
 const permissionRequests=(d.permission_requests||[]).filter(p=>p.revision===d.revision&&['pending','deferred'].includes(p.status));
 const permissionCards=permissionRequests.map(p=>`<section class="permission-card"><span class="eyebrow">${p.status==='deferred'?'보류한 권한 요청':'도구 사용 승인 필요'}</span><h3>${esc(workerName(p.worker))} · ${esc(toolLabels[p.tool]||p.tool)}</h3><p>이 기능은 실행 전에 사용자 허용이 필요합니다.</p><details><summary>요청한 작업과 범위 확인</summary><pre>${esc(JSON.stringify({기능:p.tool,필요권한:p.permission,요청내용:p.arguments},null,2))}</pre></details><div class="permission-actions">${[['once','이번만 허용'],['always','항상 허용'],['defer','요청 보류']].map(([value,label])=>`<button type="button" class="${value==='once'?'':'quiet'}" data-permission="${esc(p.id)}" data-decision="${value}" ${['queued','running','waiting_tasks','applying','cancelled'].includes(d.status)?'disabled':''}>${label}</button>`).join('')}</div><p class="hint">이번만: 표시된 호출 한 번 · 항상: 이 담당자의 해당 기능을 이후 대화·업무 요청에서도 허용. 허용하면 세션 예산 내에서 이어서 처리합니다.</p></section>`).join('');
 const tasks=d.tasks.map(t=>`<div class="task"><div class="section-head"><h3>${esc(t.title)}</h3>${badge(t.status)}</div><code>${esc(t.worker)} · ${esc(t.id)}${t.depends_on.length?' ← '+esc(t.depends_on.join(', ')):''}</code>${markdown(t.instructions)}${t.outcome?markdown(t.outcome.summary):''}${t.evidence?`<a href="${dcURL('agentcontrol')}?run=${encodeURIComponent(t.evidence.split('/').pop())}">실행 증거 확인 ↗</a>`:''}</div>`).join('');
 const changes=d.changes.map(c=>`<div class="task"><div class="section-head"><h3>${esc(c.summary)}</h3>${badge(c.status)}</div><p>${c.kind==='website'?'검증된 정적 파일을 기존 WAF 아래 /projects/ 경로에 배포합니다.':'지정 파라미터에 페이로드 문자열이 포함된 요청을 차단합니다.'}</p><details open><summary>검증 결과와 적용 내용</summary><pre>${esc(JSON.stringify({검증:c.tests,변경:c.metadata,결과:c.result,오류:c.error},null,2))}</pre></details><small>변경 버전 ${esc(c.sha256.slice(0,16))}</small><div class="change-actions">${c.status==='proposed'&&d.status==='waiting_approval'?`<button data-change="${esc(c.id)}" data-action="apply" data-hash="${esc(c.sha256)}">이 변경안 적용</button>`:''}${c.status==='applied'?`<button class="quiet" data-change="${esc(c.id)}" data-action="rollback" data-hash="${esc(c.sha256)}">이 변경 되돌리기</button>`:''}</div></div>`).join('');
 $('detail').innerHTML=`<section class="panel detail-card ${chat?'chat-card':''}"><div class="section-head"><span class="eyebrow">${chat?'근무자와 대화':esc(d.id)}</span>${badge(d.status)}</div><h2>${chat?esc(workerName(d.worker)):esc(d.title)}</h2>${chat?`<p class="chat-subject">${esc(d.title)}</p>`:''}<p class="hint">${esc(time(d.created))} · ${d.sessions}/${d.budget} 세션 배정 · ${d.scope==='read'?'조회 전용':'조회 · 파일 작성 · 변경안 준비'}</p>${d.result&&(!chat||d.result.reason==='budget')?`<section class="result-report"><h3 class="report-label">업무 결과</h3>${markdown(d.result.summary)}</section>${d.result.question?`<div class="result-question"><strong>확인할 내용</strong>${markdown(d.result.question)}</div>`:''}`:''}${permissionCards}<div class="conversation ${chat?'chat-messages':''}" ${chat?'id="chat-messages" aria-label="대화 내용"':''}>${d.messages.map(m=>{
 const task=d.tasks.find(t=>t.id===m.task),v=m.verification||task?.verification;
 return `<div class="message ${m.role==='user'?'from-user':'from-agent'}"><small>${m.role==='user'?'나':esc(workerName(m.worker))} · ${esc(time(m.at))}</small>${m.role==='user'?`<p>${esc(m.text)}</p>`:markdown(m.text)}${m.question?markdown(m.question):''}${chat&&m.role==='agent'?`<div class="message-evidence">${v?esc(v.observed_live_evidence?'자료 조회 · '+(v.tools||[]).map(t=>({disk_usage:'디스크 사용률',siem_search:'SIEM 경보',inventory_query:'자산 목록',env_read:'시설 상태',log_read:'최근 로그',infrastructure_read:'서비스 상태',firewall_read:'방화벽 상태',agent_activity:'에이전트 활동'}[t]||t)).join(', '):'대화 답변 · 이번 응답의 새 조회 근거 없음'):'조회 기록 확인 필요'}${task?.evidence?` · <a href="${dcURL('agentcontrol')}?run=${encodeURIComponent(task.evidence.split('/').pop())}">실행 기록 ↗</a>`:''}</div>`:''}</div>`;
 }).join('')}${busy?`<div class="chat-pending" role="status">${esc(workerName(d.worker))} · 답변 준비·조사 중입니다.</div>`:''}</div><form id="reply-form"><label>${chat?'메시지':'답변 · 추가 요청'}<textarea id="reply-text" rows="3" maxlength="16000" required placeholder="추가 정보 또는 수정할 내용을 적어 주세요." ${disabled?'disabled':''}>${esc(draft)}</textarea></label><div class="section-head"><button ${disabled?'disabled':''}>${chat?'보내기':'후속 요청 보내기'}</button>${!['completed','cancelled','applying'].includes(d.status)?'<button type="button" id="cancel" class="quiet">업무 중지</button>':''}</div><p class="hint">${chat?(busy?'답변이 도착하면 이어서 이야기할 수 있습니다.':'Enter로 보내기 · Shift+Enter로 줄바꿈 · 이전 대화를 기억하며 답합니다.'):'후속 요청은 새 회차로 처리합니다. 이전 회차의 미완료 작업과 미적용 변경안은 폐기하고, 기존 산출물은 유지합니다.'}</p></form></section><section class="panel detail-card">${chat?'<details><summary>조회 기록과 세션 예산</summary>':'<h2>작업 계획과 실행</h2>'}${tasks}<form id="budget-form" class="form-row"><label>전체 세션 예산<input id="new-budget" type="number" min="${Math.max(1,d.sessions)}" max="30" value="${d.budget}" required></label><button>예산 변경</button></form><p class="hint">배정된 세션을 합산한 상한입니다. 토큰 사용량은 실행 관제에서 확인하세요. 실패한 작업은 내용을 확인하고 후속 요청으로 재개합니다.</p>${chat?'</details>':''}</section><section class="panel detail-card" ${chat?'hidden':''}><h2>프로젝트 에이전트</h2>${d.agents.length?d.agents.map(a=>`<div class="agent"><b>${esc(a.name)}</b><small>${esc(a.runtime)} · ${a.state==='archived'?'업무 종료 · 보관':'활동'} · 요청 전용</small><details><summary>역할과 실행 기능</summary>${markdown(a.mission)}<small>${esc(a.capabilities.join(', '))}</small></details></div>`).join(''):'<p class="empty">기존 담당자가 처리합니다. 필요하면 한도 내에서 전문 역할을 추가합니다.</p>'}</section>${changes?`<section class="panel detail-card"><h2>변경 검토</h2>${changes}</section>`:''}<section class="panel detail-card" ${chat?'hidden':''}><h2>산출물</h2><div class="artifacts">${d.artifacts.length?d.artifacts.map(a=>`<button data-artifact="${esc(a.path)}">↓ ${esc(a.path)} · ${Math.ceil(a.size/1024)} KB</button>`).join(''):'<p class="empty">작성된 파일이 없습니다. 조회 결과는 대화와 실행 증거에 남습니다.</p>'}</div><details><summary>요청 처리 기록</summary><pre>${esc(JSON.stringify(d.events,null,2))}</pre></details></section>`;
 $('reply-form').onsubmit=e=>{e.preventDefault();action(async()=>{await api(`requests/${d.id}/reply`,'POST',{message:$('reply-text').value});$('reply-text').value='';signature='';await refresh()},e.submitter)};
 if($('cancel'))$('cancel').onclick=e=>action(async()=>{await api(`requests/${d.id}/cancel`,'POST');await refresh()},e.currentTarget);
 $('budget-form').onsubmit=e=>{e.preventDefault();action(async()=>{await api(`requests/${d.id}/budget`,'POST',{budget:Number($('new-budget').value)});await refresh();notify('세션 예산을 변경했습니다.')},e.submitter)};
 $('detail').querySelectorAll('[data-permission]').forEach(b=>b.onclick=()=>action(async()=>{
  await api(`requests/${d.id}/permissions/${b.dataset.permission}`,'POST',{decision:b.dataset.decision});signature='';await refresh();
  notify(b.dataset.decision==='defer'?'요청을 보류했습니다. 필요할 때 허용할 수 있습니다.':'허용했습니다. 담당자가 중단된 작업을 이어갑니다.');
 },b));
 $('detail').querySelectorAll('[data-change]').forEach(b=>b.onclick=()=>action(async()=>{await api(`requests/${d.id}/changes/${b.dataset.change}/${b.dataset.action}`,'POST',{sha256:b.dataset.hash});await refresh();notify('변경 작업을 접수했습니다. 검증 결과가 이 화면에 반영됩니다.')},b));
 $('detail').querySelectorAll('[data-artifact]').forEach(b=>b.onclick=()=>action(async()=>{const response=await fetch(`/api/requests/${d.id}/artifacts/${b.dataset.artifact.split('/').map(encodeURIComponent).join('/')}`,{headers:{'X-API-Key':key}});if(!response.ok)throw new Error('산출물을 내려받지 못했습니다');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=b.dataset.artifact.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)},b));
 if(chat){
  $('reply-text').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!e.repeat){e.preventDefault();if(!disabled)$('reply-form').requestSubmit($('reply-form').querySelector('button'))}};
  const box=$('chat-messages');box.scrollTop=stickToBottom?box.scrollHeight:oldScroll;
 }
 if(hadFocus&&!disabled)$('reply-text').focus();
}
$('login-form').onsubmit=e=>{e.preventDefault();key=$('key').value;action(async()=>{
 const data=await api('request-workers');workers=data.workers;
 $('chat-worker').innerHTML=workers.map(w=>`<option value="${esc(w.id)}">${esc(w.name)} · ${esc(w.floor)}</option>`).join('');
 $('chat-worker').value=workers.some(w=>w.id===initialWorker)?initialWorker:'soc-analyst';workerDescription();
 if(initialWorker)showWork('conversation');
 await refresh();$('login').hidden=true;$('app').hidden=false;$('key').value='';notify('업무 공간에 연결했습니다.');
 const id=location.hash.slice(1);if(/^req-[a-f0-9]{16}$/.test(id))openRequest(id);
},e.submitter)};
$('logout').onclick=()=>{$('permission-settings').hidden=true;$('permission-grants').innerHTML='';key='';current=null;signature='';guide=null;listMarkup='';rows=[];workers=[];$('detail').innerHTML='';$('guide-content').value='';$('guide-list').innerHTML='';$('list').innerHTML='';$('prompt').value='';$('chat-prompt').value='';$('app').hidden=true;$('login').hidden=false;notify('업무 공간을 잠갔습니다.')};
$('new-request').onclick=()=>newRequest();
$('chat-tab').onclick=()=>{if(view==='conversation')showWork();else newRequest('conversation')};
async function loadPermissions(){
 const data=await api('tool-permissions');
 $('permission-grants').innerHTML=data.grants.length?data.grants.map(g=>`<div class="permission-grant section-head"><div><b>${esc(workerName(g.worker))}</b><p>${esc(toolLabels[g.tool]||g.tool)}</p><small>${g.policy_current===false?'직무 정책 변경으로 효력 없음':esc(time(g.created))+'부터 현재 직무 범위에서 허용'}</small></div><button class="quiet" data-revoke="${esc(g.id)}">허용 철회</button></div>`).join(''):'<p class="empty">항상 허용한 기능이 없습니다.</p>';
 $('permission-grants').querySelectorAll('[data-revoke]').forEach(b=>b.onclick=()=>action(async()=>{await api('tool-permissions/'+b.dataset.revoke,'DELETE');await loadPermissions();notify('항상 허용을 철회했습니다. 다음 호출부터 다시 승인이 필요합니다.')},b));
}
$('permission-manage').onclick=()=>action(async()=>{await loadPermissions();$('permission-settings').hidden=false;$('permission-settings').scrollIntoView({block:'nearest'})});
$('permission-close').onclick=()=>{$('permission-settings').hidden=true};
$('chat-worker').onchange=workerDescription;
$('chat-example').onclick=()=>{$('chat-prompt').value='1시간 전부터 시스템이 좀 이상해. 로그 한번 봐줘.';$('chat-prompt').focus()};
$('chat-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 const r=await api('requests','POST',{mode:'conversation',worker:$('chat-worker').value,prompt:$('chat-prompt').value,budget:Number($('chat-budget').value),timezone:timezone()});
 $('chat-prompt').value='';openRequest(r.id);notify('담당자에게 메시지를 보냈습니다.');
},e.submitter)};
$('create-form').onsubmit=e=>{e.preventDefault();action(async()=>{const r=await api('requests','POST',{prompt:$('prompt').value,scope:$('scope').value,budget:Number($('budget').value),max_agents:Number($('agents').value),timezone:timezone()});$('prompt').value='';openRequest(r.id);notify('업무를 접수했습니다.')},e.submitter)};
document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>$('prompt').value=b.dataset.example);
$('work-tab').onclick=()=>{if(view==='request')showWork();else newRequest('request')};
async function loadGuide(name){guide=await api('request-guides/'+name.split('/').map(encodeURIComponent).join('/'));$('guide-name').textContent=guide.path;$('guide-content').value=guide.content;document.querySelectorAll('[data-guide]').forEach(b=>b.classList.toggle('active',b.dataset.guide===name))}
$('guide-tab').onclick=()=>action(async()=>{$('work').hidden=true;$('guides').hidden=false;$('guide-tab').classList.add('selected');$('work-tab').classList.remove('selected');$('chat-tab').classList.remove('selected');if(guide)return;const data=await api('request-guides');$('guide-list').innerHTML=data.files.map(name=>`<button data-guide="${esc(name)}">${esc(name)}</button>`).join('');$('guide-list').querySelectorAll('button').forEach(b=>b.onclick=()=>action(()=>loadGuide(b.dataset.guide)));await loadGuide('README.md')});
$('guide-form').onsubmit=e=>{e.preventDefault();action(async()=>{if(!guide)return;const r=await api('request-guides/'+guide.path.split('/').map(encodeURIComponent).join('/'),'PUT',{content:$('guide-content').value,sha256:guide.sha256});guide.sha256=r.sha256;notify('지침을 저장했습니다. 다음 업무 세션부터 반영됩니다.')},e.submitter)};
setInterval(()=>{if(key && !document.hidden)action(refresh)},5000);
})();
