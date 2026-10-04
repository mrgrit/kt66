const assert=require('node:assert/strict'),fs=require('node:fs');
module.exports=async page=>{
  const errors=[];page.on('pageerror',error=>errors.push(String(error)));
  const key=fs.readFileSync('/home/ccc/work/kt66/.env','utf8').split('\n').find(l=>l.startsWith('API_KEY=')).split('=').slice(1).join('=').trim().replace(/^["']|["']$/g,'');
  await page.setViewport({width:1440,height:1000});
  await page.goto('http://192.168.12.100:8020/agent-control?worker=network-engineer',{waitUntil:'networkidle2'});
  assert.equal(await page.$$eval('[data-run]',nodes=>nodes.length),0);
  await page.type('#evidence-key',key);await page.click('#evidence-auth button');
  await page.waitForSelector('[data-run]');
  const id=await page.evaluate(async key=>{
    const rows=await (await fetch('/api/agent-control/runs?worker=network-engineer&hours=24',{headers:{'x-api-key':key}})).json();
    return rows.items.find(r=>r.execution_evidence?.checks?.length)?.id;
  },key);
  assert.ok(id,'실제 네트워크 검사 기록이 필요합니다');
  await page.click('[data-run="'+id+'"]');
  await page.waitForSelector('.execution-proof');
  const text=await page.$eval('.execution-proof',e=>e.textContent);
  assert.match(text,/network-diagnosis/);assert.match(text,/network_probe/);assert.match(text,/검사 범위 정상|이상 관측|판정 보류/);
  await page.click('.proof-link');await page.waitForSelector('.timeline-event details[open]');
  await page.click('[data-detail-tab="files"]');
  assert.match(await page.$eval('.detail-body',e=>e.textContent),/\.codex\/agents/);
  await page.click('[data-detail-tab="overview"]');
  for(const width of [1440,390,320]){
    await page.setViewport({width,height:1000});await new Promise(resolve=>setTimeout(resolve,250));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.screenshot({path:'/tmp/kt66-inspect-ui/execution-proof-'+width+'.png',fullPage:true});
  }
  await page.setViewport({width:1440,height:1000});
  await page.goto('http://192.168.12.100:8020/?floor=4F',{waitUntil:'networkidle2'});
  await page.waitForFunction(()=>typeof LAYOUT!=='undefined'&&LAYOUT?.it_assets?.some(a=>a.id==='windows-user'));
  await page.waitForSelector('#scene [data-asset="windows-user"]');
  await page.click('#scene [data-asset="windows-user"]');
  assert.match(await page.$eval('#dr-body',e=>e.textContent),/NAT/);
  assert.match(await page.$eval('#dr-body',e=>e.textContent),/설치 완료/);
  assert.equal(await page.$eval('#dr-body a[href*="8090"]',e=>e.textContent),'웹 콘솔 열기 ↗');
  await page.screenshot({path:'/tmp/kt66-inspect-ui/windows-asset.png'});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({evidenceRun:id,proofLinks:true,nativeProfiles:true,viewports:3,windowsAsset:true,errors}));
};
