const assert=require('node:assert/strict'),fs=require('node:fs');
module.exports=async page=>{
 const errors=[],writes=[],sizes=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.setCacheEnabled(false);await page.setRequestInterception(true);
 page.on('request',r=>{if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}r.continue()});
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/?floor=5F',{waitUntil:'networkidle2'});
 await page.waitForSelector('[data-control-room="5F"]');
 await page.evaluate(()=>{clearInterval(pollingTimer);clearInterval(workerStateTimer)});
 for(const [width,height] of [[1440,1000],[1366,768],[390,844],[320,568]]){
  await page.setViewport({width,height,hasTouch:width<500});
  for(const [floor,workers] of [['5F',['skill-researcher','skill-evaluator']],['XOC',['agent-supervisor','soc-analyst']],['4F',['service-desk','ops-lead','compliance-auditor','application-developer']]]){
   await page.evaluate(f=>enterFloor(f),floor);
   await new Promise(r=>setTimeout(r,300));
   const actual=await page.$$eval('#scene [data-worker]',nodes=>nodes.map(e=>e.dataset.worker));
   assert.deepEqual(actual.sort(),workers.slice().sort());
   for(const id of workers){
    const point=await page.$eval('#scene [data-worker="'+id+'"] [data-agent-hit="head"]',e=>{const b=e.getBoundingClientRect();const x=b.left+b.width/2,y=b.top+b.height/2;return {x,y,hit:document.elementFromPoint(x,y)?.closest('[data-worker]')?.dataset.worker}});
    assert.equal(point.hit,id,JSON.stringify({width,floor,...point}));
    if(width<500)await page.touchscreen.tap(point.x,point.y);else await page.mouse.click(point.x,point.y);
    await page.waitForSelector('#drawer:not([hidden])');
    assert.ok((await page.$eval('#dr-body',e=>e.textContent)).includes(id));
    await page.click('#dr-close');
   }
   const measure=await page.evaluate(()=>{
    const svg=document.querySelector('#scene').getBoundingClientRect();
    return {overflow:document.documentElement.scrollWidth>innerWidth+1,stageBottom:document.querySelector('#stage').getBoundingClientRect().bottom,
     clipped:[...document.querySelectorAll('#scene [data-worker-figure]')].some(e=>{const b=e.getBoundingClientRect();return b.left<svg.left-1||b.right>svg.right+1||b.top<svg.top-1||b.bottom>svg.bottom+1})};
   });
   assert.equal(measure.overflow,false);assert.equal(measure.clipped,false);assert.ok(measure.stageBottom<=height+1,JSON.stringify({width,height,floor,...measure}));
   sizes.push({width,floor,...measure});await page.mouse.move(2,2);
   await page.screenshot({path:'/tmp/kt66-inspect-ui/center-'+floor+'-'+width+'.png'});
  }
  await page.evaluate(()=>enterBuilding());
  await page.mouse.move(2,2);
  assert.equal(await page.$$eval('[data-building-floor]',e=>e.length),7);
  // 등각도 그림의 빈 모서리는 겹칠 수 있다. 실제 윤곽의 겹침은 compact-layout-browser에서 픽셀로 검사한다.
  assert.equal(await page.$$eval('[data-layout-outline]',e=>e.length),8);
  await page.screenshot({path:'/tmp/kt66-inspect-ui/center-building-'+width+'.png'});
 }
 const key=fs.readFileSync('/home/ccc/work/kt66/.env','utf8').split('\n').find(l=>l.startsWith('API_KEY=')).split('=').slice(1).join('=').trim().replace(/^['"]|['"]$/g,'');
 for(const route of ['xoc','research-lab']){
  await page.setViewport({width:1440,height:1000});
  await page.goto('http://192.168.12.100:8050/'+route,{waitUntil:'networkidle2'});
  assert.equal(await page.$eval('#workspace',e=>e.hidden),true);
  await page.type('#center-key',key);await page.click('#unlock button');
  await page.waitForSelector('#workspace:not([hidden])');
  assert.ok(!(await page.$eval('#message',e=>e.className)).includes('error'));
  for(const width of [1440,390,320]){
   await page.setViewport({width,height:1000});
   await new Promise(r=>setTimeout(r,350));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,route+' '+width);
   await page.screenshot({path:'/tmp/kt66-inspect-ui/center-'+route+'-page-'+width+'.png'});
  }
 }
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/agent-control',{waitUntil:'networkidle2'});
 assert.equal(await page.$$eval('[data-run]',e=>e.length),0);
 await page.type('#evidence-key',key);await page.click('#evidence-auth button');
 await page.waitForSelector('[data-run]');
 await page.click('[data-run]');await page.waitForSelector('.case-head');
 for(const width of [1440,390,320]){
  await page.setViewport({width,height:1000});
  await new Promise(r=>setTimeout(r,350));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'evidence '+width);
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({floors:7,newWorkers:3,clicks:sizes.length,viewports:4,authenticatedPages:3,errors,writes}));
};
