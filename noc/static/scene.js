/* Architectural room renderer. One cabinet per CMDB rack, one populated slot
   per asset; decorative floor tiles and conduits never count as equipment.
   Uses the projection, picking and final label layer supplied by app.js. */
function roomLine(a, b, color, width = 1, extra = {}) {
  const [x1,y1] = iso(...a), [x2,y2] = iso(...b);
  return el('line', {x1,y1,x2,y2,stroke:color,'stroke-width':width,...extra});
}
function roomFace(x,y,z,side=false) {
  const [sx,sy] = iso(x,y,z);
  return `matrix(${side ? -XS : XS},${YS},0,${-ZS},${sx},${sy})`;
}
function roomRect(x,y,w,h,fill,extra={}) {
  return el('rect',{x,y,width:w,height:h,fill,...extra});
}
function cabinetDoor(x,y,z,w,h,accent,slots=[]) {
  const face = el('g',{transform:roomFace(x,y,z)});
  face.appendChild(roomRect(.08,.08,w-.16,h-.16,'#101b22', {rx:.045}));
  face.appendChild(roomRect(.13,.13,w-.26,h-.26,'#25323a', {rx:.025}));
  // The unoccupied area is a perforated blanking panel, not extra servers.
  for(let row=.25;row<h-.2;row+=.17) {
    for(let col=.24;col<w-.2;col+=.13)
      face.appendChild(roomRect(col,row,.055,.032,'#111d24'));
  }
  face.appendChild(roomRect(.12,.15,.045,h-.3,'#74838a'));
  face.appendChild(roomRect(w-.16,.15,.045,h-.3,'#778890'));
  let top=h-.27;
  slots.forEach(a=>{
    const height=Math.max(.16,(a.u || 1)*.105), up=alive(a), color=zoneColor(a.zone);
    top-=height+.025;
    if(top < .2)return;
    const row=el('g',{'data-asset':a.id,class:'hit',on:{click:e=>{e.stopPropagation();openAsset(a.id)}}});
    row.appendChild(roomRect(.19,top,w-.38,height,'#60717b',{rx:.017}));
    row.appendChild(roomRect(.22,top+.025,w-.44,height-.05,'#35464f'));
    for(let c=.3;c<w-.5;c+=.14)row.appendChild(roomRect(c,top+.045,.07,height-.09,'#14252e'));
    row.appendChild(roomRect(w-.38,top+.047,.04,.042,up?'#b5f18a':'#f48181'));
    row.appendChild(roomRect(.19,top,.035,height,color));
    tipify(row,{title:a.name,sub:`${a.id} · ${a.ip || ''}`,color,
      rows:[['상태',up?'가동':'정지'],['존',a.zone],['환산 전력',`${assetState(a.id).kw.toFixed(2)} kW`]],foot:'선택하면 자산 상세를 엽니다'});
    face.appendChild(row);
  });
  face.appendChild(roomRect(w-.1,h*.42,.025,.32,'#c5cfd3',{rx:.01}));
  face.appendChild(roomRect(.16,h-.115,w-.32,.025,accent));
  return face;
}
function drawRoomRack(rack,x,y,z) {
  if(rack.style==='nvidia-ai')return drawNvidiaRack(rack,x,y,z);
  const list=(LAYOUT.it_assets || []).filter(a=>a.rack===rack.id);
  const aisle=ST?.aisles?.[rack.aisle], load=list.reduce((s,a)=>s+assetState(a.id).kw,0);
  const bad=list.some(assetDown) || load>rack.design_kw;
  const w=1.65,d=1.5,h=rack.u>=42?3.3:2.35;
  const g=el('g',{'data-rack':rack.id,class:'hit',on:{click:e=>{e.stopPropagation();openRack(rack.id)}}});
  g.appendChild(prism(x,y,z,w,d,h,'#52626b'));
  // Powder coated side panel and small ventilation slits.
  const side=el('g',{transform:roomFace(x+w,y,z,true)});
  side.appendChild(roomRect(.1,.15,d-.2,h-.3,'#283b45',{rx:.02}));
  for(let i=0;i<12;i++)side.appendChild(roomRect(.25,.45+i*.085,d-.5,.022,'#4d626d'));
  side.appendChild(roomRect(.17,h-.32,d-.34,.09,'#1b2a32'));
  g.appendChild(side);
  g.appendChild(cabinetDoor(x,y+d,z,w,h,bad?'#f48181':'#b2de86',list));
  // Top exhaust grilles; no animated lights on empty rack units.
  for(let j=0;j<2;j++) {
    const fan=el('g',{transform:`translate(${iso(x+.46+j*.71,y+.72,z+h+.015).join(',')}) scale(1,${YS/XS})`});
    fan.appendChild(el('circle',{r:7.5,fill:'#1b2d37',stroke:'#7a909e','stroke-width':.7}));
    for(let k=-4;k<=4;k+=2)fan.appendChild(el('line',{x1:-5.5,x2:5.5,y1:k,y2:k,stroke:'#596e79','stroke-width':.8}));
    g.appendChild(fan);
  }
  for(const dx of [.15,w-.15])g.appendChild(prism(x+dx,y+d-.2,z-.06,.08,.12,.14,'#141f25'));
  if(bad){const p=iso(x+w/2,y+d/2,z+h);g.appendChild(warnBadge(p[0],p[1]-13))}
  tipify(g,{title:rack.id,color:bad?'#f48181':'#b9df90',sub:`${rack.floor} / ${rack.u}U / ${rack.aisle} 아일`,
    rows:[['자산',`${list.length}대`],['부하',`${load.toFixed(2)} / ${rack.design_kw} kW`],['온도',aisle?`${aisle.temp_c} °C`:'센서 없음'],['존',[...new Set(list.map(a=>a.zone))].join(' · ')]],
    bar:load/rack.design_kw,barColor:'#b9df90',foot:'랙 선택으로 탑재 자산을 확인합니다'});
  return g;
}
/* NVIDIA desktop systems sit on a display rack; the illustration is not a
   claim that Spark/Thor are full-size enterprise rack servers. */
