/* kt66 모델 운영 — 화면.
 *
 * 화면은 상태를 만들지 않는다. /api/state 가 준 것만 그린다. 지표를 화면에서
 * 다시 계산하기 시작하면 학생이 보는 숫자와 채점이 보는 숫자가 갈라진다. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let ST = null;

const key = () => $('#key').value.trim();
const H = () => ({ 'content-type': 'application/json', 'x-api-key': key() });

function err(msg) {
  const b = $('#errbar');
  if (!msg) { b.hidden = true; return; }
  b.hidden = false; b.textContent = msg;
  setTimeout(() => { b.hidden = true; }, 9000);
}

async function api(path, opt = {}) {
  const r = await fetch(path, { headers: H(), ...opt });
  const t = await r.text();
  let d; try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error(typeof d === 'string' ? d : (d.detail || r.status));
  return d;
}

/* ── 단계 ─────────────────────────────────────────────── */
$$('.step').forEach(b => b.onclick = () => {
  $$('.step').forEach(x => x.classList.toggle('on', x === b));
  $$('.panel').forEach(p => p.classList.toggle('on', p.dataset.panel === b.dataset.step));
});

/* ── 1. 요청 ──────────────────────────────────────────── */
function drawReqs() {
  const flags = r => {
    const f = [];
    if (r.truncated) f.push('입력 잘림');
    if (!r.retrieved && !r.refused) f.push('근거 없음');
    if (r.refused && !r.must_refuse) f.push('과잉 거부');
    if (r.must_refuse && !r.refused) f.push('⚠ 막았어야 했다');
    return f;
  };
  $('#reqs').innerHTML = ST.recent.map(r => {
    const f = flags(r);
    return `<div class="rq ${r.ok ? 'good' : 'bad'}">
      <span class="who">${r.persona}</span>
      <span class="tag2">${r.version}</span>
      <span class="p" title="${esc(r.prompt)}">${esc(r.prompt)}</span>
      <span class="m">${r.latency_ms}ms</span>
      ${f.length ? `<span class="flags">${f.join(' · ')}</span>` : ''}
    </div>`;
  }).join('') || '<p class="muted">아직 요청이 없습니다.</p>';
}

const esc = s => (s || '').replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));

$('#ask-go').onclick = async () => {
  const p = $('#ask-in').value.trim();
  if (!p) return;
  try {
    const o = await api('/api/ask', { method: 'POST', body: JSON.stringify({ prompt: p }) });
    const box = $('#ask-out'); box.hidden = false;
    box.textContent = `${o.text}\n\n— ${o.latency_ms}ms · ${o.backend}`
      + (o.refused ? ` · 거부(${o.refuse_pattern})` : '')
      + (o.truncated ? ` · 입력 ${o.dropped_chars}자 잘림` : '')
      + (o.retrieved_chars ? ` · 사내지식 ${o.retrieved_chars}자` : ' · 근거 없음');
    refresh();
  } catch (e) { err(String(e.message)); }
};

