/* 원본 파일에 연결된 스킬과 근무자 업무 R&R. 키·원문은 브라우저 영구 저장소에 추가하지 않는다. */
let skillCatalog = [], editingSkill = null, assignment = null, assignmentDirty = false;
const settingMessage = (selector, message, bad = false) => {
  const el = $(selector); el.textContent = message; el.className = 'msg ' + (bad ? 'bad' : 'ok');
};
const settingsApi = (path, opt = {}) => api(path, {
  ...opt, headers: {'content-type': 'application/json', 'x-api-key': KEY()}
});
const requireSettingsKey = () => {
  if (KEY()) return true;
  alert('화면 오른쪽 위에 강사 키를 입력하세요.'); $('#key').focus(); return false;
};
function selectSettingsStep(step) { $('[data-step="' + step + '"]').click(); }
async function loadSkills() {
  const data = await api('/api/skills');
  skillCatalog = data.skills;
  $('#skill-summary').textContent = '원본 스킬 ' + data.skills.length + '개';
  $('#skill-errors').textContent = (data.errors || []).join('\n');
  $('#skill-view').innerHTML = data.skills.map(s =>
    '<article class="card"><div class="t">' + esc(s.name) + '</div><p class="s">' + esc(s.description) +
    '</p><div class="s">근무자: ' + (s.workers.map(w => esc(w.name) + (w.active ? '' : ' (보관)')).join(', ') || '연결 없음') +
    '</div><div class="s">' + (s.automatic.length ? '자동 사용: ' + s.automatic.map(esc).join(', ') : '직접 연결 스킬') +
    '</div><div class="source-path">' + esc(s.path) + '</div><div class="toolbar">' +
    '<button data-skill-edit="' + esc(s.name) + '">원문 편집</button><button data-skill-link="' + esc(s.name) +
    '">근무자에 연결</button></div></article>').join('') || '<p class="muted">스킬을 추가하고 담당 근무자에게 연결하세요.</p>';
  $$('[data-skill-edit]').forEach(b => b.onclick = () => openSkill(b.dataset.skillEdit).catch(showSettingsError));
  $$('[data-skill-link]').forEach(b => b.onclick = () => {
    selectSettingsStep('4');
    $('#worker-view').scrollIntoView({behavior:'smooth'});
    $('#assignment-msg').textContent = '';
    pendingSkill = b.dataset.skillLink;
    $('#worker-settings-hint').textContent = '연결할 스킬: ' + pendingSkill + ' — 담당 근무자의 R&R·스킬·담당 설정을 선택하세요.';
  });
}
function showSettingsError(error) { alert(error.message); }
const skillTemplate = name => '---\nname: ' + name + '\ndescription: "이 절차를 선택할 상황을 한국어로 적으세요."\n---\n\n' +
  '# 업무 절차\n\n## 적용 상황\n언제 이 절차가 필요한지 적으세요.\n\n## 입력과 범위\n대상·기간·자료와 조회 범위를 적으세요.\n\n' +
  '## 작업 순서\n1. 확인할 사실과 필요한 증거를 정하세요.\n2. 허용된 도구로 확인하고 사실·추정·미확인을 구분하세요.\n\n' +
  '## 판단·협업 기준\n자료가 부족하면 보류하고 필요한 자료와 담당자를 안내하세요.\n\n## 결과와 완료 조건\n결론·근거·한계·후속 작업을 보고하세요.\n';
