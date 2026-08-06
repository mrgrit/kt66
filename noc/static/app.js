/* kt66 NOC — 관제 화면.
 *
 * 화면이 지켜야 하는 규칙 하나: **여기서 상태를 만들지 않는다.**
 * 전력·온도·경보는 전부 envsim 이 계산한 값을 그대로 그린다. 화면이 자기 나름대로
 * 보간하거나 예쁘게 다듬기 시작하면 학생이 보는 숫자와 SIEM 에 남는 숫자가 갈라진다.
 * 그 순간 이 화면은 교보재가 아니라 장식이 된다.
 *
 * 그리는 방식:
 *   층(floor)은 물리 배치다 — 아이소메트릭으로 4개를 쌓는다.
 *   존(zone)은 네트워크 보안등급이다 — **바닥에 깔린 색 영역**으로 그린다.
 *   둘은 직교한다. 한 랙의 장비들이 서로 다른 존 영역에 흩어져 그려지는 것이
 *   정상이고, 그 어긋남이 눈에 보이는 것이 이 화면의 목적이다.
 *   존 경계에는 PEP(fw/ips/web)를 세운다. 우회로가 없다는 사실을 형태로 보여준다.
 */
'use strict';

const SVGNS = 'http://www.w3.org/2000/svg';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let LAYOUT = null;      // /api/layout — 건물·존·자산 대장 (정적)
let ST = null;          // /api/state  — 전력·열·경보·컨테이너 (동적)
let ROSTER = { workers: [] };
let FAULTS = { available: {}, active: {} };
let EVENTS = [];
let VIEW = { mode: 'building', floor: null, zoom: 1, panx: 0, pany: 0 };
let BASE_VB = null;
let SELECTED = null;
let upsDismissed = false;

/* ── 좌표계 ──────────────────────────────────────────────────────── */
// 층 평면 격자 (가로, 세로).
// 층 간격은 겹침을 피하려면 층 깊이보다 커야 한다 — 간격만 줄이면 층이 다시 뒤엉킨다.
// 그래서 평면 자체를 줄였다(26x14 → 18x10). 각도는 그대로인데 건물 전체 높이가
// 1289px → 914px 로 줄어 한 화면에 원래 크기로 들어온다. 축소해서 볼 일이 없어졌다.
const GW = 18, GD = 10;
// XS:YS 가 시선의 각도다. 이 비가 크면 바닥을 눕혀 보는 것이고, 눕힐수록 안쪽 면이
// 안 보인다. 3:1 정도가 "데이터센터 바닥이 보이면서 건물도 4층으로 읽히는" 절충이다.
const XS = 24, YS = 8.0, ZS = 32;
/* 층을 수직으로만 쌓으면 층 간격이 층 깊이(224px)보다 커야 해서 건물이 세로로만
 * 길어진다. 그러면 전체를 보려고 축소하는 수밖에 없고, 가로는 400px 넘게 남는다.
 *
 * 그래서 **대각으로 엇갈려 쌓는다**(exploded axonometric). 남는 가로를 써서 세로를
 * 줄이는 것이다. 층 평면은 (XS,YS)와 (−XS,YS) 두 방향의 평행사변형이므로, 두 층이
 * 겹치지 않을 조건은 오프셋 (dx,−dy)를 그 두 방향으로 분해했을 때
 *     a = (dx/XS − dy/YS)/2,   b = (−dx/XS − dy/YS)/2
 * 중 |a| ≥ GW 또는 |b| ≥ GD 를 만족하는 것이다.
 * dx=150, dy=155 → b = −12.8, |b| ≥ GD(10) ✓ — 겹치지 않으면서 세로는 914→689px.
 */
const STAGGER = { dx: 150, dy: 155 };

const iso = (x, y, z) => [(x - y) * XS, (x + y) * YS - z * ZS];

/** 명패·외부회선·터널 배지는 투영된 네 모서리에서 자리를 잡는다. */
function edges(z) {
  const c = [[0, 0], [GW, 0], [GW, GD], [0, GD]].map(([x, y]) => iso(x, y, z));
  const xs = c.map(p => p[0]), ys = c.map(p => p[1]);
  return { left: Math.min(...xs), right: Math.max(...xs),
           mid: (Math.min(...ys) + Math.max(...ys)) / 2 };
}

// 층 안의 띠 — 뒤에서 앞으로: 존 영역 → 콜드아일 → 랙 열 → 핫아일 → 근무자.
// 실제 전산실이 이 순서로 되어 있다. 랙 앞뒤로 찬 공기와 더운 공기가 갈리는 것이
// 핫/콜드 아일이고, 1주차에 이 구획을 눈으로 익히는 것이 목표다.
const BAND = {
  zoneY: 0.8, zoneD: 3.6, drift: 0.40,
  coldY: 5.2, rackY: 5.8, hotY: 7.1, pduY: 7.9, crewY: 8.9,
};
const ZONE_ORDER = ['ext', 'pipe', 'dmz', 'int', 'app', 'ot', 'mgmt'];

/* ── 색 ──────────────────────────────────────────────────────────── */
const TEMP_STOPS = [[16, '#2563eb'], [22, '#22d3ee'], [27, '#fbbf24'],
                    [32, '#fb923c'], [38, '#ff4d6a'], [50, '#ff1f45']];

function lerpHex(a, b, t) {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [r1, g1, b1] = p(a), [r2, g2, b2] = p(b);
  const c = (x, y) => Math.round(x + (y - x) * t).toString(16).padStart(2, '0');
  return `#${c(r1, r2)}${c(g1, g2)}${c(b1, b2)}`;
}
function tempColor(t) {
  if (t == null) return '#334155';
  if (t <= TEMP_STOPS[0][0]) return TEMP_STOPS[0][1];
  for (let i = 1; i < TEMP_STOPS.length; i++) {
    const [v0, c0] = TEMP_STOPS[i - 1], [v1, c1] = TEMP_STOPS[i];
    if (t <= v1) return lerpHex(c0, c1, (t - v0) / (v1 - v0));
  }
  return TEMP_STOPS.at(-1)[1];
}
function shade(hex, f) {
  const p = i => Math.min(255, Math.round(parseInt(hex.slice(i, i + 2), 16) * f))
    .toString(16).padStart(2, '0');
  return `#${p(1)}${p(3)}${p(5)}`;
}
const zoneOf = id => (LAYOUT?.zones || []).find(z => z.id === id);
const zoneColor = id => zoneOf(id)?.color || '#64748b';

/* ── SVG 헬퍼 ───────────────────────────────────────────────────── */
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
const tip = t => el('title', { text: t });

/** 아이소메트릭 평면(바닥에 깔리는 사각형). */
function isoQuad(x, y, z, w, d, attrs) {
  return el('polygon', {
    points: pts([iso(x, y, z), iso(x + w, y, z), iso(x + w, y + d, z), iso(x, y + d, z)]),
    ...attrs,
  });
}

