/* 실제 API 읽기 + 브라우저 안에서만 격리한 이동/승인/거절 표본. 운영 데이터에는 쓰지 않는다. */
const assert=require('node:assert/strict'),fs=require('node:fs');
module.exports=async page=>{
 const errors=[],writes=[],sizes=[];let fixture=null,failFeed=false;
 page.on('pageerror',e=>errors.push(String(e)));await page.setCacheEnabled(false);await page.setRequestInterception(true);
 page.on('request',r=>{
  if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}
  if(new URL(r.url()).pathname==='/api/agent-control/risk-map'){
   if(failFeed)return r.respond({status:503,contentType:'application/json',body:'{"detail":"test unavailable"}'});
   if(fixture)return r.respond({status:200,contentType:'application/json',body:JSON.stringify(fixture)});
  }
  r.continue();
 });
 const key=fs.readFileSync('/home/ccc/work/kt66/.env','utf8').split('\n').find(l=>l.startsWith('API_KEY=')).split('=').slice(1).join('=').trim().replace(/^['"]|['"]$/g,'');
 await page.setViewport({width:1440,height:1000,hasTouch:true});
 await page.goto('http://192.168.12.100:8020/?view=risk',{waitUntil:'networkidle2'});
 await page.waitForSelector('[data-risk-zone]');
 assert.equal(await page.$$eval('[data-risk-zone]',e=>e.length),12);
 assert.equal(await page.$$eval('[data-risk-worker]',e=>e.length),0);
 const noAuth=await page.evaluate(async()=>({status:(await fetch('/api/agent-control/risk-map')).status}));assert.equal(noAuth.status,401);
 await page.screenshot({path:'/tmp/kt66-inspect-ui/risk-auth.png'});
 await page.type('#risk-key',key);await page.click('#risk-auth button');await page.waitForSelector('#risk-auth[hidden]');
 await page.waitForSelector('[data-risk-worker]');
 const data=await page.evaluate(async k=>(await fetch('/api/agent-control/risk-map',{headers:{'x-api-key':k}})).json(),key);
 assert.equal(await page.$eval('#risk-key',e=>e.value),'');
 assert.equal(await page.$$eval('[data-risk-worker]',e=>e.length),data.workers.length);
 for(const [width,height] of [[1920,1080],[1440,1000],[1366,768],[1024,768],[768,1024],[390,844],[320,568]]){
  await page.setViewport({width,height,hasTouch:true});await new Promise(r=>setTimeout(r,250));
  const geometry=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,
   mapWidth:document.querySelector('#risk-map-frame').getBoundingClientRect().width,
   clipped:[...document.querySelectorAll('#risk-map [data-risk-worker]')].some(e=>{const b=e.getBBox();return !Number.isFinite(b.x)||!Number.isFinite(b.y)})}));
  assert.equal(geometry.overflow,false,'가로 넘침 '+width);assert.equal(geometry.clipped,false);
  sizes.push({width,height,...geometry});await page.screenshot({path:'/tmp/kt66-inspect-ui/risk-live-'+width+'.png',fullPage:true});
 }
 await page.setViewport({width:1440,height:1000,hasTouch:true});
 await page.click('[data-risk-person="soc-analyst"]');
 assert.ok((await page.$eval('#risk-inspection',e=>e.textContent)).includes('soc-analyst'));
 if(data.events.length){
  await page.click('#risk-replay');assert.equal(await page.$eval('#risk-replay',e=>e.getAttribute('aria-pressed')),'true');
  await page.$eval('#risk-scrub',e=>{e.value=Math.min(Number(e.max),20);e.dispatchEvent(new Event('input',{bubbles:true}))});
  await page.click('#risk-play');await new Promise(r=>setTimeout(r,1250));await page.click('#risk-play');
  assert.ok((await page.$eval('#risk-connection',e=>e.textContent)).includes('기록 재생'));
  await page.screenshot({path:'/tmp/kt66-inspect-ui/risk-replay.png'});
 }
 // 격리 표본: 실제 기록을 변경하지 않고 브라우저 응답만 치환한다.
 const base=structuredClone(data),worker=base.workers.find(w=>w.id==='network-engineer'),now=Date.now()/1000;
 base.workers.forEach(w=>{w.location='DOCK';w.presence='idle';w.state='idle';w.last_event=null;w.findings=[];w.hold=null});
 base.findings=[];base.summary={agents:base.workers.length,active:1,unknown:0,open_findings:0,holds:0,denied:0,pending:0,events:1};
 const event={id:'browser-fixture:1',at:now-1,worker:worker.id,run_id:'loop-1790145000000000000-network-engineer',kind:'tool',name:'firewall_read',zone:'Z02',attempted_zone:'Z02',axes:['Z02','Z04'],exposure:2,outcome:'observed',summary:'브라우저 격리 표본',targets:['kt66-fw'],permission:{role:'network',mode:'allow'},source:'tools.jsonl#L1',assertion:'tool_receipt',trigger:'periodic',reason:''};
 Object.assign(worker,{last_event:event,location:'Z02',presence:'active',state:'working'});base.events=[event];base.collected_at=now;
 fixture=base;await page.click('#risk-live');await page.waitForFunction(()=>document.querySelector('[data-risk-worker="network-engineer"]')?.dataset.location==='Z02');
 await new Promise(r=>setTimeout(r,3200));
 const before=await page.$eval('[data-risk-worker="network-engineer"]',e=>e.getAttribute('transform'));
 const denied={...event,id:'browser-fixture:2',at:now+1,name:'disk_usage',zone:'Z04',attempted_zone:'Z02',outcome:'denied',summary:'접근 거절 · 실행 진입 안 함',targets:['host'],permission:{role:'network',mode:'deny'}};
 fixture=structuredClone(base);fixture.events.push(denied);Object.assign(fixture.workers.find(w=>w.id===worker.id),{last_event:denied,location:'Z04'});
 await page.waitForFunction(()=>document.querySelector('[data-risk-worker="network-engineer"]')?.dataset.outcome==='denied',{timeout:10000});
 await new Promise(r=>setTimeout(r,200));const during=await page.$eval('[data-risk-worker="network-engineer"]',e=>e.getAttribute('transform'));
 await new Promise(r=>setTimeout(r,3100));const after=await page.$eval('[data-risk-worker="network-engineer"]',e=>e.getAttribute('transform'));
 assert.notEqual(before,during);assert.notEqual(during,after,'새 기록의 실제 이동 애니메이션');
 await page.click('[data-risk-person="network-engineer"]');
 assert.ok((await page.$eval('#risk-inspection',e=>e.textContent)).includes('접근 거절'));
 const gate=await page.evaluate(()=>{const n=document.querySelector('[data-risk-worker="network-engineer"]'),z=document.querySelector('[data-risk-zone="Z04"] .zone-base');const m=n.transform.baseVal.consolidate().matrix,b=z.getBBox();return m.f>b.y+b.height});assert.equal(gate,true,'거절된 에이전트는 구역 밖 게이트에 정지');
 const pending={...denied,id:'browser-fixture:3',at:now+3,name:'firewall_read',targets:['kt66-fw'],zone:'Z05',outcome:'pending',summary:'승인 대기',permission:{role:'network',mode:'ask'}};
 fixture=structuredClone(fixture);fixture.events.push(pending);Object.assign(fixture.workers.find(w=>w.id===worker.id),{last_event:pending,location:'Z05'});
 await page.waitForFunction(()=>document.querySelector('[data-risk-worker="network-engineer"]')?.dataset.outcome==='pending',{timeout:10000});
 await new Promise(r=>setTimeout(r,3100));
 assert.ok((await page.$eval('#risk-inspection',e=>e.textContent)).includes('승인 대기'));
 await page.screenshot({path:'/tmp/kt66-inspect-ui/risk-fixture-pending.png'});
 failFeed=true;await page.waitForFunction(()=>document.querySelector('#risk-connection').textContent.includes('수집 지연'),{timeout:10000});
 assert.ok((await page.$eval('#risk-map-note',e=>e.textContent)).includes('수집 중단'));
 await page.click('#risk-lock');assert.equal(await page.$$eval('[data-risk-worker]',e=>e.length),0);
 assert.equal(await page.$$eval('[data-risk-event]',e=>e.length),0);
 failFeed=false;fixture=null;await page.type('#risk-key',key);await page.click('#risk-auth button');await page.waitForSelector('#risk-auth[hidden]');
 assert.equal(await page.$$eval('#risk-worker-filter option',e=>e.length),data.workers.length+1,'재인증 후 근무자 필터 복원');
 await page.click('#risk-lock');
 await page.click('#physical-tab');await page.waitForSelector('[data-building-layout]');
 assert.equal(await page.$eval('#risk-panel',e=>e.hidden),true);
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 fs.writeFileSync('/tmp/kt66-risk-map-validation.json',JSON.stringify({sizes,workers:data.workers.length,events:data.events.length,errors,writes},null,2));
 console.log(JSON.stringify({sizes,workers:data.workers.length,events:data.events.length,movement:true,deniedGate:true,pending:true,replay:true,stale:true,lock:true,errors,writes}));
};
