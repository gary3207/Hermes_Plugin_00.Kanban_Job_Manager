"""Offline checks using the installed Hermes dependency environment."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

SOURCE = Path(__file__).resolve().parent
command = json.loads(subprocess.check_output(['hermes', '--print-runtime-command'], text=True, shell=os.name == 'nt'))
constants = [n.value for n in ast.walk(ast.parse(command[3])) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
runtime = next(Path(p) for p in constants if Path(p).name == 'hermes-agent' and Path(p).is_dir())
sys.path.insert(0, str(runtime))
from pm.environments import runtime_facts_path
facts = json.loads(runtime_facts_path(runtime).read_text(encoding='utf-8'))
venv = Path(facts['packages']['venv']['environment'])
deps = venv / ('Lib/site-packages' if os.name == 'nt' else f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
paths = [SOURCE, SOURCE / 'plugin', runtime, deps]
sys.path[:0] = list(map(str, paths))
os.environ['PYTHONPATH'] = os.pathsep.join(map(str, paths))
work = Path(os.environ.get('KANBAN_TEST_WORK', SOURCE / 'test-work'))
work.mkdir(exist_ok=True)
os.environ['HERMES_HOME'] = str(work / 'bootstrap-home')
# kanban_home() ignores HERMES_HOME; without this pin tests write to the user's real kanban.db.
os.environ['HERMES_KANBAN_HOME'] = str(work / 'kanban-home')
for pin in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD'): os.environ.pop(pin, None)
from hermes_cli.kanban_db import kanban_db_path
assert Path(kanban_db_path(board='default')).resolve().is_relative_to(work.resolve()), 'REFUSING_REAL_KANBAN_DB'
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1 else unittest.defaultTestLoader.discover(str(SOURCE), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
from hermes_cli.observability.relay_shared_metrics import shutdown_runtimes
shutdown_runtimes()
sys.exit(not result.wasSuccessful())