/** 아이소메트릭 직육면체. 보이는 면은 윗면 + x면 + y면 셋뿐이다. */
function isoBox(x, y, z, w, d, h, color, opts = {}) {
  const T = [iso(x, y, z + h), iso(x + w, y, z + h),
             iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  const st = opts.stroke || 'none';
  return el('g', { class: opts.cls }, [
    el('polygon', { points: pts([T[3], T[2], iso(x + w, y + d, z), iso(x, y + d, z)]),
                    fill: shade(color, .5), stroke: st, 'stroke-width': .4 }),
    el('polygon', { points: pts([T[1], T[2], iso(x + w, y + d, z), iso(x + w, y, z)]),
                    fill: shade(color, .72), stroke: st, 'stroke-width': .4 }),
    el('polygon', { points: pts(T), fill: color, stroke: st, 'stroke-width': .5,
                    class: opts.hl ? 'hl' : null }),
  ]);
}

/** 화면 좌표에 붙이는 라벨. 아이소메트릭 왜곡을 주지 않는다(읽혀야 하니까). */
function label(x, y, z, text, o = {}) {
  const [sx, sy] = iso(x, y, z);
  return el('text', {
    x: sx + (o.dx || 0), y: sy + (o.dy || 0), 'font-size': o.size || 9,
    fill: o.fill || '#8fa5bd', 'text-anchor': o.anchor || 'start',
    'font-weight': o.weight || 400, class: o.mono ? 'mono' : null, opacity: o.op,
    text,
  });
}

/* ── 데이터 조회 ────────────────────────────────────────────────── */
const floors = () => LAYOUT?.floors || [];
const racksOf = f => (LAYOUT?.racks || []).filter(r => r.floor === f);
const assetsOf = f => (LAYOUT?.it_assets || []).filter(a => a.floor === f);
const crewOf = f => (ROSTER.workers || []).filter(w => w.floor === f);
const assetState = id => ST?.assets?.[id] || { kw: 0, util: 0 };

/* 살아 있는가. 컨테이너는 docker 가 답하고, 터널 너머 원격은 사용률로 답한다 —
 * 도달하지 못하면 envsim 이 0.0 을 넣으므로 그것이 곧 "꺼졌다"는 신호다. */
const alive = a => a.container ? ST?.containers?.[a.container]?.state === 'running'
  : a.remote ? (ST?.assets?.[a.id]?.util ?? 0) > 0
  : true;

/** 그 층에 실재하는 존을 체인 순서로. 자산이 없는 논리 존도 포함한다. */
function zonesOf(fid) {
  const f = floors().find(x => x.id === fid);
  const ids = Array.isArray(f?.zone) ? f.zone : [f?.zone].filter(Boolean);
  return ids.filter(z => zoneOf(z))
    .sort((a, b) => ZONE_ORDER.indexOf(a) - ZONE_ORDER.indexOf(b))
    .map(z => zoneOf(z));
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

/* ════════════════════════════════════════════════════════════════
 *  층 하나를 그린다. 건물 뷰와 층 뷰가 같은 코드를 쓰고 detail 로만 갈린다 —
 *  두 벌을 두면 반드시 어긋나고, 어긋난 배치도는 교보재로 못 쓴다.
 * ════════════════════════════════════════════════════════════════ */
function drawFloorContent(fid, z, detail) {
  const g = el('g');
  const f = floors().find(x => x.id === fid);
  const temp = floorTemp(fid);
  const heat = temp == null ? null : tempColor(temp);
  const zones = zonesOf(fid);
  const assets = assetsOf(fid);

  // ── 슬래브 + 이중바닥 격자 ───────────────────────────────────
  const slabTop = temp == null ? '#101a26' : lerpHex('#101a26', heat, .22);
  g.appendChild(isoBox(0, 0, z, GW, GD, .22, slabTop,
    { stroke: 'rgba(34,211,238,.16)' }));

  const grid = el('g', { opacity: .13, stroke: '#22d3ee', 'stroke-width': .4, fill: 'none' });
  for (let x = 0; x <= GW; x += 2) grid.appendChild(el('line', {
    x1: iso(x, 0, z + .22)[0], y1: iso(x, 0, z + .22)[1],
    x2: iso(x, GD, z + .22)[0], y2: iso(x, GD, z + .22)[1] }));
  for (let y = 0; y <= GD; y += 2) grid.appendChild(el('line', {
    x1: iso(0, y, z + .22)[0], y1: iso(0, y, z + .22)[1],
    x2: iso(GW, y, z + .22)[0], y2: iso(GW, y, z + .22)[1] }));
  g.appendChild(grid);

  const zf = z + .22;                     // 바닥면 높이

  // ── 외벽 (낮은 파라펫) ──────────────────────────────────────
  const wall = 'rgba(60,86,116,.5)';
  g.appendChild(isoBox(0, 0, zf, GW, .18, .5, '#16222f', { stroke: wall }));
  g.appendChild(isoBox(0, 0, zf, .18, GD, .5, '#16222f', { stroke: wall }));

  // ── 수직 코어(계단·전기 샤프트) ─────────────────────────────
  g.appendChild(isoBox(0.5, 0.4, zf, 1.2, 1.2, 1.4, '#0f766e',
    { stroke: 'rgba(45,212,191,.5)' }));
  if (detail) g.appendChild(label(0.5, 1.6, zf, '샤프트', { size: 10, fill: '#2dd4bf', dy: 13 }));

  // ── 존 영역 — 이 화면의 뼈대다 ──────────────────────────────
  const n = zones.length;
  const ZW = n >= 4 ? 2.9 : n === 3 ? 4.0 : n === 2 ? 5.9 : 8.7;
  const step = ZW + .42;
  const zoneRect = {};                    // 존 id -> 화면 배치 (PEP·논리존이 참조)

  zones.forEach((zo, i) => {
    if (zo.logical) return;               // 논리 존은 바닥을 차지하지 않는다
    const zx = 2.4 + i * step, zy = BAND.zoneY + i * BAND.drift;
    zoneRect[zo.id] = { x: zx, y: zy, w: ZW, d: BAND.zoneD };

    const zg = el('g', {
      class: 'hit', on: { click: e => { e.stopPropagation(); openZone(zo.id); } } });
    // 바닥 색 + 테두리
    zg.appendChild(isoQuad(zx, zy, zf + .01, ZW, BAND.zoneD,
      { fill: zo.color, opacity: .13 }));
    zg.appendChild(isoQuad(zx, zy, zf + .02, ZW, BAND.zoneD,
      { fill: 'none', stroke: zo.color, 'stroke-width': 1.2, opacity: .75, class: 'hl' }));
    // 낮은 경계벽 — 존이 '방'처럼 보이게 한다
    zg.appendChild(isoBox(zx, zy + BAND.zoneD - .1, zf, ZW, .1, .34, zo.color,
      { stroke: 'none' }));

    // 라벨
    const zn2 = assets.filter(a => a.zone === zo.id).length;
    zg.appendChild(label(zx, zy, zf, `${zo.id} · ${zo.name}${zn2 ? `  (${zn2})` : ''}`,
      { size: detail ? 14 : 12.5, fill: zo.color, weight: 700, dy: -13 }));
    zg.appendChild(label(zx, zy, zf, `${zo.cidr || '세그먼트 없음'}  sec:${zo.trust}`,
      { size: detail ? 11 : 9.5, fill: 'rgba(200,214,230,.55)', mono: true, dy: -2 }));
    zg.appendChild(tip(`${zo.id} · ${zo.name}\n${zo.cidr || '세그먼트 없음'} · ${zo.trust}\n${zo.role}`));
    g.appendChild(zg);

    // 존 안의 자산 — 랙 단위가 아니라 존 단위로 놓는다(논리 배치)
    const list = assets.filter(a => a.zone === zo.id);
    const cols = Math.max(2, Math.floor((ZW - .5) / .70));
    list.forEach((a, k) => {
      const ax = zx + .32 + (k % cols) * .70;
      const ay = zy + .42 + Math.floor(k / cols) * .74;
      a._lblRow = k % 2;                       // 이름표를 한 칸씩 엇갈리게
      g.appendChild(assetUnit(a, ax, ay, zf, detail));
    });
  });

  // ── PEP — 존을 넘을 때 반드시 지나는 지점 ────────────────────
  for (const c of (LAYOUT?.zone_chain || [])) {
    const A = zoneRect[c.from], B = zoneRect[c.to];
    if (!A || !B) continue;
    const px = (A.x + A.w + B.x) / 2 - .18, py = (A.y + B.y) / 2 + 1.15;
    const pg = el('g');
    pg.appendChild(isoBox(px, py, zf, .36, .36, .8, '#f59e0b',
      { stroke: 'rgba(253,224,71,.7)' }));
    const [tx, ty] = iso(px + .18, py + .18, zf + .8);
    pg.appendChild(el('polygon', { points: `${tx},${ty - 13} ${tx - 5},${ty - 5} ${tx + 5},${ty - 5}`,
      fill: '#fbbf24', class: 'pulse' }));
    pg.appendChild(el('text', { x: tx + 8, y: ty - 5, 'font-size': detail ? 11.5 : 10,
      fill: '#fbbf24', class: 'mono', 'font-weight': 600, text: `PEP ${c.via}` }));
    pg.appendChild(tip(`${c.from} → ${c.to}\n경유: ${c.via}\n${c.label}`));
    g.appendChild(pg);
  }

  // ── 논리 존(mgmt) — 점선으로 자산들을 감싼다 ─────────────────
  const logical = zones.find(z2 => z2.logical);
  if (logical) {
    const own = assets.filter(a => a.logical_zone === logical.id);
    if (own.length) {
      const box = { x: 2.2, y: BAND.zoneY - .5, w: (n - 1) * step + ZW + .6, d: BAND.zoneD + 1.2 };
      g.appendChild(isoQuad(box.x, box.y, zf + .03, box.w, box.d, {
        fill: 'none', stroke: logical.color, 'stroke-width': 1.4,
        'stroke-dasharray': '7 6', opacity: .8 }));
      g.appendChild(label(box.x, box.y + box.d, zf,
        `${logical.id} · ${logical.name} — 망 경계가 아니다 (자산 ${own.length})`,
        { size: detail ? 11.5 : 10, fill: logical.color, dy: 15 }));
    }
  }

  // ── 주 통로 ─────────────────────────────────────────────────
  g.appendChild(isoQuad(0.7, 4.7, zf + .01, GW - 1.4, .4,
    { fill: '#22d3ee', opacity: .05 }));
  for (let t = 1.0; t < GW - 1.0; t += 1.5) {
    const [mx, my] = iso(t, 4.9, zf + .02);
    g.appendChild(el('line', { x1: mx - 9, y1: my, x2: mx + 9, y2: my + 1,
      stroke: '#22d3ee', 'stroke-width': 1.4, opacity: .16 }));
  }

  // ── 핫/콜드 아일 + 랙 열 (물리 배치) ─────────────────────────
  const racks = racksOf(fid);
  if (racks.length) {
    const aisleId = racks[0].aisle;
    const a = ST?.aisles?.[aisleId];
    const x0 = 2.2, xw = Math.max(7, racks.length * 3.2 + 1.0);
    // 콜드 아일 — CRAC 이 찬 공기를 밀어 넣는 쪽
    g.appendChild(isoQuad(x0, BAND.coldY, zf + .01, xw, .52,
      { fill: '#38bdf8', opacity: a && a.cooling_kw > 0 ? .18 : .05 }));
    // 핫 아일 — 장비가 뱉은 더운 공기가 모이는 쪽. 온도에 따라 붉어진다
    g.appendChild(isoQuad(x0, BAND.hotY, zf + .01, xw, .52,
      { fill: a ? tempColor(a.temp_c + 6) : '#7f1d2b', opacity: .22 }));
    if (detail) {
      g.appendChild(label(x0, BAND.coldY, zf, `콜드 아일 · CRAC 급기`,
        { size: 10, fill: '#38bdf8', dy: 12 }));
      g.appendChild(label(x0, BAND.hotY + .52, zf,
        a ? `핫 아일 ${aisleId} · ${a.temp_c}°C · 발열 ${a.it_kw}kW / 냉방 ${a.cooling_kw}kW`
          : `핫 아일 ${aisleId}`,
        { size: 10, fill: a ? tempColor(a.temp_c) : '#67809a', dy: 13 }));
    }
    // 케이블 트레이 — 랙 열 위를 가로지른다. 전산실이라는 신호다
    const tray = el('g', { opacity: .5 });
    const [t1x, t1y] = iso(x0, BAND.rackY + .62, zf + 3.0);
    const [t2x, t2y] = iso(x0 + xw, BAND.rackY + .62, zf + 3.0);
    tray.appendChild(el('line', { x1: t1x, y1: t1y, x2: t2x, y2: t2y,
      stroke: '#475569', 'stroke-width': 3.5 }));
    for (let t = 0; t <= xw; t += .8) {
      const [cx, cy] = iso(x0 + t, BAND.rackY + .62, zf + 3.0);
      tray.appendChild(el('line', { x1: cx, y1: cy - 3, x2: cx, y2: cy + 3,
        stroke: '#334155', 'stroke-width': 1 }));
    }
    g.appendChild(tray);
  }
  // 여기부터는 부피가 있는 물건이다. 그리는 순서가 곧 앞뒤이므로 **깊이순으로 모아**
  // 한 번에 붙인다(x+y 가 클수록 앞). 섹션별로 그리면 뒤에 있어야 할 CRAC 이 앞의
  // 랙을 덮는 식으로 어긋난다 — 화면이 배치를 거짓말하게 된다.
  const objs = [];
  const put = (x, y, node) => objs.push({ d: x + y, node });

  racks.forEach((r, i) => put(2.4 + i * 3.2 + 1.1, BAND.rackY + .6,
    rackCabinet(r, 2.4 + i * 3.2, BAND.rackY, zf, detail)));

  // ── 시설 계통 ────────────────────────────────────────────────
  const fac = facilityOf(fid);
  const seen = {};
  fac.forEach(item => {
    const k = item.kind;
    const idx = (seen[k] = (seen[k] ?? -1) + 1);
    const [fx, fy] = facilitySlot(k, idx);
    const st = FAC_STYLE[k] || FAC_STYLE.facility;
    put(fx + st.w / 2, fy + st.d / 2, facilityUnit(item, fx, fy, zf, detail));
  });

  // ── 배전 모선 — 수전 → UPS → 라이저. 색이 곧 지금의 공급원이다 ──
  const hasUtil = fac.some(i => i.kind === 'utility');
  const hasUps = fac.some(i => i.kind === 'ups');
  if (hasUtil && hasUps) {
    const p = ST?.power;
    const bc = !p ? '#41566d' : !p.utility_ok
      ? (p.generator_running ? '#f59e0b' : '#ff4d6a') : '#3ddc97';
    const path = [[1.8, 1.1], [1.8, 2.8], [1.8, 4.6], [1.8, 6.4], [2.8, 6.4], [2.8, 1.4], [1.7, 1.0]];
    g.appendChild(el('polyline', {
      points: pts(path.map(([px, py]) => iso(px, py, zf + .06))),
      fill: 'none', stroke: bc, 'stroke-width': 2, opacity: .55,
      class: p?.on_battery ? 'flow' : null,
    }));
    if (detail) g.appendChild(label(2.9, 6.7, zf,
      p?.on_battery ? '배전 모선 — 배터리 급전' :
      p?.generator_running ? '배전 모선 — 발전기 급전' : '배전 모선 — 상용전원',
      { size: 10.5, fill: bc, mono: true }));
  }

  // ── 근무자 ───────────────────────────────────────────────────
  crewOf(fid).forEach((w, i, arr) => {
    const cx = 2.4 + i * Math.min(2.2, (GW - 5.5) / Math.max(arr.length, 1));
    if (detail) put(cx + .25, BAND.crewY + .25,
      isoBox(cx - .35, BAND.crewY - .07, zf, 1.2, .62, .38, '#7c5c3a',
        { stroke: 'rgba(180,140,90,.45)' }));   // 데스크
    put(cx + .25, BAND.crewY + .7, crewFigure(w, cx + .25, BAND.crewY + .7, zf, detail));
  });

  // 깊이순으로 붙인다 — 뒤에 있는 것부터
  objs.sort((a, b) => a.d - b.d).forEach(o => g.appendChild(o.node));

  // ── 층 명패 (건물 뷰 전용 — 층 뷰에서는 왼쪽 위 오버레이가 대신한다) ──
  if (detail) return g;
  const E = edges(zf);
  const plate = el('g', {
    class: 'hit', transform: `translate(${E.left - 232},${E.mid - 32})`,
    on: { click: e => { e.stopPropagation(); enterFloor(fid); } } });
  plate.appendChild(el('rect', { width: 218, height: 62, rx: 5,
    fill: 'rgba(8,13,20,.92)', stroke: 'rgba(34,211,238,.28)', class: 'hl' }));
  plate.appendChild(el('text', { x: 12, y: 21, 'font-size': 17, 'font-weight': 700,
    fill: '#22d3ee', class: 'mono', text: fid }));
  plate.appendChild(el('text', { x: 50, y: 21, 'font-size': 13.5, fill: '#c8d6e6', text: f.name }));
  plate.appendChild(el('text', { x: 12, y: 38, 'font-size': 11, class: 'mono',
    fill: '#67809a',
    text: `zone: ${zones.map(z2 => z2.id).join(' → ')}` }));
  plate.appendChild(el('text', { x: 12, y: 54, 'font-size': 11, class: 'mono',
    fill: temp == null ? '#41566d' : heat,
    text: temp == null ? `자산 ${assets.length} · 센서 없음`
      : `${temp.toFixed(1)}°C · ${(ST?.floors?.[fid]?.it_kw ?? 0).toFixed(1)}kW · 자산 ${assets.length}` }));
  plate.appendChild(tip(`${fid} ${f.name}\n${f.role}`));
  g.appendChild(plate);

  return g;
}

/** 시설 항목의 자리.
 *
 * 이 투영에서 화면 앞쪽은 x+y 가 큰 쪽이다. 부피가 큰 설비를 거기 두면 뒤에 있는
 * 랙·자산을 통째로 가린다 — 실제로 CRAC·발전기·UPS 가 그러고 있었다.
 * 그래서 큰 것은 전부 **벽면(뒤 또는 왼쪽)** 으로 붙이고, 앞줄에는 부피가 작은
 * 것만 남긴다(PDU·소화설비). 실제 전산실에서도 대형 설비는 벽을 따라 선다.
 */
function facilitySlot(kind, i) {
  switch (kind) {
    // 왼쪽 벽 — 화면에서 왼쪽으로 밀려나므로 가운데 랙 열을 가리지 않는다
    case 'utility':   return [0.55, 0.35];
    case 'generator': return [0.55, 2.15];
    case 'ups':       return [0.55, 3.95];
    case 'chiller':   return [0.55, 5.75];
    // 뒤쪽 벽 — 존 영역보다 뒤라 아무것도 가리지 않는다
    case 'crac':      return [GW - 1.6, 0.3 + i * 1.9];
    case 'cctv':      return [GW - 0.85, 0.2];
    // 앞줄에는 작은 것만
    case 'pdu':       return [2.5 + i * 3.2, BAND.pduY];
    case 'fire':      return [GW - 1.1, GD - 1.1];
    case 'door':      return [0.05, GD - 2.4];
    default:          return [GW - 0.85, 1.8 + i * .9];
  }
}

/** 존 영역 안의 장비 한 대 — 얇은 캐비닛. 색은 존, 밝기는 실측 사용률. */
function assetUnit(a, x, y, z, detail) {
  const st = assetState(a.id), up = alive(a);
  const base = up ? zoneColor(a.zone) : '#ff4d6a';
  const col = up ? lerpHex('#16222f', base, .3 + st.util * .6) : '#7f1d2b';
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openAsset(a.id); } } });
  g.appendChild(isoBox(x, y, z, .55, .48, .45 + st.util * .45, col,
    { stroke: up ? 'rgba(140,175,210,.35)' : '#ff4d6a', hl: true, cls: up ? null : 'blink' }));
  // 상태 LED
  const [lx, ly] = iso(x + .275, y + .24, z + .45 + st.util * .45);
  g.appendChild(el('circle', { cx: lx, cy: ly - 1, r: 1.7,
    fill: up ? (st.util > .7 ? '#ff4d6a' : st.util > .35 ? '#fbbf24' : '#3ddc97') : '#ff4d6a',
    class: up ? null : 'blink' }));
  if (detail) g.appendChild(label(x, y + .48, z, a.name,
    { size: 9.5, fill: up ? 'rgba(200,214,230,.8)' : '#ff9fb0',
      dy: 12 + (a._lblRow || 0) * 11 }));
  g.appendChild(tip(`${a.name} (${a.id})\n존 ${a.zone}`
    + (a.logical_zone ? ` · 권한 ${a.logical_zone}` : '')
    + `\n${a.rack || '랙 외'} · ${a.ip || ''}`
    + `\n${up ? '가동' : '⚠ 정지'} · 사용률 ${(st.util * 100).toFixed(0)}% · ${st.kw.toFixed(2)}kW`));
  return g;
}

