/* 승인 세 선택·자동 재개·항상 허용 철회를 모델 호출 없이 확인한다. */
const assert=require('node:assert/strict');
module.exports=async function(page){
 await page.setCacheEnabled(false);await page.setViewport({width:1440,height:1000});
 const errors=[],writes=[];page.on('pageerror',e=>errors.push(String(e)));
 const now=Date.now()/1000,id='req-bbbbbbbbbbbbbbbb';
 const d={id,mode:'conversation',worker:'systems-engineer',title:'80% 이상 디스크 확인',scope:'read',revision:1,status:'waiting_input',created:now,updated:now,sessions:1,budget:8,agents:[],changes:[],artifacts:[],events:[],tasks:[{id:'conversation-1',title:'디스크 조회',worker:'systems-engineer',status:'waiting_input',instructions:'조회',depends_on:[]}],messages:[{role:'user',text:'80% 이상 디스크 확인',at:now},{role:'agent',worker:'systems-engineer',text:'디스크 사용률 조회를 허용해 주세요.',at:now}],permission_requests:[{id:'permission-test',revision:1,status:'pending',tool:'disk_usage',worker:'systems-engineer',permission:'metrics_read',arguments:{target:'all',threshold_pct:80}}]};
 let grants=[];
 await page.setRequestInterception(true);page.on('request',r=>{
  const path=new URL(r.url()).pathname;if(!path.startsWith('/api/'))return r.continue();
  const body=r.postData()?JSON.parse(r.postData()):null;let result={};
  if(r.method()!=='GET')writes.push({path,body,method:r.method()});
  if(path==='/api/request-workers')result={workers:[{id:'systems-engineer',name:'시스템/스토리지 엔지니어',floor:'2F',assets:[]}]};
  else if(path==='/api/requests')result={requests:[d],runner:{at:Date.now()/1000,status:'running',active_workers:[]}};
  else if(path==='/api/requests/'+id)result=d;
  else if(path===`/api/requests/${id}/permissions/permission-test`){
   d.permission_requests[0].status={once:'allowed_once',always:'allowed_always',defer:'deferred'}[body.decision];
   if(body.decision!=='defer'){d.status='queued';d.sessions++;}
   if(body.decision==='always')grants=[{id:'grant-test',worker:d.worker,tool:'disk_usage',created:now}];
   result={status:d.status};
  }else if(path==='/api/tool-permissions')result={grants};
  else if(path==='/api/tool-permissions/grant-test'&&r.method()==='DELETE'){grants=[];result={revoked:'grant-test'};}
  return r.respond({status:200,contentType:'application/json',body:JSON.stringify(result)});
 });
 await page.goto('http://192.168.12.100:8050/requests#'+id,{waitUntil:'networkidle2'});await page.type('#key','fixture');await page.click('#login-form button');
 await page.waitForSelector('[data-decision="once"]');
 assert.deepEqual(await page.$$eval('[data-decision]',els=>els.map(e=>e.textContent)),['이번만 허용','항상 허용','요청 보류']);
 assert.match(await page.$eval('.permission-card',e=>e.textContent),/디스크 사용률 조회/);
 await page.click('[data-decision="defer"]');await page.waitForFunction(()=>document.querySelector('.permission-card').textContent.includes('보류한 권한'));
 assert.equal(d.sessions,1);assert.equal(writes[0].body.decision,'defer');
 await page.click('[data-decision="once"]');await page.waitForSelector('.chat-pending');assert.equal(d.sessions,2);
 assert.equal(await page.$('[data-decision]'),null);
 d.status='waiting_input';d.permission_requests[0].status='pending';await page.waitForSelector('[data-decision="always"]',{timeout:15000});
 const widths=[];
 for(const width of [1440,1024,768,390,320]){await page.setViewport({width,height:1000});const w=await page.evaluate(()=>document.documentElement.scrollWidth);assert.ok(w<=width+1,'권한 화면 넘침 '+width+':'+w);widths.push(width);}
 await page.screenshot({path:'/tmp/kt66-inspect-ui/permission-mobile.png',fullPage:true});
 await page.setViewport({width:1440,height:1000});await page.screenshot({path:'/tmp/kt66-inspect-ui/permission-desktop.png',fullPage:true});
 await page.click('[data-decision="always"]');await page.waitForSelector('.chat-pending');
 await page.click('#permission-manage');await page.waitForSelector('[data-revoke]');
 assert.match(await page.$eval('#permission-grants',e=>e.textContent),/시스템\/스토리지 엔지니어.*디스크 사용률 조회/s);
 await page.click('[data-revoke]');await page.waitForFunction(()=>document.querySelector('#permission-grants').textContent.includes('없습니다'));
 assert.deepEqual(writes.map(w=>w.body?.decision||w.method),['defer','once','always','DELETE']);assert.deepEqual(errors,[]);
 return {checks:'세 가지 권한 선택, 보류 시 미실행, 허용 후 재개, 항상 허용 철회, 모바일',widths,errors,model_calls:0};
};
