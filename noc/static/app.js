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
let INJCAT = { injections: [] }, INJACT = { active: [] };
let VIEW = { mode: 'building', floor: null, zoom: 1, panx: 0, pany: 0 };
let BASE_VB = null, SELECTED = null, upsDismissed = false;
let MOUSE = { x: 0, y: 0 };
const TIPS = new Map();                 // tipId -> 툴팁 payload
let tipSeq = 0;

/* ══ 좌표계 ═════════════════════════════════════════════════════
 * 층 평면 18x10. 층은 대각으로 엇갈려 쌓는다(exploded axonometric) — 수직으로만
 * 쌓으면 층 간격이 층 깊이보다 커야 해서 건물이 세로로만 길어지고, 전체를 보려면
 * 축소하는 수밖에 없다. 남는 가로를 써서 세로를 줄인다.
 */
const GW = 18, GD = 10;
const XS = 24, YS = 8, ZS = 32;
const STAGGER = { dx: 152, dy: 150 };
const iso = (x, y, z) => [(x - y) * XS, (x + y) * YS - z * ZS];

/* 층 안의 띠 — 뒤에서 앞으로: 설비벽 → 존 영역 → 통로 → 콜드·랙·핫 → 근무자.
 * 부피 큰 설비는 전부 뒤/옆 벽에 붙인다. 앞줄에 세우면 뒤가 통째로 가린다. */
const B = {
  zoneY: 1.5, zoneD: 3.6, drift: .35,
  walkY: 5.5, coldY: 6.2, rackY: 6.8, hotY: 8.0, pduY: 8.7, crewY: 9.3,
};
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

/** 아이소메트릭 프리즘.
 *  게임 스프라이트처럼 보이려면 세 가지가 필요하다 — 짙은 외곽선(배경에서 형태가
 *  떨어져 나온다), 면마다 다른 명도(입체가 읽힌다), 윗면 앞모서리 하이라이트(질감). */
