/* Shared navigation. Each console serves its own copy, so navigation does not
   depend on NOC being available. Named hosts and direct ports both work. */
(() => {
  const paths = {
    overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    server: 'M4 3h16v7H4z M4 14h16v7H4z M7 6.5h.01 M7 17.5h.01 M11 6.5h6 M11 17.5h6',
    crew: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M18 8a3 3 0 0 1 0 6 M22 21v-2a4 4 0 0 0-3-3.87',
    model: 'M12 3l9 5v8l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v8 M7.5 5.5l9 5',
    shield: 'M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6z M8 12l3 3 5-6',
    activity: 'M2 12h4l3-8 6 16 3-8h4',
    book: 'M3 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-3H3z M21 4h-6a3 3 0 0 0-3 3v14a4 4 0 0 1 4-3h5z',
    arrow: 'M7 17L17 7 M7 7h10v10',
  };
  window.dcIcon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.server}"/></svg>`;
  window.dcURL = (name) => {
    if(name==='agentcontrol')return window.dcURL('noc')+'agent-control';
    const ports = {noc:8020, portal:8000, agentops:8050, modelops:8060, infraops:8070, siem:5601, landing:80};
    const host = location.hostname;
    if (host.endsWith('.kt66.lab')) return `${name === 'siem' ? 'https:' : location.protocol}//${name === 'landing' ? 'kt66.lab' : name + '.kt66.lab'}/`;
    return `${name === 'siem' ? 'https:' : 'http:'}//${host.includes(':') ? '['+host+']' : host}${ports[name] === 80 ? '' : ':'+ports[name]}/`;
  };
  const current = document.body.dataset.console;
  if (!current) return;
  const sidebar = document.createElement('aside');
  sidebar.className = 'dc-sidebar'; sidebar.id = 'dc-sidebar';
  const nav = (name, title, subtitle, icon) => `<a class="dc-nav-link ${current === name ? 'is-current' : ''}" href="${dcURL(name)}" ${current === name ? 'aria-current="page"' : ''}>${dcIcon(icon)}<span>${title}<small>${subtitle}</small></span>${current === name ? '<i class="dc-nav-marker"></i>' : ''}</a>`;
  sidebar.innerHTML = `
    <a class="dc-brand" href="${dcURL('noc')}" aria-label="kt66 관제 홈"><span class="dc-logomark">k<span>t</span></span><span>kt66<span class="dc-brand-sub">DATACENTER</span></span></a>
    <div class="dc-site"><span class="dc-site-icon">${dcIcon('server')}</span><span>교육 데이터센터<small>INFRASTRUCTURE LAB</small></span><span class="dc-site-dot"></span></div>
    <div class="dc-nav-caption">WORKSPACE</div>
    <nav aria-label="콘솔 이동">
      ${nav('noc','통합 관제','Overview','overview')}
      ${nav('agentcontrol','AI 에이전트 관제','4F · Agent control','activity')}
      ${nav('portal','인프라 자산','Infrastructure','server')}
      ${nav('agentops','근무자 운영','Agent operations','crew')}
      ${nav('modelops','모델 운영','Model operations','model')}
      ${nav('infraops','인프라 실습','Work orders','activity')}
    </nav>
    <div class="dc-nav-caption">SECURITY & LEARNING</div>
    <nav aria-label="보안과 실습">
      ${nav('siem','보안 관제','Wazuh SIEM','shield')}
      ${nav('landing','실습 환경','Lab directory','book')}
    </nav>
    <div class="dc-sidebar-foot"><div class="dc-mini-grid"><i></i><i></i><i></i><i></i><i></i><i></i></div><b>하나의 랩, 하나의 데이터센터.</b><p>관측하고, 판단하고, 운영합니다.</p><span>KT66 / OPERATIONS PLATFORM</span></div>`;
  document.body.prepend(sidebar);
  const toggle = document.createElement('button');
  toggle.className = 'dc-menu-toggle'; toggle.type = 'button'; toggle.setAttribute('aria-label','콘솔 메뉴 열기');
  toggle.setAttribute('aria-controls', 'dc-sidebar'); toggle.setAttribute('aria-expanded','false');
  toggle.innerHTML = '<span></span><span></span><span></span>';
  const shade = document.createElement('button'); shade.className = 'dc-menu-shade'; shade.hidden = true; shade.setAttribute('aria-label','콘솔 메뉴 닫기');
  const menu = open => {document.body.classList.toggle('dc-menu-open',open);toggle.setAttribute('aria-expanded',String(open));shade.hidden = !open;};
  toggle.onclick = () => menu(!document.body.classList.contains('dc-menu-open'));
  shade.onclick = () => menu(false);
  document.addEventListener('keydown', e=>{if(e.key==='Escape')menu(false)});
  document.body.prepend(toggle,shade);
  document.querySelectorAll('[data-console-link]').forEach(a=>{a.href=dcURL(a.dataset.consoleLink)});
})();
