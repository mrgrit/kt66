/* 근무자를 실제 좌표로 클릭·터치한다. DOM 이벤트 직접 호출은 가림을 놓친다. */
const assert=require('node:assert/strict');
module.exports=async(page)=>{
 const errors=[],writes=[],checked=[];
 page.on('pageerror',e=>errors.push(String(e)));await page.setCacheEnabled(false);
 await page.setRequestInterception(true);page.on('request',r=>{
  if(!['GET','HEAD'].includes(r.method())){writes.push(r.method());return r.abort()}
  r.continue();
 });
 await page.setViewport({width:1440,height:1000});
 await page.goto('http://192.168.12.100:8020/?floor=1F',{waitUntil:'networkidle2'});
 await page.waitForSelector('[data-worker="physical-security"]');
 await page.evaluate(()=>{clearInterval(pollingTimer);clearInterval(workerStateTimer)});
 for(const [width,height] of [[1440,1000],[1366,768],[390,844],[320,568]]){
  await page.setViewport({width,height,hasTouch:width<500});
  await page.evaluate(()=>enterFloor('1F'));await new Promise(r=>setTimeout(r,200));
  const points=await page.evaluate(()=>{
   const figure=document.querySelector('[data-worker-figure="physical-security"]');
   const sprite=figure.querySelector('.agent-pixel-sprite').getBoundingClientRect();
   const ring=figure.querySelector('[data-agent-ring]').getBoundingClientRect();
   return [['head',sprite.left+sprite.width/2,sprite.top+sprite.height*.2],
    ['body',sprite.left+sprite.width/2,sprite.top+sprite.height*.57],
    ['ring',ring.left+ring.width/2,ring.top+ring.height/2]].map(([part,x,y])=>({part,x,y,worker:document.elementFromPoint(x,y)?.closest('[data-worker]')?.dataset.worker||null}));
  });
  for(const p of points){
   assert.equal(p.worker,'physical-security',JSON.stringify({width,height,...p}));
   if(width<500)await page.touchscreen.tap(p.x,p.y);else await page.mouse.click(p.x,p.y);
   await page.waitForSelector('#drawer:not([hidden]) [data-crew-activity="physical-security"]',{timeout:2000});
   await page.click('#dr-close');
  }
  await page.$eval('[data-worker="physical-security"]',e=>e.focus());await page.keyboard.press('Enter');
  await page.waitForSelector('#drawer:not([hidden]) [data-crew-activity="physical-security"]');await page.click('#dr-close');
  await page.mouse.move(2,2);
  await page.screenshot({path:'/tmp/kt66-inspect-ui/lobby-worker-'+width+'.png'});
  checked.push({width,height,selection:width<500?'touch':'mouse',parts:points.map(p=>p.part)});
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({checked,errors,writes}));
};
