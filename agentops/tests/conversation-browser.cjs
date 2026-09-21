/* 근무자 선택·대화 이어가기·조사 표시·입력 보호·모바일을 모델 호출 없이 확인한다. */
const assert=require('node:assert/strict');
module.exports=async function(page){
 await page.setCacheEnabled(false);
 const errors=[],writes=[];page.on('pageerror',e=>errors.push(String(e)));
 const now=Date.now()/1000,id='req-aaaaaaaaaaaaaaaa';
 const workers=[{id:'soc-analyst',name:'SOC 분석가',floor:'4F',assets:['kt66-siem']},{id:'network-engineer',name:'네트워크 엔지니어',floor:'2F',assets:['kt66-fw']}];
 let d=null;
 const finish=()=>{d.status='completed';d.tasks.at(-1).status='completed';d.tasks.at(-1).evidence='/agents/evidence/loop-test-soc-analyst';d.tasks.at(-1).outcome={summary:'확인했습니다.'};d.messages.push({role:'agent',worker:'soc-analyst',task:d.tasks.at(-1).id,at:now,text:'## 조회 결과\n\n**보관 로그**에서 확인했습니다.\n\n| 대상 | 결과 |\n| --- | --- |\n| 웹 서버 | 경보 확인 |',verification:{observed_live_evidence:true,tools:['siem_search']}})};
 await page.setRequestInterception(true);
 page.on('request',r=>{
  const url=new URL(r.url()),path=url.pathname;
  if(!path.startsWith('/api/'))return r.continue();
  const body=r.postData()?JSON.parse(r.postData()):null;
  if(r.method()!=='GET')writes.push({path,body});
  let data={};
  if(path==='/api/request-workers')data={workers};
  else if(path==='/api/requests'&&r.method()==='POST'){
   assert.equal(body.worker,'soc-analyst');assert.equal(body.mode,'conversation');assert.equal(body.timezone,'Asia/Seoul');
   d={...body,id,title:body.prompt,scope:'read',status:'queued',created:now,updated:now,sessions:1,agents:[],tasks:[{id:'conversation-1',title:'대화 답변·조사',worker:body.worker,depends_on:[],instructions:'조회',status:'running'}],changes:[],artifacts:[],events:[],messages:[{role:'user',text:body.prompt,at:now}]};data=d;
  }else if(path==='/api/requests')data={requests:d?[d]:[],runner:{at:Date.now()/1000,status:'running',active_workers:[]}};
  else if(path===`/api/requests/${id}/reply`){d.messages.push({role:'user',text:body.message,at:now});d.status='running';d.sessions++;d.tasks.push({...d.tasks[0],id:'conversation-2',status:'running'});data=d;}
  else if(path===`/api/requests/${id}`)data=d;
  else if(path==='/api/request-guides')data={files:['README.md']};
  else if(path==='/api/request-guides/README.md')data={path:'README.md',content:'# 한국어 지침',sha256:'test'};
  return r.respond({status:200,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.emulateTimezone('Asia/Seoul');await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8050/requests?worker=soc-analyst',{waitUntil:'networkidle2'});
 await page.type('#key','browser-test');await page.click('#login-form button');await page.waitForSelector('#chat-compose:not([hidden])');
 assert.equal(await page.$eval('#chat-worker',e=>e.value),'soc-analyst');
 await page.select('#chat-worker','network-engineer');assert.match(await page.$eval('#worker-description',e=>e.textContent),/kt66-fw/);
 await page.select('#chat-worker','soc-analyst');await page.click('#chat-example');await page.click('#chat-form button:not([type])');
 await page.waitForSelector('.chat-pending');assert.equal(await page.$eval('#reply-text',e=>e.disabled),true);
 finish();await page.waitForSelector('.message-evidence',{timeout:15000});
 assert.match(await page.$eval('.message-evidence',e=>e.textContent),/자료 조회.*SIEM 경보/);
 assert.ok(await page.$('.from-agent table'));assert.equal(await page.$eval('#reply-text',e=>e.disabled),false);
 await page.type('#reply-text','그중 위험한 것만 설명해 줘');
 await page.keyboard.down('Shift');await page.keyboard.press('Enter');await page.keyboard.up('Shift');
 assert.equal(writes.length,1);await page.keyboard.press('Enter');await page.waitForSelector('.chat-pending');
 assert.equal(writes.length,2);assert.match(writes[1].body.message,/그중 위험한 것만/);
 d.status='completed';d.tasks.at(-1).status='completed';d.messages.push({role:'agent',worker:'soc-analyst',at:now,task:'conversation-2',text:'기존 조사 내용에서 **웹 경보**부터 확인하세요.',verification:{observed_live_evidence:false,tools:[]}});
 await page.waitForFunction(()=>document.querySelectorAll('.from-agent').length===2,{timeout:15000});
 assert.match(await page.$eval('.from-agent:last-child',e=>e.textContent),/새 조회 근거 없음/);
 await page.type('#reply-text','아직 보내지 않은 내용');d.updated++;d.budget=9;
 await page.waitForFunction(()=>document.querySelector('#new-budget').value==='9',{timeout:15000});
 assert.equal(await page.$eval('#reply-text',e=>e.value),'아직 보내지 않은 내용');
 await page.click('#guide-tab');d.budget=10;
 await new Promise(r=>setTimeout(r,5300));assert.equal(await page.$eval('#guides',e=>e.hidden),false);
 await page.click('#chat-tab');
 const widths=[];
 for(const width of [1440,1024,768,390,320]){
  await page.setViewport({width,height:1000});
  const size=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
  assert.ok(size.document<=width+1,JSON.stringify(size));widths.push(size);
 }
 await page.screenshot({path:'/tmp/kt66-inspect-ui/chat-mobile.png',fullPage:true});
 await page.setViewport({width:1440,height:1000});await page.screenshot({path:'/tmp/kt66-inspect-ui/chat-desktop.png',fullPage:true});
 await page.click('#work-tab');assert.equal(await page.$eval('#compose',e=>e.hidden),false);
 assert.equal(await page.$$eval('[data-request]',els=>els.length),0);
 await page.click('#chat-tab');await page.waitForSelector('[data-request]',{visible:true});await page.click('[data-request]');await page.waitForSelector('.chat-card');
 assert.equal(await page.$$eval('.from-user',els=>els.length),2);
 await page.click('#logout');assert.equal(await page.$eval('#detail',e=>e.textContent),'');
 assert.deepEqual(errors,[]);assert.equal(writes.length,2);
 return {checks:'담당자 선택, 직접 대화, 후속 질문, 조회 근거 구분, 중복 전송 방지, 초안 보존, 탭 전환, 대화 재열기, 잠금',widths,errors,model_calls:0};
};
