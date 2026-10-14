"""Offline synthetic worker; production does not import it."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import job_manager as m

p=argparse.ArgumentParser(); p.add_argument('--run-dir',required=True)
args=p.parse_args(); run=Path(args.run_dir)
gate=m.await_gate(run)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])
m.U.save(run/'fixture-child.json',m.U.identity(child.pid))
if os.environ.get('SCHEDULED_JOBS_FIXTURE_MODE')=='hold':
    while True: time.sleep(.05)
for phase in range(1,len(m.stages(gate))+1): m.report_progress(run,gate['token'],phase)
root=Path(gate['output_root']); root.mkdir(parents=True,exist_ok=True)
artifact=root/(gate['token']+'.md'); artifact.write_text('# Synthetic output\n',encoding='utf-8')
m.report_progress(run,gate['token'],len(m.stages(gate)),outcome='success',artifacts=[str(artifact)])
m.U.save(run/'result.json',dict(outcome='success',native_success=True,final_response='Synthetic fixture result'))
