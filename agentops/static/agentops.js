/* kt66 에이전트 운영 콘솔.
 *
 * 화면은 상태를 만들지 않는다. agents/ 의 파일이 진실원천이고 여기는 비추기만 한다
 * (관제 화면과 같은 규칙이다). 저장은 서버가 검증하고, 화면은 그 결과를 그대로 보여준다 —
 * 화면이 자체 검증을 흉내내면 서버와 갈리는 순간 학생이 무엇을 믿을지 알 수 없게 된다.
 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const KEY = () => $('#key').value.trim();
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let ORG = null;

async function api(path, opt = {}) {
  const o = { headers: { 'content-type': 'application/json' }, ...opt };
  if (opt.body !== undefined) o.body = JSON.stringify(opt.body);
  const r = await fetch(path, o);
  const t = await r.text();
  let d; try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error(typeof d === 'object' ? (d.detail || JSON.stringify(d)) : d);
  return d;
}
const withKey = p => p + (p.includes('?') ? '&' : '?') + 'key=' + encodeURIComponent(KEY());

/* ── 단계 전환 ──────────────────────────────────────────── */
$$('.step').forEach(b => b.onclick = () => {
  $$('.step').forEach(x => x.classList.toggle('on', x === b));
  $$('.panel').forEach(p => p.classList.toggle('on', p.dataset.panel === b.dataset.step));
  if (b.dataset.step === '6') loadBackups();
});

/* ── 로드 ───────────────────────────────────────────────── */
async function load() {
  ORG = await api('/api/org');
  renderCompany(); renderDepts(); renderTeams(); renderGraph();
  renderWorkers(); renderHarness(); renderLoops(); renderErrors();
  for (const n of ['company', 'departments', 'teams', 'harness'])
    api('/api/file/' + n).then(t => { const e = $('#ed-' + n); if (e) e.value = t; }).catch(() => {});
  api('/api/file/graph').then(t => $('#ed-graph').value = t).catch(() => {});
}
function renderErrors() {
  const bar = $('#errbar'), errs = ORG.errors || [];
  bar.hidden = !errs.length;
  bar.textContent = errs.length ? `조직 정합성 오류 ${errs.length}건\n` +
    errs.map(e => '· ' + e).join('\n') : '';
  const h = $('#health');
  h.className = 'pill ' + (errs.length ? 'bad' : 'ok');
  h.textContent = `근무자 ${ORG.roster.workers?.length || 0} · 팀 ${ORG.teams.teams?.length || 0}` +
    (errs.length ? ` · 오류 ${errs.length}` : '');
}

/* ── 1. 회사 ────────────────────────────────────────────── */
function renderCompany() {
  const c = ORG.company.company || {};
  $('#company-view').innerHTML = `
    <div class="card" style="grid-column:1/-1">
      <div class="t">비전</div><div class="s" style="font-size:14px;color:var(--ink)">${esc(c.vision)}</div>
    </div>
    <div class="card" style="grid-column:1/-1">
      <div class="t">충돌 시의 순서</div>
      <ol class="prio">${(c.priority_order || []).map(p => `<li>${esc(p)}</li>`).join('')}</ol>
      <div class="s" style="margin-top:8px">${esc(c.priority_note)}</div>
    </div>
    ${(c.goals || []).map(g => `<div class="card">
      <div class="t"><span class="tag goal">${esc(g.id)}</span> ${esc(g.name)}</div>
      <div class="s"><code>${esc(g.metric)}</code> → <b>${esc(g.target)}</b></div>
      <div class="s" style="margin-top:6px">${esc(g.why)}</div></div>`).join('')}
    <div class="card" style="grid-column:1/-1"><div class="t">운영 원칙</div>
      <ul>${(c.operating_principles || []).map(p =>
        `<li><b>${esc(p.id)}</b> ${esc(p.rule)} <span class="tag">${esc(p.from)}</span></li>`).join('')}</ul>
    </div>`;
  $('#company-levers').innerHTML = (c.levers || []).map(l => `<li>${esc(l)}</li>`).join('');
}

/* ── 2. 조직 ────────────────────────────────────────────── */
function renderDepts() {
  $('#dept-view').innerHTML = (ORG.departments.departments || []).map(d => `
    <div class="card">
      <div class="t">${esc(d.name)} <span class="tag floor">${esc(d.floor)}</span>
        ${(d.owns_goals || []).map(g => `<span class="tag goal">${esc(g)}</span>`).join('')}</div>
      <div class="s">${esc(d.mission)}</div>
      <div class="notjob"><b>하지 않는 일</b> — ${esc(d.not_our_job)}</div>
      <div class="s" style="margin-top:8px">팀: ${(d.teams || []).map(esc).join(', ')}</div>
      <div class="s">에스컬레이션 → ${esc(d.escalates_to || '사람')}</div>
    </div>`).join('');
}

