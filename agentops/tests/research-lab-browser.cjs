/* /tmp 격리 서버에서 실제 API를 사용한다. fixture/evaluate만 합성 모델 응답이다. */
const assert=require('node:assert/strict');
module.exports=async(page,{live=false}={})=>{
  const errors=[],calls=[];
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('request',r=>{if(r.method()==='POST')calls.push(new URL(r.url()).pathname)});
  await page.setCacheEnabled(false);
  await page.setViewport({width:1440,height:1000});
  const host=live?'http://192.168.12.100:8050':'http://127.0.0.1:18052';
  await page.goto(host+'/research-lab',{waitUntil:'networkidle2'});
  assert.equal(await page.$eval('#workspace',e=>e.hidden),true);
  assert.equal(await page.$eval('#example',e=>e.disabled),true);
  const key=live?require('node:fs').readFileSync('/home/ccc/work/kt66/.env','utf8').split('\n').find(l=>l.startsWith('API_KEY=')).split('=').slice(1).join('=').trim().replace(/^["']|["']$/g,''):'test-key';
  await page.type('#center-key',key);await page.click('#unlock button[type=submit]');
  await page.waitForSelector('#workspace:not([hidden])');
  assert.equal(await page.$eval('#center-key',e=>e.value),'');
  await page.click('#suites-tab');
  await page.click('[data-suite="soc-ip-analysis"]');
  await page.waitForSelector('#suite-dialog[open]');
  assert.equal(await page.$$eval('.suite-case',e=>e.length),8);
  await page.click('[data-close="suite-dialog"]');
  await page.click('#experiments-tab');
  for(const width of [1440,1024,768,390,320]){
    await page.setViewport({width,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'메인 화면 '+width);
    if([1440,390].includes(width))await page.screenshot({path:`/tmp/kt66-inspect-ui/workflow-${live?'live':'fixture'}-${width}.png`,fullPage:true});
  }
  await page.setViewport({width:1440,height:1000});
  await page.click('#example');await page.waitForSelector('#candidate-dialog[open]');
  assert.equal(await page.$eval('#candidate-suite',e=>e.value),'soc-ip-analysis');
  assert.match(await page.$eval('#candidate-content',e=>e.value),/고유 IP/);
  if(live){
    await page.click('[data-close="candidate-dialog"]');await page.click('#lock');
    assert.equal(await page.$eval('#workspace',e=>e.hidden),true);
    assert.equal(await page.$eval('#cards',e=>e.textContent),'');
    assert.deepEqual(calls,[]);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({mode:'live-read-only',viewports:5,cases:8,modelCalls:0,errors}));return;
  }
  await page.$eval('#candidate-form [name=hypothesis]',e=>e.value='브라우저 회귀 검사용 합성 실험입니다. 실제 모델의 성과로 제시하지 않습니다.');
  await page.click('#candidate-form button[type=submit]');
  await page.waitForSelector('#detail[open] #evaluate:not([disabled])');
  assert.equal(await page.$eval('#apply',e=>e.disabled),true);
  await page.click('#evaluate');
  await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('평가 대기'));
  const cid=await page.evaluate(async()=>{const data=await(await fetch('/api/research-lab',{headers:{'x-api-key':'test-key'}})).json();return data.candidates[0].id});
  const evaluation=await page.evaluate(async cid=>(await(await fetch('/fixture/evaluate/'+cid,{method:'POST'})).json()),cid);
  assert.equal(evaluation.gate_passed,true);
  await page.click('#reload-report');await page.waitForSelector('.case-result');
  assert.equal(await page.$$eval('.case-result',e=>e.length),12);
  assert.match(await page.$eval('#detail-body',e=>e.textContent),/대상 선정 정밀도/);
  assert.match(await page.$eval('#detail-body',e=>e.textContent),/개선/);
  for(const width of [1440,390,320]){
    await page.setViewport({width,height:1000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'비교 결과 '+width);
    const overflow=await page.$eval('#detail',e=>e.scrollWidth>e.clientWidth+2);
    assert.equal(overflow,false,'대화상자 '+width);
  }
  await page.setViewport({width:1440,height:1000});
  await page.screenshot({path:'/tmp/kt66-inspect-ui/workflow-fixture-comparison.png',fullPage:true});
  await page.$eval('#reason',e=>e.value='격리된 시험 환경에서 적용과 복구 경로를 확인합니다.');
  await page.click('#apply');await page.waitForSelector('#rollback:not([disabled])');
  await page.$eval('#reason',e=>e.value='격리된 시험의 원본 복원과 증거 보존을 확인합니다.');
  await page.click('#rollback');await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('복구 완료'));
  await page.click('[data-close="detail"]');
  await page.click('#suites-tab');await page.click('[data-suite="soc-ip-analysis"]');await page.waitForSelector('#suite-dialog[open] #suite-form');
  await page.$eval('#suite-form [name=id]',e=>e.value='browser-soc-class');
  await page.click('#suite-form button[type=submit]');
  await page.waitForSelector('[data-suite="browser-soc-class"]');
  await page.click('[data-suite="browser-soc-class"]');await page.waitForSelector('#suite-dialog[open] #delete-suite');
  page.once('dialog',dialog=>dialog.accept());await page.click('#delete-suite');
  await page.waitForFunction(()=>!document.querySelector('[data-suite="browser-soc-class"]'));
  await page.click('#lock');assert.equal(await page.$eval('#workspace',e=>e.hidden),true);
  assert.equal(await page.$eval('#detail-body',e=>e.textContent),'');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({mode:'isolated-fixture',viewports:5,caseResults:12,checked:'등록→평가→사례 비교→적용→복구, 세트 복제/삭제, 잠금',calls,errors}));
};