function prism(x, y, z, w, d, h, color, o = {}) {
  const T = [iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  const Bt = [iso(x + w, y, z), iso(x + w, y + d, z), iso(x, y + d, z)];
  return el('g', {}, [
    el('polygon', {                                   // 외곽선(실루엣)
      points: pts([T[0], T[1], Bt[0], Bt[1], Bt[2], T[3]]),
      fill: 'none', stroke: '#02050a', 'stroke-width': 3.4, 'stroke-linejoin': 'round',
      class: o.glow ? 'glow' : null }),
    el('polygon', { points: pts([T[3], T[2], Bt[1], Bt[2]]), fill: shade(color, .44) }),
    el('polygon', { points: pts([T[1], T[2], Bt[1], Bt[0]]), fill: shade(color, .68) }),
    el('polygon', { points: pts(T), fill: color }),
    el('line', {                                      // 윗면 앞모서리 하이라이트
      x1: T[3][0], y1: T[3][1], x2: T[2][0], y2: T[2][1],
      stroke: 'rgba(255,255,255,.35)', 'stroke-width': 1.2 }),
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
    for (let i = 0; i < 80; i++) {
      const hit = placed.find(p => x < p.x + p.w + 5 && p.x < x + bw + 5
                                && y < p.y + p.h + 4 && p.y < y + bh + 4);
      if (!hit) break;
      y = hit.y + hit.h + 5;
    }
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
    default: return false;
  }
}
const floorAlarms = fid => (ST?.alarms || []).filter(a =>
  (a.scope || '').startsWith(fid) || (fid === '1F' && (a.scope || '').startsWith('facility')));

/* ══ 스프라이트 ═════════════════════════════════════════════════ */

/** 고장 배지 — 물건 위에 뜨는 경고. 퍼지는 링이 시선을 끌어온다. */
const warnBadge = (cx, cy) => el('g', {}, [
  el('circle', { cx, cy, r: 4, fill: 'none', stroke: '#ff4d6a', 'stroke-width': 1.6, class: 'warnring' }),
  el('circle', { cx, cy, r: 7, fill: '#ff4d6a', stroke: '#02050a', 'stroke-width': 1.6 }),
  el('rect', { x: cx - .9, y: cy - 4, width: 1.8, height: 5, fill: '#fff' }),
  el('rect', { x: cx - .9, y: cy + 2, width: 1.8, height: 1.8, fill: '#fff' }),
]);

/** 존 영역 — 바닥에 깔리는 색 구역 + 모서리 브래킷(게임 UI 관용구). */
function zoneArea(zo, zx, zy, ZW, zf, count) {
  const g = el('g', { class: 'hit', on: { click: e => { e.stopPropagation(); openZone(zo.id); } } });
  g.appendChild(quad(zx, zy, zf + .012, ZW, B.zoneD, { fill: zo.color, opacity: .13 }));
  g.appendChild(quad(zx, zy, zf + .02, ZW, B.zoneD, { fill: 'none', stroke: zo.color,
    'stroke-width': 1.3, opacity: .5, 'stroke-dasharray': '9 6', class: 'flow' }));
  const L = 1.0;
  [[zx, zy, 1, 1], [zx + ZW, zy, -1, 1], [zx + ZW, zy + B.zoneD, -1, -1], [zx, zy + B.zoneD, 1, -1]]
    .forEach(([px, py, sx, sy]) => g.appendChild(el('polyline', {
      points: pts([iso(px + sx * L, py, zf + .03), iso(px, py, zf + .03), iso(px, py + sy * L, zf + .03)]),
      fill: 'none', stroke: zo.color, 'stroke-width': 2.4, 'stroke-linecap': 'round', class: 'glow' })));
  g.appendChild(prism(zx, zy + B.zoneD - .08, zf, ZW, .08, .28, zo.color));
  tipify(g, { title: `${zo.id} · ${zo.name}`, color: zo.color,
    sub: `${zo.cidr || '세그먼트 없음'}  ·  신뢰 ${zo.trust}${zo.isolated ? ' · 격리' : ''}`,
    rows: [['자산', `${count} 대`]], foot: zo.role });
  return g;
}

/** 게이트(PEP) — 존을 넘을 때 반드시 지나는 지점. 기둥 둘 + 빛나는 문. */
function gate(px, py, zf, via, label) {
  const g = el('g', { class: 'hit' });
  g.appendChild(prism(px, py, zf, .3, .3, 1.5, '#c8871a'));
  g.appendChild(prism(px, py + 1.2, zf, .3, .3, 1.5, '#c8871a'));
  const a = iso(px + .15, py + .15, zf + 1.5), b = iso(px + .15, py + 1.35, zf + 1.5);
  g.appendChild(el('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1],
    stroke: '#ffb020', 'stroke-width': 3, class: 'pulse' }));
  const [tx, ty] = iso(px + .15, py + .75, zf + 1.5);
  g.appendChild(el('polygon', { points: `${tx},${ty - 21} ${tx - 6.5},${ty - 9} ${tx + 6.5},${ty - 9}`,
    fill: '#ffb020', stroke: '#02050a', 'stroke-width': 1.4, class: 'glow' }));
  tipify(g, { title: `게이트 · ${via}`, color: '#ffb020', sub: label,
    foot: '존을 넘는 트래픽은 반드시 여기를 지난다. 우회로가 없다.' });
  return g;
}

/** 존 안의 장비 한 대. 색은 존, 밝기와 높이는 실측 사용률. */
function assetUnit(a, x, y, z) {
  const st = assetState(a.id), up = alive(a);
  const base = up ? zoneColor(a.zone) : '#ff4d6a';
  const col = up ? lerpHex('#16242f', base, .34 + st.util * .56) : '#7f1d2b';
  const h = .42 + st.util * .5;
  const g = el('g', { class: 'hit', on: { click: e => { e.stopPropagation(); openAsset(a.id); } } });
  g.appendChild(prism(x, y, z, .58, .5, h, col, { glow: true }));
  const [lx, ly] = iso(x + .29, y + .25, z + h);
  g.appendChild(el('circle', { cx: lx, cy: ly - 1.5, r: 2,
    fill: up ? (st.util > .7 ? '#ff4d6a' : st.util > .35 ? '#ffb020' : '#3ddc97') : '#ff4d6a',
    class: up ? 'led' : 'blink', style: `animation-duration:${1.4 + (a.id.length % 5) * .3}s` }));
  if (!up) g.appendChild(warnBadge(lx, ly - 20));
  tipify(g, {
    title: a.name, color: zoneColor(a.zone), sub: `${a.id}  ·  ${a.ip || ''}`,
    rows: [['존', `${a.zone}${a.logical_zone ? ` (권한 ${a.logical_zone})` : ''}`],
           ['위치', `${a.floor} · ${a.rack || '랙 외'}`],
           ['상태', up ? '가동' : '⚠ 정지'],
           ['사용률', `${(st.util * 100).toFixed(0)}%`],
           ['전력', `${st.kw.toFixed(2)} kW`]],
    bar: st.util, barColor: st.util > .7 ? '#ff4d6a' : '#2ee6ff',
    foot: '클릭하면 접속 수단이 열립니다' });
  return g;
}

/** 랙 캐비닛. 앞면 LED 는 자산 하나가 한 줄 — 존 색이 세로로 섞여 보인다.
 *  한 랙에 여러 존이 섞여 있다는 사실이 여기서 눈에 들어와야 한다. */
function rackCabinet(rack, x, y, z) {
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === rack.id);
  const aisle = ST?.aisles?.[rack.aisle];
  const kw = list.reduce((s, a) => s + assetState(a.id).kw, 0);
  const over = kw > rack.design_kw;
  const body = aisle ? lerpHex('#1a2836', tempColor(aisle.temp_c), .26) : '#1a2836';
  const w = 2.2, d = 1.2, h = 2.4;
  const g = el('g', { class: 'hit', on: { click: e => { e.stopPropagation(); openRack(rack.id); } } });
  g.appendChild(prism(x, y, z, w, d, h, body, { glow: true }));
  g.appendChild(el('polygon', {                      // 앞면 도어 프레임
    points: pts([[x + .16, y + d, z + h - .12], [x + w - .16, y + d, z + h - .12],
                 [x + w - .16, y + d, z + .14], [x + .16, y + d, z + .14]].map(p => iso(...p))),
    fill: 'rgba(0,0,0,.3)', stroke: 'rgba(150,190,225,.22)', 'stroke-width': .9 }));
  list.forEach((a, i) => {
    const t = z + h - .32 - i * .24;
    if (t <= z + .22) return;
    const st = assetState(a.id), up = alive(a);
    const p1 = iso(x + .32, y + d, t), p2 = iso(x + w - .32, y + d, t);
    g.appendChild(el('line', { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1],
      stroke: up ? zoneColor(a.zone) : '#ff4d6a', 'stroke-width': 2.8, 'stroke-linecap': 'round',
      opacity: up ? .32 + st.util * .68 : 1, class: up ? null : 'blink' }));
  });
  const top = iso(x + w / 2, y + d / 2, z + h);
  if (over) g.appendChild(warnBadge(top[0], top[1] - 24));
  tipify(g, { title: rack.id, color: aisle ? tempColor(aisle.temp_c) : '#2ee6ff',
    sub: `${rack.floor} · ${rack.aisle} 아일 · ${rack.u}U`,
    rows: [['부하', `${kw.toFixed(2)} / ${rack.design_kw} kW`], ['탑재', `${list.length} 대`],
           ['섞인 존', [...new Set(list.map(a => a.zone))].join(' · ')],
           ...(aisle ? [['아일 온도', `${aisle.temp_c} °C`]] : [])],
    bar: kw / rack.design_kw, barColor: over ? '#ff4d6a' : '#3ddc97',
    foot: '물리적으로 한 랙인데 논리적으로는 여러 존이다' });
  return g;
}

