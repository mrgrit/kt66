/* 읽기 전용: 배치 간섭·누락·선택 가능성·화면 맞춤을 실제 SVG에서 확인한다. */
const assert=require('node:assert/strict');
module.exports=async(page,{screenshots='/tmp/kt66-inspect-ui',fixture=false}={})=>{
 const errors=[],writes=[],checked=[];
 page.on('pageerror',e=>errors.push(String(e)));await page.setCacheEnabled(false);
 await page.setRequestInterception(true);page.on('request',r=>{
  if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}
  const path=new URL(r.url()).pathname;
  if(fixture && ['/api/layout','/api/roster'].includes(path))return r.respond({status:200,contentType:'application/json',body:require('fs').readFileSync('/tmp/kt66-floor-'+(path.endsWith('layout')?'layout':'roster')+'.json','utf8')});
  r.continue();
 });
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/?floor=B1',{waitUntil:'networkidle2'});
 await page.waitForSelector('[data-floorplan=basement]');
 await page.evaluate(()=>{clearInterval(pollingTimer);clearInterval(workerStateTimer)});
 const geometry=await page.evaluate(()=>{
  const errors=[],P=BASEMENT_FLOORPLAN;
  const overlap=(a,b)=>a[0]<b[0]+b[2]-.001&&a[0]+a[2]>b[0]+.001&&a[1]<b[1]+b[3]-.001&&a[1]+a[3]>b[1]+.001;
  const solid=Object.entries(P.equipment).filter(([,p])=>!p.mount_z).map(([id,p])=>({id,rect:[...p.pos,...p.size.slice(0,2)],room:p.room}));
  for(let i=0;i<solid.length;i++){
   const a=solid[i],r=P.rooms.find(r=>r.id===a.room).rect;
   if(a.rect[0]<r[0]||a.rect[1]<r[1]||a.rect[0]+a.rect[2]>r[0]+r[2]||a.rect[1]+a.rect[3]>r[1]+r[3])errors.push(a.id+' outside room');
   for(const b of solid.slice(i+1))if(overlap(a.rect,b.rect))errors.push(a.id+' overlaps '+b.id);
   if(overlap(a.rect,[.12,4.39,11.76,.86]))errors.push(a.id+' blocks corridor');
   for(const area of P.clearances)if(overlap(a.rect,area.rect))errors.push(a.id+' blocks maintenance '+area.id);
   for(const path of P.paths)for(let n=1;n<path.points.length;n++){
    const u=path.points[n-1],v=path.points[n],route=[Math.min(u[0],v[0])-.06,Math.min(u[1],v[1])-.06,Math.abs(u[0]-v[0])+.12,Math.abs(u[1]-v[1])+.12];
    if(overlap(a.rect,route))errors.push(a.id+' blocks route '+path.id);
   }
  }
  const walls=[...document.querySelectorAll('[data-wall-bounds]')].map(e=>e.dataset.wallBounds.split(',').map(Number));
  for(const d of P.doors){
   const aperture=d.axis==='x'?[d.x,d.y-.01,d.width,.02]:[d.x-.01,d.y,.02,d.width];
   if(walls.some(w=>overlap(aperture,w)))errors.push('wall blocks door '+d.id);
  }
  const l=LOBBY_FLOORPLAN,center=l.entrance.x+l.entrance.width/2;
  for(const id of ['mantrap-01','mdet-01']){
   const p=l.equipment[id];if(Math.abs(p.pos[0]+p.size[0]/2-center)>.02)errors.push(id+' is off entrance route');
  }
  if(l.entrance.y<7.8||l.equipment['mdet-01'].pos[1]<=l.equipment['mantrap-01'].pos[1])errors.push('invalid entrance sequence');
  return {errors,solid:solid.length,doors:P.doors.length,clearances:P.clearances.length};
 });
 assert.deepEqual(geometry.errors,[]);
 for(const floor of ['B1','1F']){
  await page.evaluate(f=>enterFloor(f),floor);
  const ids=await page.$$eval('#scene [data-facility]',els=>els.map(e=>e.dataset.facility));
  const expected=await page.evaluate(f=>physicalFacilitiesOf(f).map(i=>i.id),floor);
  assert.deepEqual(ids.slice().sort(),expected.slice().sort());assert.equal(new Set(ids).size,ids.length);
  assert.equal(ids.length,floor==='B1'?15:5);
  const workers=await page.$$eval('#scene [data-agent-ring]',els=>els.map(e=>e.dataset.agentRing));
  assert.deepEqual(workers,[floor==='B1'?'facility-engineer':'physical-security']);
  for(const id of ids){
   // 실제 포인터로 선택할 수 있는 노출 면적이 있어야 한다.
   const point=await page.evaluate(id=>{
    const e=document.querySelector('#scene [data-facility="'+id+'"]'),b=e.getBoundingClientRect();
    for(let y=b.top+1;y<b.bottom;y+=2)for(let x=b.left+1;x<b.right;x+=2){
     const hit=document.elementFromPoint(x,y)?.closest('[data-facility]');if(hit===e)return {x,y};
    }
    return null;
   },id);
   assert.ok(point,'occluded equipment: '+id);await page.mouse.click(point.x,point.y);
   assert.ok(await page.$eval('#dr-body',(e,id)=>e.textContent.includes(id),id),id+' wrong drawer');
   assert.ok(await page.$('[data-placement-reason]'),id+' placement explanation');
   await page.click('#dr-close');
   await page.$eval('#scene [data-facility="'+id+'"]',e=>e.focus());await page.keyboard.press('Enter');
   assert.ok(await page.$eval('#dr-body',(e,id)=>e.textContent.includes(id),id));await page.click('#dr-close');
   checked.push(id);
  }
  await page.mouse.move(230,220);await page.screenshot({path:screenshots+'/'+floor.toLowerCase()+'-facility-plan.png'});
 }
 const sizes=[];
 for(const [width,height] of [[1440,1000],[1366,768],[390,844],[320,568]]){
  await page.setViewport({width,height});
  for(const mode of ['B1','1F','SITE','BUILDING']){
   await page.evaluate(mode=>mode==='SITE'?enterSite():mode==='BUILDING'?enterBuilding():enterFloor(mode),mode);
   await new Promise(r=>setTimeout(r,180));
   const fit=await page.evaluate(()=>{
    const scene=document.querySelector('#scene').getBoundingClientRect();
    const items=[...document.querySelectorAll('#scene [data-facility]')].map(e=>e.getBoundingClientRect());
    return {overflow:document.documentElement.scrollWidth>innerWidth+1,stage:document.querySelector('#stage').getBoundingClientRect().bottom,clipped:items.some(b=>b.left<scene.left-1||b.top<scene.top-1||b.right>scene.right+1||b.bottom>scene.bottom+1)};
   });
   assert.equal(fit.overflow,false,JSON.stringify({width,mode,fit}));assert.equal(fit.clipped,false,JSON.stringify({width,mode,fit}));assert.ok(fit.stage<=height+1,JSON.stringify({width,height,mode,fit}));sizes.push({width,height,mode});
   if(width===390&&['B1','1F'].includes(mode))await page.screenshot({path:screenshots+'/'+mode.toLowerCase()+'-facility-mobile.png'});
  }
 }
 await page.setViewport({width:1440,height:1000});await page.evaluate(()=>enterBuilding());
 assert.equal(await page.$$eval('#scene [data-site=outdoor] [data-facility]',els=>els.length),12);
 await page.screenshot({path:screenshots+'/building-with-basement.png'});
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({geometry,checked,sizes,errors,writes}));
};