/** 랙 캐비닛(물리). 앞면 LED 는 자산 하나가 한 줄 — 존 색이 세로로 섞여 보인다. */
function rackCabinet(rack, x, y, z, detail) {
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === rack.id);
  const aisle = ST?.aisles?.[rack.aisle];
  const kw = list.reduce((s, a) => s + assetState(a.id).kw, 0);
  const over = kw > rack.design_kw;
  const body = aisle ? lerpHex('#18242f', tempColor(aisle.temp_c), .3) : '#18242f';
  const w = 2.2, d = 1.2, h = 2.3;
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openRack(rack.id); } } });
  g.appendChild(isoBox(x, y, z, w, d, h, body,
    { stroke: over ? '#ff4d6a' : 'rgba(130,170,205,.4)', hl: true }));

  list.forEach((a, i) => {
    const t = z + h - .16 - i * .25;
    if (t <= z + .1) return;
    const st = assetState(a.id), up = alive(a);
    const [p1x, p1y] = iso(x + .11, y + d, t);
    const [p2x, p2y] = iso(x + w - .11, y + d, t);
    g.appendChild(el('line', { x1: p1x, y1: p1y, x2: p2x, y2: p2y,
      stroke: up ? zoneColor(a.zone) : '#ff4d6a', 'stroke-width': 2.6,
      opacity: up ? .3 + st.util * .7 : 1, class: up ? null : 'blink' }));
  });
  g.appendChild(label(x, y, z, `${rack.id}`,
    { size: detail ? 11 : 9.5, fill: '#8fa5bd', mono: true, dy: -9 }));
  if (detail) g.appendChild(label(x, y, z,
    `${kw.toFixed(1)}/${rack.design_kw}kW · ${rack.aisle}아일`,
    { size: 10, fill: over ? '#ff4d6a' : '#67809a', mono: true, dy: 1 }));
  g.appendChild(tip(`${rack.id} · ${rack.aisle} 아일 · ${rack.u}U\n`
    + `부하 ${kw.toFixed(2)} / 설계 ${rack.design_kw}kW`
    + (aisle ? `\n아일 ${aisle.temp_c}°C · 냉방 ${aisle.cooling_kw}kW` : '')));
  return g;
}

