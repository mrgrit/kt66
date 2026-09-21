/* kt66 NOC — 관제 화면.
 *
 * ── 규칙 1. 화면은 상태를 만들지 않는다 ──────────────────────────
 * 전력·온도·경보는 전부 envsim 이 계산한 값을 그대로 그린다. 화면이 스스로 보간하거나
 * 다듬기 시작하면 학생이 보는 숫자와 SIEM 에 남는 숫자가 갈라진다. 그 순간 교보재가
 * 아니라 장식이 된다.
 *
 * ── 규칙 2. 3D 장면 안에 글자를 넣지 않는다 ──────────────────────
 * 아이소메트릭 좌표가 겹치면 글자도 겹치고, 나중에 그린 물건이 앞서 그린 글자를 덮는다.
 * 앞선 버전이 정확히 그렇게 망가졌다. 그래서 층위를 나눴다:
 *
 *     장면(SVG)   모양과 색으로만 읽힌다. 글자 0개.
 *     라벨층      장면 위에 마지막으로 얹는다. 서로 겹치면 밀어낸다.
 *     툴팁(HTML)  커서를 따라다니며 전부 말해 준다. 절대 가려지지 않는다.
 *     레일·리프트 이름·수치의 본진.
 *
 * ── 규칙 3. 층은 물리, 존은 논리. 둘은 직교한다 ─────────────────
 * 한 랙의 장비가 서로 다른 존 영역에 흩어져 보이는 것이 정상이고, 그 어긋남이
 * 눈에 보이는 것이 이 화면의 목적이다.
 */
'use strict';

const SVGNS = 'http://www.w3.org/2000/svg';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let LAYOUT = null, ST = null, ROSTER = { workers: [] }, FAULTS = { available: {} }, EVENTS = [];
let WORKER_STATES = null, workerStateTimer = null, workerStateLoading = false;
const WORKER_STATE_STYLES = Object.freeze({
  working:{label:'작업 중',color:'#77d9a0'},
  idle:{label:'대기',color:'#93a4b0'},
  waiting:{label:'재시도·한도 대기',color:'#e9b46f'},
  attention:{label:'실패·검토 필요',color:'#f48181'},
  stopped:{label:'실행기 중지',color:'#b3a0db',dash:'6 3'},
  unknown:{label:'상태 미확인',color:'#a0acb5',dash:'2 3'},
});
function workerActivity(id) {
  const data=WORKER_STATES,now=Date.now()/1000;
  const entry=data?.items.find(w=>w?.worker===id);
  const fresh=data && now-data.collected_at>=0 && now-data.collected_at<=30
    && now-data.engine_at>=0 && now-data.engine_at<=60;
  const state=fresh && entry && WORKER_STATE_STYLES[entry.state]?entry.state:'unknown';
  return {...WORKER_STATE_STYLES[state],state,
    reason:state==='unknown' && (!fresh || !entry)?'최신 자동 실행 상태를 확인할 수 없습니다.':entry.reason,
    counts:state==='unknown'?null:entry?.counts||null,observedAt:fresh?data.collected_at:null,
    monitoring:fresh?entry?.monitoring||[]:[]};
}
async function pollWorkerStates() {
  if(workerStateLoading)return;
  workerStateLoading=true;
  try {
    const response=await fetch('/api/agent-control/workers',{cache:'no-store',signal:AbortSignal.timeout(8000)});
    if(!response.ok)throw new Error('worker states unavailable');
    const data=await response.json();
    if(!Array.isArray(data.items) || !Number.isFinite(data.collected_at))throw new Error('invalid worker states');
    WORKER_STATES=data;
  } catch { WORKER_STATES=null; }
  finally {workerStateLoading=false;render();renderCrew();refreshCrewActivity();}
}
function workerActivityMarkup(id) {
  const activity=workerActivity(id);
  const counts=activity.counts;
  return `${kv('자동 작업 상태',`<span style="color:${activity.color}">${safeText(activity.label)}</span>`)}
    ${kv('판정 근거',safeText(activity.reason))}
    ${kv('작업 대기',counts?`${counts.queued||0}건 · 재시도 ${counts.retry||0}건 · 한도 대기 ${counts.waiting_capacity||0}건`:'미확인')}
    ${kv('검토 대기',counts?`${counts.needs_review||0}건`:'미확인')}
    ${kv('관측 시각',activity.observedAt?new Date(activity.observedAt*1000).toLocaleTimeString('ko-KR',{hour12:false}):'미확인')}
    ${activity.monitoring.map(m=>kv('점검 주기',`${safeText(m.loop)} · ${m.interval_sec/60}분 · 다음 ${new Date(m.next_check_at*1000).toLocaleTimeString('ko-KR',{hour12:false})}`)).join('')}`;
}
function refreshCrewActivity() {
  const block=$('[data-crew-activity]');
  if(block)block.innerHTML=workerActivityMarkup(block.dataset.crewActivity);
}
let INJCAT = { injections: [] }, INJACT = { active: [] };
let VIEW = { mode: 'floor', floor: '2F', zoom: 1, panx: 0, pany: 0 };
const requestedFloor = new URLSearchParams(location.search).get('floor');
if (['1F','2F','3F','4F'].includes(requestedFloor)) VIEW.floor = requestedFloor;
let BASE_VB = null, SELECTED = null, upsDismissed = false;
let MOUSE = { x: 0, y: 0 };
const TIPS = new Map();                 // tipId -> 툴팁 payload
let tipSeq = 0;

/* Architectural projection. Rooms are schematic 12×8 floor plates, not
 * surveyed dimensions. The ledger determines racks, assets and facilities. */
const GW = 12, GD = 8;
const XS = 28, ZS = 32;
let YS = 14;
const STAGGER = { dx: 130, dy: 170 };
const iso = (x, y, z) => [(x - y) * XS, (x + y) * YS - z * ZS];

const ZONE_ORDER = ['ext', 'pipe', 'dmz', 'int', 'app', 'ot', 'mgmt'];

/* ══ 색 ════════════════════════════════════════════════════════ */
const TEMP_STOPS = [[16, '#2563eb'], [22, '#2ee6ff'], [27, '#ffb020'],
                    [32, '#ff7a3d'], [38, '#ff4d6a'], [50, '#ff1f45']];
function lerpHex(a, b, t) {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [r1, g1, b1] = p(a), [r2, g2, b2] = p(b);
  const c = (x, y) => Math.round(x + (y - x) * t).toString(16).padStart(2, '0');
  return `#${c(r1, r2)}${c(g1, g2)}${c(b1, b2)}`;
}
function tempColor(t) {
  if (t == null) return '#33475e';
  if (t <= TEMP_STOPS[0][0]) return TEMP_STOPS[0][1];
  for (let i = 1; i < TEMP_STOPS.length; i++) {
    const [v0, c0] = TEMP_STOPS[i - 1], [v1, c1] = TEMP_STOPS[i];
    if (t <= v1) return lerpHex(c0, c1, (t - v0) / (v1 - v0));
  }
  return TEMP_STOPS.at(-1)[1];
}
const shade = (hex, f) => '#' + [1, 3, 5].map(i =>
  Math.min(255, Math.round(parseInt(hex.slice(i, i + 2), 16) * f)).toString(16).padStart(2, '0')).join('');
const zoneOf = id => (LAYOUT?.zones || []).find(z => z.id === id);
const zoneColor = id => zoneOf(id)?.color || '#64748b';

/* ══ SVG ═══════════════════════════════════════════════════════ */
function el(tag, attrs = {}, kids = []) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === 'text') n.textContent = v;
    else if (k === 'on') Object.entries(v).forEach(([e, f]) => n.addEventListener(e, f));
    else n.setAttribute(k, v);
  }
  (Array.isArray(kids) ? kids : [kids]).filter(Boolean).forEach(c => n.appendChild(c));
  return n;
}
const pts = a => a.map(p => p.join(',')).join(' ');
const quad = (x, y, z, w, d, a) => el('polygon', {
  points: pts([iso(x, y, z), iso(x + w, y, z), iso(x + w, y + d, z), iso(x, y + d, z)]), ...a });

/* ══ 재질 ══════════════════════════════════════════════════════
 * 예전에는 면마다 단색 하나였다. 단색 세 개를 붙이면 입체로는 읽히지만
 * **종이를 오려 붙인 것처럼** 보인다 — 빛이 면 위를 흐르지 않기 때문이다.
 *
 * 그래서 세 가지를 얹었다. 색을 늘리지 않고 같은 색 위에 얹는 방식이라
 * 존 색·온도 색이 그대로 유지된다.
 *   ① 면마다 위→아래 빛 감쇠 (겹쳐 그리는 투명 그라디언트 두 장)
 *   ② 접지 그림자 — 물체가 바닥에서 떠 있으면 아무리 칠해도 안 예쁘다.
 *      필터(blur)를 쓰지 않고 어두운 사각형 두 장을 어긋나게 깔았다.
 *      장면이 5초마다 다시 그려지므로 필터 수십 개는 부담이다.
 *   ③ 뒷모서리 림라이트 — 어두운 배경에서 실루엣이 떨어져 나온다
 */
