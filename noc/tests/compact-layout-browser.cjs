/* 실제 화면의 크기·겹침·픽킹·통로를 검사한다. 운영 변경과 모델 호출은 하지 않는다. */
const assert=require('node:assert/strict'),fs=require('node:fs');
module.exports=async page=>{
 const errors=[],writes=[],measurements=[],selection=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.setCacheEnabled(false);await page.setRequestInterception(true);
 page.on('request',r=>['GET','HEAD'].includes(r.method())?r.continue():(writes.push(r.method()),r.abort()));
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/',{waitUntil:'networkidle2'});
 await page.waitForFunction(()=>typeof LAYOUT!=='undefined'&&LAYOUT);
 await page.evaluate(()=>enterBuilding());await page.waitForSelector('[data-building-layout]');
 await page.evaluate(()=>{clearInterval(pollingTimer);clearInterval(workerStateTimer)});
 // 같은 화면에서 수정 전에 측정한 층 가로 크기(px). 최소 20% 확대를 유지한다.
 const cases=[[1920,1080,309.6],[1440,1000,250.3],[1366,768,157.8],[1024,768,94.0],[768,1024,161.5],[390,844,105.9],[320,568,61.4]];
 for(const [width,height,before] of cases){
  await page.setViewport({width,height,hasTouch:width<500});await page.evaluate(()=>enterBuilding());
  await new Promise(r=>setTimeout(r,300));await page.mouse.move(2,2);
  const data=await page.evaluate(()=>{
   const svg=document.querySelector('#scene'),view=svg.viewBox.baseVal,screen=svg.getBoundingClientRect(),nodes=[...svg.querySelectorAll('[data-layout-outline]')];
   const canvas=document.createElement('canvas');canvas.width=Math.ceil(screen.width);canvas.height=Math.ceil(screen.height);
   const ctx=canvas.getContext('2d');ctx.scale(screen.width/view.width,screen.height/view.height);ctx.translate(-view.x,-view.y);
   ctx.fillStyle='rgba(255,0,0,0.5)';
   for(const node of nodes){const points=JSON.parse(node.dataset.layoutOutline);ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();ctx.fill()}
   const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let overlap=0;
   for(let i=3;i<pixels.length;i+=4)if(pixels[i]>160)overlap++;
   const rects=nodes.map(e=>e.getBoundingClientRect());
   return {rooms:nodes.length,overlap,floorWidth:svg.querySelector('[data-floorplan="basement"]').getBoundingClientRect().width,
    clipped:rects.some(b=>b.left<screen.left-1||b.right>screen.right+1||b.top<screen.top-1||b.bottom>screen.bottom+1),
    overflow:document.documentElement.scrollWidth>innerWidth+1,bottom:document.querySelector('#stage').getBoundingClientRect().bottom};
  });
  assert.equal(data.rooms,8);assert.equal(data.overlap,0,'겹침 '+width);assert.equal(data.clipped,false);assert.equal(data.overflow,false);assert.ok(data.bottom<=height+1);
  assert.ok(data.floorWidth>=before*1.2,'그림 크기 회귀 '+JSON.stringify({width,before,...data}));
  measurements.push({width,height,...data,gainPercent:Math.round((data.floorWidth/before-1)*100)});
  await page.screenshot({path:'/tmp/kt66-inspect-ui/compact-building-'+width+'.png'});
  await page.evaluate(()=>enterFloor('3F'));await new Promise(r=>setTimeout(r,200));
  const geometry=await page.evaluate(()=>{
   const p=AI_FLOORPLAN,rects=[{id:'rack',rect:[...p.rack.pos,...p.rack.size]},...Object.entries(p.equipment).filter(([,a])=>!a.mount_z).map(([id,a])=>({id,rect:[...a.pos,...a.size.slice(0,2)]}))];
   const overlap=(a,b)=>Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0])>0.01&&Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1])>0.01;
   return {blocked:p.clearances.flatMap(c=>rects.filter(a=>overlap(c.rect,a.rect)).map(a=>c.id+'/'+a.id)),
    outside:rects.filter(a=>a.rect[0]<0||a.rect[1]<0||a.rect[0]+a.rect[2]>12||a.rect[1]+a.rect[3]>8).map(a=>a.id),
    expected:physicalFacilitiesOf('3F').map(a=>a.id).sort(),actual:[...document.querySelectorAll('#scene [data-facility]')].map(e=>e.dataset.facility).sort()};
  });
  assert.deepEqual(geometry.blocked,[]);assert.deepEqual(geometry.outside,[]);assert.deepEqual(geometry.actual,geometry.expected);
  assert.equal(await page.$$eval('[data-nvidia-device]',e=>e.length),7);
  for(const id of ['dgx-spark-01','dgx-01','dgx-02','dgx-03','dgx-06','thor-02','thor-03']){
   const point=await page.$eval('#scene [data-asset="'+id+'"]',(e,id)=>{const b=e.getBoundingClientRect();for(const yy of [.5,.3,.7,.15,.85])for(const xx of [.5,.3,.7,.15,.85]){const x=b.left+b.width*xx,y=b.top+b.height*yy;if(document.elementFromPoint(x,y)?.closest('[data-asset]')?.dataset.asset===id)return {x,y}}return null},id);
   assert.ok(point,'장비 선택 가림 '+width+' '+id);
   if(width<500)await page.touchscreen.tap(point.x,point.y);else await page.mouse.click(point.x,point.y);
   await page.waitForSelector('#drawer:not([hidden])');assert.ok((await page.$eval('#dr-body',e=>e.textContent)).includes(id));await page.click('#dr-close');
  }
  const worker=await page.$eval('[data-worker="gpu-platform-engineer"] [data-agent-hit="head"]',e=>{const b=e.getBoundingClientRect(),x=b.left+b.width/2,y=b.top+b.height/2;return {x,y,id:document.elementFromPoint(x,y)?.closest('[data-worker]')?.dataset.worker}});
  assert.equal(worker.id,'gpu-platform-engineer');await page.mouse.click(worker.x,worker.y);await page.waitForSelector('#drawer:not([hidden])');await page.click('#dr-close');
  const facilities=await page.$$eval('#scene [data-facility]',els=>els.map(e=>{const b=e.getBoundingClientRect();let hit=false;for(const yy of [.2,.4,.6,.8])for(const xx of [.2,.4,.6,.8])if(document.elementFromPoint(b.left+b.width*xx,b.top+b.height*yy)?.closest('[data-facility]')?.dataset.facility===e.dataset.facility)hit=true;return {id:e.dataset.facility,hit}}));
  assert.deepEqual(facilities.filter(f=>!f.hit).map(f=>f.id),[],'시설 선택 가림 '+width);
  selection.push({width,facilities});console.log(JSON.stringify({width,gainPercent:measurements.at(-1).gainPercent,hiddenFacilities:facilities.filter(f=>!f.hit).map(f=>f.id)}));await page.mouse.move(2,2);
  await page.screenshot({path:'/tmp/kt66-inspect-ui/compact-3F-'+width+'.png'});
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync('/tmp/kt66-compact-layout-validation.json',JSON.stringify({measurements,selection,errors,writes},null,2));
 console.log(JSON.stringify({measurements,selection,errors,writes}));
};