/* ── 3. 팀 + 경험그래프 ─────────────────────────────────── */
function renderTeams() {
  $('#team-view').innerHTML = (ORG.teams.teams || []).map(t => `
    <div class="card">
      <div class="t">${esc(t.name)} <span class="tag">${esc(t.department)}</span></div>
      <div class="s">${(t.members || []).map(esc).join(', ') || '멤버 없음'}</div>
      <ul>${(t.kpi || []).map(k => `<li>
        ${k.shared_with ? '<span class="tag shared">공유</span> ' : ''}
        <code>${esc(k.metric)}</code> ${esc(k.target)}
        <span class="tag">w${esc(k.weight)}</span>
        ${k.why ? `<br><span class="s">${esc(k.why)}</span>` : ''}</li>`).join('')}</ul>
    </div>`).join('');
}
function renderGraph() {
  const g = ORG.graph, byId = Object.fromEntries((g.nodes || []).map(n => [n.id, n]));
  const kinds = {};
  (g.nodes || []).forEach(n => (kinds[n.kind] ||= []).push(n));
  const lab = id => {
    const n = byId[id];
    return n ? `<span class="gnode k-${n.kind}">${esc(n.label)}</span>`
             : `<span class="gnode" style="border-color:var(--bad)">${esc(id)}?</span>`;
  };
  $('#graph-view').innerHTML =
    Object.entries(kinds).map(([k, ns]) =>
      `<div style="margin-bottom:8px"><span class="s" style="color:var(--dim)">${esc(k)}</span><br>
       ${ns.map(n => `<span class="gnode k-${k}">${esc(n.label)}</span>`).join('')}</div>`).join('') +
    `<h3 style="margin-top:14px">연결 ${(g.edges || []).length}개 — 전부 출처를 갖는다</h3>` +
    (g.edges || []).map(e => `<div class="edge">${lab(e.from)}
      <span class="etype">${esc(e.type)}</span> ${lab(e.to)}
      <span class="src">${esc(e.source)}${e.confidence != null ? ` · ${e.confidence}` : ''}</span>
      ${e.note ? `<div class="s" style="flex-basis:100%">${esc(e.note)}</div>` : ''}</div>`).join('') +
    (g.open_questions?.length ? `<h3 style="margin-top:14px">아직 모르는 것</h3>
      <ul style="margin:0;padding-left:18px">${g.open_questions.map(q =>
        `<li class="s">${esc(q)}</li>`).join('')}</ul>` : '');
}

