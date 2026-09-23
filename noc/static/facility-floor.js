/* 지하 설비층과 1층 로비의 건축 배치. 좌표는 화면용 상대 단위이며 시공 치수·법정 이격 거리가 아니다.
 * 설비 ID는 assets.yaml을 참조한다. 방·문·점검 여유와 동선을 함께 수정한다. */
const BASEMENT_FLOORPLAN = {
  rooms: [
    {id:'electrical',name:'전기실 · UPS',rect:[.16,.16,5.49,4.1],color:'#a39c86'},
    {id:'mechanical',name:'기계실',rect:[5.8,.16,6.04,4.1],color:'#7b9290'},
    {id:'battery',name:'배터리실',rect:[.16,5.46,3.02,2.38],color:'#92978a'},
    {id:'media',name:'매체 폐기실',rect:[3.36,5.46,2.21,2.38],color:'#9a9387'},
    {id:'core',name:'상층 이동',rect:[5.78,5.46,2.2,2.38],color:'#89969b'},
    {id:'suppression',name:'소화용기실',rect:[8.16,5.46,3.68,2.38],color:'#a7978f'}
  ],
  doors: [
    {id:'electrical',name:'전기실 출입',x:3.05,y:4.3,width:1,axis:'x',height:1.25,into:-1},
    {id:'suppression',name:'소화용기실 출입',x:10.1,y:5.38,width:1.1,axis:'x',height:1.25,into:1},
    {id:'mechanical',name:'기계실 반입문',x:9,y:4.3,width:1.65,axis:'x',height:1.25,double:true,into:-1},
    {id:'battery',name:'배터리실 출입',x:2.15,y:5.38,width:.95,axis:'x',height:1.25,into:1},
    {id:'media',name:'매체 폐기실 출입',x:3.75,y:5.38,width:1.2,axis:'x',height:1.25,into:1},
    {id:'core',name:'상층 이동 출입',x:6.15,y:5.38,width:1.6,axis:'x',height:1.25,glass:true,double:true,into:1},
    {id:'exit',name:'피난계단 연결',x:.08,y:4.32,width:1.04,axis:'y',height:1.25,double:true,into:-1}
  ],
  equipment: {
    'swgr-01': {room:'electrical',pos:[.55,.65],size:[1.38,.72,1.55],reason:'수전측 반을 전기실 뒤쪽에 두고 전면 조작·점검 공간을 비웁니다.'},
    'tr-01': {room:'electrical',pos:[2.52,.65],size:[1.22,.85,1.32],reason:'수전반 가까운 변압기 구획입니다. 실내 외함형 변압기를 표현합니다.'},
    'ats-01': {room:'electrical',pos:[.55,2.52],size:[.8,.65,1.42],reason:'UPS 앞단의 절체반입니다. 하부 급전 경로와 점검 통로를 구분합니다.'},
    'ups-01': {room:'electrical',pos:[1.63,2.52],size:[1.13,.65,1.48],reason:'배터리실과 가까운 전기실 전면에 두고, 정면 점검 여유를 남깁니다.'},
    'batt-01': {room:'battery',pos:[.5,5.78],size:[.68,.66,1.25],reason:'별도 배터리실의 캐비닛 A입니다. 앞쪽 공통 점검 공간으로 접근합니다.'},
    'batt-02': {room:'battery',pos:[1.36,5.78],size:[.68,.66,1.25],reason:'캐비닛 A와 나란히 두되 출입문과 점검 동선을 막지 않습니다.'},
    'chiller-01': {room:'mechanical',pos:[6.25,.82],size:[2.35,1.02,1.06],reason:'냉동기는 기계실의 중심 설비입니다. 옆 열교환기·펌프와 연결하고 전면 점검 공간을 확보합니다.'},
    'hx-01': {room:'mechanical',pos:[9.2,.67],size:[.62,.75,1.08],reason:'판형 열교환기를 냉동기와 옥외 냉각 배관 사이에 둡니다.'},
    'eco-01': {room:'mechanical',pos:[10.45,.67],size:[.96,.77,1.05],reason:'옥외 냉각 계통과 가까운 배관·밸브 스키드로 표현합니다.'},
    'pump-pcw-01': {room:'mechanical',pos:[6.32,2.66],size:[.8,.47,.43],reason:'프로세스수 주 펌프를 공통 헤더 앞의 방진 베이스에 놓습니다.'},
    'pump-pcw-02': {room:'mechanical',pos:[7.55,2.66],size:[.8,.47,.43],reason:'예비 펌프는 주 펌프와 나란히 배치합니다. 예비 설정은 운전 상태와 별개입니다.'},
    'pump-cw-01': {room:'mechanical',pos:[9.02,2.3],size:[.88,.5,.46],reason:'응축수 펌프는 냉각탑으로 이어지는 배관측에 배치합니다.'},
    'fm200-01': {room:'suppression',pos:[8.85,5.85],size:[1.1,.65,1.28],reason:'소화용기는 독립된 용기실에서 점검하도록 분리하고 상부 헤더로 연결합니다.'},
    'vesda-01': {room:'electrical',pos:[4.72,.18],size:[.48,.14,.39],mount_z:1.03,reason:'배터리실의 샘플링 배관을 연결한 감지기를 인접 전기실 벽의 점검 가능한 위치에 둡니다.'},
    'shred-01': {room:'media',pos:[3.67,6.51],size:[.88,.78,.91],reason:'전기실·소화용기실과 분리된 매체 폐기 작업실입니다. 출입문 앞 인계·작업 공간을 남깁니다.'}
  },
  clearances:[
    {id:'switchgear-front',rect:[.5,1.53,3.28,.67]},
    {id:'ups-front',rect:[.5,3.27,2.46,.69]},
    {id:'battery-front',rect:[.42,6.64,1.72,.8]},
    {id:'chiller-front',rect:[6.16,1.99,2.48,.5]},
    {id:'mechanical-front',rect:[6.15,3.34,4.6,.65]},
    {id:'media-work',rect:[3.54,5.65,1.82,.66]}
  ],
  paths:[
    {id:'service',color:'#e4c48f',points:[[6.9,5.45],[6.9,4.83],[9.83,4.83],[9.83,3.72],[6.4,3.72]],label:'장비 반입·정비'},
    {id:'electric-access',color:'#e4c48f',points:[[3.57,4.83],[3.57,3.65],[1.18,3.65]]},
    {id:'battery-access',color:'#e4c48f',points:[[2.65,4.83],[2.65,7.1],[1.02,7.1]]},
    {id:'exit',color:'#b5cabc',points:[[7.1,4.83],[.1,4.83]],label:'비상 출구'}
  ]
};
const LOBBY_FLOORPLAN = {
  equipment: {
    'fp-01': {roomName:'안내·방재 데스크',pos:[4.1,.18],size:[.7,.16,.6],mount_z:1.03,reason:'안내 데스크 뒤 벽의 소방 수신반입니다. 출입 담당자가 바로 확인할 수 있는 위치입니다.'},
    'cctv-01': {roomName:'보안 검색 구역',pos:[10.65,3.18],size:[.4,.36,.3],mount_z:1.5,reason:'보안 경계 벽에 부착하여 검색대와 정문을 향하게 배치합니다.'},
    'mantrap-01': {roomName:'통제구역 경계',pos:[8.2,2.9],size:[1.6,1.2,1.7],reason:'검색대 다음의 인터록 포털입니다. 인증을 거쳐 계단·승강기 로비로 진입하는 경계에 둡니다.'},
    'mdet-01': {roomName:'보안 검색 구역',pos:[8.36,5.3],size:[1.28,.36,1.65],reason:'정문과 맨트랩을 연결하는 직선 동선에 놓고, 검색 전후 대기 공간을 남깁니다.'}
  },
  entrance:{id:'entrance',asset:'door-main',name:'정문',x:8,y:7.91,width:2,axis:'x',height:1.8,double:true,glass:true,into:-1}
};
function facilityPlacement(item) {
  if(item.location==='outdoor')return null;
  if(item.floor==='B1'){
    const plan=BASEMENT_FLOORPLAN.equipment[item.id];
    return plan?{...plan,roomName:BASEMENT_FLOORPLAN.rooms.find(r=>r.id===plan.room)?.name}:null;
  }
  if(item.floor==='1F'){
    if(item.id==='door-main')return {roomName:'보안 로비 · 외벽',reason:'외벽의 정문에서 검색대·맨트랩·승강기 로비까지 동선이 이어집니다.'};
    return LOBBY_FLOORPLAN.equipment[item.id]||null;
  }
  return null;
}
function planRoute(points,z,color,attributes={}) {
  const g=el('g',{'pointer-events':'none',...attributes});
  g.appendChild(el('polyline',{points:pts(points.map(p=>iso(...p,z))),fill:'none',stroke:color,'stroke-width':2.2,'stroke-linejoin':'round',opacity:.65}));
  for(let n=1;n<points.length;n++){
    const a=points[n-1],b=points[n],dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy);if(len<.45)continue;
    const ux=dx/len,uy=dy/len,c=[a[0]+dx*.63,a[1]+dy*.63],back=[c[0]-ux*.19,c[1]-uy*.19];
    g.appendChild(el('polygon',{points:pts([iso(...c,z),iso(back[0]-uy*.09,back[1]+ux*.09,z),iso(back[0]+uy*.09,back[1]-ux*.09,z)]),fill:color,opacity:.9}));
  }
  return g;
}
function planDoor(door,z,asset) {
  const g=el('g',{'data-plan-door':door.id,'data-door-axis':door.axis,'data-door-opening':[door.x,door.y,door.width].join(',')});
  const p=(along,depth,height)=>door.axis==='x'?[door.x+along,door.y+depth,z+height]:[door.x+depth,door.y+along,z+height];
  const post=(along,depth,w,d,h,color)=>{const v=p(along,depth,0);g.appendChild(prism(...v,door.axis==='x'?w:d,door.axis==='x'?d:w,h,color))};
  post(-.035,-.055,.07,.13,door.height,'#c1cbc7');post(door.width-.035,-.055,.07,.13,door.height,'#c1cbc7');
  const beam=p(-.035,-.055,door.height-.085);g.appendChild(prism(...beam,door.axis==='x'?door.width+.07:.13,door.axis==='x'?.13:door.width+.07,.085,'#b3c0ba'));
  const threshold=[p(0,-.12,.02),p(door.width,-.12,.02),p(door.width,.12,.02),p(0,.12,.02)];
  g.appendChild(el('polygon',{points:pts(threshold.map(v=>iso(...v))),fill:'#bbc3b7',stroke:'#dce0cb','stroke-width':.45}));
  const leaves=door.double?2:1,span=door.width/leaves;
  for(let n=0;n<leaves;n++){
    const hinge=n?door.width:0,sign=n?-1:1,dx=sign*span*.35,depth=door.into*span*.65;
    const corners=[p(hinge,0,.05),p(hinge+dx,depth,.05),p(hinge+dx,depth,door.height-.14),p(hinge,0,door.height-.14)];
    g.appendChild(el('polygon',{points:pts(corners.map(v=>iso(...v))),fill:door.glass?'#86b9be':'#acb7af',opacity:door.glass?.34:.8,stroke:'#d2d9cc','stroke-width':.8}));
    const arc=[];for(let k=0;k<=10;k++){const angle=k*Math.PI/20;arc.push(iso(...p(hinge+sign*span*Math.cos(angle),door.into*span*Math.sin(angle),.027)))}
    g.appendChild(el('polyline',{points:pts(arc),fill:'none',stroke:'#cbd0b6','stroke-width':.55,'stroke-dasharray':'2 2',opacity:.5}));
  }
  if(['exit','entrance','loading'].includes(door.id)){
    const [sx,sy]=iso(...p(door.width/2,0,door.height+.05));
    g.appendChild(el('rect',{x:sx-12,y:sy-7,width:24,height:8,rx:1,fill:door.id==='exit'?'#437960':'#485f64'}));
    g.appendChild(el('text',{x:sx,y:sy-.9,'text-anchor':'middle','font-size':5.2,fill:'#e5efdc','font-family':'sans-serif',text:door.id==='exit'?'STAIR':door.id==='loading'?'SERVICE':'ENTRY'}));
  }
  if(asset){
    const open=e=>{e.stopPropagation();openFacility(asset)};
    g.setAttribute('data-facility',asset.id);g.setAttribute('class','hit');g.setAttribute('role','button');g.setAttribute('tabindex',0);
    g.setAttribute('aria-label',asset.name+' 상세');
    g.addEventListener('click',open);g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();open(e)}});
    tipify(g,{title:asset.name,sub:'1F / 보안 로비 외벽',rows:[['출입 경로','정문 → 검색 → 맨트랩 → 계단·승강기']],foot:'선택하면 출입 설비 상세를 엽니다'});
  }
  return g;
}
function drawFacilityFloor(detail) {
  const g=el('g',{'data-floorplan':'basement'}),z=.22,P=BASEMENT_FLOORPLAN,objects=[],wide=$('#scene').clientWidth>=560;
  const put=(x,y,node)=>objects.push({depth:x+y,node});
  g.appendChild(el('g',{filter:'url(#plateShadow)'},[prism(0,0,0,GW,GD,z,'#6c7877',{flat:true})]));
  g.appendChild(quad(0,0,z+.01,GW,GD,{fill:'#b4baae'}));
  P.rooms.forEach(room=>{
    const [x,y,w,d]=room.rect;
    g.appendChild(quad(x,y,z+.018,w,d,{fill:room.color,'data-plan-room':room.id,'data-room-bounds':room.rect.join(',')}));
    if(room.id==='lobby')for(let xx=x;xx<x+w;xx+=.4)for(let yy=y;yy<y+d;yy+=.4)
      g.appendChild(quad(xx,yy,z+.025,Math.min(.39,x+w-xx),Math.min(.39,y+d-yy),{fill:'#b5bbaf',stroke:'#929f98','stroke-width':.18}));
  });
  g.appendChild(quad(.12,4.39,z+.035,11.76,.86,{fill:'#617d77','data-corridor':'main','data-corridor-bounds':'.12,4.39,11.76,.86'}));
  for(const yy of [4.42,5.22])g.appendChild(roomLine([.2,yy,z+.041],[11.8,yy,z+.041],'#cbd5b8',.65));
  P.clearances.forEach(area=>{
    const [x,y,w,d]=area.rect;
    g.appendChild(quad(x,y,z+.04,w,d,{fill:'#d1c9a4',opacity:.19,stroke:'#e4d3a1','stroke-width':.6,'stroke-dasharray':'3 2','data-maintenance-clearance':area.id,'data-clearance-bounds':area.rect.join(',')}));
    for(let xx=x;xx<x+w;xx+=.22)g.appendChild(roomLine([xx,y,z+.041],[Math.min(xx+.16,x+w),y+Math.min(.16,d),z+.041],'#dfcf9d',.6));
  });
  P.paths.forEach(path=>g.appendChild(planRoute(path.points,z+.055,path.color,{'data-walkpath':path.id})));
  const wall=(x,y,w,d,h=.38)=>put(x+w/2,y+d/2,el('g',{'data-plan-wall':'','data-wall-bounds':[x,y,w,d].join(',')},[prism(x,y,z,w,d,h,'#b4beba')]));
  // 긴 뒤 벽을 중심점으로 깊이 정렬하면 가까운 장비 위에 그려진다.
  // 외곽 뒤 벽은 배경에 먼저 그리고, 내부 벽·설비만 깊이 정렬한다.
  g.appendChild(prism(0,0,z,12,.12,1.72,'#b4beba'));
  g.appendChild(prism(0,.12,z,.12,4.2,1.28,'#b4beba'));
  wall(0,5.36,.12,2.64,.3);
  wall(11.88,.12,.12,7.76,.22);
  wall(0,7.88,12,.12,.18);
  wall(5.68,.12,.12,4.18,.28);
  for(const x of [3.23,5.64,8.02])wall(x,5.44,.12,2.44,.22);
  for(const [a,b] of [[.12,3.01],[4.09,8.96],[10.69,11.88]])wall(a,4.27,b-a,.12,.33);
  for(const [a,b] of [[.12,2.11],[3.14,3.71],[4.99,6.11],[7.79,10.06],[11.24,11.88]])wall(a,5.32,b-a,.12,.22);
  const fac=physicalFacilitiesOf('B1');
  for(const door of P.doors)put(door.x+(door.axis==='x'?door.width/2:0),door.y+(door.axis==='y'?door.width/2:0),planDoor({...door,height:door.id==='exit'?1.25:.58},z,door.asset?fac.find(f=>f.id===door.asset):null));
  // Cooling pipework stays in the mechanical room; the electrical room has its own tray.
  const services=el('g',{'pointer-events':'none','data-plant-connections':'mechanical'});
  for(const [offset,color] of [[0,'#68b4c5'],[.17,'#d1a67f']]){
    services.appendChild(el('polyline',{points:pts([[11.8,.3+offset,.44],[6.0,.3+offset,.44],[6.0,2.4+offset,.44],[9.9,2.4+offset,.44]].map(p=>iso(p[0],p[1],z+p[2]))),fill:'none',stroke:color,'stroke-width':2.6,'stroke-linejoin':'round'}));
    for(const xx of [6.72,7.94,9.48])services.appendChild(roomLine([xx,2.4+offset,z+.44],[xx,2.8,z+.3],color,1.7));
  }
  put(8,1.2,services);
  const tray=el('g',{'pointer-events':'none'});
  tray.appendChild(prism(.45,.3,z+1.6,3.37,.19,.07,'#889186'));
  tray.appendChild(roomLine([.5,.4,z+1.69],[3.8,.4,z+1.69],'#d9b878',1.7));
  for(let xx=.5;xx<3.8;xx+=.26)tray.appendChild(roomLine([xx,.29,z+1.69],[xx,.5,z+1.69],'#c5c8b5',.5));
  put(2,.4,tray);
  // Drain grating belongs to the wet mechanical zone.
  for(let xx=6.03;xx<11.65;xx+=.16)g.appendChild(quad(xx,3.18,z+.04,.08,.08,{fill:'#42595c'}));
  for(const item of fac){
    const placement=P.equipment[item.id];if(!placement)continue;
    const [x,y]=placement.pos,ground=z+(placement.mount_z||0);
    const node=drawRoomEquipment(item,x,y,ground,true,placement);
    node.setAttribute('data-plan-equipment',item.id);node.setAttribute('data-equipment-room',placement.room);
    node.setAttribute('data-equipment-bounds',[x,y,...placement.size.slice(0,2)].join(','));
    if(placement.mount_z)node.setAttribute('data-wall-mounted','true');
    put(x+placement.size[0]/2,y+placement.size[1]/2,node);
  }
  // Sampling line runs to the detector in the battery room.
  put(1,5.42,el('g',{'pointer-events':'none'},[
    roomLine([.45,5.43,z+1.5],[2.9,5.43,z+1.5],'#d0a7a0',1.5),
    roomLine([.72,5.43,z+1.5],[.72,5.43,z+1.12],'#d0a7a0',1.2)
  ]));
  const core=el('g',{'data-architectural-feature':'stairs-lift'});
  core.appendChild(prism(5.86,6.45,z,.73,1.2,1.35,'#73898b'));
  const lift=el('g',{transform:roomFace(5.87,7.66,z)});lift.appendChild(roomRect(.06,.05,.6,1.17,'#a3b4b2'));lift.appendChild(roomRect(.35,.05,.014,1.17,'#597577'));core.appendChild(lift);
  for(let i=0;i<7;i++)core.appendChild(prism(6.88,5.98+i*.24,z,.79,.25,(i+1)*.18,'#a2afad'));
  for(const xx of [6.86,7.72]){core.appendChild(roomLine([xx,6.05,z+.5],[xx,7.55,z+1.58],'#d2d7c5',1.3));for(let i=0;i<3;i++)core.appendChild(roomLine([xx,6.13+i*.62,z+.22+i*.44],[xx,6.13+i*.62,z+.57+i*.44],'#819897',.9))}
  put(6.8,7.0,core);
  for(const worker of crewOf('B1')){
    const [x,y]=[4.8,3.5];
    put(x,y,drawRoomWorker(worker,x,y,z,detail&&wide));
  }
  objects.sort((a,b)=>a.depth-b.depth).forEach(o=>g.appendChild(o.node));
  if(detail){
    for(const room of P.rooms){
      if(!wide&&!['electrical','mechanical','battery'].includes(room.id))continue;
      const [x,y,w,d]=room.rect,p=iso(x+w/2,y<4?y+.3:y+d-.1,z+(y<4?1.85:0));
      pill(p[0],p[1]+(y<4?-2:24),room.name,{size:8.5,anchor:'mid',gap:2,color:'#dee5d4'});
    }
    if(wide){const corridor=iso(5.8,4.83,z+.05);pill(corridor[0],corridor[1]+6,'관리 복도',{size:8,anchor:'mid',gap:2,color:'#bdd3bb'})}
    const unknown=fac.filter(item=>!P.equipment[item.id] && !P.doors.some(d=>d.asset===item.id));
    if(unknown.length){const p=iso(6,8,z);pill(p[0],p[1]+42,'미배치 설비 '+unknown.length+'개 · 자산 목록에서 확인',{size:9,anchor:'mid',color:'#e3ba86'})}
  }
  return g;
}
function drawLobbyFloor(detail) {
  const g=el('g',{'data-floorplan':'lobby'}),z=.22,objects=[],P=LOBBY_FLOORPLAN,wide=$('#scene').clientWidth>=560;
  const put=(x,y,node)=>objects.push({depth:x+y,node});
  g.appendChild(prism(0,0,0,GW,GD,z,'#778984'));
  // 대기·안내 공간은 넓은 석재 타일, 통제 구역은 차분한 회색 바닥.
  g.appendChild(quad(0,0,z+.01,GW,GD,{fill:'#aebbad'}));
  for(let x=.12;x<11.88;x+=.6)for(let y=3.35;y<7.87;y+=.6)
    g.appendChild(quad(x,y,z+.018,Math.min(.585,11.88-x),Math.min(.585,7.88-y),{fill:'#c1c6b6',stroke:'#9ba99d','stroke-width':.22}));
  g.appendChild(quad(.2,.2,z+.025,11.6,2.94,{fill:'#7d9391','data-controlled-zone':'vertical-circulation'}));
  g.appendChild(quad(7.97,3.42,z+.03,2.07,4.37,{fill:'#a5b8a0',opacity:.7}));
  g.appendChild(planRoute([[9,7.8],[9,6.08],[9,2.35],[7.1,2.35]],z+.04,'#607e60',{'data-walkpath':'entry-screening-authentication'}));
  g.appendChild(planRoute([[8.75,7.36],[6.7,7.36],[6.7,5.25]],z+.04,'#8c9e89',{'data-walkpath':'visitor-reception'}));
  const wall=(x,y,w,d,h)=>put(x+w/2,y+d/2,prism(x,y,z,w,d,h,'#b8c5b9'));
  g.appendChild(prism(0,0,z,12,.12,1.85,'#b8c5b9'));
  g.appendChild(prism(0,.12,z,.12,7.76,.8,'#b8c5b9'));
  wall(11.88,.12,.12,7.76,.22);
  wall(0,7.88,7.96,.12,.18);wall(10.04,7.88,1.96,.12,.18);
  // 좌측은 벽, 우측은 유리 칸막이. 맨트랩 개구부만 통과할 수 있다.
  wall(.12,3.19,5.1,.13,.32);
  const glass=(x,w)=> {
    const node=el('g',{'data-security-partition':''});
    node.appendChild(el('polygon',{points:pts([[x,3.25,z+.08],[x+w,3.25,z+.08],[x+w,3.25,z+1.45],[x,3.25,z+1.45]].map(p=>iso(...p))),fill:'#b4d5cd',opacity:.24,stroke:'#d1dfd0','stroke-width':.8}));
    for(const dx of [0,w])node.appendChild(prism(x+dx,3.19,z,.045,.13,1.5,'#99afa6'));
    put(x+w/2,3.25,node);
  };
  glass(5.22,2.96);glass(9.82,2.02);
  // 낮게 잘라 보이는 건축 벽에서도 카메라 부착 구간은 남긴다.
  wall(10.52,3.14,.65,.12,1.83);
  const desk=el('g',{'data-architectural-feature':'reception'});
  desk.appendChild(prism(4.2,4.0,z,2.8,.82,.73,'#5c7669'));
  desk.appendChild(prism(4.14,3.95,z+.73,2.92,.93,.09,'#d4d1b7'));
  for(let dx=4.35;dx<6.85;dx+=.17)desk.appendChild(prism(dx,4.825,z+.08,.055,.03,.52,'#81977c'));
  desk.appendChild(prism(5.38,4.2,z+.82,.64,.09,.41,'#2a474a'));
  const screen=el('g',{transform:roomFace(5.4,4.3,z+.84)});screen.appendChild(roomRect(.04,.04,.5,.29,'#436f6d'));desk.appendChild(screen);
  put(5.6,4.45,desk);
  // 방문객 좌석은 출입 검색 동선에서 벗어난 왼쪽 창가에 둔다.
  for(const y of [4.17,6.53]){
    const sofa=el('g',{'data-architectural-feature':'waiting-seat'});
    sofa.appendChild(prism(1.05,y,z,2.18,.66,.3,'#526f6b'));
    sofa.appendChild(prism(1.05,y,z+.3,2.18,.17,.41,'#76978a'));
    for(const x of [1.08,3.0])sofa.appendChild(prism(x,y,z+.3,.2,.66,.23,'#8aa594'));
    for(let n=0;n<3;n++)sofa.appendChild(prism(1.31+n*.54,y+.19,z+.3,.5,.4,.08,'#a2b7a0'));
    put(2.1,y+.3,sofa);
  }
  const table=el('g',{'data-architectural-feature':'waiting-table'});
  table.appendChild(prism(1.7,5.4,z,.08,.46,.35,'#768c7f'));
  table.appendChild(prism(1.46,5.27,z+.35,1.21,.74,.08,'#d1c8a8'));put(2,5.6,table);
  // 승강기·계단은 인증 경계 뒤에서 지하와 전산층으로 이어진다.
  const core=el('g',{'data-architectural-feature':'stairs-lift'});
  core.appendChild(prism(5.65,.2,z,1.35,1.35,1.62,'#748c85'));
  const doors=el('g',{transform:roomFace(5.65,1.56,z)});
  doors.appendChild(roomRect(.13,.04,1.08,1.45,'#b8c7bc'));
  doors.appendChild(roomRect(.655,.04,.025,1.45,'#587a73'));
  doors.appendChild(roomRect(1.25,.77,.06,.17,'#d3d8bc'));core.appendChild(doors);
  for(let n=0;n<7;n++)core.appendChild(prism(10.45,.32+n*.24,z,.95,.25,(7-n)*.18,'#b3c1b2'));
  for(const xx of [10.43,11.43])core.appendChild(roomLine([xx,.39,z+1.66],[xx,1.85,z+.55],'#cbd9c6',1.4));
  put(8.5,1.1,core);
  // 별도 문 패널을 방 안에 두지 않고 정문을 외벽 개구부에 붙인다.
  const facilities=physicalFacilitiesOf('1F');
  put(9,7.91,planDoor(P.entrance,z,facilities.find(item=>item.id==='door-main')));
  for(const item of facilities){
    const plan=P.equipment[item.id];if(!plan)continue;
    const [x,y]=plan.pos,node=drawRoomEquipment(item,x,y,z+(plan.mount_z||0),false,plan);
    node.setAttribute('data-plan-equipment',item.id);
    node.setAttribute('data-equipment-bounds',[x,y,...plan.size.slice(0,2)].join(','));
    if(plan.mount_z)node.setAttribute('data-wall-mounted','true');
    put(x+plan.size[0]/2,y+plan.size[1]/2,node);
  }
  for(const worker of crewOf('1F'))put(6.2,3.65,drawRoomWorker(worker,6.2,3.65,z,detail&&wide));
  // 방재 패널과 건물 이름은 뒤 벽면을 사용한다.
  const sign=el('g',{'data-architectural-feature':'lobby-sign'});
  sign.appendChild(prism(.7,.15,z+.91,3.15,.06,.52,'#344f47'));
  sign.appendChild(roomLettering(.9,.22,z+1.25,'AI DATA CENTER',.22,'#d9e7c5'));
  sign.appendChild(roomLettering(.91,.22,z+1.05,'SECURITY RECEPTION',.107,'#aabd9d'));put(2.2,.2,sign);
  objects.sort((a,b)=>a.depth-b.depth).forEach(o=>g.appendChild(o.node));
  if(detail){
    for(const [x,y,label,zz] of [[2.1,7.72,'방문객 대기',0],[5.6,5.04,'안내 · 방재',0],[9,5.29,'출입 검색',1.92],[9,3.15,'인증 · 맨트랩',2.05],[7.1,1.45,'승강기 · B1–4F',1.97],[10.7,.15,'계단',1.85]]){
      if(!wide&&!['방문객 대기','출입 검색','승강기 · B1–4F'].includes(label))continue;
      const p=iso(x,y,z+zz);pill(p[0],p[1]+(zz?0:18),label,{size:9,anchor:'mid',gap:2,color:'#e0e7d5'});
    }
  }
  return g;
}
