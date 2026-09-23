"""연구소와 xOC의 인증된 운영 화면. GET은 세션·탐지·평가를 시작하지 않는다."""
import hmac
from fastapi import Body, HTTPException, Request
import yaml


def install(app, root, key, templates, write):
    import xoc
    import research_lab
    import research_benchmarks

    @app.middleware('http')
    async def private_records(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(('/api/xoc', '/api/research-lab', '/api/compliance-evidence', '/api/monitoring')):
            response.headers['Cache-Control'] = 'private, no-store'
        return response

    def auth(request):
        if not key or not hmac.compare_digest(request.headers.get('x-api-key', ''), key):
            raise HTTPException(401, '제한구역입니다. 강사 키를 입력하세요')

    def perform(fn):
        try:
            return fn()
        except (ValueError, TypeError, KeyError, yaml.YAMLError) as error:
            raise HTTPException(400, str(error))

    @app.get('/xoc', include_in_schema=False)
    def xoc_page(request: Request):
        return templates.TemplateResponse('operations-center.html', {'request': request, 'initial': 'xoc'})

    @app.get('/research-lab', include_in_schema=False)
    def lab_page(request: Request):
        return templates.TemplateResponse('research-lab.html', {'request': request})

    @app.get('/api/xoc')
    def xoc_status(request: Request):
        auth(request)
        return perform(lambda: xoc.snapshot(root))

    @app.post('/api/xoc/review')
    def xoc_review(request: Request, body: dict = Body(...)):
        auth(request)
        result = perform(lambda: xoc.review(root, body.get('finding_id'), body.get('status'), body.get('reason'), 'instructor'))
        if hasattr(app.state, 'monitor'):
            with app.state.monitor.lock:
                app.state.monitor.cache.clear()
        return result

    @app.post('/api/xoc/containment')
    def xoc_hold(request: Request, body: dict = Body(...)):
        auth(request)
        result = perform(lambda: xoc.containment(root, body.get('worker'), body.get('minutes'), body.get('reason'), 'instructor', body.get('finding_id')))
        if hasattr(app.state, 'monitor'):
            with app.state.monitor.lock:
                app.state.monitor.cache.clear()
        return result

    @app.get('/api/compliance-evidence')
    def compliance_status(request: Request):
        auth(request)
        return perform(lambda: xoc.compliance(root))

    @app.get('/api/research-lab')
    def lab_status(request: Request):
        auth(request)
        return perform(lambda: research_lab.catalog(root))

    @app.post('/api/research-lab/candidates')
    def lab_candidate(request: Request, body: dict = Body(...)):
        auth(request)
        return perform(lambda: research_lab.propose(root, body, 'instructor'))

    @app.get('/api/research-lab/example')
    def lab_example(request: Request):
        auth(request)
        return perform(lambda: research_lab.example(root))

    @app.get('/api/research-lab/candidates/{cid}')
    def lab_detail(cid: str, request: Request):
        auth(request)
        return perform(lambda: research_lab.detail(root, cid))

    @app.get('/api/research-lab/suites/{sid}')
    def lab_suite(sid: str, request: Request):
        auth(request)
        return perform(lambda: research_benchmarks.get(root, sid))

    @app.post('/api/research-lab/suites/{sid}')
    def lab_suite_save(sid: str, request: Request, body: dict = Body(...)):
        auth(request)
        roles = {w['security_role'] for w in research_lab.workers(root).values()}
        return perform(lambda: research_benchmarks.save(root, sid, body.get('yaml'), body.get('revision'), roles))

    @app.post('/api/research-lab/suites/{sid}/delete')
    def lab_suite_delete(sid: str, request: Request, body: dict = Body(...)):
        auth(request)
        return perform(lambda: research_benchmarks.delete(root, sid, body.get('revision')))

    @app.post('/api/research-lab/candidates/{cid}/{action}')
    def lab_action(cid: str, action: str, request: Request, body: dict = Body(...)):
        auth(request)
        if action == 'evaluate':
            # 제출자는 강사라도 평가는 별도 평가원의 고정 실행 경로다.
            return perform(lambda: research_lab.queue(root, cid, 'skill-evaluator', requested_by='instructor'))
        if action == 'archive':
            return perform(lambda: research_lab.archive(root, cid))
        if action in ('apply', 'rollback'):
            return perform(lambda: research_lab.apply_candidate(root, cid, action == 'rollback', body.get('reason'), write))
        raise HTTPException(404, '지원하지 않는 작업입니다')