const FAC_STYLE = {
  utility:   { c: '#94a3b8', w: 1.1, d: 1.4, h: 1.0, t: '수전' },
  generator: { c: '#f59e0b', w: 1.1, d: 1.5, h: 0.95, t: 'GEN' },
  ups:       { c: '#3ddc97', w: 1.1, d: 1.5, h: 1.1, t: 'UPS' },
  pdu:       { c: '#fbbf24', w: .5,  d: .5,  h: .8,  t: 'P' },
  chiller:   { c: '#38bdf8', w: 1.1, d: 1.5, h: 1.0, t: '냉동기' },
  crac:      { c: '#0ea5e9', w: 1.1, d: 1.4, h: 1.3, t: 'CRAC' },
  fire:      { c: '#ef4444', w: .55, d: .55, h: .55, t: 'FM' },
  door:      { c: '#94a3b8', w: .2,  d: 1.1, h: 1.1, t: '' },
  cctv:      { c: '#7dd3fc', w: .45, d: .45, h: .6,  t: '' },
  facility:  { c: '#64748b', w: .55, d: .55, h: .7,  t: '' },
};

function facilityUnit(item, x, y, z, detail) {
  const s = FAC_STYLE[item.kind] || FAC_STYLE.facility;
  const down = facilityDown(item);
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openFacility(item); } } });
  g.appendChild(isoBox(x, y, z, s.w, s.d, s.h, down ? '#7f1d2b' : s.c,
    { stroke: down ? '#ff4d6a' : 'rgba(0,0,0,.4)', hl: true }));
  const [cx, cy] = iso(x + s.w / 2, y + s.d / 2, z + s.h);
  if (s.t) g.appendChild(el('text', { x: cx, y: cy + 3, 'font-size': s.w > 1.0 ? 9.5 : 8,
    'text-anchor': 'middle', fill: down ? '#ffd0d8' : 'rgba(5,10,16,.85)',
    'font-weight': 700, class: 'mono', text: s.t }));
  if (down) g.appendChild(el('circle', { cx, cy: cy - 16, r: 3.6, fill: '#ff4d6a', class: 'blink' }));

  // CRAC 급기 — 살아 있으면 찬 공기가 나간다
  if (item.kind === 'crac' && !down) {
    for (let i = 0; i < 3; i++) {
      const [ax, ay] = iso(x, y + .3 + i * .42, z + .45);
      g.appendChild(el('line', { x1: ax, y1: ay, x2: ax - 30, y2: ay + 5,
        stroke: '#38bdf8', 'stroke-width': 1.2, opacity: .45, class: 'flow' }));
    }
  }
  if (detail && s.w > .9) g.appendChild(label(x, y + s.d, z, item.name || item.id,
    { size: 10, fill: down ? '#ff9fb0' : '#67809a', dy: 13 }));
  g.appendChild(tip(`${item.name || item.id} (${item.id})${down ? '\n⚠ 이상' : ''}`));
  return g;
}

const RT_COLOR = { bastion: '#22d3ee', hermes: '#a78bfa', claude: '#fbbf24' };
const AU_COLOR = { L3: '#ff4d6a', approver: '#3ddc97', L2: '#38bdf8', L1: '#475569' };

/** 근무자 — 픽셀 인형. 모자 색이 런타임, 조끼 색이 자율등급이다. */
function crewFigure(w, x, y, z, detail) {
  const [cx, cy] = iso(x, y, z);
  const rt = RT_COLOR[w.runtime] || '#94a3b8';
  const vest = AU_COLOR[w.autonomy] || '#475569';
  const g = el('g', {
    class: 'hit px', transform: `translate(${cx},${cy})`,
    on: { click: e => { e.stopPropagation(); openCrew(w.id); } } });
  g.appendChild(el('ellipse', { cx: 0, cy: 2, rx: 7, ry: 2.8, fill: 'rgba(0,0,0,.45)' }));
  g.appendChild(el('rect', { x: -5, y: -27, width: 10, height: 6, fill: rt, class: 'hl' }));
  g.appendChild(el('rect', { x: -4, y: -21, width: 8, height: 6, fill: '#e6cbA8' }));
  g.appendChild(el('rect', { x: -4, y: -19, width: 2, height: 2, fill: '#1e293b' }));
  g.appendChild(el('rect', { x: 2, y: -19, width: 2, height: 2, fill: '#1e293b' }));
  g.appendChild(el('rect', { x: -6, y: -15, width: 12, height: 11, fill: vest }));
  g.appendChild(el('rect', { x: -6, y: -13, width: 12, height: 1.6, fill: 'rgba(255,255,255,.35)' }));
  g.appendChild(el('rect', { x: -5, y: -4, width: 4, height: 5, fill: '#1e293b' }));
  g.appendChild(el('rect', { x: 1, y: -4, width: 4, height: 5, fill: '#1e293b' }));
  g.appendChild(el('text', { x: 0, y: 15, 'text-anchor': 'middle',
    'font-size': detail ? 11 : 10, fill: '#8fa5bd', text: w.name }));
  g.appendChild(tip(`${w.name} (${w.id})\n${w.runtime} · ${w.autonomy} · ${w.zone} 존`
    + (w.loops?.length ? `\n루프: ${w.loops.join(', ')}` : '\n루프 없음')));
  return g;
}

/* ════════════════════════════════════════════════════════════════
 *  장면
 * ════════════════════════════════════════════════════════════════ */
function drawBuilding() {
  const svg = $('#scene');
  svg.replaceChildren();
  const root = el('g');
  svg.appendChild(root);

  // 아래층부터 — 위층이 나중에 그려져야 겹침이 자연스럽다
  floors().forEach((f, i) => root.appendChild(el('g',
    { transform: `translate(${i * STAGGER.dx},${-i * STAGGER.dy})` },
    [drawFloorContent(f.id, 0, false)])));

  // 라이저 — 배전·통신이 위층으로 올라가는 길. 엇갈려 쌓았으므로 층 사이를 잇는
  // 연결선으로 그린다. 층이 물리적으로 이어져 있다는 사실이 보여야 한다.
  const riser = el('g', { opacity: .4 });
  for (let i = 0; i + 1 < floors().length; i++) {
    [[0.6, 0.5], [1.7, 0.5], [1.7, 1.6], [0.6, 1.6]].forEach(([rx, ry]) => {
      const [ax, ay] = iso(rx, ry, 1.6);
      const [bx, by] = iso(rx, ry, .22);
      riser.appendChild(el('line', {
        x1: ax + i * STAGGER.dx,       y1: ay - i * STAGGER.dy,
        x2: bx + (i + 1) * STAGGER.dx, y2: by - (i + 1) * STAGGER.dy,
        stroke: '#0f766e', 'stroke-width': 1.5 }));
    });
  }
  root.appendChild(riser);

  // 외부 회선 — 공격자는 건물 밖에서 들어온다
  const i2 = floors().findIndex(f => f.id === '2F');
  if (i2 >= 0) {
    const E2 = edges(1.2);
    const ex = E2.left + i2 * STAGGER.dx, ey = E2.mid - 26 - i2 * STAGGER.dy;
    root.appendChild(el('circle', { cx: ex - 96, cy: ey, r: 5, fill: '#ff4d6a', class: 'pulse' }));
    root.appendChild(el('text', { x: ex - 87, y: ey + 4.5, 'font-size': 12,
      fill: '#ff4d6a', text: '외부 / 인터넷' }));
    root.appendChild(el('line', { x1: ex - 90, y1: ey, x2: ex + 24, y2: ey + 16,
      stroke: '#ff4d6a', 'stroke-width': 1.4, opacity: .5, class: 'flow' }));
  }

  // WireGuard 터널 — DGX Spark 는 건물 밖 실물이다. 감추지 않는다.
  const i3 = floors().findIndex(f => f.id === '3F');
  const dgx = (LAYOUT?.it_assets || []).find(a => a.remote);
  if (i3 >= 0 && dgx) {
    const z = 1.6;
    const E3 = edges(z);
    const ax = E3.right + i3 * STAGGER.dx, ay = E3.mid - i3 * STAGGER.dy;
    const bx = ax + 130, by = ay - 40;
    const live = alive(dgx);
    root.appendChild(el('path', {
      d: `M${ax},${ay} C${ax + 70},${ay - 10} ${bx - 70},${by + 8} ${bx},${by}`,
      fill: 'none', stroke: live ? '#a78bfa' : '#41566d', 'stroke-width': 1.5,
      class: live ? 'flow' : null, opacity: .9 }));
    const bg = el('g', { class: 'hit', transform: `translate(${bx},${by - 24})`,
      on: { click: e => { e.stopPropagation(); openAsset(dgx.id); } } });
    bg.appendChild(el('rect', { width: 168, height: 48, rx: 5, fill: 'rgba(8,13,20,.95)',
      stroke: live ? '#a78bfa' : '#2b3d55', class: 'hl' }));
    bg.appendChild(el('text', { x: 10, y: 18, 'font-size': 11.5, fill: '#a78bfa',
      text: 'DGX Spark (원격 · GB10)' }));
    bg.appendChild(el('text', { x: 10, y: 34, 'font-size': 9.5, class: 'mono', fill: '#67809a',
      text: `WireGuard · ${dgx.ip} · ${live ? '연결' : '두절'}` }));
    root.appendChild(bg);
  }

  finish(svg, root);
}