function sceneDefs() {
  const lg = (id, stops, x2 = 0, y2 = 1) => el('linearGradient',
    { id, x1: 0, y1: 0, x2, y2 },
    stops.map(([o, c, a]) => el('stop', { offset: o, 'stop-color': c, 'stop-opacity': a })));
  return el('defs', {}, [
    // 면 위를 흐르는 빛. 윗면·앞면·옆면이 감쇠 세기가 다르다.
    lg('fTop', [[0, '#ffffff', .20], [.55, '#ffffff', .05], [1, '#000000', .10]]),
    lg('fFront', [[0, '#ffffff', .13], [1, '#000000', .26]]),
    lg('fSide', [[0, '#ffffff', .05], [1, '#000000', .34]]),
    // 바닥판 — 가운데가 살짝 밝다. 시선이 가운데로 모인다.
    el('radialGradient', { id: 'plate', cx: .5, cy: .42, r: .72 }, [
      el('stop', { offset: 0, 'stop-color': '#2ee6ff', 'stop-opacity': .055 }),
      el('stop', { offset: .55, 'stop-color': '#2ee6ff', 'stop-opacity': .012 }),
      el('stop', { offset: 1, 'stop-color': '#000000', 'stop-opacity': .34 })]),
    // 경보용 번짐. 개수가 적어 필터를 써도 된다.
    el('filter', { id: 'bloom', x: '-60%', y: '-60%', width: '220%', height: '220%' }, [
      el('feGaussianBlur', { stdDeviation: 3.6, result: 'b' }),
      el('feMerge', {}, [el('feMergeNode', { in: 'b' }), el('feMergeNode', { in: 'SourceGraphic' })])]),
    // 층 바닥판이 바닥에서 떠 보이게 하는 넓은 그림자. 층당 1개뿐이다.
    el('filter', { id: 'plateShadow', x: '-25%', y: '-25%', width: '150%', height: '160%' }, [
      el('feDropShadow', { dx: 0, dy: 16, stdDeviation: 14,
        'flood-color': '#000814', 'flood-opacity': .55 })]),
  ]);
}

/** 접지 그림자. 높이에 비례해 길어진다 — 키 큰 물체가 더 떠 보이면 안 된다. */
function contactShadow(x, y, z, w, d, h) {
  const o = 1.6 + Math.min(h, 2.2) * 2.4;
  return el('g', { 'pointer-events': 'none' }, [
    quad(x, y, z, w, d, { fill: '#000308', opacity: .38,
      transform: `translate(${-o * .45},${o * .95})` }),
    quad(x, y, z, w, d, { fill: '#000308', opacity: .16,
      transform: `translate(${-o * .95},${o * 1.9})` }),
  ]);
}

/** 아이소메트릭 프리즘.
 *  형태가 배경에서 떨어져 나오려면 짙은 외곽선이 필요하고, 입체로 읽히려면
 *  면마다 명도가 달라야 하며, **바닥에 붙어 보이려면 그림자가 있어야 한다.** */
