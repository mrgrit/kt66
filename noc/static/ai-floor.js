/* 3F의 건축 배치. 상대 좌표이며 시공 도면이나 법정 이격 거리 산정이 아니다.
 * 실물 Spark/Thor는 공랭 전시 캐비닛, 액냉 설비는 분리된 비교 실습 구역이다. */
const AI_FLOORPLAN={
  rack:{pos:[1.55,2.3],size:[5.9,1.7]},
  door:{id:'entrance',asset:'door-3f',x:7.4,y:7.91,width:1.5,axis:'x',height:1.65,double:true,into:-1},
  clearances:[
    {id:'rack-rear',rect:[1.4,1.25,6.15,1.0]},
    {id:'rack-front',rect:[1.4,4.05,6.15,1.5]},
    {id:'entry-route',rect:[7.65,4.15,1.25,3.6]},
    {id:'main-route',rect:[1.0,5.6,6.65,1.0]}
  ],
  path:[[8.15,7.85],[8.15,6.1],[3.2,6.1],[3.2,5.15]],
  equipment:{
    'crac-02':{pos:[.25,4.0],size:[.95,1.0,1.7],roomName:'공랭 구역 · 측벽 공조',reason:'항온항습기를 측벽에 두고 랙 전면의 냉기 공급 구간과 구분합니다. 전면 정비 통로를 비워 둡니다.'},
    'fcu-3f-01':{pos:[7.85,2.3],size:[.7,1.7,2.2],roomName:'공랭 구역 · 랙 열 끝',reason:'인로우 팬코일은 랙 열 옆에 놓습니다. 랙 전면 작업 통로와 출입 동선을 가로막지 않습니다.'},
    'pdu-03':{pos:[.3,6.85],size:[.5,.65,1.25],roomName:'공랭 구역 · 측벽 전원',reason:'전원 분배는 측벽을 따라 상부 부스바와 연결하고 액체냉각 배관과 분리합니다. 통로 바닥을 가로지르는 전원선을 그리지 않습니다.'},
    'bus-3f-b':{pos:[1.5,.45],size:[6.05,.3,.16],mount_z:3.0,roomName:'공랭 구역 · 상부 전원',reason:'부스바는 랙 후면 상부에 설치하고 탭오프로 PDU에 연결합니다. 바닥에 독립 장비처럼 세우지 않습니다.'},
    'cdu-01':{pos:[9.5,.55],size:[1.05,.8,1.45],roomName:'액체냉각 비교 실습 구역',reason:'CDU와 침지 시험 설비는 공랭 Spark·Thor에서 분리된 실습 구역에 둡니다. 실물 장비에 액체 배관이 연결된 것처럼 표시하지 않습니다.'},
    'imm-01':{pos:[9.5,2.5],size:[2.05,1.35,.8],roomName:'액체냉각 비교 실습 구역',reason:'침지 시험 탱크는 누수 대응이 가능한 별도 실습 구역에 두고 앞쪽 정비 공간과 출입 동선을 확보합니다.'},
    'fm200-03':{pos:[10.4,6.5],size:[1.05,.55,1.35],roomName:'출입구 옆 방재 구역',reason:'소화 용기는 출입문을 막지 않는 측면 보호 구역에 둡니다. 밸브 점검 공간을 확보하고 이동 동선에서 분리합니다.'},
    'vesda-02':{pos:[.16,6.2],size:[.5,.16,.45],mount_z:1.08,roomName:'벽부 조기연기감지',reason:'감지 본체를 벽에 부착하고 샘플링 배관을 상부로 연결합니다. 통로에 센서를 세워 놓지 않습니다.'},
    'cctv-03':{pos:[11.1,.2],size:[.48,.2,.3],mount_z:1.72,roomName:'출입 동선 감시',reason:'상부 벽의 카메라가 출입구와 주요 이동 통로를 향하도록 배치합니다.'},
    'badge-3f':{pos:[9.04,7.855],size:[.23,.08,.32],mount_z:.82,roomName:'출입문 옆 카드리더',reason:'카드리더는 출입문 옆 벽에 붙이고 문 개폐 범위 밖에서 사용합니다.'}
  }
};
function drawAIFloor(detail){
  const P=AI_FLOORPLAN,z=.22,g=el('g',{'data-floorplan':'ai-compute'}),objects=[],fac=physicalFacilitiesOf('3F');
  const put=(x,y,node)=>objects.push({depth:x+y,node});
  g.appendChild(prism(0,0,0,GW,GD,z,'#617b80',{flat:true}));
  for(let x=0;x<GW;x+=.5)for(let y=0;y<GD;y+=.5)
    g.appendChild(quad(x+.008,y+.008,z+.015,.484,.484,{fill:(Math.round((x+y)*2)%2)?'#6b8489':'#728c90',stroke:'#91a5a5','stroke-width':.2}));
  g.appendChild(quad(9.15,.22,z+.025,2.62,4.8,{fill:'#738f91',stroke:'#b1d1ca','stroke-width':.7,'data-plan-room':'liquid-lab'}));
  // 배수·누수 대비 경계는 실습 구역 안에만 둔다.
  g.appendChild(prism(9.08,.2,z,.08,4.9,.13,'#adc1b7'));
  g.appendChild(prism(9.08,5.02,z,2.72,.08,.13,'#adc1b7'));
  g.appendChild(prism(0,0,z,GW,.13,1.8,'#91a7aa'));
  g.appendChild(prism(0,.13,z,.13,7.74,1.3,'#809ca1'));
  for(const x of [1.65,4,6.35,8.7])g.appendChild(roomLine([x,.15,z+1.62],[x+1.5,.15,z+1.62],'#c6e2d1',1.6));
  g.appendChild(roomLettering(9.3,.15,z+1.7,'LIQUID COOLING / TRAINING',.15,'#e4ece0'));
  P.clearances.forEach(area=>{const [x,y,w,d]=area.rect;g.appendChild(quad(x,y,z+.035,w,d,{fill:area.id==='rack-front'?'#6da9b4':'#a4ae94',opacity:.3,stroke:'#c4d2b5','stroke-width':.6,'stroke-dasharray':'3 2','data-maintenance-clearance':area.id,'data-clearance-bounds':area.rect.join(',')}))});
  g.appendChild(planRoute(P.path,z+.05,'#d3ddbc',{'data-walkpath':'ai-entry'}));
  // 전면 타공 타일 → 공랭 장비 → 후면 리턴. 실물 공랭과 액냉 비교 실습을 연결하지 않는다.
  for(let x=1.7;x<7.3;x+=.32)for(let y=4.15;y<4.6;y+=.13)
    g.appendChild(quad(x,y,z+.055,.18,.03,{fill:'#347f90'}));
  const rack=racksOf('3F').find(r=>r.style==='nvidia-ai');
  if(rack){const [x,y]=P.rack.pos,node=drawNvidiaRack(rack,x,y,z);node.setAttribute('data-equipment-bounds',[x,y,...P.rack.size].join(','));put(x+2.95,y+.85,node)}
  // 용기 보호대와 카드리더 부착 벽은 건축 요소다.
  for(const x of [10.18,11.63])put(x,6.8,prism(x,6.36,z,.06,.84,.8,'#b5c4b6'));
  put(9.16,7.785,prism(8.98,7.72,z,.38,.13,1.27,'#91a7aa'));
  for(const item of fac){
    if(item.id===P.door.asset)continue;
    const plan=P.equipment[item.id];if(!plan)continue;
    const [x,y]=plan.pos,node=drawRoomEquipment(item,x,y,z+(plan.mount_z||0),true,plan);
    node.setAttribute('data-plan-equipment',item.id);node.setAttribute('data-equipment-bounds',[x,y,...plan.size.slice(0,2)].join(','));
    if(plan.mount_z)node.setAttribute('data-mounted',plan.mount||'wall-or-overhead');
    put(x+plan.size[0]/2,y+plan.size[1]/2,node);
  }
  const services=el('g',{'pointer-events':'none','data-ai-connections':'separated'});
  services.appendChild(roomLine([1.55,.63,z+3.02],[.3,.63,z+3.02],'#d4b67e',1.5));
  services.appendChild(roomLine([.3,.63,z+3.02],[.3,7.1,z+3.02],'#d4b67e',1.2));
  services.appendChild(roomLine([.3,7.1,z+3.02],[.55,7.1,z+1.25],'#d4b67e',1.2));
  services.appendChild(roomLine([.22,6.25,z+1.53],[.22,6.25,z+2.1],'#d6b4a6',.9));
  services.appendChild(roomLine([.22,6.25,z+2.1],[.22,.3,z+2.1],'#d6b4a6',.9));
  // 비교 실습 회로는 오른쪽 구역 안에서만 이어진다.
  for(const [offset,color] of [[0,'#6ab6c8'],[.13,'#d1a58b']]){
    services.appendChild(roomLine([9.63+offset,.98,z+.5],[9.63+offset,2.6,z+.5],color,1.3));
  }
  put(6,1,services);
  put(P.door.x+P.door.width/2,P.door.y,planDoor(P.door,z,fac.find(item=>item.id===P.door.asset)));
  for(const worker of crewOf('3F'))put(3.7,5.95,drawRoomWorker(worker,3.7,5.95,z,detail));
  objects.sort((a,b)=>a.depth-b.depth).forEach(o=>g.appendChild(o.node));
  g.appendChild(roomLine([0,GD,z],[7.38,GD,z],'#c4d6cf',1.1));
  g.appendChild(roomLine([8.95,GD,z],[GW,GD,z],'#c4d6cf',1.1));
  g.appendChild(roomLine([GW,0,z],[GW,GD,z],'#b7cfc6',1.1));
  if(detail){
    const p=iso(5,4.15,z);pill(p[0],p[1]+25,'NVIDIA · 공랭 장비',{size:9,anchor:'mid',color:'#c9df9d',gap:2});
    const q=iso(10.55,5.1,z);pill(q[0],q[1]+23,'액체냉각 · 비교 실습',{size:9,anchor:'mid',color:'#b6d8d5',gap:2});
  }
  return g;
}