function drawFloor(fid) {
  const svg = $('#scene');
  svg.replaceChildren();
  const root = el('g');
  svg.appendChild(root);
  root.appendChild(drawFloorContent(fid, 0, true));
  finish(svg, root);
}

function finish(svg, root) {
  const b = root.getBBox(), pad = 46;
  BASE_VB = [b.x - pad, b.y - pad, b.width + pad * 2, b.height + pad * 2];
  applyVB();
}

function applyVB() {
  if (!BASE_VB) return;
  const [x, y, w, h] = BASE_VB, k = VIEW.zoom;
  const nw = w / k, nh = h / k;
  $('#scene').setAttribute('viewBox',
    `${x + (w - nw) / 2 + VIEW.panx} ${y + (h - nh) / 2 + VIEW.pany} ${nw} ${nh}`);
  $('#scene').setAttribute('preserveAspectRatio', 'xMidYMid meet');
}

function render() {
  if (!LAYOUT) return;
  if (VIEW.mode === 'floor' && VIEW.floor) drawFloor(VIEW.floor);
  else drawBuilding();
  renderCrumbs();
  renderFloorCard();
}

function enterFloor(fid) {
  VIEW = { ...VIEW, mode: 'floor', floor: fid, zoom: 1, panx: 0, pany: 0 };
  selectTab('zone');
  render(); renderZonePane();
}
function enterBuilding() {
  VIEW = { ...VIEW, mode: 'building', floor: null, zoom: 1, panx: 0, pany: 0 };
  render(); renderZonePane();
}
function renderCrumbs() {
  const c = $('#crumbs');
  c.replaceChildren();
  const mk = (txt, active, fn) => {
    const b = document.createElement('button');
    b.className = 'crumb' + (active ? ' active' : '');
    b.textContent = txt; b.onclick = fn; return b;
  };
  c.appendChild(mk('건물', VIEW.mode === 'building', enterBuilding));
  floors().forEach(f => c.appendChild(mk(f.id, VIEW.floor === f.id, () => enterFloor(f.id))));
}

function renderFloorCard() {
  const card = $('#floorcard');
  if (VIEW.mode !== 'floor') { card.hidden = true; return; }
  const fid = VIEW.floor;
  const f = floors().find(x => x.id === fid);
  const assets = assetsOf(fid);
  const up = assets.filter(alive).length;
  const kw = assets.reduce((s, a) => s + assetState(a.id).kw, 0);
  const temp = floorTemp(fid);
  const zs = zonesOf(fid);
  card.hidden = false;
  card.innerHTML = `
    <h3><span>${fid}</span>${f.name}</h3>
    <div class="role">${f.role}</div>
    <div class="fstats">
      <div class="fstat"><i>존</i><b>${zs.length}</b></div>
      <div class="fstat"><i>자산</i><b>${up}/${assets.length}</b></div>
      <div class="fstat"><i>랙</i><b>${racksOf(fid).length}</b></div>
      <div class="fstat"><i>근무자</i><b>${crewOf(fid).length}</b></div>
      <div class="fstat"><i>전력</i><b>${kw.toFixed(1)} kW</b></div>
      <div class="fstat"><i>온도</i><b style="color:${temp == null ? '#41566d' : tempColor(temp)}">${
        temp == null ? '—' : temp.toFixed(1) + '°C'}</b></div>
      <div class="fstat" style="grid-column:1/3"><i>교과</i><b>${
        (f.curriculum || []).join(' · ')}</b></div>
    </div>`;
}

/* ════════════════════════════════════════════════════════════════
 *  우측 레일
 * ════════════════════════════════════════════════════════════════ */
function selectTab(name) {
  $$('#tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.pane').forEach(p => p.classList.toggle('active', p.id === `pane-${name}`));
}

