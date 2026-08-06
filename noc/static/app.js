/* kt66 NOC — 관제 화면.
 *
 * 화면이 지켜야 하는 규칙 하나: **여기서 상태를 만들지 않는다.**
 * 전력·온도·경보는 전부 envsim 이 계산한 값을 그대로 그린다. 화면이 자기 나름대로
 * 보간하거나 예쁘게 다듬기 시작하면 학생이 보는 숫자와 SIEM 에 남는 숫자가 갈라진다.
 * 그 순간 이 화면은 교보재가 아니라 장식이 된다.
 *
 * 그리는 것은 둘:
 *   건물 뷰 — 4개 층을 아이소메트릭으로 잘라 보여준다. 층은 물리 배치다.
 *   층  뷰 — 전산실 평면도. 랙·아일·CRAC·PDU·출입문, 그리고 존 색.
 * 층과 존은 직교한다. 한 층 안에 여러 존이 섞여 있는 것이 정상이고, 그 어긋남이 보여야 한다.
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
let VIEW = { mode: 'building', floor: null };
let SELECTED = null;
let upsDismissed = false;

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
const zoneOf = id => (LAYOUT?.zones || []).find(z => z.id === id);
const zoneColor = id => zoneOf(id)?.color || '#64748b';

/* 색 어둡게 — 아이소메트릭 면 음영에 쓴다 */
function shade(hex, f) {
  const p = i => Math.round(parseInt(hex.slice(i, i + 2), 16) * f)
    .toString(16).padStart(2, '0');
  return `#${p(1)}${p(3)}${p(5)}`;
}

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

/* ── 아이소메트릭 투영 ───────────────────────────────────────────── */
const XS = 24, YS = 13.5, ZS = 30, FLOOR_H = 2.3;
const iso = (x, y, z) => [(x - y) * XS, (x + y) * YS - z * ZS];

/** 아이소메트릭 직육면체. 보이는 면은 윗면 + x면 + y면 셋뿐이다. */
function isoBox(x, y, z, w, d, h, color, opts = {}) {
  const T = [iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)];
  const g = el('g', { class: opts.cls });
  // +y 면(왼쪽 앞), +x 면(오른쪽 앞)
  g.appendChild(el('polygon', {
    points: pts([T[3], T[2], iso(x + w, y + d, z), iso(x, y + d, z)]),
    fill: shade(color, .52), stroke: opts.stroke || 'none', 'stroke-width': .5,
  }));
  g.appendChild(el('polygon', {
    points: pts([T[1], T[2], iso(x + w, y + d, z), iso(x + w, y, z)]),
    fill: shade(color, .72), stroke: opts.stroke || 'none', 'stroke-width': .5,
  }));
  g.appendChild(el('polygon', {
    points: pts(T), fill: color, stroke: opts.stroke || 'none', 'stroke-width': .5,
    class: opts.hl ? 'hl' : null,
  }));
  return g;
}

/* ── 데이터 조회 헬퍼 ───────────────────────────────────────────── */
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

/** 그 층의 시설 항목 전부를 한 배열로. 종류(kind)를 붙여 준다. */
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

/** 아일 온도 — 그 층의 아일들 중 가장 뜨거운 값. 평균은 위험을 감춘다. */
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
 *  건물 뷰 — 4개 층 아이소메트릭
 * ════════════════════════════════════════════════════════════════ */
