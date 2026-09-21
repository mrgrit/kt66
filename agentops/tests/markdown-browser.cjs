/* Chromium에서 렌더링 의미·원문 보존·HTML 격리를 검사한다. 모델은 호출하지 않는다. */
const assert = require('node:assert/strict');
const path = require('node:path');
module.exports = async function checkMarkdown(page, root) {
  await page.setContent('<!doctype html><html lang="ko"><body><main id="report"></main></body></html>');
  for (const name of ['vendor/marked-18.0.13/marked.umd.js', 'vendor/dompurify-3.4.15/purify.min.js', 'request-markdown.js']) {
    await page.addScriptTag({path:path.join(root,'agentops/static',name)});
  }
  const markdown = [
    '# 보안 분석 결과', '', '## 주요 발견', '', '**중요** · *확인 필요* · ~~이전 판단~~', '',
    '| 자산 | 상태 | 건수 |', '| --- | :---: | ---: |', '| 방화벽 | 정상<br>확인 | 3 |', '',
    '1. 로그 조회', '   - 증거 확인', '2. 결과 검토', '', '- [x] 검사 완료', '- [ ] 후속 확인', '',
    '> 보관된 경보만 분석했습니다.', '', '```python', 'print("<script>실행 금지</script>")', 'pattern = r"\\n"', '```', '',
    '[참조](https://example.com/report)', '', '![첨부 그림](https://example.com/tracker.png)', '',
    '<img src="https://example.com/tracker2.png" onerror="window.injected=1">',
    '<script>window.injected=1</script>', '<style>body{display:none}</style>', '',
    '[위험 링크](javascript:alert%281%29)', '[인코딩 링크](jav&#x61;script:alert%281%29)',
    '[데이터 링크](data:text/html;base64,PHNjcmlwdD4=)', '', '<br onclick="window.injected=1">',
  ].join('\n');
  const facts = await page.evaluate(source => {
    window.injected=0;
    document.querySelector('#report').innerHTML=RequestMarkdown.render(source);
    const report=document.querySelector('#report');
    const code=report.querySelector('pre code');
    return {
      heading:report.querySelector('h3')?.textContent,
      table:report.querySelector('table')?.rows.length,
      breakInTable:Boolean(report.querySelector('td br')),
      nestedList:Boolean(report.querySelector('ol ul')),
      strong:report.querySelector('strong')?.textContent,
      checked:[...report.querySelectorAll('input')].map(c=>({checked:c.checked,disabled:c.disabled})),
      code:code?.textContent,
      quote:report.querySelector('blockquote')?.textContent,
      unsafeElements:report.querySelectorAll('script,style,img,iframe,svg,object,embed,form').length,
      links:[...report.querySelectorAll('a[href]')].map(a=>({href:a.getAttribute('href'),rel:a.rel,target:a.target})),
      onHandlers:[...report.querySelectorAll('*')].flatMap(el=>[...el.attributes].filter(a=>a.name.startsWith('on')).map(a=>a.name)),
      rawHtmlVisible:report.textContent.includes('<img src=') && report.textContent.includes('<script>window.injected=1</script>'),
      injected:window.injected,
      tableFocus:report.querySelector('.markdown-table')?.tabIndex,
    };
  },markdown);
  assert.equal(facts.heading,'보안 분석 결과');
  assert.equal(facts.table,2);assert.equal(facts.breakInTable,true);assert.equal(facts.nestedList,true);
  assert.equal(facts.strong,'중요');assert.match(facts.quote,/보관된 경보/);
  assert.deepEqual(facts.checked,[{checked:true,disabled:true},{checked:false,disabled:true}]);
  assert.equal(facts.code,'print("<script>실행 금지</script>")\npattern = r"\\n"\n');
  assert.equal(facts.unsafeElements,0);assert.equal(facts.injected,0);assert.deepEqual(facts.onHandlers,[]);
  assert.equal(facts.rawHtmlVisible,true);assert.equal(facts.tableFocus,0);
  assert.equal(facts.links.length,2);
  assert.ok(facts.links.every(a=>a.href.startsWith('https://example.com/') && a.target==='_blank' && a.rel==='noopener noreferrer'));
  const fallback=await page.evaluate(()=>{const original=DOMPurify;window.DOMPurify=null;const result=RequestMarkdown.render('<img src=x onerror=alert(1)>');window.DOMPurify=original;return result});
  assert.match(fallback,/&lt;img/);assert.doesNotMatch(fallback,/<img/);
  return {checks:'headings, tables, lists, code preservation, links, checkboxes, raw HTML, unsafe URLs, fail-closed fallback'};
};
