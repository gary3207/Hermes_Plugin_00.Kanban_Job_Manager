"""Reused controller regressions on disposable native DBs and real owned Windows trees."""
import json
import os
from pathlib import Path
import sys
import time
import tempfile
import unittest
from hermes_cli import kanban_db as db
from hermes_cli.kanban_db_connect import connect_closing

ROOT=Path(__file__).resolve().parent

class ControllerTests(unittest.TestCase):
    def setUp(self):
        work=Path(os.environ.get('KANBAN_TEST_WORK',ROOT/'test-work'))
        self.home=Path(tempfile.mkdtemp(dir=work,prefix='ctrl-'))
        os.environ['HERMES_HOME']=os.environ['HERMES_KANBAN_HOME']=str(self.home)
        import job_manager
        self.m=job_manager
        from cron.jobs import create_job
        original=create_job('Synthetic original prompt','0 9 * * *',name='Synthetic Agent',deliver='local')
        self.jobs={original['id']:original}
        self.state=self.m.private_state()
        self.manifest={original['id']:dict(original=original,output_root=str(self.home/'output'))}

    def initialize(self):
        for key in self.jobs: self.m.register_job(key,output_root=self.home/'output')

    def tearDown(self):
        os.environ.pop('SCHEDULED_JOBS_RUN',None)
        from hermes_cli.observability.relay_shared_metrics import shutdown_runtimes
        shutdown_runtimes()

    def test_explicit_phases_fenced_current_artifacts_and_concrete_redacted_failure(self):
        self.initialize()
        self.assertTrue(hasattr(self.m,'report_progress'),'explicit progress missing')
        key=next(iter(self.jobs))
        journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,journal['waiting']); card=db.claim_task(conn,journal['waiting'])
        run=self.state/key/'runs'/'test-token'; run.mkdir(parents=True)
        active=dict(job=key,board='default',task=card.id,run=card.current_run_id,token='test-token',directory=str(run),started_epoch=__import__('time').time(),controller=self.m.U.identity(os.getpid()),worker=self.m.U.identity(os.getpid()))
        journal['active']=active; self.m.U.save(self.state/key/'journal.json',journal)
        self.m.U.save(run/'gate.json',active)
        self.m.report_progress(run,'test-token',1)
        self.m.report_progress(run,'test-token',1)
        self.m.report_progress(run,'test-token',2)
        events=self.m.U.read(run/'progress.json')
        self.assertEqual(len(events['phases']),2)
        self.assertGreaterEqual(events['phases'][0]['seconds'],0)
        with self.assertRaises(RuntimeError): self.m.report_progress(run,'wrong-token',3)
        root=self.home/'output'; root.mkdir(exist_ok=True)
        artifact=root/'report.html'; artifact.write_text('<!doctype html><html><body>Test</body></html>',encoding='utf-8')
        verified=self.m.verify_artifacts([str(artifact)],root,active['started_epoch'])
        self.assertEqual(len(verified),1)
        self.assertEqual(len(verified[0]['sha256']),64)
        stale=root/'stale.html'; stale.write_text('<html>stale</html>')
        os.utime(stale,(1,1))
        with self.assertRaises(ValueError): self.m.verify_artifacts([str(stale)],root,active['started_epoch'])
        with self.assertRaises(ValueError): self.m.verify_artifacts([str(ROOT/'test_scheduled_jobs.py')],root,active['started_epoch'])
        message=self.m.safe_error('Synthetic execution failure; Authorization: Bearer ABCDEF...3456')
        self.assertIn('Synthetic execution failure',message)
        self.assertNotIn('ABCDEFGHIJKLMNOPQRSTUVWXYZ123456',message)
        with connect_closing(board='default') as conn:
            db.block_task(conn,active['task'],reason='Operator cancellation',kind='transient',expected_run_id=active['run'])
        with self.assertRaises(RuntimeError): self.m.report_progress(run,'test-token',3)
        self.assertEqual(self.m.U.read(run/'progress.json')['phase'],2)

    def test_isolated_success_creates_native_history_attachments_and_next_wait(self):
        self.initialize()
        self.assertTrue(hasattr(self.m,'supervise'),'shared supervisor missing')
        key=next(iter(self.jobs))
        result=self.m.supervise(key,state=self.state,origin='automatic',fixture=str(ROOT/'fixture.py'))
        self.assertEqual(result['outcome'],'success')
        journal=self.m.status(self.state)[key]
        self.assertIsNone(journal['active'])
        with connect_closing(board='default') as conn:
            history=db.get_task(conn,result['task'])
            self.assertEqual(history.status,'done')
            self.assertIn('[완료]',history.title)
            self.assertIn('[폴더 열기](<',history.body)
            self.assertEqual(len(db.list_attachments(conn,history.id)),1)
            self.assertEqual(db.get_task(conn,journal['waiting']).status,'scheduled')
            self.assertNotEqual(journal['waiting'],history.id)
            self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],1)
        self.assertFalse(self.m.U.alive(result['worker']))
        summary=self.m.U.read(self.state/key/'runs'/result['token']/'summary.json')
        self.assertNotIn('final_response',summary,'diagnostic summary must not duplicate business delivery content')
        self.assertTrue(self.m.U.alive(self.m.U.identity(os.getpid())))

    def test_dispatch_intent_is_single_and_does_not_claim_before_native_manual(self):
        self.initialize()
        self.assertTrue(hasattr(self.m,'dispatch_hook'),'native dispatch integration missing')
        calls=[]
        def launch(journal,job,token):
            calls.append((job,token)); return self.m.U.identity(os.getpid())
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,wait)
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,dry_run=True,launcher=launch))
        self.assertTrue(self.m.dispatch_hook(board='default',state=self.state,launcher=launch))
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=launch))
        self.assertEqual(len(calls),1)
        with connect_closing(board='default') as conn:
            self.assertEqual(db.get_task(conn,wait).status,'ready')
            self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],0)
        self.assertIsNone(self.m.status(self.state)[key]['active'])
        self.assertEqual(self.m.parser().parse_args(['status']).action,'status')

    def test_recovery_claim_gap_never_starts_agent_and_replaces_only_after_terminal(self):
        self.initialize()
        key=next(iter(self.jobs)); journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,journal['waiting']); claimed=db.claim_task(conn,journal['waiting'])
        result=self.m.supervise(key,state=self.state,origin='recovery',fixture=str(ROOT/'fixture.py'))
        self.assertEqual(result['outcome'],'cancelled')
        with connect_closing(board='default') as conn:
            self.assertEqual(db.get_task(conn,claimed.id).status,'blocked')
            self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],1)
            self.assertEqual(db.get_task(conn,self.m.status(self.state)[key]['waiting']).status,'scheduled')
        self.assertIsNone(self.m.status(self.state)[key]['active'])

    def test_stop_refuses_host_identity_and_attachment_tamper(self):
        self.assertTrue(hasattr(self.m,'stop_owned'),'host protection missing')
        host=self.m.U.identity(os.getpid())
        with self.assertRaises(RuntimeError): self.m.stop_owned(dict(controller=host,worker=host,launcher=host,children=[]))
        self.assertTrue(self.m.U.alive(host))
        output=self.home/'output'; output.mkdir()
        artifact=output/'a.html'; artifact.write_text('<html>one</html>')
        verified=self.m.verify_artifacts([str(artifact)],output,0)[0]
        artifact.write_text('<html>changed</html>')
        with self.assertRaises(ValueError): self.m.artifact_bytes(verified)

    def test_operator_block_and_delete_cancel_real_contained_children(self):
        import threading,time
        self.initialize()
        key=next(iter(self.jobs))
        for operation in ('block','delete'):
            with self.subTest(operation=operation):
                os.environ['SCHEDULED_JOBS_FIXTURE_MODE']='hold'
                results=[]; errors=[]
                def supervise():
                    try: results.append(self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'),timeout=20))
                    except BaseException as exc: errors.append(exc)
                thread=threading.Thread(target=supervise); thread.start()
                deadline=time.monotonic()+10; child=None; active=None
                while time.monotonic()<deadline:
                    active=self.m.status(self.state)[key].get('active')
                    if active:
                        child=self.m.U.read(Path(active['directory'])/'fixture-child.json')
                        if child: break
                    time.sleep(.025)
                try:
                    self.assertIsNotNone(child,'fixture failed startup')
                    self.assertNotEqual(active['worker']['pid'],os.getpid())
                    with self.assertRaisesRegex(RuntimeError,'JOB_BUSY'):
                        self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
                    with self.assertRaisesRegex(RuntimeError,'JOB_BUSY'):
                        self.initialize()
                    with connect_closing(board='default') as conn:
                        if operation=='block': db.block_task(conn,active['task'],reason='Operator stop',kind='transient',expected_run_id=active['run'])
                        else: db.delete_task(conn,active['task'])
                    thread.join(10)
                    self.assertFalse(thread.is_alive()); self.assertFalse(errors,str(errors))
                    self.assertEqual(results[0]['outcome'],'cancelled')
                    self.assertFalse(self.m.U.alive(child)); self.assertFalse(self.m.U.alive(active['worker']))
                    self.assertIsNone(self.m.status(self.state)[key]['active'])
                    with connect_closing(board='default') as conn:
                        self.assertEqual(db.get_task(conn,self.m.status(self.state)[key]['waiting']).status,'scheduled')
                finally:
                    if thread.is_alive() and active:
                        with connect_closing(board='default') as conn:
                            db.block_task(conn,active['task'],reason='Test cleanup',kind='transient',expected_run_id=active['run'])
                        thread.join(10)
                    os.environ.pop('SCHEDULED_JOBS_FIXTURE_MODE',None)

    def test_every_explicit_phase_is_durably_queued_without_periodic_updates(self):
        self.initialize()
        self.assertTrue(hasattr(self.m,'consume_progress'),'phase event queue missing')
        key=next(iter(self.jobs))
        result=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
        journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            edits=[e for e in db.list_events(conn,result['task']) if e.kind=='edited']
            self.assertGreaterEqual(len(edits),4)
            terminal_body=db.get_task(conn,result['task']).body
            self.m.reject_history(conn,journal)
            self.assertEqual(db.get_task(conn,result['task']).body,terminal_body)

    def test_recovery_preserves_already_native_completed_run(self):
        self.initialize()
        key=next(iter(self.jobs))
        result=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
        journal=self.m.status(self.state)[key]
        # Simulate a crash after native Done + durable summary but before journal clear.
        with connect_closing(board='default') as conn:
            before=db.get_task(conn,result['task'])
            before_summary=conn.execute('SELECT summary FROM task_runs WHERE id=?',(result['run'],)).fetchone()[0]
        old_wait=journal['waiting']
        with connect_closing(board='default') as conn: db.delete_task(conn,old_wait)
        journal['waiting']=result['task']
        directory=self.state/key/'runs'/result['token']
        active=self.m.U.read(directory/'gate.json'); active['controller']={'pid':99999999,'birth':0}
        journal['active']=active; journal['cards'][result['task']]['role']='active'
        self.m.U.save(self.state/key/'journal.json',journal)
        recovered=self.m.supervise(key,state=self.state,origin='recovery',fixture=str(ROOT/'fixture.py'))
        self.assertEqual(recovered['outcome'],'success')
        with connect_closing(board='default') as conn:
            self.assertEqual(db.get_task(conn,result['task']),before)
            self.assertEqual(conn.execute('SELECT summary FROM task_runs WHERE id=?',(result['run'],)).fetchone()[0],before_summary)
            self.assertNotIn('| 단계 |',before_summary)
            self.assertEqual(db.get_task(conn,result['task']).status,'done')
            self.assertIn('[완료]',db.get_task(conn,result['task']).title)
            self.assertEqual(len(db.list_attachments(conn,result['task'])),1)
            self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],1)

    def test_real_controller_crash_closes_windows_job_and_recovery_never_replays(self):
        import subprocess,time,psutil
        self.initialize()
        key=next(iter(self.jobs)); journal=self.m.status(self.state)[key]
        command=self.m.process_command(journal,'supervise',['supervise','--job',key,'--state',str(self.state),'--fixture',str(ROOT/'fixture.py'),'--timeout','30'],fixture=str(ROOT/'plugin'/'job_manager.py'))
        env=dict(os.environ,SCHEDULED_JOBS_FIXTURE_MODE='hold')
        proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
        active=None; child=None
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                active=self.m.status(self.state)[key].get('active')
                if active:
                    child=self.m.U.read(Path(active['directory'])/'fixture-child.json')
                    if child: break
                time.sleep(.025)
            self.assertIsNotNone(child,'controller fixture did not start')
            self.assertNotEqual(active['controller']['pid'],os.getpid())
            self.assertIn(active['controller'],self.m.U.tree_identities(self.m.U.identity(proc.pid)))
            psutil.Process(active['controller']['pid']).kill(); proc.wait(timeout=10)
            deadline=time.monotonic()+5
            while self.m.U.alive(child) and time.monotonic()<deadline: time.sleep(.025)
            self.assertFalse(self.m.U.alive(child)); self.assertFalse(self.m.U.alive(active['worker']))
            result=self.m.supervise(key,state=self.state,origin='recovery',fixture=str(ROOT/'fixture.py'))
            self.assertEqual(result['outcome'],'cancelled')
            with connect_closing(board='default') as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],1)
                self.assertEqual(db.get_task(conn,result['task']).status,'blocked')
        finally:
            if proc.poll() is None:
                if active and self.m.U.alive(active['controller']): psutil.Process(active['controller']['pid']).kill()
                proc.kill(); proc.wait(timeout=10)

    def test_declared_success_does_not_show_completed_before_verification(self):
        self.initialize()
        key=next(iter(self.jobs)); journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,journal['waiting']); card=db.claim_task(conn,journal['waiting'])
            run=self.state/key/'runs'/'provisional'; run.mkdir(parents=True)
            active=dict(job=key,board='default',task=card.id,run=card.current_run_id,token='provisional',directory=str(run),controller=self.m.U.identity(os.getpid()),started=self.m.U.now(),name='Synthetic Agent')
            journal['active']=active; self.m.U.save(self.state/key/'journal.json',journal); self.m.U.save(run/'gate.json',active)
            self.m.report_progress(run,'provisional',3,outcome='success',artifacts=['C:/not-verified.xlsx'])
            self.m.consume_progress(conn,journal,active,0)
            current=db.get_task(conn,card.id)
            self.assertEqual(current.status,'running')
            self.assertIn('[실행 중]',current.title)
            self.assertNotIn('[완료]',current.title)

    def test_todo_readiness_history_rejection_and_timeout_are_native(self):
        self.initialize()
        key=next(iter(self.jobs)); journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            with db.write_txn(conn):
                conn.execute("UPDATE tasks SET status='todo' WHERE id=?",(journal['waiting'],))
                db._append_event(conn,journal['waiting'],'status',{'status':'todo','actor':'offline-operator'})
        result=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
        self.assertEqual(result['outcome'],'success')
        journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            before=db.get_task(conn,result['task']).body
            with db.write_txn(conn): conn.execute("UPDATE tasks SET status='ready' WHERE id=?",(result['task'],))
            self.m.reject_history(conn,journal)
            self.assertEqual(db.get_task(conn,result['task']).status,'done')
            self.assertEqual(db.get_task(conn,result['task']).body,before)
            self.assertEqual(conn.execute('SELECT count(*) FROM task_runs').fetchone()[0],1)
        os.environ['SCHEDULED_JOBS_FIXTURE_MODE']='hold'
        try: timed=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'),timeout=.2)
        finally: os.environ.pop('SCHEDULED_JOBS_FIXTURE_MODE',None)
        self.assertEqual(timed['outcome'],'failed')
        self.assertIn('SUPERVISOR_TIMEOUT',timed['error'])
        self.assertFalse(self.m.U.alive(timed['worker']))

    def test_worker_startup_refuses_cancelled_claim_before_agent(self):
        self.initialize()
        key=next(iter(self.jobs)); journal=self.m.status(self.state)[key]
        with connect_closing(board='default') as conn:
            db.unblock_task(conn,journal['waiting']); card=db.claim_task(conn,journal['waiting'])
            db.block_task(conn,card.id,reason='Operator cancelled startup',kind='transient',expected_run_id=card.current_run_id)
        run=self.state/key/'runs'/'cancel-before-gate'; run.mkdir(parents=True)
        active=dict(job=key,board='default',task=card.id,run=card.current_run_id,token='cancel-before-gate',directory=str(run),controller=self.m.U.identity(os.getpid()),worker=self.m.U.identity(os.getpid()))
        journal['active']=active; self.m.U.save(self.state/key/'journal.json',journal); self.m.U.save(run/'gate.json',active)
        calls=[]
        with self.assertRaisesRegex(RuntimeError,'STARTUP_CLAIM_LOST'):
            self.m.worker(run,runner=lambda *a,**kw:calls.append(a))
        self.assertEqual(calls,[])
        self.assertFalse((run/'progress.json').exists())

    def test_native_manual_refusal_becomes_failed_history_not_retry_storm(self):
        self.initialize()
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:self.m.U.identity(os.getpid()))
        token=self.m.status(self.state)[key]['request']['token']
        result=self.m.manual(key,token,state=self.state,executor=lambda job:dict(claimed=False,success=False,error='Job is paused/disabled; resume it before running.'))
        self.assertFalse(result['success'])
        with connect_closing(board='default') as conn:
            history=db.get_task(conn,wait)
            self.assertEqual('blocked',history.status)
            self.assertIn('[실패]',history.title)
            self.assertIn('paused/disabled',history.body)
            self.assertEqual('scheduled',db.get_task(conn,self.m.status(self.state)[key]['waiting']).status)
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:self.fail('refusal relaunched')))

    def test_cancellation_during_artifact_read_publishes_no_attachment(self):
        from unittest.mock import patch
        self.initialize()
        key=next(iter(self.jobs)); original=self.m.artifact_bytes; cancelled=[]
        def read_then_cancel(artifact):
            data=original(artifact)
            if not cancelled:
                active=self.m.status(self.state)[key]['active']
                with connect_closing(board='default') as conn:
                    db.block_task(conn,active['task'],reason='Operator cancelled during artifact read',kind='transient',expected_run_id=active['run'])
                cancelled.append(active['task'])
            return data
        with patch.object(self.m,'artifact_bytes',side_effect=read_then_cancel):
            result=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
        self.assertEqual('cancelled',result['outcome'])
        with connect_closing(board='default') as conn:
            self.assertEqual([],db.list_attachments(conn,cancelled[0]))

    def test_running_preparation_is_visible_before_worker_gate(self):
        from unittest.mock import patch
        from hermes_cli.local_runtime import processes
        self.initialize()
        key=next(iter(self.jobs)); original=processes.spawn_server; snapshots=[]
        def capture_before_spawn(*args,**kwargs):
            active=self.m.status(self.state)[key]['active']
            with connect_closing(board='default') as conn:
                card=db.get_task(conn,active['task']); snapshots.append((card.title,card.body))
            return original(*args,**kwargs)
        with patch.object(processes,'spawn_server',side_effect=capture_before_spawn):
            result=self.m.supervise(key,state=self.state,fixture=str(ROOT/'fixture.py'))
        self.assertEqual('success',result['outcome'])
        self.assertIn('[실행 중]',snapshots[0][0])
        self.assertIn('1/3',snapshots[0][1])

    def test_native_inflight_contention_coalesces_same_intent_with_finite_retries(self):
        from unittest.mock import patch
        self.initialize()
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']; launches=[]; executions=[]
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        def launch(journal,job,token):
            launches.append(token)
            return None  # Completed/offline launcher, no invented PID.
        def refuse(job):
            with self.m.U.lock(self.state/key/'job.lock',blocking=False): executions.append(job)
            return dict(claimed=False,success=False,error='Job is already being fired by the scheduler; not run again.')
        self.m.dispatch_hook(board='default',state=self.state,launcher=launch)
        token=self.m.status(self.state)[key]['request']['token']
        with patch('cron.executions.live_inflight_execution',return_value={'status':'running'}):
            for attempt in range(3):
                self.m.manual(key,token,state=self.state,executor=refuse)
                journal=self.m.status(self.state)[key]
                if attempt<2:
                    self.assertEqual(token,journal.get('request',{}).get('token'))
                    self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=launch))
                    with patch.object(self.m.time,'time',return_value=journal['request']['retry_after']+1):
                        self.assertTrue(self.m.dispatch_hook(board='default',state=self.state,launcher=launch))
        self.assertEqual([token]*3,launches)
        self.assertEqual([key]*3,executions)
        journal=self.m.status(self.state)[key]
        self.assertNotIn('request',journal)
        with connect_closing(board='default') as conn:
            self.assertEqual('blocked',db.get_task(conn,wait).status)
            self.assertIn('retry limit',db.get_task(conn,wait).body)
            self.assertEqual('scheduled',db.get_task(conn,journal['waiting']).status)
            self.assertEqual(1,conn.execute('SELECT count(*) FROM task_runs').fetchone()[0])
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=launch))

    def test_native_executor_exception_persists_sanitized_preparation_failure(self):
        from unittest.mock import patch
        self.initialize()
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:self.m.U.identity(os.getpid()))
        token=self.m.status(self.state)[key]['request']['token']
        def explode(job):
            raise OSError('Native executor unavailable; Authorization: Bearer privat...tial')
        with patch('cron.executions.live_inflight_execution',return_value={'status':'running'}):
            result=self.m.manual(key,token,state=self.state,executor=explode)
        self.assertFalse(result['success'])
        self.assertIn('OSError',result['error'])
        run=self.state/key/'runs'/token
        gate=self.m.U.read(run/'gate.json'); progress=self.m.U.read(run/'progress.json'); summary=self.m.U.read(run/'summary.json')
        self.assertEqual(wait,gate['task'])
        self.assertIsNone(gate['worker'])
        self.assertEqual(self.m.U.identity(os.getpid()),gate['controller'])
        self.assertEqual(1,progress['phase'])
        self.assertEqual('failed',summary['outcome'])
        self.assertEqual(gate['run'],summary['run'])
        with connect_closing(board='default') as conn:
            history=db.get_task(conn,wait)
            self.assertEqual('blocked',history.status)
            self.assertIn('Native executor unavailable',history.body)
            self.assertEqual('scheduled',db.get_task(conn,self.m.status(self.state)[key]['waiting']).status)
        for file in run.iterdir():
            if file.suffix in ('.json','.jsonl'): self.assertNotIn('private-refusal-credential',file.read_text(encoding='utf-8'))
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:self.fail('exception relaunched')))

    def test_manual_todo_dependencies_gate_executor_and_dispatch(self):
        self.initialize()
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']; calls=[]
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:None)
        token=self.m.status(self.state)[key]['request']['token']
        with connect_closing(board='default') as conn:
            parent=db.create_task(conn,title='Unfinished dependency')
            db.link_tasks(conn,parent,wait)
            self.assertEqual('todo',db.get_task(conn,wait).status)
        self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:calls.append('launch')))
        result=self.m.manual(key,token,state=self.state,executor=lambda job:calls.append('execute'))
        self.assertFalse(result['success'])
        self.assertEqual([],calls)
        with connect_closing(board='default') as conn:
            self.assertEqual('todo',db.get_task(conn,wait).status)
            self.assertEqual(0,conn.execute('SELECT count(*) FROM task_runs').fetchone()[0])
            db.recompute_ready(conn); claimed=db.claim_task(conn,parent)
            self.assertIsNotNone(claimed)
            db.complete_task(conn,parent,result='Dependency finished',expected_run_id=claimed.current_run_id)
        self.assertTrue(self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:calls.append('launch')))
        self.assertEqual(['launch'],calls)
        self.assertEqual(token,self.m.status(self.state)[key]['request']['token'])

    def test_manual_refusal_defers_to_concurrent_job_lock_before_active_publish(self):
        import threading
        self.initialize()
        key=next(iter(self.jobs)); wait=self.m.status(self.state)[key]['waiting']
        with connect_closing(board='default') as conn: db.unblock_task(conn,wait)
        self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:None)
        token=self.m.status(self.state)[key]['request']['token']; locked=threading.Event(); release=threading.Event()
        def automatic_lock():
            with self.m.U.lock(self.state/key/'job.lock',blocking=False):
                locked.set(); release.wait(10)
        thread=threading.Thread(target=automatic_lock)
        def refuse(job):
            thread.start()
            self.assertTrue(locked.wait(5))
            return dict(claimed=False,success=False,error='Native job already running')
        try:
            result=self.m.manual(key,token,state=self.state,executor=refuse)
            self.assertFalse(result['success'])
            journal=self.m.status(self.state)[key]
            self.assertIsNone(journal['active'])
            self.assertEqual(token,journal['request']['token'])
            self.assertEqual(wait,journal['waiting'])
            with connect_closing(board='default') as conn:
                self.assertEqual('ready',db.get_task(conn,wait).status)
                self.assertEqual(0,conn.execute('SELECT count(*) FROM task_runs').fetchone()[0])
            self.assertFalse(self.m.dispatch_hook(board='default',state=self.state,launcher=lambda *args:self.fail('job lock bypassed')))
        finally:
            release.set()
            if thread.ident is not None: thread.join(5)
        self.assertFalse(thread.is_alive())