function drawBuilding() {
  const svg = $('#scene');
  svg.replaceChildren();
  const root = el('g');
  svg.appendChild(root);

  const fl = floors();
  const [GW, GD] = fl[0]?.grid || [12, 8];

  // 아래층부터 그린다 — 위층이 나중에 그려져야 겹침이 자연스럽다
  fl.forEach((f, idx) => {
    const z = idx * FLOOR_H;
    const g = el('g', { class: 'hit', on: { click: () => enterFloor(f.id) } });
    root.appendChild(g);

    const temp = floorTemp(f.id);
    const heat = temp == null ? null : tempColor(temp);
    const slabTop = temp == null ? '#16202e' : lerpHex('#16202e', heat, .3);

    // 바닥 슬래브(이중바닥). 두께 0.22 — 아래층 천장과 사이를 띄운다.
    g.appendChild(isoBox(0, 0, z, GW, GD, .22, slabTop,
      { stroke: 'rgba(34,211,238,.22)', hl: true }));

    // 이중바닥 타일 격자 — 데이터센터라는 신호를 주는 가장 값싼 단서
    const grid = el('g', { opacity: .16, stroke: '#22d3ee', 'stroke-width': .4, fill: 'none' });
    for (let x = 0; x <= GW; x += 2) grid.appendChild(el('line', {
      x1: iso(x, 0, z + .22)[0], y1: iso(x, 0, z + .22)[1],
      x2: iso(x, GD, z + .22)[0], y2: iso(x, GD, z + .22)[1],
    }));
    for (let y = 0; y <= GD; y += 2) grid.appendChild(el('line', {
      x1: iso(0, y, z + .22)[0], y1: iso(0, y, z + .22)[1],
      x2: iso(GW, y, z + .22)[0], y2: iso(GW, y, z + .22)[1],
    }));
    g.appendChild(grid);

    // 층 위의 물건들. 화면 아래쪽(x+y 큰 것)이 앞이므로 그 순서로 그린다.
    const items = [
      ...racksOf(f.id).map(r => ({ t: 'rack', o: r })),
      ...facilityOf(f.id).map(o => ({ t: 'fac', o })),
      ...crewOf(f.id).map((w, i) => ({ t: 'crew', o: w, i })),
    ].filter(it => it.t === 'crew' || it.o.pos);

    // 근무자는 층 앞쪽에 나란히 세운다 — 배치 좌표가 따로 없다
    items.filter(it => it.t === 'crew').forEach((it, i, arr) => {
      it.o._px = 1.2 + i * (Math.min(9, GW - 2) / Math.max(arr.length, 1));
      it.o._py = GD - 1.1;
    });

    items.sort((a, b) => {
      const k = it => it.t === 'crew' ? it.o._px + it.o._py : it.o.pos[0] + it.o.pos[1];
      return k(a) - k(b);
    });

    for (const it of items) {
      if (it.t === 'rack') g.appendChild(isoRack(it.o, z + .22));
      else if (it.t === 'fac') g.appendChild(isoFacility(it.o, z + .22));
      else g.appendChild(isoCrew(it.o, z + .22));
    }

    // 층 명패 — 왼쪽 바깥에
    const [lx, ly] = iso(0, GD, z + .6);
    const plate = el('g', { transform: `translate(${lx - 178},${ly - 18})` });
    plate.appendChild(el('rect', {
      width: 168, height: 44, rx: 4, fill: 'rgba(12,19,30,.9)',
      stroke: 'rgba(34,211,238,.25)',
    }));
    plate.appendChild(el('text', {
      x: 10, y: 17, 'font-size': 13, 'font-weight': 600, fill: '#22d3ee',
      class: 'mono', text: f.id,
    }));
    plate.appendChild(el('text', { x: 40, y: 17, 'font-size': 11.5, fill: '#c8d6e6', text: f.name }));
    plate.appendChild(el('text', {
      x: 10, y: 34, 'font-size': 10.5, class: 'mono',
      fill: temp == null ? '#41566d' : heat,
      text: temp == null ? '온도 센서 없음'
        : `${temp.toFixed(1)}°C · ${(ST?.floors?.[f.id]?.it_kw ?? 0).toFixed(1)}kW`,
    }));
    // 존 배지 — 한 층에 여러 존이 섞여 있음을 여기서 바로 보여준다
    (Array.isArray(f.zone) ? f.zone : [f.zone]).forEach((zid, i) => {
      plate.appendChild(el('rect', {
        x: 108 + i * 15, y: 26, width: 12, height: 10, rx: 2,
        fill: zoneColor(zid), opacity: .85,
      }));
    });
    g.appendChild(plate);
    plate.appendChild(el('title', { text: `${f.id} ${f.name}\n${f.role}` }));
  });

  // 외부 회선 — 공격자는 건물 밖에서 들어온다
  const fwFloor = fl.findIndex(f => f.id === '2F');
  if (fwFloor >= 0) {
    const [ex, ey] = iso(-1.5, 4, fwFloor * FLOOR_H + .8);
    root.appendChild(el('circle', { cx: ex - 60, cy: ey, r: 4, fill: '#ff4d6a', class: 'pulse' }));
    root.appendChild(el('text', {
      x: ex - 52, y: ey + 4, 'font-size': 10.5, fill: '#ff4d6a', text: '외부 / 인터넷',
    }));
    root.appendChild(el('line', {
      x1: ex - 56, y1: ey, x2: ex + 34, y2: ey + 10,
      stroke: '#ff4d6a', 'stroke-width': 1.2, opacity: .55, class: 'flow',
    }));
  }

  // WireGuard 터널 — DGX Spark 는 건물 밖 실물이다. 감추면 안 된다.
  const gpuFloor = fl.findIndex(f => f.id === '3F');
  const dgx = (LAYOUT?.it_assets || []).find(a => a.remote);
  if (gpuFloor >= 0 && dgx) {
    const z = gpuFloor * FLOOR_H + 1.1;
    const [ax, ay] = iso(11, 2, z);
    const bx = ax + 132, by = ay - 26;
    const live = assetState(dgx.id).util > 0;
    root.appendChild(el('path', {
      d: `M${ax},${ay} C${ax + 60},${ay - 8} ${bx - 60},${by + 6} ${bx},${by}`,
      fill: 'none', stroke: live ? '#a78bfa' : '#41566d', 'stroke-width': 1.4,
      class: live ? 'flow' : null, opacity: .85,
    }));
    const bg = el('g', {
      class: 'hit', transform: `translate(${bx},${by - 22})`,
      on: { click: e => { e.stopPropagation(); openAsset(dgx.id); } },
    });
    bg.appendChild(el('rect', {
      width: 150, height: 44, rx: 5, fill: 'rgba(12,19,30,.94)',
      stroke: live ? '#a78bfa' : '#2b3d55', class: 'hl',
    }));
    bg.appendChild(el('text', { x: 9, y: 17, 'font-size': 11, fill: '#a78bfa', text: 'DGX Spark (원격)' }));
    bg.appendChild(el('text', {
      x: 9, y: 32, 'font-size': 9.5, class: 'mono', fill: '#67809a',
      text: `WireGuard · ${dgx.ip || ''}`,
    }));
    root.appendChild(bg);
  }

  fitView(svg, root, 40);
}

