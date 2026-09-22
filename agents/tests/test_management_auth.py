"""관리 키 공백을 인증 해제로 해석하지 않는지 검사한다. 서비스 시작 부작용은 제외한다."""
import ast
from pathlib import Path
import types
import unittest

from fastapi import HTTPException, Request

ROOT=Path(__file__).resolve().parents[2]


class ManagementAuth(unittest.TestCase):
    def test_empty_key_never_disables_management_authentication(self):
        # 운영 모듈은 import 때 DB/스케줄러/장치에 연결한다. 실제 인증 함수만 분리해 실행한다.
        for file,name,header in [('agentops/app.py','_auth',False),('envsim/app.py','_auth',False),
                                  ('injector/app.py','_auth',False),('modelops/app.py','_auth',True),
                                  ('bastion/api.py','_check_api_key',False),('noc/app.py','_auth',False)]:
            with self.subTest(service=file):
                node=next(n for n in ast.parse((ROOT/file).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name)
                space={'HTTPException':HTTPException,'Request':Request,'API_KEY':''}
                exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),space)
                call=lambda value:space[name](types.SimpleNamespace(headers={'x-api-key':value}) if header else value)
                for key in ('','incorrect','configured',None):
                    with self.assertRaises(HTTPException) as error:call(key)
                    self.assertEqual(error.exception.status_code,401)
                space['API_KEY']='configured'
                for key in ('','incorrect',None):
                    with self.assertRaises(HTTPException):call(key)
                self.assertIsNone(call('configured'))


if __name__=='__main__':unittest.main()
