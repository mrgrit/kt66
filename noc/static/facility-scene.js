/* 작은 설비도 팬·루버·실린더·절연애자·배관의 형태로 구분한다. 실측 도면은 아니다. */
function facilityCylinder(g,x,y,z,r,h,color) {
  const [cx,cy]=iso(x,y,z),ry=r*YS*1.25,rx=r*XS*1.4,top=cy-h*ZS;
  g.appendChild(el('path',{d:`M${cx-rx},${top} L${cx-rx},${cy} A${rx},${ry} 0 0 0 ${cx+rx},${cy} L${cx+rx},${top}Z`,fill:color,stroke:'#263e49','stroke-width':.65}));
  g.appendChild(el('ellipse',{cx,cy:top,rx,ry,fill:color,stroke:'#c9d5d2','stroke-width':.7}));
}
function facilityFan(g,x,y,z,r) {
  const p=iso(x,y,z),fan=el('g',{transform:`translate(${p.join(',')}) scale(1,${YS/XS})`});
  fan.appendChild(el('circle',{r,fill:'#263942',stroke:'#b0c2c8','stroke-width':1}));
  for(let k=0;k<5;k++)fan.appendChild(el('path',{d:`M0,-1 Q${r*.25},${-r*.85} ${r*.9},0 L0,2Z`,fill:'#75969d',transform:`rotate(${k*72})`}));
  fan.appendChild(el('circle',{r:1.6,fill:'#d0dcdb'}));g.appendChild(fan);
}
function drawRoomEquipment(item,x,y,z,compact=false) {
  const kind=item.kind,type=equipmentKind(kind),bad=facilityDown(item),out=item.location==='outdoor';
  const accent=bad?'#f48181':type==='cooling'?'#7dd5dd':type==='power'?'#e8bd79':kind==='fire'?'#ec9c92':'#b9cbd1';
  const open=e=>{e.stopPropagation();openFacility(item)};
  const g=el('g',{class:'hit','data-facility':item.id,role:'button',tabindex:0,
    'aria-label':(item.name||item.id)+' 설비 상세',on:{click:open,keydown:e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();open(e)}}}});
  const w=out?1.65:compact?.96:1.08,d=out?1.1:.7;
  const h=['ups','battery','switchgear','ats','crac','fan_coil','cdu','storage','fuel_cell'].includes(kind)?1.45:1;
  const box=(xx,yy,zz,ww,dd,hh,color)=>g.appendChild(prism(xx,yy,zz,ww,dd,hh,color));
  const pipe=(a,b,color=accent,width=2)=>g.appendChild(roomLine(a,b,color,width));
  const vents=(height=h,color='#293f48')=>{
    const front=el('g',{transform:roomFace(x,y+d,z)});
    for(let n=.16;n<height-.15;n+=.12)front.appendChild(roomRect(.09,n,w-.18,.025,color));
    g.appendChild(front);
  };
  const faceRect=(xx,zz,ww,hh,color)=>{
    const front=el('g',{transform:roomFace(x,y+d+.01,z)});front.appendChild(roomRect(xx,zz,ww,hh,color));g.appendChild(front);
  };
  if(['utility','substation','transformer'].includes(kind)) {
    if(kind==='utility') {
      for(const dx of [.2,w-.2])box(x+dx,y+.35,z,.07,.1,1.75,'#8b9695');
      pipe([x+.12,y+.4,z+1.5],[x+w-.1,y+.4,z+1.5],'#d5ba7c',3);
      for(let i=0;i<3;i++){box(x+.3+i*.35,y+.3,z+1.25,.08,.15,.35,'#94b9ac');pipe([x+.34+i*.35,y+.37,z+1.65],[x+.34+i*.35,y+.75,z+.35],'#b8915d',1.5)}
    } else {
      box(x,y,z,w,d,.85,'#7f9693');
      for(let i=.12;i<w-.1;i+=.15)box(x+i,y+d,z+.12,.06,.13,.56,'#526f70');
      for(let i=0;i<3;i++){const dx=w*(i+1)/4;box(x+dx,y+.3,z+.85,.07,.09,.43,'#6b8b77');for(let j=0;j<3;j++)box(x+dx-.055,y+.245,z+.94+j*.11,.18,.2,.025,'#b4c5a3')}
    }
  } else if(kind==='generator') {
    box(x,y,z,w,d,.92,'#a7a78e');vents(.92);
    faceRect(.08,.12,.5,.5,'#747e70');faceRect(.16,.4,.21,.13,'#243e3a');
    pipe([x+w*.7,y+.2,z+.92],[x+w*.7,y+.2,z+1.47],'#566a70',3);
    pipe([x+w*.7,y+.2,z+1.47],[x+w*.9,y+.2,z+1.47],'#566a70',3);
  } else if(['fuel_tank','water_tank'].includes(kind)) {
    g.appendChild(quad(x-.1,y-.1,z+.01,w+.2,d+.2,{fill:'#657b81',stroke:'#d4b87b','stroke-width':1.2}));
    facilityCylinder(g,x+w/2,y+d/2,z+.06,kind==='fuel_tank'?.42:.53,kind==='fuel_tank'?.8:1.5,kind==='fuel_tank'?'#b9a78b':'#aec3c8');
    pipe([x+w*.75,y+d/2,z+.15],[x+w,y+d/2,z+.15],'#6bc4d4',2);
    if(kind==='water_tank')for(let zz=.3;zz<1.45;zz+=.25)pipe([x+.4,y+d,z+zz],[x+w-.4,y+d,z+zz],'#647f8b',.6);
  } else if(kind==='cooling_tower') {
    for(const dx of [.1,w-.2])box(x+dx,y+.1,z,.12,d-.2,.22,'#657d83');
    box(x,y,z+.22,w,d,.96,'#94afb6');vents(1.1);
    facilityFan(g,x+w*.3,y+d/2,z+1.21,9);facilityFan(g,x+w*.72,y+d/2,z+1.21,9);
    pipe([x+w,y+.3,z+.75],[x+w+.1,y+.3,z+.75],'#76cbd7',2);
    pipe([x+w+.1,y+.3,z+.75],[x+w+.1,y+.3,z+.07],'#76cbd7',2);
  } else if(kind==='solar') {
    for(const dx of [.15,w-.2])box(x+dx,y+.18,z,.06,.5,.6,'#7b9097');
    const panel=el('g');
    for(let i=0;i<4;i++)for(let j=0;j<3;j++)panel.appendChild(quad(x+i*w/4,y+j*d/3,z+.63,w/4-.035,d/3-.035,{fill:'#345b8f',stroke:'#b2c9d7','stroke-width':.65}));
    g.appendChild(panel);
  } else if(kind==='chiller') {
    box(x,y,z,w,d,.18,'#66888b');
    for(const dy of [.14,.48]){box(x+.06,y+dy,z+.2,w-.12,.22,.32,'#a4bbb9');const f=iso(x+w-.08,y+dy+.11,z+.36);g.appendChild(el('ellipse',{cx:f[0],cy:f[1],rx:4,ry:5,fill:'#597e82',stroke:'#c2d8d3','stroke-width':.7}))}
    box(x+.15,y+.1,z+.54,w*.38,.36,.29,'#759396');box(x+w*.65,y+.3,z+.56,.22,.27,.39,'#d4d5bd');
    pipe([x,y+d,z+.3],[x,y+d+.13,z+.3],'#76c8dd',2);
  } else if(kind==='pump') {
    box(x,y,z,w,d,.12,'#617982');box(x+.22,y+.22,z+.12,w*.5,.3,.34,'#538f8f');
    facilityCylinder(g,x+.19,y+.37,z+.1,.16,.35,'#a2b9b9');
    pipe([x-.12,y+.37,z+.28],[x+w+.1,y+.37,z+.28],'#73becd',2.5);
    box(x+.22,y+.2,z+.42,.15,.34,.08,'#abc4c7');
  } else if(kind==='heat_exchanger' || kind==='economizer') {
    for(let i=0;i<7;i++)box(x+.14+i*.08,y+.07,z+.12,.035,d*.7,.95,i%2?'#94b4b7':'#557e87');
    for(const zz of [.26,.88])pipe([x+.12,y+d,z+zz],[x+w,y+d,z+zz],zz>.5?'#d8a77a':'#77c7da',2);
    box(x+.08,y,z,.11,d,1.22,'#a1b8b9');
  } else if(kind==='fire' && !item.subtype) {
    for(let i=0;i<2;i++){facilityCylinder(g,x+.24+i*.42,y+.28,z,.16,.95,'#bd7974');pipe([x+.24+i*.42,y+.28,z+.95],[x+.24+i*.42,y+.28,z+1.18],'#c7b49a',1.5)}
    pipe([x+.15,y+.28,z+1.18],[x+.82,y+.28,z+1.18],'#d0b693',2);
  } else if(kind==='fire') {
    box(x,y,z,w,d*.4,.86,item.subtype==='panel'?'#b78d86':'#c9d0c9');
    const face=el('g',{transform:roomFace(x,y+d*.4,z)});
    face.appendChild(roomRect(.12,.42,w-.24,.25,'#374f50'));
    for(let i=0;i<3;i++)face.appendChild(roomRect(.16+i*.18,.2,.08,.07,i?'#bdbba0':'#e09886'));
    g.appendChild(face);
    if(item.subtype==='aspirating')pipe([x+.15,y,z+.8],[x+.15,y,z+1.5],'#d59790',2);
  } else if(['door','metal_detector','mantrap'].includes(kind)) {
    for(const dx of [0,w-.1])box(x+dx,y,z,.1,d*.4,1.65,kind==='metal_detector'?'#b9bbad':'#657f89');
    box(x,y,z+1.55,w,d*.4,.12,'#8ba8ac');
    if(kind!=='metal_detector'){const p=[[x+.12,y+.25,z+.05],[x+w-.12,y+.25,z+.05],[x+w-.12,y+.25,z+1.5],[x+.12,y+.25,z+1.5]];g.appendChild(el('polygon',{points:pts(p.map(a=>iso(...a))),fill:'#97c6ca',opacity:.42,stroke:'#b2d7d4','stroke-width':1}));}
    if(kind==='mantrap'){box(x,y+d-.1,z,.1,.1,1.65,'#657f89');box(x+w-.1,y+d-.1,z,.1,.1,1.65,'#657f89');box(x,y+d-.1,z+1.55,w,.1,.12,'#8ba8ac')}
  } else if(['cctv','weather'].includes(kind)) {
    box(x+w/2,y+d/2,z,.055,.065,1.6,'#a6b9bc');
    if(kind==='cctv'){box(x+w/2-.18,y+d/2,z+1.5,.52,.2,.22,'#cad4cf');box(x+w/2+.32,y+d/2,z+1.53,.06,.18,.15,'#253f4f')}
    else {for(const dx of [-.35,0,.35])pipe([x+w/2,y+d/2,z+1.65],[x+w/2+dx,y+d/2+.25,z+1.65],'#c7d5ce',1.4);box(x+w/2-.2,y+d/2-.1,z+.8,.43,.25,.4,'#d5d9ca')}
  } else if(kind==='guard_post') {
    box(x,y,z,w,d,1.3,'#b2b5a5');box(x-.07,y-.06,z+1.3,w+.14,d+.12,.1,'#6f8c87');
    faceRect(.12,.55,w-.24,.5,'#43767f');faceRect(w*.6,.06,.42,.95,'#54777a');
  } else if(kind==='immersion') {
    box(x,y,z,w,d,.55,'#9dafb3');g.appendChild(quad(x+.09,y+.09,z+.56,w-.18,d-.18,{fill:'#679c95'}));
    for(let i=0;i<4;i++)box(x+.14+i*.2,y+.18,z+.48,.09,d-.36,.26,'#344e56');
  } else if(kind==='busway') {
    for(const dx of [.1,w-.15])box(x+dx,y+.15,z,.035,.05,1.5,'#7e949e');
    box(x,y+.1,z+1.4,w,.23,.13,'#c6ba8a');
    for(let i=0;i<2;i++)box(x+.15+i*.5,y+.05,z+1.1,.22,.32,.3,'#7d9294');
  } else if(kind==='rack_mover') {
    box(x,y,z+.1,w,d,.28,'#c0a677');for(const dx of [.13,w-.18])facilityCylinder(g,x+dx,y+d,z,.09,.16,'#344950');
    box(x+.15,y+.15,z+.38,w-.3,d-.3,.12,'#7d9596');
  } else {
    box(x,y,z,w,d,h,type==='cooling'?'#8aa9b1':kind==='battery'?'#6e8684':'#98a8a4');
    faceRect(.1,h-.48,w-.2,.29,'#263e48');faceRect(.16,h-.4,w-.32,.05,accent);
    if(kind==='battery')for(let i=0;i<4;i++){faceRect(.14,.12+i*.2,w-.28,.14,'#394f4e');faceRect(.2,.16+i*.2,.09,.035,'#a6cf96')}
    else vents(h-.55);
    if(['crac','fan_coil','cdu'].includes(kind)){facilityFan(g,x+w/2,y+d/2,z+h+.015,7);pipe([x+w+.03,y+d,z+.1],[x+w+.03,y+d,z+.85],'#75c4d4',2)}
    if(kind==='disk_shredder')faceRect(.12,h-.28,w-.24,.09,'#1c3037');
  }
  const point=iso(x+w*.5,y+d,z+.12);
  g.appendChild(el('circle',{cx:point[0],cy:point[1],r:2,fill:bad?'#f48181':'#b4d6a0',stroke:'#263e49','stroke-width':.7}));
  if(bad){const p=iso(x+w/2,y+d/2,z+1.6);g.appendChild(warnBadge(p[0],p[1]-5))}
  tipify(g,{title:item.name||item.id,sub:item.id+' / '+(out?'옥외':item.floor),color:accent,
    rows:[['설비',facilityGuide(item).title||kind],['고장 주입',bad?'활성 이상 있음':'해당 설비 활성 고장 없음']],
    foot:'교육용 가상 시설 · 역할·설계값·상태·고장 영향 보기'});
  return g;
}
function drawOutdoor(detail) {
  const g=el('g',{'data-site':'outdoor'}),z=.13,items=physicalFacilitiesOf('SITE');
  g.appendChild(prism(0,0,0,GW,GD,z,'#64736c'));
  g.appendChild(quad(.15,.15,z+.01,GW-.3,GD-.3,{fill:'#718078'}));
  // Outdoor yard: no storey walls, open fence and separate equipment plinths.
  for(let x=0;x<=GW;x+=1.5){g.appendChild(prism(x,0,z,.035,.035,.75,'#a1b2a4'));if(x<GW)g.appendChild(roomLine([x,0,z+.55],[x+1.5,0,z+.55],'#a3b4a6',1))}
  for(const yy of [2.65,5.02])g.appendChild(roomLine([.3,yy,z+.02],[GW-.3,yy,z+.02],'#b8b79d',2));
  items.slice().sort((a,b)=>a.site_pos[0]+a.site_pos[1]-b.site_pos[0]-b.site_pos[1]).forEach(item=>{
    const [x,y]=item.site_pos;
    g.appendChild(quad(x-.1,y-.12,z+.018,1.9,1.45,{fill:'#89928a',stroke:'#c2c0a5','stroke-width':.7}));
    g.appendChild(drawRoomEquipment(item,x,y,z+.02));
    if(detail){const p=iso(x+.8,y+1.35,z);pill(p[0],p[1]+8,item.name||item.id,{size:8,anchor:'mid',gap:1,color:'#e0e5d9'})}
  });
  return g;
}