function prism(x, y, z, w, d, h, color, o = {}) {
  const T = [iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  const Bt = [iso(x + w, y, z), iso(x + w, y + d, z), iso(x, y + d, z)];
  const face = (p, base, grad) => [
    el('polygon', { points: pts(p), fill: base }),
    el('polygon', { points: pts(p), fill: `url(#${grad})`, 'pointer-events': 'none' })];
  return el('g', {}, [
    o.flat ? null : contactShadow(x, y, z, w, d, h),
    el('polygon', {                                   // 외곽선(실루엣)
      points: pts([T[0], T[1], Bt[0], Bt[1], Bt[2], T[3]]),
      fill: 'none', stroke: '#04080f', 'stroke-width': 2.6, 'stroke-linejoin': 'round',
      class: o.glow ? 'glow' : null }),
    ...face([T[3], T[2], Bt[1], Bt[2]], shade(color, .54), 'fSide'),
    ...face([T[1], T[2], Bt[1], Bt[0]], shade(color, .78), 'fFront'),
    ...face(T, color, 'fTop'),
    el('polyline', {                                  // 윗면 앞모서리 하이라이트
      points: pts([T[3], T[2], T[1]]), fill: 'none',
      stroke: 'rgba(255,255,255,.42)', 'stroke-width': 1.2, 'stroke-linejoin': 'round' }),
    el('polyline', {                                  // 뒷모서리 림라이트 — 배경과 분리
      points: pts([T[3], T[0], T[1]]), fill: 'none',
      stroke: 'rgba(120,205,255,.30)', 'stroke-width': 1, 'stroke-linejoin': 'round' }),
  ]);
}

/* ══ 라벨층 ═════════════════════════════════════════════════════
 * 장면 위에 마지막으로 얹는다. 서로 겹치면 아래로 밀어낸다 —
 * 겹친 글자는 없는 글자와 같다. */
let LBL = [];
const pill = (sx, sy, text, o = {}) => LBL.push({ sx, sy, text, ...o });
const textW = (t, s) => [...t].reduce((n, c) => n + (c.charCodeAt(0) > 0x2000 ? s : s * .56), 0);

/** 라벨은 **화면 배율과 무관하게 항상 같은 크기**여야 한다. 장면과 함께 확대되면
 *  층 뷰처럼 배율이 큰 화면에서 글자가 장면을 통째로 덮어 버린다(실제로 그랬다).
 *  그래서 라벨층 전체를 픽셀 좌표계로 그리고 1/k 로 되돌린다 — k 는 지금의 화면 배율.
 *  겹치면 아래로 밀어내고, 밀려난 만큼 지시선을 긋는다. */
function emitLabels(root, k, vb) {
  // 라벨은 장면 맨 위에 얹힌다. 클릭과 툴팁은 통과시켜야 한다 —
  // 밀려난 라벨이 장비를 덮으면 그 장비를 누를 수 없게 되기 때문이다.
  const layer = el('g', { 'pointer-events': 'none',
    transform: `translate(${vb[0]},${vb[1]}) scale(${1 / k})` });
  const placed = [];
  const toPx = (sx, sy) => [(sx - vb[0]) * k, (sy - vb[1]) * k];
  for (const L of LBL.sort((a, b) => a.sy - b.sy)) {
    const size = L.size || 11.5, pad = 8;
    const bw = Math.max(textW(L.text, size), L.sub ? textW(L.sub, 9.5) : 0) + pad * 2;
    const bh = L.sub ? size + 21 : size + 12;
    const [ax, ay] = toPx(L.sx, L.sy);
    let x = ax - (L.anchor === 'mid' ? bw / 2 : 0), y = ay - bh - (L.gap || 8);
    x = Math.max(5, Math.min(x, vb[2] * k - bw - 5));
    y = Math.max(5, Math.min(y, vb[3] * k - bh - 5));
    for (let i = 0; i < 80; i++) {
      const hit = placed.find(p => x < p.x + p.w + 5 && p.x < x + bw + 5
                                && y < p.y + p.h + 4 && p.y < y + bh + 4);
      if (!hit) break;
      y = hit.y + hit.h + 5;
    }
    y = Math.max(5, Math.min(y, vb[3] * k - bh - 5));
    placed.push({ x, y, w: bw, h: bh });
    const g = el('g');
    if (Math.abs(y + bh - ay) > 12)
      g.appendChild(el('line', { x1: ax, y1: ay, x2: x + bw / 2, y2: y + bh,
        stroke: L.color || '#1e3049', 'stroke-width': 1, opacity: .45 }));
    g.appendChild(el('rect', { x, y, width: bw, height: bh, rx: 6,
      fill: 'rgba(6,12,20,.93)', stroke: L.color || '#1e3049', 'stroke-width': 1.1 }));
    g.appendChild(el('text', { x: x + pad, y: y + size + 2, 'font-size': size,
      'font-weight': L.weight || 700, fill: L.color || '#d5e2f0', text: L.text }));
    if (L.sub) g.appendChild(el('text', { x: x + pad, y: y + size + 13.5, 'font-size': 9.5,
      class: 'mono', fill: 'rgba(213,226,240,.55)', text: L.sub }));
    layer.appendChild(g);
  }
  root.appendChild(layer);
}

/* ══ 툴팁 ══════════════════════════════════════════════════════ */
function tipify(node, payload) {
  const id = 'T' + (++tipSeq);
  TIPS.set(id, payload);
  node.setAttribute('data-tip', id);
  return node;
}
function showTip(id) {
  const p = TIPS.get(id), box = $('#tip');
  if (!p) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `
    <div class="tt-t"><span style="width:9px;height:9px;border-radius:3px;background:${
      p.color || '#2ee6ff'};display:inline-block;flex:none"></span>${p.title}</div>
    ${p.sub ? `<div class="tt-s">${p.sub}</div>` : ''}
    ${(p.rows || []).map(([k, v]) => `<div class="tt-r"><i>${k}</i><b>${v}</b></div>`).join('')}
    ${p.bar != null ? `<div class="tt-bar"><i style="width:${Math.min(p.bar * 100, 100)}%;background:${
      p.barColor || '#2ee6ff'}"></i></div>` : ''}
    ${p.foot ? `<div class="tt-f">${p.foot}</div>` : ''}`;
  placeTip();
}
function placeTip() {
  const box = $('#tip');
  if (box.hidden) return;
  const host = $('#stage-body').getBoundingClientRect();
  const w = box.offsetWidth, h = box.offsetHeight;
  let x = MOUSE.x - host.left + 18, y = MOUSE.y - host.top + 18;
  if (x + w > host.width - 8) x = MOUSE.x - host.left - w - 18;
  if (y + h > host.height - 8) y = MOUSE.y - host.top - h - 18;
  box.style.left = Math.max(6, x) + 'px';
  box.style.top = Math.max(6, y) + 'px';
}
/** 3초마다 장면을 다시 그리면 커서 밑의 노드가 사라져 툴팁이 꺼진다.
 *  마우스를 안 움직이면 영영 안 돌아온다 — 다시 그린 뒤 커서 밑을 되짚는다. */
function refreshTip() {
  const n = document.elementFromPoint(MOUSE.x, MOUSE.y);
  const owner = n && n.closest ? n.closest('[data-tip]') : null;
  if (owner) showTip(owner.getAttribute('data-tip'));
  else $('#tip').hidden = true;
}

/* ══ 데이터 ════════════════════════════════════════════════════ */
const floors = () => LAYOUT?.floors || [];
const racksOf = f => (LAYOUT?.racks || []).filter(r => r.floor === f);
const assetsOf = f => (LAYOUT?.it_assets || []).filter(a => a.floor === f);
const crewOf = f => (ROSTER.workers || []).filter(w => w.floor === f);
const assetState = id => ST?.assets?.[id] || { kw: 0, util: 0 };
const alive = a => a.container ? ST?.containers?.[a.container]?.state === 'running'
  : a.remote ? (ST?.assets?.[a.id]?.util ?? 0) > 0 : true;

function zonesOf(fid) {
  const f = floors().find(x => x.id === fid);
  const ids = Array.isArray(f?.zone) ? f.zone : [f?.zone].filter(Boolean);
  return ids.filter(z => zoneOf(z))
    .sort((a, b) => ZONE_ORDER.indexOf(a) - ZONE_ORDER.indexOf(b)).map(z => zoneOf(z));
}
function facilityOf(f) {
  const F = LAYOUT?.facility || {}, out = [];
  const push = (kind, list) => (list || []).filter(i => i.floor === f)
    .forEach(i => out.push({ ...i, kind: i.kind || kind }));
  if (F.utility && F.utility.floor === f) out.push({ ...F.utility, kind: 'utility' });
  push('generator', F.generator); push('ups', F.ups); push('pdu', F.pdu);
  push('chiller', F.chiller); push('crac', F.crac); push('fire', F.fire);
  push('facility', F.security);
  // ── 신규 계통. 교재 5장의 전력·냉각 계통을 층에 실제로 세운다 ──
  // containment · raised_floor · cold_plate · weather 는 상자가 아니라 성질이라
  // 프리즘으로 세우지 않는다 — 아일 지표(기류·격리)와 우측 레일에서 읽힌다.
  push('substation', F.substation); push('switchgear', F.switchgear);
  push('transformer', F.transformer); push('ats', F.ats);
  push('battery', F.battery); push('fuel_tank', F.fuel_tank);
  push('microgrid', F.microgrid); push('busway', F.busway);
  push('cooling_tower', F.cooling_tower); push('pump', F.pump);
  push('heat_exchanger', F.heat_exchanger); push('economizer', F.economizer);
  push('fan_coil', F.fan_coil); push('cdu', F.cdu);
  push('water_tank', F.water_tank); push('immersion', F.immersion);
  push('automation', F.automation);
  return out;
}
function floorTemp(f) {
  const a = Object.values(ST?.aisles || {}).filter(x => x.floor === f);
  return a.length ? Math.max(...a.map(x => x.temp_c)) : null;
}
function facilityDown(item) {
  const F = ST?.faults || {};
  const hit = k => (F[k] || []).includes(item.id) || (F[k] || []).includes('*');
  switch (item.kind) {
    case 'crac': return hit('crac_fail');
    case 'chiller': return hit('chiller_fail');
    case 'utility': return !ST?.power?.utility_ok;
    case 'generator': return !!ST?.power?.generator_failed;
    case 'pdu': return hit('pdu_overload');
    case 'door': return hit('door_forced') || hit('door_held');
    case 'cctv': return hit('cctv_offline');
    case 'fire': return (F.smoke || []).includes(item.floor) || (F.smoke || []).includes('*');
    /* ── 신규 계통 — 고장 표시. 상류일수록 아래층 전체가 함께 붉어진다 ── */
    case 'substation': return hit('substation_fail');
    case 'transformer': return hit('transformer_fail');
    case 'ats': return hit('ats_fail');
    case 'busway': return hit('busway_trip');
    case 'fuel_tank': return hit('fuel_leak') || (ST?.fuel?.hours_left ?? 999) < 2;
    case 'battery': return hit('battery_runaway') || !!ST?.battery?.[item.id]?.runaway;
    case 'cooling_tower': return hit('cooling_tower_fail');
    case 'pump': return hit('pump_fail') || ST?.plant?.pumps?.[item.id] === false;
    case 'heat_exchanger': return hit('hx_fouling');
    case 'economizer': return hit('economizer_stuck');
    case 'cdu': return hit('cdu_leak');
    case 'fan_coil': return hit('fan_coil_fail');
    default: return false;
  }
}
const floorAlarms = fid => (ST?.alarms || []).filter(a =>
  (a.scope || '').startsWith(fid) || (fid === '1F' && (a.scope || '').startsWith('facility')));

/* ══ 스프라이트 ═════════════════════════════════════════════════ */

/** 고장 배지 — 물건 위에 뜨는 경고. 퍼지는 링이 시선을 끌어온다. */
const warnBadge = (cx, cy) => el('g', { filter: 'url(#bloom)' }, [
  el('circle', { cx, cy, r: 4, fill: 'none', stroke: '#ff4d6a', 'stroke-width': 1.6, class: 'warnring' }),
  el('circle', { cx, cy, r: 7, fill: '#ff4d6a', stroke: '#02050a', 'stroke-width': 1.6 }),
  el('rect', { x: cx - .9, y: cy - 4, width: 1.8, height: 5, fill: '#fff' }),
  el('rect', { x: cx - .9, y: cy + 2, width: 1.8, height: 1.8, fill: '#fff' }),
]);

const RT_COLOR = { bastion: '#2ee6ff', hermes: '#a78bfa', claude: '#ffb020', codex: '#a7e1bc' };
const AU_COLOR = { L3: '#ff4d6a', approver: '#3ddc97', L2: '#38bdf8', L1: '#5b7185' };

function drawFloorContent(fid, detail) { return drawRoom(fid, detail); }

/* ══ 장면 ══════════════════════════════════════════════════════ */
function sceneFrame(svg) {
  const compact = svg.clientHeight < 260;
  const side = svg.clientWidth < 500 ? 12 : 18;
  const top = compact ? 36 : 44, bottom = compact ? 40 : 50;
  return {side, top, bottom, width:Math.max(1, svg.clientWidth - side * 2),
    height:Math.max(1, svg.clientHeight - top - bottom)};
}
function prepareScene(svg, building) {
  const frame = sceneFrame(svg), aspect = frame.width / frame.height;
  if (building) {
    YS = 14;
    // Fan the floors sideways on landscape displays; keep the vertical stack
    // on narrow screens. Equipment proportions stay the same in every room.
    STAGGER.dy = Math.max(90, Math.min(170, 170 - (aspect - 1.25) * 55));
    const steps = Math.max(1, floors().length - 1);
    STAGGER.dx = Math.max(130, (aspect * (350 + steps * STAGGER.dy) - (GW + GD) * XS) / steps);
  } else {
    // Lower the camera elevation when width is available. Cabinet heights and
    // labels are preserved; only the floor projection becomes shallower.
    YS = Math.max(5, Math.min(14, ((GW + GD) * XS / aspect - 125) / 16));
  }
}
function drawBuilding() {
  const svg = $('#scene');
  prepareScene(svg, true);
  svg.replaceChildren(); LBL = []; TIPS.clear(); tipSeq = 0;
  svg.appendChild(sceneDefs());
  const root = el('g'); svg.appendChild(root);
  // Riser routes are behind the cutaway rooms.
  for (let i = 0; i + 1 < floors().length; i++) {
    const a = iso(.2, .2, 1.7), b = iso(.2, .2, .22);
    root.appendChild(el('line', { x1:a[0]+i*STAGGER.dx, y1:a[1]-i*STAGGER.dy,
      x2:b[0]+(i+1)*STAGGER.dx, y2:b[1]-(i+1)*STAGGER.dy,
      stroke:'#688b9e', 'stroke-width':1.3, 'stroke-dasharray':'4 5', opacity:.45 }));
  }
  floors().forEach((f, i) => {
    root.appendChild(el('g', {
      transform: `translate(${i * STAGGER.dx},${-i * STAGGER.dy})`,
      class: 'hit', on: { click: () => enterFloor(f.id) } }, [drawFloorContent(f.id, false)]));
    const [sx,sy] = iso(GW-.3, GD-.2, .22);
    pill(sx+i*STAGGER.dx, sy-i*STAGGER.dy+5, `${f.id} · ${f.name}`, {
      anchor:'mid', sub: `${racksOf(f.id).length} RACKS / 근무자 ${crewOf(f.id).length}명`,
      color:floorAlarms(f.id).length?'#f49797':'#bfd5e2', size:10, gap:3 });
  });
  finish(svg, root);
}

function drawFloor(fid) {
  const svg = $('#scene');
  prepareScene(svg, false);
  svg.replaceChildren(); LBL = []; TIPS.clear(); tipSeq = 0;
  svg.appendChild(sceneDefs());
  const root = el('g'); svg.appendChild(root);
  root.appendChild(drawFloorContent(fid, true));
  finish(svg, root);
}

function finish(svg, root) {
  const b = root.getBBox(), frame = sceneFrame(svg);
  const scale = Math.max(.05, Math.min(frame.width / b.width, frame.height / b.height));
  // Use the viewport's full aspect ratio. Pixel-sized gutters reserve space
  // for the caption and controls without adding large margins around the room.
  const width = svg.clientWidth / scale, height = svg.clientHeight / scale;
  BASE_VB = [b.x - (width - b.width) / 2,
    b.y - frame.top / scale - (frame.height / scale - b.height) / 2, width, height];
  const vb = curVB(), k = Math.max(.05, Math.min(svg.clientWidth / vb[2], svg.clientHeight / vb[3]));
  emitLabels(root, k, vb);                     // 그 배율로 라벨을 얹는다
  applyVB();
}
function curVB() {
  const [x, y, w, h] = BASE_VB, k = VIEW.zoom, nw = w / k, nh = h / k;
  return [x + (w - nw) / 2 + VIEW.panx, y + (h - nh) / 2 + VIEW.pany, nw, nh];
}
function applyVB() {
  if (!BASE_VB) return;
  $('#scene').setAttribute('viewBox', curVB().join(' '));
  $('#scene').setAttribute('preserveAspectRatio', 'xMidYMid meet');
}
function render() {
  if (!LAYOUT) return;
  if (drag) return;
  if ($('#stage-body').hidden) { renderLift(); renderAssetExplorer(); return; }
  if (VIEW.mode === 'floor' && VIEW.floor) drawFloor(VIEW.floor); else drawBuilding();
  renderLift();
  refreshTip();
}
function enterFloor(fid) {
  VIEW = { ...VIEW, mode: 'floor', floor: fid, zoom: 1, panx: 0, pany: 0 };
  render(); renderZonePane(); renderCrew(); renderAssetExplorer();
}
function enterBuilding() {
  VIEW = { ...VIEW, mode: 'building', floor: null, zoom: 1, panx: 0, pany: 0 };
  render(); renderZonePane(); renderCrew(); renderAssetExplorer();
}

/* ══ 엘리베이터 패널 ════════════════════════════════════════════ */
function renderLift() { renderFloorSelector(); }

/* ══ HUD 게이지 ═════════════════════════════════════════════════ */
function renderGauges() { renderMetrics(); }

/* ══ 우측 레일 ══════════════════════════════════════════════════ */
function selectTab(name) {
  $$('#tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.pane').forEach(p => p.classList.toggle('active', p.id === `pane-${name}`));
}

function renderPower() {
  const p = ST?.power; if (!p) return;
  const pdus = LAYOUT?.facility?.pdu || [];
  const src = !p.utility_ok ? (p.generator_running ? ['발전기', 'on'] : ['UPS 배터리', 'off'])
                            : ['상용전원', 'on'];
  $('#pane-power').innerHTML = `
  <div class="card"><h4>전원 계통</h4><div class="body">
    <div class="row"><span class="k">공급원</span><span class="v"><span class="pill ${src[1]}">${src[0]}</span></span></div>
    <div class="row"><span class="k">수전</span><span class="v"><span class="pill ${p.utility_ok ? 'on' : 'off'}">${p.utility_ok ? '정상' : '상실'}</span></span></div>
    <div class="row"><span class="k">비상 발전기</span><span class="v"><span class="pill ${
      p.generator_failed ? 'off' : p.generator_running ? 'on' : ''}">${
      p.generator_failed ? '기동 실패' : p.generator_running ? '운전 중' : '대기'}</span></span></div>
    <div class="row"><span class="k">총 부하 / 정격</span><span class="v">${p.total_kw.toFixed(1)} / ${p.rated_kw} kW</span></div>
    <div class="bar"><i style="width:${Math.min(p.total_kw / p.rated_kw * 100, 100)}%;background:${
      p.total_kw / p.rated_kw > .9 ? 'var(--red)' : 'var(--cyan)'}"></i></div>
    <div class="row"><span class="k">UPS 충전</span><span class="v">${p.ups_charge_pct}%${
      p.on_battery ? ` · -${p.drain_pct_per_min}%/분` : ''}</span></div>
    <div class="bar"><i style="width:${p.ups_charge_pct}%;background:${
      p.ups_charge_pct < 25 ? 'var(--red)' : p.on_battery ? 'var(--amber)' : 'var(--green)'}"></i></div>
    ${p.on_battery ? `<div class="row"><span class="k">잔여 시간</span><span class="v" style="color:var(--red)">${p.ups_runtime_min} 분</span></div>` : ''}
    <div class="row" title="랩의 진짜 소비. 화면의 kW 는 대표 DC 규모로 환산한 값이고 사용률만 실측이다.">
      <span class="k">실물 실측 — ${(p.measured_scope || ['—']).join(', ')} (환산 전)</span>
      <span class="v">${p.measured_kw} kW</span></div>
  </div></div>
  <div class="card"><h4>PDU 부하</h4><div class="body">${pdus.map(d => {
    const kw = p.pdu?.[d.id] ?? 0, pct = kw / d.capacity_kw * 100;
    return `<div class="row"><span class="k">${d.id} <span class="muted">${d.floor}</span></span>
      <span class="v">${kw.toFixed(2)} / ${d.capacity_kw} kW</span></div>
      <div class="bar"><i style="width:${Math.min(pct, 100)}%;background:${
        pct > 90 ? 'var(--red)' : pct > 70 ? 'var(--amber)' : 'var(--green)'}"></i></div>`;
  }).join('')}</div></div>
  <div class="card"><h4>아일 온습도</h4><div class="body">${
    Object.values(ST.aisles || {}).map(a => `
    <div class="row"><span class="k">${a.aisle} 아일 <span class="muted">${a.floor}</span></span>
      <span class="v" style="color:${tempColor(a.temp_c)}">${a.temp_c}°C · ${a.humidity_pct}%RH</span></div>
    <div class="bar"><i style="width:${Math.min((a.temp_c - 16) / 26 * 100, 100)}%;background:${tempColor(a.temp_c)}"></i></div>
    <div class="row"><span class="k muted">발열 ${a.it_kw}kW · 냉방 ${a.cooling_kw}kW</span>
      <span class="v" style="font-size:10.5px;color:${a.cooling_kw < a.it_kw ? 'var(--red)' : 'var(--green)'}">${
        a.cooling_kw < a.it_kw ? '냉방 부족' : '균형'}</span></div>`).join('')}</div></div>`;
}

function renderZonePane() {
  const pane = $('#pane-zone');
  const scope = VIEW.mode === 'floor' ? VIEW.floor : null;
  const zs = scope ? zonesOf(scope) : (LAYOUT?.zones || []);
  const pool = scope ? assetsOf(scope) : (LAYOUT?.it_assets || []);
  pane.innerHTML = `<div class="railhead">${scope ? `${scope} 의 존 ${zs.length}개` : '전체 존'} — 층은 물리, 존은 논리</div>`
    + zs.map(z => {
      const mine = pool.filter(a => a.zone === z.id || (z.logical && a.logical_zone === z.id));
      const down = mine.filter(a => !alive(a)).length;
      return `<div class="zcard" data-z="${z.id}" style="border-left-color:${z.color}">
        <div class="zh"><b style="color:${z.color}">${z.id}</b><span>${z.name}</span>
          <span class="trust">${z.trust}${z.logical ? ' 논리' : ''}${z.isolated ? ' 격리' : ''}</span></div>
        <div class="cidr">${z.cidr || '— 세그먼트 없음 (권한 경계)'}${z.gateway ? ` · gw ${z.gateway}` : ''}</div>
        <div class="zrole">${z.role}</div>
        ${mine.length ? `<div class="zassets">${mine.map(a =>
          `<span class="za ${alive(a) ? '' : 'down'}">${a.name}</span>`).join('')}</div>` : ''}
        ${down ? `<div class="zrole" style="color:var(--red)">정지 ${down}건</div>` : ''}</div>`;
    }).join('');
  $$('[data-z]', pane).forEach(n => n.onclick = () => openZone(n.dataset.z));
}

function crewPortrait(w) {
  const sprite=createAgentSprite(w);
  sprite.classList.add('por');
  return sprite.outerHTML;
}

function renderCrew() {
  const pane = $('#pane-crew'), ws = ROSTER.workers || [];
  if (!ws.length) { pane.innerHTML = '<div class="empty">근무자 명단을 읽지 못했습니다</div>'; return; }
  const scope = VIEW.mode === 'floor' ? VIEW.floor : null;
  const groups = scope ? floors().filter(f => f.id === scope) : floors();
  pane.innerHTML = groups.map(f => {
    const list = ws.filter(w => w.floor === f.id);
    if (!list.length) return '';
    return `<div class="railhead">${f.id} ${f.name} · AI 에이전트 ${list.length}명</div>` + list.map(w => `
      <div class="crew" data-crew="${w.id}">${crewPortrait(w)}
        <div style="flex:1;min-width:0">
          <div class="nm">${w.name}</div><div class="sub">${w.id}</div>
          <div class="crew-activity" style="color:${workerActivity(w.id).color}">${workerActivity(w.id).label}</div>
          <div class="meta"><span class="tag rt-${w.runtime}">${w.runtime}</span>
            <span class="tag au-${w.autonomy}">${w.autonomy}</span>
            <span class="tag" style="color:${zoneColor(w.zone)};border-color:${zoneColor(w.zone)}88">${w.zone}</span></div>
          <div class="loops">${(w.loop_detail || []).length
            ? w.loop_detail.map(l => `· ${l.name} <span style="color:var(--dimmer)">${
                l.cadence || ''}${l.runbook ? ' · 런북' : ''}</span>`).join('<br>')
            : '<span style="color:var(--dimmer)">등록된 루프 없음</span>'}</div>
        </div></div>`).join('');
  }).join('') || '<div class="empty">이 층에 배치된 근무자가 없습니다</div>';
  $$('[data-crew]', pane).forEach(n => n.onclick = () => openCrew(n.dataset.crew));
}

function renderAlarms() {
  const list = ST?.alarms || [];
  $('#pane-alarm').innerHTML = !list.length ? '<div class="empty">활성 경보 없음</div>'
    : list.map(a => `<div class="alarm-item ${a.level >= 12 ? 'l12' : ''}">
      <span class="lv">L${a.level}</span><div class="t">${a.msg}</div>
      <div class="m">${a.scope} · ${a.metric}=${a.value}</div></div>`).join('');
}
const hhmmss = ts => { const d = new Date(ts * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, '0')).join(':'); };
function renderLog() {
  $('#pane-log').innerHTML = !EVENTS.length ? '<div class="empty">이벤트 없음</div>'
    : EVENTS.slice().reverse().map(e =>
      `<div class="logline k-${e.kind}"><span class="ts">${hhmmss(e.ts)}</span><span>${e.msg}</span></div>`).join('');
}
function renderTicker() {
  const last = EVENTS[EVENTS.length - 1], line = $('#tk-line');
  line.className = 'tk-line' + (last ? ` k-${last.kind}` : '');
  line.textContent = last ? `${hhmmss(last.ts)}  ${last.msg}` : '이벤트 없음';
  const nf = Object.values(ST?.faults || {}).reduce((s, v) => s + v.length, 0);
  $('#tk-stats').textContent = `이벤트 ${EVENTS.length} · 경보 ${(ST?.alarms || []).length}`
    + ` · 주입 ${nf} · 차단 ${(ST?.shed || []).length}`;
}
function renderLegend() { renderRoomLegend(); }

/* ══ 복사 ══════════════════════════════════════════════════════
 * 이 화면은 평문 http(:8020) 로 연다. navigator.clipboard 는 **보안 컨텍스트에서만**
 * 존재하므로 여기서는 대개 undefined 다. 예전 코드는 `navigator.clipboard?.writeText(…)`
 * 로 조용히 아무것도 하지 않고 바로 다음 줄에서 "복사했습니다"를 띄웠다 — 화면이
 * 거짓말을 했다. 학생은 붙여넣기가 안 되는 이유를 알 길이 없었다.
 *
 * 그래서 ① 쓸 수 있으면 표준 API 를 쓰고, ② 아니면 execCommand 로 떨어지고,
 * ③ 둘 다 안 되면 **성공했다고 말하지 않는다.**
 */
async function copyText(text) {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 권한 거부·정책 차단 — 아래 폴백으로 */ }
  try {
    // 낡았지만 평문 http 에서도 동작하는 유일한 길이다.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);      // iOS 는 select() 만으로 부족하다
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch { return false; }
}

function selectNode(n) {
  try {
    const r = document.createRange();
    r.selectNodeContents(n);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  } catch { /* 선택도 안 되면 할 수 있는 게 없다 */ }
}

/* ══ 상세 패널 ══════════════════════════════════════════════════ */
function showDrawer(name, zoneId, html) {
  $('#dr-name').textContent = name;
  const z = $('#dr-zone');
  if (zoneId) { z.textContent = zoneId; z.style.color = zoneColor(zoneId); z.hidden = false; }
  else z.hidden = true;
  $('#dr-body').innerHTML = html;
  $('#drawer').hidden = false;
  $$('#dr-body .cmd').forEach(n => n.onclick = async () => {
    const text = n.textContent.replace(/^\$ /, '');
    if (await copyText(text)) {
      const t = n.textContent;
      n.textContent = '복사했습니다';
      setTimeout(() => n.textContent = t, 900);
    } else {
      // 글자를 바꾸면 선택이 풀린다. 실패했을 때 쓸모 있는 건 메시지가 아니라
      // **골라 둔 텍스트**다 — 그대로 두고 선택한 뒤, 안내는 CSS 로 겹쳐 띄운다.
      selectNode(n);
      n.classList.add('copy-fail');
      setTimeout(() => n.classList.remove('copy-fail'), 2400);
    }
  });
}
const kv = (k, v) => v == null || v === '' ? ''
  : `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;

