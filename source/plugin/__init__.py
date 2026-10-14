"""Registration is inert: commands, an owned dispatch hook and operating guidance only."""
import os
from pathlib import Path
from .job_manager import parser,execute,dispatch_hook,progress_tool


def register(ctx):
    def cli(args):
        code,text=execute(args)
        print(text)
        if code: raise SystemExit(code)
    ctx.register_cli_command('kanban-jobs','Explicit native Kanban job registrations',parser,cli)
    ctx.register_hook('on_kanban_dispatch_tick',dispatch_hook)
    ctx.register_skill('kanban-job-manager',Path(__file__).parent/'skills'/'kanban-job-manager'/'SKILL.md',description='Use when explicitly registering or operating native jobs on Kanban.')
    ctx.register_tool('kanban_job_progress',toolset='terminal',
        schema={'name':'kanban_job_progress','description':'Optional actual stage/substage transition and current-run output files. No credentials or business rows.',
            'parameters':{'type':'object','properties':{
                'phase':{'type':'integer','minimum':1,'maximum':32},
                'subphase':{'type':'integer','minimum':1},
                'outcome':{'type':'string','enum':['success','failed']},
                'error':{'type':'string'},'artifacts':{'type':'array','items':{'type':'string'}}},
                'required':['phase'],'additionalProperties':False}},
        handler=progress_tool,check_fn=lambda:bool(os.environ.get('SCHEDULED_JOBS_RUN')))