function roomLettering(x,y,z,text,size,color,extra={}) {
  const [sx,sy]=iso(x,y,z);
  return el('text',{transform:'matrix('+XS+','+YS+',0,'+ZS+','+sx+','+sy+')',
    x:0,y:0,'font-family':'Arial,sans-serif','font-size':size,'font-weight':700,
    fill:color,text,...extra});
}
function drawNvidiaDevice(asset,x,y,z) {
  const thor=asset.form_factor==='jetson-thor',w=1.43,d=.86,h=thor?.48:.39;
  const color=thor?'#343b3d':'#c6af83';
  const open=e=>{e.stopPropagation();openAsset(asset.id)};
  const g=el('g',{'data-asset':asset.id,'data-nvidia-device':asset.form_factor,
    class:'hit',role:'button',tabindex:0,'aria-label':asset.name+' 자산 상세',
    on:{click:open,keydown:e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open(e)}}}});
  g.appendChild(prism(x,y,z,w,d,h,color));
  const face=el('g',{transform:roomFace(x,y+d,z)});
  face.appendChild(roomRect(.05,.055,w-.1,h-.1,thor?'#13191b':'#88764f',{rx:.045}));
  for(let v=.12;v<w-.12;v+=thor?.105:.065)
    face.appendChild(roomRect(v,.085,thor?.042:.021,h-.16,thor?'#566363':'#d8c495'));
  face.appendChild(roomRect(w-.19,.075,.065,.024,assetIndicator(asset),{rx:.008}));
  g.appendChild(face);
  if(thor) {
    // Black heat-spreader, exposed fan and deep fin stack.
    for(let v=.12;v<1.28;v+=.12)
      g.appendChild(quad(x+v,y+.1,z+h+.012,.045,.63,{fill:'#172122'}));
    const p=iso(x+w*.68,y+d*.47,z+h+.02);
    const fan=el('g',{transform:'translate('+p.join(',')+') scale(1,'+(YS/XS)+')'});
    fan.appendChild(el('circle',{r:7.4,fill:'#0a1112',stroke:'#63726c','stroke-width':.8}));
    for(let k=0;k<7;k++)fan.appendChild(el('path',{d:'M0,-1 Q3,-6 6,-2 L1,1Z',fill:'#8b9991',transform:'rotate('+(k*360/7)+')'}));
    fan.appendChild(el('circle',{r:1.5,fill:'#76b900'}));g.appendChild(fan);
  } else {
    g.appendChild(quad(x+.04,y+.04,z+h+.009,w-.08,d-.08,{fill:'#cbb88e',stroke:'#e0cca1','stroke-width':.6}));
    g.appendChild(roomLettering(x+.17,y+d+.007,z+.27,'DGX SPARK',.102,'#f4e6c5'));
    for(let v=.2;v<1.3;v+=.14)
      g.appendChild(roomLine([x+v,y+.07,z+h+.018],[x+v,y+.73,z+h+.018],'#9b865e',.34));
  }
  g.appendChild(roomLettering(x+.03,y+d+.02,z-.075,
    asset.id==='dgx-spark-01'?'SPARK · EXISTING':asset.id.toUpperCase(),.125,'#e0e7d5'));
  tipify(g,{title:asset.name,sub:asset.ip || asset.remote,color:assetIndicator(asset),
    rows:[['제품',asset.product],['GPU',asset.gpu_model],['접속 상태',assetStatusLabel(asset)],
      ['성능 수집',asset.telemetry?.mode==='inventory'?'등록 시 SSH 점검 · 실시간 미연동':'Ollama 기반 추정']],
    foot:'선택하면 사양·확인 시각·SSH 접속 정보를 엽니다'});
  return g;
}
function drawNvidiaRack(rack,x,y,z) {
  const list=(LAYOUT.it_assets||[]).filter(a=>a.rack===rack.id);
  const devices=list.filter(a=>a.vendor==='NVIDIA');
  const utility=list.filter(a=>a.vendor!=='NVIDIA');
  const w=5.9,d=1.7,h=3.9,green='#76b900';
  const g=el('g',{'data-rack':rack.id,'data-rack-style':'nvidia-ai',class:'hit',
    on:{click:e=>{e.stopPropagation();openRack(rack.id)}}});
  g.appendChild(prism(x-.15,y-.07,z,w+.3,d+.18,.17,'#1b2829'));
  g.appendChild(prism(x,y,z+.17,w,.12,h-.17,'#202c2c'));
  for(const dx of [0,w-.15]) {
    g.appendChild(prism(x+dx,y,z+.17,.15,d,h-.17,'#36433e'));
    g.appendChild(roomLine([x+dx+.065,y+d+.007,z+.24],[x+dx+.065,y+d+.007,z+h-.08],green,2));
  }
  g.appendChild(prism(x,y,z+h-.4,w,d,.4,'#172121'));
  const spark=devices.filter(a=>a.form_factor==='dgx-spark'),thor=devices.filter(a=>a.form_factor==='jetson-thor');
  const slots=[spark.slice(0,3),spark.slice(3),thor],levels=[2.43,1.45,.47];
  levels.forEach((level,row)=>{
    g.appendChild(prism(x+.16,y+.1,z+level-.09,w-.32,d-.05,.1,'#49564e'));
    g.appendChild(roomLine([x+.22,y+d+.04,z+level-.03],[x+w-.22,y+d+.04,z+level-.03],'#a4cb60',1.5));
    slots[row].forEach((asset,column)=>{
      g.appendChild(drawNvidiaDevice(asset,x+.43+column*1.83,y+.59,z+level));
    });
  });
  // The legacy GPU gateway remains a distinct, selectable infrastructure asset.
  utility.forEach((a,i)=>{
    const p=el('g',{'data-asset':a.id,class:'hit',role:'button',tabindex:0,'aria-label':a.name,
      on:{click:e=>{e.stopPropagation();openAsset(a.id)},keydown:e=>{if(e.key==='Enter'){e.stopPropagation();openAsset(a.id)}}}});
    p.appendChild(prism(x+4.1,y+.58,z+1.45+i*.18,1.25,.8,.14,'#526066'));
    p.appendChild(roomLettering(x+4.18,y+1.4,z+1.54+i*.18,'GPU GATEWAY',.1,'#d3e1dd'));
    g.appendChild(p);
  });
  for(let i=0;i<17;i++)g.appendChild(quad(x+.28+i*.32,y+.18,z+h+.013,.14,1.27,{fill:'#3d5144'}));
  g.appendChild(roomLettering(x+.25,y+d+.018,z+h-.135,'NVIDIA',.265,'#a6df43'));
  if(devices.some(assetDown)){const p=iso(x+w-.25,y+d,z+h);g.appendChild(warnBadge(p[0],p[1]-12))}
  tipify(g,{title:'NVIDIA AI COMPUTE',sub:rack.id+' · 3층 AI 전산실',color:green,
    rows:[['DGX Spark',devices.filter(a=>a.form_factor==='dgx-spark').length+'대'],
      ['Jetson Thor',devices.filter(a=>a.form_factor==='jetson-thor').length+'대'],
      ['인프라',utility.length+'대'],['배치','실물 장비의 교육용 전시 캐비닛']],
    foot:'각 장비를 개별 선택할 수 있습니다'});
  return g;
}
function drawLeadOffice(worker,x,y,z,detail) {
  const w=3.15,d=2.75;
  const g=el('g',{'data-office-zone':'ops-lead','data-office-bounds':[x,y,w,d].join(',')});
  g.appendChild(quad(x,y,z+.022,w,d,{fill:'#304540',stroke:'#c5b581','stroke-width':1}));
  // Rear partition and east glazing leave the front open for picking and sight.
  g.appendChild(prism(x,y,z,.075,d,1.12,'#566c67'));
  g.appendChild(prism(x,y,z,w,.08,1.12,'#4b615d'));
  const glass=[[x+w,y,z+.2],[x+w,y+d-.3,z+.2],[x+w,y+d-.3,z+1.4],[x+w,y,z+1.4]];
  g.appendChild(el('polygon',{points:pts(glass.map(p=>iso(...p))),fill:'#9ac5b4',opacity:.2,stroke:'#d7e6d6','stroke-width':.8}));
  g.appendChild(roomLettering(x+.28,y+.1,z+1.02,'OPERATIONS LEAD',.14,'#e5d8a7'));
  g.appendChild(prism(x+.25,y+.2,z,.9,.28,.48,'#33463d'));
  g.appendChild(quad(x+.27,y+.21,z+.49,.86,.26,{fill:'#b8a57d'}));
  g.appendChild(drawWorkstation(worker,x+.85,y+.67,z,detail));
  return g;
}