/** 랙 — 어두운 캐비닛에 자산별 LED 줄. 온도가 오르면 캐비닛이 달아오른다. */
function isoRack(rack, z) {
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === rack.id);
  const aisle = ST?.aisles?.[rack.aisle];
  const body = aisle ? lerpHex('#1b2636', tempColor(aisle.temp_c), .34) : '#1b2636';
  const [x, y] = rack.pos;
  const w = 1.5, d = 1.0, h = 1.15;
  const g = el('g', {
    class: 'hit',
    on: { click: e => { e.stopPropagation(); openRack(rack.id); } },
  });
  g.appendChild(isoBox(x, y, z, w, d, h, body, { stroke: 'rgba(120,160,200,.3)', hl: true }));

  // 앞면(+y)에 U 단위 LED — 자산 하나가 한 줄, 색은 존, 밝기는 실측 사용률
  const top = z + h;
  list.forEach((a, i) => {
    const t = top - .12 - i * .13;
    if (t <= z + .06) return;
    const st = assetState(a.id), up = alive(a);
    const [p1x, p1y] = iso(x + .12, y + d, t);
    const [p2x, p2y] = iso(x + w - .12, y + d, t);
    g.appendChild(el('line', {
      x1: p1x, y1: p1y, x2: p2x, y2: p2y,
      stroke: up ? zoneColor(a.zone) : '#ff4d6a', 'stroke-width': 2.4,
      opacity: up ? .34 + st.util * .66 : 1,
      class: up ? null : 'blink',
    }));
  });
  g.appendChild(el('title', {
    text: `${rack.id} · ${rack.aisle} 아일 · ${rack.u}U\n설계 ${rack.design_kw}kW`
      + (aisle ? ` / 현재 ${aisle.it_kw}kW · ${aisle.temp_c}°C` : ''),
  }));
  return g;
}

const FAC_STYLE = {
  utility:   { c: '#94a3b8', w: 1.2, d: 1.2, h: 1.0, t: '⚡' },
  generator: { c: '#f59e0b', w: 1.6, d: 1.0, h: .9,  t: 'GEN' },
  ups:       { c: '#3ddc97', w: 1.4, d: 1.0, h: 1.1, t: 'UPS' },
  pdu:       { c: '#fbbf24', w: .5,  d: .5,  h: .8,  t: 'P' },
  chiller:   { c: '#38bdf8', w: 1.6, d: 1.2, h: .9,  t: 'CH' },
  crac:      { c: '#0ea5e9', w: .9,  d: 1.2, h: 1.0, t: 'CRAC' },
  fire:      { c: '#ef4444', w: .5,  d: .5,  h: .4,  t: 'FM' },
  door:      { c: '#94a3b8', w: .4,  d: 1.2, h: 1.4, t: '문' },
  cctv:      { c: '#7dd3fc', w: .4,  d: .4,  h: .5,  t: '◉' },
  facility:  { c: '#64748b', w: .5,  d: .5,  h: .5,  t: '' },
};

function isoFacility(item, z) {
  const s = FAC_STYLE[item.kind] || FAC_STYLE.facility;
  const down = facilityDown(item);
  const g = el('g', {
    class: 'hit',
    on: { click: e => { e.stopPropagation(); openFacility(item); } },
  });
  g.appendChild(isoBox(item.pos[0], item.pos[1], z, s.w, s.d, s.h,
    down ? '#7f1d2b' : s.c, { stroke: 'rgba(0,0,0,.35)', hl: true }));
  const [cx, cy] = iso(item.pos[0] + s.w / 2, item.pos[1] + s.d / 2, z + s.h);
  if (s.t) g.appendChild(el('text', {
    x: cx, y: cy + 3, 'font-size': 7.5, 'text-anchor': 'middle',
    fill: down ? '#ffb3c0' : 'rgba(6,10,17,.8)', 'font-weight': 700, class: 'mono', text: s.t,
  }));
  if (down) g.appendChild(el('circle', {
    cx, cy: cy - 14, r: 3.4, fill: '#ff4d6a', class: 'blink',
  }));
  g.appendChild(el('title', {
    text: `${item.name || item.id} (${item.id})${down ? '\n⚠ 이상' : ''}`,
  }));
  return g;
}

const RT_COLOR = { bastion: '#22d3ee', hermes: '#a78bfa', claude: '#fbbf24' };

