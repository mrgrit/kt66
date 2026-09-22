"""Frozen, tested change proposals. Only the runner applies explicitly authorized changes."""
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from work_requests import Store, write_json


def digest(directory):
    return hashlib.sha256(b''.join(str(p.relative_to(directory)).encode() + b'\0' + p.read_bytes()
        for p in sorted(directory.rglob('*')) if p.is_file() and not p.is_symlink())).hexdigest()


def run(args, timeout=35):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise ValueError((p.stderr or p.stdout or '실행 실패')[-2000:])
    return p.stdout


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in ('src', 'href'):
                self.references.append(value)


def website_validate(root, rid):
    site = Store(root).directory(rid) / 'workspace' / 'site'
    if site.is_symlink() or not (site / 'index.html').is_file():
        return {'ok': False, 'errors': ['site/index.html 파일이 필요합니다']}
    errors, checked = [], []
    for p in sorted(site.rglob('*')):
        if p.is_symlink():
            errors.append(str(p.relative_to(site)) + ': 심볼릭 링크 금지')
            continue
        if not p.is_file():
            continue
        name = str(p.relative_to(site))
        if p.suffix.lower() not in ('.html', '.css', '.js', '.json', '.svg', '.txt'):
            errors.append(name + ': 정적 배포 지원 형식이 아닙니다')
        if p.suffix == '.html':
            parser = Links()
            try:
                parser.feed(p.read_text())
                for ref in parser.references:
                    url = urllib.parse.urlsplit(ref)
                    if url.scheme or url.netloc or not url.path:
                        continue
                    target = (p.parent / urllib.parse.unquote(url.path)).resolve()
                    if url.path.startswith('/') or not target.is_relative_to(site.resolve()):
                        errors.append(name + ': 하위 경로 배포에 맞는 상대 URL 필요: ' + ref)
                    elif not target.exists():
                        errors.append(name + ': 없는 로컬 참조: ' + ref)
            except (ValueError, UnicodeError):
                errors.append(name + ': HTML 해석 실패')
        elif p.suffix == '.js':
            result = subprocess.run(['node', '--check', str(p)], capture_output=True, text=True, timeout=10)
            if result.returncode:
                errors.append(name + ': JavaScript 구문 오류')
        checked.append(name)
    return {'ok': not errors, 'errors': errors, 'files': checked, 'sha256': digest(site),
            'checks': ['index exists', 'local references', 'JavaScript syntax'],
            'limitations': ['브라우저 시각 검증과 실제 주문·결제 검증은 포함하지 않습니다']}


def isolated_test(directory, payload=None, parameter='q'):
    """No published ports/network or host credentials. The candidate is read-only."""
    directory = Path(directory)
    config = '''ServerRoot /etc/apache2
PidFile /tmp/apache.pid
Listen 127.0.0.1:18080
IncludeOptional /etc/apache2/mods-enabled/*.load
User www-data
Group www-data
ServerName localhost
ErrorLog /tmp/apache-error.log
LogLevel warn
DocumentRoot /candidate/site
TypesConfig /etc/mime.types
<Directory /candidate/site>
 Require all granted
 Options -Indexes
 AllowOverride None
</Directory>
SecRuleEngine On
SecRequestBodyAccess On
SecAuditEngine Off
IncludeOptional /candidate/rule.conf
'''
    (directory / 'httpd.conf').write_text(config)
    image = run(['docker', 'inspect', '--format', '{{.Image}}', 'kt66-web']).strip()
    command = '''set -eu
apache2 -f /candidate/httpd.conf -t
apache2 -f /candidate/httpd.conf -k start
trap 'apache2 -f /candidate/httpd.conf -k stop >/dev/null 2>&1 || true' EXIT
normal=$(curl --noproxy '*' --retry 3 --retry-connrefused --retry-delay 1 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/index.html)
printf 'normal=%s\n' "$normal"
if [ "$1" = waf ]; then
 attack=$(curl --noproxy '*' -sS -G --data-urlencode "$2" -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/index.html)
 printf 'attack=%s\n' "$attack"
fi
'''
    out = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--user', '33:33', '--memory', '128m', '--cpus', '0.5',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', '-v', str(directory.resolve()) + ':/candidate:ro',
               '--entrypoint', '/bin/bash', image, '-c', command, '--',
               'waf' if payload is not None else 'site', parameter + '=' + (payload or '')])
    codes = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    ok = codes.get('normal') == '200' and (payload is None or codes.get('attack') == '403')
    return dict(ok=ok, normal_http=codes.get('normal'), attack_http=codes.get('attack'),
                checks=['isolated Apache syntax', 'normal HTTP 200'] + (['literal payload HTTP 403'] if payload is not None else []))