function renderPower() {
  const p = ST?.power;
  if (!p) return;
  const pdus = LAYOUT?.facility?.pdu || [];
  const src = !p.utility_ok
    ? (p.generator_running ? ['발전기', 'on'] : ['UPS 배터리', 'off'])
    : ['상용전원', 'on'];

  $('#pane-power').innerHTML = `
  <div class="card">
    <h4>전원 계통</h4>
    <div class="body">
      <div class="row"><span class="k">공급원</span>
        <span class="v"><span class="pill ${src[1]}">${src[0]}</span></span></div>
      <div class="row"><span class="k">수전</span>
        <span class="v"><span class="pill ${p.utility_ok ? 'on' : 'off'}">${p.utility_ok ? '정상' : '상실'}</span></span></div>
      <div class="row"><span class="k">비상 발전기</span>
        <span class="v"><span class="pill ${p.generator_failed ? 'off' : p.generator_running ? 'on' : ''}">${
          p.generator_failed ? '기동 실패' : p.generator_running ? '운전 중' : '대기'}</span></span></div>
      <div class="row"><span class="k">총 부하 / 정격</span>
        <span class="v">${p.total_kw.toFixed(1)} / ${p.rated_kw} kW</span></div>
      <div class="bar"><i style="width:${Math.min(p.total_kw / p.rated_kw * 100, 100)}%;
        background:${p.total_kw / p.rated_kw > .9 ? '#ff4d6a' : '#22d3ee'}"></i></div>
      <div class="row"><span class="k">UPS 충전</span>
        <span class="v">${p.ups_charge_pct}%${p.on_battery ? ` · -${p.drain_pct_per_min}%/분` : ''}</span></div>
      <div class="bar"><i style="width:${p.ups_charge_pct}%;
        background:${p.ups_charge_pct < 25 ? '#ff4d6a' : p.on_battery ? '#fbbf24' : '#3ddc97'}"></i></div>
      ${p.on_battery ? `<div class="row"><span class="k">잔여 시간</span>
        <span class="v" style="color:#ff4d6a">${p.ups_runtime_min} 분</span></div>` : ''}
      <div class="row" title="랩의 진짜 소비. 화면의 kW 는 대표 DC 규모로 환산한 값이다 — 사용률만 실측이다.">
        <span class="k">실측 소비(환산 전)</span><span class="v">${p.measured_kw} kW</span></div>
    </div>
  </div>

  <div class="card"><h4>PDU 부하</h4><div class="body">
    ${pdus.map(d => {
      const kw = p.pdu?.[d.id] ?? 0, pct = kw / d.capacity_kw * 100;
      return `<div class="row"><span class="k">${d.id} <small style="color:#41566d">${d.floor}</small></span>
        <span class="v">${kw.toFixed(2)} / ${d.capacity_kw} kW</span></div>
        <div class="bar"><i style="width:${Math.min(pct, 100)}%;
          background:${pct > 90 ? '#ff4d6a' : pct > 70 ? '#fbbf24' : '#3ddc97'}"></i></div>`;
    }).join('')}
  </div></div>

  <div class="card"><h4>아일 온습도</h4><div class="body">
    ${Object.values(ST.aisles || {}).map(a => `
      <div class="row"><span class="k">${a.aisle} 아일 <small style="color:#41566d">${a.floor}</small></span>
        <span class="v" style="color:${tempColor(a.temp_c)}">${a.temp_c}°C · ${a.humidity_pct}%RH</span></div>
      <div class="bar"><i style="width:${Math.min((a.temp_c - 16) / 26 * 100, 100)}%;
        background:${tempColor(a.temp_c)}"></i></div>
      <div class="row"><span class="k" style="font-size:10.5px">발열 ${a.it_kw}kW · 냉방 ${a.cooling_kw}kW</span>
        <span class="v" style="font-size:10.5px;color:${a.cooling_kw < a.it_kw ? '#ff4d6a' : '#3ddc97'}">
          ${a.cooling_kw < a.it_kw ? '냉방 부족' : '균형'}</span></div>`).join('')}
  </div></div>`;
}

/** 존 탭 — 건물이면 전체 존, 층이면 그 층의 존만. */
function renderZonePane() {
  const pane = $('#pane-zone');
  const scope = VIEW.mode === 'floor' ? VIEW.floor : null;
  const zs = scope ? zonesOf(scope) : (LAYOUT?.zones || []);
  const pool = scope ? assetsOf(scope) : (LAYOUT?.it_assets || []);

  pane.innerHTML = `<div class="railhead">${
    scope ? `${scope} 의 존 ${zs.length}개 — 층은 물리, 존은 논리다` : '전체 존 · 신뢰등급 순'}</div>`
    + zs.map(z => {
      const mine = pool.filter(a => a.zone === z.id || (z.logical && a.logical_zone === z.id));
      const down = mine.filter(a => !alive(a)).length;
      return `<div class="zcard" data-z="${z.id}" style="border-left-color:${z.color}">
        <div class="zh"><b style="color:${z.color}">${z.id}</b>
          <span>${z.name}</span>
          <span class="trust">${z.trust}${z.logical ? ' 논리' : ''}${z.isolated ? ' 격리' : ''}</span></div>
        <div class="cidr">${z.cidr || '— 세그먼트 없음 (권한 경계)'}${
          z.gateway ? ` · gw ${z.gateway}` : ''}</div>
        <div class="zrole">${z.role}</div>
        ${mine.length ? `<div class="zassets">${mine.map(a =>
          `<span class="za ${alive(a) ? '' : 'down'}">${a.name}</span>`).join('')}</div>` : ''}
        ${down ? `<div class="zrole" style="color:#ff4d6a">정지 ${down}건</div>` : ''}
      </div>`;
    }).join('');
  $$('[data-z]', pane).forEach(n => n.onclick = () => openZone(n.dataset.z));
}

function crewPortrait(w) {
  const rt = RT_COLOR[w.runtime] || '#94a3b8';
  const vest = AU_COLOR[w.autonomy] || '#475569';
  return `<svg class="por" viewBox="0 0 26 30" shape-rendering="crispEdges">
    <rect x="6" y="3" width="14" height="7" fill="${rt}"/>
    <rect x="7" y="10" width="12" height="8" fill="#e6cba8"/>
    <rect x="10" y="13" width="2" height="2" fill="#1e293b"/>
    <rect x="15" y="13" width="2" height="2" fill="#1e293b"/>
    <rect x="5" y="18" width="16" height="10" fill="${vest}"/>
    <rect x="5" y="20" width="16" height="2" fill="rgba(255,255,255,.35)"/>
  </svg>`;
}

function renderCrew() {
  const pane = $('#pane-crew');
  const ws = ROSTER.workers || [];
  if (!ws.length) { pane.innerHTML = '<div class="empty">근무자 명단을 읽지 못했습니다</div>'; return; }
  const scope = VIEW.mode === 'floor' ? VIEW.floor : null;
  const groups = scope ? [floors().find(f => f.id === scope)] : floors();
  pane.innerHTML = groups.filter(Boolean).map(f => {
    const list = ws.filter(w => w.floor === f.id);
    if (!list.length) return '';
    return `<div class="railhead">${f.id} ${f.name} · ${list.length}명</div>`
      + list.map(w => `
      <div class="crew" data-crew="${w.id}">
        ${crewPortrait(w)}
        <div class="info">
          <div class="nm">${w.name}</div>
          <div class="sub">${w.id}</div>
          <div class="meta">
            <span class="tag rt-${w.runtime}">${w.runtime}</span>
            <span class="tag au-${w.autonomy}">${w.autonomy}</span>
            <span class="tag" style="color:${zoneColor(w.zone)};border-color:${zoneColor(w.zone)}88">${w.zone}</span>
            ${(w.curriculum || []).map(c => `<span class="tag">${c}</span>`).join('')}
          </div>
          <div class="loops">${(w.loop_detail || []).length
            ? w.loop_detail.map(l => `· ${l.name} <span style="color:#41566d">${l.cadence || ''}${
                l.runbook ? ' · 런북' : ''}</span>`).join('<br>')
            : '<span style="color:#41566d">등록된 루프 없음</span>'}</div>
        </div>
      </div>`).join('');
  }).join('') || '<div class="empty">이 층에 배치된 근무자가 없습니다</div>';
  $$('[data-crew]', pane).forEach(n => n.onclick = () => openCrew(n.dataset.crew));
}

function renderAlarms() {
  const pane = $('#pane-alarm');
  const list = ST?.alarms || [];
  if (!list.length) { pane.innerHTML = '<div class="empty">활성 경보 없음</div>'; return; }
  pane.innerHTML = list.map(a => `
    <div class="alarm-item ${a.level >= 12 ? 'l12' : ''}">
      <span class="lv">L${a.level}</span>
      <div class="t">${a.msg}</div>
      <div class="m">${a.scope} · ${a.metric}=${a.value}</div>
    </div>`).join('');
}

const hhmmss = ts => {
  const d = new Date(ts * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(x => String(x).padStart(2, '0')).join(':');
};

function renderLog() {
  const pane = $('#pane-log');
  if (!EVENTS.length) { pane.innerHTML = '<div class="empty">이벤트 없음</div>'; return; }
  pane.innerHTML = EVENTS.slice().reverse().map(e =>
    `<div class="logline k-${e.kind}"><span class="ts">${hhmmss(e.ts)}</span><span>${e.msg}</span></div>`
  ).join('');
}

function renderTicker() {
  const last = EVENTS[EVENTS.length - 1];
  const line = $('#tk-line');
  line.className = 'tk-line' + (last ? ` k-${last.kind}` : '');
  line.textContent = last ? `${hhmmss(last.ts)}  ${last.msg}` : '이벤트 없음';
  const a = ST?.alarms || [];
  const nf = Object.values(ST?.faults || {}).reduce((s, v) => s + v.length, 0);
  $('#tk-stats').textContent =
    `이벤트 ${EVENTS.length} · 경보 ${a.length} · 주입 ${nf} · 차단 ${(ST?.shed || []).length}`;
}

/* ── 존 범례 + 체인 ─────────────────────────────────────────────── */
function renderChain() {
  const chain = LAYOUT?.zone_chain || [];
  const node = id => {
    const z = zoneOf(id) || { id, name: id };
    return `<div class="node" style="border-color:${z.color}66">
      <b style="color:${z.color}">${z.id} ${z.name}</b>
      <small>${z.cidr || '세그먼트 없음'}</small></div>`;
  };
  const main = ['ext', 'pipe', 'dmz', 'int'];
  let html = '';
  main.forEach((z, i) => {
    if (i) {
      const hop = chain.find(c => c.to === z);
      html += `<div class="hop"><span class="arrow">▶</span>${hop ? hop.via : ''}</div>`;
    }
    html += node(z);
  });
  const branches = chain.filter(c => !main.includes(c.to));
  if (branches.length) {
    html += `<div class="hop"><span class="arrow">┬</span>ips</div><div class="branch">`
      + branches.map(c => node(c.to)).join('') + `</div>`;
  }
  $('#chain').innerHTML = html;
}

/* ── 상단 KPI ────────────────────────────────────────────────────── */
function renderKpis() {
  if (!ST) return;
  const p = ST.power;
  const temps = Object.values(ST.aisles || {});
  const hot = temps.length ? Math.max(...temps.map(a => a.temp_c)) : null;
  const alarms = ST.alarms || [];
  const crit = alarms.filter(a => a.level >= 12).length;
  const assets = LAYOUT?.it_assets || [];
  const up = assets.filter(alive).length;
  const ws = ROSTER.workers || [];

  const set = (id, val, sub, cls) => {
    const n = $(id);
    n.querySelector('b').textContent = val;
    n.querySelector('small').textContent = sub;
    n.className = 'kpi' + (cls ? ' ' + cls : '');
  };
  set('#kpi-power', `${p.total_kw.toFixed(1)}kW`,
    `정격 ${p.rated_kw}kW · 실측 ${p.measured_kw}kW`,
    p.total_kw / p.rated_kw > .9 ? 'crit' : p.total_kw / p.rated_kw > .75 ? 'warn' : '');
  set('#kpi-ups', p.on_battery ? `${p.ups_runtime_min}분` : `${p.ups_charge_pct}%`,
    p.on_battery ? `배터리 ${p.ups_charge_pct}% · -${p.drain_pct_per_min}%/분`
      : p.generator_running ? '발전기 운전 중' : '상용전원',
    p.on_battery ? 'crit' : p.generator_running ? 'warn' : '');
  set('#kpi-temp', hot == null ? '—' : `${hot.toFixed(1)}°C`, 'ASHRAE 18~27°C',
    hot > 32 ? 'crit' : hot > 27 ? 'warn' : '');
  set('#kpi-alarm', String(alarms.length), crit ? `L12 이상 ${crit}건` : '심각 없음',
    crit ? 'crit' : alarms.length ? 'warn' : '');
  set('#kpi-cont', `${up}/${assets.length}`, '컨테이너·원격 실측',
    up < assets.length ? 'warn' : '');
  set('#kpi-crew', String(ws.length),
    `L3 ${ws.filter(w => w.autonomy === 'L3').length} · 승인자 ${ws.filter(w => w.autonomy === 'approver').length}`, '');

  $('#bld-name').textContent = ST.building || 'kt66';
  const tb = $('#tsbadge'), ts = ST.time_scale ?? 1;
  tb.hidden = ts === 1;
  tb.textContent = `시간 ×${ts}`;
}

/* ════════════════════════════════════════════════════════════════
 *  상세 드로어
 * ════════════════════════════════════════════════════════════════ */
function showDrawer(name, zoneId, html) {
  $('#dr-name').textContent = name;
  const z = $('#dr-zone');
  if (zoneId) { z.textContent = zoneId; z.style.color = zoneColor(zoneId); z.hidden = false; }
  else z.hidden = true;
  $('#dr-body').innerHTML = html;
  $('#drawer').hidden = false;
  $$('#dr-body .cmd').forEach(n => n.onclick = () => {
    navigator.clipboard?.writeText(n.textContent.replace(/^\$ /, ''));
    const t = n.textContent; n.textContent = '복사했습니다';
    setTimeout(() => n.textContent = t, 900);
  });
}
const kv = (k, v) => v == null || v === '' ? ''
  : `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;