/** 근무자 — 픽셀 인형. 헬멧 색이 런타임, 조끼 색이 자율등급이다. */
function isoCrew(w, z) {
  const [cx, cy] = iso(w._px, w._py, z);
  const rt = RT_COLOR[w.runtime] || '#94a3b8';
  const vest = w.autonomy === 'L3' ? '#ff4d6a'
    : w.autonomy === 'approver' ? '#3ddc97'
    : w.autonomy === 'L2' ? '#38bdf8' : '#475569';
  const g = el('g', {
    class: 'hit', transform: `translate(${cx},${cy})`, 'shape-rendering': 'crispEdges',
    on: { click: e => { e.stopPropagation(); openCrew(w.id); } },
  });
  g.appendChild(el('ellipse', { cx: 0, cy: 1, rx: 6, ry: 2.6, fill: 'rgba(0,0,0,.42)' }));
  g.appendChild(el('rect', { x: -4, y: -22, width: 8, height: 6, fill: rt, class: 'hl' }));  // 헬멧
  g.appendChild(el('rect', { x: -3, y: -16, width: 6, height: 4, fill: '#e2c9a8' }));        // 얼굴
  g.appendChild(el('rect', { x: -5, y: -12, width: 10, height: 9, fill: vest }));            // 조끼
  g.appendChild(el('rect', { x: -4, y: -3, width: 3, height: 3, fill: '#1e293b' }));
  g.appendChild(el('rect', { x: 1, y: -3, width: 3, height: 3, fill: '#1e293b' }));
  g.appendChild(el('title', {
    text: `${w.name} (${w.id})\n${w.runtime} · ${w.autonomy} · ${w.zone} 존`
      + (w.loops?.length ? `\n루프: ${w.loops.join(', ')}` : '\n루프 없음'),
  }));
  return g;
}

/* ════════════════════════════════════════════════════════════════
 *  층 뷰 — 전산실 평면도
 * ════════════════════════════════════════════════════════════════ */
const CELL = 62;

function drawFloor(fid) {
  const svg = $('#scene');
  svg.replaceChildren();
  const root = el('g');
  svg.appendChild(root);

  const f = floors().find(x => x.id === fid);
  if (!f) return;
  const [GW, GD] = f.grid || [12, 8];
  const W = GW * CELL, D = GD * CELL;

  // 벽 + 이중바닥
  root.appendChild(el('rect', {
    x: -8, y: -8, width: W + 16, height: D + 16, rx: 6,
    fill: '#0a111b', stroke: '#22384f', 'stroke-width': 2,
  }));
  const tiles = el('g', { stroke: 'rgba(34,211,238,.09)', 'stroke-width': 1 });
  for (let x = 0; x <= GW; x++) tiles.appendChild(el('line', { x1: x * CELL, y1: 0, x2: x * CELL, y2: D }));
  for (let y = 0; y <= GD; y++) tiles.appendChild(el('line', { x1: 0, y1: y * CELL, x2: W, y2: y * CELL }));
  root.appendChild(tiles);

  // 아일 열지도 — 랙이 놓인 줄을 따라 온도 띠를 깐다
  for (const r of racksOf(fid)) {
    const a = ST?.aisles?.[r.aisle];
    if (!a) continue;
    root.appendChild(el('rect', {
      x: 0, y: (r.pos[1] - .55) * CELL, width: W, height: 2.1 * CELL,
      fill: tempColor(a.temp_c), opacity: .09,
    }));
    root.appendChild(el('text', {
      x: W - 8, y: (r.pos[1] - .28) * CELL, 'text-anchor': 'end',
      'font-size': 10.5, class: 'mono', fill: tempColor(a.temp_c),
      text: `${r.aisle} 아일 ${a.temp_c}°C · ${a.humidity_pct}%RH · ${a.it_kw}kW`,
    }));
  }

  // CRAC → 콜드아일 급기
  for (const c of facilityOf(fid).filter(i => i.kind === 'crac')) {
    const down = facilityDown(c);
    const x = c.pos[0] * CELL, y = c.pos[1] * CELL;
    root.appendChild(planBox(c, x, y, CELL * .9, CELL * 1.4, down ? '#7f1d2b' : '#0ea5e9', 'CRAC'));
    if (!down) for (let i = 0; i < 3; i++) root.appendChild(el('line', {
      x1: x - 6, y1: y + 18 + i * 26, x2: x - 42, y2: y + 18 + i * 26,
      stroke: '#38bdf8', 'stroke-width': 1.4, opacity: .5, class: 'flow',
      'marker-end': 'url(#ar)',
    }));
  }

  // 그 밖의 시설
  for (const it of facilityOf(fid).filter(i => i.kind !== 'crac')) {
    const s = FAC_STYLE[it.kind] || FAC_STYLE.facility;
    root.appendChild(planBox(it, it.pos[0] * CELL, it.pos[1] * CELL,
      s.w * CELL, s.d * CELL, facilityDown(it) ? '#7f1d2b' : s.c, s.t || it.id));
  }

  // PDU → 랙 급전선
  for (const p of (LAYOUT?.facility?.pdu || []).filter(p => p.floor === fid)) {
    const r = racksOf(fid).find(r => r.id === p.rack);
    if (!r || !p.pos) continue;
    root.appendChild(el('line', {
      x1: (p.pos[0] + .25) * CELL, y1: (p.pos[1] + .25) * CELL,
      x2: (r.pos[0] + .2) * CELL, y2: (r.pos[1] + .4) * CELL,
      stroke: '#fbbf24', 'stroke-width': 1.2, opacity: .45, 'stroke-dasharray': '3 3',
    }));
  }

  // 랙 — 평면도에는 발자국만. 안에 뭐가 들었는지는 오른쪽 입면도에서 본다.
  for (const r of racksOf(fid)) root.appendChild(planRackFootprint(r));

  // 근무자
  crewOf(fid).forEach((w, i, arr) => {
    const x = (1 + i * (Math.min(9, GW - 2) / Math.max(arr.length, 1))) * CELL;
    root.appendChild(planCrew(w, x, D - CELL * .7));
  });

  // 랙 입면도 — 실제 DC 문서가 평면도와 함께 늘 갖고 다니는 그림이다.
  // 존 색 띠가 세로로 쌓이므로 "한 랙 안에 여러 존이 섞여 있다"가 눈에 바로 들어온다.
  const elev = el('g', { transform: `translate(${W + 90},0)` });
  let ey = 0;
  for (const r of racksOf(fid)) {
    const g = rackElevation(r, ey);
    elev.appendChild(g);
    ey += rackElevHeight(r) + 30;
  }
  // 이 층에 있으나 랙에 들지 않은 자산(원격 등)
  const orphan = assetsOf(fid).filter(a => !a.rack);
  if (orphan.length) {
    elev.appendChild(el('text', {
      x: 0, y: ey + 12, 'font-size': 10, class: 'mono', fill: '#67809a', text: '랙 외 자산',
    }));
    orphan.forEach((a, i) => elev.appendChild(planAsset(a, 0, ey + 20 + i * 22, 210, 20)));
  }
  root.appendChild(elev);

  root.appendChild(el('defs', {}, [
    el('marker', {
      id: 'ar', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 5, markerHeight: 5,
      orient: 'auto',
    }, [el('path', { d: 'M0,0 L8,4 L0,8 z', fill: '#38bdf8' })]),
  ]));

  fitView(svg, root, 34);
}