function openAsset(id) {
  const a = (LAYOUT?.it_assets || []).find(x => x.id === id); if (!a) return;
  SELECTED = id;
  const st = assetState(id), up = alive(a), zn = zoneOf(a.zone);
  const ct = a.container ? ST?.containers?.[a.container] : null;
  const grp = LAYOUT?.shed_groups?.[a.shed_group];
  showDrawer(a.name, a.zone, `
    ${kv('자산 ID', a.id)}
    ${kv('상태', up ? '<span style="color:var(--green)">가동 중</span>' : '<span style="color:var(--red)">정지</span>')}
    ${kv('위치', `${a.floor} · ${a.rack || '랙 외'}${a.u ? ` · ${a.u}U` : ''}`)}
    ${kv('존', `${a.zone} (${zn?.trust || '-'}) ${zn?.cidr || ''}`)}
    ${a.logical_zone ? kv('권한 경계', `${a.logical_zone} — 망 경계와 다르다`) : ''}
    ${kv('주소', a.ip)} ${kv('실체', a.container || (a.remote ? `원격 ${a.remote}`
      : a.host || '-'))}
    ${ct ? kv('컨테이너', ct.status) : ''}
    <div class="kv"><span class="k">수집 기반 사용률</span><span class="v">${(st.util * 100).toFixed(0)}%</span></div>
    <div class="bar"><i style="width:${Math.min(st.util * 100, 100)}%;background:${
      st.util > .7 ? 'var(--red)' : 'var(--cyan)'}"></i></div>
    ${kv('환산 전력', `${st.kw.toFixed(2)} kW <span style="color:var(--dimmer)">(${a.idle_kw}~${a.rated_kw})</span>`)}
    ${grp ? kv('부하 그룹', `${grp.name} · 우선순위 ${grp.priority}`) : ''}
    ${grp ? `<div class="note">차단 시: ${grp.impact}</div>` : ''}
    <div class="access">
      ${a.web ? `<a class="btn act" href="${a.web}" target="_blank" rel="noopener">웹 콘솔 열기 ↗</a>` : ''}
      ${a.api ? `<a class="btn" href="${a.api}" target="_blank" rel="noopener">API ↗</a>` : ''}
      ${a.ssh ? `<div class="cmd">${a.ssh}</div>` : ''}
      ${a.container ? `<div class="cmd">docker exec -it ${a.container} sh</div>` : ''}
      ${a.container ? `<div class="cmd">docker logs -f --tail 100 ${a.container}</div>` : ''}
    </div>`);
}
function openZone(id) {
  const z = zoneOf(id); if (!z) return; SELECTED = null;
  const mine = (LAYOUT?.it_assets || []).filter(a => a.zone === id || (z.logical && a.logical_zone === id));
  const inn = (LAYOUT?.zone_chain || []).filter(c => c.to === id);
  const out = (LAYOUT?.zone_chain || []).filter(c => c.from === id);
  showDrawer(`${z.id} · ${z.name}`, id, `
    ${kv('대역', z.cidr || '없음 — 논리 존')}
    ${kv('신뢰등급', `${z.trust}${z.isolated ? ' · 격리망' : ''}${z.logical ? ' · 논리' : ''}`)}
    ${kv('게이트웨이', z.gateway)}
    <div class="note">${z.role}</div>
    <div class="dsec">들어오는 길 ${inn.length}</div>
    ${inn.map(c => kv(`${c.from} →`, `${c.via} · ${c.label}`)).join('') || '<div class="muted">없음</div>'}
    <div class="dsec">나가는 길 ${out.length}</div>
    ${out.map(c => kv(`→ ${c.to}`, `${c.via} · ${c.label}`)).join('') || '<div class="muted">없음</div>'}
    <div class="dsec">자산 ${mine.length}</div>
    ${mine.map(a => `<div class="kv" style="cursor:pointer" data-a="${a.id}">
      <span class="k">${a.name} <span class="muted">${a.floor}</span></span>
      <span class="v" style="color:${alive(a) ? 'var(--tx)' : 'var(--red)'}">${
        alive(a) ? assetState(a.id).kw.toFixed(2) + 'kW' : '정지'}</span></div>`).join('')
      || '<div class="muted">없음</div>'}
    <div class="note">존 밖으로 나가는 트래픽은 위의 경유 지점을 반드시 지난다.
      우회로가 없다는 것이 이 랩의 핵심 성질이다.</div>`);
  $$('#dr-body [data-a]').forEach(n => n.onclick = () => openAsset(n.dataset.a));
}
function openRack(id) {
  const r = (LAYOUT?.racks || []).find(x => x.id === id); if (!r) return; SELECTED = null;
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === id);
  const a = ST?.aisles?.[r.aisle];
  const kw = list.reduce((s, x) => s + assetState(x.id).kw, 0);
  showDrawer(r.id, null, `
    ${kv('층 · 아일', `${r.floor} · ${r.aisle}`)}
    ${kv('용량', `${r.u}U · 설계 ${r.design_kw}kW`)}
    ${kv('현재 부하', `${kw.toFixed(2)} kW (${(kw / r.design_kw * 100).toFixed(0)}%)`)}
    ${a ? kv('아일 온습도', `${a.temp_c}°C · ${a.humidity_pct}%RH`) : ''}
    ${a ? kv('냉방', `${a.cooling_kw}kW ${a.cooling_kw < a.it_kw ? '— 부족' : ''}`) : ''}
    ${kv('섞여 있는 존', [...new Set(list.map(x => x.zone))]
      .map(z => `<span style="color:${zoneColor(z)}">${z}</span>`).join(' '))}
    <div class="dsec">탑재 자산 ${list.length}</div>
    ${list.map(x => `<div class="kv" style="cursor:pointer" data-a="${x.id}">
      <span class="k"><i style="display:inline-block;width:8px;height:8px;border-radius:2px;
        background:${zoneColor(x.zone)};margin-right:7px"></i>${x.name}</span>
      <span class="v">${assetState(x.id).kw.toFixed(2)}kW</span></div>`).join('')}
    <div class="note">한 랙 안에 서로 다른 존이 섞여 있다 — 물리적으로 옆자리인데 논리적으로
      다른 망이다. 이 어긋남이 1주차 실습 재료다.<br><br>
      같은 아일의 랙끼리는 열이 섞인다. 한 랙의 폭주가 옆 랙 온도를 올린다.</div>`);
  $$('#dr-body [data-a]').forEach(n => n.onclick = () => openAsset(n.dataset.a));
}
function openFacility(item) {
  SELECTED = null;
  const down = facilityDown(item), p = ST?.power;
  let extra = '';
  if (item.kind === 'ups' && p) extra = kv('충전', `${p.ups_charge_pct}%`)
    + kv('잔여', `${p.ups_runtime_min} 분`) + kv('배터리', `${item.battery_kwh} kWh / ${item.capacity_kw} kW`);
  else if (item.kind === 'generator' && p) extra = kv('상태', p.generator_failed ? '기동 실패'
    : p.generator_running ? '운전 중' : '대기') + kv('기동 지연', `${item.start_delay_s} 초`)
    + kv('연료', `${item.fuel_hours} 시간`);
  else if (item.kind === 'pdu') { const kw = p?.pdu?.[item.id] ?? 0;
    extra = kv('부하', `${kw.toFixed(2)} / ${item.capacity_kw} kW (${(kw / item.capacity_kw * 100).toFixed(0)}%)`)
      + kv('급전 랙', item.rack); }
  else if (item.kind === 'crac') { const a = ST?.aisles?.[item.aisle];
    extra = kv('담당 아일', item.aisle) + kv('정격', `${item.capacity_kw} kW`)
      + (a ? kv('현재 출력', `${a.cooling_kw} kW`) : ''); }
  showDrawer(item.name || item.id, 'ot', `
    ${kv('설비 ID', item.id)} ${kv('종류', item.kind)} ${kv('층', item.floor)}
    ${kv('상태', down ? '<span style="color:var(--red)">이상</span>'
                      : '<span style="color:var(--green)">정상</span>')}
    ${extra}
    <div class="note">시설 계통은 가상이다. 다만 이 계통이 계산에 쓰는 <b>발열은 실측</b>이다 —
      컨테이너 CPU 와 GPU 상태에서 온다.</div>`);
}
function openCrew(id) {
  const w = (ROSTER.workers || []).find(x => x.id === id); if (!w) return; SELECTED = null;
  const auto = { L1: '보고 전용 — 상태를 바꾸지 않는다',
    L2: '승인 후 실행 — 운영 리드의 판정이 있어야 움직인다',
    L3: '무인 실행 — 런북이 등록된 작업에만 허용된다',
    approver: '승인 전담 — 스스로 실행하지 않고 L2 요청을 판정한다' }[w.autonomy] || '';
  showDrawer(w.name, w.zone, `
    <a class="btn agent-control-link" href="${dcURL('requests')}?worker=${encodeURIComponent(w.id)}">${safeText(w.name)}에게 말 걸기 ↗</a>
    <a class="btn agent-control-link" href="/agent-control?worker=${encodeURIComponent(w.id)}">이 에이전트의 실행·판단·증거 조사 ↗</a>
    <div data-crew-activity="${safeText(w.id)}">${workerActivityMarkup(w.id)}</div>
    ${kv('페르소나 ID', w.id)} ${kv('배치', `${w.floor} · ${w.zone} 존`)}
    ${kv('런타임', w.runtime)} ${kv('자율 등급', w.autonomy)}
    <div class="note">${auto}</div>
    ${kv('담당 자산', (w.assets || []).join(', ') || '-')}
    ${kv('교과 주차', (w.curriculum || []).join(', ') || '-')}
    <div class="dsec">루프 ${(w.loop_detail || []).length}</div>
    ${(w.loop_detail || []).map(l => kv(l.name,
      `${l.cadence || ''} · ${l.steps}단계 · 게이트 ${l.gates}`)).join('')
      || '<div class="muted">등록된 루프 없음</div>'}
    <div class="access">
      <div class="cmd">agents/agentctl render ${w.id}</div>
      <div class="cmd">agents/agentctl runtime ${w.id} hermes</div>
    </div>
    <div class="note">런타임은 페르소나마다 따로 고른다. 명세는 중립이고 어댑터가 각
      런타임의 형식으로 렌더한다.</div>`);
}

