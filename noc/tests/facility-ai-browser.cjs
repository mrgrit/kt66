const assert=require('node:assert/strict');
module.exports=async(page,{screenshots='/tmp/kt66-inspect-ui'}={})=>{
 const errors=[],writes=[],services=[];let fixture=null;
 page.on('pageerror',e=>errors.push(String(e)));await page.setCacheEnabled(false);
 await page.setRequestInterception(true);page.on('request',r=>{
  if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}
  if(fixture && new URL(r.url()).pathname==='/api/assets/dgx-01/services')return r.respond({status:200,contentType:'application/json',body:JSON.stringify(fixture)});
  r.continue();
 });
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/?view=site',{waitUntil:'networkidle2'});
 await page.waitForSelector('[data-site="outdoor"]');
 await page.evaluate(()=>{clearInterval(pollingTimer);clearInterval(workerStateTimer)});
 assert.equal(await page.$$eval('#scene [data-facility]',n=>n.length),12);
 assert.match(await page.$eval('#scene-floor-code',e=>e.textContent),/OUTDOOR/);
 await page.screenshot({path:screenshots+'/facility-outdoor.png'});
 for(const mode of ['SITE','1F','2F','3F','4F']){
  await page.evaluate(mode=>mode==='SITE'?enterSite():enterFloor(mode),mode);
  const ids=await page.$$eval('#scene [data-facility]',n=>n.map(e=>e.dataset.facility));
  assert.equal(new Set(ids).size,ids.length);
  if(mode==='1F'){assert.ok(!ids.includes('ct-01'));assert.ok(ids.includes('chiller-01'));await page.screenshot({path:screenshots+'/facility-indoor.png'})}
  for(const id of ids){
   await page.$eval('#scene [data-facility="'+id+'"]',e=>e.focus());await page.keyboard.press('Enter');
   const text=await page.$eval('#dr-body',e=>e.textContent);
   for(const phrase of [id,'교육용 가상 설비','역할과 배치 이유','현재 시뮬레이션 상태','설계·설정값','점검할 내용'])assert.ok(text.includes(phrase),id+': '+phrase);
   if(mode==='SITE')assert.ok(text.includes('옥외 부지 · 층 구분 없음'));
   if(id==='crac-02')assert.ok(text.includes('아일 냉방 합계'));
   await page.click('#dr-close');
  }
 }
 await page.evaluate(()=>enterSite());
 await page.click('#scene [data-facility="ct-01"]');
 assert.match(await page.$eval('#dr-body',e=>e.textContent),/냉각탑/);
 await page.screenshot({path:screenshots+'/facility-detail.png'});await page.click('#dr-close');
 await page.evaluate(()=>enterFloor('3F'));
 for(const id of ['dgx-spark-01','dgx-01','dgx-02','dgx-03','dgx-06','thor-02','thor-03']){
  await page.$eval('#scene [data-asset="'+id+'"]',e=>e.focus());await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('.service-card'),{timeout:15000});
  const data=await page.evaluate(id=>AI_SERVICE_CACHE.get(id).data,id);
  assert.equal(data.asset_id,id);
  for(const service of data.services)assert.equal(service.status,'응답 확인',id+' '+service.name);
  assert.equal(data.services.length,id==='thor-03'?4:id==='thor-02'?2:1);
  services.push({id,services:data.services.map(s=>({name:s.name,status:s.status,models:s.model_count,loaded:s.loaded_count}))});
  if(id==='thor-03'){await new Promise(r=>setTimeout(r,250));await page.screenshot({path:screenshots+'/ai-services-live.png'});await page.click('[data-service-jump="vllm"]');await new Promise(r=>setTimeout(r,400));await page.screenshot({path:screenshots+'/ai-services-vllm.png'});}
  await page.click('#dr-close');
 }
 // More than five models, escaped server metadata, and unknown vs empty residency.
 fixture={asset_id:'dgx-01',checked_at:Date.now()/1000,interval_sec:60,services:[{id:'ollama',platform:'ollama',name:'Ollama',purpose:'테스트',endpoint:'http://192.0.2.10:11434',status:'응답 확인',model_count:8,models:Array.from({length:8},(_,n)=>({name:n===0?'<img src=x onerror=alert(1)>':'model-'+n,parameter_size:'7B'})),loaded_count:1,loaded:[{name:'model-2',size:3*1024**3,size_vram:2*1024**3,context_length:8192}]}]};
 await page.evaluate(()=>{AI_SERVICE_CACHE.delete('dgx-01');openAsset('dgx-01')});
 await page.waitForSelector('[data-service-expand]');
 assert.equal(await page.$eval('[data-service-expand]',e=>e.open),false);
 assert.equal(await page.$$eval('.ai-services img',e=>e.length),0);
 assert.match(await page.$eval('[data-ai-services]',e=>e.textContent),/GPU 배치 2.0 GiB/);
 await page.click('[data-service-expand] summary');
 await page.evaluate(()=>openAsset('dgx-01'));
 assert.equal(await page.$eval('[data-service-expand]',e=>e.open),true);
 fixture.services[0].loaded=null;fixture.services[0].loaded_error='인증 필요';
 await page.evaluate(()=>{AI_SERVICE_CACHE.delete('dgx-01');openAsset('dgx-01')});
 await page.waitForFunction(()=>document.querySelector('[data-ai-services]').textContent.includes('인증 필요'));
 assert.doesNotMatch(await page.$eval('[data-ai-services]',e=>e.textContent),/현재 적재된 모델이 없습니다/);
 await page.click('#dr-close');fixture=null;
 const sizes=[];
 for(const [width,height] of [[1440,1000],[1366,768],[390,844],[320,568]]){
  await page.setViewport({width,height});
  for(const mode of ['1F','SITE','BUILDING']){
   await page.evaluate(mode=>mode==='SITE'?enterSite():mode==='BUILDING'?enterBuilding():enterFloor(mode),mode);
   await new Promise(r=>setTimeout(r,200));
   const fit=await page.evaluate(()=>{
    const scene=document.querySelector('#scene').getBoundingClientRect();
    const items=[...document.querySelectorAll('#scene [data-facility]')].map(e=>e.getBoundingClientRect());
    return {overflow:document.documentElement.scrollWidth>innerWidth+1,stage:document.querySelector('#stage').getBoundingClientRect().bottom,clipped:items.some(b=>b.left<scene.left-1||b.top<scene.top-1||b.right>scene.right+1||b.bottom>scene.bottom+1)};
   });
   assert.equal(fit.overflow,false);assert.equal(fit.clipped,false);assert.ok(fit.stage<=height+1,JSON.stringify({mode,width,height,...fit}));sizes.push({width,height,mode,...fit});
   if(width===390&&mode==='SITE')await page.screenshot({path:screenshots+'/facility-outdoor-mobile.png'});
  }
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({services,sizes,errors,writes}));
};
