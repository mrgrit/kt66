/* Presentation of backend readings. History contains only samples actually
   received during this visit; it is never seeded with illustrative values. */
const observations = [];
const safeText = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const metricNumber = (value,digits=1) => Number.isFinite(value) ? value.toFixed(digits) : '—';
function recordObservation() {
  const p=ST?.power;
  if(!p || !Number.isFinite(p.total_kw) || observations.at(-1)?.ts===ST.ts)return;
  observations.push({ts:ST.ts,at:Date.now(),kw:p.total_kw});
  if(observations.length>80)observations.shift();
}
function renderMetrics() {
  if(!ST?.power)return;
  const p=ST.power,temps=Object.values(ST.aisles || {}),hot=temps.length?Math.max(...temps.map(a=>a.temp_c)):null;
  const alarms=ST.alarms || [],crit=alarms.filter(a=>a.level>=12).length;
  const assets=LAYOUT.it_assets || [],up=assets.filter(alive).length,pue=ST.efficiency?.pue,target=LAYOUT.building?.pue_target;
  const gauge=(key,value,unit,note,icon,cls='',fill=null)=>`<article class="gauge ${cls}"><span class="g-k">${key}${dcIcon(icon)}</span><span class="g-v">${value}<small>${unit}</small></span><span class="g-s">${safeText(note)}</span>${fill==null?'':`<i class="g-bar" style="width:${Math.max(0,Math.min(fill*100,100))}%"></i>`}</article>`;
  const r=p.total_kw/p.rated_kw;
  $('#gauges').innerHTML=
    gauge('IT 전력 부하',metricNumber(p.total_kw),'kW',`정격 ${p.rated_kw} kW · 환산값`,'activity',r>.9?'crit':r>.75?'warn':'',r)
    +gauge('UPS 배터리',metricNumber(p.ups_charge_pct,0),'%',p.on_battery?`배터리 공급 · 잔여 ${p.ups_runtime_min}분`:p.generator_running?'발전기 전원 공급':'상용전원 공급 중','shield',p.on_battery?'crit':'',p.ups_charge_pct/100)
    +gauge('최고 아일 온도',metricNumber(hot),'°C','운영 기준 18–27°C','activity',hot>32?'crit':hot>27?'warn':'',hot==null?0:(hot-16)/26)
    +gauge('전력 사용 효율',metricNumber(pue,2),'PUE',Number.isFinite(target)?`목표 ${target.toFixed(2)} · 환경 모델`:'환경 모델 계산값','model',pue>target?'warn':'')
    +gauge('가동 자산',String(up),`/ ${assets.length}`,'컨테이너 · 원격 접속 응답','server',up<assets.length?'warn':'',up/Math.max(assets.length,1))
    +gauge('활성 경보',String(alarms.length),'건',crit?`심각 경보 ${crit}건`:alarms.length?'확인이 필요한 경보가 있습니다':'현재 활성 경보 없음','shield',crit?'crit':alarms.length?'warn':'');
  $('#bld-name').textContent=ST.building || 'kt66 데이터센터';
  $('#tsbadge').hidden=(ST.time_scale ?? 1)===1;$('#tsbadge').textContent=`시뮬레이션 ×${ST.time_scale}`;
  $('#netbadge').hidden=ST.netglue!==false;
  $('#stage-body').classList.toggle('crit',crit>0);
  $('#tab-alarm-n').hidden=!alarms.length;$('#tab-alarm-n').textContent=alarms.length;
  $('#rail-status').textContent=alarms.length?`경보 ${alarms.length}`:'경보 없음';$('#rail-status').classList.toggle('bad',alarms.length>0);
}
function renderOperations() {
  if(!ST)return;
  $('#pane-overview').innerHTML=floors().slice().reverse().map(f=>{
    const assets=assetsOf(f.id),kw=assets.reduce((n,a)=>n+assetState(a.id).kw,0),temp=floorTemp(f.id);
    const bad=floorAlarms(f.id).length>0 || assets.some(assetDown);
    return `<button class="floor-status" data-open-floor="${safeText(f.id)}"><span class="fh"><span class="floor-no">${safeText(f.id)}</span><span class="floor-name">${safeText(f.name)}</span><i class="floor-signal ${bad?'bad':''}" aria-label="${bad?'이상':'활성 경보 없음'}"></i></span><span class="fd-data"><span><b>${metricNumber(kw)}</b> kW</span><span><b>${temp==null?'—':metricNumber(temp)}</b> ${temp==null?'센서 없음':'°C'}</span><span>${assets.length} assets</span></span></button>`;
  }).join('')+`<div class="network-mini"><h4>서비스 진입 경로</h4><div class="network-chain">${['fw','ips','web'].map((id,i)=>{
    const a=LAYOUT.it_assets.find(a=>a.id===id);
    return `${i?'<span>→</span>':''}<button data-network-asset="${id}" class="${a&&!alive(a)?'off':''}" ${a?'':'disabled'}>${{fw:'FW',ips:'IPS',web:'WAF'}[id]}</button>`;
  }).join('')}<span>→</span><button data-open-zone="int">APP</button></div><p class="network-note">${ST.netglue===false?'웹 진입 경로 점검 필요':ST.netglue===true?'웹 진입 경로 응답 확인':'웹 진입 경로 확인 중'} · 근무자 ${ROSTER.workers.length}명</p></div>`;
  $$('[data-open-floor]').forEach(b=>b.onclick=()=>enterFloor(b.dataset.openFloor));
  $$('[data-network-asset]').forEach(b=>b.onclick=()=>openAsset(b.dataset.networkAsset));
  $$('[data-open-zone]').forEach(b=>b.onclick=()=>openZone(b.dataset.openZone));
}
function renderTelemetry() {
  if(!ST?.power)return;
  const p=ST.power,ptsHistory=observations;
  let chart='<div class="trend-wait">실제 관측 기록을 쌓는 중입니다</div>';
  if(ptsHistory.length>1){
    const max=Math.max(p.rated_kw || 0,...ptsHistory.map(o=>o.kw),1),start=ptsHistory[0].at,end=ptsHistory.at(-1).at;
    const xy=ptsHistory.map(o=>`${((o.at-start)/Math.max(1,end-start)*300).toFixed(1)},${(60-o.kw/max*56).toFixed(1)}`);
    chart=`<svg class="trend-chart" viewBox="0 0 300 64" preserveAspectRatio="none" role="img" aria-label="관측 전력 부하. 세로축 0에서 ${max} 킬로와트"><defs><linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#c6f582" stop-opacity=".2"/><stop offset="1" stop-color="#c6f582" stop-opacity="0"/></linearGradient></defs><path d="M0 4H300 M0 32H300 M0 60H300" stroke="#38505e" stroke-width=".5" stroke-dasharray="3 4" fill="none"/><path d="M0,64 L${xy.join(' L')} L300,64Z" fill="url(#trend-fill)"/><polyline points="${xy.join(' ')}" fill="none" stroke="#c6f582" stroke-width="1.7" vector-effect="non-scaling-stroke"/></svg>`;
  }
  const shortTime=at=>new Date(at).toLocaleTimeString('ko-KR',{hour12:false,hour:'2-digit',minute:'2-digit'});
  $('#power-trend').innerHTML=`<div class="trend-value">${metricNumber(p.total_kw)}<small>kW</small><span>정격 ${p.rated_kw} kW</span></div>${chart}<div class="trend-axis"><span>${ptsHistory.length?shortTime(ptsHistory[0].at):'—'}</span><span>0–${p.rated_kw} kW · ${ptsHistory.length}개 표본</span><span>현재</span></div>`;
  const plant=ST.plant,e=ST.efficiency;
  if(plant){
    const pumps=Object.values(plant.pumps || {}),ok=plant.capacity_kw>=plant.demand_kw;
    $('#cooling-status').textContent=ok?'가용 용량 확보':'냉각 용량 부족';
    const stages=[['냉각탑',plant.tower_kw>0],['펌프',pumps.length?pumps.every(Boolean):null],['열교환',plant.hx_fouling_pct<100],['냉동기',plant.chiller_running || plant.free_cooling]];
    $('#cooling-summary').innerHTML=`<div class="cooling-kpis"><div><label>가용 냉각</label><strong>${metricNumber(plant.capacity_kw,0)}</strong><small>kW</small></div><div><label>냉각 수요</label><strong>${metricNumber(plant.demand_kw,1)}</strong><small>kW</small></div><div><label>냉각수 입구</label><strong>${metricNumber(plant.coldplate_inlet_c,0)}</strong><small>°C</small></div></div><div class="cooling-path">${stages.map(([label,up])=>`<span class="cooling-node ${up===false?'off':''}">${label} · ${up==null?'—':up?'가동':'정지'}</span>`).join('')}</div><p class="cooling-note">${plant.free_cooling?'자연냉각 운전':'기계식 냉각 운전'} · 물 사용 ${metricNumber(e?.water_lpm,2)} L/min · 모델 계산값</p>`;
  } else {$('#cooling-status').textContent='데이터 없음';$('#cooling-summary').innerHTML='<div class="empty">냉각 계통 상태를 수집하지 못했습니다.</div>'}
  $('#recent-events').innerHTML=EVENTS.length?EVENTS.slice(-3).reverse().map(ev=>`<div class="event-preview ${ev.kind==='alarm'?'alarm':''}"><i class="ev-dot"></i><div><b title="${safeText(ev.msg)}">${safeText(ev.msg)}</b><time>${safeText(hhmmss(ev.ts))} · ${safeText({alarm:'경보',clear:'해제',inject:'주입',info:'정보'}[ev.kind] || ev.kind)}</time></div></div>`).join(''):'<div class="empty">기록된 이벤트가 없습니다.</div>';
}
function renderFloorSelector() {
  const box=$('#lift');
  const signature = `${VIEW.mode}:${VIEW.floor}:${floors().map(f=>f.id).join(',')}`;
  if (box.dataset.signature !== signature) {
  box.innerHTML=`<button class="all ${VIEW.mode==='building'?'on':''}" data-f="" aria-pressed="${VIEW.mode==='building'}">전체</button>`+floors().map(f=>`<button class="${VIEW.floor===f.id?'on':''}" data-f="${safeText(f.id)}" aria-pressed="${VIEW.floor===f.id}"><span class="fl">${safeText(f.id)}</span><span class="fn">${safeText({'B1':'설비','1F':'로비','2F':'전산실','3F':'AI','4F':'운영','5F':'연구소','XOC':'제한구역'}[f.id] || '')}</span></button>`).join('');
  $$('[data-f]',box).forEach(b=>b.onclick=()=>b.dataset.f?enterFloor(b.dataset.f):enterBuilding());
  const site=document.createElement('button');site.textContent='옥외';site.dataset.view='site';
  site.classList.toggle('on',VIEW.mode==='site');site.setAttribute('aria-pressed',String(VIEW.mode==='site'));site.onclick=enterSite;box.appendChild(site);
  box.dataset.signature = signature;
  }
  const floor=floors().find(f=>f.id===VIEW.floor),pool=floor?assetsOf(floor.id):LAYOUT.it_assets;
  $('#view-title').textContent=floor?floor.name:'데이터센터 전체 배치';
  const centerLink=$('#agent-control-link');centerLink.hidden=!['5F','XOC'].includes(VIEW.floor);centerLink.href=dcURL(VIEW.floor==='5F'?'researchlab':'xoc');centerLink.textContent=VIEW.floor==='5F'?'연구·평가 관리 ↗':'xOC 관제 ↗';
  $('#scene-floor-code').textContent=floor?`${floor.id} / ${({'B1':'PLANT ROOMS','1F':'SECURITY LOBBY','2F':'SERVER HALL','3F':'AI COMPUTE','4F':'OPERATIONS','5F':'AI RESEARCH','XOC':'RESTRICTED / 층 번호 대외비'}[floor.id] || '')}`:'KT66 / BUILDING';
  $('#scene-floor-note').textContent=VIEW.mode==='building'?'층별 분해 배치 · xOC 실제 층 비공개':VIEW.floor==='3F'?'NVIDIA 7대 공랭 구역 · 액체냉각 비교 실습 분리':VIEW.floor==='5F'?'실무 방법론 연구 → 독립 평가 → 검토 후 적용':VIEW.floor==='XOC'?'AI 에이전트 관제 · SOC / 접근 권한 분리':VIEW.floor==='4F'?'운영 리드 전용 공간 · 역할별 근무석':'자산 대장 기반 개념 배치도';
  $('#scene-summary').textContent=`${floor?racksOf(floor.id).length:LAYOUT.racks.length} RACKS / ${pool.length} ASSETS / 근무자 ${floor?crewOf(floor.id).length:ROSTER.workers.length}명`;
  if(VIEW.mode==='site') {
    $('#view-title').textContent='옥외 전력·냉각 설비';
    $('#scene-floor-code').textContent='SITE / OUTDOOR';
    $('#scene-floor-note').textContent='건물 밖 독립 설비 · 교육용 개념 배치';
    $('#scene-summary').textContent=facilityOf('SITE').length+' FACILITIES / 층 구분 없음';
  } else if(['B1','1F'].includes(floor?.id)) {
    $('#scene-floor-note').textContent=floor.id==='B1'?'전기 · 기계 · 배터리실 / 낮은 벽은 내부를 보여주는 절단 표현':'안내·대기 → 검색 → 인증 → 계단·승강기';
    $('#scene-summary').textContent=facilityOf(floor.id).length+' FACILITIES / 근무자 '+crewOf(floor.id).length+'명';
  }
}
function renderAssetExplorer() {
  if(!LAYOUT || $('#asset-explorer').hidden)return;
  const q=$('#asset-search').value.trim().toLowerCase(),pool=VIEW.mode==='site'?[]:VIEW.mode==='floor'?assetsOf(VIEW.floor):LAYOUT.it_assets;
  const facilities=(VIEW.mode==='site'?facilityOf('SITE'):VIEW.mode==='floor'?facilityOf(VIEW.floor):allFacilities()).filter(a=>[a.id,a.name,facilityGuide(a).title].join(' ').toLowerCase().includes(q));
  const matches=pool.filter(a=>[a.name,a.id,a.ip,a.zone,a.rack].join(' ').toLowerCase().includes(q));
  const box=$('#asset-list'), signature=matches.map(a=>a.id).join(',')+'|'+facilities.map(a=>a.id+':'+facilityDown(a)).join(',');
  if(box.dataset.signature === signature){
    $$('[data-inspect-asset]',box).forEach(b=>{
      const a=matches.find(a=>a.id===b.dataset.inspectAsset);
      b.querySelector('i').style.background=assetIndicator(a);
      b.querySelector(':scope > span').textContent=`${assetPowerLabel(a)}`;
    });
    return;
  }
  box.dataset.signature=signature;
  box.innerHTML=matches.map(a=>`<button class="asset-entry" data-inspect-asset="${safeText(a.id)}"><i style="background:${assetIndicator(a)}"></i><b>${safeText(a.name)}<small>${safeText(a.id)} · ${safeText(a.ip)} · ${safeText(a.zone)}</small></b><span>${assetPowerLabel(a)}</span></button>`).join('') || '<div class="asset-empty">일치하는 자산이 없습니다.</div>';
  $$('[data-inspect-asset]').forEach(b=>b.onclick=()=>openAsset(b.dataset.inspectAsset));
  if(facilities.length && !matches.length)box.innerHTML='';
  box.insertAdjacentHTML('beforeend',facilities.map(f=>`<button class="asset-entry" data-inspect-facility="${safeText(f.id)}"><i style="background:${facilityDown(f)?'#f48181':'#96b4ac'}"></i><b>${safeText(f.name||facilityGuide(f).title||f.id)}<small>${safeText(f.id)} · ${safeText(f.location==='outdoor'?'옥외':f.floor)}</small></b><span>가상 시설</span></button>`).join(''));
  $$('[data-inspect-facility]').forEach(b=>b.onclick=()=>openFacility(facilities.find(f=>f.id===b.dataset.inspectFacility)));
}
function renderRoomLegend() {
  $('#legend').innerHTML=`<div><b>랙 캐비닛</b> = 자산 대장 기반 교육용 배치</div><div><b>3F 금색 / 검정 장비</b> = DGX Spark / Jetson Thor</div><div>신규 외부 장비 LED는 60초 간격 SSH 포트 접속성입니다. 사양·GPU 값은 등록 시점 점검 기록입니다.</div><div><b>서버 슬롯의 색 띠</b> = 네트워크 존</div><div>${(LAYOUT.zones || []).map(z=>`<span style="white-space:nowrap"><i class="sw" style="background:${safeText(z.color)}"></i> ${safeText(z.id)}</span>`).join(' · ')}</div><div><b>1F 녹색 화살표</b> = 방문·인증 동선 / <b>B1 황색 화살표</b> = 반입·점검 동선</div><div><b>바닥 빗금</b> = 점검 여유 공간 / <b>낮은 벽</b> = 내부를 보여주기 위한 절단 표현</div><div><b>청색 통로·배관</b> = 냉각 / <b>황색 배선</b> = 전력</div><div><b>IT 녹색 LED</b> = 컨테이너 또는 접속 응답 / <b>시설 LED</b> = 활성 고장 없음</div><div>시설 상태는 시뮬레이션이며 실제 설비 센서값이 아닙니다. 옥외는 층과 분리된 부지입니다.</div><div><b>유니폼을 입은 도트 근무자</b> = 명단에 배치된 AI 에이전트</div><div>유니폼은 담당 업무, 모자는 자율 등급, 명찰은 모델·런타임을 구분합니다.</div><div><b>발밑 원</b> = 자동 실행 작업 상태</div><div class="agent-state-legend">${Object.values(WORKER_STATE_STYLES).map(s=>`<span><i style="border-color:${s.color};${s.dash?'border-style:dashed':''}"></i>${s.label}</span>`).join('')}</div><div>10초마다 확인 · 개별 CLI 세션은 제외 · 격리/강제 종료 상태는 미연동</div><div>작업 중에도 검토 대기 건이 있을 수 있습니다. 근무자를 선택해 확인하세요.</div><div>층은 물리 배치, 존은 논리 경계입니다.</div>`;
}
function updateConnection(ok,error='') {
  $('#link-status').classList.toggle('down',!ok);
  $('#link-status').title=ok?'상태 수집 정상':'연결 실패 · 이전 데이터 표시';
  $('#sync-label').textContent=ok?`갱신 ${new Date().toLocaleTimeString('ko-KR',{hour12:false})}`:'수집 중단 · 이전 데이터';
  $('#load-error').hidden=ok;
  if(!ok)$('#load-error-text').textContent=`실시간 상태를 읽지 못했습니다. ${ST?'마지막 수집값을 표시합니다. ':''}${error}`;
}