const FAC = {
  utility:   { c: '#94a3b8', w: 1.2, d: 1.5, h: 1.1, n: '수전설비' },
  generator: { c: '#f59e0b', w: 1.2, d: 1.6, h: 1.0, n: '비상 발전기' },
  ups:       { c: '#3ddc97', w: 1.2, d: 1.6, h: 1.2, n: '무정전 전원장치' },
  chiller:   { c: '#38bdf8', w: 1.2, d: 1.6, h: 1.1, n: '냉동기' },
  crac:      { c: '#0ea5e9', w: 1.1, d: 1.5, h: 1.5, n: '항온항습기' },
  pdu:       { c: '#ffb020', w: .45, d: .45, h: 1.0, n: 'PDU' },
  fire:      { c: '#ef4444', w: .5,  d: .5,  h: .55, n: '소방 설비' },
  door:      { c: '#94a3b8', w: .22, d: 1.2, h: 1.3, n: '출입문' },
  cctv:      { c: '#7dd3fc', w: .4,  d: .4,  h: .5,  n: 'CCTV' },
  facility:  { c: '#64748b', w: .5,  d: .5,  h: .6,  n: '설비' },
};

/** 설비 위에 얹는 픽셀 글리프. 이름표 없이 무엇인지 알아보게 하는 유일한 단서다. */
function facIcon(kind, cx, cy, ok) {
  const ink = ok ? 'rgba(4,9,15,.85)' : '#ffd9e0';
  const g = el('g', { fill: ink });
  const R = (x, y, w, h) => g.appendChild(el('rect', { x: cx + x, y: cy + y, width: w, height: h }));
  switch (kind) {
    case 'generator': g.appendChild(el('polygon', { points:
      `${cx + 1},${cy - 6} ${cx - 3},${cy + 1} ${cx - .5},${cy + 1} ${cx - 2},${cy + 6} ${cx + 3},${cy - 1} ${cx + .5},${cy - 1}` })); break;
    case 'ups': R(-5, -4, 9, 8); R(4, -2, 2, 4); R(-3.5, -2.5, 2, 5); R(-.6, -2.5, 2, 5); break;
    case 'utility': R(-4, -1, 8, 2); R(-2.5, -5, 1.6, 4); R(.9, -5, 1.6, 4); R(-1, 1, 2, 4); break;
    case 'chiller': [0, 60, 120].forEach(a => g.appendChild(el('rect',
      { x: cx - 6, y: cy - .8, width: 12, height: 1.6, transform: `rotate(${a} ${cx} ${cy})` }))); break;
    case 'crac':
      g.appendChild(el('circle', { cx, cy, r: 5.4, fill: 'none', stroke: ink, 'stroke-width': 1.4 }));
      [0, 120, 240].forEach(a => g.appendChild(el('rect',
        { x: cx - .8, y: cy - 4.6, width: 1.6, height: 4.6, transform: `rotate(${a} ${cx} ${cy})` }))); break;
    case 'pdu': [-3, 0, 3].forEach(dy => R(-2, dy - .7, 4, 1.4)); break;
    case 'fire': g.appendChild(el('circle', { cx, cy, r: 3.6, fill: 'none', stroke: ink,
      'stroke-width': 1.6 })); R(-.8, -6.4, 1.6, 2.6); break;
    case 'cctv': g.appendChild(el('circle', { cx, cy: cy - 1, r: 3 })); R(-.7, 2, 1.4, 3); break;
    case 'door': R(-3, -5, 6, 1.4); R(-3, -5, 1.3, 10); R(1.7, -5, 1.3, 10); break;
    default: g.appendChild(el('circle', { cx, cy, r: 3 }));
  }
  return g;
}

function facilityUnit(item, x, y, z) {
  const s = FAC[item.kind] || FAC.facility;
  const down = facilityDown(item);
  const g = el('g', { class: 'hit', on: { click: e => { e.stopPropagation(); openFacility(item); } } });
  g.appendChild(prism(x, y, z, s.w, s.d, s.h, down ? '#8c1f33' : s.c, { glow: true }));
  const [cx, cy] = iso(x + s.w / 2, y + s.d / 2, z + s.h);
  g.appendChild(facIcon(item.kind, cx, cy, !down));
  if (down) g.appendChild(warnBadge(cx, cy - 24));
  if (item.kind === 'crac' && !down) for (let i = 0; i < 3; i++) {
    const [ax, ay] = iso(x, y + .3 + i * .45, z + .5);
    g.appendChild(el('line', { x1: ax, y1: ay, x2: ax - 30, y2: ay + 5,
      stroke: '#38bdf8', 'stroke-width': 1.4, opacity: .5, class: 'flow' }));
  }
  const p = ST?.power;
  const rows = [['층', item.floor], ['상태', down ? '⚠ 이상' : '정상']];
  if (item.kind === 'ups' && p) rows.push(['충전', `${p.ups_charge_pct}%`], ['잔여', `${p.ups_runtime_min} 분`]);
  if (item.kind === 'generator' && p) rows.push(['상태', p.generator_failed ? '기동 실패'
    : p.generator_running ? '운전 중' : '대기'], ['기동 지연', `${item.start_delay_s}초`]);
  if (item.kind === 'pdu') rows.push(['부하', `${(p?.pdu?.[item.id] ?? 0).toFixed(2)} / ${item.capacity_kw} kW`]);
  if (item.kind === 'crac') rows.push(['담당 아일', item.aisle],
    ['출력', `${ST?.aisles?.[item.aisle]?.cooling_kw ?? 0} kW`]);
  tipify(g, { title: item.name || s.n, color: down ? '#ff4d6a' : s.c,
    sub: `${item.id} · 시설 계통(ot)`, rows,
    foot: '시설은 가상이지만 계산에 쓰는 발열은 실측이다' });
  return g;
}

const RT_COLOR = { bastion: '#2ee6ff', hermes: '#a78bfa', claude: '#ffb020' };
const AU_COLOR = { L3: '#ff4d6a', approver: '#3ddc97', L2: '#38bdf8', L1: '#5b7185' };