function openAsset(id) {
  const a = (LAYOUT?.it_assets || []).find(x => x.id === id);
  if (!a) return;
  SELECTED = id;
  const st = assetState(id), up = alive(a);
  const ct = a.container ? ST?.containers?.[a.container] : null;
  const zn = zoneOf(a.zone);
  const grp = LAYOUT?.shed_groups?.[a.shed_group];

  showDrawer(a.name, a.zone, `
    ${kv('자산 ID', a.id)}
    ${kv('상태', up ? '<span style="color:#3ddc97">가동 중</span>'
                    : '<span style="color:#ff4d6a">정지</span>')}
    ${kv('위치', `${a.floor} · ${a.rack || '랙 외'}${a.u ? ` · ${a.u}U` : ''}`)}
    ${kv('존', `${a.zone} (${zn?.trust || '-'}) ${zn?.cidr || ''}`)}
    ${a.logical_zone ? kv('권한 경계', `${a.logical_zone} — 망 경계와 다르다`) : ''}
    ${kv('주소', a.ip)}
    ${kv('실체', a.container || (a.remote ? `원격 ${a.remote}` : '-'))}
    ${ct ? kv('컨테이너', ct.status) : ''}
    <div class="kv"><span class="k">실측 사용률</span><span class="v">${(st.util * 100).toFixed(0)}%</span></div>
    <div class="bar"><i style="width:${Math.min(st.util * 100, 100)}%;background:${
      st.util > .7 ? '#ff4d6a' : '#22d3ee'}"></i></div>
    ${kv('환산 전력', `${st.kw.toFixed(2)} kW  <span style="color:#41566d">(${a.idle_kw}~${a.rated_kw})</span>`)}
    ${grp ? kv('부하 그룹', `${grp.name} · 우선순위 ${grp.priority}`) : ''}
    ${grp ? `<div class="dim" style="margin-top:6px">차단 시: ${grp.impact}</div>` : ''}
    <div class="access">
      ${a.web ? `<a class="btn act" href="${a.web}" target="_blank" rel="noopener">웹 콘솔 열기 ↗</a>` : ''}
      ${a.api ? `<a class="btn" href="${a.api}" target="_blank" rel="noopener">API ↗</a>` : ''}
      ${a.ssh ? `<div class="cmd">${a.ssh}</div>` : ''}
      ${a.container ? `<div class="cmd">docker exec -it ${a.container} sh</div>` : ''}
      ${a.container ? `<div class="cmd">docker logs -f --tail 100 ${a.container}</div>` : ''}
    </div>`);
}

function openZone(id) {
  const z = zoneOf(id);
  if (!z) return;
  SELECTED = null;
  const mine = (LAYOUT?.it_assets || []).filter(a => a.zone === id
    || (z.logical && a.logical_zone === id));
  const inn = (LAYOUT?.zone_chain || []).filter(c => c.to === id);
  const out = (LAYOUT?.zone_chain || []).filter(c => c.from === id);
  showDrawer(`${z.id} · ${z.name}`, id, `
    ${kv('대역', z.cidr || '없음 — 논리 존')}
    ${kv('신뢰등급', `${z.trust}${z.isolated ? ' · 격리망' : ''}${z.logical ? ' · 논리' : ''}`)}
    ${kv('게이트웨이', z.gateway)}
    <div class="dim" style="margin:6px 0 10px;line-height:1.6">${z.role}</div>
    <h4 style="margin:12px 0 5px;font-size:11px;color:#67809a">들어오는 길 ${inn.length}</h4>
    ${inn.map(c => kv(`${c.from} →`, `${c.via} · ${c.label}`)).join('') || '<div class="dim">없음</div>'}
    <h4 style="margin:12px 0 5px;font-size:11px;color:#67809a">나가는 길 ${out.length}</h4>
    ${out.map(c => kv(`→ ${c.to}`, `${c.via} · ${c.label}`)).join('') || '<div class="dim">없음</div>'}
    <h4 style="margin:12px 0 5px;font-size:11px;color:#67809a">자산 ${mine.length}</h4>
    ${mine.map(a => `<div class="kv" style="cursor:pointer" data-a="${a.id}">
      <span class="k">${a.name} <small style="color:#41566d">${a.floor}</small></span>
      <span class="v" style="color:${alive(a) ? '#c8d6e6' : '#ff4d6a'}">${
        alive(a) ? assetState(a.id).kw.toFixed(2) + 'kW' : '정지'}</span></div>`).join('')
      || '<div class="dim">없음</div>'}
    <div class="dim" style="margin-top:12px">존 밖으로 나가는 트래픽은 위의 경유 지점을
      반드시 지난다. 우회로가 없다는 것이 이 랩의 핵심 성질이다.</div>`);
  $$('#dr-body [data-a]').forEach(n => n.onclick = () => openAsset(n.dataset.a));
}

function openRack(id) {
  const r = (LAYOUT?.racks || []).find(x => x.id === id);
  if (!r) return;
  SELECTED = null;
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === id);
  const a = ST?.aisles?.[r.aisle];
  const kw = list.reduce((s, x) => s + assetState(x.id).kw, 0);
  const zs = [...new Set(list.map(x => x.zone))];
  showDrawer(r.id, null, `
    ${kv('층 · 아일', `${r.floor} · ${r.aisle}`)}
    ${kv('용량', `${r.u}U · 설계 ${r.design_kw}kW`)}
    ${kv('현재 부하', `${kw.toFixed(2)} kW (${(kw / r.design_kw * 100).toFixed(0)}%)`)}
    ${a ? kv('아일 온습도', `${a.temp_c}°C · ${a.humidity_pct}%RH`) : ''}
    ${a ? kv('냉방', `${a.cooling_kw}kW ${a.cooling_kw < a.it_kw ? '— 부족' : ''}`) : ''}
    ${kv('섞여 있는 존', zs.map(z => `<span style="color:${zoneColor(z)}">${z}</span>`).join(' '))}
    <h4 style="margin:13px 0 5px;font-size:11px;color:#67809a">탑재 자산 ${list.length}</h4>
    ${list.map(x => `<div class="kv" style="cursor:pointer" data-a="${x.id}">
      <span class="k"><i style="display:inline-block;width:7px;height:7px;border-radius:2px;
        background:${zoneColor(x.zone)};margin-right:6px"></i>${x.name}</span>
      <span class="v">${assetState(x.id).kw.toFixed(2)}kW</span></div>`).join('')}
    <div class="dim" style="margin-top:10px">한 랙 안에 서로 다른 존이 섞여 있다 —
      물리적으로 옆자리인데 논리적으로 다른 망이다. 이 어긋남이 1주차 실습 재료다.<br><br>
      같은 아일의 랙끼리는 열이 섞인다. 한 랙의 폭주가 옆 랙 온도를 올린다.</div>`);
  $$('#dr-body [data-a]').forEach(n => n.onclick = () => openAsset(n.dataset.a));
}

function openFacility(item) {
  SELECTED = null;
  const down = facilityDown(item);
  const p = ST?.power;
  let extra = '';
  if (item.kind === 'ups' && p) {
    extra = kv('충전', `${p.ups_charge_pct}%`) + kv('잔여', `${p.ups_runtime_min} 분`)
      + kv('배터리', `${item.battery_kwh} kWh / ${item.capacity_kw} kW`);
  } else if (item.kind === 'generator' && p) {
    extra = kv('상태', p.generator_failed ? '기동 실패' : p.generator_running ? '운전 중' : '대기')
      + kv('기동 지연', `${item.start_delay_s} 초`) + kv('연료', `${item.fuel_hours} 시간`);
  } else if (item.kind === 'pdu') {
    const kw = p?.pdu?.[item.id] ?? 0;
    extra = kv('부하', `${kw.toFixed(2)} / ${item.capacity_kw} kW (${(kw / item.capacity_kw * 100).toFixed(0)}%)`)
      + kv('급전 랙', item.rack);
  } else if (item.kind === 'crac') {
    const a = ST?.aisles?.[item.aisle];
    extra = kv('담당 아일', item.aisle) + kv('정격', `${item.capacity_kw} kW`)
      + (a ? kv('현재 출력', `${a.cooling_kw} kW`) : '');
  }
  showDrawer(item.name || item.id, 'ot', `
    ${kv('설비 ID', item.id)}
    ${kv('종류', item.kind)}
    ${kv('층', item.floor)}
    ${kv('상태', down ? '<span style="color:#ff4d6a">이상</span>'
                      : '<span style="color:#3ddc97">정상</span>')}
    ${extra}
    <div class="dim" style="margin-top:12px">시설 계통은 가상이다. 다만 이 계통이 계산에 쓰는
      <b>발열은 실측</b>이다 — 컨테이너 CPU 와 GPU 상태에서 온다.</div>`);
}

function openCrew(id) {
  const w = (ROSTER.workers || []).find(x => x.id === id);
  if (!w) return;
  SELECTED = null;
  const auto = {
    L1: '보고 전용 — 상태를 바꾸지 않는다',
    L2: '승인 후 실행 — 운영 리드의 판정이 있어야 움직인다',
    L3: '무인 실행 — 런북이 등록된 작업에만 허용된다',
    approver: '승인 전담 — 스스로 실행하지 않고 L2 요청을 판정한다',
  }[w.autonomy] || '';
  showDrawer(w.name, w.zone, `
    ${kv('페르소나 ID', w.id)}
    ${kv('배치', `${w.floor} · ${w.zone} 존`)}
    ${kv('런타임', w.runtime)}
    ${kv('자율 등급', w.autonomy)}
    <div class="dim" style="margin:4px 0 10px">${auto}</div>
    ${kv('담당 자산', (w.assets || []).join(', ') || '-')}
    ${kv('교과 주차', (w.curriculum || []).join(', ') || '-')}
    <h4 style="margin:13px 0 5px;font-size:11px;color:#67809a">루프 ${(w.loop_detail || []).length}</h4>
    ${(w.loop_detail || []).map(l => `
      <div class="kv"><span class="k">${l.name}</span>
        <span class="v">${l.cadence || ''} · ${l.steps}단계 · 게이트 ${l.gates}</span></div>`).join('')
      || '<div class="dim">등록된 루프 없음</div>'}
    <div class="access">
      <div class="cmd">agents/agentctl render ${w.id}</div>
      <div class="cmd">agents/agentctl runtime ${w.id} hermes</div>
    </div>
    <div class="dim" style="margin-top:10px">런타임은 페르소나마다 따로 고른다.
      명세는 중립이고 어댑터가 각 런타임의 형식으로 렌더한다.</div>`);
}