/* ══ UPS 절체 판단 ══════════════════════════════════════════════ */
function renderUps() {
  const p = ST?.power, modal = $('#ups-modal');
  if (!p?.on_battery) { modal.hidden = true; upsDismissed = false; return; }
  if (upsDismissed) { modal.hidden = true; return; }
  modal.hidden = false;
  $('#ups-sub').textContent = p.generator_failed
    ? '비상 발전기 기동 실패 — 배터리만 남았다' : '발전기 기동 대기 중';
  $('#ups-stats').innerHTML = `
    <div class="ups-stat ${p.ups_charge_pct < 30 ? 'crit' : ''}"><label>배터리</label>
      <b>${p.ups_charge_pct}%</b><small>분당 ${p.drain_pct_per_min}% 감소</small></div>
    <div class="ups-stat ${p.ups_runtime_min < 10 ? 'crit' : ''}"><label>잔여 시간</label>
      <b>${p.ups_runtime_min}분</b><small>배터리 실부하 ${p.battery_kw}kW 기준</small></div>
    <div class="ups-stat"><label>총 부하</label><b>${p.total_kw.toFixed(1)}kW</b>
      <small>정격 ${p.rated_kw}kW · UPS 손실 +${(p.battery_kw - p.total_kw).toFixed(2)}kW</small></div>
    <div class="ups-stat"><label>냉방</label>
      <b style="color:${p.generator_running ? 'var(--green)' : 'var(--red)'}">${
        p.generator_running ? '가동' : '정지'}</b><small>CRAC 은 UPS 를 타지 않는다</small></div>`;
  $('#ups-body').innerHTML = `<table>
    <thead><tr><th>부하 그룹 · 차단 시 영향</th><th style="text-align:right">소비</th>
      <th style="text-align:right">차단 시 잔여</th><th></th></tr></thead><tbody>${
    (ST.shed_analysis || []).map(g => {
      const empty = (g.assets ?? 1) === 0;
      return `<tr class="${g.shed ? 'shed' : ''}">
        <td><b>${g.name}</b><div class="muted">${empty
          ? '이 그룹에 배치된 자산이 아직 없다 — 끊어도 부하가 줄지 않는다' : g.impact}</div></td>
        <td class="num">${empty ? '—' : g.kw.toFixed(1) + 'kW'}</td>
        <td class="num" style="color:${!empty && g.runtime_if_shed_min > p.ups_runtime_min * 1.3
          ? 'var(--green)' : 'var(--dim)'}">${g.shed || empty ? '—' : `${g.runtime_if_shed_min}분`}</td>
        <td>${empty ? '' : `<button class="btn sm ${g.shed ? '' : 'danger'}" data-shed="${g.group}"
          data-restore="${g.shed}">${g.shed ? '복구' : '차단'}</button>`}</td></tr>`;
    }).join('')}</tbody></table>`;
  $$('#ups-body [data-shed]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    await post('/api/shed', { group: b.dataset.shed, restore: b.dataset.restore === 'true' });
    await poll();
  });
}