/** 근무자 — 픽셀 인형. 모자 색이 런타임, 조끼와 발밑 링이 자율등급. */
function crewFigure(w, x, y, z, i) {
  const [cx, cy] = iso(x, y, z);
  const rt = RT_COLOR[w.runtime] || '#94a3b8', vest = AU_COLOR[w.autonomy] || '#5b7185';
  const outer = el('g', { class: 'hit', transform: `translate(${cx},${cy})`,
    on: { click: e => { e.stopPropagation(); openCrew(w.id); } } });
  outer.appendChild(el('ellipse', { cx: 0, cy: 1, rx: 10, ry: 3.6, fill: 'none',
    stroke: vest, 'stroke-width': 1.4, opacity: .55 }));
  outer.appendChild(el('ellipse', { cx: 0, cy: 2, rx: 7, ry: 2.4, fill: 'rgba(0,0,0,.5)' }));
  const b = el('g', { class: 'bob px', style: `animation-delay:${(i % 5) * .35}s` });
  const R = (x2, y2, w2, h2, f) => b.appendChild(el('rect',
    { x: x2, y: y2, width: w2, height: h2, fill: f }));
  R(-6, -31, 12, 3, '#02050a');
  R(-5, -35, 10, 5, rt);
  R(-4.5, -28, 9, 7, '#e8cfae');
  R(-2.5, -26, 2, 2, '#141d2b'); R(1, -26, 2, 2, '#141d2b');
  R(-6.5, -21, 13, 13, '#0f1a27');
  R(-5.5, -20, 11, 11, vest);
  R(-5.5, -17, 11, 2, 'rgba(255,255,255,.45)');
  R(-5.5, -6, 4.5, 6, '#141d2b'); R(1, -6, 4.5, 6, '#141d2b');
  outer.appendChild(b);
  tipify(outer, { title: w.name, color: rt, sub: `${w.id} · ${w.floor} · ${w.zone} 존`,
    rows: [['런타임', w.runtime], ['자율등급', w.autonomy],
           ['루프', (w.loops || []).length ? `${w.loops.length}개` : '없음']],
    foot: { L1: '보고 전용 — 상태를 바꾸지 않는다',
            L2: '승인 후 실행 — 운영 리드의 판정이 있어야 움직인다',
            L3: '무인 실행 — 런북이 등록된 작업에만 허용',
            approver: '승인 전담 — 스스로 실행하지 않는다' }[w.autonomy] });
  return outer;
}

/* ══ 층 그리기 ══════════════════════════════════════════════════ */
function facilitySlot(kind, i) {
  switch (kind) {
    case 'utility':   return [2.6, 2.2];                   // 1F 는 이 구역이 곧 기계실이다
    case 'generator': return [4.9, 2.2];
    case 'ups':       return [7.2, 2.2];
    case 'chiller':   return [9.5, 2.2];
    case 'crac':      return [GW - 1.9, 0.35 + i * 1.8];   // 뒤쪽 벽
    case 'cctv':      return [GW - 0.9, 2.6];
    case 'pdu':       return [2.6 + i * 3.2, B.pduY];
    case 'fire':      return [GW - 1.2, GD - 1.2];
    case 'door':      return [0.06, GD - 2.6];
    default:          return [GW - 0.9, 4.2 + i * .9];
  }
}