// Fit the scene and its controls in the first viewport. Measure the actual
// header/metric heights so wrapping, alerts and mobile layouts are accounted for.
function installViewportFit() {
  const stage = $('#stage'), workspace = $('#workspace');
  let frame = null;
  const fit = () => {
    frame = null;
    const content = $('#stage-body').hidden ? $('#asset-explorer') : $('#stage-body');
    const top = content.getBoundingClientRect().top + window.scrollY;
    const footer = stage.querySelector('.stage-bottom').getBoundingClientRect().height;
    const height = Math.max(180, Math.floor(window.innerHeight - top - footer - 13));
    stage.style.setProperty('--noc-scene-height', `${height}px`);
    workspace.style.setProperty('--noc-rail-height', `${stage.getBoundingClientRect().height}px`);
    render();
  };
  const schedule = () => { if (frame === null) frame = requestAnimationFrame(fit); };
  const observer = new ResizeObserver(schedule);
  document.querySelectorAll('#hud, .overview-heading, #load-error, #gauges, #stage > .section-heading, .stage-toolbar, .stage-bottom')
    .forEach(element => observer.observe(element));
  new MutationObserver(schedule).observe($('#stage-body'), {attributes:true, attributeFilter:['hidden']});
  window.addEventListener('resize', schedule);
  schedule();
}

