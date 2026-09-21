/* 에이전트의 Markdown을 읽기 전용 문서로 표시한다. 원문과 실행 증거는 수정하지 않는다. */
(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const parser = window.marked && new window.marked.Marked({
    gfm: true,
    breaks: true,
    renderer: {
      // 보안 로그의 HTML 페이로드는 실행하거나 숨기지 않고 텍스트로 보여 준다.
      html: token => /^<br\s*\/?>$/i.test(token.text.trim()) ? '<br>' : escape(token.text),
      heading(token) {
        const level = Math.min(6, token.depth + 2);
        return `<h${level}>${this.parser.parseInline(token.tokens)}</h${level}>\n`;
      },
      // 보고서를 여는 것만으로 외부 이미지 서버에 접속하지 않는다.
      image: token => `<a href="${escape(token.href)}">이미지: ${escape(token.text || '열기')}</a>`,
    },
  });
  const options = {
    ALLOWED_TAGS: ['p', 'br', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'del', 'ul', 'ol', 'li',
      'blockquote', 'pre', 'code', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'input'],
    ALLOWED_ATTR: ['href', 'title', 'class', 'align', 'start', 'type', 'checked', 'disabled'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  };
  function render(value) {
    const source = String(value ?? '');
    if (!parser || !window.DOMPurify) return `<div class="prose">${escape(source)}</div>`;
    try {
      const fragment = window.DOMPurify.sanitize(parser.parse(source), options);
      // 링크는 명시적 사용자 클릭으로만 이동하며 현재 업무 화면을 보존한다.
      fragment.querySelectorAll('a').forEach(link => {
        const href = link.getAttribute('href');
        let allowed = false;
        try { allowed = Boolean(href) && ['http:', 'https:', 'mailto:'].includes(new URL(href, location.href).protocol); } catch {}
        if (!allowed) { link.removeAttribute('href'); return; }
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.referrerPolicy = 'no-referrer';
      });
      fragment.querySelectorAll('input').forEach(input => {
        if (input.type !== 'checkbox') { input.remove(); return; }
        input.disabled = true;
        input.setAttribute('aria-label', input.checked ? '완료한 항목' : '미완료 항목');
      });
      fragment.querySelectorAll('table').forEach(table => {
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-table';
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', '결과 표 · 넓은 표는 가로로 스크롤할 수 있습니다');
        table.querySelectorAll('thead th').forEach(cell => cell.scope = 'col');
        table.replaceWith(wrapper);
        wrapper.append(table);
      });
      const container = document.createElement('div');
      container.className = 'markdown-body';
      container.append(fragment);
      return container.outerHTML;
    } catch {
      return `<div class="prose">${escape(source)}</div>`;
    }
  }
  window.RequestMarkdown = Object.freeze({render});
})();