function drawFloorContent(fid, detail) {
  const g = el('g');
  const temp = floorTemp(fid), heat = temp == null ? null : tempColor(temp);
  const zones = zonesOf(fid), assets = assetsOf(fid), zf = .24;

  g.appendChild(prism(0, 0, 0, GW, GD, .24, temp == null ? '#0e1a27' : lerpHex('#0e1a27', heat, .18)));
  const grid = el('g', { opacity: .12, stroke: '#2ee6ff', 'stroke-width': .5, fill: 'none' });
  for (let x = 0; x <= GW; x += 2) grid.appendChild(el('line',
    { x1: iso(x, 0, zf)[0], y1: iso(x, 0, zf)[1], x2: iso(x, GD, zf)[0], y2: iso(x, GD, zf)[1] }));
  for (let y = 0; y <= GD; y += 2) grid.appendChild(el('line',
    { x1: iso(0, y, zf)[0], y1: iso(0, y, zf)[1], x2: iso(GW, y, zf)[0], y2: iso(GW, y, zf)[1] }));
  g.appendChild(grid);

  g.appendChild(prism(0, 0, zf, GW, .14, .62, '#16263a'));   // 뒷벽 두 면
  g.appendChild(prism(0, 0, zf, .14, GD, .62, '#16263a'));
  g.appendChild(prism(.5, .4, zf, 1.1, 1.1, 1.5, '#0f766e'));  // 수직 코어

  // ── 존 영역 ──
  const n = zones.length;
  const ZW = n >= 4 ? 2.9 : n === 3 ? 4.0 : n === 2 ? 5.9 : 8.7;
  const step = ZW + .42;
  const rect = {};
  zones.forEach((zo, i) => {
    if (zo.logical) return;
    const zx = 2.4 + i * step, zy = B.zoneY + i * B.drift;
    rect[zo.id] = { x: zx, y: zy, w: ZW, d: B.zoneD };
    const list = assets.filter(a => a.zone === zo.id);
    g.appendChild(zoneArea(zo, zx, zy, ZW, zf, list.length));
    const cols = Math.max(2, Math.floor((ZW - .5) / .72));
    list.forEach((a, k) => g.appendChild(assetUnit(a,
      zx + .34 + (k % cols) * .72, zy + .5 + Math.floor(k / cols) * .78, zf)));
    if (detail) {
      const [sx, sy] = iso(zx + ZW / 2, zy, zf);
      pill(sx, sy - 14, `${zo.id} · ${zo.name}`, { color: zo.color, anchor: 'mid', size: 12.5,
        sub: `${zo.cidr || '세그먼트 없음'}  sec:${zo.trust}  ·  자산 ${list.length}` });
    }
  });

  // ── 게이트(PEP) ──
  for (const c of (LAYOUT?.zone_chain || [])) {
    const A = rect[c.from], Z = rect[c.to];
    if (!A || !Z) continue;
    g.appendChild(gate((A.x + A.w + Z.x) / 2 - .15, (A.y + Z.y) / 2 + .9, zf, c.via, c.label));
  }

  // ── 논리 존 ──
  const logical = zones.find(z => z.logical);
  if (logical) {
    const own = assets.filter(a => a.logical_zone === logical.id);
    if (own.length) {
      const bx = 2.0, by = B.zoneY - .55, bw = (n - 1) * step + ZW + .8, bd = B.zoneD + 1.1;
      g.appendChild(quad(bx, by, zf + .035, bw, bd, { fill: 'none', stroke: logical.color,
        'stroke-width': 1.6, 'stroke-dasharray': '3 7', opacity: .85 }));
      if (detail) {
        const [sx, sy] = iso(bx, by + bd, zf);
        pill(sx, sy + 34, `${logical.id} · ${logical.name}`,
          { sub: `망 경계가 아니다 — 자산 ${own.length}`, color: logical.color, size: 11 });
      }
    }
  }

  g.appendChild(quad(.8, B.walkY, zf + .01, GW - 1.6, .5, { fill: '#2ee6ff', opacity: .05 }));

  // 여기부터는 부피가 있는 물건이다. 그리는 순서가 곧 앞뒤이므로 깊이(x+y)순으로
  // 모아 한 번에 붙인다 — 섹션별로 그리면 뒤에 있어야 할 것이 앞을 덮는다.
  const objs = [];
  const put = (x, y, node) => objs.push({ d: x + y, node });

  // ── 핫/콜드 아일 + 랙 ──
  const racks = racksOf(fid);
  if (racks.length) {
    const a = ST?.aisles?.[racks[0].aisle];
    const x0 = 2.2, xw = Math.max(7, racks.length * 3.2 + .8);
    g.appendChild(quad(x0, B.coldY, zf + .01, xw, .5,
      { fill: '#38bdf8', opacity: a && a.cooling_kw > 0 ? .2 : .05 }));
    g.appendChild(quad(x0, B.hotY, zf + .01, xw, .5,
      { fill: a ? tempColor(a.temp_c + 7) : '#7f1d2b', opacity: .24 }));
    const tray = el('g', { opacity: .45 });
    const t1 = iso(x0, B.rackY + .6, zf + 3.1), t2 = iso(x0 + xw, B.rackY + .6, zf + 3.1);
    tray.appendChild(el('line', { x1: t1[0], y1: t1[1], x2: t2[0], y2: t2[1],
      stroke: '#3d5470', 'stroke-width': 4, 'stroke-linecap': 'round' }));
    for (let t = 0; t <= xw; t += .8) {
      const c1 = iso(x0 + t, B.rackY + .6, zf + 3.1);
      tray.appendChild(el('line', { x1: c1[0], y1: c1[1] - 3, x2: c1[0], y2: c1[1] + 3,
        stroke: '#26364c', 'stroke-width': 1.1 }));
    }
    g.appendChild(tray);
    if (detail && a) {
      const [sx, sy] = iso(x0, B.hotY + .5, zf);
      pill(sx, sy + 30, `${a.aisle} 아일 · ${a.temp_c}°C`, { color: tempColor(a.temp_c), size: 11,
        sub: `발열 ${a.it_kw}kW / 냉방 ${a.cooling_kw}kW · ${a.humidity_pct}%RH` });
    }
  }
  racks.forEach((r, i) => {
    const x = 2.4 + i * 3.2;
    put(x + 1.1, B.rackY + .6, rackCabinet(r, x, B.rackY, zf));
    if (detail) {
      const [sx, sy] = iso(x + 1.1, B.rackY, zf + 2.4);
      pill(sx, sy - 8, r.id, { anchor: 'mid', size: 10.5, color: '#7b93ad',
        sub: `${(LAYOUT.it_assets.filter(a => a.rack === r.id)
          .reduce((s, a) => s + assetState(a.id).kw, 0)).toFixed(1)} / ${r.design_kw} kW` });
    }
  });

  // ── 시설 ──
  const fac = facilityOf(fid), seen = {};
  fac.forEach(item => {
    const k = item.kind, idx = (seen[k] = (seen[k] ?? -1) + 1);
    const [fx, fy] = facilitySlot(k, idx), s = FAC[k] || FAC.facility;
    put(fx + s.w / 2, fy + s.d / 2, facilityUnit(item, fx, fy, zf));
  });

  // ── 배전 모선 (1F) ──
  if (fac.some(i => i.kind === 'utility') && fac.some(i => i.kind === 'ups')) {
    const p = ST?.power;
    const bc = !p ? '#4c6480'
      : !p.utility_ok ? (p.generator_running ? '#f59e0b' : '#ff4d6a') : '#3ddc97';
    g.appendChild(el('polyline', {
      points: pts([[2.4, 3.9], [10.9, 3.9], [10.9, 3.62], [1.6, 3.62], [1.6, 1.2]]
        .map(([px, py]) => iso(px, py, zf + .06))),
      fill: 'none', stroke: bc, 'stroke-width': 2.4, opacity: .6, 'stroke-linejoin': 'round',
      class: p?.on_battery ? 'flow' : null }));
  }

  // ── 근무자 ──
  crewOf(fid).forEach((w, i, arr) => {
    const cx = 2.6 + i * Math.min(2.4, (GW - 5.5) / Math.max(arr.length, 1));
    put(cx, B.crewY - .3, prism(cx - .55, B.crewY - .45, zf, 1.25, .62, .36, '#7c5c3a'));
    put(cx, B.crewY + .5, crewFigure(w, cx, B.crewY + .5, zf, i));
    if (detail) {
      const [sx, sy] = iso(cx, B.crewY + .5, zf);
      pill(sx, sy + 38, w.name, { anchor: 'mid', size: 10.5, color: RT_COLOR[w.runtime] || '#7b93ad' });
    }
  });

  objs.sort((a, b) => a.d - b.d).forEach(o => g.appendChild(o.node));
  return g;
}