/* ════════════════════════════════════════════════════════════════
 *  UPS 절체 판단
 * ════════════════════════════════════════════════════════════════ */
function renderUps() {
  const p = ST?.power;
  const modal = $('#ups-modal');
  if (!p?.on_battery) { modal.hidden = true; upsDismissed = false; return; }
  if (upsDismissed) { modal.hidden = true; return; }
  modal.hidden = false;

  $('#ups-sub').textContent = p.generator_failed
    ? '비상 발전기 기동 실패 — 배터리만 남았다' : '발전기 기동 대기 중';

  $('#ups-stats').innerHTML = `
    <div class="ups-stat ${p.ups_charge_pct < 30 ? 'crit' : ''}">
      <label>배터리</label><b>${p.ups_charge_pct}%</b>
      <small>분당 ${p.drain_pct_per_min}% 감소</small></div>
    <div class="ups-stat ${p.ups_runtime_min < 10 ? 'crit' : ''}">
      <label>잔여 시간</label><b>${p.ups_runtime_min}분</b>
      <small>현재 부하 유지 시</small></div>
    <div class="ups-stat"><label>총 부하</label><b>${p.total_kw.toFixed(1)}kW</b>
      <small>정격 ${p.rated_kw}kW</small></div>
    <div class="ups-stat"><label>냉방</label>
      <b style="color:${p.generator_running ? '#3ddc97' : '#ff4d6a'}">${
        p.generator_running ? '가동' : '정지'}</b>
      <small>CRAC 은 UPS 를 타지 않는다</small></div>`;

  const rows = (ST.shed_analysis || []).map(g => {
    const empty = (g.assets ?? 1) === 0;
    return `
    <tr class="${g.shed ? 'shed' : ''}">
      <td><b>${g.name}</b><div class="dim">${empty
        ? '이 그룹에 배치된 자산이 아직 없다 — 끊어도 부하가 줄지 않는다'
        : g.impact}</div></td>
      <td class="num">${empty ? '—' : g.kw.toFixed(1) + 'kW'}</td>
      <td class="num" style="color:${!empty && g.runtime_if_shed_min > p.ups_runtime_min * 1.3
        ? '#3ddc97' : '#67809a'}">${g.shed || empty ? '—' : `${g.runtime_if_shed_min}분`}</td>
      <td>${empty ? '' : `<button class="btn sm ${g.shed ? '' : 'danger'}" data-shed="${g.group}"
        data-restore="${g.shed}">${g.shed ? '복구' : '차단'}</button>`}</td>
    </tr>`;
  }).join('');

  $('#ups-body').innerHTML = `
    <table>
      <thead><tr><th>부하 그룹 · 차단 시 영향</th><th style="text-align:right">소비</th>
        <th style="text-align:right">차단 시 잔여</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;

  $$('#ups-body [data-shed]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    await post('/api/shed', { group: b.dataset.shed, restore: b.dataset.restore === 'true' });
    await poll();
  });
}

/* ════════════════════════════════════════════════════════════════
 *  강사 패널
 * ════════════════════════════════════════════════════════════════ */
function faultTargets(fault) {
  const F = LAYOUT?.facility || {};
  const ids = l => (l || []).map(i => i.id);
  switch (fault) {
    case 'utility_fail': return [F.utility?.id].filter(Boolean);
    case 'generator_fail': return ids(F.generator);
    case 'chiller_fail': return ids(F.chiller);
    case 'crac_fail': return ids(F.crac);
    case 'pdu_overload': return ids(F.pdu);
    case 'smoke': return floors().map(f => f.id);
    case 'door_forced':
    case 'door_held': return (F.security || []).filter(s => s.kind === 'door').map(s => s.id);
    case 'cctv_offline': return (F.security || []).filter(s => s.kind === 'cctv').map(s => s.id);
    case 'humidity_drift': return Object.keys(ST?.aisles || {});
    default: return ['*'];
  }
}

function renderInjector() {
  const body = $('#inj-body');
  const active = ST?.faults || {};
  const ts = ST?.time_scale ?? 1;

  // 유휴 랩은 발열이 12kW 남짓이라 냉동기를 죽여도 분당 0.2°C 밖에 안 오른다.
  // 열 시나리오는 배속을 올려야 한 교시 안에 전개된다. 반대로 ENV-03 은 ×1 이어야 한다.
  const speed = `
    <div class="frow" style="margin-bottom:10px">
      <span class="fname">시간 배속<small>열 시나리오는 ×10 이상 · UPS 절체(ENV-03)는 ×1 유지</small></span>
      <select id="ts-sel">${[1, 5, 10, 30, 60].map(v =>
        `<option value="${v}" ${v === ts ? 'selected' : ''}>×${v}${v === 1 ? ' 실시간' : ''}</option>`).join('')}</select>
      <button class="btn sm act" id="ts-apply">적용</button>
    </div>`;

  body.innerHTML = speed + '<div class="fgrid">' + Object.entries(FAULTS.available || {}).map(([k, desc]) => {
    const on = (active[k] || []);
    const tg = faultTargets(k);
    return `<div class="frow ${on.length ? 'active' : ''}">
      <span class="fname">${desc}<small>${k}${on.length ? ` · 진행 중: ${on.join(', ')}` : ''}</small></span>
      <select data-tg="${k}">${tg.map(t => `<option value="${t}">${t}</option>`).join('')}</select>
      <button class="btn sm danger" data-inj="${k}">주입</button>
      <button class="btn sm" data-clr="${k}" ${on.length ? '' : 'disabled'}>해제</button>
    </div>`;
  }).join('') + '</div>';

  $('#ts-apply', body).onclick = async () => {
    await post('/api/timescale', { value: $('#ts-sel', body).value });
    await poll(); renderInjector();
  };

  const tgOf = k => $(`[data-tg="${k}"]`, body)?.value || '*';
  $$('[data-inj]', body).forEach(b => b.onclick = async () => {
    await post('/api/inject', { fault: b.dataset.inj, target: tgOf(b.dataset.inj) });
    await poll(); renderInjector();
  });
  $$('[data-clr]', body).forEach(b => b.onclick = async () => {
    const k = b.dataset.clr;
    for (const t of (ST?.faults?.[k] || [])) await post('/api/inject', { fault: k, target: t, clear: true });
    await poll(); renderInjector();
  });
}

/* ── 통신 ────────────────────────────────────────────────────────── */
async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}
async function post(url, params) {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])));
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
    $('#link-status').classList.add('down');
    $('#link-status').title = String(e);
    return;
  }
  // 3초마다 패널을 다시 그리므로 스크롤 위치를 붙잡아 둔다 — 목록을 읽는 중에
  // 맨 위로 튀면 아무도 안 읽는다.
  const body = $('.tabbody'), keep = body.scrollTop;
  renderKpis(); renderPower(); renderAlarms(); renderLog(); renderTicker();
  renderZonePane(); renderCrew(); renderUps();
  body.scrollTop = keep;
  render();
  if (SELECTED) openAsset(SELECTED);          // 열려 있는 드로어도 같이 갱신
}

async function boot() {
  try {
    [LAYOUT, ROSTER, FAULTS] = await Promise.all([
      get('/api/layout'), get('/api/roster'), get('/api/faults'),
    ]);
  } catch (e) {
    document.body.innerHTML =
      `<div class="empty" style="padding:60px">초기 데이터를 읽지 못했습니다 — ${e}</div>`;
    return;
  }
  renderChain(); renderCrumbs();
  await poll();
  setInterval(poll, 3000);
  setInterval(() => {
    $('#clock').textContent = new Date().toLocaleTimeString('ko-KR', { hour12: false });
  }, 1000);
}

/* ── 이벤트 배선 ─────────────────────────────────────────────────── */
$$('#tabs .tab').forEach(t => t.onclick = () => selectTab(t.dataset.tab));
$('#dr-close').onclick = () => { $('#drawer').hidden = true; SELECTED = null; };
$('#ups-close').onclick = () => { upsDismissed = true; $('#ups-modal').hidden = true; };
$('#btn-instructor').onclick = () => { renderInjector(); $('#inj-modal').hidden = false; };
$('#inj-close').onclick = () => $('#inj-modal').hidden = true;
$('#btn-reset').onclick = async () => {
  await post('/api/reset', {}); upsDismissed = false;
  await poll(); renderInjector();
};

const zoom = k => { VIEW.zoom = Math.max(.4, Math.min(VIEW.zoom * k, 6)); applyVB(); };
$('#z-in').onclick = () => zoom(1.25);
$('#z-out').onclick = () => zoom(1 / 1.25);
$('#z-fit').onclick = () => { VIEW.zoom = 1; VIEW.panx = VIEW.pany = 0; applyVB(); };

// 휠 확대 · 드래그 이동
const scene = $('#scene');
scene.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12); },
  { passive: false });
let drag = null;
scene.addEventListener('pointerdown', e => {
  drag = { x: e.clientX, y: e.clientY, px: VIEW.panx, py: VIEW.pany };
  scene.setPointerCapture(e.pointerId);
});
scene.addEventListener('pointermove', e => {
  if (!drag || !BASE_VB) return;
  const s = (BASE_VB[2] / VIEW.zoom) / scene.clientWidth;
  VIEW.panx = drag.px - (e.clientX - drag.x) * s;
  VIEW.pany = drag.py - (e.clientY - drag.y) * s;
  applyVB();
});
scene.addEventListener('pointerup', () => drag = null);
scene.addEventListener('pointercancel', () => drag = null);

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!$('#drawer').hidden) { $('#drawer').hidden = true; SELECTED = null; }
    else if (!$('#inj-modal').hidden) $('#inj-modal').hidden = true;
    else if (VIEW.mode === 'floor') enterBuilding();
  }
});
window.addEventListener('resize', () => applyVB());

boot();