/* ── 2. 지표 ──────────────────────────────────────────── */
function drawMetrics() {
  const m = ST.metrics;
  if (!m.n) { $('#gauges').innerHTML = '<p class="muted">표본이 없습니다.</p>'; return; }
  const G = (k, v, note, cls) =>
    `<div class="g ${cls || ''}"><div class="k">${k}</div><div class="v">${v}</div>
     <div class="n">${note}</div></div>`;
  $('#gauges').innerHTML =
      G('성공률', m.ok_rate + '%', `표본 ${m.n}건 · 최근 ${ST.window}초`,
        m.ok_rate < 60 ? 'bad' : m.ok_rate < 85 ? 'warn' : 'ok')
    + G('p95 지연', m.p95 + 'ms', `p50 ${m.p50}ms`,
        m.p95 > 2500 ? 'bad' : m.p95 > 1500 ? 'warn' : 'ok')
    + G('입력 잘림', m.truncated, 'context_tokens 가 작다는 신호',
        m.truncated > 5 ? 'bad' : m.truncated ? 'warn' : 'ok')
    + G('근거 없는 답', m.ungrounded, 'retrieval 이 꺼져 있는가',
        m.ungrounded > 10 ? 'bad' : m.ungrounded ? 'warn' : 'ok')
    + G('과잉 거부', m.over_refuse, '정상 질문을 막았다',
        m.over_refuse > 3 ? 'bad' : m.over_refuse ? 'warn' : 'ok')
    + G('유출', m.leak, '막아야 할 것을 놓쳤다 — 가장 나쁘다',
        m.leak ? 'bad' : 'ok');

  const rows = ST.by_version.map(v => `<tr class="${v.version === ST.active ? 'live' : ''}">
      <td>${v.version}${v.version === ST.active ? ' (운영)' : ''}</td>
      <td>${v.n}</td><td>${v.ok_rate}%</td><td>${v.p50}</td><td>${v.p95}</td>
      <td>${v.truncated}</td><td>${v.ungrounded}</td><td>${v.over_refuse}</td>
      <td>${v.leak}</td></tr>`).join('');
  $('#byver').innerHTML = `<table><thead><tr><th>버전</th><th>표본</th><th>성공률</th>
    <th>p50</th><th>p95</th><th>잘림</th><th>근거없음</th><th>과잉거부</th><th>유출</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

/* ── 3. 티켓 ──────────────────────────────────────────── */
function drawTickets() {
  $('#tickets').innerHTML = ST.tickets.map(t => `
    <div class="card" style="opacity:${t.state === 'closed' ? .5 : 1}">
      <div class="t">${esc(t.title)}
        <span class="tag ${t.state === 'open' ? 'goal' : ''}">${t.state === 'open' ? '열림' : '닫힘'}</span></div>
      <div class="s">${esc(t.body)}</div>
      <div style="margin-top:8px">${(t.hint || []).map(h =>
        `<span class="tag shared">${h}</span>`).join(' ')}</div>
      ${t.state === 'open'
        ? `<div style="margin-top:10px"><button data-close="${t.id}">해결 처리</button></div>` : ''}
    </div>`).join('') || '<p class="muted">아직 티켓이 없습니다. 지표를 먼저 보세요.</p>';
  $$('[data-close]').forEach(b => b.onclick = async () => {
    try {
      await api(`/api/ticket/${b.dataset.close}/close`, { method: 'POST', body: '{}' });
      refresh();
    } catch (e) { err(String(e.message)); }
  });
}

/* ── 4. 수정 ──────────────────────────────────────────── */
function fillVers() {
  const cur = ST.versions.map(v => `<option ${v === ST.active ? 'selected' : ''}>${v}</option>`).join('');
  const keep = [$('#src-ver').value, $('#eval-ver').value];
  $('#src-ver').innerHTML = cur; $('#eval-ver').innerHTML = cur;
  if (keep[0] && ST.versions.includes(keep[0])) $('#src-ver').value = keep[0];
  if (keep[1] && ST.versions.includes(keep[1])) $('#eval-ver').value = keep[1];
}

$('#load-ver').onclick = async () => {
  try {
    const v = await api(`/api/version/${$('#src-ver').value}`);
    // 화면에는 사람이 읽는 순서로 늘어놓는다. 저장할 때 서버가 다시 검사한다.
    $('#ed-man').value = Object.entries(v)
      .filter(([k]) => !['version', 'knowledge'].includes(k))
      .map(([k, val]) => `${k}: ${JSON.stringify(val, null, 0)}`).join('\n');
    $('#ed-know').value = v.knowledge || '';
    $('[data-msg="save"]').textContent = `${v.version} 불러옴 — 새 이름으로 저장하세요`;
    $('[data-msg="save"]').className = 'msg ok';
  } catch (e) { err(String(e.message)); }
};

$('#save-ver').onclick = async () => {
  const name = $('#new-ver').value.trim();
  const m = $('[data-msg="save"]');
  if (!name) { m.className = 'msg bad'; m.textContent = '새 버전 이름을 적으세요'; return; }
  let manifest = {};
  try {
    for (const line of $('#ed-man').value.split('\n')) {
      const i = line.indexOf(':');
      if (i < 1) continue;
      manifest[line.slice(0, i).trim()] = JSON.parse(line.slice(i + 1).trim());
    }
  } catch (e) {
    m.className = 'msg bad';
    m.textContent = '값은 JSON 표기로 씁니다 — 문자열은 "따옴표", 참/거짓은 true/false';
    return;
  }
  try {
    await api(`/api/version/${name}`, {
      method: 'POST',
      body: JSON.stringify({ manifest, knowledge: $('#ed-know').value })
    });
    m.className = 'msg ok'; m.textContent = `${name} 저장됨 — 5단계에서 평가 후 배포`;
    refresh();
  } catch (e) { m.className = 'msg bad'; m.textContent = String(e.message); }
};

/* ── 5. 평가·배포 ─────────────────────────────────────── */
$('#run-eval').onclick = async () => {
  try {
    const r = await api(`/api/eval/${$('#eval-ver').value}`, { method: 'POST' });
    $('#evalout').innerHTML = `<div class="card"><div class="t">${r.version} — ${r.passed}/${r.total} 통과</div>
      <div class="s">전부 통과하는 설정은 없습니다. 무엇을 포기했는지 말할 수 있으면 됩니다.</div></div>`
      + r.items.map(i => `<div class="card ev ${i.passed ? 'pass' : 'fail'}">
          <div class="t">${i.id} · ${i.kind} <span class="tag ${i.passed ? 'floor' : ''}">
            ${i.passed ? '통과' : '실패'}</span></div>
          <div class="s">${esc(i.why)}</div>
          <div class="s" style="margin-top:6px;color:var(--ink)">${esc(i.detail)}</div>
        </div>`).join('');
  } catch (e) { err(String(e.message)); }
};

$('#do-deploy').onclick = async () => {
  const v = $('#eval-ver').value;
  const m = $('[data-msg="deploy"]');
  try {
    await api(`/api/deploy/${v}`, { method: 'POST' });
    m.className = 'msg ok'; m.textContent = `${v} 배포됨 — 지표는 다음 창에서 갈립니다`;
    refresh();
  } catch (e) { m.className = 'msg bad'; m.textContent = String(e.message); }
};

/* ── 폴링 ─────────────────────────────────────────────── */
async function refresh() {
  try {
    ST = await api('/api/state');
    $('#active').textContent = '운영 중: ' + (ST.active || '없음');
    $('#active').className = 'pill ok';
    drawReqs(); drawMetrics(); drawTickets(); fillVers();
  } catch (e) { err('상태 조회 실패: ' + e.message); }
}

(async () => {
  try {
    const h = await (await fetch('/health')).json();
    $('#backend').textContent = h.backend === 'ollama' ? '실모델(Ollama)' : '모의 백엔드';
    $('#backend').className = 'pill' + (h.backend === 'ollama' ? ' ok' : '');
  } catch { }
  await refresh();
  setInterval(refresh, 5000);
})();