function equipmentKind(kind) {
  if(['cooling_tower','crac','chiller','fan_coil','economizer','heat_exchanger','cdu','water_tank','pump','immersion','weather','cold_plate','containment','raised_floor'].includes(kind))return 'cooling';
  if(['utility','generator','ups','pdu','substation','switchgear','transformer','ats','battery','fuel_tank','microgrid','busway','solar','storage','fuel_cell'].includes(kind))return 'power';
  return 'security';
}
function workerAttributes(worker) {
  const open=e=>{e.stopPropagation();openCrew(worker.id)};
  return {class:'hit','data-worker':worker.id,role:'button',tabindex:0,
    'aria-label':`${worker.name} AI 에이전트 상세 보기`,on:{click:open,keydown:e=>{
      if(e.key==='Enter' || e.key===' '){e.preventDefault();open(e)}
    }}};
}
function drawWorkerFigure(worker,x,y,z,seated=false) {
  const [sx,sy]=iso(x,y,z),width=seated?22:24,height=width*AGENT_SPRITE_SIZE.height/AGENT_SPRITE_SIZE.width;
  const g=el('g',{'data-worker-figure':worker.id,'data-avatar-style':'pixel-office',
    transform:`translate(${sx},${sy})`});
  const activity=workerActivity(worker.id);
  const ring=el('ellipse',{'data-agent-ring':worker.id,'data-agent-state':activity.state,
    cx:0,cy:1,rx:width*.7,ry:width*.24,fill:'#14212966',stroke:activity.color,
    'stroke-width':1.6,'vector-effect':'non-scaling-stroke',
    ...(activity.dash?{'stroke-dasharray':activity.dash}:{}),
    'aria-label':`${worker.name}: ${activity.label}`});
  g.appendChild(ring);
  const sprite=createAgentSprite(worker,{seated});
  sprite.setAttribute('x',-width/2);sprite.setAttribute('y',-height);
  sprite.setAttribute('width',width);sprite.setAttribute('height',height);
  sprite.setAttribute('aria-hidden','true');
  g.appendChild(sprite);
  // A forgiving target for pointer/touch selection, including transparent pixels.
  const hitWidth=Math.max(width,32);
  g.appendChild(roomRect(-hitWidth/2,-height,hitWidth,height,'transparent'));
  g.appendChild(roomRect(-width*.3,-height*.9,width*.6,height*.27,'transparent',{'data-agent-hit':'head'}));
  return g;
}

