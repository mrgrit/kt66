/* SIEM 집계와 현행 판정 원장을 구분한다. 키는 메모리에만 보관하며 자동 조치하지 않는다. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = v => v == null ? '—' : new Intl.NumberFormat('ko-KR').format(v);
  const date = (v, short=false) => !v ? '미수집' : new Date(typeof v==='number'?v*1000:v).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', ...(short?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{})});
  const status = v => ({open:'신규',acknowledged:'검토 중',resolved:'종결',false_positive:'오탐',reference:'참고 기록'}[v]||v||'미분류');
  const empty = message => '<div class="oc-empty">'+esc(message)+'</div>';
  let key='', data=null, view=document.body.dataset.initial||'xoc', generation=0, page=0, caseData=null, loading=false, queuedRefresh=false;
  const controllers=new Set();
  function message(value,bad=false){$('#message').textContent=value;$('#message').classList.toggle('error',bad)}
  function lock(){generation++;controllers.forEach(c=>c.abort());controllers.clear();key='';data=null;caseData=null;loading=false;queuedRefresh=false;$('#center-key').value='';$('#workspace').hidden=true;$('#auth-panel').hidden=false;$('#lock').hidden=true;$('#detail').close();['#metrics','#priority-list','#case-list','#detail-body','#pipeline','#holds','#trend','#distribution','#secondary','#policy','#feed-status','#coverage'].forEach(s=>$(s).replaceChildren());message('관제 세션을 잠갔습니다.')}
  async function api(path,body){
    const version=generation,controller=new AbortController();controllers.add(controller);let timedOut=false;
    const timeout=setTimeout(()=>{timedOut=true;controller.abort()},25000);
    try{
      const response=await fetch(path,{method:body?'POST':'GET',headers:{'x-api-key':key,'content-type':'application/json'},cache:'no-store',signal:controller.signal,...(body?{body:JSON.stringify(body)}:{})});
      const result=await response.json();if(version!==generation)throw new DOMException('Session changed','AbortError');
      if(!response.ok){if(response.status===401)lock();throw new Error(result.detail||'관제 조회 실패')}
      return result;
    } catch(e){if(timedOut)throw new Error('관제 서버 응답이 지연되고 있습니다.');throw e}
    finally {clearTimeout(timeout);controllers.delete(controller)}
  }
  function error(e){if(e.name!=='AbortError')message(e.message,true)}
  const metric=(label,value,note,hot=false)=>'<div class="oc-metric '+(hot?'hot':'')+'"><span>'+esc(label)+'</span><strong>'+number(value)+'</strong><small>'+esc(note)+'</small></div>';
  function bars(rows){
    if(!rows?.length)return empty('선택 기간에 집계된 자료가 없습니다.');
    const max=Math.max(...rows.map(r=>r.count),1);
    return '<div class="oc-bars">'+rows.map(r=>'<div class="oc-bar-row"><div><span title="'+esc(r.key)+'">'+esc(r.key)+'</span><b>'+number(r.count)+'</b></div><div class="oc-bar-track"><i style="width:'+Math.max(1,r.count/max*100)+'%"></i></div></div>').join('')+'</div>';
  }
  function chart(rows,soc){
    if(!rows?.length)return empty('이 기간의 시계열이 없습니다. 수집 상태를 확인하세요.');
    const w=680,h=155,left=42,right=12,top=10,bottom=27,max=Math.max(1,...rows.map(r=>r.count));
    const x=i=>left+i/Math.max(1,rows.length-1)*(w-left-right),y=n=>top+(1-n/max)*(h-top-bottom);
    const points=field=>rows.map((r,i)=>x(i).toFixed(1)+','+y(r[field]||0).toFixed(1)).join(' ');
    let svg='<svg class="oc-chart" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+(soc?'SIEM 경보 추세. 녹색은 전체, 주황색은 수준 12 이상.':'SIEM에 수집된 에이전트 이벤트 추세.')+'"><title>선택 기간 시간대별 기록 건수</title>';
    for(let i=0;i<3;i++){const value=Math.round(max*i/2),cy=y(value);svg+='<line class="grid" x1="'+left+'" x2="'+(w-right)+'" y1="'+cy+'" y2="'+cy+'"/><text x="'+(left-8)+'" y="'+(cy+3)+'" text-anchor="end">'+number(value)+'</text>'}
    svg+='<polygon class="area" points="'+left+','+y(0)+' '+points('count')+' '+x(rows.length-1)+','+y(0)+'"/><polyline class="series" points="'+points('count')+'"/>';
    if(soc)svg+='<polyline class="high-series" points="'+points('high')+'"/>';
    [...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])].forEach(i=>{svg+='<text x="'+x(i)+'" y="'+(h-4)+'" text-anchor="'+(i===0?'start':i===rows.length-1?'end':'middle')+'">'+esc(date(rows[i].at,true))+'</text>'});
    rows.forEach((r,i)=>{svg+='<circle cx="'+x(i)+'" cy="'+y(r.count)+'" r="5" fill="transparent"><title>'+esc(date(r.at)+' · '+number(r.count)+'건'+(soc?' / 수준 12 이상 '+number(r.high)+'건':''))+'</title></circle>'});
    return svg+'</svg>';
  }
  function feed(){
    const c=data.collector,a=data.agent,s=data.soc;
    $('#feed-status').innerHTML='<span class="'+(a.available?'good':'bad')+'">● 에이전트 SIEM '+(a.available?'연결됨':'조회 불가')+'</span><span class="'+(s.available?'good':'bad')+'">● 보안 SIEM '+(s.available?'연결됨':'조회 불가')+'</span><span class="'+(c.fresh&&c.state==='running'?'good':'warn')+'">● 수집기 '+esc(!c.fresh?'응답 지연':({running:'정상',backfill:'과거 증적 적재 중',degraded:'일부 수집 확인 필요'}[c.state]||c.state))+'</span><span>집계 '+esc(date(data.generated_at,true))+' KST</span>';
    $('#coverage').textContent='기간 '+date(data.start,true)+' ~ '+date(data.end,true)+' KST · 현행 미종결은 기간과 무관한 전체 원장 기준 · IP 수는 SIEM 고유값 추정치';
  }
  function render(){
    if(!data)return;
    $('#workspace').hidden=false;$('#auth-panel').hidden=true;$('#lock').hidden=false;feed();
    $('#overview').hidden=view==='cases';$('#case-view').hidden=view!=='cases';
    $$('.oc-tabs [data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));document.body.dataset.view=view;
    if(view==='cases')return;
    const soc=view==='soc', source=soc?data.soc:data.agent, x=data.xoc, c=data.collector, t=x.totals;
    $('#view-code').textContent=soc?'SECURITY OPERATIONS / SOC':'AI AGENT OPERATIONS / xOC';
    $('#view-title').textContent=soc?'인프라 보안의 현재 징후':'에이전트의 행동과 위험을 한눈에';
    $('#view-description').textContent=soc?'Wazuh 원본 경보에서 우선 조사할 징후와 반복 패턴을 추립니다.':'권한·품질·비용 이상을 검토하고, 반복되는 신호는 하나의 묶음으로 확인합니다.';
    $('#specialist-link').href=dcURL('requests')+'?worker='+(soc?'soc-analyst':'agent-supervisor');
    $('#metrics').innerHTML=soc?
      metric('SIEM 경보',source.available?source.total:null,'선택 기간 · 원본 경보')+metric('수준 12 이상',source.available?source.high:null,'우선 조사 기준',true)+metric('출발지 IP',source.available?source.sources:null,'srcip가 기록된 고유 IP')+metric('관측 자산',source.available?source.assets:null,'경보의 agent.id 기준')+metric('집계 시간',data.hours,'시간 · KST로 표시')+metric('xOC 실행 보류',t?.held,'현재 에이전트 통제 상태'):
      metric('미종결 사건',t?.open,'현재 전체 · 반복 포함')+metric('위험도 12 이상',t?.high,'가능성 × 영향 / 25',true)+metric('우선 검토 묶음',x.available?x.group_total:null,'담당자 × 탐지 규칙')+metric('도구 호출 기록',source.available?source.tools:null,'선택 기간 · 영수증')+metric('접근 거절',source.available?source.outcomes?.denied||0:null,'선택 기간 · 실행 진입 거절',true)+metric('실측 토큰',source.available&&source.measured_sessions?source.tokens:null,'측정된 '+number(source.measured_sessions||0)+' / '+number(source.sessions||0)+'개 실행 · 청구액 아님');
    $('#priority-title').textContent=soc?'수준 12 이상 · 우선 조사 경보':'반복을 묶은 우선 검토 사건';
    $('#all-cases').textContent=soc?'SIEM 전체 경보 ↗':'미종결 사건함 →';
    if(soc){
      $('#priority-list').innerHTML=!source.available?empty(source.error):source.priority?.length?source.priority.map((r,i)=>'<button class="oc-incident" data-alert="'+i+'"><span class="oc-risk high">'+esc(r.rule?.level)+'</span><span><strong>'+esc(r.rule?.description||'보안 경보')+'</strong><small>'+esc(r.data?.srcip||'출발지 미기록')+' → '+esc(r.agent?.name||r.location||'대상 미기록')+' · 규칙 '+esc(r.rule?.id)+'</small></span><span class="oc-repeat"><b>'+number(r.count||1)+'건</b><small>'+esc(date(r.timestamp,true))+'</small></span></button>').join(''):empty('이 기간에 수준 12 이상 경보가 없습니다. 경보 부재가 안전을 보장하지는 않습니다.');
      $('#priority-note').textContent='같은 규칙의 경보를 묶고 대표 증거를 표시합니다. 보안 사고 확정이나 대응 완료 여부는 SOC의 교차 검증이 필요합니다.';
      $$('[data-alert]').forEach(b=>b.onclick=()=>openDetail('soc',source.priority[+b.dataset.alert].id,source.priority[+b.dataset.alert].index));
    } else {
      $('#priority-list').innerHTML=!x.available?empty(x.error):x.groups.length?x.groups.map(g=>'<button class="oc-incident" data-finding="'+esc(g.finding_id)+'"><span class="oc-risk '+(g.risk>=12?'high':'')+'">'+number(g.risk)+'</span><span><strong>'+esc(g.title)+'</strong><small>'+esc(g.worker)+' · '+esc(g.rule)+' · 최근 '+esc(date(g.latest_at,true))+'</small></span><span class="oc-repeat"><b>'+number(g.count)+'건</b><small>대표 사건 →</small></span></button>').join(''):empty('현재 원장에 미종결 탐지 사건이 없습니다. 최근 검사 범위를 함께 확인하세요.');
      $('#priority-note').textContent='전체 '+number(x.group_total||0)+'묶음 중 우선순위 상위 8개. 묶음은 표시만 정리하며 개별 사건의 판정·증거는 그대로 남습니다.';
      $$('[data-finding]').forEach(b=>b.onclick=()=>openDetail('xoc',b.dataset.finding));
    }
    $('#collector-dot').style.background=c.fresh&&c.state==='running'?'var(--oc-accent)':'var(--oc-hot)';
    const pipe=(title,body,cls='')=>'<div class="oc-pipeline-row"><b class="'+cls+'">'+esc(title)+'</b><small>'+esc(body)+'</small></div>';
    $('#pipeline').innerHTML=pipe('01 / 증적 → SIEM',c.fresh?'전송 대기 '+number(c.pending)+'건 · 문서 오류 '+number(c.rejected)+'건':'수집기 응답이 늦습니다. 마지막 수집 기록을 확인하세요.',c.fresh&&!c.error?'':'warn')+
      pipe('02 / 탐지 검사',x.available?'최근 '+date(x.checked_at,true)+' · '+number(x.coverage?.checked_runs)+'개 실행 검사':'탐지 상태 미수집',!x.checked_at||Date.now()/1000-x.checked_at>1200?'warn':'')+
      pipe('03 / 실행 통제','현재 '+number(t?.held)+'명 기한부 보류 · 기존 직무와 도구 권한을 유지합니다.')+
      pipe('기록 출처',soc?'wazuh-alerts-* · 원본 보안 경보':'kt66-agent-events-* · 실행과 도구 영수증')+
      (c.source_error_count?pipe('원본 확인 필요',number(c.source_error_count)+'개 수집 오류 · 기존 증적과 대기열을 보존했습니다.','warn'):'');
    $('#holds').innerHTML=(x.holds||[]).map(h=>'<div class="oc-hold">'+esc(h.worker)+'<br>'+esc(date(h.until))+'까지 보류<br><button data-release="'+esc(h.worker)+'">근거 입력 후 해제</button></div>').join('');
    $$('[data-release]').forEach(b=>b.onclick=()=>release(b.dataset.release));
    $('#trend-title').textContent=soc?'보안 경보 발생 추세':'에이전트 이벤트 관측 추세';
    $('#trend-total').textContent=source.available?number(source.total)+'건':'미수집';
    $('#trend').innerHTML=source.available?chart(source.trend,soc):empty(source.error);
    $('#trend-note').textContent=soc?'전체 경보 / 주황선: 수준 12 이상 · 시간대는 KST':'시작·자기 보고·도구·결과 이벤트를 합산합니다. 세션 결과는 시작 시각에 집계하며, 과거 적재 중에는 건수가 증가할 수 있습니다.';
    $('#distribution-title').textContent=soc?'경보에 많이 등장한 출발지':'기록이 많은 에이전트';
    $('#distribution').innerHTML=source.available?bars(soc?source.origins:source.workers):empty('SIEM 연결 후 집계됩니다.');
    $('#secondary-title').textContent=soc?'많이 발생한 탐지 규칙':'사용한 도구 분포';
    $('#evidence-link').href=dcURL(soc?'siem':'agentcontrol');$('#evidence-link').textContent=soc?'SIEM 원본 →':'실행 증적 →';
    const rows=soc?source.rules:source.tool_names;
    $('#secondary').innerHTML=source.available&&rows?.length?rows.map(r=>'<div><b>'+number(r.count)+'<small> 건</small></b>'+esc(r.key)+'<span>'+esc(r.description||'도구 영수증 기준')+'</span></div>').join(''):empty('집계할 자료가 없습니다.');
    $('#policy').innerHTML='<p>미종결은 전체 xOC 원장 기준입니다. SIEM 이벤트·토큰·SOC 경보 통계에는 상단의 분석 기간이 적용됩니다. 경보·업무 보고서·세션 수는 서로 다른 단위입니다. 역할 변경이나 자동 대응은 이 조회 화면에서 발생하지 않습니다.</p><p>SIEM 내 전용 인덱스: <code>kt66-agent-events-*</code>, <code>kt66-agent-findings-v1</code>, <code>kt66-agent-tickets-v1</code>. 업무 보고서는 원문에 처리 상태가 없으면 참고 기록으로 분류합니다. 토큰은 CLI 실측이며 비용·청구액이 아닙니다.</p><p>전체 OS 동작이나 에이전트 내부 추론을 수집한 것은 아닙니다. 브로커 영수증, 런타임 기록과 에이전트 자기 보고를 구분합니다. 터미널 세션 도구의 상태 역시 별도의 보조 관측입니다.</p><table><thead><tr><th>규칙</th><th>탐지 대상</th><th>위험도</th></tr></thead><tbody>'+Object.entries(x.rules?.rules||{}).map(([id,r])=>'<tr><td>'+esc(id)+'</td><td>'+esc(r.title)+'</td><td>'+esc(r.likelihood)+' × '+esc(r.impact)+'</td></tr>').join('')+'</tbody></table>';
  }
  async function load(){
    if(!key)return;if(loading){queuedRefresh=true;return}loading=true;const version=generation;
    try{data=await api('/api/monitoring/summary?hours='+$('#hours').value);render();message('최근 집계 '+date(data.generated_at,true)+' KST · 수집기 및 SIEM 상태를 함께 확인하세요.');if(view==='cases')await loadCases()}
    catch(e){error(e);if(data){$('#feed-status').innerHTML='<span class="bad">● 갱신 실패 · 화면은 '+esc(date(data.generated_at))+'의 마지막 관측입니다.</span>'}}
    finally{if(version===generation){loading=false;if(queuedRefresh&&key){queuedRefresh=false;load()}}}
  }
  async function loadCases(){
    const kind=$('#case-kind').value;
    $('#case-status-label').hidden=kind!=='xoc';$('#case-domain-label').hidden=kind!=='tickets';
    $('#case-caption').textContent='기록을 조회하고 있습니다…';
    const parameters=new URLSearchParams({kind,status:$('#case-status').value,domain:$('#case-domain').value,q:$('#case-search').value,page:String(page)});
    try{
      const result=await api('/api/monitoring/cases?'+parameters);
      if(parameters.toString()!==new URLSearchParams({kind:$('#case-kind').value,status:$('#case-status').value,domain:$('#case-domain').value,q:$('#case-search').value,page:String(page)}).toString())return;
      caseData=result;$('#case-caption').textContent=number(result.total)+'건 · '+result.source;
      $('#case-list').innerHTML=result.rows.length?result.rows.map(r=>'<button class="oc-case-row" data-case="'+esc(kind==='xoc'?r.id:r.event_id)+'"><span class="badge '+(r.risk>=12?'high':'')+'">'+esc(kind==='xoc'?'위험 '+r.risk:'보고서')+'</span><span><strong>'+esc(r.title)+'</strong><small>'+esc(r.rule||r.domain)+' · '+esc(status(r.status))+'</small></span><span>'+esc(r.worker)+'</span><span><small>'+esc(date(r.created_at||r['@timestamp'],true))+'</small></span></button>').join(''):empty('조건에 맞는 기록이 없습니다.');
      $('#case-page').textContent=(page+1)+' / '+Math.max(1,Math.ceil(result.total/25));$('#case-prev').disabled=page===0;$('#case-next').disabled=(page+1)*25>=result.total||page>=399;
      $$('[data-case]').forEach(b=>b.onclick=()=>openDetail(kind==='xoc'?'xoc':'ticket',b.dataset.case));
    }catch(e){error(e);$('#case-caption').textContent='조회 실패 · 목록은 최신 상태가 아닙니다.';$('#case-list').replaceChildren();$('#case-next').disabled=true}
  }
  const fact=(name,value)=>'<div><small>'+esc(name)+'</small>'+esc(value??'미기록')+'</div>';
  async function openDetail(kind,id,index=''){
    try{
      const row=await api('/api/monitoring/detail?'+new URLSearchParams({kind,id,index}));
      $('#detail-title').textContent=row.title||row.rule?.description||'사건 상세';$('#detail-message').textContent='';
      if(kind==='xoc'){
        $('#detail-body').innerHTML='<div class="oc-detail-facts">'+fact('담당자',row.worker)+fact('상태',status(row.status))+fact('위험도',row.likelihood+' × '+row.impact+' = '+row.risk)+fact('최초 탐지',date(row.created_at))+'</div><p class="oc-reason">'+esc(row.detail)+'</p><p>'+esc(row.response)+'</p>'+(row.run_id?'<a href="'+esc(dcURL('agentcontrol')+'?run='+encodeURIComponent(row.run_id))+'">연결된 실행 증거 →</a>':'')+'<details><summary>증거 참조 · 해시</summary><pre>'+esc(JSON.stringify(row.evidence,null,2))+'</pre></details>'+(row.reason?'<p>최근 판정 근거: '+esc(row.reason)+'</p>':'')+'<label>이 사건의 판정·조치 근거<textarea id="reason" minlength="10" maxlength="3000" rows="3" placeholder="확인한 증거와 판단 이유, 복구 조건을 적어 주세요."></textarea></label><div class="actions"><button data-review="acknowledged">검토 중</button><button data-review="resolved">종결</button><button data-review="false_positive">오탐 판정</button><button id="hold-worker">대상 15분 보류</button></div><p class="oc-note">이 대표 사건 한 건만 판정합니다. 묶음 전체는 자동 종결하지 않습니다. 보류는 새 세션·다음 도구 호출에 적용됩니다.</p>';
        $$('[data-review]').forEach(b=>b.onclick=()=>act('/api/xoc/review',{finding_id:id,status:b.dataset.review,reason:$('#reason').value},b));
        $('#hold-worker').onclick=()=>act('/api/xoc/containment',{worker:row.worker,minutes:15,reason:$('#reason').value,finding_id:id},$('#hold-worker'));
      }else if(kind==='ticket'){
        $('#detail-body').innerHTML='<div class="oc-detail-facts">'+fact('작성자',row.worker)+fact('분류','참고 기록 · '+row.domain)+fact('시각',date(row['@timestamp']))+fact('출처',row.evidence_ref)+'</div>'+(row.run_id?'<a href="'+esc(dcURL('agentcontrol')+'?run='+encodeURIComponent(row.run_id))+'">실행 증거 확인 →</a>':'')+'<div class="oc-markdown">'+RequestMarkdown.render(row.body||'본문 미수집')+'</div>';
      }else{
        $('#detail-body').innerHTML='<div class="oc-detail-facts">'+fact('시각',date(row.timestamp))+fact('탐지 수준',row.rule?.level)+fact('출발지 IP',row.data?.srcip)+fact('관측 자산',row.agent?.name)+'</div><p class="oc-reason">원본 SIEM 경보입니다. 실제 침해 여부와 차단 필요성은 교차 검증 후 판단합니다.</p><pre>'+esc(JSON.stringify(row,null,2))+'</pre><a href="'+esc(dcURL('requests')+'?worker=soc-analyst')+'">SOC 분석가와 조사 →</a>';
      }
      $('#detail').showModal();
    }catch(e){error(e)}
  }
  async function act(path,body,button){
    if(!body.reason||body.reason.trim().length<10){$('#detail-message').textContent='판정 근거를 10자 이상 적어 주세요.';return}
    button.disabled=true;
    try{await api(path,body);$('#detail').close();await load()}catch(e){$('#detail-message').textContent=e.message}finally{button.disabled=false}
  }
  async function release(worker){
    $('#detail-title').textContent=worker+' · 보류 해제';$('#detail-message').textContent='';
    $('#detail-body').innerHTML='<p>재개해도 되는지 확인한 증거와 이유를 기록합니다.</p><label>해제 근거<textarea id="release-reason" minlength="10" maxlength="3000" rows="3"></textarea></label><button id="release-submit">보류 해제</button>';
    $('#release-submit').onclick=()=>act('/api/xoc/containment',{worker,minutes:0,reason:$('#release-reason').value},$('#release-submit'));$('#detail').showModal();
  }
  function setView(next){view=next;render();if(next==='cases')loadCases()}
  $('#unlock').onsubmit=async e=>{e.preventDefault();key=$('#center-key').value;$('#center-key').value='';await load()};
  $('#lock').onclick=lock;$('#close').onclick=()=>$('#detail').close();$('#refresh').onclick=load;
  $('#hours').onchange=load;$$('.oc-tabs [data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
  $('#all-cases').onclick=()=>view==='soc'?window.open(dcURL('siem'),'_blank','noopener'):setView('cases');
  $('#case-filters').onsubmit=e=>{e.preventDefault();page=0;loadCases()};
  ['#case-kind','#case-status','#case-domain'].forEach(s=>$(s).onchange=()=>{page=0;loadCases()});
  $('#case-prev').onclick=()=>{page=Math.max(0,page-1);loadCases()};$('#case-next').onclick=()=>{page++;loadCases()};
  $('#risk-map-link').href=dcURL('noc')+'?view=risk';
  setInterval(()=>{if(key&&!document.hidden&&view!=='cases'&&!$('#detail').open)load()},30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&key&&view!=='cases')load()});
})();
