"""Only disposable native cron jobs and Kanban databases; no network."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(dir=Path(os.environ.get('KANBAN_TEST_WORK',SOURCE/'test-work')), prefix='jm-'))
        os.environ['HERMES_HOME'] = os.environ['HERMES_KANBAN_HOME'] = str(self.home)
        self.assertTrue((SOURCE/'plugin'/'job_manager.py').is_file(), 'portable job-free adapter is missing')
        import job_manager
        self.m = job_manager
        from cron.jobs import create_job
        self.original = create_job('Synthetic original request', '0 9 * * *', name='Synthetic Agent', model='synthetic-model', provider='synthetic-provider', enabled_toolsets=['terminal'], deliver='local', workdir=str(self.home))

    def tearDown(self):
        os.environ.pop('SCHEDULED_JOBS_RUN', None)
        from hermes_cli.observability.relay_shared_metrics import shutdown_runtimes
        shutdown_runtimes()

    def test_registration_is_explicit_idempotent_preserves_unrelated_jobs_and_schedule(self):
        from cron.jobs import create_job, get_job
        from hermes_cli import kanban_db as db
        from hermes_cli.kanban_db_connect import connect_closing
        unrelated = create_job('Unrelated synthetic request', '0 10 * * *', deliver='local')
        self.assertEqual(self.m.status(), {})
        self.assertFalse(self.m.dispatch_hook(board='default'))
        self.assertEqual(get_job(self.original['id']), self.original)
        first = self.m.register_job(self.original['id'])
        self.assertEqual(self.m.register_job(self.original['id']), first)
        current = get_job(self.original['id'])
        self.assertEqual({k:v for k,v in current.items() if k not in ('script','no_agent','interpreter')}, {k:v for k,v in self.original.items() if k not in ('script','no_agent','interpreter')})
        self.assertEqual(get_job(unrelated['id']), unrelated)
        self.assertEqual(self.m.status()[self.original['id']]['original'], self.original)
        with connect_closing(board='default') as conn:
            cards = db.list_tasks(conn)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].status, 'scheduled')
            # no 'blocked' event: Desktop would toast "Task blocked — needs your input" for the waiting card
            self.assertEqual([k for (k,) in conn.execute('SELECT kind FROM task_events WHERE task_id=? ORDER BY id',(cards[0].id,))], ['created','scheduled'])
            self.assertEqual(db.recompute_ready(conn), 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0], 0)
        self.assertFalse(self.m.dispatch_hook(board='default'))

    def test_native_plugin_validation_and_fresh_skill_cli_discovery_are_job_free(self):
        self.assertTrue((SOURCE/'plugin'/'plugin.yaml').is_file(),'native plugin manifest missing')
        from hermes_cli.plugin_validate import validate_plugin_dir
        import shutil,subprocess,sys,json
        fresh=Path(tempfile.mkdtemp(dir=self.home.parent,prefix='fresh-'))
        payload=fresh/'plugins'/'hermes-kanban-job-manager'; payload.mkdir(parents=True)
        for name in ('__init__.py','job_manager.py','controller_utils.py','plugin.yaml'):
            shutil.copy2(SOURCE/'plugin'/name,payload/name)
        shutil.copytree(SOURCE/'plugin'/'skills',payload/'skills')
        report=validate_plugin_dir(payload)
        self.assertTrue(report.ok,str(report))
        env=dict(os.environ,HERMES_HOME=str(fresh),HERMES_KANBAN_HOME=str(fresh))
        def cli(*args):
            result=subprocess.run([sys.executable,'-m','hermes_cli.main',*args],env=env,capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return result.stdout
        cli('plugins','enable','hermes-kanban-job-manager','--no-allow-tool-override')
        self.assertIn('hermes-kanban-job-manager',cli('plugins','list','--plain','--no-bundled'))
        self.assertEqual(json.loads(cli('kanban-jobs','status')), {})
        self.assertEqual(json.loads(cli('kanban-jobs','list')), {})
        self.assertFalse((fresh/'cron'/'jobs.json').exists())
        self.assertFalse((fresh/'kanban.db').exists())
        probe="from hermes_cli.plugins import discover_plugins,get_plugin_manager; discover_plugins(); p=get_plugin_manager().find_plugin_skill('hermes-kanban-job-manager:kanban-job-manager'); assert p; print(p)"
        result=subprocess.run([sys.executable,'-c',probe],env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('SKILL.md',result.stdout)

    def test_verifier_refuses_windows_batch_metacharacters_before_launch(self):
        import ast,subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        tree=ast.parse((SOURCE/'verify_install.py').read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='cli')
        namespace={'os':os,'subprocess':subprocess,'env':{}}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'verifier-cli','exec'),namespace)
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')) as launch:
            with self.assertRaisesRegex(ValueError,'shell metacharacters'): namespace['cli']('unsafe&argument')
            launch.assert_not_called()

    def test_registration_requires_absolute_output_and_rejects_unsafe_script_paths(self):
        from cron.jobs import create_job,get_job
        with self.assertRaisesRegex(ValueError,'OUTPUT_ROOT_MUST_BE_ABSOLUTE'):
            self.m.register_job(self.original['id'],output_root='relative-output')
        self.assertEqual(get_job(self.original['id']),self.original)
        outside=self.home/'outside.py'; outside.write_text('print("fixture")')
        script=create_job(None,'0 9 * * *',script=str(outside),no_agent=True,deliver='local')
        with self.assertRaisesRegex(ValueError,'ORIGINAL_SCRIPT_OUTSIDE_HOME_OR_MISSING'):
            self.m.register_job(script['id'])
        self.assertEqual(get_job(script['id']),script)
        with self.assertRaisesRegex(ValueError,'INVALID_JOB_ID'): self.m.register_job('../escape')
        self.assertEqual(self.m.status(),{})

    def test_custom_parent_only_plan_has_no_required_stage_or_artifact_gate(self):
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        job=self.original['id']; plan=[{'name':'Prepare','children':[]},{'name':'Optional work','children':[]}]
        self.m.register_job(job,plan=plan)
        self.assertEqual(plan[0]['children'],[])
        self.assertEqual(self.m.status()[job]['plan'],plan)
        result=self.m.supervise(job,fixture=str(SOURCE/'fixture.py'))
        self.assertEqual(result['outcome'],'success',result)
        with connect_closing(board='default') as conn: self.assertEqual(db.get_task(conn,result['task']).status,'done')

    def test_registration_refuses_native_fire_fence_and_rolls_back_failed_update(self):
        from unittest.mock import patch
        from contextlib import contextmanager
        from cron.jobs import get_job
        job=self.original['id']
        @contextmanager
        def busy(*args): yield False
        with patch('cron.jobs._fire_job_lock',busy):
            with self.assertRaisesRegex(RuntimeError,'JOB_BUSY'): self.m.register_job(job)
        with patch('cron.jobs.update_job',side_effect=OSError('Synthetic registration write failure')):
            with self.assertRaises(OSError): self.m.register_job(job)
        self.assertEqual(get_job(job),self.original)
        self.assertEqual(self.m.status(),{})
        self.assertFalse((self.home/'scripts'/f'kanban-managed-{job}.py').exists())
        self.assertTrue(self.m.register_job(job))

    def test_registration_rejects_changed_metadata_and_can_reregister_after_unregister(self):
        job=self.original['id']
        wait=self.m.register_job(job,output_root=self.home/'output')
        with self.assertRaisesRegex(RuntimeError,'REGISTRATION_METADATA_CHANGED'):
            self.m.register_job(job,output_root=self.home/'other')
        self.m.unregister_job(job)
        replacement=self.m.register_job(job,output_root=self.home/'other')
        self.assertNotEqual(wait,replacement)
        self.assertEqual(self.m.status()[job]['output_root'],str((self.home/'other').resolve()))

    def test_agent_retains_post_registration_prompt_model_and_tool_policy_edits(self):
        from cron.jobs import update_job
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        job=self.original['id']; wait=self.m.register_job(job)
        edits=dict(prompt='Revised local synthetic prompt',model='revised-model',enabled_toolsets=['file'])
        update_job(job,edits)
        journal=self.m.status()[job]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,wait); card=db.claim_task(conn,wait)
        run=self.m.private_state()/job/'runs'/'policy-edits'; run.mkdir(parents=True)
        host=self.m.U.identity(os.getpid())
        active=dict(job=job,name=journal['name'],plan=journal['plan'],board='default',task=wait,run=card.current_run_id,token='policy-edits',directory=str(run),controller=host,worker=host,started_epoch=0,started=self.m.U.now())
        journal['active']=active; self.m.U.save(run.parent.parent/'journal.json',journal); self.m.U.save(run/'gate.json',active)
        calls=[]
        def runner(original,**kwargs): calls.append(original); return True,'fixture','fixture',None
        self.assertEqual(self.m.worker(run,runner=runner)['outcome'],'success')
        for key,value in edits.items(): self.assertEqual(calls[0][key],value)
        self.assertEqual(calls[0]['script'],self.original['script'])
        self.assertEqual(calls[0]['no_agent'],self.original['no_agent'])

    def test_agent_uses_original_policies_without_extra_gates_or_required_artifacts(self):
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        job=self.original['id']; wait=self.m.register_job(job)
        journal=self.m.status()[job]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,wait); card=db.claim_task(conn,wait)
        run=self.m.private_state()/job/'runs'/'agent-boundary'; run.mkdir(parents=True)
        host=self.m.U.identity(os.getpid())
        active=dict(job=job,name=journal['name'],plan=journal['plan'],board='default',task=wait,run=card.current_run_id,token='agent-boundary',directory=str(run),controller=host,worker=host,started_epoch=0,started=self.m.U.now())
        journal['active']=active; self.m.U.save(run.parent.parent/'journal.json',journal)
        self.m.U.save(run/'gate.json',active)
        calls=[]
        def runner(original,**kwargs):
            calls.append((original,kwargs))
            return True,'Synthetic full native output','Synthetic Agent result without files',None
        report=self.m.worker(run,runner=runner)
        self.assertEqual(report['outcome'],'success')
        self.assertEqual(calls[0][0],self.original)
        self.assertIsNone(calls[0][1]['extra_prompt'])
        self.assertEqual(self.m.U.read(run/'progress.json')['artifacts'],[])
        self.assertEqual((run/'native-output.md').read_text(),'Synthetic full native output')

    def test_empty_agent_response_fails_real_supervision_but_silent_succeeds(self):
        from cron.jobs import get_job
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        from unittest.mock import patch
        job=self.original['id']; self.m.register_job(job)
        fixture=self.home/'agent-response.py'
        for response in ('', ' \n\t ', 'SILENT'):
            with self.subTest(response=response):
                fixture.write_text(
                    "import argparse, sys\nimport job_manager as m\n"
                    "p=argparse.ArgumentParser(); p.add_argument('--run-dir'); a=p.parse_args()\n"
                    f"r=m.worker(a.run_dir,runner=lambda *a,**kw:(True,'Synthetic output',{response!r},None))\n"
                    "sys.exit(0 if r['outcome']=='success' else 1)\n",encoding='utf-8')
                result=self.m.supervise(job,fixture=str(fixture))
                expected='success' if response=='SILENT' else 'failed'
                self.assertEqual(result['outcome'],expected,result)
                run=self.m.private_state()/job/'runs'/result['token']
                self.assertEqual(self.m.U.read(run/'result.json')['outcome'],expected)
                self.assertEqual(self.m.U.read(run/'summary.json')['outcome'],expected)
                with connect_closing(board='default') as conn:
                    card=db.get_task(conn,result['task'])
                    self.assertEqual(card.status,'done' if response=='SILENT' else 'blocked')
                    self.assertEqual(db.list_attachments(conn,card.id),[])
                    self.assertNotIn('[완료]',card.title) if expected=='failed' else None
                    terminal=conn.execute('SELECT outcome FROM task_runs WHERE id=?',(result['run'],)).fetchone()[0]
                    self.assertNotEqual(terminal,'completed') if expected=='failed' else None
                with patch.object(self.m,'supervise',return_value=result):
                    code,text=self.m.execute(self.m.parser().parse_args(['supervise','--job',job]))
                self.assertEqual(code,0 if response=='SILENT' else 1)
                self.assertEqual(text,'SILENT') if response=='SILENT' else self.assertIn('[CRON_FAILURE]',text)
                self.assertEqual(get_job(job)['next_run_at'],self.original['next_run_at'])

    def test_inner_native_retry_hints_are_diagnostics_not_outer_scheduler_policy(self):
        from cron.jobs import get_job
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        from unittest.mock import patch
        job=self.original['id']; self.m.register_job(job)
        outer=get_job(job); fixture=self.home/'retry-hints.py'
        for hints,raises in (({'_model_unreachable':True},False), ({'_quota_hold_seconds':120},False),
                             ({'_model_unreachable':True,'_quota_hold_seconds':90},True), ({},False)):
            with self.subTest(hints=hints,raises=raises):
                fixture.write_text(
                    "import argparse, sys\nimport job_manager as m\n"
                    "p=argparse.ArgumentParser(); p.add_argument('--run-dir'); a=p.parse_args()\n"
                    "def runner(original,**kw):\n"
                    f"    original.update({hints!r},_private_unrelated='must not retain')\n"
                    +("    raise RuntimeError('Synthetic quota model unreachable failure')\n" if raises else
                      "    return False,'Synthetic output','','Synthetic quota model unreachable failure'\n")+
                    "r=m.worker(a.run_dir,runner=runner)\nsys.exit(0 if r['outcome']=='success' else 1)\n",encoding='utf-8')
                result=self.m.supervise(job,fixture=str(fixture))
                self.assertEqual(result['outcome'],'failed',result)
                self.assertEqual(result.get('native_retry_metadata'),hints)
                run=self.m.private_state()/job/'runs'/result['token']
                for filename in ('result.json','summary.json'):
                    self.assertEqual(self.m.U.read(run/filename)['native_retry_metadata'],hints)
                self.assertEqual(get_job(job),outer,'diagnostics must not modify native scheduling state')
                warning='cannot be passed to the outer script scheduler; next normal schedule or manual retry applies'
                with connect_closing(board='default') as conn:
                    card=db.get_task(conn,result['task'])
                    self.assertEqual(card.status,'blocked')
                    if hints: self.assertIn(warning,card.body)
                    else: self.assertNotIn(warning,card.body)
                with patch.object(self.m,'supervise',return_value=result):
                    code,text=self.m.execute(self.m.parser().parse_args(['supervise','--job',job]))
                self.assertEqual(code,1)
                if hints: self.assertIn(warning,text)
                else: self.assertNotIn(warning,text)

    def test_unregister_restores_only_adapter_fields_retains_history_and_is_repeatable(self):
        from cron.jobs import get_job,update_job
        from hermes_cli.kanban_db_connect import connect_closing
        from hermes_cli import kanban_db as db
        job=self.original['id']; wait=self.m.register_job(job)
        current=get_job(job)
        update_job(job,{'name':'Locally renamed','prompt':'Locally revised request'})
        self.assertTrue(hasattr(self.m,'unregister_job'),'explicit unregister is missing')
        result=self.m.unregister_job(job)
        self.assertTrue(result['unregistered'])
        restored=get_job(job)
        self.assertEqual(restored['name'],'Locally renamed')
        self.assertEqual(restored['prompt'],'Locally revised request')
        for key in ('script','no_agent','interpreter'):
            self.assertEqual(restored.get(key),self.original.get(key))
        self.assertEqual(restored['schedule'],current['schedule'])
        self.assertEqual(restored['next_run_at'],current['next_run_at'])
        self.assertEqual(self.m.status(),{})
        self.assertFalse(self.m.dispatch_hook(board='default'))
        self.assertFalse(self.m.unregister_job(job)['unregistered'])
        with connect_closing(board='default') as conn:
            self.assertIsNotNone(db.get_task(conn,wait))
            self.assertEqual(db.get_task(conn,wait).status,'archived')

    def test_native_script_manual_fire_preserves_schedule_and_publishes_no_artifact_success(self):
        from cron.jobs import create_job, get_job, get_cron_output_dir
        from cron.executions import list_executions
        from tools.cronjob_tools import execute_job_for_event
        from hermes_cli import kanban_db as db
        from hermes_cli.kanban_db_connect import connect_closing
        scripts = self.home/'scripts'; scripts.mkdir()
        (scripts/'synthetic.py').write_text("print('Synthetic native result')\n", encoding='utf-8')
        original = create_job(None, '0 9 * * *', name='Synthetic Script', script='synthetic.py', no_agent=True, deliver='local')
        wait = self.m.register_job(original['id'])
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        # Native launcher prepares a fresh isolated home before the 30s worker handshake.
        import subprocess
        from hermes_cli._launchers import runtime_command
        prepared=subprocess.run(runtime_command(Path(self.m.status()[original['id']]['runtime']),['plugins','list','--plain','--no-bundled'],home=self.home),capture_output=True,text=True,timeout=180)
        self.assertEqual(prepared.returncode,0,prepared.stderr)
        result = execute_job_for_event(original['id'])
        self.assertTrue(result['success'],result)
        current=get_job(original['id'])
        self.assertEqual(current['next_run_at'],original['next_run_at'])
        self.assertEqual(current['schedule'],original['schedule'])
        journal=self.m.status()[original['id']]
        with connect_closing(board='default') as conn:
            card=db.get_task(conn,wait)
            self.assertEqual(card.status,'done')
            self.assertIn('Synthetic native result',card.body)
            self.assertEqual(db.list_attachments(conn,wait),[])
            self.assertEqual(db.get_task(conn,journal['waiting']).status,'scheduled')
            title,text=self.m.wait_text(journal)
            self.assertRegex(title,r'· \d\d/\d\d\([월화수목금토일]\) \d\d:\d\d 시작 예정$')
            self.assertEqual((db.get_task(conn,journal['waiting']).title,db.get_task(conn,journal['waiting']).body),(title,text))
            self.assertIn('**예정 시작: ',text)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0],1)
        self.assertNotEqual(wait,journal['waiting'])
        self.assertTrue(list_executions(job_id=original['id']))
        files=list((get_cron_output_dir()/original['id']).glob('*'))
        self.assertTrue(any('Synthetic native result' in p.read_text(encoding='utf-8') for p in files if p.is_file()))
        self.assertEqual(list_executions(job_id=original['id'])[0]['status'],'completed')
        self.assertIsNone(get_job(original['id']).get('fire_claim'))
        # Same real native entry point, nonzero script exit, no Agent/account/network.
        (scripts/'synthetic-failure.py').write_text("import sys; print('Synthetic script failure'); sys.exit(7)\n",encoding='utf-8')
        failed=create_job(None,'0 10 * * *',name='Synthetic failure',script='synthetic-failure.py',no_agent=True,deliver='local')
        failed_wait=self.m.register_job(failed['id'])
        with connect_closing(board='default') as conn: db.unblock_task(conn,failed_wait)
        result=execute_job_for_event(failed['id'])
        self.assertFalse(result['success'],result)
        self.assertEqual(get_job(failed['id'])['next_run_at'],failed['next_run_at'])
        self.assertEqual(list_executions(job_id=failed['id'])[0]['status'],'failed')
        self.assertIsNone(get_job(failed['id']).get('fire_claim'))
        with connect_closing(board='default') as conn:
            self.assertEqual(db.get_task(conn,failed_wait).status,'blocked')
            self.assertEqual(db.get_task(conn,self.m.status()[failed['id']]['waiting']).status,'scheduled')
            self.assertEqual(db.list_attachments(conn,failed_wait),[])
