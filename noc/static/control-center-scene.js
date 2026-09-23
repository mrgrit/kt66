/* 업무 공간은 코드로 그린다. xOC에는 실제 층 번호나 가상의 운영 수치를 넣지 않는다. */
function drawControlCenterRoom(fid,detail) {
  const lab=fid==='5F',z=.22,accent=lab?'#b6a1e2':'#7bd5c3';
  const g=el('g',{'data-control-room':fid});
  g.appendChild(prism(0,0,0,GW,GD,z,lab?'#768390':'#3c5664'));
  g.appendChild(quad(0,0,z+.005,GW,GD,{fill:lab?'#8192a0':'#405b68'}));
  for(let x=.3;x<GW;x+=.8)for(let y=.3;y<GD;y+=.8)
    g.appendChild(quad(x,y,z+.012,.78,.78,{fill:lab?'#8497a4':'#46616e',stroke:lab?'#97a7b1':'#53727e','stroke-width':.35}));
  g.appendChild(prism(0,0,z,GW,.15,1.8,lab?'#adbac2':'#546c7a'));
  g.appendChild(prism(0,0,z,.15,GD,1.35,lab?'#93a8b7':'#496477'));
  g.appendChild(roomLine([.2,.22,z+1.67],[GW-.25,.22,z+1.67],accent,2));
  // 읽을 수 있는 벽면 표식. 좌표 변환의 Y축을 양수로 두어 글자를 뒤집지 않는다.
  const sign=el('a',{href:dcURL(lab?'researchlab':'xoc'),'aria-label':lab?'AI 연구소 전문가 업무 방식 실험실':'xOC 제한구역 관제 화면',on:{click:e=>e.stopPropagation()}});
  sign.appendChild(roomLettering(.65,.19,z+2.05,lab?'AI RESEARCH LAB  /  5F':'xOC  /  RESTRICTED',.26,accent));
  g.appendChild(sign);
  const station=(id,x,y)=>{const worker=crewOf(fid).find(w=>w.id===id);if(worker)g.appendChild(drawWorkstation(worker,x,y,z,detail))};
  if(lab) {
    // 자료 열람 벽과 시험 보드. 장식 그래프는 실시간 성능으로 표시하지 않는다.
    for(let n=0;n<3;n++) {
      const x=.6+n*1.35;
      g.appendChild(prism(x,.35,z,1.14,.35,1.48,'#435c6b'));
      for(let shelf=0;shelf<3;shelf++)for(let book=0;book<6;book++)
        g.appendChild(prism(x+.08+book*.16,.65,z+.13+shelf*.44,.11,.08,.31,['#b6a1e2','#a5c2c8','#d8c2a2'][book%3]));
    }
    g.appendChild(prism(6.4,.23,z+.6,4.6,.09,1.08,'#d0d7d4'));
    g.appendChild(roomLettering(6.65,.34,z+1.46,'RESEARCH  >  TEST  >  REVIEW',.14,'#435965'));
    for(let i=0;i<3;i++)for(let j=0;j<2;j++) {
      const f=el('g',{transform:roomFace(6.8+i*1.3,.35,z+.78+j*.25)});
      f.appendChild(roomRect(0,0,.75,.14,['#ac9ed7','#8fbcbc','#c8b68e'][i]));g.appendChild(f);
    }
    // 독립 연구·평가 자리 사이에 넓은 중앙 동선을 둔다.
    station('skill-researcher',2.05,3.45);station('skill-evaluator',7.8,3.45);
    g.appendChild(prism(4.9,5.6,z,1.9,1.05,.72,'#687d8e'));
    g.appendChild(quad(5,5.7,z+.74,1.7,.85,{fill:'#b4bdc0'}));
    g.appendChild(roomLettering(4.98,6.68,z+.58,'EXPERIMENTS',.12,'#e0e9ec'));
  } else {
    // 두 관제 직무는 별도의 콘솔·모니터 벽을 갖는다.
    for(let side=0;side<2;side++) {
      const x=.65+side*5.9;
      for(let panel=0;panel<3;panel++) {
        const f=el('g',{transform:roomFace(x+panel*1.55,.24,z+.57)});
        f.appendChild(roomRect(0,0,1.4,.94,'#152b39',{rx:.035}));
        f.appendChild(roomRect(.06,.07,1.28,.8,'#214353'));
        for(let line=0;line<5;line++)f.appendChild(roomRect(.14,.16+line*.11,.84-(line%3)*.14,.025,side?'#91b7d3':'#7fae9c'));
        g.appendChild(f);
      }
      g.appendChild(roomLettering(x,.26,z+1.75,side?'SECURITY OPERATIONS':'AI AGENT OPERATIONS',.19,side?'#b5ccde':accent));
    }
    // 4층의 과밀을 풀기 위해 담당자별 전면 작업석을 확보한다.
    station('agent-supervisor',2.15,3.7);station('soc-analyst',8,3.7);
    // 출입 인증 구역은 앞 모서리에 낮게 배치해 근무자 클릭을 가리지 않는다.
    g.appendChild(quad(.5,6.45,z+.03,2.3,1.1,{fill:'#364751',stroke:'#8fbdac','stroke-width':1}));
    g.appendChild(prism(.6,6.5,z,.28,.32,.82,'#607885'));
    g.appendChild(prism(2.4,6.5,z,.28,.32,.82,'#607885'));
    g.appendChild(roomLettering(.9,7.2,z+.08,'ACCESS CONTROL',.12,'#bdd6d2'));
  }
  g.appendChild(roomLine([0,GD,z+.02],[GW,GD,z+.02],accent,1.5));
  g.appendChild(roomLine([GW,0,z+.02],[GW,GD,z+.02],accent,1.2));
  return g;
}
