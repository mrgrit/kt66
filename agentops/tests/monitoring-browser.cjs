/* 실제 운영 데이터는 읽기만. 장애·고위험 경보 표본은 브라우저 응답으로만 제공한다. */
const assert=require('node:assert/strict'),fs=require('node:fs');
module.exports=async page=>{
 const errors=[],writes=[],sizes=[];let fixture=null;
 const key=fs.readFileSync('/home/ccc/work/kt66/.env','utf8').split('\n').find(l=>l.startsWith('API_KEY=')).split('=').slice(1).join('=').trim().replace(/^['"]|['"]$/g,'');
 await page.setCacheEnabled(false);await page.setRequestInterception(true);
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',r=>{if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}
  if(fixture&&new URL(r.url()).pathname==='/api/monitoring/summary')return r.respond({status:200,contentType:'application/json',body:JSON.stringify(fixture)});
  r.continue();});
 await page.setViewport({width:1440,height:1000,hasTouch:true});
 await page.goto('http://192.168.12.100:8050/xoc',{waitUntil:'networkidle2'});
 assert.equal(await page.$eval('#workspace',e=>e.hidden),true);
 assert.equal(await page.evaluate(async()=> (await fetch('/api/monitoring/summary')).status),401);
 await page.type('#center-key',key);await page.click('#unlock button');await page.waitForSelector('#workspace:not([hidden])');
 assert.equal(await page.$eval('#center-key',e=>e.value),'');
 const live=await page.evaluate(async k=>(await fetch('/api/monitoring/summary',{headers:{'x-api-key':k}})).json(),key);
 assert.equal(await page.$$eval('[data-finding]',e=>e.length),live.xoc.groups.length);
 assert.ok(live.xoc.totals.open>=live.xoc.group_total);
 for(const width of [1920,1440,1366,1024,768,390,320]){
  await page.setViewport({width,height:width<500?844:1000,hasTouch:true});
  await new Promise(r=>setTimeout(r,300)); // 공통 메뉴의 반응형 슬라이드가 끝난 뒤 실제 클릭
  for(const view of ['xoc','soc']){
   if(!await page.$('.oc-tabs [data-view="'+view+'"]'))throw new Error('관제 탭 이탈 '+JSON.stringify({width,view,url:page.url(),errors}));
   await page.click('.oc-tabs [data-view="'+view+'"]');await new Promise(r=>setTimeout(r,150));
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   assert.equal(overflow,false,view+' '+width);sizes.push({view,width,overflow});
   if([1440,390,320].includes(width))await page.screenshot({path:'/tmp/kt66-inspect-ui/monitoring-'+view+'-'+width+'.png',fullPage:true});
  }
 }
 await page.setViewport({width:1440,height:1000,hasTouch:true});await page.click('.oc-tabs [data-view=xoc]');
 await page.click('[data-finding]');await page.waitForSelector('#detail[open]');
 assert.ok((await page.$eval('#detail-body',e=>e.textContent)).includes('이 대표 사건 한 건만 판정'));
 await page.click('#close');await page.click('.oc-tabs [data-view=cases]');await page.waitForSelector('[data-case]');
 await page.select('#case-status','resolved');await page.waitForFunction(()=>document.querySelector('#case-caption').textContent.startsWith('0건'));
 await page.select('#case-kind','tickets');await page.waitForSelector('[data-case]');
 assert.ok((await page.$eval('#case-caption',e=>e.textContent)).includes('참고 기록'));
 await page.click('[data-case]');await page.waitForSelector('#detail[open] .markdown-body');assert.equal(await page.$$eval('.markdown-body script,.markdown-body img',e=>e.length),0);
 await page.screenshot({path:'/tmp/kt66-inspect-ui/monitoring-report.png',fullPage:true});await page.click('#close');
 await page.click('#case-next');await page.waitForFunction(()=>document.querySelector('#case-page').textContent.startsWith('2 /'));
 await page.select('#case-domain','soc');await page.waitForFunction(()=>document.querySelector('#case-page').textContent.startsWith('1 /'));
 await page.screenshot({path:'/tmp/kt66-inspect-ui/monitoring-cases.png',fullPage:true});
 await page.setViewport({width:320,height:844,hasTouch:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 // 장애를 0건/정상으로 보이지 않게 한다.
 fixture=structuredClone(live);fixture.agent={available:false,error:'테스트: SIEM 연결 실패'};fixture.soc={available:false,error:'테스트: SIEM 연결 실패'};
 await page.setViewport({width:1440,height:1000,hasTouch:true});await page.click('.oc-tabs [data-view=soc]');await page.click('#refresh');
 await page.waitForFunction(()=>document.querySelector('#priority-list').textContent.includes('테스트:'));
 assert.equal(await page.$eval('#metrics .oc-metric strong',e=>e.textContent),'—');
 assert.ok((await page.$eval('#feed-status',e=>e.textContent)).includes('조회 불가'));
 await page.screenshot({path:'/tmp/kt66-inspect-ui/monitoring-unavailable.png'});
 fixture=structuredClone(live);fixture.soc.priority=[{id:'fixture',index:'wazuh-alerts-4.x-fixture',count:124,timestamp:new Date().toISOString(),rule:{id:'1001',level:14,description:'브라우저 표본: 반복 SQL 공격 의심'},data:{srcip:'203.0.113.7'},agent:{name:'web'}}];fixture.soc.high=124;
 await page.click('#refresh');await page.waitForSelector('[data-alert]');
 assert.ok((await page.$eval('[data-alert]',e=>e.textContent)).includes('124건'));
 await page.screenshot({path:'/tmp/kt66-inspect-ui/monitoring-soc-fixture.png'});
 await page.click('#lock');assert.equal(await page.$eval('#workspace',e=>e.hidden),true);assert.equal(await page.$$eval('[data-finding],[data-case],[data-alert]',e=>e.length),0);
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync('/tmp/kt66-monitoring-browser-validation.json',JSON.stringify({sizes,liveAgent:live.agent.total,liveSoc:live.soc.total,open:live.xoc.totals.open,groups:live.xoc.group_total,errors,writes},null,2));
 console.log(JSON.stringify({sizes,groups:live.xoc.group_total,cases:true,markdown:true,lock:true,unavailable:true,errors,writes}));
};
