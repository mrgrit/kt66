/* kt66 인프라 요구사항 — 화면.
 * 판정은 서버가 랩을 보고 한다. 화면은 그 결과를 그대로 보여줄 뿐이다. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => (s || '').replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));
let CUR = null;

function err(m) {
  const b = $('#errbar');
  if (!m) { b.hidden = true; return; }
  b.hidden = false; b.textContent = m; setTimeout(() => b.hidden = true, 9000);
}

async function api(p, o = {}) {
  const r = await fetch(p, o);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.status);
  return d;
}

async function list() {
  const d = await api('/api/catalog');
  if (d.errors.length) err('요구사항 로드 오류:\n' + d.errors.join('\n'));
  $('#list').innerHTML = d.requirements.map(r => `
    <div class="card" data-open="${r.id}" style="cursor:pointer">
      <div class="t">${esc(r.title)} <span class="kind ${r.kind}">${r.kind}</span></div>
      <div class="s">${esc(r.requested_by || '')} · 난이도 ${r.difficulty} · 확인 ${r.checks}개
        · 소요 ${r.lead_time || '—'}</div>
      <div class="s" style="margin-top:8px;color:var(--ink)">${esc((r.request || '').trim().split('\n')[0])}</div>
      <div style="margin-top:8px">${(r.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join(' ')}</div>
    </div>`).join('');
  $$('[data-open]').forEach(c => c.onclick = () => open(c.dataset.open));
}

async function open(id) {
  const r = await api('/api/requirement/' + id);
  CUR = id;
  $('#d-title').textContent = `${r.id} · ${r.title}`;
  $('#d-request').textContent = (r.request || '').trim();
  $('#d-unstated').innerHTML = (r.unstated || []).map(u => `<li>${esc(u)}</li>`).join('')
    || '<li class="muted">없음</li>';
  $('#d-gt').textContent = r.ground_truth || '';
  $('#d-checks').innerHTML = r.acceptance.map(a => `
    <div class="card ck"><div class="t">${a.id} · ${a.type}
      <span class="tag">${a.weight}점</span></div>
      <div class="s">${esc(a.note || '')}</div></div>`).join('');
  $('#d-score').textContent = '';
  $('#dlg').showModal();
}

$('#d-close').onclick = () => $('#dlg').close();

$('#d-verify').onclick = async () => {
  const s = $('#d-score');
  s.className = 'msg'; s.textContent = '랩을 확인하는 중…';
  try {
    const v = await api(`/api/verify/${CUR}`, { method: 'POST' });
    s.className = 'msg ' + (v.done ? 'ok' : 'bad');
    s.textContent = `${v.points} / ${v.max}점` + (v.done ? ' — 완료' : ' — 아직 남았습니다');
    $('#d-checks').innerHTML = v.items.map(i => `
      <div class="card ck ${i.passed ? 'pass' : 'fail'}">
        <div class="t">${i.id} · ${i.type}
          <span class="tag ${i.passed ? 'floor' : ''}">${i.passed ? '충족' : '미충족'}</span>
          <span class="tag">${i.weight}점</span></div>
        <div class="s">${esc(i.note || '')}</div>
        <div class="d">${esc(i.detail)}</div>
      </div>`).join('')
      + `<div class="card"><div class="t">진행</div>
         <div class="bar"><i style="width:${Math.round(v.points / v.max * 100)}%"></i></div></div>`;
  } catch (e) { s.className = 'msg bad'; s.textContent = String(e.message); }
};

(async () => {
  try {
    const h = await api('/health');
    $('#health').textContent = `요구사항 ${h.requirements}건`;
    $('#health').className = 'pill ' + (h.ok ? 'ok' : 'bad');
  } catch (e) { err(String(e.message)); }
  await list();
})();
