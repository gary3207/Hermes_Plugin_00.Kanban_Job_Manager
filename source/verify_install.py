"""Native validation or immutable private-Git reinstall into a NEW disposable home."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--home',type=Path,required=True,help='New short disposable data-home path; never the live profile')
p.add_argument('--repository',help='owner/repository/source/plugin (remote publication is a separate authorized step)')
p.add_argument('--ref',help='Exact immutable 40-character commit SHA for remote installation')
p.add_argument('--payload',type=Path,default=Path(__file__).parent/'plugin')
a=p.parse_args()
if a.repository and (not a.ref or not re.fullmatch('[0-9a-fA-F]{40}',a.ref)): p.error('--repository requires an exact 40-character --ref')
if a.home.exists(): p.error('--home must not exist; refusing to overwrite an existing home')
if a.home.resolve()==Path(os.environ.get('HERMES_HOME',Path.home()/'.hermes')).resolve(): p.error('refusing active data home')
a.home.mkdir(parents=True)
# Command-scoped gh authentication only. Never resolve/print tokens or change global Git config.
env=dict(os.environ,HERMES_HOME=str(a.home.resolve()),GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never',
    GIT_CONFIG_COUNT='2',GIT_CONFIG_KEY_0='credential.helper',GIT_CONFIG_VALUE_0='',
    GIT_CONFIG_KEY_1='credential.helper',GIT_CONFIG_VALUE_1='!gh auth git-credential')
def cli(*args):
    if os.name=='nt' and any(any(c in str(arg) for c in '&|<>^%!"\r\n') for arg in args):
        raise ValueError('Windows batch arguments must not contain shell metacharacters')
    result=subprocess.run(['hermes',*args],env=env,capture_output=True,text=True,shell=os.name=='nt',timeout=600)
    if result.returncode: raise RuntimeError(f'Native command failed ({result.returncode}): {args!r}\n{result.stderr}\n{result.stdout}')
    print(result.stdout.strip())
    return result.stdout

if a.repository:
    cli('plugins','install',a.repository,'--ref',a.ref,'--no-enable','--yes-deps')
    cli('plugins','enable','hermes-kanban-job-manager','--no-allow-tool-override')
    cli('plugins','list','--plain','--no-bundled')
    assert json.loads(cli('kanban-jobs','status'))=={}
    assert json.loads(cli('kanban-jobs','list'))=={}
    assert not (a.home/'cron'/'jobs.json').exists(), 'install unexpectedly created jobs'
    assert not (a.home/'plugin-data'/'hermes-kanban-job-manager').exists(), 'install unexpectedly created registrations'
    target='hermes-kanban-job-manager'
else:
    target=str(a.payload.resolve())
cli('plugins','validate',str(a.payload.resolve()) if not a.repository else str(a.home/'plugins'/target),'--json')
cli('plugins','doctor',target,'--ci')
print(json.dumps({'verified':'native-local-payload' if not a.repository else 'native-immutable-install','home':str(a.home),'ref':a.ref,'jobs_created':False}))