/* ── 4. 근무자 ──────────────────────────────────────────── */
function renderWorkers() {
  const r = ORG.roster, models = r.models || {}, runtimes = r.runtimes || {};
  const teams = (ORG.teams.teams || []);
  const kpiOf = tid => (teams.find(t => t.id === tid)?.kpi || []).map(k => k.metric).join(', ');
  const opt = (o, cur) => Object.entries(o).map(([k, v]) =>
    `<option value="${esc(k)}" ${k === cur ? 'selected' : ''}>${esc(v.name || k)}</option>`).join('');
  $('#worker-view').innerHTML = (r.workers || []).map(w => `
    <div class="wk" data-w="${esc(w.id)}">
      <div class="t"><span>${esc(w.name)}</span>
        <span><span class="tag floor">${esc(w.floor)}</span>
          <span class="tag">${esc(w.zone)}</span></span></div>
      <div class="s" style="font-size:12px;color:var(--dim)">${esc(w.id)}</div>
      <div class="row"><label>런타임</label>
        <select data-f="runtime">${opt(runtimes, w.runtime)}</select></div>
      <div class="row"><label>모델</label>
        <select data-f="model">${opt(models, w.model)}</select></div>
      <div class="row"><label>자율성</label>
        <select data-f="autonomy">${['L1', 'L2', 'L3', 'approver'].map(a =>
          `<option ${a === w.autonomy ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="row"><label>팀</label>
        <select data-f="team">${teams.map(t =>
          `<option value="${esc(t.id)}" ${t.id === w.team ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></div>
      <div class="s" style="margin-top:7px">${esc(runtimes[w.runtime]?.note || '')}</div>
      <div class="s">${esc(models[w.model]?.note || '')}</div>
      <div class="kpi">KPI: ${esc(kpiOf(w.team)) || '—'}<br>
        루프: ${(w.loops || []).map(esc).join(', ') || '없음'}</div>
      <div class="row" style="margin-top:9px">
        <button data-persona="${esc(w.id)}">페르소나 편집</button>
        <button class="danger" data-del="${esc(w.id)}">삭제</button></div>
    </div>`).join('');

  $$('.wk select').forEach(sel => sel.onchange = async () => {
    const wid = sel.closest('.wk').dataset.w;
    try {
      await api(withKey('/api/worker/' + wid), { method: 'PATCH',
        body: { [sel.dataset.f]: sel.value } });
      await load();
    } catch (e) { alert('변경 실패:\n' + e.message); await load(); }
  });
  $$('[data-persona]').forEach(b => b.onclick = async () => {
    const id = b.dataset.persona;
    $('#persona-box').hidden = false;
    $('#persona-title').textContent = `personas/${id}.md`;
    $('#persona-box').dataset.id = id;
    $('#ed-persona').value = await api('/api/file/persona:' + id);
    $('#persona-box').scrollIntoView({ behavior: 'smooth' });
  });
  $$('[data-del]').forEach(b => b.onclick = async () => {
    const id = b.dataset.del;
    if (!confirm(`${id} 를 근무자 명단에서 뺍니다.\n페르소나 파일은 남습니다 — 다시 넣을 수 있습니다.`)) return;
    try { await api(withKey('/api/worker/' + id), { method: 'DELETE' }); await load(); }
    catch (e) { alert('삭제 실패:\n' + e.message); }
  });
}
$('#save-persona').onclick = async () => {
  const id = $('#persona-box').dataset.id, m = $('[data-msg=persona]');
  try {
    await api(withKey('/api/file/persona:' + id), { method: 'POST', body: { text: $('#ed-persona').value } });
    m.className = 'msg ok'; m.textContent = '저장됨';
  } catch (e) { m.className = 'msg bad'; m.textContent = e.message; }
};

/* ── 5. 하네스 + 루프 ───────────────────────────────────── */
const VERB_KO = {
  constrain: ['할 수 없게 만든다', 'permission · sandbox · autonomy'],
  inform: ['알 수 있게 만든다', '컨텍스트 표면 · AX'],
  verify: ['맞는지 검사한다', 'hook · typed output · gate'],
  correct: ['틀렸을 때 복구하고 조인다', 'retry · ratchet'],
  escalate: ['못 하겠으면 넘긴다', '누구에게 · 언제'],
};
function renderHarness() {
  const d = ORG.harness.defaults || {};
  const body = (verb) => {
    const v = d[verb] || {};
    if (verb === 'constrain') {
      const p = v.permission || {};
      return Object.entries(p).map(([k, val]) =>
        `<div class="perm"><span>${esc(k)}</span><b class="p-${esc(val)}">${esc(val)}</b></div>`).join('') +
        `<div class="s" style="margin-top:7px">샌드박스: ${esc(v.sandbox?.network)} · ${esc(v.sandbox?.filesystem)}</div>`;
    }
    if (verb === 'inform') return `<ul>${(v.context || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      <div class="s" style="margin-top:6px">필요할 때: ${(v.on_demand || []).map(esc).join(' · ')}</div>`;
    if (verb === 'verify') return `<ul>${(v.gates || []).map(g =>
      `<li><b>${esc(g.name)}</b> — ${esc(g.rule)}</li>`).join('')}</ul>`;
    if (verb === 'correct') return `<div class="s">재시도 ${esc(v.retry?.max_attempts)}회 → ${esc(v.retry?.on_fail)}</div>
      <ul>${(v.ratchet || []).map(r => `<li>${esc(r.rule || r)}</li>`).join('') ||
        '<li class="s">아직 배운 규칙이 없다</li>'}</ul>`;
    return `<div class="s">→ ${esc(v.to)}</div><ul>${(v.when || []).map(x =>
      `<li>${esc(x)}</li>`).join('')}</ul>`;
  };
  $('#harness-view').innerHTML = Object.keys(VERB_KO).map(k => `
    <div class="verb"><h4>${k}</h4><div class="ko">${esc(VERB_KO[k][0])} — ${esc(VERB_KO[k][1])}</div>
      ${body(k)}</div>`).join('') +
    `<div class="verb" style="grid-column:1/-1"><h4>근무자별 조정</h4>
      <div class="ko">defaults 를 덮어쓴 근무자</div>
      ${Object.entries(ORG.harness.workers || {}).map(([wid, o]) =>
        `<div class="perm"><span>${esc(wid)}</span><b>${esc(Object.keys(o).join(', '))}</b></div>`).join('')}</div>`;
}
function renderLoops() {
  const owners = {};
  (ORG.roster.workers || []).forEach(w => (w.loops || []).forEach(l => owners[l] = w.name));
  $('#loop-view').innerHTML = ORG.loops.map(l => `
    <div class="card"><div class="t">${esc(l)}</div>
      <div class="s">담당: ${esc(owners[l] || '— 아무도 안 쓴다')}</div>
      <div class="row" style="margin-top:8px"><button data-loop="${esc(l)}">편집</button></div>
    </div>`).join('');
  $$('[data-loop]').forEach(b => b.onclick = async () => {
    const id = b.dataset.loop;
    $('#loop-box').hidden = false; $('#loop-box').dataset.id = id;
    $('#loop-title').textContent = `loops/${id}.yaml`;
    $('#ed-loop').value = await api('/api/file/loop:' + id);
    $('#loop-box').scrollIntoView({ behavior: 'smooth' });
  });
}
$('#save-loop').onclick = async () => {
  const id = $('#loop-box').dataset.id, m = $('[data-msg=loop]');
  try {
    await api(withKey('/api/file/loop:' + id), { method: 'POST', body: { text: $('#ed-loop').value } });
    m.className = 'msg ok'; m.textContent = '저장됨';
  } catch (e) { m.className = 'msg bad'; m.textContent = e.message; }
};

/* ── 원문 저장 ──────────────────────────────────────────── */
$$('[data-save]').forEach(b => b.onclick = async () => {
  const n = b.dataset.save, m = $(`[data-msg=${n}]`);
  m.className = 'msg'; m.textContent = '저장 중…';
  try {
    await api(withKey('/api/file/' + n), { method: 'POST', body: { text: $('#ed-' + n).value } });
    m.className = 'msg ok'; m.textContent = '저장됨';
    await load();
  } catch (e) { m.className = 'msg bad'; m.textContent = e.message; }
});

/* ── 근무자 추가 ────────────────────────────────────────── */
$('#add-worker').onclick = () => {
  const r = ORG.roster;
  $('#nw-team').innerHTML = (ORG.teams.teams || []).map(t =>
    `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  $('#nw-runtime').innerHTML = Object.entries(r.runtimes || {}).map(([k, v]) =>
    `<option value="${esc(k)}">${esc(v.name)}</option>`).join('');
  $('#nw-model').innerHTML = Object.entries(r.models || {}).map(([k, v]) =>
    `<option value="${esc(k)}">${esc(k)} — ${esc(v.name)}</option>`).join('');
  $('#dlg-worker').showModal();
};
$('#nw-ok').onclick = async (e) => {
  e.preventDefault();
  const body = { id: $('#nw-id').value.trim(), name: $('#nw-name').value.trim(),
    floor: $('#nw-floor').value, zone: $('#nw-zone').value.trim(),
    team: $('#nw-team').value, runtime: $('#nw-runtime').value,
    model: $('#nw-model').value, autonomy: $('#nw-autonomy').value };
  try {
    const r = await api(withKey('/api/worker'), { method: 'POST', body });
    $('#dlg-worker').close();
    alert(`추가됨: ${r.id}\n\n${r.note}`);
    await load();
  } catch (err) { alert('추가 실패:\n' + err.message); }
};

/* ── 6. 적용 ────────────────────────────────────────────── */
$('#render-all').onclick = async () => {
  const out = $('#render-out'); out.textContent = '렌더 중…';
  try {
    const r = await api(withKey('/api/render'), { method: 'POST' });
    out.textContent = (r.stdout || '') + (r.stderr ? '\n[stderr]\n' + r.stderr : '') ||
      (r.ok ? '완료 (출력 없음)' : '실패');
  } catch (e) { out.textContent = '실패: ' + e.message; }
};
async function loadBackups() {
  const d = await api('/api/backups');
  $('#backup-view').innerHTML = d.backups.map(b => `
    <div class="card"><div class="t" style="font-size:12.5px">${esc(b.file)}</div>
      <div class="s">${new Date(b.mtime * 1000).toLocaleString('ko-KR')} · ${b.size}B</div>
      <div class="row" style="margin-top:8px"><button data-rs="${esc(b.file)}">이 시점으로 되돌리기</button></div>
    </div>`).join('') || '<p class="muted">아직 백업이 없습니다.</p>';
  $$('[data-rs]').forEach(b => b.onclick = async () => {
    if (!confirm(`${b.dataset.rs} 로 되돌립니다. 현재 내용은 다시 백업됩니다.`)) return;
    try { await api(withKey('/api/restore?name=' + encodeURIComponent(b.dataset.rs)),
      { method: 'POST' }); await load(); await loadBackups(); alert('되돌렸습니다'); }
    catch (e) { alert('실패: ' + e.message); }
  });
}

/* ── 부팅 ───────────────────────────────────────────────── */
$('#key').value = localStorage.getItem('kt66_agentops_key') || '';
$('#key').oninput = () => localStorage.setItem('kt66_agentops_key', KEY());
load().catch(e => { $('#errbar').hidden = false; $('#errbar').textContent = '로드 실패: ' + e.message; });