function planBox(item, x, y, w, h, color, label) {
  const down = facilityDown(item);
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openFacility(item); } },
  });
  g.appendChild(el('rect', {
    x, y, width: w, height: h, rx: 3, fill: color, opacity: down ? .85 : .55,
    stroke: down ? '#ff4d6a' : shade(color, 1), 'stroke-width': down ? 1.6 : 1,
    class: down ? 'hl blink' : 'hl',
  }));
  g.appendChild(el('text', {
    x: x + w / 2, y: y + h / 2 + 3.5, 'text-anchor': 'middle', 'font-size': 9.5,
    class: 'mono', 'font-weight': 700, fill: '#06111c', text: label,
  }));
  g.appendChild(el('title', { text: `${item.name || item.id} (${item.id})` }));
  return g;
}

/** 평면도의 랙 — 캐비닛 발자국. 앞면(콜드아일 쪽)을 밝은 띠로 표시한다. */
function planRackFootprint(rack) {
  const x = rack.pos[0] * CELL, y = rack.pos[1] * CELL;
  const W = CELL * 2.2, H = CELL * .62;
  const aisle = ST?.aisles?.[rack.aisle];
  const kw = (LAYOUT?.it_assets || []).filter(a => a.rack === rack.id)
    .reduce((s, a) => s + assetState(a.id).kw, 0);
  const over = kw > rack.design_kw;
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openRack(rack.id); } },
  });
  g.appendChild(el('rect', {
    x, y, width: W, height: H, rx: 3, fill: '#111c2a',
    stroke: over ? '#ff4d6a' : aisle ? tempColor(aisle.temp_c) : '#2b3d55',
    'stroke-width': 1.5, class: 'hl',
  }));
  g.appendChild(el('rect', { x, y, width: W, height: 4, rx: 2, fill: '#38bdf8', opacity: .6 }));
  g.appendChild(el('text', {
    x: x + 7, y: y - 6, 'font-size': 10, class: 'mono', fill: '#67809a',
    text: `${rack.id} · ${rack.aisle} 아일 · ${rack.u}U`,
  }));
  g.appendChild(el('text', {
    x: x + W / 2, y: y + H / 2 + 5, 'text-anchor': 'middle', 'font-size': 11,
    class: 'mono', fill: over ? '#ff4d6a' : '#8fa5bd',
    text: `${kw.toFixed(1)} / ${rack.design_kw} kW`,
  }));
  g.appendChild(el('title', { text: `${rack.id}\n클릭하면 탑재 자산을 봅니다` }));
  return g;
}

