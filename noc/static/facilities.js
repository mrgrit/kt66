/* 설명은 facility-guide.yaml, 설계 수치는 assets.yaml, 현재 값은 envsim에서 온다. */
function allFacilities() {
  return Object.entries(LAYOUT?.facility||{}).flatMap(([system,rows])=>
    (Array.isArray(rows)?rows:[rows]).filter(item=>item?.id).map(item=>({...item,
      system_kind:system,subtype:item.kind,
      kind:['security','automation','microgrid'].includes(system)?item.kind:system})));
}
const facilityOf = floor => allFacilities().filter(item=>floor==='SITE'?item.location==='outdoor':item.location!=='outdoor' && item.floor===floor);
const physicalFacilitiesOf = floor => facilityOf(floor).filter(i=>!['containment','raised_floor','cold_plate'].includes(i.kind));
function facilityGuide(item) {
  const key=item.kind==='fire' && item.subtype ? item.subtype : item.kind;
  return LAYOUT?.facility_guide?.types?.[key]||{};
}
const FACILITY_FIELDS = {
  capacity_kw:['정격 용량','kW'],battery_kwh:['배터리 저장량','kWh'],kwh:['저장 에너지','kWh'],
  hv_kv:['상위 전압','kV'],mv_kv:['중압','kV'],kv:['정격 전압','kV'],
  primary_kv:['1차 전압','kV'],secondary_v:['2차 전압','V'],efficiency:['효율 (비율)',''],
  transfer_ms:['절체 시간','ms'],start_delay_s:['기동 지연','초'],fuel_hours:['설계 연료 시간','시간'],
  liters:['저장량','L'],lph_at_full_load:['만부하 연료 소비','L/h'],topology:['UPS 방식',''],
  chemistry:['배터리 종류',''],cells:['셀 수','개'],temp_c:['초기 온도','°C'],
  runaway_temp_c:['모델 열폭주 기준','°C'],installed:['설치 기록',''],soc_pct:['초기 SOC','%'],
  approach_c:['습구 대비 접근 온도','°C'],fan_kw:['팬 정격','kW'],makeup_lpm:['보충수 기준','L/min'],
  freeze_risk_c:['모델 결빙 기준','°C'],cop:['COP',''],kw:['소비전력 설계값','kW'],lpm:['정격 유량','L/min'],
  loop:['회로',''],standby:['예비기 설정',''],fouling_pct:['초기 오염률','%'],
  enable_wetbulb_c:['자연냉각 전환 습구','°C'],damper_ok:['댐퍼 초기 상태',''],
  phase:['냉각 방식',''],commissioned:['도입·가동 설정',''],thermal_buffer_kwh:['열 완충량','kWh'],
  drybulb_c:['초기 건구 온도','°C'],wetbulb_c:['초기 습구 온도','°C'],humidity_pct:['초기 습도','%RH'],
  agent:['소화 약제',''],sensitivity:['감도 설정',''],capacity_kg:['정격 중량','kg'],
  verification_hood:['대상 검증 설정',''],cod_log:['파기증명 기록 설정',''],height_mm:['마루 높이','mm'],
  perforated_tiles:['타공 타일','개'],required_cfm:['요구 기류','CFM'],supplied_cfm:['공급 기류','CFM'],
  sealed:['밀폐 설정',''],efficiency_gain:['격리 효율 이득 (비율)',''],quick_connect:['연결 방식','']
};
function facilityReadings(item) {
  if(!ST)return '<p class="note">상태를 아직 수집하지 못했습니다.</p>';
  const p=ST.power,plant=ST.plant,aisle=ST.aisles?.[item.aisle],rows=[];
  const add=(key,value,unit='')=>{if(value!==undefined && value!==null)rows.push(kv(key,safeText(String(value))+(unit?' '+unit:'')))};
  if(item.kind==='utility' && p)add('상용 전원',p.utility_ok?'공급':'상실');
  if(item.kind==='ups' && p){add('배터리 운전',p.on_battery?'사용 중':'상용·발전기 공급');add('충전율',p.ups_charge_pct,'%');add('현재 잔여 시간',p.ups_runtime_min,'분');add('공급 부하',p.total_kw,'kW')}
  if(item.kind==='generator' && p){add('발전기',p.generator_failed?'기동 실패':p.generator_running?'운전 중':'대기');if(ST.fuel)add('연료 잔량',ST.fuel.liters,'L')}
  if(item.kind==='fuel_tank' && ST.fuel){add('연료 잔량',ST.fuel.liters,'L');add('운전 잔여 시간',ST.fuel.hours_left>=999?'대기 중 · 산출하지 않음':ST.fuel.hours_left+' 시간')}
  if(item.kind==='battery'){const b=ST.battery?.[item.id];if(b){add('현재 온도',b.temp_c,'°C');add('열폭주 상태',b.runaway?'발생':'활성 징후 없음')}}
  if(item.kind==='pdu')add('해당 PDU 부하',p?.pdu?.[item.id],'kW');
  if(aisle){add('담당 아일',item.aisle);add('아일 온도',aisle.temp_c,'°C');add('아일 습도',aisle.humidity_pct,'%RH');add('아일 냉방 합계',aisle.cooling_kw,'kW');add('아일 IT 부하',aisle.it_kw,'kW')}
  if(plant && equipmentKind(item.kind)==='cooling'){
    if(item.kind==='pump')add('해당 펌프 가용',plant.pumps?.[item.id]===undefined?null:plant.pumps[item.id]?'가용':'고장');
    if(item.kind==='heat_exchanger')add('열교환기 오염',plant.hx_fouling_pct,'%');
    if(item.kind==='water_tank')add('잔여 열 완충',plant.buffer_kwh,'kWh');
    if(['cdu','cold_plate'].includes(item.kind))add('모델 콜드플레이트 입구',plant.coldplate_inlet_c,'°C');
    add('플랜트 전체 가용 냉각',plant.capacity_kw,'kW');add('플랜트 전체 냉각 수요',plant.demand_kw,'kW');
    add('플랜트 운전',plant.free_cooling?'자연냉각':plant.chiller_running?'기계식 냉각':'냉동기 대기');
  }
  if(item.kind==='weather'){add('현재 건구',ST.weather?.drybulb_c,'°C');add('현재 습구',ST.weather?.wetbulb_c,'°C')}
  return rows.join('')||'<p class="note">이 설비의 개별 수치 센서는 미연동입니다. 아래 값은 설계·설정값입니다.</p>';
}
function openFacility(item) {
  SELECTED=null;
  const scroll=SELECTED_FACILITY===item.id?$('#dr-body').scrollTop:0;
  const expanded=SELECTED_FACILITY===item.id && !!$('.facility-related[open]');
  const guide=facilityGuide(item),placement=facilityPlacement(item),esc=safeText,related=[];
  const refer=value=>{
    for(const id of (Array.isArray(value)?value:[value]).filter(Boolean)) {
      const target=allFacilities().find(f=>f.id===id);
      related.push(target?`<button class="btn sm" data-related-facility="${esc(id)}">${esc(target.name||id)}</button>`:esc(String(id)));
    }
  };
  ['sources','feeds','battery_string','fuel_tank','condenser','pdu','rack','racks','tap_offs','asset','zone_covered'].forEach(key=>refer(item[key]));
  const configs=Object.entries(FACILITY_FIELDS).filter(([key])=>item[key]!=null).map(([key,[label,unit]])=>
    kv(label,esc(typeof item[key]==='boolean'?(item[key]?'예':'아니오'):String(item[key]))+(unit?' '+unit:''))).join('');
  const neighbors=allFacilities().filter(other=>other.id!==item.id && facilityGuide(other).system===guide.system);
  showDrawer(item.name||guide.title||item.id,'ot',
    '<span class="facility-tag">교육용 가상 설비</span>'+
    kv('설비 ID',esc(item.id))+kv('종류',esc(guide.title||item.kind))+
    kv('배치',esc(item.location==='outdoor'?'옥외 부지 · 층 구분 없음':item.floor+' 실내'))+
    (placement?kv('실내 구역',esc(placement.roomName)):'')+
    kv('고장 주입 상태',!ST?'미확인':facilityDown(item)?'<span class="service-unknown">활성 이상 있음</span>':'해당 설비의 활성 고장 없음')+
    '<div class="dsec">역할과 배치 이유</div><p class="facility-prose">'+esc(guide.role||'자산 대장에 등록된 시설입니다.')+'</p><p class="note">'+esc(guide.placement||'')+'</p>'+
    (placement?'<p class="facility-prose" data-placement-reason>'+esc(placement.reason)+'</p>':'')+
    '<div class="dsec">계통에서의 위치</div><p class="facility-flow">'+esc(LAYOUT.facility_guide?.systems?.[guide.system]||'')+'</p>'+
    (related.length?'<div class="facility-relations">'+related.join(' · ')+'</div>':'')+
    '<div class="dsec">현재 시뮬레이션 상태</div>'+facilityReadings(item)+
    '<div class="dsec">설계·설정값</div>'+configs+
    (item.subtype?kv('세부 방식',esc(item.subtype)):'')+
    '<div class="dsec">이상이 미치는 영향</div><p class="facility-prose">'+esc(guide.impact||'개별 영향은 모델의 지원 범위에 따라 다릅니다.')+'</p>'+
    '<div class="dsec">점검할 내용</div><ul class="facility-checks">'+(guide.checks||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>'+
    '<details class="facility-related"><summary>같은 계통 설비 '+neighbors.length+'개</summary>'+neighbors.map(f=>`<button class="btn sm" data-related-facility="${esc(f.id)}">${esc(f.name||f.id)} · ${esc(f.location==='outdoor'?'옥외':f.floor)}</button>`).join('')+'</details>'+
    '<p class="note">'+esc(LAYOUT.facility_guide?.source_note||'')+' 정격·용량은 설계값, 현재 상태는 시뮬레이션 계산값입니다. IT 사용률 기반 환산 부하를 이용하며 실제 시설 센서의 실측값은 아닙니다.</p>');
  SELECTED_FACILITY=item.id;
  if(expanded)$('.facility-related').open=true;
  $$('[data-related-facility]').forEach(button=>button.onclick=()=>openFacility(allFacilities().find(f=>f.id===button.dataset.relatedFacility)));
  $('#dr-body').scrollTop=scroll;
}