/* ══ 장면 ══════════════════════════════════════════════════════ */
function drawBuilding() {
  const svg = $('#scene');
  svg.replaceChildren(); LBL = []; TIPS.clear(); tipSeq = 0;
  const root = el('g'); svg.appendChild(root);

  floors().forEach((f, i) => root.appendChild(el('g', {
    transform: `translate(${i * STAGGER.dx},${-i * STAGGER.dy})`,
    class: 'hit', on: { click: () => enterFloor(f.id) } }, [drawFloorContent(f.id, false)])));

  // 라이저 — 층 사이를 잇는 배전·통신 통로. 엇갈려 쌓았으니 연결선으로 그린다.
  const riser = el('g', { opacity: .4 });
  for (let i = 0; i + 1 < floors().length; i++)
    [[.6, .5], [1.6, .5], [1.6, 1.5], [.6, 1.5]].forEach(([rx, ry]) => {
      const a = iso(rx, ry, 1.74), b = iso(rx, ry, .24);
      riser.appendChild(el('line', {
        x1: a[0] + i * STAGGER.dx, y1: a[1] - i * STAGGER.dy,
        x2: b[0] + (i + 1) * STAGGER.dx, y2: b[1] - (i + 1) * STAGGER.dy,
        stroke: '#0f766e', 'stroke-width': 1.6 }));
    });
  root.appendChild(riser);

  // 외부 회선 — 공격자는 건물 밖에서 들어온다
  const i2 = floors().findIndex(f => f.id === '2F');
  if (i2 >= 0) {
    const e = iso(0, GD, .8);
    const px = e[0] + i2 * STAGGER.dx - 30, py = e[1] - i2 * STAGGER.dy + 4;
    root.appendChild(el('line', { x1: px - 78, y1: py + 28, x2: px, y2: py,
      stroke: '#ff4d6a', 'stroke-width': 1.5, opacity: .55, class: 'flow' }));
    root.appendChild(tipify(el('g', { class: 'hit' }, [
      el('circle', { cx: px - 84, cy: py + 30, r: 12, fill: 'none', stroke: '#ff4d6a',
        'stroke-width': 1.4, class: 'warnring' }),
      el('circle', { cx: px - 84, cy: py + 30, r: 6.5, fill: '#ff4d6a', stroke: '#02050a',
        'stroke-width': 2 })]),
      { title: '외부 / 인터넷', color: '#ff4d6a',
        foot: '여기서 들어온 트래픽은 fw → ips → web 을 지나야만 안으로 들어간다' }));
    pill(px - 84, py + 20, '외부 / 인터넷', { color: '#ff4d6a', size: 11, anchor: 'mid' });
  }

  // WireGuard 터널 — DGX Spark 는 건물 밖 실물이다
  const i3 = floors().findIndex(f => f.id === '3F');
  const dgx = (LAYOUT?.it_assets || []).find(a => a.remote);
  if (i3 >= 0 && dgx) {
    const a0 = iso(GW, 0, 1.6);
    const ax = a0[0] + i3 * STAGGER.dx, ay = a0[1] - i3 * STAGGER.dy;
    const bx = ax + 132, by = ay - 46;
    const live = alive(dgx), st = assetState(dgx.id);
    root.appendChild(el('path', {
      d: `M${ax},${ay} C${ax + 62},${ay - 14} ${bx - 62},${by + 12} ${bx},${by}`,
      fill: 'none', stroke: live ? '#a78bfa' : '#4c6480', 'stroke-width': 1.8,
      class: live ? 'flow' : null, opacity: .9 }));
    const o = iso(.55, .55, 1);
    const node = el('g', { class: 'hit', transform: `translate(${bx - o[0]},${by - o[1]})`,
      on: { click: e => { e.stopPropagation(); openAsset(dgx.id); } } },
      [prism(0, 0, 0, 1.1, 1.1, 1.0, live ? '#8b5cf6' : '#3a4a63', { glow: true })]);
    tipify(node, { title: 'DGX Spark (GB10)', color: '#a78bfa',
      sub: `원격 실물 · WireGuard · ${dgx.ip}`,
      rows: [['상태', live ? '연결' : '두절'], ['사용률', `${(st.util * 100).toFixed(0)}%`],
             ['전력', `${st.kw.toFixed(2)} kW`]],
      bar: st.util, barColor: '#a78bfa',
      foot: '건물 밖 실물이지만 3F app 존의 정식 구성원이다' });
    root.appendChild(node);
    pill(bx, by - 26, 'DGX Spark', { sub: `${dgx.ip} · ${live ? '연결' : '두절'}`,
      color: '#a78bfa', size: 11, anchor: 'mid' });
  }

  finish(svg, root);
}

function drawFloor(fid) {
  const svg = $('#scene');
  svg.replaceChildren(); LBL = []; TIPS.clear(); tipSeq = 0;
  const root = el('g'); svg.appendChild(root);
  root.appendChild(drawFloorContent(fid, true));
  finish(svg, root);
}

function finish(svg, root) {
  const b = root.getBBox(), pad = 44;          // 장면만의 경계 — 라벨은 아직 없다
  BASE_VB = [b.x - pad, b.y - pad, b.width + pad * 2, b.height + pad * 2];
  const vb = curVB(), k = Math.min(svg.clientWidth / vb[2], svg.clientHeight / vb[3]);
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
  if (VIEW.mode === 'floor' && VIEW.floor) drawFloor(VIEW.floor); else drawBuilding();
  renderLift();
  refreshTip();
}
function enterFloor(fid) {
  VIEW = { ...VIEW, mode: 'floor', floor: fid, zoom: 1, panx: 0, pany: 0 };
  selectTab('zone'); render(); renderZonePane(); renderCrew();
}
function enterBuilding() {
  VIEW = { ...VIEW, mode: 'building', floor: null, zoom: 1, panx: 0, pany: 0 };
  render(); renderZonePane(); renderCrew();
}

/* ══ 엘리베이터 패널 ════════════════════════════════════════════ */
function renderLift() {
  const box = $('#lift');
  box.innerHTML = `<div class="lift-h">층 이동</div>` + floors().slice().reverse().map(f => {
    const t = floorTemp(f.id), al = floorAlarms(f.id);
    const crit = al.some(a => a.level >= 12);
    const kw = assetsOf(f.id).reduce((s, a) => s + assetState(a.id).kw, 0);
    const dead = assetsOf(f.id).filter(a => !alive(a)).length;
    return `<button class="${VIEW.floor === f.id ? 'on' : ''}" data-f="${f.id}">
      <span class="fl">${f.id}</span>
      <span class="fm"><span class="fn">${f.name}</span>
        <span class="fs">${t == null ? '센서 없음' : t.toFixed(1) + '°C'} · ${kw.toFixed(1)}kW · ${
          zonesOf(f.id).map(z => z.id).join('/')}</span></span>
      <span class="fd ${crit || dead ? 'crit' : al.length ? 'warn' : ''}"></span></button>`;
  }).join('') + `<button class="all ${VIEW.mode === 'building' ? 'on' : ''}" data-f="">건물 전체 보기</button>`;
  $$('[data-f]', box).forEach(b => b.onclick = () =>
    b.dataset.f ? enterFloor(b.dataset.f) : enterBuilding());
}