async function openSkill(name = '') {
  if (!requireSettingsKey()) return;
  editingSkill = name ? await settingsApi('/api/skills/' + encodeURIComponent(name)) : null;
  const row = skillCatalog.find(s => s.name === name);
  $('#skill-name').value = name; $('#skill-name').readOnly = !!name;
  $('#skill-title').textContent = name ? name + ' 편집' : '스킬 추가';
  $('#skill-content').value = editingSkill ? editingSkill.content : skillTemplate('new-skill');
  $('#skill-usage').textContent = row ? '연결: ' + (row.workers.map(w => w.name).join(', ') || '없음') +
    (row.automatic.length ? ' / 자동 사용: ' + row.automatic.join(', ') : '') : '저장 후 근무자 연결이 필요합니다.';
  $('#skill-delete').hidden = !name;
  $('#skill-delete').disabled = row ? !row.deletable : true;
  $('#skill-delete').title = row?.automatic.length ? '실행기가 자동 사용하는 필수 스킬입니다.' : '연결된 근무자·보관 페르소나가 없어야 삭제할 수 있습니다.';
  $('#skill-msg').textContent = '';
  $('#dlg-skill').showModal();
}
$('#skill-new').onclick = () => openSkill().catch(showSettingsError);
$('#skill-refresh').onclick = () => loadSkills().catch(showSettingsError);
$('#skill-name').oninput = () => {
  if (!editingSkill) $('#skill-content').value = $('#skill-content').value.replace(/^name:.*$/m, 'name: ' + $('#skill-name').value.trim());
};
$('#skill-close').onclick = () => $('#dlg-skill').close();
$('#skill-form').onsubmit = async event => {
  event.preventDefault();
  const button = $('#skill-form button[type=submit]');
  button.disabled = true;
  try {
    const name = $('#skill-name').value.trim(), content = $('#skill-content').value;
    const path = '/api/skills' + (editingSkill ? '/' + encodeURIComponent(name) : '');
    const result = await settingsApi(path, {method: editingSkill ? 'PUT' : 'POST',
      body: {name, content, ...(editingSkill ? {sha256:editingSkill.sha256} : {})}});
    editingSkill = {name, content, sha256:result.sha256};
    $('#skill-name').readOnly = true; $('#skill-title').textContent = name + ' 편집';
    settingMessage('#skill-msg', '원본 저장·하네스 반영 완료. 근무자에 연결하면 다음 업무에서 사용할 수 있습니다.');
    await loadSkills(); await load();
    const row = skillCatalog.find(s => s.name === name);
    $('#skill-delete').hidden = false; $('#skill-delete').disabled = !row?.deletable;
  } catch (e) { settingMessage('#skill-msg', e.message, true); }
  finally { button.disabled = false; }
};
$('#skill-delete').onclick = async () => {
  if (!editingSkill || !confirm(editingSkill.name + ' 스킬을 삭제합니다. 원본은 백업되며 적용 화면에서 복원할 수 있습니다.')) return;
  $('#skill-delete').disabled = true;
  try {
    await settingsApi('/api/skills/' + encodeURIComponent(editingSkill.name), {method:'DELETE', body:{sha256:editingSkill.sha256}});
    $('#dlg-skill').close(); editingSkill = null;
    await loadSkills(); await load();
  } catch (e) { settingMessage('#skill-msg', e.message, true); $('#skill-delete').disabled = false; }
};
let pendingSkill = '';
async function openAssignment(id) {
  if (!requireSettingsKey()) return;
  if (assignmentDirty && !confirm('저장하지 않은 담당 설정을 버리고 다른 근무자를 불러올까요?')) return;
  const [data] = await Promise.all([settingsApi('/api/assignments/' + encodeURIComponent(id)), loadSkills()]);
  assignment = data;
  $('#assignment-title').textContent = data.worker.name + ' — R&R·스킬·담당 설정';
  $('#assignment-context').textContent = '주 소속: ' + data.department.name + ' / ' + data.team.name +
    ' · 부서 책임: ' + data.department.mission + ' · 제외 업무: ' + data.department.not_our_job;
  $('#assignment-description').value = data.description;
  $('#assignment-instructions').value = data.instructions;
  $('#assignment-assets').value = (data.worker.assets || []).join('\n');
  $('#assignment-skills').innerHTML = skillCatalog.map(s => '<label class="check-option"><input type="checkbox" value="' +
    esc(s.name) + '" ' + (data.skills.includes(s.name) || pendingSkill === s.name ? 'checked' : '') + '><span><b>' +
    esc(s.name) + '</b><small>' + esc(s.description) + '</small></span></label>').join('');
  const loopRows = ORG.loop_details || [];
  $('#assignment-loops').innerHTML = loopRows.map(l => '<label class="check-option"><input type="checkbox" value="' +
    esc(l.id) + '" ' + ((data.worker.loops || []).includes(l.id) ? 'checked' : '') +
    (l.owner !== id ? ' disabled' : '') + '><span>' + esc(l.id) + '<small>owner: ' + esc(l.owner) +
    ' · ' + esc(l.cadence) + '</small></span></label>').join('');
  $('#assignment-policy').textContent = JSON.stringify({
    '직무 상한':data.authorization, '상속 후 정책':data.policy,
    '정기 업무 실효 도구':data.available_tools, '원본':data.sources,
    '주의':'사용자 업무에는 배정 기능·조회 범위·대화 승인 조건이 추가로 적용됩니다.'
  }, null, 2);
  $('#assignment-msg').textContent = '';
  $('#assignment-box').hidden = false;
  assignmentDirty = !!pendingSkill && !data.skills.includes(pendingSkill);
  pendingSkill = '';
  $('#worker-settings-hint').textContent = '';
  $('#assignment-box').scrollIntoView({behavior:'smooth'});
}
$('#assignment-form').oninput = () => { assignmentDirty = true; };
$('#assignment-close').onclick = () => {
  if (assignmentDirty && !confirm('저장하지 않은 담당 설정을 닫을까요?')) return;
  $('#assignment-box').hidden = true; assignmentDirty = false;
};
$('#assignment-form').onsubmit = async event => {
  event.preventDefault();
  const button = $('#assignment-form button[type=submit]'); button.disabled = true;
  try {
    const body = {sha256:assignment.sha256, description:$('#assignment-description').value,
      instructions:$('#assignment-instructions').value,
      skills:$$('#assignment-skills input:checked').map(el=>el.value),
      loops:$$('#assignment-loops input:checked').map(el=>el.value),
      assets:$('#assignment-assets').value.split('\n').map(s=>s.trim()).filter(Boolean)};
    assignment = await settingsApi('/api/assignments/' + encodeURIComponent(assignment.worker.id), {method:'PUT', body});
    assignmentDirty = false;
    settingMessage('#assignment-msg', 'R&R·담당 범위·스킬 연결 저장 및 하네스 반영 완료');
    await load(); await loadSkills();
  } catch (e) { settingMessage('#assignment-msg', e.message, true); }
  finally { button.disabled = false; }
};
async function loadConfigAudit() {
  const data = await api('/api/config-audit');
  $('#config-audit').innerHTML = '<div class="toolbar"><b>원본 연결 검사: ' +
    (data.errors.length ? esc(data.errors.length) + '건 확인 필요' : '오류 없음') + '</b></div>' +
    (data.errors.length ? '<pre class="msg bad">' + esc(data.errors.join('\n')) + '</pre>' : '') +
    '<div class="audit-table"><table><thead><tr><th>원본</th><th>화면</th><th>역할</th><th>편집 범위</th></tr></thead><tbody>' +
    data.files.map(f => '<tr><td><code>' + esc(f.path) + '</code></td><td>' + esc(f.screen) +
      '</td><td>' + esc(f.purpose) + '</td><td>' + esc(f.support) + '</td></tr>').join('') +
    '</tbody></table></div><h3>원본과 적용 사본</h3><div class="audit-workers">' +
    data.workers.map(w => '<span class="tag ' + (w.current ? 'floor' : '') + '">' + esc(w.name) + ': ' +
      (w.current ? '일치' : '적용 대기·확인 필요') + '</span>').join(' ') +
    '</div><ul>' + data.notes.map(n => '<li>' + esc(n) + '</li>').join('') + '</ul>';
}
$$('.step').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.step === '5') loadSkills().catch(showSettingsError);
  if (button.dataset.step === '6') loadConfigAudit().catch(e => {$('#config-audit').textContent = e.message;});
}));
window.addEventListener('beforeunload', event => {
  if (assignmentDirty) { event.preventDefault(); event.returnValue = ''; }
});
$('#worker-view').addEventListener('click', event => {
  const button = event.target.closest('[data-assignment]');
  if (button) openAssignment(button.dataset.assignment).catch(showSettingsError);
});
