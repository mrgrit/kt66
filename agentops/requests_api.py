"""사용자 업무와 교육용 지침 편집. 모든 자료 API는 강사 키로 인증한다."""
import hashlib
import hmac
import json
from pathlib import Path
import sys

from fastapi import Body, HTTPException, Request
from fastapi.responses import FileResponse


def install(app, root, key, templates, backup_write):
    root = Path(root)
    sys.path.insert(0, str(root))
    from work_requests import Store
    from request_tools import safe_file
    from request_changes import authorize
    store = Store(root)

    def auth(request):
        if not key or not hmac.compare_digest(request.headers.get('x-api-key', ''), key):
            raise HTTPException(401, '강사 키를 확인하세요')

    def perform(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError:
            raise HTTPException(404, '요청 또는 파일을 찾을 수 없습니다')
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(400, str(error))

    def detail(rid):
        return store.detail(rid)

    @app.get('/requests')
    def page(request: Request):
        return templates.TemplateResponse('requests.html', {'request': request})

    @app.get('/api/requests')
    def listing(request: Request):
        auth(request)
        health = root / 'tickets' / 'loop-engine-status.json'
        return {'requests': store.list(), 'runner': json.loads(health.read_text()) if health.exists() else None}

    @app.get('/api/request-workers')
    def workers(request: Request):
        auth(request)
        return {'workers': perform(store.workers)}

    @app.post('/api/requests')
    def create(request: Request, body: dict = Body(...)):
        auth(request)
        if set(body) - {'prompt', 'title', 'scope', 'budget', 'max_agents', 'mode', 'worker', 'timezone'}:
            raise HTTPException(400, '지원하지 않는 요청 필드')
        return perform(store.create, **body)

    @app.get('/api/requests/{rid}')
    def get(rid: str, request: Request):
        auth(request)
        return perform(detail, rid)

    @app.post('/api/requests/{rid}/reply')
    def reply(rid: str, request: Request, body: dict = Body(...)):
        auth(request)
        return perform(store.reply, rid, body.get('message'))

    @app.post('/api/requests/{rid}/cancel')
    def cancel(rid: str, request: Request):
        auth(request)
        return perform(store.cancel, rid)

    @app.post('/api/requests/{rid}/budget')
    def budget(rid: str, request: Request, body: dict = Body(...)):
        auth(request)
        def update():
            value = body.get('budget')
            if type(value) is not int or not 1 <= value <= 30:
                raise ValueError('세션 예산은 1~30입니다')
            with store.edit(rid) as data:
                if value < data['sessions']:
                    raise ValueError('이미 사용·배정한 세션보다 예산을 낮출 수 없습니다')
                data['budget'] = value
                if data['status'] == 'blocked' and (data.get('result') or {}).get('reason') == 'budget':
                    data['status'] = 'queued'
            return store.get(rid)
        return perform(update)

    @app.post('/api/requests/{rid}/changes/{cid}/{action}')
    def change(rid: str, cid: str, action: str, request: Request, body: dict = Body(...)):
        auth(request)
        return perform(authorize, root, rid, cid, body.get('sha256'), action)

    @app.get('/api/requests/{rid}/artifacts/{name:path}')
    def artifact(rid: str, name: str, request: Request):
        auth(request)
        def download():
            path = safe_file(store.directory(rid) / 'workspace', name)
            if not path.is_file():
                raise FileNotFoundError(name)
            return FileResponse(path, filename=path.name, media_type='application/octet-stream',
                                headers={'Content-Security-Policy': "sandbox; default-src 'none'", 'X-Content-Type-Options': 'nosniff'})
        return perform(download)

    def documents():
        return {str(p.relative_to(root / 'native')): p for p in (root / 'native').rglob('*.md') if not p.is_symlink()}

    @app.get('/api/request-guides')
    def guide_list(request: Request):
        auth(request)
        return {'files': sorted(documents())}

    @app.get('/api/request-guides/{name:path}')
    def guide_get(name: str, request: Request):
        auth(request)
        path = documents().get(name)
        if path is None:
            raise HTTPException(404, '편집 가능한 지침 파일이 아닙니다')
        return {'path': name, 'content': path.read_text(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    @app.put('/api/request-guides/{name:path}')
    def guide_save(name: str, request: Request, body: dict = Body(...)):
        auth(request)
        path = documents().get(name)
        if path is None:
            raise HTTPException(404, '편집 가능한 지침 파일이 아닙니다')
        def save():
            import fcntl
            import yaml
            content = body.get('content')
            if not isinstance(content, str) or not content.strip() or len(content) > 30000:
                raise ValueError('지침은 1~30000자입니다')
            if name.endswith('SKILL.md') or name.startswith('.claude/agents/'):
                parts = content.split('---', 2)
                if len(parts) != 3 or parts[0].strip():
                    raise ValueError('---로 구분된 표준 메타데이터가 필요합니다')
                meta = yaml.safe_load(parts[1])
                original = yaml.safe_load(path.read_text().split('---', 2)[1])
                if not isinstance(meta, dict) or meta.get('name') != original['name'] or not isinstance(meta.get('description'), str) or not meta['description'].strip():
                    raise ValueError('name을 유지하고 description을 입력하세요')
            with (root / 'native' / '.edit.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if body.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError('다른 편집 내용이 있습니다. 다시 불러온 뒤 저장하세요')
                backup_write(path, content)
            return {'saved': True, 'applies': '다음 업무 세션부터 반영됩니다', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        return perform(save)