/* ══ HUD 게이지 ═════════════════════════════════════════════════ */
function renderGauges() {
  if (!ST) return;
  const p = ST.power;
  const temps = Object.values(ST.aisles || {});
  const hot = temps.length ? Math.max(...temps.map(a => a.temp_c)) : null;
  const alarms = ST.alarms || [], crit = alarms.filter(a => a.level >= 12).length;
  const assets = LAYOUT?.it_assets || [], up = assets.filter(alive).length;
  const ws = ROSTER.workers || [];
  const G = (k, v, s, cls, fill) => `<div class="gauge ${cls || ''}">
    <span class="g-k">${k}</span><span class="g-v">${v}</span><span class="g-s">${s}</span>
    ${fill != null ? `<i class="g-bar" style="width:${Math.min(fill * 100, 100)}%"></i>` : ''}</div>`;
  const loadR = p.total_kw / p.rated_kw;
  $('#gauges').innerHTML =
    G('전력', `${p.total_kw.toFixed(1)}kW`, `정격 ${p.rated_kw}kW · 실측 ${p.measured_kw}kW`,
      loadR > .9 ? 'crit' : loadR > .75 ? 'warn' : '', loadR)
  + G('UPS', p.on_battery ? `${p.ups_runtime_min}분` : `${p.ups_charge_pct}%`,
      p.on_battery ? `배터리 · -${p.drain_pct_per_min}%/분`
        : p.generator_running ? '발전기 운전' : '상용전원',
      p.on_battery ? 'crit' : p.generator_running ? 'warn' : '', p.ups_charge_pct / 100)
  + G('최고 온도', hot == null ? '—' : `${hot.toFixed(1)}°C`, 'ASHRAE 18~27°C',
      hot > 32 ? 'crit' : hot > 27 ? 'warn' : '', hot == null ? 0 : (hot - 16) / 26)
  + G('경보', String(alarms.length), crit ? `L12 이상 ${crit}건` : '심각 없음',
      crit ? 'crit' : alarms.length ? 'warn' : '')
  + G('가동 자산', `${up}/${assets.length}`, '컨테이너·원격 실측',
      up < assets.length ? 'warn' : '', up / Math.max(assets.length, 1))
  + G('근무자', String(ws.length), `L3 ${ws.filter(w => w.autonomy === 'L3').length}`
      + ` · 승인자 ${ws.filter(w => w.autonomy === 'approver').length}`);
  $('#bld-name').textContent = ST.building || 'kt66';
  const tb = $('#tsbadge'), ts = ST.time_scale ?? 1;
  tb.hidden = ts === 1; tb.textContent = `시간 ×${ts}`;
  $('#stage-body').classList.toggle('crit', crit > 0);
  const bn = $('#tab-alarm-n');
  bn.hidden = !alarms.length; bn.textContent = alarms.length;
}

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
      <span class="k">실측 소비(환산 전)</span><span class="v">${p.measured_kw} kW</span></div>
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
  const rt = RT_COLOR[w.runtime] || '#94a3b8', vest = AU_COLOR[w.autonomy] || '#5b7185';
  return `<svg class="por" viewBox="0 0 28 32" shape-rendering="crispEdges">
    <rect x="6" y="2" width="16" height="3" fill="#02050a"/>
    <rect x="7" y="5" width="14" height="5" fill="${rt}"/>
    <rect x="8" y="10" width="12" height="8" fill="#e8cfae"/>
    <rect x="11" y="13" width="2" height="2" fill="#141d2b"/><rect x="16" y="13" width="2" height="2" fill="#141d2b"/>
    <rect x="6" y="18" width="16" height="12" fill="${vest}"/>
    <rect x="6" y="21" width="16" height="2" fill="rgba(255,255,255,.45)"/></svg>`;
}
function renderCrew() {
  const pane = $('#pane-crew'), ws = ROSTER.workers || [];
  if (!ws.length) { pane.innerHTML = '<div class="empty">근무자 명단을 읽지 못했습니다</div>'; return; }
  const scope = VIEW.mode === 'floor' ? VIEW.floor : null;
  const groups = scope ? floors().filter(f => f.id === scope) : floors();
  pane.innerHTML = groups.map(f => {
    const list = ws.filter(w => w.floor === f.id);
    if (!list.length) return '';
    return `<div class="railhead">${f.id} ${f.name} · ${list.length}명</div>` + list.map(w => `
      <div class="crew" data-crew="${w.id}">${crewPortrait(w)}
        <div style="flex:1;min-width:0">
          <div class="nm">${w.name}</div><div class="sub">${w.id}</div>
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
function renderLegend() {
  $('#legend').innerHTML =
    `<div>${(LAYOUT?.zones || []).map(z => `<i class="sw" style="background:${z.color}"></i>`).join('')}
      <b>바닥 색</b> = 존(네트워크 보안등급) · 층은 물리 배치. 둘은 직교한다</div>
     <div><b>▲ 게이트</b> = 존을 넘을 때 반드시 지나는 지점 (${(LAYOUT?.zone_chain || [])
       .map(c => c.via).filter((v, i, a) => a.indexOf(v) === i).join(' · ')})</div>
     <div><b>점선 테두리</b> = 논리 존 — 권한 경계이지 망 경계가 아니다</div>
     <div><b>랙 LED</b> 색 = 존 · 밝기 = 실측 사용률 &nbsp;|&nbsp; <b>빨강 점멸 + !</b> = 정지·고장</div>
     <div><b>근무자</b> 모자 = 런타임 · 조끼와 발밑 링 = 자율등급</div>
     <div style="color:var(--dimmer)">무엇이든 <b>마우스를 올리면</b> 상세가 뜨고,
       <b>클릭하면</b> 접속 수단이 열립니다</div>`;
}

/* ══ 상세 패널 ══════════════════════════════════════════════════ */
function showDrawer(name, zoneId, html) {
  $('#dr-name').textContent = name;
  const z = $('#dr-zone');
  if (zoneId) { z.textContent = zoneId; z.style.color = zoneColor(zoneId); z.hidden = false; }
  else z.hidden = true;
  $('#dr-body').innerHTML = html;
  $('#drawer').hidden = false;
  $$('#dr-body .cmd').forEach(n => n.onclick = () => {
    navigator.clipboard?.writeText(n.textContent.replace(/^\$ /, ''));
    const t = n.textContent; n.textContent = '복사했습니다'; setTimeout(() => n.textContent = t, 900);
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
    ${kv('주소', a.ip)} ${kv('실체', a.container || (a.remote ? `원격 ${a.remote}` : '-'))}
    ${ct ? kv('컨테이너', ct.status) : ''}
    <div class="kv"><span class="k">실측 사용률</span><span class="v">${(st.util * 100).toFixed(0)}%</span></div>
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
      <b>${p.ups_runtime_min}분</b><small>현재 부하 유지 시</small></div>
    <div class="ups-stat"><label>총 부하</label><b>${p.total_kw.toFixed(1)}kW</b>
      <small>정격 ${p.rated_kw}kW</small></div>
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
    default: return ['*'];
  }
}
/* 주입 목록은 두 곳에서 온다 — 시설(OT) 10종은 envsim, IT 38종은 injector.
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
  const list = allInjections();
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

    <input class="inj-search" id="inj-q" placeholder="이름 · ID · 시나리오로 찾기 (예: FLT-03)" value="${INJQ}">

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
    await poll(); await refreshInj(); };
  $$('[data-dom]', body).forEach(b => b.onclick = () => { INJDOM = b.dataset.dom; renderInjector(); });
  const qi = $('#inj-q', body);
  qi.oninput = () => { INJQ = qi.value; renderInjector();
    const n = $('#inj-q'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
  $$('[data-clr1]', body).forEach(b => b.onclick = async () => {
    b.disabled = true; await clearOne(act[+b.dataset.clr1]); await poll(); await refreshInj(); });
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
    await poll(); await refreshInj();
  });
}

async function refreshInj() {
  try { INJACT = await get('/api/inj/active'); } catch { INJACT = { active: [] }; }
  if (!$('#inj-modal').hidden) renderInjector();
}

/* ══ 통신 ══════════════════════════════════════════════════════ */
async function get(url) {
  const r = await fetch(url); if (!r.ok) throw new Error(`${url} → ${r.status}`); return r.json();
}
async function post(url, params) {
  const q = new URLSearchParams(Object.fromEntries(
    Object.entries(params).map(([k, v]) => [k, String(v)])));
  const r = await fetch(`${url}?${q}`, { method: 'POST' });
  if (!r.ok) alert(`실패: ${(await r.json().catch(() => ({}))).detail || r.status}`);
  return r.json().catch(() => ({}));
}
async function poll() {
  try {
    const [st, ev] = await Promise.all([get('/api/state'), get('/api/events?limit=80')]);
    ST = st; EVENTS = ev.events || [];
    $('#link-status').classList.remove('down');
  } catch (e) {
    $('#link-status').classList.add('down'); $('#link-status').title = String(e); return;
  }
  const body = $('#tabbody'), keep = body.scrollTop;
  renderGauges(); renderPower(); renderAlarms(); renderLog(); renderTicker();
  renderZonePane(); renderCrew(); renderUps();
  body.scrollTop = keep;
  render();
  if (SELECTED) openAsset(SELECTED);
}
async function boot() {
  try {
    [LAYOUT, ROSTER, FAULTS] = await Promise.all([
      get('/api/layout'), get('/api/roster'), get('/api/faults')]);
    // IT 계통 38종은 별도 서비스(injector)에 있다. 없어도 시설 10종은 돌아가야 한다.
    try { INJCAT = await get('/api/inj/catalog'); }
    catch (e) { console.warn('injector 미가동 — 시설 주입만 제공', e); }
  } catch (e) {
    document.body.innerHTML = `<div class="empty" style="padding:60px">초기 데이터를 읽지 못했습니다 — ${e}</div>`;
    return;
  }
  renderLegend();
  await poll();
  setInterval(poll, 3000);
  setInterval(() => { if (!$('#inj-modal').hidden) refreshInj(); }, 3000);
  setInterval(() => $('#clock').textContent =
    new Date().toLocaleTimeString('ko-KR', { hour12: false }), 1000);
}

/* ══ 배선 ══════════════════════════════════════════════════════ */
$$('#tabs .tab').forEach(t => t.onclick = () => selectTab(t.dataset.tab));
$('#dr-close').onclick = () => { $('#drawer').hidden = true; SELECTED = null; };
$('#ups-close').onclick = () => { upsDismissed = true; $('#ups-modal').hidden = true; };
$('#btn-instructor').onclick = async () => { $('#inj-modal').hidden = false;
  renderInjector(); await refreshInj(); };
$('#inj-close').onclick = () => $('#inj-modal').hidden = true;
$('#btn-reset').onclick = async () => {
  await post('/api/reset', {});             // 시설 고장 + 부하 차단
  await post('/api/inj/clear_all', {});     // IT 계통 주입 전부 + 잔재 정리
  upsDismissed = false; await poll(); await refreshInj(); };
$('#legend-toggle').onclick = () => $('#legend').hidden = !$('#legend').hidden;

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
  if (!$('#drawer').hidden) { $('#drawer').hidden = true; SELECTED = null; }
  else if (!$('#inj-modal').hidden) $('#inj-modal').hidden = true;
  else if (VIEW.mode === 'floor') enterBuilding();
});
window.addEventListener('resize', render);

boot();
