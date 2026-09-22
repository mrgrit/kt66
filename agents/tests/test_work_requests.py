"""요청 상태·권한·교육용 지침·변경 검증의 회귀 검사."""
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import work_requests as wr
import loop_engine
import request_runtime
import request_tools
import request_changes
import siem_query
import session_cli


class Requests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'agents'
        self.root.mkdir()
        for name in ('company.yaml','departments.yaml','teams.yaml','roster.yaml','harness.yaml'):
            shutil.copy2(ROOT/name, self.root/name)
        for name in ('personas','loops','native'):
            shutil.copytree(ROOT/name, self.root/name, ignore=shutil.ignore_patterns('.edit.lock'))
        (self.root.parent/'.env').write_text('API_KEY=test-key\nINT_HOST_IP=127.0.0.1\n')
        self.store=wr.Store(self.root)
        self.req=self.store.create('보안 자산 IP를 정리하세요')
        self.rid=self.req['id']
        with patch.object(loop_engine,'ROOT',self.root):
            self.db=loop_engine.db_open()
        self.addCleanup(self.db.close)

    def poll(self):
        wr.poll(self.root,self.db,loop_engine.enqueue)

    def finish(self, task_id, status='completed'):
        task=next(t for t in self.store.get(self.rid)['tasks'] if t['id']==task_id)
        self.db.execute('UPDATE jobs SET status=?,result=?,evidence=? WHERE id=?',
            ('completed', json.dumps({'request_outcome':{'status':status,'summary':'근거 확인','question':'','artifacts':[]}}),'/evidence/test',task['job_id']))
        self.db.commit()

    def task(self, ident, deps=None, worker='soc-analyst'):
        return dict(id=ident,title='조회',instructions='조회 후 결과 저장',worker=worker,depends_on=deps or [],capabilities=['inventory'])

    def test_read_only_poll_does_not_rewrite_completed_request(self):
        self.poll();self.finish('plan-1');self.poll()
        before=(self.store.directory(self.rid)/'request.json').stat().st_mtime_ns
        self.poll()
        self.assertEqual(before,(self.store.directory(self.rid)/'request.json').stat().st_mtime_ns)

    def chat(self, **kwargs):
        self.store.cancel(self.rid)
        self.req=self.store.create('1시간 전부터 시스템이 좀 이상해. 로그 한번 봐줘.',
                                   mode='conversation',worker='soc-analyst',timezone='Asia/Seoul',**kwargs)
        self.rid=self.req['id']

    def test_conversation_dispatches_selected_worker_once_without_review(self):
        self.chat();self.poll();self.poll()
        job=self.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(job['worker'],'soc-analyst')
        self.finish('conversation-1');self.poll();self.poll()
        d=self.store.get(self.rid)
        self.assertEqual(d['status'],'completed')
        self.assertEqual((d['sessions'],len(d['tasks'])),(1,1))
        self.assertEqual((d['scope'],d['max_agents']),('read',0))

    def test_conversation_followup_keeps_worker_and_history_and_waits_without_calls(self):
        self.chat();self.poll();self.finish('conversation-1','waiting_input');self.poll()
        for _ in range(3):self.poll()
        self.assertEqual(self.store.get(self.rid)['sessions'],1)
        self.store.reply(self.rid,'웹 서버가 느려요');self.poll()
        d=self.store.context(self.rid)
        self.assertEqual([t['worker'] for t in d['request']['tasks']],['soc-analyst']*2)
        self.assertEqual([m['role'] for m in d['request']['messages']],['user','agent','user'])
        self.assertEqual(d['request']['messages'][-1]['text'],'웹 서버가 느려요')
        self.assertEqual(d['request']['sessions'],2)

    def test_conversation_rejects_unknown_worker_writes_and_busy_reply(self):
        for args in ({'worker':'missing'}, {'worker':'soc-analyst','scope':'prepare'},
                     {'worker':'soc-analyst','max_agents':1}, {'worker':'soc-analyst','timezone':'../bad'}):
            with self.assertRaises(ValueError):self.store.create('대화',mode='conversation',**args)
        self.chat();before=self.store.get(self.rid)
        with self.assertRaisesRegex(ValueError,'답변 중'):self.store.reply(self.rid,'추가')
        self.assertEqual(before,self.store.get(self.rid))

    def test_relative_time_anchors_to_latest_user_message_not_execution_clock(self):
        with patch.object(wr.time,'time',return_value=1700000000):self.chat()
        self.poll();self.finish('conversation-1');self.poll()
        first=self.store.context(self.rid)['turn']
        self.assertEqual(first['sent_at'],'2023-11-14T22:13:20+00:00')
        self.assertEqual(first['local_sent_at'],'2023-11-15T07:13:20+09:00')
        self.assertEqual(first['last_hour']['start'],'2023-11-14T21:13:20+00:00')
        with patch.object(wr.time,'time',return_value=1700003600):self.store.reply(self.rid,'지금 최근 1시간은?')
        self.assertEqual(self.store.context(self.rid)['turn']['last_hour']['start'],first['sent_at'])

    def test_conversation_compiles_selected_persona_with_read_only_tools(self):
        self.chat();self.poll();dest,m=self.compile()
        self.assertEqual(m['worker']['id'],'soc-analyst')
        self.assertEqual(m['request']['phase'],'conversation')
        self.assertEqual(set(m['request']['capabilities']),{'inventory','siem'})
        self.assertNotIn('request-coordination',m['request']['skills'])
        self.assertIn('근무자와 직접 대화',(dest/'AGENTS.md').read_text())
        import harness_tools
        with patch.object(harness_tools,'ROOT',self.root):
            broker=harness_tools.Broker(dest/'manifest.json',dest.parent)
            for name,args in [('request_plan',{'tasks':[self.task('a')]}),('workspace_write',{'path':'test.txt','content':'내용'})]:
                self.assertEqual(broker.call(name,args)['status'],'denied')
            with self.assertRaisesRegex(ValueError,'response_kind'):
                broker.call('request_finish',dict(status='completed',summary='설명',question='',artifacts=[]))

    def test_conversation_explanation_needs_no_new_query_but_investigation_does(self):
        self.chat();self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        for kind,expected in [('reply','completed'),('investigation','blocked')]:
            response=dict(body=json.dumps(dict(status='completed',summary='응답',question='',artifacts=[],response_kind=kind)),session_id='fake')
            with patch.object(session_cli,'run',return_value=response):result=wr.execute(self.root,job)
            self.assertEqual(result['request_outcome']['status'],expected)
            self.assertFalse(result['verification']['observed_live_evidence'])

    def test_conversation_query_evidence_is_preserved_for_followup(self):
        self.chat();self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        query=self.store.context(self.rid)['turn']['last_hour']
        def session(*args,**kwargs):
            (kwargs['evidence_dir']/'tools.jsonl').write_text(json.dumps(dict(tool='siem_search',arguments=query,result={'total':0}))+'\n')
            return dict(body=json.dumps(dict(status='completed',summary='보관 로그 내 경보 없음',question='',artifacts=[],response_kind='investigation')),session_id='fake')
        with patch.object(session_cli,'run',side_effect=session):result=wr.execute(self.root,job)
        self.db.execute('UPDATE jobs SET status=?, result=?, evidence=? WHERE id=?',('completed',json.dumps(result),result['evidence'],job['id']));self.db.commit();self.poll()
        d=self.store.context(self.rid)['request']
        self.assertEqual(d['messages'][-1]['verification']['queries'],[query])
        self.assertEqual(d['messages'][-1]['verification']['tools'],['siem_search'])
        self.assertTrue(d['messages'][-1]['verification']['observed_live_evidence'])

    def test_disk_probe_counts_only_actual_measurements_as_completion_evidence(self):
        self.chat();self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        for status,count,expected in [('partial',1,'completed'),('unavailable',0,'blocked')]:
            def session(*args,**kwargs):
                (kwargs['evidence_dir']/'tools.jsonl').write_text(json.dumps(dict(tool='disk_usage',arguments={},result={'status':status,'measured_targets':count}))+'\n')
                return dict(body=json.dumps(dict(status='completed',summary='점검 결과',question='',artifacts=[],response_kind='investigation')),session_id='fake')
            with patch.object(session_cli,'run',side_effect=session):result=wr.execute(self.root,job)
            self.assertEqual(result['request_outcome']['status'],expected)
            self.assertEqual(result['verification']['observed_live_evidence'],bool(count))

    def test_pending_tool_permission_keeps_request_waiting_even_if_model_says_completed(self):
        import harness_tools
        self.store.cancel(self.rid)
        self.rid=self.store.create('디스크 조회',mode='conversation',worker='systems-engineer')['id']
        self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        def session(*args,**kwargs):
            with patch.object(harness_tools,'ROOT',self.root):
                b=harness_tools.Broker(kwargs['harness']/'manifest.json',kwargs['evidence_dir'])
                b.permissions['metrics_read']='ask'
                self.assertEqual(b.call('disk_usage',{})['status'],'approval_required')
            return dict(body=json.dumps(dict(status='completed',summary='권한 안내',question='',artifacts=[],response_kind='reply')),session_id='fake')
        with patch.object(session_cli,'run',side_effect=session):result=wr.execute(self.root,job)
        self.assertEqual(result['request_outcome']['status'],'waiting_input')
        self.assertTrue(result['request_outcome']['question'])
        self.assertFalse(result['verification']['observed_live_evidence'])

    def test_runtime_listing_includes_requests_beyond_ui_limit(self):
        for i in range(3):self.store.create('요청 '+str(i))
        self.assertEqual(len(self.store.list(limit=2)),2)
        self.assertEqual(len(self.store.list(limit=None)),4)

    def test_detail_includes_final_answer_without_rewriting_original_record(self):
        name='loop-test-service-desk'
        evidence=self.root/'evidence'/name;evidence.mkdir(parents=True)
        (evidence/'session-result.json').write_text(json.dumps({'body':'자산별 IP 목록과 실제 분석 본문'}))
        with self.store.edit(self.rid) as d:
            d['tasks'][0].update(evidence='/host/agents/evidence/'+name,outcome={'status':'completed','summary':'조회 완료'})
            d['messages'].append({'role':'agent','task':'plan-1','text':'조회 완료'})
        self.assertEqual(self.store.detail(self.rid)['messages'][-1]['text'],'자산별 IP 목록과 실제 분석 본문')
        self.assertEqual(self.store.get(self.rid)['messages'][-1]['text'],'조회 완료')

    def test_queue_is_idempotent_and_completion_needs_evidence_result(self):
        self.poll();self.poll()
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
        self.assertEqual(self.store.get(self.rid)['sessions'],1)
        self.finish('plan-1');self.poll()
        self.assertEqual(self.store.get(self.rid)['status'],'completed')

    def test_dependency_plan_and_final_review(self):
        self.poll()
        self.store.plan(self.rid,1,[self.task('one'),self.task('two',['one'])])
        self.finish('plan-1');self.poll()
        d=self.store.get(self.rid)
        self.assertEqual([t['status'] for t in d['tasks']],['completed','running','queued'])
        self.finish('v1-one');self.poll();self.finish('v1-two');self.poll()
        self.assertEqual(self.store.get(self.rid)['tasks'][-1]['phase'],'review')
        self.finish('review-1');self.poll()
        self.assertEqual(self.store.get(self.rid)['status'],'completed')
        self.assertEqual(self.store.get(self.rid)['sessions'],4)

    def test_invalid_dag_and_budget_never_register_partial_plan(self):
        self.poll()
        for tasks in ([self.task('a',['b']),self.task('b',['a'])],[self.task('a',['missing'])], [self.task('a'),self.task('a')]):
            with self.assertRaises(ValueError):self.store.plan(self.rid,1,tasks)
        with self.store.edit(self.rid) as d:d['budget']=2
        with self.assertRaisesRegex(ValueError,'예산'):self.store.plan(self.rid,1,[self.task('one')])
        self.assertEqual(len(self.store.get(self.rid)['tasks']),1)

    def test_waiting_for_reply_never_enqueues_extra_sessions(self):
        self.poll();self.finish('plan-1','waiting_input')
        for _ in range(5):self.poll()
        self.assertEqual(self.store.get(self.rid)['sessions'],1)
        self.assertEqual(self.store.get(self.rid)['status'],'waiting_input')

    def test_reply_and_cancel_revoke_old_task_and_pending_queue(self):
        self.poll();self.store.reply(self.rid,'변경한 범위로 처리하세요');self.poll()
        with self.assertRaises(ValueError):self.store.check(self.rid,'plan-1',1)
        self.assertEqual(self.db.execute("SELECT status FROM jobs WHERE id=?",(f'request:{self.rid}:plan-1',)).fetchone()[0],'superseded')
        self.store.cancel(self.rid);self.poll()
        self.assertEqual(self.store.get(self.rid)['status'],'cancelled')
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0],0)

    def test_applying_request_cannot_be_revised_or_cancelled(self):
        with self.store.edit(self.rid) as d:d['status']='applying'
        with self.assertRaises(ValueError):self.store.reply(self.rid,'수정')
        with self.assertRaises(ValueError):self.store.cancel(self.rid)

    def test_new_agent_reuses_template_and_does_not_change_roster(self):
        before=(self.root/'roster.yaml').read_bytes()
        a=self.store.agent(self.rid,1,'개발자','정적 홈페이지 담당','application-developer',['workspace','website'])
        b=self.store.agent(self.rid,1,'개발자','정적 홈페이지 담당','application-developer',['workspace','website'])
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(before,(self.root/'roster.yaml').read_bytes())
        with self.assertRaises(ValueError):self.store.agent(self.rid,1,'임의 실행','임의 셸','service-desk',['shell'])
        self.store.cancel(self.rid)
        self.assertEqual(self.store.get(self.rid)['agents'][0]['state'],'archived')

    def compile(self):
        d=self.store.get(self.rid)
        return request_runtime.compile_request(self.root,d,d['tasks'][0],self.root/'evidence'/str(len(list((self.root/'evidence').glob('*')))))

    def test_korean_skill_edit_changes_next_harness_not_previous_snapshot(self):
        self.chat()
        dest,m=self.compile()
        source=self.root/'native/.agents/skills/inventory-report/SKILL.md'
        original=(dest/'.agents/skills/inventory-report/SKILL.md').read_text()
        source.write_text(source.read_text()+'\n결과에 교육용 비교 표를 추가하세요.\n')
        new,m2=self.compile()
        self.assertNotEqual(m['version'],m2['version'])
        self.assertEqual(original,(dest/'.agents/skills/inventory-report/SKILL.md').read_text())
        self.assertIn('교육용 비교 표',(new/'.claude/skills/inventory-report/SKILL.md').read_text())
        self.assertIn('inventory_query',m2['available_tools'])

    def test_soc_request_and_routine_share_skill_without_inlining_body(self):
        import harness_compiler, harness_tools
        self.chat();self.poll();dest,m=self.compile()
        skill='ip-risk-investigation'
        routine,base=harness_compiler.compile_worker('soc-analyst',self.root)
        relative=Path('.agents/skills')/skill/'SKILL.md'
        body=(routine/relative).read_text()
        self.assertEqual((dest/relative).read_text(),body)
        self.assertEqual(m['role_skills'],base['role_skills'])
        self.assertIn(skill,m['request']['skills'])
        for name in ('AGENTS.md','HARNESS.md','.claude/agents/kt66-request-worker.md'):
            self.assertNotIn(body,(dest/name).read_text())
        with patch.object(harness_tools,'ROOT',self.root):
            b=harness_tools.Broker(dest/'manifest.json',dest.parent)
            self.assertEqual(b.call('skill_read',{'name':skill})['content'],body)
            self.assertEqual(b.call('skill_read',{'name':'siem-period-analysis'})['name'],'siem-period-analysis')
            with self.assertRaises(ValueError):b.call('skill_read',{'name':'project-development'})

    def test_common_role_edit_changes_both_runtimes(self):
        dest,m=self.compile()
        source=self.root/'native/.claude/agents/kt66-request-worker.md'
        source.write_text(source.read_text()+'\n재현 절차를 두 줄로 적으세요.\n')
        new,m2=self.compile()
        self.assertNotEqual(m['version'],m2['version'])
        for name in ('AGENTS.md','.claude/agents/kt66-request-worker.md','.codex/agents/kt66-request-worker.toml'):
            self.assertIn('재현 절차를 두 줄', (new/name).read_text())

    def test_read_only_scope_and_parent_deny_enforced(self):
        self.chat()
        with self.store.edit(self.rid) as d:d['scope']='read'
        dest,m=self.compile()
        self.assertEqual(set(m['request']['capabilities']),{'inventory','siem'})
        self.assertEqual(m['policy']['constrain']['permission']['simulation_control'],'deny')
        self.assertEqual(m['policy']['constrain']['permission']['ticket_create'],'deny')
        with self.assertRaises(ValueError):self.store.agent(self.rid,1,'개발','개발','service-desk',['workspace'])
        import yaml
        config=yaml.safe_load((self.root/'harness.yaml').read_text())
        config['defaults']['constrain']['permission']['user_request']='deny'
        (self.root/'harness.yaml').write_text(yaml.safe_dump(config))
        _,m=self.compile()
        self.assertEqual(m['policy']['constrain']['permission']['user_request'],'deny')

    def test_cancel_revokes_every_broker_tool(self):
        import harness_tools
        self.poll();dest,m=self.compile()
        with patch.object(harness_tools,'ROOT',self.root):
            b=harness_tools.Broker(dest/'manifest.json',dest.parent)
            self.store.cancel(self.rid)
            with self.assertRaises(ValueError):b.call('env_read',{})

    def test_completion_without_real_work_is_blocked(self):
        self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        response=dict(body=json.dumps(dict(status='completed',summary='다 했음',question='',artifacts=[])),session_id='test',usage={})
        with patch.object(session_cli,'run',return_value=response):r=wr.execute(self.root,job)
        self.assertEqual(r['request_outcome']['status'],'blocked')

    def test_finish_tool_persists_structured_outcome_despite_free_text_cli(self):
        self.poll();job=dict(self.db.execute('SELECT * FROM jobs').fetchone())
        def session(*args, **kwargs):
            evidence=kwargs['evidence_dir']
            class Broker:
                m=json.loads((kwargs['harness']/'manifest.json').read_text())
                permissions=m['policy']['constrain']['permission']
                path=kwargs['harness']/'manifest.json'
                def access(self,*args):pass
            broker=Broker();broker.session=evidence
            result=request_tools.call(broker,self.root,'request_finish',dict(status='waiting_input',summary='대상 확인 필요',question='분석 대상은 무엇인가요?',artifacts=[]))
            self.assertTrue(result['recorded'])
            return dict(body='자유 형식의 한국어 답변',session_id='test',usage={})
        with patch.object(session_cli,'run',side_effect=session):r=wr.execute(self.root,job)
        self.assertEqual(r['request_outcome']['status'],'waiting_input')
        self.assertEqual(r['request_outcome']['question'],'분석 대상은 무엇인가요?')

    def test_restart_does_not_silently_spend_another_request_session(self):
        self.poll();self.db.execute("UPDATE jobs SET status='running'");self.db.commit()
        with patch.object(loop_engine,'ROOT',self.root):
            reopened=loop_engine.db_open()
            self.assertEqual(reopened.execute('SELECT status FROM jobs').fetchone()[0],'failed')
            reopened.close()
        self.poll()
        self.assertEqual(self.store.get(self.rid)['status'],'blocked')
        self.assertEqual(self.store.get(self.rid)['sessions'],1)

    def test_workspace_escape_and_symlinks_rejected(self):
        workspace=self.store.directory(self.rid)/'workspace'
        (workspace/'link').symlink_to('/tmp')
        for name in ('../escape','/tmp/escape','.env','site/../../escape','link/file.txt'):
            with self.assertRaises(ValueError):request_tools.safe_file(workspace,name)
        self.assertEqual(request_tools.safe_file(workspace,'site/index.html'),workspace/'site/index.html')
        self.assertEqual(request_tools.safe_file(workspace,'보안_로그_분석보고서.md'),workspace/'보안_로그_분석보고서.md')

    def test_website_validation_rejects_broken_references_and_javascript(self):
        site=self.store.directory(self.rid)/'workspace/site';site.mkdir()
        (site/'index.html').write_text('<script src="absent.js"></script>')
        self.assertFalse(request_changes.website_validate(self.root,self.rid)['ok'])
        (site/'absent.js').write_text('const broken = ;')
        self.assertFalse(request_changes.website_validate(self.root,self.rid)['ok'])
        (site/'absent.js').write_text('const working = 1;')
        self.assertTrue(request_changes.website_validate(self.root,self.rid)['ok'])

    def test_approved_change_requires_final_review_and_exact_hash(self):
        import authorization
        task=self.task('rule',worker='network-engineer');task['capabilities']=['waf']
        task=self.store.plan(self.rid,1,[task])[0]
        p=authorization.load(self.root,'network-engineer')
        author=dict(worker=task['worker'],task_id=task['id'],template=p['template'],fingerprint=p['fingerprint'])
        c=dict(id='change-test',kind='waf',status='proposed',revision=1,sha256='fixed',author=author)
        with self.store.edit(self.rid) as d:d['changes']=[c]
        with self.assertRaises(ValueError):request_changes.authorize(self.root,self.rid,c['id'],'fixed')
        with self.store.edit(self.rid) as d:d['status']='waiting_approval'
        with self.assertRaises(ValueError):request_changes.authorize(self.root,self.rid,c['id'],'other')
        r=request_changes.authorize(self.root,self.rid,c['id'],'fixed')
        self.assertEqual(r['status'],'applying')

    def test_project_agent_compiles_with_own_identity_and_inherited_denials(self):
        agent=self.store.agent(self.rid,1,'웹 개발','정적 홈페이지 개발','application-developer',['workspace','website'])
        self.poll()
        task=self.task('build',worker=agent['id']);task['capabilities']=['workspace','website']
        self.store.plan(self.rid,1,[task]);d=self.store.get(self.rid)
        _,m=request_runtime.compile_request(self.root,d,d['tasks'][1],self.root/'evidence/project')
        self.assertEqual(m['worker']['id'],agent['id'])
        self.assertEqual(m['persona'],'정적 홈페이지 개발')
        self.assertNotIn('waf_prepare',m['available_tools'])
        self.assertEqual(m['authorization']['role'],'developer')
        self.assertEqual(set(m['request']['capabilities']),{'workspace','website'})

    def test_read_only_plan_cannot_assign_write_capability(self):
        with self.store.edit(self.rid) as d:d['scope']='read'
        task=self.task('report');task['capabilities']=['siem','workspace']
        with self.assertRaises(ValueError):self.store.plan(self.rid,1,[task])

    def test_native_role_is_delivered_to_cli(self):
        import subprocess
        dest,m=self.compile()
        def launch(cmd,**kw):
            if '--session-id' in cmd:
                role=json.loads(cmd[cmd.index('--agents')+1])['kt66-request-worker']
                self.assertIn('사용자 업무 담당자',role['prompt'])
                self.assertEqual(cmd[cmd.index('--agent')+1],'kt66-request-worker')
                self.assertNotIn('--append-system-prompt',cmd)
                return subprocess.CompletedProcess(cmd,0,json.dumps({'session_id':cmd[cmd.index('--session-id')+1],'result':'완료'}),'')
            raise AssertionError(cmd)
        with patch.object(session_cli,'executable',return_value='claude'), patch.object(session_cli,'authenticated',return_value={'method':'claude.ai'}), patch.object(session_cli.subprocess,'run',side_effect=launch):
            result=session_cli.run('claude','haiku','업무',harness=dest,evidence_dir=dest.parent)
        self.assertTrue(result['fresh_session'])

    def test_payload_cannot_inject_config_or_expand_macros(self):
        for payload in ('x\nSecRuleEngine Off','%{REMOTE_ADDR}'):
            with self.assertRaises(ValueError):request_changes.waf_prepare(self.root,self.rid,payload,'q')

    def test_changed_proposal_is_not_applied(self):
        directory=self.store.directory(self.rid)/'changes/change-test';directory.mkdir(parents=True)
        (directory/'rule.conf').write_text('modified')
        with self.store.edit(self.rid) as d:
            d['status']='applying';d['changes']=[dict(id='change-test',kind='waf',status='approved',sha256='old',summary='규칙',metadata={})]
        with patch.object(request_changes,'run') as run:request_changes.apply_pending(self.root,self.rid)
        run.assert_not_called()
        self.assertEqual(self.store.get(self.rid)['changes'][0]['status'],'failed')


