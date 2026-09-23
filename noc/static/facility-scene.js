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
// 수평 압력용기: x축으로 놓인 원통과 원형 플랜지를 투영한다.
function facilityVessel(g,x,y,z,length,r,color) {
  const ring=(xx,fill)=>{
    const face=el('g',{transform:roomFace(xx,y,z,true)});
    face.appendChild(el('circle',{r,fill,stroke:'#b8cccb','stroke-width':.025}));
    return face;
  };
  g.appendChild(ring(x,color));
  g.appendChild(el('polygon',{points:pts([[x,y,z+r],[x+length,y,z+r],[x+length,y,z-r],[x,y,z-r]].map(p=>iso(...p))),fill:color,stroke:'#52757a','stroke-width':.5}));
  g.appendChild(roomLine([x,y,z+r*.56],[x+length,y,z+r*.56],'#d3ded6',1.2));
  const end=ring(x+length,'#668f94');
  end.appendChild(el('circle',{r:r*.71,fill:'#8dafad',stroke:'#d2dfd5','stroke-width':.015}));
  for(let n=0;n<8;n++)end.appendChild(el('circle',{cx:Math.cos(n*Math.PI/4)*r*.84,cy:Math.sin(n*Math.PI/4)*r*.84,r:.017,fill:'#374f56'}));
  g.appendChild(end);
}
function drawRoomEquipment(item,x,y,z,compact=false,placement=null) {
  const kind=item.kind,type=equipmentKind(kind),bad=facilityDown(item),out=item.location==='outdoor';
  const accent=bad?'#f48181':type==='cooling'?'#7dd5dd':type==='power'?'#e8bd79':kind==='fire'?'#ec9c92':'#b9cbd1';
  const open=e=>{e.stopPropagation();openFacility(item)};
  const g=el('g',{class:'hit','data-facility':item.id,role:'button',tabindex:0,
    'aria-label':(item.name||item.id)+' 설비 상세',on:{click:open,keydown:e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();open(e)}}}});
  const [w,d,h]=placement?.size||[out?1.65:compact?.96:1.08,out?1.1:.7,
    ['ups','battery','switchgear','ats','crac','fan_coil','cdu','storage','fuel_cell'].includes(kind)?1.45:1];
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
  if(kind==='transformer' && !out) {
    // 실내 건식 변압기: 환기 외함 안에 3상 몰드 코일이 보인다.
    box(x,y,z,w,d,h,'#bac2b7');
    faceRect(w*.08,h*.13,w*.84,h*.7,'#31484c');
    for(let i=0;i<3;i++){
      faceRect(w*(.13+i*.26),h*.25,w*.21,h*.47,'#ad7b54');
      for(let n=0;n<6;n++)faceRect(w*(.14+i*.26),h*(.28+n*.065),w*.19,h*.014,'#d0a078');
      faceRect(w*(.21+i*.26),h*.35,w*.045,h*.27,'#535955');
    }
    for(let i=0;i<8;i++)faceRect(w*(.1+i*.105),h*.16,w*.014,h*.63,'#a3b5ae');
    faceRect(w*.42,h*.89,w*.16,h*.035,'#e9c375');
  } else if(['utility','substation','transformer'].includes(kind)) {
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
    box(x,y,z,w,d,h*.12,'#4b747a');
    for(const dy of [.26,.73])facilityVessel(g,x+w*.04,y+d*dy,z+h*.37,w*.83,Math.min(d*.22,h*.23),'#adc7c3');
    for(const dx of [.16,.51]){
      box(x+w*dx,y+d*.14,z+h*.61,w*.23,d*.47,h*.24,'#426e73');
      for(let n=0;n<5;n++)box(x+w*(dx+.025+n*.041),y+d*.14,z+h*.61,w*.012,d*.47,h*.24,'#86a7a5');
    }
    box(x+w*.75,y+d*.5,z+h*.57,w*.23,d*.41,h*.43,'#d5d8c5');
    const controls=el('g',{transform:roomFace(x+w*.75,y+d*.91,z+h*.57)});
    controls.appendChild(roomRect(w*.035,h*.17,w*.16,h*.16,'#2d5258'));g.appendChild(controls);
    for(const dy of [.26,.73])pipe([x+w*.9,y+d*dy,z+h*.37],[x+w,y+d*dy,z+h*.37],'#70bdc8',2.5);
  } else if(kind==='pump') {
    box(x,y,z,w,d,h*.15,'#627e82');
    facilityVessel(g,x+w*.4,y+d*.5,z+h*.53,w*.5,h*.27,'#4c888c');
    for(let n=0;n<5;n++)box(x+w*(.42+n*.09),y+d*.22,z+h*.27,w*.025,d*.57,h*.51,'#739f9e');
    const volute=el('g',{transform:roomFace(x+w*.29,y+d*.5,z+h*.49,true)});
    volute.appendChild(el('circle',{r:h*.36,fill:'#8cacaa',stroke:'#456f78','stroke-width':.035}));g.appendChild(volute);
    pipe([x,y+d*.5,z+h*.49],[x+w*.28,y+d*.5,z+h*.49],'#83c4c9',2.6);
    pipe([x+w*.25,y+d*.5,z+h*.55],[x+w*.25,y+d*.5,z+h],'#83c4c9',2.5);
    pipe([x+w*.16,y+d*.5,z+h*.88],[x+w*.34,y+d*.5,z+h*.88],'#d2b477',1.4);
  } else if(kind==='heat_exchanger') {
    box(x,y,z,w,d,h*.1,'#617e85');
    for(let i=0;i<9;i++)box(x+w*(.16+i*.071),y+d*.12,z+h*.15,w*.04,d*.68,h*.74,i%2?'#b8c8bc':'#749395');
    for(const dx of [.07,.8])box(x+w*dx,y+d*.04,z+h*.07,w*.1,d*.88,h*.91,'#527d85');
    for(const zz of [.24,.78])pipe([x+w*.15,y+d,z+h*zz],[x+w*.83,y+d,z+h*zz],zz>.5?'#d8a77a':'#77c7da',2);
  } else if(kind==='economizer') {
    // 프리쿨링 전환용 배관·밸브 스키드, 냉동기나 공랭 팬과 구분한다.
    box(x,y,z,w,d,h*.1,'#637f7e');
    for(const dx of [.1,.85])box(x+w*dx,y+d*.2,z,w*.045,d*.65,h*.96,'#8eaaa5');
    for(const zz of [.36,.77]){
      pipe([x+w*.09,y+d*.5,z+h*zz],[x+w*.93,y+d*.5,z+h*zz],zz>.5?'#d1ad82':'#73bbca',3);
      box(x+w*.43,y+d*.37,z+h*(zz-.07),w*.15,d*.26,h*.14,'#c0b697');
      pipe([x+w*.5,y+d*.5,z+h*zz],[x+w*.5,y+d*.5,z+h*(zz+.17)],'#d1b677',1.3);
      pipe([x+w*.4,y+d*.5,z+h*(zz+.17)],[x+w*.61,y+d*.5,z+h*(zz+.17)],'#d1b677',1.3);
    }
  } else if(kind==='fire' && !item.subtype) {
    for(let i=0;i<3;i++){
      const dx=w*(.18+i*.31);
      facilityCylinder(g,x+dx,y+d*.45,z,w*.12,h*.78,'#b05f54');
      pipe([x+dx,y+d*.45,z+h*.78],[x+dx,y+d*.45,z+h*.94],'#c7b49a',1.5);
      const gauge=iso(x+dx,y+d*.58,z+h*.84);g.appendChild(el('circle',{cx:gauge[0],cy:gauge[1],r:1.25,fill:'#e1ded0',stroke:'#617475','stroke-width':.5}));
    }
    pipe([x+w*.09,y+d*.45,z+h*.95],[x+w*.95,y+d*.45,z+h*.95],'#b49c7a',2);
    pipe([x+w*.93,y+d*.45,z+h*.95],[x+w*.93,y+d*.45,z+h],'#b49c7a',2);
  } else if(kind==='fire') {
    box(x,y,z,w,d,h,item.subtype==='panel'?'#b87870':'#d0d8cd');
    faceRect(w*.12,h*.45,w*.76,h*.35,'#344f53');
    faceRect(w*.17,h*.59,w*.34,h*.09,'#80b9ac');
    for(let i=0;i<4;i++)faceRect(w*(.16+i*.18),h*.23,w*.09,h*.07,i?'#c8b590':'#d97762');
  } else if(kind==='mantrap') {
    // 원형 인터록 포털: 앞·뒤 곡면 도어와 투명 측벽, 별도 리더.
    const circle=(zz)=>Array.from({length:33},(_,i)=>iso(x+w/2+Math.cos(i*Math.PI/16)*w*.48,y+d/2+Math.sin(i*Math.PI/16)*d*.48,z+zz));
    const bottom=circle(.04),top=circle(h-.12);
    g.appendChild(el('polygon',{points:pts(bottom),fill:'#8ca3a0',stroke:'#c9d8ca','stroke-width':.8}));
    for(let n=0;n<32;n++)g.appendChild(el('polygon',{points:pts([bottom[n],bottom[n+1],top[n+1],top[n]]),fill:'#a5d0cf',opacity:.18,stroke:'none'}));
    for(const dx of [.03,.94])box(x+w*dx,y+d*.42,z,w*.035,d*.16,h,'#a6bdb6');
    for(const yy of [y+d*.02,y+d*.98])pipe([x+w*.5,yy,z+.08],[x+w*.5,yy,z+h-.12],'#e1eadd',.8);
    g.appendChild(el('polygon',{points:pts(top),fill:'#b4c8bb',opacity:.85,stroke:'#d9e2d1','stroke-width':.9}));
    box(x+w*.87,y+d*.79,z+h*.4,w*.075,d*.06,h*.21,'#304e51');
  } else if(['door','metal_detector'].includes(kind)) {
    const height=placement?h:1.65,depth=kind==='metal_detector'?d:d*.4;
    for(const dx of [0,w-.1])box(x+dx,y,z,.1,depth,height,kind==='metal_detector'?'#c3c8bb':'#657f89');
    box(x,y,z+height-.1,w,depth,.1,'#819f9f');
    if(kind==='metal_detector'){
      for(const dx of [.025,w-.075])box(x+dx,y+depth,z+height*.22,.025,.01,height*.62,'#526f70');
      box(x+w*.35,y+depth,z+height-.085,w*.3,.012,.055,'#314d51');
    } else {const p=[[x+.12,y+depth,z+.05],[x+w-.12,y+depth,z+.05],[x+w-.12,y+depth,z+height-.12],[x+.12,y+depth,z+height-.12]];g.appendChild(el('polygon',{points:pts(p.map(a=>iso(...a))),fill:'#97c6ca',opacity:.42,stroke:'#b2d7d4','stroke-width':1}))}
  } else if(['cctv','weather'].includes(kind)) {
    if(kind==='cctv' && placement?.mount_z){
      pipe([x,y,z+h*.4],[x,y+d*.5,z+h*.4],'#c4cebd',2);
      box(x,y+d*.3,z,w,d*.7,h*.62,'#d6dbcc');
      const lens=el('g',{transform:roomFace(x,y+d,z)});lens.appendChild(el('circle',{cx:w*.5,cy:h*.3,r:w*.2,fill:'#253f4f',stroke:'#80a6a5','stroke-width':.025}));g.appendChild(lens);
    } else if(kind==='cctv'){box(x+w/2,y+d/2,z,.055,.065,1.6,'#a6b9bc');box(x+w/2-.18,y+d/2,z+1.5,.52,.2,.22,'#cad4cf');box(x+w/2+.32,y+d/2,z+1.53,.06,.18,.15,'#253f4f')}
    else {box(x+w/2,y+d/2,z,.055,.065,1.6,'#a6b9bc');for(const dx of [-.35,0,.35])pipe([x+w/2,y+d/2,z+1.65],[x+w/2+dx,y+d/2+.25,z+1.65],'#c7d5ce',1.4);box(x+w/2-.2,y+d/2-.1,z+.8,.43,.25,.4,'#d5d9ca')}
  } else if(kind==='guard_post') {
    box(x,y,z,w,d,1.3,'#b2b5a5');box(x-.07,y-.06,z+1.3,w+.14,d+.12,.1,'#6f8c87');
    faceRect(.12,.55,w-.24,.5,'#43767f');faceRect(w*.6,.06,.42,.95,'#54777a');
  } else if(kind==='immersion') {
    box(x,y,z,w,d,.55,'#9dafb3');g.appendChild(quad(x+.09,y+.09,z+.56,w-.18,d-.18,{fill:'#679c95'}));
    for(let i=0;i<4;i++)box(x+.14+i*.2,y+.18,z+.48,.09,d-.36,.26,'#344e56');
  } else if(kind==='badge_reader' && placement) {
    box(x,y,z,w,d,h,'#334d54');
    faceRect(w*.15,h*.27,w*.7,h*.5,'#a5c7c3');faceRect(w*.34,h*.12,w*.32,h*.05,'#aadd80');
  } else if(kind==='busway' && placement?.mount_z) {
    box(x,y,z,w,d,h,'#c6ba8a');
    for(const dx of [.15,w-.35]){pipe([x+dx,y+d/2,z+h],[x+dx,y+d/2,z+h+.3],'#aebfc0',.8);box(x+dx,y,z-.25,.22,d+.08,.25,'#768d8d')}
  } else if(kind==='busway') {
    for(const dx of [.1,w-.15])box(x+dx,y+.15,z,.035,.05,1.5,'#7e949e');
    box(x,y+.1,z+1.4,w,.23,.13,'#c6ba8a');
    for(let i=0;i<2;i++)box(x+.15+i*.5,y+.05,z+1.1,.22,.32,.3,'#7d9294');
  } else if(kind==='rack_mover') {
    box(x,y,z+.1,w,d,.28,'#c0a677');for(const dx of [.13,w-.18])facilityCylinder(g,x+dx,y+d,z,.09,.16,'#344950');
    box(x+.15,y+.15,z+.38,w-.3,d-.3,.12,'#7d9596');
  } else if(['switchgear','ats','ups','battery'].includes(kind)) {
    const dark=['ups','battery'].includes(kind);
    box(x,y,z,w,d,h,dark?'#627876':'#c5cabc');
    const bays=kind==='switchgear'?3:kind==='ups'?2:1;
    for(let n=0;n<bays;n++){
      const left=w*(n/bays+.035),width=w*(1/bays-.07);
      faceRect(left,h*.07,width,h*.87,dark?'#354e52':'#a7b5aa');
      faceRect(left+width*.1,h*.67,width*.72,h*.18,'#304d53');
      faceRect(left+width*.17,h*.73,width*.4,h*.06,'#81b6aa');
      faceRect(left+width*.85,h*.42,width*.035,h*.14,'#d5dac4');
      if(kind==='ups' || kind==='battery')for(let row=0;row<4;row++){
        faceRect(left+width*.08,h*(.13+row*.12),width*.7,h*.08,'#718981');
        faceRect(left+width*.12,h*(.15+row*.12),width*.035,h*.025,'#bbd5a4');
        for(let slot=0;slot<4;slot++)faceRect(left+width*(.23+slot*.12),h*(.16+row*.12),width*.06,h*.012,'#2d454b');
      } else {
        for(let slot=0;slot<4;slot++)faceRect(left+width*.12,h*(.15+slot*.045),width*.65,h*.012,'#65817d');
        faceRect(left+width*.48,h*.4,width*.025,h*.24,'#566e66');
        if(kind==='ats'){
          faceRect(left+width*.25,h*.58,width*.49,h*.023,'#d3c39d');
          for(const offset of [.24,.7])faceRect(left+width*offset,h*.56,width*.055,h*.09,'#b9ce9e');
        }
      }
    }
  } else if(kind==='disk_shredder') {
    box(x,y,z,w,d,h*.76,'#9ba99e');
    faceRect(w*.11,h*.12,w*.78,h*.51,'#536d70');
    faceRect(w*.16,h*.42,w*.66,h*.035,'#233f49');
    box(x+w*.1,y+d*.12,z+h*.76,w*.8,d*.76,h*.21,'#b8c3ad');
    g.appendChild(quad(x+w*.22,y+d*.29,z+h*.975,w*.56,d*.38,{fill:'#2c454b',stroke:'#d1d7bd','stroke-width':.6}));
    faceRect(w*.68,h*.8,w*.065,h*.065,'#cd7765');
  } else {
    box(x,y,z,w,d,h,type==='cooling'?'#8aa9b1':kind==='battery'?'#6e8684':'#98a8a4');
    faceRect(.1,h-.48,w-.2,.29,'#263e48');faceRect(.16,h-.4,w-.32,.05,accent);
    if(kind==='battery')for(let i=0;i<4;i++){faceRect(.14,.12+i*.2,w-.28,.14,'#394f4e');faceRect(.2,.16+i*.2,.09,.035,'#a6cf96')}
    else vents(h-.55);
    if(['crac','fan_coil','cdu'].includes(kind)){
      // 실내 공조 캐비닛은 전면 흡입 그릴, CDU는 배관 접속부로 구분한다.
      faceRect(w*.08,h*.05,w*.84,h*.025,'#d0d5c2');
      for(const dx of [w*.86,w*.94])pipe([x+dx,y,z+.06],[x+dx,y,z+.73],dx<w*.9?'#75c4d4':'#d0a07c',1.8);
    }
  }
  const point=iso(x+w*.5,y+d,z+.12);
  g.appendChild(el('circle',{cx:point[0],cy:point[1],r:2,fill:bad?'#f48181':'#b4d6a0',stroke:'#263e49','stroke-width':.7}));
  if(bad){const p=iso(x+w/2,y+d/2,z+h+.1);g.appendChild(warnBadge(p[0],p[1]-5))}
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