/* ══ 강사 패널 ══════════════════════════════════════════════════ */
function faultTargets(fault) {
  const F = LAYOUT?.facility || {}, ids = l => (l || []).map(i => i.id);
  switch (fault) {
    case 'utility_fail': return [F.utility?.id].filter(Boolean);
    case 'generator_fail': return ids(F.generator);
    case 'chiller_fail': return ids(F.chiller);
    case 'crac_fail': return ids(F.crac);
    case 'pdu_overload': return ids(F.pdu);
    case 'smoke': return floors().map(f => f.id);
    case 'door_forced': case 'door_held':
      return (F.security || []).filter(s => s.kind === 'door').map(s => s.id);
    case 'cctv_offline': return (F.security || []).filter(s => s.kind === 'cctv').map(s => s.id);
    case 'humidity_drift': return Object.keys(ST?.aisles || {});
    /* ── 상류 전력 (교재 5.2.1) ─────────────────────────────────── */
    case 'substation_fail': return ids(F.substation);
    case 'transformer_fail': return ids(F.transformer);
    case 'ats_fail': return ids(F.ats);
    case 'busway_trip': return ids(F.busway);
    case 'fuel_leak': return ids(F.fuel_tank);
    case 'battery_runaway': return ids(F.battery);
    /* ── 냉각 계통 (교재 5.3) ───────────────────────────────────── */
    case 'cooling_tower_fail': return ids(F.cooling_tower);
    case 'pump_fail': return ids(F.pump);
    case 'hx_fouling': return ids(F.heat_exchanger);
    case 'economizer_stuck': return ids(F.economizer);
    case 'cdu_leak': return ids(F.cdu);
    case 'fan_coil_fail': return ids(F.fan_coil);
    case 'containment_open': return (F.containment || []).filter(c => c.sealed).map(c => c.id);
    case 'airflow_block': return ids(F.raised_floor);
    case 'heatwave': return [F.weather?.id].filter(Boolean);
    default: return ['*'];
  }
}
/* 주입 목록은 두 곳에서 온다 — 시설(OT) 25종은 envsim, IT 38종은 injector.
 * 강사는 그 구분을 알 필요가 없으므로 한 목록으로 합쳐 보여준다. */