class Siem(unittest.TestCase):
    def test_archive_and_current_pagination_timezone_and_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'2026/Sep/ossec-alerts-19.json.gz';archive.parent.mkdir(parents=True)
            rows=[dict(id=str(i),timestamp=f'2026-09-19T12:0{i}:00.000+0000',rule={'id':'100'}) for i in range(3)]
            with gzip.open(archive,'wt') as f:f.write('\n'.join(json.dumps(r) for r in rows))
            (root/'alerts.json').write_text(json.dumps(dict(id='outside',timestamp='2026-09-20T12:00:00Z')))
            query=dict(start=datetime.datetime.fromisoformat('2026-09-19T12:00:00+00:00').timestamp(),end=datetime.datetime.fromisoformat('2026-09-19T13:00:00+00:00').timestamp(),limit=1,cursor='')
            ids=[]
            for _ in range(8):
                result=siem_query.search(query,root);ids.extend(r['id'] for r in result['records'])
                if not result['next_cursor']:break
                query['cursor']=result['next_cursor']
            self.assertEqual(ids,['0','1','2'])
            self.assertEqual(result['coverage'],'retained_alert_files')
            result=siem_query.search({**query,'cursor':''},root/'missing')
            self.assertEqual(result['coverage'],'partial')

    def test_no_timezone_and_future_range_rejected_before_docker(self):
        with patch.object(request_tools.subprocess,'run') as run:
            with self.assertRaises(ValueError):request_tools.siem_search(ROOT,'2026-09-19T00:00:00','2026-09-19T05:00:00')
            with self.assertRaises(ValueError):request_tools.siem_search(ROOT,'2099-09-19T00:00:00Z','2099-09-19T05:00:00Z')
            run.assert_not_called()


if __name__=='__main__':unittest.main()