// Native-like keyboard behavior for the two modal workflows. The asset drawer
// stays non-modal so operators can keep inspecting other equipment.
function installModalFocus() {
  let active = null;
  for (const modal of document.querySelectorAll('.modal')) {
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', modal.id === 'inj-modal' ? '강사 고장 주입 패널' : 'UPS 부하 차단 판단');
    let wasOpen = !modal.hidden, previous = null;
    new MutationObserver(() => {
      const open = !modal.hidden;
      if (open === wasOpen) return;
      wasOpen = open;
      if (open) {
        previous = document.activeElement; active = modal;
        (modal.querySelector('input:not([disabled]), button:not([disabled])'))?.focus();
      } else {
        if (active === modal) active = null;
        if (previous?.isConnected) previous.focus();
      }
    }).observe(modal, {attributes:true, attributeFilter:['hidden']});
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab' || !active || active.hidden) return;
    const items = [...active.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]')].filter(el=>el.getClientRects().length);
    if (!items.length) return;
    const first=items[0], last=items.at(-1);
    if (e.shiftKey && (document.activeElement === first || !active.contains(document.activeElement))) {e.preventDefault();last.focus();}
    else if (!e.shiftKey && (document.activeElement === last || !active.contains(document.activeElement))) {e.preventDefault();first.focus();}
  });
}