const DOM_ALL = {
  facility: { name: '시설 · 환경(OT)', color: '#4fc3f7' },
  system:   { name: '시스템 · 프로세스', color: '#38bdf8' },
  storage:  { name: '스토리지 · 디스크', color: '#ffb020' },
  network:  { name: '네트워크', color: '#a78bfa' },
  security: { name: '보안', color: '#ff4d6a' },
  load:     { name: '부하 · 성능', color: '#3ddc97' },
};
let INJDOM = 'facility', INJQ = '';

function allInjections() {
  const fac = Object.entries(FAULTS.available || {}).map(([k, desc]) => ({
    src: 'env', id: k, domain: 'facility', name: desc, desc: '',
    teaches: '', kind: 'state', danger: 2, targets: faultTargets(k), params: [], scenarios: [],
  }));
  return [...fac, ...(INJCAT.injections || []).map(i => ({ ...i, src: 'inj' }))];
}

/** 지금 걸려 있는 것. envsim 은 fault→대상 목록, injector 는 handle 단위다. */
function activeInjections() {
  const out = [];
  for (const [k, ts] of Object.entries(ST?.faults || {}))
    for (const t of ts) out.push({ src: 'env', id: k, target: t, domain: 'facility',
      name: FAULTS.available?.[k] || k, remaining: null });
  for (const a of (INJACT.active || []))
    out.push({ src: 'inj', handle: a.handle, id: a.id, target: a.target,
      domain: a.domain, name: a.name, remaining: a.remaining, elapsed: a.elapsed });
  return out;
}

async function clearOne(a) {
  if (a.src === 'env') await post('/api/inject', { fault: a.id, target: a.target, clear: true });
  else await post('/api/inj/clear', { handle: a.handle });
}

function renderInjector() {
  const body = $('#inj-body'), ts = ST?.time_scale ?? 1;
  Object.assign(DOM_ALL, INJCAT.domains || {});
  const list = allInjections();
  $('#inj-count').textContent = `고장 주입 ${list.length}종 · 시설 ${Object.keys(FAULTS.available || {}).length} + IT ${(INJCAT.injections || []).length}`;
  const act = activeInjections();
  const counts = {};
  list.forEach(i => counts[i.domain] = (counts[i.domain] || 0) + 1);
  const q = INJQ.trim().toLowerCase();
  const shown = list.filter(i => i.domain === INJDOM &&
    (!q || (i.name + i.id + (i.desc || '') + (i.scenarios || []).join()).toLowerCase().includes(q)));

  const DANGER = ['', '국소', '서비스 영향', '랩 전체'];

  body.innerHTML = `
    <div class="inj-top">
      <div class="frow" style="flex:1">
        <span class="fname">시간 배속<small>열 시나리오는 ×10 이상 · UPS 절체(ENV-03)는 ×1 유지</small></span>
        <select id="ts-sel">${[1, 5, 10, 30, 60].map(v =>
          `<option value="${v}" ${v === ts ? 'selected' : ''}>×${v}${v === 1 ? ' 실시간' : ''}</option>`).join('')}</select>
        <button class="btn sm act" id="ts-apply">적용</button>
      </div>
    </div>

    ${act.length ? `<div class="inj-active">
      <div class="ia-h">진행 중 ${act.length}건 <span class="muted">— state 형은 TTL 이 지나면 스스로 풀린다</span></div>
      ${act.map((a, n) => `<div class="ia-row">
        <i style="background:${DOM_ALL[a.domain]?.color || '#7b93ad'}"></i>
        <b>${a.name}</b><span class="mono">${a.target}</span>
        ${a.remaining != null ? `<span class="ttl">${Math.floor(a.remaining / 60)}:${
          String(a.remaining % 60).padStart(2, '0')}</span>` : '<span class="ttl">—</span>'}
        <button class="btn sm" data-clr1="${n}">해제</button></div>`).join('')}
    </div>` : ''}

    <div class="inj-tabs">${Object.entries(DOM_ALL).map(([k, v]) =>
      `<button class="itab ${k === INJDOM ? 'on' : ''}" data-dom="${k}"
        style="--c:${v.color}">${v.name}<em>${counts[k] || 0}</em></button>`).join('')}</div>

    <input class="inj-search" id="inj-q" placeholder="이름 · ID · 시나리오로 찾기 (예: FLT-03)" value="${safeText(INJQ)}">

    <div class="fgrid">${shown.map((i, n) => {
      const on = act.filter(a => a.id === i.id);
      return `<div class="frow inj ${on.length ? 'active' : ''}">
        <div class="fname">
          <div class="ih"><b>${i.name}</b>
            <span class="dg d${i.danger}">${DANGER[i.danger] || ''}</span>
            ${i.kind === 'action' ? '<span class="kd">1회성</span>' : ''}
            ${(i.scenarios || []).map(s => `<span class="sc">${s}</span>`).join('')}
          </div>
          ${i.desc ? `<small class="dsc">${i.desc}</small>` : ''}
          ${i.teaches ? `<small class="tch">▸ ${i.teaches}</small>` : ''}
          ${on.length ? `<small class="onn">진행 중: ${on.map(a => a.target).join(', ')}</small>` : ''}
        </div>
        <div class="fctl">
          <select data-tg="${n}">${(i.targets || []).map(t =>
            `<option value="${t}">${t.replace(/^kt66-/, '')}</option>`).join('')}</select>
          ${(i.params || []).map(p => `<label class="pin"><span>${p.label}</span>
            <input data-p="${n}:${p.name}" type="${p.type === 'str' ? 'text' : 'number'}"
              value="${p.default}" ${p.type === 'float' ? 'step="0.05"' : ''}></label>`).join('')}
          <button class="btn sm danger" data-go="${n}">주입</button>
        </div>
      </div>`;
    }).join('') || '<div class="empty">해당하는 주입이 없습니다</div>'}</div>`;

  $('#ts-apply', body).onclick = async () => {
    await post('/api/timescale', { value: $('#ts-sel', body).value });
    await poll(); await refreshInj(true); };
  $$('[data-dom]', body).forEach(b => b.onclick = () => { INJDOM = b.dataset.dom; renderInjector(); });
  const qi = $('#inj-q', body);
  qi.oninput = () => { INJQ = qi.value; renderInjector();
    const n = $('#inj-q'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
  $$('[data-clr1]', body).forEach(b => b.onclick = async () => {
    b.disabled = true; await clearOne(act[+b.dataset.clr1]); await poll(); await refreshInj(true); });
  $$('[data-go]', body).forEach(b => b.onclick = async () => {
    const n = +b.dataset.go, i = shown[n];
    const target = $(`[data-tg="${n}"]`, body)?.value;
    const p = {};
    (i.params || []).forEach(pp => {
      const el2 = $(`[data-p="${n}:${pp.name}"]`, body);
      if (el2) p[pp.name] = pp.type === 'str' ? el2.value : Number(el2.value);
    });
    b.disabled = true;
    if (i.src === 'env') await post('/api/inject', { fault: i.id, target });
    else await post('/api/inj/inject', Object.keys(p).length
      ? { id: i.id, target, params: JSON.stringify(p) } : { id: i.id, target });
    await poll(); await refreshInj(true);
  });
}

async function refreshInj(force = false) {
  const identity = d => JSON.stringify((d.active || []).map(a => [a.handle, a.id, a.target]));
  const before = identity(INJACT);
  try {
    INJACT = await get('/api/inj/active');
    $('#inj-keyhint').textContent = '고장 주입과 해제는 강사 키를 입력한 뒤 사용할 수 있습니다.';
  } catch {
    $('#inj-keyhint').textContent = 'IT 주입 현황을 읽지 못했습니다. 마지막 확인값을 표시합니다.';
    return;
  }
  if ($('#inj-modal').hidden) return;
  if (force || before !== identity(INJACT)) {
    renderInjector();
  } else {
    // Refresh countdowns without replacing the target selectors under the cursor.
    const active = activeInjections();
    $$('.ia-row .ttl').forEach((el, i) => {
      const remaining = active[i]?.remaining;
      if (remaining != null) el.textContent = `${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')}`;
    });
  }
}

/* ══ 통신 ══════════════════════════════════════════════════════ */
async function get(url) {
  const r = await fetch(url, { signal: AbortSignal.timeout(12000) }); if (!r.ok) throw new Error(`${url} → ${r.status}`); return r.json();
}
/* 강사 키 — 상태를 바꾸는 요청에만 붙는다.
 *
 * 이 화면은 학생이 보라고 있다. 층을 돌리고 경보를 읽는 것은 전부 열려 있어야 하고,
 * 잠그는 것은 고장 주입·해제·시간배속·초기화뿐이다. 그런데 그 버튼들이 아무나 누를 수
 * 있는 자리에 있었다 — 수업 중에 학생이 장난으로 누르면 그대로 들어갔다.
 *
 * 부하 차단(/api/shed)에도 키가 붙지만 서버가 그 라우트에서는 보지 않는다. ENV-03 은
 * 학생이 무엇을 끊을지 판단하는 실습이라 잠그면 실습 자체가 없어진다. 키를 붙이는 쪽이
 * 아니라 **보는 쪽**을 라우트마다 고른다 — 화면이 무엇을 잠글지 정하면 우회할 길이 생긴다.
 */
const IKEY = () => (document.getElementById('inj-key')?.value || '').trim();

async function post(url, params) {
  const q = new URLSearchParams(Object.fromEntries(
    Object.entries({ ...params, key: IKEY() }).map(([k, v]) => [k, String(v)])));
  const r = await fetch(`${url}?${q}`, { method: 'POST' });
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    if (r.status === 401) {
      const el = document.getElementById('inj-key');
      if (el) { el.focus(); el.classList.add('bad'); setTimeout(() => el.classList.remove('bad'), 1500); }
    }
    alert(`실패: ${d || r.status}`);
  }
  return r.json().catch(() => ({}));
}
let polling = false, pollingTimer = null, instructorTimer = null, clockTimer = null;
let rosterFetchedAt = 0;
async function poll() {
  if (polling || !LAYOUT) return;
  polling = true;
  try {
    const [st, ev] = await Promise.all([get('/api/state'), get('/api/events?limit=80')]);
    if (!st || !st.power || !st.assets) throw new Error('운영 상태 응답이 올바르지 않습니다.');
    ST = st; EVENTS = ev.events || [];
    if (Date.now() - rosterFetchedAt > 30000) {
      try { ROSTER = await get('/api/roster'); rosterFetchedAt = Date.now(); } catch { /* Retain the last known roster. */ }
    }
    updateConnection(true); recordObservation();
    const body = $('#tabbody'), keep = body.scrollTop;
    renderGauges(); renderOperations(); renderTelemetry();
    renderPower(); renderAlarms(); renderLog(); renderTicker();
    renderZonePane(); renderCrew(); renderUps();
    body.scrollTop = keep;
    render(); renderAssetExplorer();
    if (SELECTED) openAsset(SELECTED);
  } catch (e) {
    updateConnection(false, String(e.message || e));
  } finally { polling = false; }
}
async function boot() {
  clearInterval(pollingTimer); clearInterval(instructorTimer); clearInterval(clockTimer);
  clearInterval(workerStateTimer);
  try {
    [LAYOUT, ROSTER, FAULTS] = await Promise.all([
      get('/api/layout'), get('/api/roster'), get('/api/faults')]);
    rosterFetchedAt = Date.now();
    if (VIEW.mode === 'floor' && !floors().some(f => f.id === VIEW.floor)) {
      VIEW.mode = 'building'; VIEW.floor = null;
    }
    try { INJCAT = await get('/api/inj/catalog'); }
    catch (e) { console.warn('IT 카탈로그 연결 실패', e); }
  } catch (e) { updateConnection(false, String(e.message || e)); return; }
  renderLegend();
  await poll();
  pollWorkerStates();
  workerStateTimer=setInterval(pollWorkerStates,10000);
  pollingTimer = setInterval(poll, 3000);
  instructorTimer = setInterval(() => { if (!$('#inj-modal').hidden) refreshInj(); }, 3000);
  const clock = () => {
    $('#clock').textContent = new Date().toLocaleTimeString('ko-KR', { hour12: false });
    $('#today').textContent = new Date().toLocaleDateString('en-GB', {day:'2-digit', month:'short', year:'numeric'}).toUpperCase();
  };
  clock(); clockTimer = setInterval(clock, 1000);
}


/* ══ 배선 ══════════════════════════════════════════════════════ */
$$('#tabs .tab').forEach(t => t.onclick = () => selectTab(t.dataset.tab));
$('#dr-close').onclick = () => { $('#drawer').hidden = true; SELECTED = null; };
$('#ups-close').onclick = () => { upsDismissed = true; $('#ups-modal').hidden = true; };
/* 키는 브라우저에만 남는다. 강사 노트북에서 한 번 넣으면 다음 수업에도 그대로 있다. */
{
  const el = document.getElementById('inj-key');
  if (el) {
    el.value = localStorage.getItem('kt66_noc_key') || '';
    el.oninput = () => localStorage.setItem('kt66_noc_key', IKEY());
  }
}
$('#btn-instructor').onclick = async () => {
  $('#btn-instructor').disabled = true;
  try { await refreshInj(); renderInjector(); $('#inj-modal').hidden = false; }
  finally { $('#btn-instructor').disabled = false; }
};
$('#inj-close').onclick = () => $('#inj-modal').hidden = true;
$('#btn-reset').onclick = async () => {
  await post('/api/reset', {});             // 시설 고장 + 부하 차단
  await post('/api/inj/clear_all', {});     // IT 계통 주입 전부 + 잔재 정리
  upsDismissed = false; await poll(); await refreshInj(true); };
$('#legend-toggle').onclick = () => { $('#legend').hidden = !$('#legend').hidden; $('#legend-toggle').setAttribute('aria-expanded', String(!$('#legend').hidden)); };
$('#asset-toggle').onclick = () => {
  const showing = $('#asset-explorer').hidden;
  $('#asset-explorer').hidden = !showing; $('#stage-body').hidden = showing;
  $('#asset-toggle').textContent = showing ? '입체 배치도' : '자산 목록';
  $('#asset-toggle').setAttribute('aria-pressed', String(showing));
  if (showing) { renderAssetExplorer(); $('#asset-search').focus(); } else render();
};
$('#asset-search').oninput = renderAssetExplorer;
$('#retry').onclick = () => boot();
$('#show-events').onclick = () => { selectTab('log'); $('#rail').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); };

