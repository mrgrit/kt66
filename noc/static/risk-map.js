/* Evidence-driven scene. No generated incidents, background model calls or random walking. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s),NS='http://www.w3.org/2000/svg';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock=t=>t?new Date(t*1000).toLocaleTimeString('ko-KR',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}):'미수집';
  const stamp=t=>t?new Date(t*1000).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'미수집';
  const shortNames={'facility-engineer':'시설','physical-security':'물리보안','network-engineer':'네트워크','systems-engineer':'시스템','gpu-platform-engineer':'GPU','service-desk':'데스크','soc-analyst':'SOC','ops-lead':'운영리드','compliance-auditor':'감사','application-developer':'개발','skill-researcher':'스킬연구','skill-evaluator':'평가','agent-supervisor':'AI관제'};
  const outcomeLabels={observed:'호출 관측',denied:'접근 거절',pending:'승인 대기',error:'오류',held:'실행 보류',declared:'자체 보고',started:'요청 수신',completed:'종료'};
  const stateLabels={active:'작업 중',recent:'최근 관측',awaiting_evidence:'도구 기록 대기',idle:'대기',unknown:'미확인',stopped:'실행기 중지',held:'실행 보류',replay:'기록 재생'};
  const triggers={periodic:'정기 점검',event:'이벤트 대응',user_request:'사용자 요청',approval:'승인',delegation:'위임',review:'사후 검증',manual:'개별 실행'};
  const order=['Z01','Z03','Z04','Z05','Z02','Z06','Z09','Z11','Z07','Z08','Z10','Z12'];
  const S={active:false,key:'',data:null,catalogue:null,mode:'live',cursor:-1,selected:null,zone:null,event:null,zoom:1,stale:false,
    loading:false,generation:0,received:0,seen:new Set(),nodes:new Map(),positions:new Map(),motion:new Map(),board:null,frame:0,play:0,controller:null,highlight:false};
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const svgNode=(tag,attrs={},text)=>{const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text!=null)e.textContent=text;return e};
  const zoneName=id=>S.catalogue?.zones.find(z=>z.id===id)?.name||({DOCK:'대기·관측 없음',HOLD:'실행 보류'}[id])||id;
  const workerName=id=>S.data?.workers.find(w=>w.id===id)?.name||id;
  const shortName=w=>shortNames[w.id]||String(w.name||w.id).slice(0,6);
  const iconPaths={
    Z01:'M5 9h39v29H5z M12 17h23m-23 7h17m-17 7h23 M38 5v8m-4-4h8',
    Z02:'M4 8h14v31H4z M22 8h14v31H22z M8 14h6m-6 5h6m-6 5h6m12-10h6m-6 5h6m-6 5h6 M9 34h4m14 0h4',
    Z03:'M10 5h28v37H10z M18 5V1h12v4 M19 17a5 5 0 1 0 10 0a5 5 0 1 0-10 0 M15 34c0-12 18-12 18 0z',
    Z04:'M2 11h42v28H2z M2 20h42M2 30h42M14 11v9m15-9v9M9 20v10m15-10v10m14-10v10M15 30v9m15-9v9',
    Z05:'M4 9h17v30H4z M27 9h17v30H27z M8 15h9m14 0h9 M21 24h6 M12 4v5m24-5v5 M17 23v7m14-7v7',
    Z06:'M6 10c0-9 36-9 36 0v27c0 9-36 9-36 0z M6 10c0 9 36 9 36 0 M6 23c0 9 36 9 36 0',
    Z07:'M4 10h9v31H4z M16 5h10v36H16z M31 12l9-3 9 28-9 3z M7 15h3m9-3h4m-16 24h3m9 0h4',
    Z08:'M5 9h38v30H5z M12 22l7 7 17-14 M15 44h18m-9-5v5',
    Z09:'M12 4H3v38h9 M35 4h10v38H35 M16 12l-8 11 8 10 M31 12l8 11-8 10 M28 9l-9 27',
    Z10:'M18 16h12v12H18z M2 2h10v10H2z M36 2h10v10H36z M2 34h10v10H2z M36 34h10v10H36z M12 12l6 6m12 0 6-6M12 34l6-6m12 0 6 6',
    Z11:'M4 13h35v24H4z M39 20h5v10h-5 M10 18v14m7-14v14m7-14v14 M34 6a16 16 0 0 0-22-2M12 1v7H5',
    Z12:'M2 4h20v15H2z M27 4h20v15H27z M2 25h20v15H2z M27 25h20v15H27z M7 11h9m16 0h9M7 32h9m16 0h9'
  };

  function setPerspective(risk){
    S.active=risk;document.body.dataset.perspective=risk?'risk':'physical';$('#risk-panel').hidden=!risk;
    $('#physical-tab').setAttribute('aria-selected',String(!risk));$('#risk-tab').setAttribute('aria-selected',String(risk));
    $('#physical-tab').tabIndex=risk?-1:0;$('#risk-tab').tabIndex=risk?0:-1;
    $('#perspective-eyebrow').textContent=risk?'SECURITY & RISK OBSERVATORY':'FACILITY EXPLORER';
    $('#drawer').hidden=true;SELECTED=null;SELECTED_FACILITY=null;
    if(risk){$('#view-title').textContent='데이터센터 전체 배치';if(!S.catalogue)loadCatalogue();else buildScene();if(S.key)load();fit();}
    else {stopPlay();stopMotion();enterBuilding();window.dispatchEvent(new Event('resize'));}
  }
  async function loadCatalogue(){
    try{
      const r=await fetch('/api/agent-control/risk-map/zones',{cache:'no-store',signal:AbortSignal.timeout(10000)});
      if(!r.ok)throw new Error('구역 설정을 읽지 못했습니다. 다시 관제 탭을 선택해 주세요.');
      S.catalogue=await r.json();buildScene();renderMetrics();fit();
    }catch(e){$('#risk-auth-message').textContent=e.message;$('#risk-auth-message').classList.add('error');}
  }
  function lock(message='키는 이 화면의 메모리에서만 사용합니다.'){
    S.generation++;S.controller?.abort();S.key='';S.data=null;S.event=null;S.selected=null;S.zone=null;S.mode='live';S.seen.clear();S.received=0;
    stopPlay();stopMotion();S.nodes.clear();S.positions.clear();$('#risk-key').value='';$('#risk-auth').hidden=false;$('#risk-lock').hidden=true;
    $('#risk-auth-message').textContent=message;$('#risk-auth-message').classList.toggle('error',message.includes('실패')||message.includes('다시'));
    $('#risk-worker-filter').innerHTML='<option value="">전체 근무자</option>';delete $('#risk-worker-filter').dataset.signature;buildScene();renderAll();
  }
  async function load(){
    if(!S.active||!S.key||S.loading||document.hidden||S.mode==='replay')return;
    const generation=S.generation;S.loading=true;S.controller=new AbortController();const timeout=setTimeout(()=>S.controller.abort(),20000);
    try{
      const r=await fetch('/api/agent-control/risk-map',{cache:'no-store',headers:{'x-api-key':S.key},signal:S.controller.signal});
      if(generation!==S.generation)return;
      if(r.status===401){lock('인증에 실패했습니다. 강사 키를 다시 입력해 주세요.');return;}
      if(!r.ok)throw new Error('관측 자료 조회 실패 ('+r.status+')');
      const data=await r.json();if(generation!==S.generation||S.mode==='replay')return;
      const first=!S.data,wasStale=S.stale||Date.now()-S.received>20000;
      const changedCatalogue=JSON.stringify(S.catalogue)!==JSON.stringify(data.catalogue);
      const newer=first?[]:data.events.filter(e=>!S.seen.has(e.id));
      for(const e of data.events)S.seen.add(e.id);if(S.seen.size>3000)S.seen=new Set(data.events.map(e=>e.id));
      S.data=data;S.catalogue=data.catalogue;S.received=Date.now();S.stale=false;
      $('#risk-auth').hidden=true;$('#risk-lock').hidden=false;$('#risk-key').value='';
      if(first||changedCatalogue||S.board&&geometry().height!==S.board.height){buildScene();updateWorkerFilter();}
      else updateWorkerFilter();
      if(S.mode==='live'){
        syncAgents(!first&&!wasStale,newer);
        if(S.event)S.event=data.events.find(e=>e.id===S.event.id)||S.event;
      }
      renderAll();fit();
    }catch(e){
      if(generation!==S.generation)return;
      S.stale=true;stopMotion();$('#risk-auth-message').textContent='관측 서버에 연결하지 못했습니다. 잠시 후 다시 시도합니다.';
      $('#risk-auth-message').classList.add('error');renderConnection();
    }finally{clearTimeout(timeout);S.loading=false;}
  }
  function updateWorkerFilter(){
    const select=$('#risk-worker-filter'),value=select.value;
    const signature=S.data.workers.map(w=>w.id+':'+w.name).join('|');
    if(select.dataset.signature===signature)return;
    select.innerHTML='<option value="">전체 근무자</option>'+S.data.workers.map(w=>`<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');
    select.value=S.data.workers.some(w=>w.id===value)?value:'';select.dataset.signature=signature;
  }
  function geometry(){
    const columns=$('#risk-map-frame').clientWidth<530?2:4,rows=12/columns;
    const viewport=$('#risk-map-viewport'),rowStep=176,roomHeight=140;
    const width=columns===4?Math.round(Math.max(1160,Math.min(1900,viewport.clientWidth/Math.max(1,viewport.clientHeight)*642))):600;
    const step=(width-40)/columns,dockY=rows*rowStep+33,dockColumns=Math.max(3,Math.floor((width-230)/52));
    const dockRows=Math.max(1,Math.ceil((S.data?.workers.length||13)/dockColumns));
    return {columns,rows,width,height:dockY+40+dockRows*49,dockY,dockColumns,step,rowStep,
      zones:new Map(order.map((id,i)=>[id,{x:30+(i%columns)*step,y:34+Math.floor(i/columns)*rowStep,w:step-20,h:roomHeight}]))};
  }
  function buildScene(){
    if(!S.catalogue||!S.active)return;
    stopMotion();S.board=geometry();S.nodes.clear();S.positions.clear();
    const b=S.board,svg=$('#risk-map');svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${b.width} ${b.height}`);
    const defs=svgNode('defs');
    const pattern=svgNode('pattern',{id:'risk-grid',width:14,height:14,patternUnits:'userSpaceOnUse'});
    pattern.append(svgNode('path',{d:'M14 0H0V14',fill:'none',stroke:'#76978b','stroke-width':.4,opacity:.13}));defs.append(pattern);
    const hatch=svgNode('pattern',{id:'risk-hatch',width:6,height:6,patternUnits:'userSpaceOnUse',patternTransform:'rotate(45)'});hatch.append(svgNode('line',{x1:0,y1:0,x2:0,y2:6,stroke:'#dcaa79','stroke-width':2,opacity:.22}));defs.append(hatch);svg.append(defs);
    svg.append(svgNode('rect',{width:b.width,height:b.height,fill:'url(#risk-grid)'}));
    for(let row=0;row<b.rows;row++)svg.append(svgNode('path',{d:`M16 ${194+row*b.rowStep}H${b.width-16}`,class:'risk-corridor'}));
    for(let col=1;col<b.columns;col++)svg.append(svgNode('path',{d:`M${col*b.step+20} 16V${b.dockY+12}`,class:'risk-corridor'}));
    const rooms=svgNode('g',{'data-risk-rooms':'true'});svg.append(rooms);
    for(const z of S.catalogue.zones){
      const {x,y,w,h}=b.zones.get(z.id),g=svgNode('g',{'data-risk-zone':z.id,class:'risk-zone',role:'button',tabindex:0,'aria-label':z.id+' '+z.name});
      const d=`M${x+8} ${y}H${x+w-8}L${x+w} ${y+8}V${y+h-8}L${x+w-8} ${y+h}H${x+8}L${x} ${y+h-8}V${y+8}Z`;
      g.append(svgNode('path',{d,class:'zone-base'}));
      g.append(svgNode('rect',{x:x+9,y:y+66,width:w-18,height:h-77,rx:3,class:'zone-inset'}));
      g.append(svgNode('path',{d:`M${x+16} ${y+7}h32`,stroke:z.color,'stroke-width':2.5}));
      g.append(svgNode('text',{x:x+15,y:y+24,class:'zone-code'},z.id));
      g.append(svgNode('text',{x:x+15,y:y+45,class:'zone-name'},z.name));
      g.append(svgNode('text',{x:x+15,y:y+58,class:'zone-subtitle'},z.subtitle));
      g.append(svgNode('text',{x:x+w-14,y:y+24,class:'zone-count','text-anchor':'end','data-zone-count':z.id},'—'));
      g.append(svgNode('path',{d:iconPaths[z.id]||'',transform:`translate(${x+22},${y+79}) scale(.8)`,class:'zone-icon'}));
      for(let n=0;n<4;n++)g.append(svgNode('path',{d:`M${x+104+n*(w-130)/4} ${y+77}v${h-87}`,class:'zone-ledger'}));
      g.append(svgNode('text',{x:x+20,y:y+h-17,class:'zone-detail','data-zone-caption':z.id},'관측 연결 대기'));
      g.append(svgNode('rect',{x:x+w/2-23,y:y+h-1,width:46,height:3,rx:1,fill:z.color,opacity:.65}));
      g.append(svgNode('path',{d:`M${x+w/2-21} ${y+h+4}v6m42-6v6`,stroke:z.color,'stroke-width':1,opacity:.6}));
      g.addEventListener('click',()=>selectZone(z.id));g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();selectZone(z.id)}});rooms.append(g);
    }
    const dock=svgNode('g',{'data-risk-dock':'true'});
    dock.append(svgNode('rect',{x:25,y:b.dockY,width:b.width-220,height:b.height-b.dockY-12,rx:5,fill:'#162528',stroke:'#374c47','stroke-dasharray':'3 3'}));
    dock.append(svgNode('text',{x:40,y:b.dockY+17,fill:'#87a393','font-size':9,'font-family':'var(--mono)'},'STANDBY / 최근 접근 없음 · 대기 · 미확인'));
    dock.append(svgNode('rect',{x:b.width-180,y:b.dockY,width:155,height:b.height-b.dockY-12,rx:5,fill:'url(#risk-hatch)',stroke:'#675542'}));
    dock.append(svgNode('text',{x:b.width-165,y:b.dockY+17,fill:'#c4a987','font-size':9,'font-family':'var(--mono)'},'HOLD / 실행 보류'));
    svg.append(dock,svgNode('g',{'id':'risk-trails'}),svgNode('g',{'id':'risk-agents'}));
    syncAgents(false);updateZones();sizeMap();
  }
  function displayedWorkers(){
    if(!S.data)return [];
    if(S.mode==='live')return S.data.workers;
    const prior=new Map();for(const e of S.data.events.slice(0,S.cursor+1))prior.set(e.worker,e);
    return S.data.workers.map(w=>{const e=prior.get(w.id);return {...w,last_event:e||null,location:!e||e.outcome==='completed'?'DOCK':e.outcome==='held'?'HOLD':e.zone,presence:'replay',hold:null}});
  }
  function point(w,workers=displayedWorkers()){
    const b=S.board,location=w.location||'DOCK';
    const peers=workers.filter(x=>x.location===location).sort((a,c)=>a.id.localeCompare(c.id));const index=Math.max(0,peers.findIndex(x=>x.id===w.id));
    if(location==='HOLD')return {x:b.width-154+(index%3)*44,y:b.dockY+67+Math.floor(index/3)*46,zone:location};
    if(location==='DOCK')return {x:52+(index%b.dockColumns)*52,y:b.dockY+68+Math.floor(index/b.dockColumns)*49,zone:location};
    const room=b.zones.get(location);if(!room)return point({...w,location:'DOCK'},workers);
    const blocked=['denied','pending','held'].includes(w.last_event?.outcome);
    if(blocked)return {x:room.x+room.w/2+(index-(peers.length-1)/2)*27,y:room.y+room.h+17,zone:location,gate:true};
    if(peers.length<=3)return {x:room.x+room.w*.55+(index-(peers.length-1)/2)*58,y:room.y+118,zone:location};
    return {x:room.x+28+(index%7)*(room.w-56)/6,y:room.y+(peers.length>7?89:120)+Math.floor(index/7)*37,zone:location,crowded:true};
  }
  function route(a,b){
    const ga=S.board.zones.get(a.zone),gb=S.board.zones.get(b.zone);
    if(a.zone===b.zone)return [a,b];
    const exit=ga?{x:ga.x+ga.w/2,y:ga.y+ga.h+20}:a,entry=gb?{x:gb.x+gb.w/2,y:gb.y+gb.h+20}:b;
    const spine=ga?ga.x+ga.w+10:gb?gb.x-10:20;
    return [a,exit,{x:spine,y:exit.y},{x:spine,y:entry.y},entry,b];
  }
  function putPosition(id,p){const node=S.nodes.get(id);if(!node)return;node.setAttribute('transform',`translate(${p.x.toFixed(2)} ${p.y.toFixed(2)})`);S.positions.set(id,p);}
  function createWorker(w){
    const g=svgNode('g',{class:'risk-agent','data-risk-worker':w.id,role:'button',tabindex:0});
    g.append(svgNode('ellipse',{cx:0,cy:-1,rx:16,ry:5,class:'agent-ring'}));
    const sprite=window.createAgentSprite(w);sprite.setAttribute('x',-12);sprite.setAttribute('y',-43);sprite.setAttribute('aria-hidden','true');g.append(sprite);
    g.append(svgNode('text',{x:0,y:13,'text-anchor':'middle',class:'agent-name'},shortName(w)));
    g.append(svgNode('text',{x:18,y:1,'font-size':8,'font-family':'var(--mono)',class:'agent-exposure'}));
    g.append(svgNode('circle',{cx:14,cy:-31,r:3.8,class:'incident-mark'}));
    g.append(svgNode('rect',{x:-20,y:-45,width:40,height:63,rx:5,class:'risk-agent-hit'}));
    g.addEventListener('click',()=>selectWorker(w.id));g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();selectWorker(w.id)}});
    $('#risk-agents').append(g);S.nodes.set(w.id,g);return g;
  }
  function syncAgents(animate=false,newEvents=[]){
    if(!S.board||!$('#risk-agents'))return;
    const workers=displayedWorkers(),ids=new Set(workers.map(w=>w.id));
    for(const [id,node] of S.nodes)if(!ids.has(id)){node.remove();S.nodes.delete(id);S.positions.delete(id);S.motion.delete(id)}
    for(const w of workers){
      const node=S.nodes.get(w.id)||createWorker(w),p=point(w,workers),old=S.positions.get(w.id);
      const e=w.last_event,blocked=['denied','pending','held'].includes(e?.outcome)&&!['idle','unknown','stopped'].includes(w.presence);
      const color=S.stale||w.presence==='unknown'?'#82949c':w.hold?'#d2af7b':blocked?(e.outcome==='denied'?'#efa083':'#ddc27d'):w.findings?.length?'#d995b1':'#a8cf9a';
      const ring=node.querySelector('.agent-ring');ring.style.stroke=color;ring.setAttribute('stroke-dasharray',S.stale||['unknown','stopped','awaiting_evidence'].includes(w.presence)?'2 3':blocked?'4 2':'none');
      node.querySelector('.incident-mark').style.display=w.findings?.length?'':'none';
      const exposure=node.querySelector('.agent-exposure');exposure.textContent=['active','recent','replay'].includes(w.presence)&&e?'E'+(e.exposure??'?'):'';
      exposure.setAttribute('fill',S.catalogue.exposures.find(x=>x.level===e?.exposure)?.color||'#91a4ac');
      node.querySelector('.agent-pixel-sprite').setAttribute('width',p.crowded?19:24);node.querySelector('.agent-pixel-sprite').setAttribute('height',p.crowded?35:44);
      node.setAttribute('aria-label',`${w.name}, ${zoneName(w.location)}, ${stateLabels[w.presence]||w.presence}`);node.dataset.location=w.location;node.dataset.outcome=e?.outcome||'none';
      if(animate&&old&&!reduced.matches&&S.active&&!S.stale){
        const points=[old];
        for(const event of newEvents.filter(e=>e.worker===w.id).slice(-6)){
          const q=point({...w,location:event.outcome==='held'?'HOLD':event.zone,last_event:event},workers);
          points.push(...route(points.at(-1),q).slice(1));
        }
        points.push(...route(points.at(-1),p).slice(1));
        if(points.some(q=>Math.hypot(q.x-old.x,q.y-old.y)>1))startMotion(w.id,points,p);else putPosition(w.id,p);
      }else{S.motion.delete(w.id);putPosition(w.id,p);}
    }
    highlight();if(S.motion.size&&!S.frame)S.frame=requestAnimationFrame(animateFrame);
  }
  function startMotion(id,points,end){
    const lengths=points.slice(1).map((p,i)=>Math.hypot(p.x-points[i].x,p.y-points[i].y)),total=lengths.reduce((a,b)=>a+b,0);
    S.motion.set(id,{points,lengths,total,end,start:performance.now(),duration:S.mode==='replay'?650:Math.min(3000,Math.max(800,total*1.7))});
  }
  function animateFrame(now){
    S.frame=0;if(!S.active||document.hidden||S.stale)return;
    for(const [id,m] of S.motion){
      const progress=Math.min(1,(now-m.start)/m.duration),distance=(progress*progress*(3-2*progress))*m.total;
      let remaining=distance,i=0;while(i<m.lengths.length-1&&remaining>m.lengths[i])remaining-=m.lengths[i++];
      const a=m.points[i],b=m.points[i+1],t=m.lengths[i]?remaining/m.lengths[i]:1;
      putPosition(id,{x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t,zone:m.end.zone});
      if(progress>=1){putPosition(id,m.end);S.motion.delete(id)}
    }
    if(S.motion.size)S.frame=requestAnimationFrame(animateFrame);
  }
  function stopMotion(){cancelAnimationFrame(S.frame);S.frame=0;S.motion.clear();}
  function highlight(){
    const filter=$('#risk-worker-filter').value,workers=displayedWorkers();
    for(const [id,node] of S.nodes){const w=workers.find(w=>w.id===id);node.classList.toggle('selected',S.selected===id);node.classList.toggle('dimmed',!!filter&&filter!==id||S.highlight&&!w?.findings?.length&&!['denied','error','pending','held'].includes(w?.last_event?.outcome));}
    if(S.selected)S.nodes.get(S.selected)?.parentNode.append(S.nodes.get(S.selected));
    drawTrail();
  }
  function drawTrail(){
    const trails=$('#risk-trails');if(!trails)return;trails.replaceChildren();if(!S.selected||!S.data)return;
    const list=(S.mode==='replay'?S.data.events.slice(0,S.cursor+1):S.data.events).filter(e=>e.worker===S.selected).slice(-5);
    const w=displayedWorkers().find(w=>w.id===S.selected);if(!w)return;
    let prev=null;
    for(const e of list){const p=point({...w,location:e.zone,last_event:e});if(prev){const pts=route(prev,p);trails.append(svgNode('path',{class:'risk-route',d:pts.map((q,i)=>(i?'L':'M')+q.x+','+q.y).join(' ')}));}prev=p;}
  }
  function updateZones(){
    if(!S.board)return;
    const events=S.data?(S.mode==='replay'?S.data.events.slice(0,S.cursor+1):S.data.events):[];
    const selected=S.data?.workers.find(w=>w.id===S.selected),workers=displayedWorkers();
    for(const z of S.catalogue?.zones||[]){
      const node=$(`#risk-map [data-risk-zone="${z.id}"]`);if(!node)continue;
      const count=events.filter(e=>e.zone===z.id).length,occupied=workers.filter(w=>w.location===z.id).length;
      const findings=S.data?.findings.filter(f=>f.zone===z.id)||[];
      node.classList.toggle('selected',S.zone===z.id);node.classList.toggle('capability',!!selected?.capability_zones?.includes(z.id));node.classList.toggle('incident',findings.length>0);
      node.querySelector('.zone-count').textContent=S.data?(findings.length?'△ '+findings.length+' · ':'')+count+' 기록':'—';
      node.querySelector('.zone-detail').textContent=S.data?(occupied?'':count?'최근 관측 '+clock(events.filter(e=>e.zone===z.id).at(-1).at):'수집 범위 내 활동 없음'):'관측 연결 대기';
      node.querySelector('.zone-icon').style.opacity=occupied>1?.12:.48;
      node.setAttribute('aria-label',`${z.id} ${z.name}, 기록 ${count}개, 관측 위치 ${occupied}명${findings.length?', 현재 미종결 '+findings.length+'건':''}`);
    }
  }
  function renderMetrics(){
    const s=S.data?.summary;
    const entries=[['관측 에이전트',s?.agents,'명','실행·도구 기록 연결'],['자동 작업 중',s?.active,'명',s?`상태 미확인 ${s.unknown}명`:'실행기 heartbeat'],['접근 거절',s?.denied,'건','표시 중인 기록 범위'],['xOC 미종결',S.data?.coverage.xoc_available?s?.open_findings:null,'건','기존 탐지 · 현재 판정'],['실행 보류',S.data?.coverage.xoc_available?s?.holds:null,'명','새 세션·다음 도구']];
    $('#risk-metrics').innerHTML=entries.map(([label,value,unit,note],i)=>`<div class="risk-metric"><span>${esc(label)}</span><strong class="${i>1&&value?'warn':''}">${value??'—'}<small>${unit}</small></strong><p>${esc(note)}</p></div>`).join('');
  }
  function renderConnection(){
    const dot=$('.risk-live-dot');dot.classList.toggle('connected',!!S.data&&!S.stale&&S.mode==='live');dot.classList.toggle('stale',S.stale||S.mode==='replay');
    $('#risk-connection').textContent=!S.data?'접근 기록 인증 대기':S.mode==='replay'?'기록 재생 · 실시간 위치와 구분':S.stale?'수집 지연 · 마지막 관측 · 이동 중지':`기록 수신 ${clock(S.data.collected_at)} · 5초 갱신`;
    $('#risk-map-clock').textContent=!S.data?'WAITING FOR EVIDENCE':S.mode==='replay'?'REPLAY / '+clock(S.data.events[S.cursor]?.at):S.stale?'FEED DELAYED':'LIVE / '+clock(Date.now()/1000);
    $('#risk-map-note').textContent=!S.data?'접근 기록을 연결하면 실제 에이전트가 표시됩니다.':S.mode==='replay'?'기록 단위 재생 · 이동선은 증거 사이의 시각적 연결':S.stale?'수집 중단 · 현재 위치를 확인할 수 없습니다.':S.selected?'선택한 근무자의 관측 경로 · 파란 경계는 직무 도구의 관련 구역':'캐릭터 = 마지막 관측 위치 · 게이트 = 거절/승인 대기 · 대기석 = 최근 접근 없음';
    if(S.data){const c=S.data.coverage;$('#risk-coverage').textContent=`읽기 전용 · 모델 호출 없음 · ${c.selected_runs}개 실행 / ${S.data.events.length}개 기록${c.truncated?' · 수집 상한/일부 누락':''}${c.status!=='ok'?' · 수집 범위 확인 필요':''} · xOC ${S.data.findings.length}/${S.data.summary.open_findings}건 · 검사 ${clock(c.xoc_checked_at)}`;}
    else $('#risk-coverage').textContent='읽기 전용 · 모델 호출 없음';
  }
  function eventDetails(e){
    if(!e)return '<p>연결된 도구 기록이 없습니다.</p>';
    const p=e.permission||{},decisions=(p.decisions||[]).map(d=>d.decision==='once'?'이번만 허용':d.decision==='always'?'항상 허용':d.decision).join(' · ');
    const mode={allow:'정책 allow',ask:'정책 ask',deny:'정책 deny',audit_only:'감사 기록 도구',unknown:'미수집'}[p.mode]||'미수집';
    return `<h4>관측된 활동</h4><span class="risk-status ${esc(e.outcome)}">${esc(outcomeLabels[e.outcome]||e.outcome)}</span><dl class="risk-kv"><dt>도구 / 단계</dt><dd>${esc(e.name)}</dd><dt>대상</dt><dd>${esc(e.targets?.join(' · ')||'해당 기록에 명시 없음')}</dd><dt>관측 시각</dt><dd>${esc(stamp(e.at))}</dd><dt>트리거</dt><dd>${esc(triggers[e.trigger]||e.trigger||'미수집')}</dd><dt>접근 정책</dt><dd>${esc(mode)}${decisions?'<br>'+esc(decisions):''}</dd>${e.attempted_zone!==e.zone?`<dt>시도한 구역</dt><dd>${esc(zoneName(e.attempted_zone))}</dd>`:''}<dt>증거 출처</dt><dd>${esc(e.source)}<br>${esc({tool_receipt:'도구 영수증',runtime_record:'실행기 기록',agent_declared:'에이전트 자체 보고'}[e.assertion]||e.assertion)}</dd></dl><div class="risk-exposure"><b>${e.exposure==null?'E?':'E'+e.exposure}</b><span>${esc(S.catalogue.exposures.find(x=>x.level===e.exposure)?.label||'분류 미등록 · 검토 필요')}</span></div><p>${esc(e.summary)}</p>${e.reason?'<p>'+esc(e.reason)+'</p>':''}<div class="risk-axis-tags">${e.axes.map(a=>`<button data-inspect-zone="${esc(a)}">${esc(a+' '+zoneName(a))}</button>`).join('')}</div><a class="risk-evidence-link" href="/agent-control?run=${encodeURIComponent(e.run_id)}" target="_blank" rel="noopener">이 실행의 원본 증적 조사 ↗</a>`;
  }
  function renderInspection(){
    const panel=$('#risk-inspection');
    const opened=[...panel.querySelectorAll('details[open]')].map(d=>d.querySelector('summary')?.textContent);
    if(!S.data){panel.innerHTML='<div class="risk-empty"><strong>업무의 경계를 관찰합니다.</strong>인증 후 에이전트·구역을 선택하면 접근 대상, 노출 등급, 권한 검사와 근거 기록이 이곳에 표시됩니다.</div>';return;}
    const w=displayedWorkers().find(w=>w.id===S.selected);
    if(w){
      const e=S.event?.worker===w.id?S.event:w.last_event;
      panel.innerHTML=`<div class="risk-inspect-person"><div class="risk-portrait"></div><div><small>${esc(w.id)}</small><h3>${esc(w.name)}</h3><span class="risk-status ${esc(S.stale?'unknown':w.presence)}">${esc(S.stale?'마지막 관측':stateLabels[w.presence]||w.presence)}</span></div></div><dl class="risk-kv"><dt>현재 관점</dt><dd>${esc(zoneName(w.location))}</dd><dt>직무</dt><dd>${esc(w.security_role||'명단 미등록 / 임시 역할')}</dd><dt>자율 등급</dt><dd>${esc(w.autonomy||'미수집')}</dd></dl>${eventDetails(e)}${w.findings?.length?'<h4>현재 xOC 미종결</h4>'+w.findings.slice(0,3).map(f=>`<div class="risk-finding"><b>${esc(f.rule+' · '+f.title)}</b><small>우선순위 ${esc(f.risk)}/25 · ${f.confidence==='receipt_verified'?'영수증 대조 확인':'추가 검토 필요'}</small></div>`).join(''):''}<details><summary>직무 전용 도구 범위</summary><p>${esc(w.role_tools?.join(' · ')||'수집되지 않음')}</p><p>현재 직무 설정입니다. 실행 당시의 허용 여부는 각 영수증을 확인합니다.</p></details>`;
      panel.querySelector('.risk-portrait').append(window.createAgentSprite(w));
    }else if(S.zone){
      const z=S.catalogue.zones.find(z=>z.id===S.zone);const events=(S.mode==='replay'?S.data.events.slice(0,S.cursor+1):S.data.events).filter(e=>e.axes.includes(z.id));
      const findings=S.data.findings.filter(f=>f.zone===z.id);
      panel.innerHTML=`<span class="eyebrow">${esc(z.id+' / '+z.subtitle)}</span><h3>${esc(z.name)}</h3><p>${esc(z.description)}</p><div class="risk-empty"><strong>${esc(z.question)}</strong>이 평가축에 연결된 기록 ${events.length}건<br>현재 xOC 미종결 ${findings.length}건</div><h4>이 구역을 관측한 에이전트</h4><div class="risk-axis-tags">${[...new Set(events.map(e=>e.worker))].map(id=>`<button data-inspect-worker="${esc(id)}">${esc(workerName(id))}</button>`).join('')||'<p>표시 범위 내 기록이 없습니다.</p>'}</div><p>관측이 없다는 사실만으로 안전하다고 판정하지 않습니다. 관련된 다른 평가축은 각 기록에서 확인합니다.</p>`;
    }else{
      const c=S.data.coverage;
      panel.innerHTML=`<span class="eyebrow">EVIDENCE-DRIVEN OVERVIEW</span><h3>지금, 어느 경계에 있나</h3><p>근무자를 선택해 활동 경로를 추적하거나 구역을 선택해 접근 기록을 조사하세요.</p><div class="risk-empty"><strong>${S.data.summary.active?'자동 작업 '+S.data.summary.active+'명 진행 중':'자동 실행 중인 작업 없음'}</strong>작업이 시작되고 기록이 들어오면 캐릭터가 이동합니다. 기록 재생에서 이전 활동을 확인할 수 있습니다.</div><h4>노출 등급</h4>${S.catalogue.exposures.filter(e=>e.level).map(e=>`<div class="risk-exposure"><b>E${e.level}</b><span>${esc(e.label)}</span></div>`).join('')}<p>등급은 활동의 성격입니다. 이상 판단은 접근 거절·오류와 xOC 사건을 함께 확인합니다.</p><details><summary>관측 범위와 수집 상태</summary><p>${esc(c.scope)}</p><p>실행 인덱스 ${esc(stamp(c.index_at))}<br>실행기 ${esc(stamp(c.engine_at))}<br>xOC 검사 ${esc(stamp(c.xoc_checked_at))}</p><p>${esc(c.errors.join(' · ')||'인덱스·작업 DB 읽기 오류 없음')}${c.issues.length?' · 일부 실행 증거 누락/잘림':''}${!c.xoc_available?' · xOC 기록 미수집':''}</p></details>`;
    }
    panel.querySelectorAll('[data-inspect-zone]').forEach(b=>b.onclick=()=>selectZone(b.dataset.inspectZone));
    panel.querySelectorAll('[data-inspect-worker]').forEach(b=>b.onclick=()=>selectWorker(b.dataset.inspectWorker));
    panel.querySelectorAll('details').forEach(d=>{d.open=opened.includes(d.querySelector('summary')?.textContent)});
  }
  function visibleEvents(){
    if(!S.data)return [];
    const filter=$('#risk-worker-filter').value;
    return (S.mode==='replay'?S.data.events.slice(0,S.cursor+1):S.data.events).filter(e=>(!S.selected||e.worker===S.selected)&&(!filter||e.worker===filter)&&(!S.zone||e.axes.includes(S.zone)));
  }
  function renderStream(){
    const list=visibleEvents();$('#risk-stream-count').textContent=list.length+' 기록';
    const signature=JSON.stringify([S.event?.id,list.slice(-30).map(e=>[e.id,e.outcome])]);
    if($('#risk-stream').dataset.signature===signature)return;$('#risk-stream').dataset.signature=signature;
    $('#risk-stream').innerHTML=list.length?list.slice(-30).reverse().map(e=>`<button class="risk-event ${esc(e.outcome)} ${S.event?.id===e.id?'selected':''}" data-risk-event="${esc(e.id)}"><span><b>${esc(shortNames[e.worker]||workerName(e.worker))}</b><time>${esc(clock(e.at))}</time></span><small>${esc(e.name)} → ${esc(zoneName(e.zone))}</small>${['denied','pending','error','held'].includes(e.outcome)?`<em>${esc(outcomeLabels[e.outcome])}</em>`:''}</button>`).join(''):'<p class="risk-empty">표시할 관측 기록이 없습니다.</p>';
    $('#risk-stream').querySelectorAll('[data-risk-event]').forEach(b=>b.onclick=()=>{const e=S.data.events.find(e=>e.id===b.dataset.riskEvent);if(!e)return;S.selected=e.worker;S.zone=null;S.event=e;renderAll();highlight();});
  }
  function renderRoster(){
    const list=displayedWorkers(),signature=JSON.stringify([S.selected,S.stale,list.map(w=>[w.id,w.presence,w.findings?.length])]);
    if($('#risk-roster').dataset.signature===signature)return;$('#risk-roster').dataset.signature=signature;
    $('#risk-roster').innerHTML=list.map(w=>`<button class="risk-worker-card ${S.selected===w.id?'selected':''}" data-risk-person="${esc(w.id)}"><span class="risk-card-sprite"></span><span><strong>${esc(shortName(w))}</strong><small>${esc(S.stale?'마지막 관측':stateLabels[w.presence]||w.presence)}</small></span>${w.findings?.length?'<i class="risk-worker-alert" aria-label="xOC 미종결"></i>':''}</button>`).join('');
    for(const b of $('#risk-roster').querySelectorAll('[data-risk-person]')){const w=list.find(w=>w.id===b.dataset.riskPerson);b.querySelector('.risk-card-sprite').append(window.createAgentSprite(w));b.onclick=()=>selectWorker(w.id);}
  }
  function renderPlayback(){
    const replay=S.mode==='replay',n=S.data?.events.length||0;
    $('#risk-live').setAttribute('aria-pressed',String(!replay));$('#risk-replay').setAttribute('aria-pressed',String(replay));
    for(const id of ['risk-prev','risk-next','risk-play','risk-scrub'])$('#'+id).disabled=!replay||!n;
    $('#risk-prev').disabled=!replay||S.cursor<=0;$('#risk-next').disabled=!replay||S.cursor>=n-1;
    $('#risk-scrub').max=Math.max(0,n-1);$('#risk-scrub').value=replay?Math.max(0,S.cursor):Math.max(0,n-1);
    $('#risk-playback-time').textContent=!n?'기록 없음':replay?clock(S.data.events[S.cursor]?.at)+` · ${S.cursor+1}/${n}`:'최근 '+n+'개';
  }
  function renderAll(){renderMetrics();renderConnection();renderInspection();renderStream();renderRoster();renderPlayback();updateZones();highlight();}
  function selectWorker(id){S.selected=id;S.zone=null;S.event=null;renderAll();}
  function selectZone(id){S.zone=id;S.selected=null;S.event=null;renderAll();}
  function stopPlay(){clearInterval(S.play);S.play=0;$('#risk-play').textContent='▶';$('#risk-play').setAttribute('aria-label','기록 재생 시작');}
  function replayAt(index,animate=true){
    if(!S.data?.events.length)return;
    S.mode='replay';S.stale=false;S.cursor=Math.max(0,Math.min(index,S.data.events.length-1));S.event=S.data.events[S.cursor];
    S.selected=$('#risk-worker-filter').value||S.event.worker;S.zone=null;
    syncAgents(animate);renderAll();
  }
  function sizeMap(){
    if(!S.board)return;
    const viewport=$('#risk-map-viewport'),svg=$('#risk-map'),narrow=S.board.columns===2;
    svg.style.width=(viewport.clientWidth*S.zoom)+'px';
    svg.style.height=(narrow?viewport.clientWidth/S.board.width*S.board.height*S.zoom:viewport.clientHeight*S.zoom)+'px';
  }
  function fit(){
    if(!S.active)return;
    const top=$('#risk-map-viewport').getBoundingClientRect().top+window.scrollY;
    const chrome=['.risk-map-bottom','.risk-playback','.risk-legend','.risk-roster','.risk-footnote'].reduce((n,s)=>n+$(s).getBoundingClientRect().height,0)+34;
    const height=Math.max(260,Math.min(720,window.innerHeight-top-chrome));
    $('#risk-panel').style.setProperty('--risk-map-height',height+'px');
    if(S.board){const next=geometry();if(next.columns!==S.board.columns||Math.abs(next.width-S.board.width)>2||next.height!==S.board.height)buildScene();else sizeMap();}
  }
  $('#risk-tab').onclick=()=>setPerspective(true);$('#physical-tab').onclick=()=>setPerspective(false);
  for(const button of [$('#risk-tab'),$('#physical-tab')])button.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const risk=button.id==='physical-tab';setPerspective(risk);(risk?$('#risk-tab'):$('#physical-tab')).focus()}});
  $('#risk-auth').onsubmit=e=>{e.preventDefault();S.key=$('#risk-key').value.trim();S.generation++;S.stale=false;load();};
  $('#risk-lock').onclick=()=>lock();
  $('#risk-clear-selection').onclick=()=>{S.selected=null;S.zone=null;S.event=null;$('#risk-worker-filter').value='';renderAll()};
  $('#risk-worker-filter').onchange=()=>{S.selected=$('#risk-worker-filter').value||null;S.zone=null;S.event=null;renderAll()};
  $('#risk-focus-mode').onclick=()=>{S.highlight=!S.highlight;$('#risk-focus-mode').setAttribute('aria-pressed',String(S.highlight));highlight()};
  $('#risk-guide-toggle').onclick=()=>{$('#risk-guide').hidden=!$('#risk-guide').hidden;$('#risk-guide-toggle').setAttribute('aria-expanded',String(!$('#risk-guide').hidden))};
  $('#risk-live').onclick=()=>{stopPlay();S.mode='live';S.event=null;S.stale=!S.received||Date.now()-S.received>20000;syncAgents(false);renderAll();load()};
  $('#risk-replay').onclick=()=>{stopPlay();replayAt(0,false)};
  $('#risk-scrub').oninput=()=>{stopPlay();replayAt(Number($('#risk-scrub').value),false)};
  $('#risk-prev').onclick=()=>{stopPlay();replayAt(S.cursor-1)};$('#risk-next').onclick=()=>{stopPlay();replayAt(S.cursor+1)};
  $('#risk-play').onclick=()=>{if(S.play){stopPlay();return}if(!S.data?.events.length)return;if(S.cursor>=S.data.events.length-1)replayAt(0,false);$('#risk-play').textContent='Ⅱ';$('#risk-play').setAttribute('aria-label','기록 재생 일시 정지');S.play=setInterval(()=>{if(S.cursor>=S.data.events.length-1){stopPlay();return}replayAt(S.cursor+1)},1000)};
  $('#risk-zoom-in').onclick=()=>{S.zoom=Math.min(3,S.zoom*1.3);sizeMap()};$('#risk-zoom-out').onclick=()=>{S.zoom=Math.max(1,S.zoom/1.3);sizeMap()};$('#risk-fit').onclick=()=>{S.zoom=1;sizeMap();$('#risk-map-viewport').scrollTo(0,0)};
  window.addEventListener('resize',fit);
  document.addEventListener('visibilitychange',()=>{stopPlay();stopMotion();if(!document.hidden&&S.active&&S.key){S.stale=true;load()}});
  setInterval(()=>{if(S.active&&S.key&&S.mode==='live')load()},5000);
  setInterval(()=>{if(!S.active||!S.data)return;if(Date.now()-S.received>20000&&S.mode==='live'&&!S.stale){S.stale=true;stopMotion();syncAgents(false);renderAll()}renderConnection()},1000);
  if(new URLSearchParams(location.search).get('view')==='risk')setPerspective(true);
  renderMetrics();renderInspection();
})();