const ELEV_ROW = 21, ELEV_W = 214;
const rackElevHeight = r =>
  26 + (LAYOUT?.it_assets || []).filter(a => a.rack === r.id).length * ELEV_ROW + 8;

/** 랙 입면도 — U 를 위에서 아래로 쌓는다. 왼쪽 존 색 띠가 세로로 정렬된다. */
function rackElevation(rack, y0) {
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === rack.id);
  const H = rackElevHeight(rack);
  const aisle = ST?.aisles?.[rack.aisle];
  const g = el('g');
  g.appendChild(el('rect', {
    x: 0, y: y0, width: ELEV_W, height: H, rx: 5, fill: '#0b1420',
    stroke: aisle ? tempColor(aisle.temp_c) : '#2b3d55', 'stroke-width': 1.4,
  }));
  g.appendChild(el('text', {
    x: 9, y: y0 + 16, 'font-size': 10.5, class: 'mono', fill: '#8fa5bd',
    text: `${rack.id}  ${aisle ? `${aisle.temp_c}°C` : ''}`,
  }));
  list.forEach((a, i) => g.appendChild(
    planAsset(a, 7, y0 + 24 + i * ELEV_ROW, ELEV_W - 14, ELEV_ROW - 3)));
  return g;
}

/** 자산 한 칸 — 왼쪽 존 색 띠, 이름, 실측 사용률 바, 전력. */
function planAsset(a, x, y, w, h = 15) {
  const st = assetState(a.id), up = alive(a);
  const g = el('g', {
    class: 'hit', on: { click: e => { e.stopPropagation(); openAsset(a.id); } },
  });
  g.appendChild(el('rect', {
    x, y, width: w, height: h, rx: 2, fill: up ? '#16222f' : '#2a1119',
    stroke: up ? 'rgba(120,160,200,.18)' : '#ff4d6a', 'stroke-width': 1, class: 'hl',
  }));
  g.appendChild(el('rect', {                       // 존 색 띠
    x, y, width: 4, height: h, rx: 1, fill: zoneColor(a.zone),
  }));
  g.appendChild(el('rect', {                       // 실측 사용률
    x: x + 6, y: y + h - 3.5, width: (w - 12) * Math.min(st.util, 1), height: 2,
    rx: 1, fill: st.util > .7 ? '#ff4d6a' : st.util > .4 ? '#fbbf24' : '#22d3ee',
  }));
  g.appendChild(el('text', {
    x: x + 9, y: y + h / 2 + 1, 'font-size': 9.5,
    fill: up ? '#c8d6e6' : '#ff9fb0', text: a.name,
  }));
  g.appendChild(el('text', {
    x: x + w - 5, y: y + h / 2 + 1, 'text-anchor': 'end', 'font-size': 9,
    class: 'mono', fill: '#67809a', text: `${st.kw.toFixed(2)}kW`,
  }));
  g.appendChild(el('title', {
    text: `${a.name} (${a.id})\n존 ${a.zone}${a.logical_zone ? ` · 권한 ${a.logical_zone}` : ''}`
      + `\n${a.ip || ''} ${a.container || a.remote || ''}`
      + `\n${up ? '가동 중' : '⚠ 정지'} · 사용률 ${(st.util * 100).toFixed(0)}%`,
  }));
  return g;
}

function planCrew(w, x, y) {
  const rt = RT_COLOR[w.runtime] || '#94a3b8';
  const vest = w.autonomy === 'L3' ? '#ff4d6a'
    : w.autonomy === 'approver' ? '#3ddc97'
    : w.autonomy === 'L2' ? '#38bdf8' : '#475569';
  const g = el('g', {
    class: 'hit', transform: `translate(${x},${y})`, 'shape-rendering': 'crispEdges',
    on: { click: e => { e.stopPropagation(); openCrew(w.id); } },
  });
  g.appendChild(el('ellipse', { cx: 0, cy: 2, rx: 8, ry: 3, fill: 'rgba(0,0,0,.45)' }));
  g.appendChild(el('rect', { x: -5, y: -28, width: 10, height: 7, fill: rt, class: 'hl' }));
  g.appendChild(el('rect', { x: -4, y: -21, width: 8, height: 5, fill: '#e2c9a8' }));
  g.appendChild(el('rect', { x: -6, y: -16, width: 12, height: 11, fill: vest }));
  g.appendChild(el('rect', { x: -5, y: -5, width: 4, height: 5, fill: '#1e293b' }));
  g.appendChild(el('rect', { x: 1, y: -5, width: 4, height: 5, fill: '#1e293b' }));
  g.appendChild(el('text', {
    x: 0, y: 15, 'text-anchor': 'middle', 'font-size': 9, fill: '#8fa5bd', text: w.name,
  }));
  g.appendChild(el('title', { text: `${w.name}\n${w.runtime} · ${w.autonomy}` }));
  return g;
}

