"""근무자 운영의 스킬 라이브러리·R&R 편집 API."""
import hmac
from pathlib import Path

from fastapi import Body, HTTPException, Request
import yaml

import configuration as config


def install(app, root, key, write, backup):
    root = Path(root)

    def auth(request):
        if not key or not hmac.compare_digest(request.headers.get("x-api-key", ""), key):
            raise HTTPException(401, "강사 키를 확인하세요")

    def perform(fn):
        try:
            return fn()
        except FileNotFoundError:
            raise HTTPException(404, "설정 파일을 찾을 수 없습니다")
        except config.Conflict as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
            raise HTTPException(400, str(exc))

    def activate():
        import harness_compiler
        try:
            harness_compiler.compile_all(root)
        except Exception as exc:
            raise HTTPException(409, "원본은 저장됐지만 하네스 적용에 실패했습니다. 적용 상태를 확인하세요: " + str(exc))

    @app.get("/api/skills")
    def skills():
        return perform(lambda: config.library(root))

    @app.get("/api/config-audit")
    def audit():
        return perform(lambda: config.audit(root))

    @app.get("/api/skills/{name}")
    def skill(name: str, request: Request):
        auth(request)
        def read():
            path = config.skill_path(root, name)
            content = path.read_text()
            return {"name": name, "path": str(path.relative_to(root)), "content": content, "sha256": config.sha(content)}
        return perform(read)

    @app.post("/api/skills")
    def create(request: Request, body: dict = Body(...)):
        auth(request)
        def save():
            with config.edit_lock(root):
                name, content = body.get("name"), body.get("content")
                path = config.skill_path(root, name)
                if path.exists():
                    raise HTTPException(409, "이미 있는 스킬입니다")
                config.validate_skill(name, content)
                config.preflight(root, {str(path.relative_to(root)): content})
                write(path, content)
                activate()
                return {"saved": True, "name": name, "sha256": config.sha(content)}
        return perform(save)

    @app.put("/api/skills/{name}")
    def update(name: str, request: Request, body: dict = Body(...)):
        auth(request)
        def save():
            with config.edit_lock(root):
                path = config.skill_path(root, name)
                original = path.read_text()
                if body.get("sha256") != config.sha(original):
                    raise HTTPException(409, "다른 편집 내용이 있습니다. 다시 불러온 뒤 저장하세요")
                content = body.get("content")
                config.validate_skill(name, content)
                config.preflight(root, {str(path.relative_to(root)): content})
                write(path, content)
                activate()
                return {"saved": True, "sha256": config.sha(content)}
        return perform(save)

    @app.delete("/api/skills/{name}")
    def delete(name: str, request: Request, body: dict = Body(...)):
        auth(request)
        def remove():
            with config.edit_lock(root):
                path = config.skill_path(root, name)
                original = path.read_text()
                if body.get("sha256") != config.sha(original):
                    raise HTTPException(409, "다른 편집 내용이 있습니다. 다시 불러온 뒤 삭제하세요")
                if name in config.automatic_skills():
                    raise HTTPException(409, "업무 실행기가 자동 사용하는 필수 스킬입니다. 수정은 가능하지만 삭제할 수 없습니다")
                catalog = config.library(root)
                if catalog["errors"]:
                    raise HTTPException(409, "연결 상태를 확인할 수 없어 삭제하지 않았습니다:\n" + "\n".join(catalog["errors"]))
                row = next(s for s in catalog["skills"] if s["name"] == name)
                if row["workers"]:
                    raise HTTPException(409, "먼저 근무자·보관된 페르소나의 연결을 해제하세요: " +
                                        ", ".join(w["id"] for w in row["workers"]))
                config.preflight(root, {str(path.relative_to(root)): None})
                backup(path)
                path.unlink()
                activate()
                return {"deleted": True, "name": name}
        return perform(remove)

    @app.get("/api/assignments/{worker_id}")
    def assignment(worker_id: str, request: Request):
        auth(request)
        return perform(lambda: config.worker_detail(root, worker_id))

    @app.put("/api/assignments/{worker_id}")
    def assignment_save(worker_id: str, request: Request, body: dict = Body(...)):
        auth(request)
        def save():
            with config.edit_lock(root):
                updates = config.assignment_changes(root, worker_id, body)
                config.preflight(root, updates)
                for relative, text in updates.items():
                    write(config.safe_path(root, relative), text)
                activate()
                return config.worker_detail(root, worker_id)
        return perform(save)
