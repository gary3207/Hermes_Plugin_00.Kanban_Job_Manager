"""Shared persistence and owned-process safety extracted from the existing controller."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

def format_duration(seconds):
    minutes, milliseconds = divmod(max(0, round(seconds * 1000)), 60000)
    return f'{minutes}분 {milliseconds / 1000:06.3f}초'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def display(iso):
    at = datetime.fromisoformat(iso).astimezone()
    noon = '오전' if at.hour < 12 else '오후'
    return f'{at.year}년 {at.month}월 {at.day}일 {noon} {at.hour % 12 or 12}시 {at.minute}분 {at.second}초'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.journal-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt==19: raise
                time.sleep(.025)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read(path, default=None) -> Any:
    for attempt in range(20):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError: return default
        except PermissionError:
            if attempt == 19: raise
            time.sleep(.025)  # Windows replacement briefly refuses shared readers.


@contextmanager
def lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0'); stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not blocking: raise RuntimeError('JOB_BUSY') from None
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


def identity(pid):
    import psutil
    try:
        process = psutil.Process(int(pid))
        return {'pid': process.pid, 'birth': process.create_time()}
    except psutil.NoSuchProcess: return None


def alive(fingerprint):
    return bool(fingerprint and identity(fingerprint['pid']) == fingerprint)


def emit(run_dir, event):
    path=Path(run_dir)/'events.jsonl'
    data=(json.dumps(event, ensure_ascii=False)+'\n').encode('utf-8')
    # ponytail: 64 KiB diagnostic budget per run; stage/final evidence is never dropped.
    if event.get('diagnostic') and (path.stat().st_size if path.exists() else 0)+len(data)>64*1024: return
    with path.open('ab') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def tree_identities(root):
    import psutil
    if not alive(root): return []
    try:
        return [root]+[f for p in psutil.Process(root['pid']).children(recursive=True) if (f := identity(p.pid))]
    except psutil.NoSuchProcess: return []


def stop_owned(active, scope=None, proc=None):
    """Close the contained tree, then verify identities, not bare reused PIDs."""
    import psutil
    known = [f for f in [active.get('worker'),active.get('launcher')]+active.get('children',[]) if f]
    if scope: scope.close()
    else:
        # Recovery: Windows job already died with controller; fingerprint checks fence fallback.
        for f in reversed(known):
            if alive(f): psutil.Process(f['pid']).kill()
    if proc: proc.wait(timeout=10)
    deadline=time.monotonic()+10
    while any(alive(f) for f in known):
        if time.monotonic()>deadline: raise RuntimeError('OWNED_TREE_STILL_ALIVE')
        time.sleep(.05)


def diagnostic(run_dir, component, exc):
    import traceback
    causes=[]
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        causes.append(dict(error_class=type(exc).__name__,errno=getattr(exc,'errno',None),
                           frames=[dict(file=Path(f.filename).name,function=f.name,line=f.lineno)
                                   for f in traceback.extract_tb(exc.__traceback__)]))
        exc=exc.__cause__ or exc.__context__
    emit(run_dir,dict(timestamp=now(),component=component,causes=causes,diagnostic=True))