/* ── 뷰 맞춤 ─────────────────────────────────────────────────────── */
function fitView(svg, root, pad) {
  const b = root.getBBox();
  svg.setAttribute('viewBox',
    `${b.x - pad} ${b.y - pad} ${b.width + pad * 2} ${b.height + pad * 2}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
}

function render() {
  if (!LAYOUT) return;
  if (VIEW.mode === 'floor' && VIEW.floor) drawFloor(VIEW.floor);
  else drawBuilding();
  renderCrumbs();
}

function enterFloor(fid) {
  VIEW = { mode: 'floor', floor: fid };
  $('#stage-hint').textContent = '자산·시설·근무자를 클릭하면 상세와 접속 수단이 열립니다';
  render();
}
function enterBuilding() {
  VIEW = { mode: 'building', floor: null };
  $('#stage-hint').textContent = '층을 클릭하면 전산실 내부로 들어갑니다';
  render();
}
function renderCrumbs() {
  const c = $('#crumbs');
  c.replaceChildren();
  const b = document.createElement('button');
  b.className = 'crumb' + (VIEW.mode === 'building' ? ' active' : '');
  b.textContent = '건물';
  b.onclick = enterBuilding;
  c.appendChild(b);
  floors().forEach(f => {
    const x = document.createElement('button');
    x.className = 'crumb' + (VIEW.floor === f.id ? ' active' : '');
    x.textContent = `${f.id} ${f.name}`;
    x.onclick = () => enterFloor(f.id);
    c.appendChild(x);
  });
}

/* ════════════════════════════════════════════════════════════════
 *  우측 레일
 * ════════════════════════════════════════════════════════════════ */
function renderPower() {
  const p = ST?.power;
  if (!p) return;
  const pane = $('#pane-power');
  const pdus = LAYOUT?.facility?.pdu || [];

  const src = !p.utility_ok
    ? (p.generator_running ? ['발전기', 'on'] : ['UPS 배터리', 'off'])
    : ['상용전원', 'on'];

  pane.innerHTML = `
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

function renderCrew() {
  const pane = $('#pane-crew');
  const ws = ROSTER.workers || [];
  if (!ws.length) { pane.innerHTML = '<div class="empty">근무자 명단을 읽지 못했습니다</div>'; return; }
  pane.innerHTML = floors().map(f => {
    const list = ws.filter(w => w.floor === f.id);
    if (!list.length) return '';
    return `<h4 style="margin:4px 0 8px;font-size:11px;color:#67809a">${f.id} ${f.name}</h4>`
      + list.map(w => `
      <div class="crew" data-crew="${w.id}">
        <div class="top"><span class="nm">${w.name}</span>
          <span class="tag rt-${w.runtime}">${w.runtime}</span></div>
        <div class="meta">
          <span class="tag au-${w.autonomy}">${w.autonomy}</span>
          <span class="tag" style="color:${zoneColor(w.zone)};border-color:${zoneColor(w.zone)}88">${w.zone}</span>
          ${(w.curriculum || []).map(c => `<span class="tag">${c}</span>`).join('')}
        </div>
        <div class="loops">${(w.loop_detail || []).length
          ? w.loop_detail.map(l => `· ${l.name} <span style="color:#41566d">${l.cadence || ''}${
              l.runbook ? ' · 런북 있음' : ''}</span>`).join('<br>')
          : '<span style="color:#41566d">등록된 루프 없음</span>'}</div>
      </div>`).join('');
  }).join('');
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

function renderLog() {
  const pane = $('#pane-log');
  if (!EVENTS.length) { pane.innerHTML = '<div class="empty">이벤트 없음</div>'; return; }
  pane.innerHTML = EVENTS.slice().reverse().map(e => {
    const d = new Date(e.ts * 1000);
    const t = [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map(x => String(x).padStart(2, '0')).join(':');
    return `<div class="logline k-${e.kind}"><span class="ts">${t}</span><span>${e.msg}</span></div>`;
  }).join('');
}

/* ── 존 범례 + 체인 ─────────────────────────────────────────────── */
function renderZones() {
  $('#zone-legend').innerHTML = (LAYOUT?.zones || []).map(z =>
    `<span title="${(z.role || '').replace(/"/g, '')}">
      <i style="background:${z.color}"></i>${z.id}<span style="color:#41566d"> ${z.trust}${
        z.logical ? ' 논리' : ''}</span></span>`).join('');

  const chain = LAYOUT?.zone_chain || [];
  const node = id => {
    const z = zoneOf(id) || { id, name: id };
    return `<div class="node" style="border-color:${z.color}66">
      <b style="color:${z.color}">${z.id} ${z.name}</b>
      <small>${z.cidr || '세그먼트 없음'}</small></div>`;
  };
  // 주경로(ext→pipe→dmz→int)를 한 줄로, 나머지 분기는 그 뒤에 붙인다
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
    html += `<div class="hop"><span class="arrow">┬</span>ips</div><div class="branch">`;
    html += branches.map(c => node(c.to)).join('');
    html += `</div>`;
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
  set('#kpi-temp', hot == null ? '—' : `${hot.toFixed(1)}°C`, 'ASHRAE 권고 18~27°C',
    hot > 32 ? 'crit' : hot > 27 ? 'warn' : '');
  set('#kpi-alarm', String(alarms.length), crit ? `L12 이상 ${crit}건` : '심각 없음',
    crit ? 'crit' : alarms.length ? 'warn' : '');
  set('#kpi-cont', `${up}/${assets.length}`, '컨테이너·원격 실측',
    up < assets.length ? 'warn' : '');

  $('#bld-name').textContent = ST.building || 'kt66';

  // 배속이 걸려 있으면 숨기지 않는다 — 화면의 시간과 벽시계가 다르다는 사실을 학생이 알아야 한다
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
  if (zoneId) {
    z.textContent = zoneId; z.style.color = zoneColor(zoneId); z.hidden = false;
  } else z.hidden = true;
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

function openRack(id) {
  const r = (LAYOUT?.racks || []).find(x => x.id === id);
  if (!r) return;
  const list = (LAYOUT?.it_assets || []).filter(a => a.rack === id);
  const a = ST?.aisles?.[r.aisle];
  const kw = list.reduce((s, x) => s + assetState(x.id).kw, 0);
  showDrawer(r.id, null, `
    ${kv('층 · 아일', `${r.floor} · ${r.aisle}`)}
    ${kv('용량', `${r.u}U · 설계 ${r.design_kw}kW`)}
    ${kv('현재 부하', `${kw.toFixed(2)} kW (${(kw / r.design_kw * 100).toFixed(0)}%)`)}
    ${a ? kv('아일 온습도', `${a.temp_c}°C · ${a.humidity_pct}%RH`) : ''}
    ${a ? kv('냉방', `${a.cooling_kw}kW ${a.cooling_kw < a.it_kw ? '— 부족' : ''}`) : ''}
    <h4 style="margin:14px 0 6px;font-size:11px;color:#67809a">탑재 자산 ${list.length}</h4>
    ${list.map(x => `<div class="kv" style="cursor:pointer" data-a="${x.id}">
      <span class="k"><i style="display:inline-block;width:7px;height:7px;border-radius:2px;
        background:${zoneColor(x.zone)};margin-right:6px"></i>${x.name}</span>
      <span class="v">${assetState(x.id).kw.toFixed(2)}kW</span></div>`).join('')}
    <div class="dim" style="margin-top:10px">같은 아일의 랙끼리는 열이 섞인다.
      한 랙의 폭주가 옆 랙 온도를 올린다 — 핫/콜드 아일 실습의 근거다.</div>`);
  $$('#dr-body [data-a]').forEach(n => n.onclick = () => openAsset(n.dataset.a));
}

function openFacility(item) {
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
    <h4 style="margin:14px 0 6px;font-size:11px;color:#67809a">루프 ${(w.loop_detail || []).length}</h4>
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
 *  UPS 절체 판단 패널
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
    // 자산이 하나도 배치되지 않은 그룹은 끊어도 아무 일이 없다. 0.0kW 만 보여주면
    // 학생이 "차단했는데 왜 안 줄지?" 로 헤맨다 — 비어 있다고 말해 준다.
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

  // 시간 배속. 유휴 랩은 발열이 5kW 남짓이라 냉동기를 죽여도 분당 0.2°C 밖에 안 오른다.
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
    const [st, ev] = await Promise.all([get('/api/state'), get('/api/events?limit=60')]);
    ST = st; EVENTS = ev.events || [];
    $('#link-status').classList.remove('down');
  } catch (e) {
    $('#link-status').classList.add('down');
    $('#link-status').title = String(e);
    return;
  }
  renderKpis(); renderPower(); renderAlarms(); renderLog(); renderUps();
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
  renderZones(); renderCrew(); renderCrumbs();
  await poll();
  setInterval(poll, 3000);
  setInterval(() => {
    $('#clock').textContent = new Date().toLocaleTimeString('ko-KR', { hour12: false });
  }, 1000);
}

/* ── 이벤트 배선 ─────────────────────────────────────────────────── */
$$('#tabs .tab').forEach(t => t.onclick = () => {
  $$('#tabs .tab').forEach(x => x.classList.toggle('active', x === t));
  $$('.pane').forEach(p => p.classList.toggle('active', p.id === `pane-${t.dataset.tab}`));
});
$('#dr-close').onclick = () => { $('#drawer').hidden = true; SELECTED = null; };
$('#ups-close').onclick = () => { upsDismissed = true; $('#ups-modal').hidden = true; };
$('#btn-instructor').onclick = () => { renderInjector(); $('#inj-modal').hidden = false; };
$('#inj-close').onclick = () => $('#inj-modal').hidden = true;
$('#btn-reset').onclick = async () => {
  await post('/api/reset', {});
  upsDismissed = false;
  await poll(); renderInjector();
};
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    $('#drawer').hidden = true; SELECTED = null;
    $('#inj-modal').hidden = true;
    if (VIEW.mode === 'floor') enterBuilding();
  }
});
window.addEventListener('resize', () => render());

boot();