def proposal(root, rid, kind, summary, build, metadata, author=None):
    store = Store(root)
    initial = store.get(rid)
    if initial['scope'] == 'read':
        raise ValueError('조회 전용 요청에서는 변경안을 만들 수 없습니다')
    verify_author(root, initial, kind, author)
    cid = 'change-' + uuid.uuid4().hex[:16]
    directory = store.directory(rid) / 'changes' / cid
    directory.mkdir(parents=True)
    tests = build(directory)
    if not tests.get('ok'):
        raise ValueError('변경안 검증 실패: ' + json.dumps(tests, ensure_ascii=False))
    change = dict(id=cid, kind=kind, summary=summary, metadata=metadata, tests=tests,
                  sha256=digest(directory), revision=initial['revision'], created=time.time(),
                  status='proposed', author=author)
    with store.edit(rid) as d:
        if d['revision'] != initial['revision'] or d['status'] not in ('running', 'queued', 'waiting_tasks'):
            raise ValueError('검증 중 요청이 변경됐습니다')
        verify_author(root,d,kind,author)
        d['changes'].append(change)
    return change


def website_prepare(root, rid, slug, author=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,40}', slug):
        raise ValueError('사이트 경로는 영문 소문자·숫자·하이픈 2~41자입니다')
    validation = website_validate(root, rid)
    if not validation['ok']:
        return validation
    def build(directory):
        shutil.copytree(Store(root).directory(rid) / 'workspace' / 'site', directory / 'site')
        return {**isolated_test(directory), 'files': validation['files'], 'static_validation': validation}
    return proposal(root, rid, 'website', f'/projects/{slug}/ 홈페이지 배포', build, {'slug': slug}, author)


def waf_prepare(root, rid, payload, parameter, author=None):
    if not isinstance(payload, str) or not 1 <= len(payload) <= 500 or any(ord(c) < 32 for c in payload) or '%{' in payload:
        raise ValueError('페이로드는 제어문자와 ModSecurity 매크로(%{) 없는 1~500자 문자열이어야 합니다')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,50}', parameter):
        raise ValueError('차단 대상 쿼리·폼 파라미터 이름을 지정하세요')
    rule_id = 2000000 + int(hashlib.sha256((rid + parameter + payload).encode()).hexdigest()[:7], 16)
    escaped = payload.replace('\\', '\\\\').replace('"', '\\"')
    rule = f'SecRule ARGS:{parameter} "@contains {escaped}" "id:{rule_id},phase:2,deny,status:403,log,t:none,msg:\'KT66 request payload block\'"\n'
    def build(directory):
        (directory / 'site').mkdir()
        (directory / 'site' / 'index.html').write_text('<!doctype html><title>KT66 rule test</title>ok')
        (directory / 'rule.conf').write_text(rule)
        return isolated_test(directory, payload, parameter)
    return proposal(root, rid, 'waf', f'{parameter} 파라미터의 지정 페이로드 차단', build,
                    {'parameter': parameter, 'payload': payload, 'rule_id': rule_id, 'rule': rule}, author)


def verify_author(root, data, kind, author):
    import authorization
    if not isinstance(author,dict):
        raise ValueError('작성 직무와 실행 근거가 없는 변경안은 다시 준비해야 합니다')
    task = next((t for t in data['tasks'] if t['id'] == author.get('task_id')),None)
    if not task or task['worker'] != author.get('worker') or task['revision'] != data['revision']:
        raise ValueError('현재 회차의 변경안 작성자를 확인할 수 없습니다')
    p = authorization.task_profile(root,data,task)
    if author.get('fingerprint') != p['fingerprint'] or author.get('template') != p['template']:
        raise ValueError('변경안 작성 직무 정책이 변경되었습니다. 다시 검토해야 합니다')
    if authorization.require(dict(authorization=p,request=dict(phase=task['phase'],capabilities=task['capabilities'])),kind+'_prepare',{}):
        raise ValueError('담당 직무에서 허용하지 않는 변경안입니다')
    import harness_compiler
    _, manifest = harness_compiler.compile_worker(p['template'],root)
    permissions = manifest['policy']['constrain']['permission']
    if permissions.get('user_request','allow') == 'deny' or (kind == 'waf' and permissions.get('firewall_rule_change','deny') == 'deny'):
        raise ValueError('현재 상위 정책에서 금지한 변경안입니다')


