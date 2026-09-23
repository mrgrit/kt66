/* 서버가 허용한 메타데이터만 표시한다. 모델 실행·다운로드 버튼은 없다. */
const AI_SERVICE_CACHE = new Map(), AI_SERVICE_PENDING = new Set();
const modelBytes = value => typeof value==='number' ? (value/1024**3).toFixed(1)+' GiB' : '미수집';
function serviceModelRow(model, loaded=false) {
  const tags=[model.parameter_size,model.quantization_level].filter(Boolean);
  if(model.base_model && model.base_model!==model.name)tags.unshift(model.base_model);
  if(loaded) {
    tags.push('메모리 '+modelBytes(model.size),'GPU 배치 '+modelBytes(model.size_vram));
    if(model.context_length)tags.push('컨텍스트 '+model.context_length.toLocaleString());
    if(model.expires_at && Number.isFinite(Date.parse(model.expires_at)))
      tags.push('적재 만료 '+new Date(model.expires_at).toLocaleString('ko-KR'));
  } else if(model.size!=null)tags.push('파일 '+modelBytes(model.size));
  if(model.max_model_len)tags.push('최대 컨텍스트 '+model.max_model_len.toLocaleString());
  return `<div class="model-row"><b>${safeText(model.name)}</b><small>${tags.map(safeText).join(' · ')}</small></div>`;
}
function serviceCard(service) {
  const esc=safeText, isOllama=service.platform==='ollama',models=service.models,loaded=service.loaded;
  const heading=isOllama?'설치된 모델':'서비스가 제공하는 모델';
  const list=models===null?`<p class="service-unknown">${esc(service.catalog_error||'미확인')}</p>`:
    !models.length?'<p class="note">등록된 모델이 없습니다.</p>':
    models.slice(0,5).map(m=>serviceModelRow(m)).join('')+
    (models.length>5?`<details data-service-expand="${esc(service.id)}"><summary>나머지 ${models.length-5}개 펼치기</summary>${models.slice(5).map(m=>serviceModelRow(m)).join('')}</details>`:'');
  const residency=isOllama?
    `<h5>메모리에 적재된 모델 · ollama ps${loaded!==null?' / '+service.loaded_count+'개':''}</h5>`+
    (loaded===null?`<p class="service-unknown">${esc(service.loaded_error||'적재 상태 미확인')}</p>`:
      !loaded.length?'<p class="note">현재 적재된 모델이 없습니다. 설치된 모델과는 별개입니다.</p>':
      loaded.map(m=>serviceModelRow(m,true)).join(''))+
    '<p class="service-footnote">적재는 추론 요청 처리 중이라는 뜻이 아닙니다. Spark·Thor는 CPU와 GPU가 통합 메모리를 공유합니다.</p>':
    '<p class="service-footnote">이 목록은 서빙 API의 응답입니다. 메모리 상주 여부·사용량과 처리 중인 요청은 미수집입니다.</p>';
  return `<section class="service-card" data-service="${esc(service.id)}"><header><strong>${esc(service.name)}</strong><span>${esc(service.status)}</span></header>
    <p class="service-purpose">${esc(service.purpose)}${service.version?' · v'+esc(service.version):''}</p>
    <code>${esc(service.endpoint||'주소 미확인')}</code>
    <h5>${heading}${service.model_count!=null?' · '+service.model_count+'개':''}${models?.length>5?' (대표 5개)':''}</h5>
    ${list}${service.model_count>models?.length?'<p class="note">표시 상한 200개입니다.</p>':''}
    ${residency}</section>`;
}
function aiServicesMarkup(asset) {
  if(!asset.serving?.length)return '';
  const cached=AI_SERVICE_CACHE.get(asset.id),data=cached?.data;
  return `<section class="ai-services" data-ai-services="${safeText(asset.id)}"><div class="dsec">AI 서비스 · 모델 현황</div>
    <p class="note">${data?'조회 '+safeText(new Date(data.checked_at*1000).toLocaleString('ko-KR')):'서비스를 조회하고 있습니다.'} · 열어 둔 장비만 60초 간격으로 조회</p>
    ${cached?.error?'<p class="service-unknown">조회 실패 · 이전 결과일 수 있습니다. '+safeText(cached.error)+'</p>':''}
    ${data?.error?'<p class="service-unknown">'+safeText(data.error)+'</p>':''}
    ${data?.route?'<p class="service-footnote">조회 경로: '+safeText(data.route)+'</p>':''}
    <div class="service-shortcuts">${data?.services?.map(s=>`<button type="button" data-service-jump="${safeText(s.id)}"><b>${safeText(s.name)}</b><small>모델 ${s.model_count??'미확인'}${s.loaded_count!=null?' · 적재 '+s.loaded_count:''}</small></button>`).join('')||''}</div>
    ${data?.services?.map(serviceCard).join('')||''}
    <p class="service-footnote">대장에 등록된 서비스만 조회합니다. llama.cpp 등 목록에 없는 플랫폼의 설치 여부는 미확인입니다.</p>
    </section>`;
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-service-jump]');if(!button)return;
  const target=$$('[data-service]').find(el=>el.dataset.service===button.dataset.serviceJump),body=$('#dr-body');
  if(target)body.scrollTo({top:body.scrollTop+target.getBoundingClientRect().top-body.getBoundingClientRect().top-12,behavior:'smooth'});
});
async function refreshAIServices(id) {
  const asset=LAYOUT?.it_assets.find(a=>a.id===id);
  if(!asset?.serving?.length || AI_SERVICE_PENDING.has(id))return;
  const cache=AI_SERVICE_CACHE.get(id);
  const cachedAt=cache?.error?cache.at:Math.min(cache?.at||0,(cache?.data?.checked_at||0)*1000);
  if(cache && Date.now()-cachedAt<60000)return;
  AI_SERVICE_PENDING.add(id);
  try {
    const data=await get('/api/assets/'+encodeURIComponent(id)+'/services');
    AI_SERVICE_CACHE.set(id,{at:Date.now(),data});
  } catch(e) {
    AI_SERVICE_CACHE.set(id,{at:Date.now(),data:cache?.data,error:String(e.message||e)});
  } finally { AI_SERVICE_PENDING.delete(id); }
  if(SELECTED!==id || $('#drawer').hidden)return;
  const box=$('[data-ai-services]');
  if(box){
    const top=$('#dr-body').scrollTop,expanded=$$('[data-service-expand][open]',box).map(e=>e.dataset.serviceExpand);
    box.outerHTML=aiServicesMarkup(asset);
    $$('[data-service-expand]').forEach(e=>e.open=expanded.includes(e.dataset.serviceExpand));
    $('#dr-body').scrollTop=top;
  }
}
