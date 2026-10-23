"""Offline direct-engine regressions; synthetic loopback provider, disposable homes."""
import inspect
import importlib
import json
import os
import socket
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import unittest
from unittest.mock import patch


class AgentEngineTests(unittest.TestCase):
    def setUp(self):
        # Native process-global log handlers can retain Windows lockfiles until exit.
        Path(os.environ['HERMES_HOME']).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['HERMES_HOME'], ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            'PATH', 'PYTHONPATH', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TMPDIR', 'TEMP', 'TMP'}}
        environment.update({
            'HERMES_HOME': str(self.home), 'HERMES_KANBAN_HOME': str(self.home),
            'HOME': str(self.home), 'USERPROFILE': str(self.home),
            'HERMES_MANAGED_DIR': str(self.home / 'managed'),
            'HERMES_CRON_TIMEOUT': '20', 'HERMES_CRON_SESSION_DB_TIMEOUT': '5',
            'NO_PROXY': '127.0.0.1,localhost', 'HERMES_DISABLE_LAZY_INSTALLS': '1',
            'OPENAI_API_KEY': 'synthetic-not-a-credential', 'HERMES_INFERENCE_PROVIDER': 'custom',
        })
        self.env = patch.dict(os.environ, environment, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        connect = socket.socket.connect
        getaddrinfo = socket.getaddrinfo
        def local_connect(sock, address):
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1', 'localhost'):
                raise AssertionError('Offline fixture forbids non-loopback connection')
            return connect(sock, address)
        def local_lookup(host, *args, **kwargs):
            if host not in ('127.0.0.1', '::1', 'localhost', None):
                raise AssertionError('Offline fixture forbids external DNS')
            return getaddrinfo(host, *args, **kwargs)
        for target, replacement in [('socket.socket.connect', local_connect), ('socket.getaddrinfo', local_lookup)]:
            guard = patch(target, replacement)
            guard.start()
            self.addCleanup(guard.stop)

    def _real_run(self, final_text='SYNTHETIC_DONE', cancel_event=None, after_run=None, expect_agent=True, preflight=False,
                  runner=None, mcp_servers=None, tool_name='read_file'):
        engine = importlib.import_module('agent_engine')
        self.assertTrue(Path(engine.__file__ or '').is_file(), 'direct Agent engine module is missing')
        requests = []
        fixture = self.home / 'fixture.txt'
        fixture.write_text('SYNTHETIC_TOOL_VALUE', encoding='utf-8')

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if 'messages' not in body:
                    self.send_error(404)
                    return
                requests.append(body)
                tool_results = [m for m in body['messages'] if m['role'] == 'tool']
                deferred = tool_name not in [t['function']['name'] for t in body.get('tools', [])]
                if not tool_results or (deferred and len(tool_results) == 1):
                    name, arguments = tool_name, {'path': str(fixture)} if tool_name == 'read_file' else {}
                    if deferred:
                        name, arguments = ('tool_describe', {'names': [tool_name]}) if not tool_results else (
                            'tool_call', {'calls': [{'name': tool_name, 'arguments': arguments}]})
                    message: dict = dict(role='assistant', content=None, tool_calls=[dict(
                        id='fixture_call_' + str(len(tool_results)), type='function', function=dict(
                            name=name, arguments=json.dumps(arguments)))])
                    finish = 'tool_calls'
                else:
                    message = dict(role='assistant', content=final_text)
                    if cancel_event is not None:
                        cancel_event.set()
                    finish = 'stop'
                payload = json.dumps(dict(id='synthetic', object='chat.completion', created=0,
                    model='synthetic-fixture', choices=[dict(index=0, message=message, finish_reason=finish)],
                    usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))).encode()
                if body.get('stream'):
                    delta = dict(message)
                    if delta.get('tool_calls'):
                        delta['tool_calls'][0]['index'] = 0
                    chunk = dict(id='synthetic', object='chat.completion.chunk', created=0,
                        model='synthetic-fixture', choices=[dict(index=0, delta=delta, finish_reason=None)])
                    end = dict(id='synthetic', object='chat.completion.chunk', created=0,
                        model='synthetic-fixture', choices=[dict(index=0, delta={}, finish_reason=finish)])
                    payload = ('data: ' + json.dumps(chunk) + '\n\ndata: ' + json.dumps(end)
                               + '\n\ndata: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f'http://127.0.0.1:{server.server_port}/v1'
        (self.home / 'config.yaml').write_text(json.dumps(dict(
            model=dict(default='synthetic-fixture', provider='custom', base_url=base_url),
            agent=dict(max_turns=3), cron=dict(preflight=preflight),
            compression=dict(enabled=False), memory=dict(enabled=False),
            terminal=dict(backend='local'), mcp_servers=mcp_servers or {})), encoding='utf-8')
        job = dict(id='synthetic-engine', name='Synthetic fixture',
                   prompt='Read the local fixture using read_file, then answer.',
                   provider='custom', model='synthetic-fixture', base_url=base_url,
                   enabled_toolsets=list(mcp_servers) if mcp_servers else ['file'], workdir=str(self.home), preflight_alerted=True)
        original = json.loads(json.dumps(job))
        from cron import scheduler
        from run_agent import AIAgent
        engine_helper_teardown = scheduler._teardown_cron_agent
        with patch('cron.scheduler.run_job', side_effect=AssertionError('scheduler forbidden')), \
             patch('cron.scheduler_authority.run_canonical_job', side_effect=AssertionError('authority forbidden')), \
             patch('hermes_cli.gateway_client.connect_gateway', side_effect=AssertionError('gateway forbidden')), \
             patch('hermes_cli.gateway_runtime.ensure_gateway_runtime', side_effect=AssertionError('gateway start forbidden')), \
             patch.object(scheduler, '_teardown_cron_agent', wraps=scheduler._teardown_cron_agent) as teardown:
            teardown.__signature__ = inspect.signature(engine_helper_teardown)
            result = (runner or engine.run)(job, execution_id='fixture-execution', cancel_event=cancel_event)
            if after_run is not None:
                after_run(teardown)
        self.assertEqual(teardown.call_count, 1)
        agent = teardown.call_args.args[0]
        if expect_agent:
            self.assertIs(type(agent), AIAgent)  # No replacement constructor or conversation loop.
            self.assertFalse(agent._persist_disabled)
            self.assertIsNone(agent.client)
        else:
            self.assertIsNone(agent)
        self.assertEqual(job, original)
        return result, requests

    def test_real_agent_calls_local_tool_and_persists_without_gateway(self):
        result, requests = self._real_run()
        self.assertTrue(result[0], result)
        self.assertEqual(result[2], 'SYNTHETIC_DONE')
        self.assertGreaterEqual(len(requests), 2)
        self.assertTrue(any('SYNTHETIC_TOOL_VALUE' in str(m.get('content'))
                            for r in requests for m in r['messages'] if m['role'] == 'tool'))
        import sqlite3
        with sqlite3.connect(self.home / 'state.db') as db:
            rows = db.execute('SELECT role, content FROM messages').fetchall()
            self.assertTrue(any(role == 'tool' and 'SYNTHETIC_TOOL_VALUE' in content for role, content in rows))
            self.assertTrue(any(role == 'assistant' and 'SYNTHETIC_DONE' in content for role, content in rows))
            session = db.execute('SELECT source, end_reason, cwd FROM sessions').fetchone()
            self.assertEqual(session, ('cron', 'cron_complete', str(self.home)))

    def test_real_empty_placeholder_is_not_deliverable_text(self):
        result, _ = self._real_run('(No response generated)')
        self.assertTrue(result[0], result)
        self.assertEqual(result[2], '')

    def test_real_cancellation_is_failure_and_still_tears_down(self):
        result, _ = self._real_run(cancel_event=threading.Event())
        self.assertFalse(result[0], result)
        self.assertEqual(result[2], '')
        self.assertIn('RuntimeError', result[3])

    def test_native_invalid_config_refuses_before_constructing_agent(self):
        engine = importlib.import_module('agent_engine')
        (self.home / 'config.yaml').write_text('model: [unterminated', encoding='utf-8')
        from cron import scheduler
        signature = inspect.signature(scheduler._construct_cron_agent)
        init_signature = inspect.signature(scheduler._init_cron_mcp_tools)
        with patch('cron.scheduler._construct_cron_agent', wraps=scheduler._construct_cron_agent) as construct, \
             patch.object(scheduler, '_init_cron_mcp_tools', wraps=scheduler._init_cron_mcp_tools) as initialize:
            construct.__signature__ = signature
            initialize.__signature__ = init_signature
            result = engine.run(dict(id='fixture', prompt='Read fixture'), initialize_mcp=True)
        self.assertFalse(result[0], result)
        construct.assert_not_called()
        initialize.assert_not_called()

    def test_safe_exception_diagnostics_and_teardown(self):
        engine = importlib.import_module('agent_engine')
        exc = PermissionError(13, 'RAW_SECRET_MUST_NOT_ESCAPE')
        exc.winerror = 5
        from cron import scheduler
        signature = inspect.signature(scheduler._run_agent_with_watchdog)
        with patch.object(scheduler, '_run_agent_with_watchdog', side_effect=exc) as watchdog:
            watchdog.__signature__ = signature
            result, _ = self._real_run()
        self.assertFalse(result[0])
        self.assertNotIn('RAW_SECRET', str(result))
        details = json.loads(result[3])
        self.assertEqual(details['errno'], 13)
        self.assertEqual(details['winerror'], 5)
        self.assertEqual(details['exception_class'], 'PermissionError')

    def test_missing_helper_fails_with_actionable_compatibility_code(self):
        engine = importlib.import_module('agent_engine')
        with patch('cron.scheduler._open_cron_session_db', None):
            result = engine.run(dict(id='fixture', prompt='Read fixture'))
        self.assertFalse(result[0])
        self.assertIn('AGENT_ENGINE_INCOMPATIBLE', result[3])
        self.assertIn('update', result[3])

    def test_missing_deferred_cleanup_helper_is_compatibility_failure(self):
        engine = importlib.import_module('agent_engine')
        with patch('cron.scheduler_detached_worker.defer_teardown_to_running_worker', None):
            result = engine.run(dict(id='fixture', prompt='Read fixture'))
        self.assertIn('AGENT_ENGINE_INCOMPATIBLE', result[3])

    def test_engine_does_not_repeat_parent_mcp_or_write_native_preflight_markers(self):
        from cron import scheduler
        signature = inspect.signature(scheduler._init_cron_mcp_tools)
        with patch('cron.scheduler._resolve_cron_agent_setup', side_effect=AssertionError('wholesale setup forbidden')), \
             patch('cron.scheduler._init_cron_mcp_tools', side_effect=AssertionError('duplicate MCP forbidden')) as initialize, \
             patch('cron.jobs.mark_preflight_alerted', side_effect=AssertionError('marker write forbidden')), \
             patch('cron.jobs.clear_preflight_alerted', side_effect=AssertionError('marker clear forbidden')):
            initialize.__signature__ = signature
            result, _ = self._real_run(preflight=True)
        self.assertTrue(result[0], result)
        initialize.assert_not_called()

    def test_requested_mcp_initialization_runs_once_even_without_preflight(self):
        engine = importlib.import_module('agent_engine')
        from cron import scheduler
        from gateway.session_context import get_session_env
        native_init = scheduler._init_cron_mcp_tools
        def initialize(job_id):
            self.assertEqual(get_session_env('HERMES_CRON_SESSION'), '1')
            return native_init(job_id)
        with patch.object(scheduler, '_init_cron_mcp_tools', wraps=initialize) as discovery:
            discovery.__signature__ = inspect.signature(native_init)
            result, _ = self._real_run(runner=lambda job, **kwargs: engine.run(job, initialize_mcp=True, **kwargs))
        self.assertTrue(result[0], result)
        discovery.assert_called_once_with('synthetic-engine')
        self.assertNotEqual(get_session_env('HERMES_CRON_SESSION'), '1')

    def test_native_preflight_refusal_skips_requested_mcp_initialization(self):
        engine = importlib.import_module('agent_engine')
        from cron import scheduler
        signature = inspect.signature(scheduler._preflight_job_config)
        init_signature = inspect.signature(scheduler._init_cron_mcp_tools)
        with patch.object(scheduler, '_preflight_job_config', return_value='synthetic invalid configuration') as preflight, \
             patch.object(scheduler, '_init_cron_mcp_tools', wraps=scheduler._init_cron_mcp_tools) as initialize:
            preflight.__signature__ = signature
            initialize.__signature__ = init_signature
            result, requests = self._real_run(preflight=True, expect_agent=False,
                runner=lambda job, **kwargs: engine.run(job, initialize_mcp=True, **kwargs))
        self.assertFalse(result[0], result)
        self.assertIn('AGENT_ENGINE_BLOCKED_CONFIG', result[3])
        self.assertEqual(requests, [])
        initialize.assert_not_called()

    def test_mcp_helper_signature_is_checked_before_any_preparation(self):
        engine = importlib.import_module('agent_engine')
        from cron import scheduler
        def changed(*, job_id):
            self.fail('incompatible discovery must never execute')
        signature = inspect.signature(scheduler._prepare_job_prompt)
        with patch.object(scheduler, '_init_cron_mcp_tools', changed), \
             patch.object(scheduler, '_prepare_job_prompt', wraps=scheduler._prepare_job_prompt) as prepare:
            prepare.__signature__ = signature
            result = engine.run(dict(id='fixture', prompt='Read fixture'), initialize_mcp=True)
        self.assertIn('AGENT_ENGINE_INCOMPATIBLE', result[3])
        prepare.assert_not_called()

    def test_incompatible_parameter_kinds_fail_before_any_gate(self):
        engine = importlib.import_module('agent_engine')
        def changed(*, job, job_id, job_name, extra_prompt, cancel_event):
            self.fail('incompatible helper must never execute')
        with patch('cron.scheduler._prepare_job_prompt', changed):
            result = engine.run(dict(id='fixture', prompt='Read fixture'))
        self.assertIn('AGENT_ENGINE_INCOMPATIBLE', result[3])

    def test_pre_cancelled_run_does_not_prepare_or_construct(self):
        engine = importlib.import_module('agent_engine')
        cancel = threading.Event()
        cancel.set()
        from cron import scheduler
        signature = inspect.signature(scheduler._prepare_job_prompt)
        with patch.object(scheduler, '_prepare_job_prompt', wraps=scheduler._prepare_job_prompt) as prepare:
            prepare.__signature__ = signature
            result = engine.run(dict(id='fixture', prompt='Read fixture'), cancel_event=cancel)
        self.assertFalse(result[0])
        prepare.assert_not_called()
        self.assertIn('InterruptedError', result[3])

    def test_registered_compute_only_restriction_is_not_bypassed(self):
        # Simulate the existing native registration guard, never a fake owner.
        with patch('agent.runtime_session_store.is_worker_process', return_value=True):
            result, _ = self._real_run(expect_agent=False)
        self.assertFalse(result[0])
        self.assertIn('AGENT_ENGINE_WORKER_OWNER_REQUIRED', result[3])

    def test_live_future_defers_teardown_until_worker_completion(self):
        from concurrent.futures import Future
        from cron import scheduler
        future = Future()
        signature = inspect.signature(scheduler._run_agent_with_watchdog)
        def timed_out(*args, **kwargs):
            kwargs['worker_state']['future'] = future
            raise TimeoutError('synthetic still-running worker')
        def complete(teardown):
            teardown.assert_not_called()
            future.set_result({})
        with patch.object(scheduler, '_run_agent_with_watchdog', side_effect=timed_out) as watchdog:
            watchdog.__signature__ = signature
            result, _ = self._real_run(after_run=complete)
        self.assertFalse(result[0])
        self.assertIn('TimeoutError', result[3])


if __name__ == '__main__':
    unittest.main()