const zoom = k => { VIEW.zoom = Math.max(.35, Math.min(VIEW.zoom * k, 8)); render(); };
$('#z-in').onclick = () => zoom(1.25);
$('#z-out').onclick = () => zoom(1 / 1.25);
$('#z-fit').onclick = () => { VIEW.zoom = 1; VIEW.panx = VIEW.pany = 0; render(); };

const scene = $('#scene'), host = $('#stage-body');
host.addEventListener('mousemove', e => {
  MOUSE = { x: e.clientX, y: e.clientY };
  const owner = e.target.closest ? e.target.closest('[data-tip]') : null;
  if (owner) showTip(owner.getAttribute('data-tip')); else $('#tip').hidden = true;
});
host.addEventListener('mouseleave', () => $('#tip').hidden = true);
scene.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.14 : 1 / 1.14); },
  { passive: false });
/* 누른 자리에서 이만큼 움직여야 "끌기"다. 그 아래는 손떨림이고, 클릭으로 본다.
 * 누르자마자 끌기로 단정하면 안 되는 이유가 둘 있다 —
 *  1) setPointerCapture 를 걸면 뒤이은 click 이 캡처 대상(#scene)으로 옮겨 붙는다.
 *     장면 안 <g class="hit"> 의 click 핸들러는 영영 호출되지 않는다.
 *  2) pointerup 에서 render() 하면 replaceChildren() 이 방금 누른 노드를 떼어낸다.
 *     click 은 pointerup 다음에 오므로, 갈 곳이 없어져 사라진다.
 * 둘 중 하나만 있어도 장면 클릭은 전멸한다. 그래서 실제로 움직인 뒤에만 끌기로 넘어간다. */
const DRAG_MIN = 4;
let drag = null;
scene.addEventListener('pointerdown', e => {
  drag = { x: e.clientX, y: e.clientY, px: VIEW.panx, py: VIEW.pany, moved: false };
});
scene.addEventListener('pointermove', e => {
  if (!drag || !BASE_VB) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!drag.moved) {
    if (Math.hypot(dx, dy) < DRAG_MIN) return;   // 아직 클릭일 수 있다 — 아무것도 건드리지 않는다
    drag.moved = true;
    scene.classList.add('dragging');
    scene.setPointerCapture(e.pointerId);
  }
  const s = (BASE_VB[2] / VIEW.zoom) / scene.clientWidth;
  VIEW.panx = drag.px - dx * s;
  VIEW.pany = drag.py - dy * s;
  applyVB();
});
const endDrag = () => {
  const d = drag; drag = null;
  scene.classList.remove('dragging');
  if (d?.moved) render();     // 끌었을 때만 다시 그린다 — 라벨을 새 배율에 다시 앉히려고
};
scene.addEventListener('pointerup', endDrag);
scene.addEventListener('pointercancel', endDrag);

window.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!$('#inj-modal').hidden) $('#inj-modal').hidden = true;
  else if (!$('#ups-modal').hidden) { upsDismissed = true; $('#ups-modal').hidden = true; }
  else if (!$('#drawer').hidden) { $('#drawer').hidden = true; SELECTED = null; }
  else if (VIEW.mode === 'floor') enterBuilding();
});
window.addEventListener('resize', render);

installModalFocus();
installViewportFit();
boot();
