"""Explicit native cron registrations with the existing contained Kanban controller."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller_utils as U

CREATOR = 'hermes-kanban-job-manager'
TENANT = 'kanban-managed-jobs'
ASSIGNEE = 'kanban-managed-job'
DEFAULT_PLAN = [{'name':'Preparation','children':['Worker ownership']}, {'name':'Original job','children':['Native execution']}, {'name':'Publication','children':['Native result']}]


def valid_job_id(job):
    if not isinstance(job,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',job):
        raise ValueError('INVALID_JOB_ID')
    return job


def status(state=None):
    root=state_path(state)
    if not root.exists(): return {}
    result={}
    for directory in root.iterdir():
        if directory.is_dir() and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',directory.name):
            journal=U.read(directory/'journal.json')
            if journal and journal.get('enabled'): result[directory.name]=journal
    return result


def stages(scope): return [p['name'] for p in scope.get('plan',DEFAULT_PLAN)]
def substages(scope): return [p.get('children',[]) for p in scope.get('plan',DEFAULT_PLAN)]


def safe_path(path,root):
    path=Path(path).absolute()
    if not path.resolve().is_relative_to(Path(root).resolve()): raise ValueError('PATH_OUTSIDE_SCOPE')
    for cursor in (path,*path.parents):
        if cursor.is_symlink() or cursor.is_junction(): raise ValueError('PATH_REPARSE_POINT')
    return path


def wrapper_code(job):
    import hermes_cli
    runtime=str(Path(hermes_cli.__file__).resolve().parent.parent)
    module=str(Path(__file__).resolve())
    return ('# Locally generated native Kanban adapter; no job prompt embedded.\n'
        'import sys, runpy\n'
        f'sys.path.insert(0,{runtime!r})\n'
        f'sys.argv=[{module!r},"supervise","--job",{job!r}]\n'
        f'runpy.run_path({module!r},run_name="__main__")\n')


def register_job(job,board='default',output_root=None,plan=None):
    valid_job_id(job)
    if output_root is not None and not Path(output_root).is_absolute(): raise ValueError('OUTPUT_ROOT_MUST_BE_ABSOLUTE')
    if os.name!='nt': raise RuntimeError('WINDOWS_CONTAINMENT_REQUIRED')
    from hermes_constants import get_hermes_home
    from cron.jobs import get_job,update_job,_jobs_lock,_fire_job_lock
    from cron.executions import live_inflight_execution
    from hermes_cli.profiles import profile_exists
    from hermes_cli.kanban_db_connect import connect_closing
    import hermes_cli
    if profile_exists(ASSIGNEE): raise RuntimeError('RESERVED_ASSIGNEE_PROFILE_EXISTS')
    directory=safe_path(private_state()/job,get_hermes_home())
    with U.lock(directory/'job.lock',blocking=False), U.lock(directory/'state.lock'), _fire_job_lock(job) as fenced, _jobs_lock():
        if not fenced: raise RuntimeError('JOB_BUSY')
        original=get_job(job)
        if not original: raise ValueError('JOB_NOT_FOUND')
        if original.get('fire_claim') or original.get('run_claim') or live_inflight_execution(job): raise RuntimeError('JOB_ACTIVE')
        journal=U.read(directory/'journal.json')
        wrapper=f'kanban-managed-{job}.py'
        script=safe_path(get_hermes_home()/'scripts'/wrapper,get_hermes_home()/'scripts')
        code=wrapper_code(job)
        if journal and journal.get('enabled'):
            if output_root is not None and str(Path(output_root).resolve())!=journal['output_root'] or plan is not None and plan!=journal['plan']: raise RuntimeError('REGISTRATION_METADATA_CHANGED')
            if journal['board']!=board or journal.get('active') or journal.get('request'): raise RuntimeError('REGISTRATION_SCOPE_OR_ACTIVITY_CHANGED')
            if original.get('script')!=wrapper or not original.get('no_agent') or original.get('interpreter') is not None:
                raise RuntimeError('ADAPTER_FIELDS_CHANGED')
            if not script.is_file() or script.read_text(encoding='utf-8')!=code: raise RuntimeError('WRAPPER_CHANGED')
            with connect_closing(board=board) as conn: return ensure_wait(conn,directory,journal)
        previous=journal
        if original.get('monitor_script') or original.get('monitor_url'): raise ValueError('MONITOR_JOB_REQUIRES_NATIVE_CONNECTOR')
        if original.get('script'):
            from cron.scheduler_script import _resolve_script_path
            path,error=_resolve_script_path(original['script'])
            if error: raise ValueError('ORIGINAL_SCRIPT_OUTSIDE_HOME_OR_MISSING')
            safe_path(path,get_hermes_home()/'scripts')
        if script.exists(): raise RuntimeError('WRAPPER_ALREADY_EXISTS')
        output=Path(output_root or original.get('workdir') or get_hermes_home()/'cron'/'output'/job).resolve()
        plan=plan or DEFAULT_PLAN
        if not isinstance(plan,list) or not 1<=len(plan)<=32 or any(not isinstance(p,dict) or set(p)-{'name','children'} or not isinstance(p.get('name'),str) or not p['name'].strip() or not isinstance(p.get('children',[]),list) or any(not isinstance(c,str) or not c.strip() for c in p.get('children',[])) for p in plan): raise ValueError('INVALID_STAGE_PLAN')
        journal=dict(job=job,name=original.get('name') or job,board=board,original=original,output_root=str(output),plan=plan,explicit_plan=plan!=DEFAULT_PLAN,interpreter=None,runtime=str(Path(hermes_cli.__file__).resolve().parent.parent),cards={},active=None,enabled=True)
        if previous: journal['cards']=previous['cards']
        U.save(directory/'journal.json',journal)
        script.parent.mkdir(parents=True,exist_ok=True)
        with script.open('x',encoding='utf-8') as stream: stream.write(code)
        try:
            updated=update_job(job,dict(script=wrapper,no_agent=True,interpreter=None))
            expected=dict(original,script=wrapper,no_agent=True,interpreter=None)
            if updated!=expected or get_job(job)!=expected: raise RuntimeError('REGISTRATION_READBACK_MISMATCH')
            with connect_closing(board=board) as conn: return ensure_wait(conn,directory,journal)
        except Exception:
            if get_job(job)!=original:
                restored={k:original.get(k) for k in ('script','no_agent','interpreter')}
                if not update_job(job,restored): raise RuntimeError('REGISTRATION_ROLLBACK_FAILED')
            script.unlink()
            journal['enabled']=False
            if previous: U.save(directory/'journal.json',previous)
            else: U.save(directory/'journal.json',journal)
            raise

def private_state():
    from hermes_constants import get_hermes_home
    return get_hermes_home()/'plugin-data'/CREATOR


def state_path(state=None): return Path(state) if state is not None else private_state()


def owned(card,journal):
    info=journal['cards'].get(card.id) if card else None
    return bool(info and card.created_by==CREATOR and card.tenant==TENANT and card.assignee==ASSIGNEE and card.idempotency_key==info['key'])


def wait_text(journal):
    from datetime import datetime
    from cron.jobs import get_job
    raw=(get_job(journal['job']) or {}).get('next_run_at')
    if raw:
        at=datetime.fromisoformat(raw).astimezone()
        day='월화수목금토일'[at.weekday()]
        short,full=at.strftime(f'%m/%d({day}) %H:%M'),at.strftime(f'%Y-%m-%d ({day}) %H:%M')
    else: short=full='예정 없음'
    return (f"[실행 대기] {journal['name']} · {short} 시작 예정",
        f'**예정 시작: {full}**\n\n시간 자동 예약 유지 · 수동 Ready/Todo 시작. 완료한 외부 쓰기는 취소 시 보존, 진행 중 쓰기 결과는 확인 필요.')


def ensure_wait(conn,state,journal):
    from hermes_cli import kanban_db as db
    card=db.get_task(conn,journal.get('waiting',''))
    title,text=wait_text(journal)
    if owned(card,journal) and journal['cards'][card.id]['role']=='wait':
        # shortcut: refreshed only when ensure_wait runs (register/run/recovery), not on a later cron schedule edit.
        if card.status=='scheduled' and (card.title,card.body)!=(title,text):
            with db.write_txn(conn):
                conn.execute('UPDATE tasks SET title=?,body=? WHERE id=?',(title,text,card.id))
                db._append_event(conn,card.id,'edited',{'fields':['title','body'],'actor':CREATOR})
        return card.id
    key=journal.setdefault('pending_wait_key',journal['job']+':wait:'+uuid.uuid4().hex)
    U.save(state/'journal.json',journal)
    card_id=db.create_task(conn,title=title,body=text,
        initial_status='blocked',assignee=ASSIGNEE,created_by=CREATOR,tenant=TENANT,
        idempotency_key=key,board=journal['board'],workspace_kind='scratch')
    card=db.get_task(conn,card_id)
    if card.status=='blocked' and not db.schedule_task(conn,card_id): raise RuntimeError('WAIT_SCHEDULE_FAILED')
    journal['cards'][card_id]={'key':key,'role':'wait'}
    journal['waiting']=card_id
    journal.pop('pending_wait_key',None)
    U.save(state/'journal.json',journal)
    return card_id


def safe_error(error):
    from agent.redact import redact_sensitive_text
    import re
    text=redact_sensitive_text(str(error),force=True)
    text=re.sub(r'(?i)(authorization\s*:\s*bearer\s+)\S+',r'\1[REDACTED]',text)
    # Diagnostic input is an error only, never a business response/prompt/tool payload.
    return text.replace('\x00','')[:1500]


def report_progress(run_dir,token,phase,*,subphase=None,outcome=None,error=None,artifacts=None):
    run_dir=Path(run_dir)
    gate=U.read(run_dir/'gate.json')
    if not gate or gate['token']!=token or not U.alive(gate['controller']): raise RuntimeError('PROGRESS_OWNERSHIP_LOST')
    if type(phase) is not int or not 1<=phase<=len(stages(gate)): raise ValueError('INVALID_PHASE')
    if subphase is not None and (type(subphase) is not int or not 1<=subphase<=len(substages(gate)[phase-1])):
        raise ValueError('INVALID_SUBPHASE')
    if outcome not in (None,'success','failed'): raise ValueError('INVALID_OUTCOME')
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_connect import connect_closing
    with connect_closing(board=gate['board']) as conn, db.write_txn(conn), U.lock(run_dir/'progress.lock'):
        journal=U.read(run_dir.parent.parent/'journal.json')
        if not journal or not journal.get('active') or journal['active']['token']!=token or not active_card(conn,journal,gate):
            raise RuntimeError('PROGRESS_CLAIM_LOST')
        if Path(gate['directory']).resolve()!=run_dir.resolve(): raise RuntimeError('PROGRESS_DIRECTORY_MISMATCH')
        progress=U.read(run_dir/'progress.json',dict(phase=0,phases=[],revision=0))
        if progress.get('outcome'): raise RuntimeError('PROGRESS_ALREADY_TERMINAL')
        if phase<progress['phase']: raise ValueError('PHASE_REGRESSION')
        if phase==progress['phase'] and outcome is None and (subphase is None or subphase==progress.get('subphase')): return progress
        clock=time.monotonic()
        if progress.get('subphase') and (phase!=progress['phase'] or subphase!=progress.get('subphase') or outcome):
            progress['phases'][-1]['children'][-1].update(seconds=max(0,clock-progress['subphase_clock']),ended=U.now())
        if progress['phases'] and phase!=progress['phase']:
            progress['phases'][-1].update(seconds=max(0,clock-progress['phase_clock']),ended=U.now())
        if phase!=progress['phase']:
            progress['phases'].append(dict(phase=phase,started=U.now(),seconds=0))
            progress['phase_clock']=clock
            progress.pop('subphase',None)
        if subphase is not None and (subphase!=progress.get('subphase') or not progress['phases'][-1].get('children')):
            progress['phases'][-1].setdefault('children',[]).append(dict(subphase=subphase,started=U.now(),seconds=0))
            progress.update(subphase=subphase,subphase_clock=clock)
        progress.update(phase=phase,revision=progress['revision']+1)
        if outcome:
            progress.update(outcome=outcome,error=safe_error(error) if error else None,artifacts=artifacts or [])
            progress['phases'][-1].update(seconds=max(0,clock-progress['phase_clock']),ended=U.now())
            if progress.get('subphase'):
                child=progress['phases'][-1]['children'][-1]
                if not child.get('ended'): child.update(seconds=max(0,clock-progress['subphase_clock']),ended=U.now())
        U.save(run_dir/'progress.json',progress)
        U.emit(run_dir,progress)
        return progress


def verify_artifacts(paths,root,started_epoch):
    import hashlib
    import zipfile
    from xml.etree import ElementTree
    root=Path(root).resolve()
    verified=[]
    for raw in dict.fromkeys(paths):
        path=Path(raw)
        if not path.is_absolute() or not path.is_file() or not path.resolve().is_relative_to(root): raise ValueError('ARTIFACT_OUTSIDE_OUTPUT_OR_MISSING')
        cursor=path
        while True:
            if cursor.is_symlink() or cursor.is_junction(): raise ValueError('ARTIFACT_REPARSE_POINT')
            if cursor==cursor.parent: break
            cursor=cursor.parent
        stat=path.stat()
        if stat.st_mtime<started_epoch or not stat.st_size: raise ValueError('ARTIFACT_STALE_OR_EMPTY')
        data=path.read_bytes()
        suffix=path.suffix.lower()
        if suffix in ('.xlsx','.docx'):
            with zipfile.ZipFile(path) as archive:
                if archive.testzip(): raise ValueError('ARTIFACT_ZIP_CRC_FAILED')
                required='xl/workbook.xml' if suffix=='.xlsx' else 'word/document.xml'
                for name in ('[Content_Types].xml',required): ElementTree.fromstring(archive.read(name))
        elif suffix in ('.html','.htm'):
            from html.parser import HTMLParser
            text=data.decode('utf-8-sig')
            if '<html' not in text.lower() or '</html>' not in text.lower(): raise ValueError('ARTIFACT_HTML_INVALID')
            HTMLParser().feed(text)
        elif suffix=='.md':
            if not data.decode('utf-8-sig').strip(): raise ValueError('ARTIFACT_MARKDOWN_EMPTY')
        verified.append(dict(path=str(path.resolve()),sha256=hashlib.sha256(data).hexdigest(),size=len(data)))
    return verified


def active_card(conn,journal,active):
    from hermes_cli import kanban_db as db
    card=db.get_task(conn,active['task'])
    return card if owned(card,journal) and card.status=='running' and card.current_run_id==active['run'] else None


def stop_owned(active,scope=None,proc=None):
    forbidden={os.getpid(),active['controller']['pid']}
    fingerprints=[active.get('worker'),active.get('launcher'),*active.get('children',[])]
    if any(f and f['pid'] in forbidden for f in fingerprints): raise RuntimeError('REFUSE_HOST_PROCESS_TERMINATION')
    return U.stop_owned(active,scope,proc)


def artifact_bytes(artifact):
    import hashlib
    data=Path(artifact['path']).read_bytes()
    if len(data)!=artifact['size'] or hashlib.sha256(data).hexdigest()!=artifact['sha256']: raise ValueError('ARTIFACT_CHANGED_AFTER_VERIFICATION')
    return data


def body(active,report):
    names=stages(active); phase=report.get('phase',1)
    lines=[f"**{report.get('outcome','실행 중')} · {phase}/{len(names)}: {names[phase-1]}**",
           f"시작: {active['started']}"]
    if report.get('subphase'):
        child=report['subphase']
        lines.append(f"현재 하위 단계: {phase}.{child} / {len(substages(active)[phase-1])}: {substages(active)[phase-1][child-1]}")
    if report.get('ended'): lines.append(f"종료: {report['ended']} · 총 소요: {U.format_duration(report['seconds'])}")
    rows=['| 단계 | 실제 소요 |','|---|---|']
    timings={p['phase']:p for p in report.get('phases',[])}
    for number,name in enumerate(names,1):
        p=timings.get(number)
        duration=U.format_duration(p['seconds']) if p and p.get('ended') else ('진행 중' if p and not report.get('ended') else '미보고 / 기록 없음')
        rows.append(f'| {number}. {name} | {duration} |')
        for child,label in enumerate(substages(active)[number-1],1):
            intervals=[c for c in (p or {}).get('children',[]) if c['subphase']==child]
            duration=('진행 중' if any(not c.get('ended') for c in intervals) else
                U.format_duration(sum(c['seconds'] for c in intervals))) if intervals else '미보고 / 기록 없음'
            rows.append(f'| {number}.{child}. {label} | {duration} |')
    lines.append('\n'.join(rows))
    lines.append('하위 단계 시간은 실제 보고 전환 구간(재방문 시 누적). 상위 시간에 포함되며 합산하지 않음. 미보고는 미실행을 뜻하지 않음.')
    if report.get('final_response'): lines.append('Native result:\n'+safe_error(report['final_response']))
    if report.get('error'): lines.append('실패 원인: '+safe_error(report['error']))
    if report.get('ended'):
        accounted=sum(p.get('seconds',0) for p in report.get('phases',[]))
        lines.append(f"Worker 시작·종료/기록 등 단계 외 처리: {U.format_duration(max(0,report['seconds']-accounted))}")
    for artifact in report.get('verified',[]):
        lines.append(f"[결과 파일](<{Path(artifact['path']).as_posix()}>)")
    for folder in sorted({str(Path(a['path']).parent) for a in report.get('verified',[])}):
        if Path(folder).is_dir(): lines.append(f'[폴더 열기](<{Path(folder).as_posix()}>)')
    lines += ['완료한 외부 쓰기/부분 산출물은 보존. 취소 시 진행 중 외부 쓰기 결과는 미확정이며 확인 필요.',
              f"로그: `{active['directory']}` · JOB {active['job']} · run {active['run']} · correlation {active['token']}"]
    return '\n\n'.join(lines)


def edit_card(conn,journal,active,report,terminal=False):
    from hermes_cli import kanban_db as db
    with db.write_txn(conn):
        card=db.get_task(conn,active['task'])
        if not owned(card,journal): return False
        latest=conn.execute('SELECT MAX(id) FROM task_runs WHERE task_id=?',(card.id,)).fetchone()[0]
        valid=(card.status in ('done','blocked') and card.current_run_id is None and latest==active['run']) if terminal else bool(active_card(conn,journal,active))
        if not valid: return False
        prefix={'success':'완료','failed':'실패','cancelled':'취소'}.get(report.get('outcome'),'실행 중')
        conn.execute('UPDATE tasks SET title=?,body=? WHERE id=?',
            (f"[{prefix}] {active['name']} · {active['started']}",body(active,report),card.id))
        db._append_event(conn,card.id,'edited',{'fields':['title','body'],'actor':CREATOR,'expected_run_id':active['run']})
        return True


def reject_history(conn,journal):
    from hermes_cli import kanban_db as db
    with db.write_txn(conn):
        for card_id,info in journal['cards'].items():
            card=db.get_task(conn,card_id)
            if info['role']!='history' or not owned(card,journal) or card.status not in ('ready','todo'): continue
            restored='done' if info.get('outcome')=='success' else 'blocked'
            conn.execute("UPDATE tasks SET status=? WHERE id=? AND current_run_id IS NULL",(restored,card_id))
            db._append_event(conn,card_id,'status' if restored=='done' else 'blocked',{'status':restored,'reason':'HISTORY_CARD_IS_NOT_RUNNABLE','actor':CREATOR})


def consume_progress(conn,journal,active,offset):
    path=Path(active['directory'])/'events.jsonl'
    if not path.exists(): return offset
    lines=path.read_text(encoding='utf-8').splitlines(keepends=True)
    while offset<len(lines) and lines[offset].endswith('\n'):
        event=json.loads(lines[offset]); offset+=1
        if not event.get('diagnostic'):
            if event.get('outcome')=='success': event.pop('outcome')  # Agent declaration is not verified completion.
            edit_card(conn,journal,active,event)
    return offset


def finish(conn,state,journal,active,report):
    from hermes_cli import kanban_db as db
    with U.lock(state/'state.lock'):
        current=U.read(state/'journal.json')
        if not current.get('active') or current['active']['token']!=active['token']: raise RuntimeError('FINAL_OWNERSHIP_LOST')
        progress=U.read(Path(active['directory'])/'progress.json',{})
        report=dict(progress,**report)
        clock=time.monotonic()
        if report.get('phases') and not report['phases'][-1].get('ended'):
            report['phases'][-1].update(ended=U.now(),seconds=max(0,clock-report['phase_clock']))
        if report.get('subphase') and report.get('phases'):
            child=report['phases'][-1]['children'][-1]
            if not child.get('ended'): child.update(ended=U.now(),seconds=max(0,clock-report['subphase_clock']))
        report.setdefault('ended',U.now()); report.setdefault('seconds',max(0,clock-active['started_clock']))
        report.update(task=active['task'],run=active['run'],worker=active.get('worker'),token=active['token'])
        if report.get('error'): report['error']=safe_error(report['error'])
        U.save(Path(active['directory'])/'summary.json',{k:v for k,v in dict(report,native_finalized=False).items() if k!='final_response'})
        card=active_card(conn,current,active)
        old=db.get_task(conn,active['task'])
        terminal=conn.execute('SELECT task_id,outcome FROM task_runs WHERE id=?',(active['run'],)).fetchone()
        latest=conn.execute('SELECT MAX(id) FROM task_runs WHERE task_id=?',(active['task'],)).fetchone()[0]
        already_completed=bool(not card and owned(old,current) and old.status=='done' and latest==active['run']
            and terminal and terminal['task_id']==active['task'] and terminal['outcome']=='completed' and report.get('outcome')=='success')
        if not card and not already_completed: report.update(outcome='cancelled',error='OPERATOR_CANCELLED_OR_CLAIM_LOST',verified=[])
        elif already_completed: pass
        elif report['outcome']=='success':
            import mimetypes
            written=[]
            try:
                payloads=[(Path(item['path']),artifact_bytes(item)) for item in report['verified']]
                with db.write_txn(conn):
                    if not active_card(conn,current,active):
                        report.update(outcome='cancelled',error='ATTACHMENT_CLAIM_LOST',verified=[])
                    else:
                        if not db._parents_satisfied(conn,card.id): raise RuntimeError('COMPLETION_PARENT_REOPENED')
                        if card.completion_contract not in (None,'local-only'): raise RuntimeError('COMPLETION_CONTRACT_CHANGED; native review required')
                        attached={a.filename for a in db.list_attachments(conn,card.id)}
                        for path,data in payloads:
                            if path.name not in attached:
                                if len(data)>db.KANBAN_ATTACHMENT_MAX_BYTES: raise db.AttachmentTooLarge('attachment exceeds native limit')
                                dest_dir=db.task_attachments_dir(card.id,board=current['board']); dest_dir.mkdir(parents=True,exist_ok=True)
                                dest=db._collision_free_path(dest_dir,db._safe_attachment_name(path.name))
                                written.append(dest); dest.write_bytes(data)
                                conn.execute('INSERT INTO task_attachments (task_id,filename,stored_path,content_type,size,uploaded_by,created_at) VALUES (?,?,?,?,?,?,?)',
                                    (card.id,dest.name,str(dest.resolve()),mimetypes.guess_type(path.name)[0],len(data),CREATOR,int(time.time())))
                                db._append_event(conn,card.id,'attached',{'filename':dest.name,'size':len(data),'by':CREATOR})
                        summary=body(active,report).splitlines()[0] + ' · 상세: Description.'
                        evidence=(f"{summary} · 산출물 검증 통과: {len(report['verified'])}"
                                  f" · 총 소요: {U.format_duration(report['seconds'])}")
                        # Native high-level completion/attachment APIs prohibit nested transactions.
                        # Compose their native row/event primitives under one ownership fence.
                        conn.execute("UPDATE tasks SET status='done',result=?,completed_at=?,claim_lock=NULL,claim_expires=NULL,worker_pid=NULL,block_kind=NULL,block_recurrences=0 WHERE id=? AND status='running' AND current_run_id=?",
                            (evidence,int(time.time()),card.id,active['run']))
                        run_id=db._end_run(conn,card.id,outcome='completed',status='done',summary=summary)
                        db._append_event(conn,card.id,'completed',db._completed_event_payload(evidence,summary,[],None),run_id=run_id)
                if report['outcome']=='success':
                    db._clear_failure_counter(conn,card.id)
                    db.recompute_ready(conn)
                    db._cleanup_workspace(conn,card.id)
                    db._fire_task_hook('kanban_task_completed',db.get_task(conn,card.id),card.id,active['run'],summary=summary)
            except Exception as exc:
                for dest in written:
                    if not conn.execute('SELECT 1 FROM task_attachments WHERE task_id=? AND stored_path=?',(card.id,str(dest.resolve()))).fetchone():
                        try: dest.unlink(missing_ok=True)
                        except OSError: pass
                if active_card(conn,current,active):
                    report.update(outcome='failed',error=safe_error(exc),verified=[])
                    db.block_task(conn,card.id,reason=report['error'],kind='transient',expected_run_id=active['run'])
                else: report.update(outcome='cancelled',error='ATTACHMENT_CLAIM_LOST',verified=[])
        else:
            if not db.block_task(conn,card.id,reason=safe_error(report.get('error') or report['outcome']),kind='transient',expected_run_id=active['run']):
                report.update(outcome='cancelled',error='FINAL_CLAIM_LOST',verified=[])
        if not already_completed: edit_card(conn,current,active,report,terminal=True)
        report['native_finalized']=bool(db.get_task(conn,active['task']))
        U.save(Path(active['directory'])/'summary.json',{k:v for k,v in report.items() if k!='final_response'})
        current['cards'][active['task']].update(role='history',outcome=report['outcome'])
        current['active']=None
        current['waiting']=None
        current.pop('request',None)
        U.save(state/'journal.json',current)
        ensure_wait(conn,state,current)
        return report


def process_command(journal,action,args=(),fixture=None):
    module=str(Path(fixture or __file__).resolve())
    runtime=journal['runtime']
    code=f"import sys,runpy; sys.path.insert(0,{runtime!r}); runpy.run_path({module!r},run_name='__main__')"
    if fixture:
        # Offline probes bypass PM/bootstrap installs, but use the same native interpreter.
        return [journal['interpreter'] or sys.executable,'-c',code,*args]
    from hermes_cli._launchers import runtime_command
    from hermes_constants import get_hermes_home
    return runtime_command(Path(runtime),[action,*args],code=code,python=journal['interpreter'],home=get_hermes_home())


def await_gate(run_dir):
    from hermes_cli.kanban_db_connect import connect_closing
    run_dir=Path(run_dir)
    U.save(run_dir/'hello.json',U.identity(os.getpid()))
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        gate=U.read(run_dir/'gate.json')
        if gate:
            journal=U.read(run_dir.parent.parent/'journal.json')
            if gate.get('worker')!=U.identity(os.getpid()) or not U.alive(gate.get('controller')) or not journal or not journal.get('active') or journal['active']['token']!=gate['token']:
                raise RuntimeError('STARTUP_OWNERSHIP_LOST')
            with connect_closing(board=gate['board']) as conn:
                if not active_card(conn,journal,gate): raise RuntimeError('STARTUP_CLAIM_LOST')
            return gate
        time.sleep(.025)
    raise RuntimeError('STARTUP_GATE_TIMEOUT')


def supervise(job,state=None,origin='automatic',fixture=None,timeout=3500):
    import subprocess
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.kanban_db_dispatch import adopt_worker_pid,heartbeat_worker
    from hermes_cli.local_runtime.processes import spawn_server
    valid_job_id(job)
    if not 0<timeout<=3500: raise ValueError('SUPERVISOR_TIMEOUT_OUTSIDE_BOUND')
    state=state_path(state)/job
    with U.lock(state/'job.lock',blocking=False):
        journal=U.read(state/'journal.json')
        if not journal or not journal.get('enabled'): raise RuntimeError('JOB_NOT_INITIALIZED')
        with connect_closing(board=journal['board']) as conn:
            if journal.get('active'):
                active=journal['active']
                if U.alive(active.get('controller')) and active['controller']!=U.identity(os.getpid()): raise RuntimeError('CONTROLLER_STILL_ALIVE')
                stop_owned(active)
                summary=U.read(Path(active['directory'])/'summary.json')
                terminal=conn.execute('SELECT outcome FROM task_runs WHERE id=? AND task_id=?',(active['run'],active['task'])).fetchone()
                report=summary if summary and summary.get('token')==active['token'] and terminal and terminal['outcome']=='completed' else dict(outcome='cancelled',error='SUPERVISOR_LOST; interrupted external writes have unknown outcome',verified=[])
                return finish(conn,state,journal,active,report)
            reject_history(conn,journal)
            leaked=[c for c in db.list_tasks(conn,status='running',tenant=TENANT) if owned(c,journal)]
            if leaked:
                for card in leaked:
                    if card.worker_pid: raise RuntimeError('RECOVERY_WORKER_IDENTITY_MISSING')
                    if not db.block_task(conn,card.id,reason='SUPERVISOR_LOST_BEFORE_SPAWN',kind='transient',expected_run_id=card.current_run_id): raise RuntimeError('RECOVERY_CLAIM_LOST')
                    journal['cards'][card.id].update(role='history',outcome='cancelled')
                    journal['waiting']=None
                U.save(state/'journal.json',journal)
                ensure_wait(conn,state,journal)
                return dict(outcome='cancelled',error='SUPERVISOR_LOST_BEFORE_SPAWN')
            if origin=='recovery':
                ensure_wait(conn,state,journal)
                return dict(recovered=True)
            card_id=ensure_wait(conn,state,journal)
            card=db.get_task(conn,card_id)
            if not owned(card,journal) or journal['cards'][card_id]['role']!='wait': raise RuntimeError('WAIT_OWNERSHIP_LOST')
            request=journal.get('request')
            if request and (request['task']!=card_id or request['key']!=card.idempotency_key): raise RuntimeError('MANUAL_REQUEST_LOST')
            if card.status=='scheduled' and origin=='automatic' and not request: db.unblock_task(conn,card_id)
            if card.status=='todo': db.recompute_ready(conn)
            claimed=db.claim_task(conn,card_id,ttl_seconds=86400)
            if not claimed: raise RuntimeError('WAIT_NOT_READY')
            token=uuid.uuid4().hex; directory=state/'runs'/token; directory.mkdir(parents=True)
            active=dict(job=job,board=journal['board'],task=card_id,run=claimed.current_run_id,token=token,
                started=U.now(),started_epoch=time.time(),started_clock=time.monotonic(),directory=str(directory),
                controller=U.identity(os.getpid()),worker=None,output_root=journal['output_root'],name=journal['name'],plan=journal['plan'],origin='manual' if request else origin)
            journal['active']=active; journal['cards'][card_id]['role']='active'
            U.save(state/'journal.json',journal)
            if os.name!='nt': raise RuntimeError('WINDOWS_CONTAINMENT_REQUIRED')
            proc=scope=None
            report=dict(outcome='failed',error='WORKER_NO_RESULT',verified=[])
            try:
                preparation=dict(phase=1,phases=[dict(phase=1,started=active['started'],seconds=0)],
                    phase_clock=active['started_clock'],revision=1)
                if substages(active)[0]:
                    preparation.update(subphase=1,subphase_clock=active['started_clock'])
                    preparation['phases'][0]['children']=[dict(subphase=1,started=active['started'],seconds=0)]
                U.save(directory/'progress.json',preparation)
                U.emit(directory,preparation)
                edit_card(conn,journal,active,preparation)
                command=process_command(journal,'worker',['--run-dir',str(directory)],fixture=fixture)
                with (directory/'worker-stderr.log').open('wb') as stderr:
                    proc,scope=spawn_server(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,env=dict(os.environ,SCHEDULED_JOBS_RUN=str(directory)))
                active['launcher']=U.identity(proc.pid)
                U.save(state/'journal.json',journal)
                deadline=time.monotonic()+30
                while proc.poll() is None and time.monotonic()<deadline:
                    hello=U.read(directory/'hello.json')
                    if hello: break
                    if not active_card(conn,journal,active): raise RuntimeError('OPERATOR_CANCELLED_BEFORE_GATE')
                    time.sleep(.025)
                hello=U.read(directory/'hello.json')
                candidates=U.tree_identities(active['launcher'])
                if not hello or hello not in candidates or not U.alive(hello): raise RuntimeError('WORKER_HANDSHAKE_FAILED')
                active['worker']=hello
                if not active_card(conn,journal,active): raise RuntimeError('OPERATOR_CANCELLED_BEFORE_GATE')
                U.save(state/'journal.json',journal)
                adopt_worker_pid(conn,card_id,active['run'],hello['pid'])
                U.save(directory/'gate.json',active)
                offset=1; heartbeat=0; cancelled=False
                while proc.poll() is None:
                    children=U.tree_identities(active['launcher'])
                    if children!=active.get('children'):
                        active['children']=children; U.save(state/'journal.json',journal)
                    if not active_card(conn,journal,active): cancelled=True; break
                    if time.monotonic()-active['started_clock']>timeout:
                        report=dict(outcome='failed',error='SUPERVISOR_TIMEOUT; interrupted external writes have unknown outcome',verified=[]); break
                    offset=consume_progress(conn,journal,active,offset)
                    if time.monotonic()-heartbeat>30:
                        heartbeat_worker(conn,card_id,expected_run_id=active['run']); heartbeat=time.monotonic()
                    time.sleep(.025)
                if cancelled: report=dict(outcome='cancelled',error='OPERATOR_CANCELLED; completed writes preserved; inflight outcome unknown',verified=[])
                elif proc.poll() is not None:
                    report=U.read(directory/'result.json',report)
                    if proc.returncode!=0: report.update(outcome='failed',error=report.get('error') or f'WORKER_EXIT_{proc.returncode}')
                stop_owned(active,scope,proc)
                consume_progress(conn,journal,active,offset)
                if report['outcome']=='success':
                    progress=U.read(directory/'progress.json',{})
                    paths=progress.get('artifacts',[])
                    report['verified']=verify_artifacts(paths,journal['output_root'],active['started_epoch'])
            except Exception as exc:
                U.diagnostic(directory,'supervisor',exc)
                report=dict(outcome='failed',error=safe_error(exc),verified=[])
            finally:
                if proc: stop_owned(active,scope,proc)
            report['exit_code']=proc.returncode if proc else None
            return finish(conn,state,journal,active,report)


def worker(run_dir,runner=None):
    """Use the native original engine; the outer native fire owns delivery/bookkeeping."""
    from cron.scheduler import run_job,_fire_secret_scope,_cron_failure_marker_error
    from tools.terminal_scope import install_and_reset_profile_terminal_scope
    from hermes_constants import get_hermes_home
    run_dir=Path(run_dir)
    gate=await_gate(run_dir)
    journal=U.read(run_dir.parent.parent/'journal.json')
    os.environ['SCHEDULED_JOBS_RUN']=str(run_dir)
    report_progress(run_dir,gate['token'],1)
    extra=None
    if journal.get('explicit_plan'):
        extra=('Optional execution reporting only; do not add work or completion gates. '
            'Use kanban_job_progress for actual transitions in this numbered plan: '
            +json.dumps(journal['plan'],ensure_ascii=False)+
            '. Missing stage reports do not indicate failure. Never fabricate timing. '
            'Report artifacts only as existing absolute output-root paths. Never bypass approvals.')
    original={}
    try:
        with _fire_secret_scope(),install_and_reset_profile_terminal_scope(get_hermes_home()):
            if not journal.get('explicit_plan'): report_progress(run_dir,gate['token'],2)
            from cron.jobs import get_job
            current=get_job(gate['job'])
            if not current or current.get('script')!=f"kanban-managed-{gate['job']}.py" or not current.get('no_agent') or current.get('interpreter') is not None: raise RuntimeError('ADAPTER_FIELDS_CHANGED')
            original=dict(journal['original'],**{k:v for k,v in current.items() if k not in ('script','no_agent','interpreter')})
            success,output,response,error=(runner or run_job)(original,extra_prompt=extra)
        marker=_cron_failure_marker_error(response or '')
        progress=U.read(run_dir/'progress.json',{})
        empty_error='Agent completed but produced empty response (model error, timeout, or misconfiguration)' if not (response or '').strip() else None
        failed=not success or marker or empty_error or progress.get('outcome')=='failed'
        report=dict(outcome='failed' if failed else 'success',native_success=success,
            error=safe_error(error or marker or progress.get('error') or empty_error or 'NATIVE_RUN_FAILED') if failed else None,
            final_response=response or '')
        (run_dir/'native-output.md').write_text(output or '',encoding='utf-8')
        if not progress.get('outcome'):
            report_progress(run_dir,gate['token'],len(stages(gate)),outcome=report['outcome'],error=report['error'],artifacts=progress.get('artifacts',[]))
    except Exception as exc:
        U.diagnostic(run_dir,'native-job',exc)
        report=dict(outcome='failed',error=safe_error(exc),native_success=False,final_response='')
    # Inherited script-adapter limitation: native run_job mutates only this private dict.
    # Its retry/quota hints are diagnostics, not an outer-scheduler passthrough.
    report['native_retry_metadata']={k:original[k] for k in ('_model_unreachable','_quota_hold_seconds') if k in original}
    if report['outcome']=='failed' and any(report['native_retry_metadata'].values()):
        report['native_retry_warning']='Native Agent retry/quota hints cannot be passed to the outer script scheduler; next normal schedule or manual retry applies'
        report['error']=safe_error(report['error']+'; '+report['native_retry_warning'])
    U.save(run_dir/'result.json',report)
    return report


def launch_manual(journal,job,token):
    import subprocess
    state=Path(journal['state_root'])
    action='supervise' if token=='recovery' else 'manual'
    args=['--job',job,'--state',str(state)]
    args+=['--origin','recovery'] if token=='recovery' else ['--token',token]
    proc=subprocess.Popen(process_command(journal,action,args),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return U.identity(proc.pid)


def dispatch_hook(*,board=None,dry_run=False,state=None,launcher=None,**_):
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_connect import connect_closing
    if dry_run: return False
    root=state_path(state); spawned=False
    for job in status(root):
        directory=root/job
        journal=U.read(directory/'journal.json')
        if not journal or not journal.get('enabled') or journal['board']!=board: continue
        active=journal.get('active')
        if active and U.alive(active.get('controller')): continue
        try:
            with U.lock(directory/'job.lock',blocking=False), U.lock(directory/'state.lock',blocking=False):
                journal=U.read(directory/'journal.json')
                request=journal.get('request')
                if request and U.alive(request.get('launcher')): continue
                with connect_closing(board=board) as conn:
                    reject_history(conn,journal)
                    card=db.get_task(conn,journal.get('waiting',''))
                    recovery=bool(journal.get('active') or not owned(card,journal) or (card and card.status=='running'))
                    if recovery:
                        if U.alive(journal.get('recovery_launcher')): continue
                        journal['state_root']=str(root)
                        journal['recovery_launcher']=(launcher or launch_manual)(journal,job,'recovery')
                        U.save(directory/'journal.json',journal); spawned=True; continue
                    if card and card.status=='todo':
                        db.recompute_ready(conn)
                        card=db.get_task(conn,card.id)
                    if not card or card.status!='ready' or journal['cards'][card.id]['role']!='wait': continue
                    if not request or request['task']!=card.id or request['key']!=card.idempotency_key:
                        request=dict(token=uuid.uuid4().hex,task=card.id,key=card.idempotency_key,created=U.now(),launcher=None,attempts=0)
                    if request.get('retry_after',0)>time.time(): continue
                    token=request['token']
                    journal['request']=request
                    journal['state_root']=str(root)
                    U.save(directory/'journal.json',journal)
                    journal['request']['launcher']=(launcher or launch_manual)(journal,job,token)
                    U.save(directory/'journal.json',journal); spawned=True
        except RuntimeError as exc:
            if str(exc)!='JOB_BUSY': raise
    return spawned


def manual(job,token,state=None,executor=None):
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_connect import connect_closing
    valid_job_id(job)
    directory=state_path(state)/job
    try:
        with U.lock(directory/'job.lock',blocking=False), U.lock(directory/'state.lock'):
            journal=U.read(directory/'journal.json')
            request=journal.get('request')
            if not request or request['token']!=token: raise RuntimeError('MANUAL_INTENT_LOST')
            if journal.get('active'): return dict(claimed=False,success=False,error='MANUAL_DEFERRED_TO_SUPERVISOR')
            with connect_closing(board=journal['board']) as conn:
                card=db.get_task(conn,request['task'])
                if not card or not owned(card,journal) or journal.get('waiting')!=card.id or card.idempotency_key!=request['key'] or journal['cards'][card.id]['role']!='wait' or card.status not in ('ready','todo') or card.current_run_id is not None:
                    journal.pop('request',None); U.save(directory/'journal.json',journal)
                    return dict(claimed=False,success=False,error='MANUAL_CARD_CANCELLED_BEFORE_NATIVE_CLAIM')
                if card.status=='todo':
                    db.recompute_ready(conn)
                    card=db.get_task(conn,card.id)
                if not card or card.status!='ready': return dict(claimed=False,success=False,error='MANUAL_DEPENDENCIES_NOT_READY')
                request['attempts']=request.get('attempts',0)+1
                U.save(directory/'journal.json',journal)
    except RuntimeError as exc:
        if str(exc)!='JOB_BUSY': raise
        return dict(claimed=False,success=False,error='MANUAL_DEFERRED_TO_SUPERVISOR')
    from tools.cronjob_tools import execute_job_for_event
    executor_exception=False
    try:
        result=(executor or execute_job_for_event)(job)
    except Exception as exc:
        executor_exception=True
        result=dict(claimed=False,success=False,error=type(exc).__name__+': '+safe_error(exc))
    try:
        with U.lock(directory/'job.lock',blocking=False):
            with U.lock(directory/'state.lock'):
                journal=U.read(directory/'journal.json')
                request=journal.get('request')
                if not request or request['token']!=token or journal.get('active'): return result
                with connect_closing(board=journal['board']) as conn:
                    card=db.get_task(conn,request['task'])
                    if not owned(card,journal) or journal.get('waiting')!=card.id or card.idempotency_key!=request['key'] or journal['cards'][card.id]['role']!='wait' or card.status!='ready' or card.current_run_id is not None: return result
                    if result.get('success'):
                        journal.pop('request',None); U.save(directory/'journal.json',journal)
                        return result
                    from cron.executions import live_inflight_execution
                    if not executor_exception and live_inflight_execution(job):
                        # ponytail: three attempts, five-second cooldown; durable intent, no retry worker.
                        if request['attempts']<3:
                            request.update(retry_after=time.time()+5,launcher=None)
                            U.save(directory/'journal.json',journal)
                            return result
                        result=dict(result,error=safe_error(result.get('error') or 'NATIVE_INFLIGHT_CONTENTION')+'; native contention retry limit reached (3 attempts)')
                    claimed=db.claim_task(conn,card.id)
                    if not claimed: return result
                    run_dir=directory/'runs'/token
                    run_dir.mkdir(parents=True,exist_ok=True)
                    active=dict(job=job,board=journal['board'],task=card.id,run=claimed.current_run_id,token=token,
                        started=U.now(),started_epoch=time.time(),started_clock=time.monotonic(),directory=str(run_dir),
                        controller=U.identity(os.getpid()),worker=None,output_root=journal['output_root'],name=journal['name'],plan=journal['plan'],origin='manual')
                    journal['active']=active; journal['cards'][card.id]['role']='active'
                    U.save(directory/'journal.json',journal); U.save(run_dir/'gate.json',active)
                    error=safe_error(result.get('error') or 'NATIVE_EXECUTOR_FAILED_BEFORE_SUPERVISOR')
                    U.diagnostic(run_dir,'preparation',RuntimeError(error))
                    report_progress(run_dir,token,1,outcome='failed',error=error)
            with connect_closing(board=journal['board']) as conn:
                finish(conn,directory,journal,active,dict(outcome='failed',error=error,verified=[]))
    except RuntimeError as exc:
        if str(exc)!='JOB_BUSY': raise
    return result


def unregister_job(job):
    valid_job_id(job)
    from hermes_constants import get_hermes_home
    from cron.jobs import get_job,update_job,_jobs_lock,_fire_job_lock
    from cron.executions import live_inflight_execution
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_connect import connect_closing
    directory=safe_path(private_state()/job,get_hermes_home())
    with U.lock(directory/'job.lock',blocking=False), U.lock(directory/'state.lock'), _fire_job_lock(job) as fenced, _jobs_lock():
        if not fenced: raise RuntimeError('JOB_BUSY')
        journal=U.read(directory/'journal.json')
        if not journal or not journal.get('enabled'): return {'unregistered':False}
        if journal.get('active') or journal.get('request') or U.alive(journal.get('recovery_launcher')) or live_inflight_execution(job): raise RuntimeError('JOB_ACTIVE')
        current=get_job(job)
        wrapper=f'kanban-managed-{job}.py'
        script=safe_path(get_hermes_home()/'scripts'/wrapper,get_hermes_home()/'scripts')
        if not current or current.get('script')!=wrapper or not current.get('no_agent') or current.get('interpreter') is not None: raise RuntimeError('ADAPTER_FIELDS_CHANGED')
        if not script.is_file() or script.read_text(encoding='utf-8')!=wrapper_code(job): raise RuntimeError('WRAPPER_CHANGED')
        with connect_closing(board=journal['board']) as conn:
            card=db.get_task(conn,journal.get('waiting',''))
            if card and (not owned(card,journal) or card.current_run_id is not None or card.status not in ('scheduled','blocked')): raise RuntimeError('WAIT_NOT_IDLE')
            restored={k:journal['original'].get(k) for k in ('script','no_agent','interpreter')}
            expected=dict(current,**restored)
            if update_job(job,restored)!=expected or get_job(job)!=expected: raise RuntimeError('UNREGISTER_READBACK_MISMATCH')
            journal['enabled']=False
            U.save(directory/'journal.json',journal)
            if card and not db.archive_task(conn,card.id): raise RuntimeError('WAIT_ARCHIVE_FAILED')
        script.unlink()
        return {'unregistered':True,'history_preserved':True}


def parser(existing=None):
    import argparse
    p=existing or argparse.ArgumentParser(prog='kanban-jobs')
    subs=p.add_subparsers(dest='action',required=True)
    for action in ('register','unregister','list','status','dispatch','supervise','manual'):
        sub=subs.add_parser(action)
        sub.add_argument('--board',default='default')
        sub.add_argument('--state',type=Path,help=argparse.SUPPRESS)
        if action in ('register','unregister','supervise','manual'): sub.add_argument('--job',required=True)
        if action=='register':
            sub.add_argument('--output-root',type=Path)
            sub.add_argument('--stages-json',type=Path)
        if action=='supervise':
            sub.add_argument('--origin',choices=('automatic','manual','recovery'),default='automatic')
            sub.add_argument('--fixture',help=argparse.SUPPRESS)
            sub.add_argument('--timeout',type=float,default=3500)
        if action=='manual': sub.add_argument('--token',required=True)
    for action in ('worker','report'):
        sub=subs.add_parser(action); sub.add_argument('--run-dir',type=Path,required=True)
        if action=='report':
            sub.add_argument('--token',required=True); sub.add_argument('--phase',type=int,required=True)
            sub.add_argument('--subphase',type=int)
            sub.add_argument('--outcome',choices=('success','failed')); sub.add_argument('--error')
            sub.add_argument('--artifacts-json',type=Path)
    return p


def execute(args):
    try:
        if args.action=='register': result={'waiting':register_job(args.job,args.board,args.output_root,U.read(args.stages_json) if args.stages_json else None)}
        elif args.action in ('status','list'): result={key:{k:v for k,v in j.items() if k!='original'} for key,j in status(args.state).items()}
        elif args.action=='unregister': result=unregister_job(args.job)
        elif args.action=='dispatch': result={'spawned':dispatch_hook(board=args.board,state=args.state)}
        elif args.action=='manual': result=manual(args.job,args.token,args.state)
        elif args.action=='report': result=report_progress(args.run_dir,args.token,args.phase,subphase=args.subphase,outcome=args.outcome,error=args.error,artifacts=U.read(args.artifacts_json) if args.artifacts_json else None)
        elif args.action=='worker':
            result=worker(args.run_dir)
            return (0 if result['outcome']=='success' else 1),'worker finished'
        else:
            result=supervise(args.job,args.state,args.origin,args.fixture,args.timeout)
            if result.get('outcome')=='success': return 0,result['final_response']
            if result.get('outcome'): return 1,'[CRON_FAILURE]\n'+safe_error(result.get('error') or result['outcome'])
        return 0,json.dumps(result,ensure_ascii=False)
    except Exception as exc: return 1,json.dumps(dict(success=False,error=safe_error(exc)),ensure_ascii=False)


def progress_tool(arguments):
    run_dir=os.environ.get('SCHEDULED_JOBS_RUN')
    if not run_dir: return json.dumps(dict(success=False,error='NOT_ISOLATED_SCHEDULED_WORKER'))
    gate=U.read(Path(run_dir)/'gate.json')
    result=report_progress(run_dir,gate['token'],arguments['phase'],
        subphase=arguments.get('subphase'),outcome=arguments.get('outcome'),error=arguments.get('error'),artifacts=arguments.get('artifacts'))
    return json.dumps(dict(success=True,phase=result['phase'],subphase=result.get('subphase'),revision=result['revision']))


if __name__=='__main__':
    code,text=execute(parser().parse_args()); print(text); sys.exit(code)
