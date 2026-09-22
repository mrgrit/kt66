"""업무 내용·산출물 인증, 지침 편집 충돌과 경로 검증."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'agentops'))
sys.path.insert(0,str(ROOT/'agents'))
from requests_api import install
from work_requests import Store


class Api(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'agents';self.root.mkdir()
        shutil.copytree(ROOT/'agents/native',self.root/'native')
        shutil.copy2(ROOT/'agents/roster.yaml',self.root/'roster.yaml')
        app=FastAPI()
        install(app,self.root,'test-key',None,lambda p,t:p.write_text(t))
        self.client=TestClient(app)
        self.headers={'X-API-Key':'test-key'}

    def create(self):
        return self.client.post('/api/requests',headers=self.headers,json={'prompt':'보안 자산 조회'}).json()

    def test_every_data_endpoint_requires_header_auth(self):
        d=self.create();rid=d['id']
        for method,path,body in [('GET','/api/requests',None),('GET',f'/api/requests/{rid}',None),('POST',f'/api/requests/{rid}/reply',{'message':'추가'}),('POST',f'/api/requests/{rid}/cancel',None),('GET',f'/api/requests/{rid}/artifacts/file.txt',None),('GET','/api/request-guides',None),('PUT','/api/request-guides/AGENTS.md',{'content':'수정'})]:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method,path,json=body).status_code,401)
                self.assertEqual(self.client.request(method,path+'?key=test-key',json=body).status_code,401)

    def test_authenticated_create_detail_and_attachment(self):
        d=self.create();rid=d['id']
        self.assertEqual(self.client.get('/api/requests',headers=self.headers).json()['requests'][0]['id'],rid)
        path=self.root/'tickets/requests'/rid/'workspace/index.html';path.write_text('<script>alert(1)</script>')
        r=self.client.get(f'/api/requests/{rid}/artifacts/index.html',headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertIn('attachment',r.headers['content-disposition'])
        self.assertIn('sandbox',r.headers['content-security-policy'])
        r=self.client.get(f'/api/requests/{rid}',headers=self.headers)
        self.assertEqual(r.json()['artifacts'][0]['path'],'index.html')

    def test_bad_input_rejected(self):
        for body in ({},{'prompt':''},{'prompt':'업무','budget':True},{'prompt':'업무','scope':'shell'},{'prompt':'업무','extra':'bad'}):
            self.assertEqual(self.client.post('/api/requests',headers=self.headers,json=body).status_code,400)

    def test_worker_directory_and_direct_chat_are_authenticated_and_validated(self):
        self.assertEqual(self.client.get('/api/request-workers').status_code,401)
        workers=self.client.get('/api/request-workers',headers=self.headers).json()['workers']
        self.assertIn('soc-analyst',[w['id'] for w in workers])
        body={'mode':'conversation','worker':'soc-analyst','prompt':'로그를 봐줘','timezone':'Asia/Seoul'}
        self.assertEqual(self.client.post('/api/requests',json=body).status_code,401)
        r=self.client.post('/api/requests',headers=self.headers,json=body)
        self.assertEqual(r.status_code,200)
        d=r.json();self.assertEqual(d['tasks'][0]['worker'],'soc-analyst')
        self.assertEqual(d['scope'],'read')
        row=self.client.get('/api/requests',headers=self.headers).json()['requests'][0]
        self.assertEqual((row['mode'],row['worker']),('conversation','soc-analyst'))
        self.assertEqual(self.client.post('/api/requests/'+d['id']+'/reply',headers=self.headers,json={'message':'추가'}).status_code,400)
        for field,value in [('worker','missing'),('mode','invalid'),('timezone','../etc/passwd'),('scope','prepare')]:
            self.assertEqual(self.client.post('/api/requests',headers=self.headers,json={**body,field:value}).status_code,400)

    def test_guide_save_preserves_standard_metadata_and_detects_conflict(self):
        path='/api/request-guides/.agents/skills/inventory-report/SKILL.md'
        d=self.client.get(path,headers=self.headers).json()
        bad=self.client.put(path,headers=self.headers,json={'sha256':d['sha256'],'content':'필드 없음'})
        self.assertEqual(bad.status_code,400)
        saved=self.client.put(path,headers=self.headers,json={'sha256':d['sha256'],'content':d['content']+'\n결론을 먼저 쓰세요.\n'})
        self.assertEqual(saved.status_code,200)
        stale=self.client.put(path,headers=self.headers,json={'sha256':d['sha256'],'content':d['content']})
        self.assertEqual(stale.status_code,400)
        self.assertNotEqual(d['sha256'],saved.json()['sha256'])

    def test_guide_path_traversal_is_not_editable(self):
        for name in ('%2e%2e%2f.env','%2fetc%2fpasswd','missing.md'):
            r=self.client.get('/api/request-guides/'+name,headers=self.headers)
            self.assertEqual(r.status_code,404)

    def test_permission_decisions_and_revocation_require_auth_and_preserve_scope(self):
        d=self.client.post('/api/requests',headers=self.headers,json={'mode':'conversation','worker':'systems-engineer','prompt':'디스크 확인'}).json()
        rid=d['id'];store=Store(self.root)
        with store.edit(rid) as row:
            row['status']='waiting_input';row['tasks'][0]['status']='waiting_input'
            row['permission_requests']=[dict(id='permission-test',worker='systems-engineer',tool='disk_usage',permission='metrics_read',arguments={'threshold_pct':80},fingerprint='test',task_id=row['tasks'][0]['id'],revision=1,status='pending')]
        path=f'/api/requests/{rid}/permissions/permission-test'
        self.assertEqual(self.client.get('/api/tool-permissions').status_code,401)
        self.assertEqual(self.client.post(path,json={'decision':'always'}).status_code,401)
        self.assertEqual(self.client.post(path,headers=self.headers,json={'decision':'invalid'}).status_code,400)
        self.assertEqual(self.client.post(path,headers=self.headers,json={'decision':'defer'}).status_code,200)
        self.assertEqual(store.get(rid)['status'],'waiting_input')
        self.assertEqual(self.client.post(path,headers=self.headers,json={'decision':'always'}).status_code,200)
        self.assertEqual(self.client.post(path,headers=self.headers,json={'decision':'always'}).status_code,400)
        grants=self.client.get('/api/tool-permissions',headers=self.headers).json()['grants']
        self.assertEqual(len(grants),1);self.assertEqual(grants[0]['worker'],'systems-engineer')
        self.assertEqual(grants[0]['scope'],'user_requests')
        revoke='/api/tool-permissions/'+grants[0]['id']
        self.assertEqual(self.client.delete(revoke).status_code,401)
        self.assertEqual(self.client.delete(revoke,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/tool-permissions',headers=self.headers).json()['grants'],[])

if __name__=='__main__':unittest.main()