function workerInfo(g,worker,x,y,z,detail) {
  const activity=workerActivity(worker.id);
  g.setAttribute('aria-label',`${worker.name} · ${activity.label} · AI 에이전트 상세 보기`);
  tipify(g,{title:worker.name,sub:`AI 에이전트 · ${worker.id} · ${worker.floor}`,color:activity.color,
    rows:[['자동 작업 상태',activity.label],['판정 근거',safeText(activity.reason)],
      ['검토 대기',activity.counts?`${activity.counts.needs_review||0}건`:'미확인'],
      ['런타임',worker.runtime],['자율성',worker.autonomy],['담당 존',worker.zone]],
    foot:'발밑 원 = 자동 실행기 관측 상태 · 선택하면 상태와 근거를 엽니다'});
  if(detail){const [sx,sy]=iso(x,y,z);pill(sx,sy+25,`AI · ${worker.name}`,{color:'#d1e5ba',size:9,anchor:'mid',gap:3})}
  return g;
}
function drawRoomWorker(worker,x,y,z,detail) {
  const g=el('g',workerAttributes(worker),[drawWorkerFigure(worker,x,y,z)]);
  return workerInfo(g,worker,x,y,z,detail);
}
function drawWorkstation(worker,x,y,z,detail) {
  const g=el('g',workerAttributes(worker));
  // Steel frame, worktop, two displays and an ergonomic chair.
  for(const dx of [0,1.2])g.appendChild(prism(x+dx,y,z,.07,.68,.65,'#344a57'));
  g.appendChild(prism(x-.08,y-.04,z+.65,1.43,.83,.065,'#8798a2'));
  for(let k=0;k<2;k++){
    g.appendChild(prism(x+.13+k*.61,y+.12,z+.715,.035,.12,.14,'#263e4d'));
    g.appendChild(prism(x+.03+k*.62,y+.1,z+.86,.57,.04,.35,'#273f50'));
    const screen=el('g',{transform:roomFace(x+.03+k*.62,y+.145,z+.86)});
    screen.appendChild(roomRect(.025,.025,.52,.3,'#193647'));
    for(let a=0;a<4;a++)screen.appendChild(roomRect(.065,.08+a*.045,.12+a*.07,.012,k?'#7cabc2':'#a3ca86'));
    g.appendChild(screen);
  }
  g.appendChild(quad(x+.4,y+.55,z+.72,.65,.16,{fill:'#394f5f'}));
  g.appendChild(prism(x+.5,y+1.05,z,.08,.08,.45,'#45616f'));
  g.appendChild(prism(x+.27,y+.88,z+.45,.52,.5,.08,'#2b4352'));
  // Keep the chair behind the front-facing sprite so faces stay visible.
  g.appendChild(prism(x+.27,y+1.27,z+.52,.52,.09,.57,'#2c4352'));
  g.appendChild(drawWorkerFigure(worker,x+.53,y+1.1,z,true));
  return workerInfo(g,worker,x+.53,y+1.45,z,detail);
}
function drawRoom(fid,detail) {
  if(fid==='B1')return drawFacilityFloor(detail);
  if(fid==='1F')return drawLobbyFloor(detail);
  const g=el('g'),z=.22, racks=racksOf(fid), fac=physicalFacilitiesOf(fid);
  g.appendChild(el('g',{filter:'url(#plateShadow)'},[prism(0,0,0,GW,GD,z,'#647c8b',{flat:true})]));
  g.appendChild(quad(0,0,z+.004,GW,GD,{fill:'#647e8c'}));
  // Raised access floor: alternating panels and metal perimeter trim.
  for(let x=0;x<GW;x+=.5)for(let y=0;y<GD;y+=.5)
    g.appendChild(quad(x+.008,y+.008,z+.01,.484,.484,{fill:(Math.round((x+y)*2)%2)?'#657f8d':'#6c8794',stroke:'#8da1ac','stroke-width':.24}));
  g.appendChild(quad(.3,.3,z+.02,GW-.6,GD-.6,{fill:'none',stroke:'#d0b77e','stroke-width':1.15,opacity:.7}));
  // Cutaway walls stay at the back so equipment is never covered by a facade.
  g.appendChild(prism(0,0,z,GW,.14,1.65,'#8296a3'));
  g.appendChild(prism(0,0,z,.14,GD,1.3,'#738e9e'));
  for(let x=.8;x<GW;x+=2.1){
    g.appendChild(prism(x,0,z,.07,.25,1.68,'#b0bfc7'));
    g.appendChild(roomLine([x,.16,z+1.3],[Math.min(x+1.7,GW-.2),.16,z+1.3],'#b2d8e5',1.8));
  }
  // Glazed sections sit above the structural back wall, with a readable frame.
  for(let y=.5;y<GD-1;y+=1.5){
    const points=[[.15,y,z+.55],[.15,y+1.22,z+.55],[.15,y+1.22,z+1.22],[.15,y,z+1.22]];
    g.appendChild(el('polygon',{points:pts(points.map(p=>iso(...p))),fill:'#83bac6',opacity:.42,stroke:'#b7d1de','stroke-width':.8}));
  }
  const objects=[];
  const put=(x,y,node)=>objects.push({depth:x+y,node});
  if(racks.length) {
    racks.forEach((r,i)=>{
      const [x,y]=r.pos || [3+i*2.3,3];
      const aisle=ST?.aisles?.[r.aisle], rackWidth=r.style==='nvidia-ai'?6.2:2.05;
      g.appendChild(quad(x-.15,y+1.57,z+.027,rackWidth,.62,{fill:aisle?.cooling_kw>0?'#5cbed4':'#927b79',opacity:.75}));
      g.appendChild(quad(x-.15,y-.45,z+.027,rackWidth,.36,{fill:'#d6ac7a',opacity:.65}));
      // Perforated cold aisle tiles.
      for(let ix=0;ix<8;ix++)for(let iy=0;iy<3;iy++)g.appendChild(quad(x+ix*.23,y+1.65+iy*.14,z+.032,.12,.025,{fill:'#27748e',opacity:.75}));
      put(x+.85,y+.75,drawRoomRack(r,x,y,z));
      // Overhead cable tray, correctly connected to each physical cabinet.
      const tray=el('g',{'pointer-events':'none'});
      tray.appendChild(prism(x+.52,.4,z+3.65,.44,Math.max(y-.5,.3),.09,'#718995'));
      for(let yy=.55;yy<y;yy+=.3)tray.appendChild(roomLine([x+.5,yy,z+3.76],[x+.99,yy,z+3.76],'#bdc6c8',.7));
      tray.appendChild(roomLine([x+.65,.4,z+3.77],[x+.65,y+.2,z+3.77],'#e8b577',1.5));
      tray.appendChild(roomLine([x+.82,.4,z+3.77],[x+.82,y+.2,z+3.77],'#75c4d5',1.2));
      tray.appendChild(roomLine([x+.65,y+.2,z+3.77],[x+.65,y+.2,z+3.3],'#e8b577',1.5));
      put(x+.8,y+.35,tray);
      if(detail){const [sx,sy]=iso(x+(fid==='4F'?2.4:r.style==='nvidia-ai'?3:.8),y+(fid==='4F'?.7:1.7),z+(fid==='4F'?1.45:0));pill(sx,sy+(fid==='4F'?0:34),r.style==='nvidia-ai'?'DGX SPARK + JETSON THOR':r.id,{color:'#c1d6e2',size:10,anchor:'mid',sub:`${r.u}U · ${r.design_kw} kW 정격`})}
    });
  }
  // Equipment is laid out deterministically from the ledger. The layout is a
  // schematic placement; it does not claim surveyed real-world dimensions.
  fac.forEach((item,i)=>{
    // Back wall first, then right wall. Keep the rack fronts unobstructed.
    const x=i<8?.55+i*1.38:10.35,y=i<8?.4:2+(i-8)*1.3;
    put(x+.5,y+.4,drawRoomEquipment(item,x,y,z));
  });
  if(fid==='4F') {
    const workers=crewOf(fid),lead=workers.find(w=>w.id==='ops-lead');
    const staff=workers.filter(w=>w.id!=='ops-lead'),rows=Math.ceil(staff.length/2);
    staff.forEach((worker,i)=>{
      const x=1.85+(i%2)*3.15,y=rows<=1?3.65:2.55+Math.floor(i/2)*2.9/Math.max(rows-1,1);
      const node=drawWorkstation(worker,x,y,z,detail);
      node.setAttribute('data-workstation-bounds',[x-.08,y-.04,1.43,1.5].join(','));
      put(x+.7,y+1.1,node);
    });
    if(lead)put(10,6.2,drawLeadOffice(lead,8.25,4.85,z,detail));
    // Operations video wall, rendered as instrument panels without invented numbers.
    for(let n=0;n<3;n++){
      const x=2+n*1.6;g.appendChild(prism(x,.17,z+1.05,1.45,.05,.83,'#263f50'));
      const f=el('g',{transform:roomFace(x,.23,z+1.05)});
      f.appendChild(roomRect(.06,.06,1.33,.71,'#173242'));
      assetsOf(fid).slice(0,5).forEach((a,k)=>f.appendChild(roomRect(.15,.16+k*.10,Math.max(.04,Math.min(assetState(a.id).util,1)*1.1),.035,n===1?'#a7c883':'#6a9fb9')));
      g.appendChild(f);
    }
    const control=el('a',{href:'/agent-control','aria-label':'4층 AI 에이전트 관제실 열기'});
    // The wall uses positive z-up coordinates; lettering needs a y-down face.
    // Starting at the sign's top edge keeps the plate and text upright together.
    const [signX,signY]=iso(2,.24,z+2.27);
    const sign=el('g',{'data-office-sign':'agent-control',transform:`matrix(${XS},${YS},0,${ZS},${signX},${signY})`});
    sign.appendChild(roomRect(0,0,4.65,.34,'#223b32',{rx:.04}));
    sign.appendChild(el('text',{x:.2,y:.235,'font-size':.205,'font-family':'sans-serif','font-weight':600,fill:'#dcf2c4',text:'AI Agent Control  ›'}));
    control.appendChild(sign);g.appendChild(control);
  } else {
    const workers=crewOf(fid),columns=Math.min(workers.length,3);
    workers.forEach((w,i)=>{
      const x=columns===1?(fid==='3F'?2.4:7.8):2.3+(i%columns)*8/(columns-1),y=(fid==='3F'?6.3:7)-Math.floor(i/columns)*1.3;
      put(x,y,drawRoomWorker(w,x,y,z,detail));
    });
  }
  // Discrete zone stripes on physical rack panels convey mixed logical zones.
  // Zone detail remains in the rail rather than placing duplicate assets in rooms.
  objects.sort((a,b)=>a.depth-b.depth).forEach(o=>g.appendChild(o.node));
  g.appendChild(roomLine([0,GD,z+.02],[GW,GD,z+.02],'#c0d1db',1.2));
  g.appendChild(roomLine([GW,0,z+.02],[GW,GD,z+.02],'#a5c1d2',1.2));
  return g;
}