def authorize(root, rid, cid, expected_hash, action='apply'):
    if action not in ('apply', 'rollback'):
        raise ValueError('잘못된 변경 작업')
    store = Store(root)
    with store.edit(rid) as d:
        c = next((c for c in d['changes'] if c['id'] == cid), None)
        if not c or c['sha256'] != expected_hash:
            raise ValueError('변경안이 없거나 확인한 버전과 다릅니다')
        if action == 'apply' and (c['status'] != 'proposed' or c['revision'] != d['revision'] or d['status'] != 'waiting_approval'):
            raise ValueError('현재 검토 대기 중인 변경안만 적용할 수 있습니다')
        if action == 'apply':
            verify_author(root,d,c['kind'],c.get('author'))
        if action == 'rollback' and (c['status'] != 'applied' or d['status'] in ('queued', 'running', 'waiting_tasks', 'applying')):
            raise ValueError('적용된 변경만 되돌릴 수 있습니다')
        c['status'] = 'approved' if action == 'apply' else 'rollback_approved'
        c['authorized_at'] = time.time()
        c['authorized_by'] = 'instructor'
        d['status'] = 'applying'
    return store.get(rid)


def apply_pending(root, rid):
    root = Path(root)
    store = Store(root)
    with (root / 'tickets' / '.request-change.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.edit(rid) as d:
            for c in d['changes']:
                if c['status'] not in ('approved', 'rollback_approved'):
                    continue
                directory = store.directory(rid) / 'changes' / c['id']
                rollback = c['status'] == 'rollback_approved'
                journal_path = directory.parent / (c['id'] + '.execution.json')
                journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
                action = 'rollback' if rollback else 'apply'
                try:
                    if c.get('authorized_by') != 'instructor':
                        raise ValueError('사람의 적용 승인이 없습니다')
                    if not rollback:
                        verify_author(root,d,c['kind'],c.get('author'))
                    if digest(directory) != c['sha256']:
                        raise ValueError('검증 후 산출물이 변경됐습니다')
                    if c['kind'] == 'website':
                        dest = root.parent / 'web' / 'landing' / 'projects' / c['metadata']['slug']
                        if dest.is_symlink() or any(p.is_symlink() for p in dest.parents):
                            raise ValueError('배포 경로에 심볼릭 링크가 있습니다')
                        if rollback:
                            if not dest.exists() and journal.get('action') == 'rollback':
                                c['status'] = 'rolled_back'
                                c['result'] = {'rolled_back': True, 'recovered': True}
                                continue
                            if not dest.is_dir() or digest(dest) != digest(directory / 'site'):
                                raise ValueError('배포 이후 변경된 사이트는 자동 삭제하지 않습니다')
                            write_json(journal_path, {'action': action, 'target': str(dest), 'at': time.time()})
                            shutil.rmtree(dest)
                            c['result'] = {'rolled_back': True}
                        else:
                            if dest.exists() and not (journal.get('action') == 'apply' and journal.get('target') == str(dest) and digest(dest) == digest(directory / 'site')):
                                raise ValueError('이미 사용 중인 사이트 경로입니다. 다른 경로를 선택하세요')
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            if not dest.exists():
                                staging = dest.parent / ('.' + c['id'])
                                if staging.exists():
                                    shutil.rmtree(staging)
                                shutil.copytree(directory / 'site', staging)
                                write_json(journal_path, {'action': action, 'target': str(dest), 'at': time.time()})
                                os.rename(staging, dest)
                            try:
                                host = '192.168.12.100'
                                for line in (root.parent / '.env').read_text().splitlines():
                                    if line.startswith('WEB_HOST_IP='):
                                        host = line.partition('=')[2].strip().strip('"\'')
                                url = f'http://{host}/projects/{c["metadata"]["slug"]}/'
                                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                                with opener.open(url + 'index.html', timeout=10) as response:
                                    content = response.read(512000)
                                    if response.status != 200 or content != (directory / 'site' / 'index.html').read_bytes():
                                        raise ValueError('외부 경로의 실제 응답과 배포 파일이 일치하지 않습니다')
                                probe = url + '?kt66_check=' + urllib.parse.quote("' OR 1=1 --")
                                try:
                                    opener.open(probe, timeout=10).close()
                                    raise ValueError('WAF 차단 검증이 통과하지 못했습니다')
                                except urllib.error.HTTPError as error:
                                    if error.code != 403:
                                        raise ValueError('WAF 차단 결과가 403이 아닙니다')
                                c['result'] = dict(url=url, http=200, waf_probe_http=403,
                                                   index_sha256=hashlib.sha256(content).hexdigest())
                            except Exception:
                                if dest.exists() and digest(dest) == digest(directory / 'site'):
                                    shutil.rmtree(dest)
                                raise
                    elif c['kind'] == 'waf':
                        dest = root.parent / 'web' / 'request-rules' / (c['id'] + '.conf')
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if dest.is_symlink() or any(p.is_symlink() for p in dest.parents):
                            raise ValueError('규칙 경로에 심볼릭 링크가 있습니다')
                        if rollback:
                            if dest.exists() and dest.read_bytes() != (directory / 'rule.conf').read_bytes():
                                raise ValueError('변경된 규칙은 자동 삭제하지 않습니다')
                            if not dest.exists() and journal.get('action') != 'rollback':
                                raise ValueError('삭제 대상 규칙이 없습니다')
                            write_json(journal_path, {'action': action, 'target': str(dest), 'at': time.time()})
                            dest.unlink(missing_ok=True)
                        else:
                            if dest.exists() and not (journal.get('action') == 'apply' and dest.read_bytes() == (directory / 'rule.conf').read_bytes()):
                                raise ValueError('동일 변경 규칙이 이미 존재합니다')
                            write_json(journal_path, {'action': action, 'target': str(dest), 'at': time.time()})
                            shutil.copy2(directory / 'rule.conf', dest)
                        try:
                            run(['docker', 'exec', 'kt66-web', 'apachectl', '-t'])
                            if not rollback:
                                included = run(['docker', 'exec', 'kt66-web', 'apachectl', '-t', '-D', 'DUMP_INCLUDES'])
                                if '/etc/modsecurity/kt66-requests/' + dest.name not in included:
                                    raise ValueError('실제 Apache 설정에 변경 규칙이 연결되지 않았습니다')
                            run(['docker', 'exec', 'kt66-web', 'apachectl', '-k', 'graceful'])
                            if not rollback:
                                for attempt in range(5):
                                    code = run(['docker', 'exec', 'kt66-web', 'curl', '--noproxy', '*', '-sS',
                                        '-G', '--data-urlencode', c['metadata']['parameter'] + '=' + c['metadata']['payload'],
                                        '-o', '/dev/null', '-w', '%{http_code}', 'http://127.0.0.1/']).strip()
                                    if code == '403':
                                        break
                                    time.sleep(0.3)
                                if code != '403':
                                    raise ValueError('적용 후 페이로드 차단 검증 실패')
                                normal = run(['docker', 'exec', 'kt66-web', 'curl', '--noproxy', '*', '-sS',
                                    '-o', '/dev/null', '-w', '%{http_code}', 'http://127.0.0.1/']).strip()
                                if normal != '200':
                                    raise ValueError('적용 후 정상 요청 검증 실패')
                        except Exception:
                            if rollback:
                                shutil.copy2(directory / 'rule.conf', dest)
                            else:
                                dest.unlink(missing_ok=True)
                            run(['docker', 'exec', 'kt66-web', 'apachectl', '-k', 'graceful'])
                            raise
                        c['result'] = dict(rule_id=c['metadata']['rule_id'], apache_syntax=True,
                                           reloaded=True, rolled_back=rollback,
                                           normal_http=None if rollback else normal, attack_http=None if rollback else code)
                    else:
                        raise ValueError('지원하지 않는 변경 종류')
                    c['status'] = 'rolled_back' if rollback else 'applied'
                    c['completed_at'] = time.time()
                except Exception as error:
                    c['status'] = 'failed'
                    c['error'] = str(error)[:1000]
                d['events'].append(dict(at=time.time(), kind='change_' + c['status'], detail=c['summary'], change=c['id']))
            d['status'] = ('blocked' if any(c['status'] == 'failed' for c in d['changes']) else
                           'waiting_approval' if any(c['status'] == 'proposed' for c in d['changes']) else 'completed')
            if d['status'] == 'completed':
                for agent in d['agents']:
                    agent['state'] = 'archived'
