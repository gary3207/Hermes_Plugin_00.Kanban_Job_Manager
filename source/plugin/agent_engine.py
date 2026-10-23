"""Direct, tool-enabled local Agent engine for an already-contained plugin worker.

Not canonical cron admission: the outer no_agent scheduler still owns claims,
ledger, retries/quota holds and delivery. No owner impersonation or persistence
suppression: registered compute-only interpreters remain subject to Core guards.
Private cron helpers are a deliberately narrow, signature-checked adapter.
"""
from copy import deepcopy
import inspect
import json
from types import SimpleNamespace
import uuid


# Expected parameter order is the reviewed native contract, not a Core patch.
_CONTRACT = {
    '_prepare_job_prompt': 'job job_id job_name extra_prompt cancel_event',
    '_CronRunScope': 'job job_id execution_id',
    '_reload_dotenv_and_publish_delivery_target': 'job',
    '_load_cron_job_config': 'job job_id job_name',
    '_guard_job_credential_exfil': 'job',
    '_cron_preflight_enabled': 'cfg',
    '_preflight_job_config': 'job cfg',
    '_init_cron_mcp_tools': 'job_id',
    '_empty_requested_mcp_toolsets': 'job cfg',
    '_resolve_job_runtime': 'job job_id jc',
    '_resolve_job_reasoning_config': 'job cfg model',
    '_load_prefill_messages': 'cfg job_id',
    '_job_fallback_chain': 'job cfg',
    '_load_credential_pool': 'runtime job_id',
    '_open_cron_session_db': 'job',
    '_construct_cron_agent': 'AIAgent job _cfg setup * workdir session_id session_db',
    '_run_agent_with_watchdog': 'agent prompt job job_id job_name task_id cancel_event worker_state',
    '_final_response_from_result': 'result job_id job_name AIAgent',
    '_finalize_cron_session': 'session_db agent job_id job_name cron_session_id workdir',
    '_teardown_cron_agent': 'agent job_id * timeout_seconds',
    '_run_doc_header': 'job title job_id prompt * prompt_stamp',
    '_normalize_newlines': 'text',
    '_is_cron_silence_response': 'text',
    '_cron_failure_marker_error': 'text',
}


def _native():
    from cron import scheduler
    from cron.scheduler_detached_worker import defer_teardown_to_running_worker
    helpers = {}
    for name, expected in _CONTRACT.items():
        fn = getattr(scheduler, name, None)
        try:
            if not callable(fn):
                raise ValueError(name)
            fields = expected.split()
            keyword_start = fields.index('*') if '*' in fields else len(fields)
            names = [field for field in fields if field != '*']
            parameters = list(inspect.signature(fn).parameters.values())
            if [p.name for p in parameters] != names:
                raise ValueError(name)
            for index, parameter in enumerate(parameters):
                kind = inspect.Parameter.KEYWORD_ONLY if index >= keyword_start else inspect.Parameter.POSITIONAL_OR_KEYWORD
                if parameter.kind != kind:
                    raise ValueError(name)
            if name == '_teardown_cron_agent' and parameters[-1].default is inspect.Parameter.empty:
                raise ValueError(name)
        except (ValueError, TypeError):
            raise RuntimeError(f'AGENT_ENGINE_INCOMPATIBLE: {name}; update the plugin adapter for installed Hermes') from None
        helpers[name] = fn
    try:
        valid_defer = callable(defer_teardown_to_running_worker) and list(inspect.signature(
            defer_teardown_to_running_worker).parameters) == [
            'future', 'session_db', 'agent', 'job_id', 'job_name', 'cron_session_id', 'workdir']
    except (TypeError, ValueError):
        valid_defer = False
    if not valid_defer:
        raise RuntimeError('AGENT_ENGINE_INCOMPATIBLE: deferred teardown; update the plugin adapter')
    helpers['defer'] = defer_teardown_to_running_worker
    for name in ('_PROMPT_FRAME', '_RESPONSE_FRAME', '_RESPONSE_HEADING', '_RESPONSE_TERMINATOR'):
        value = getattr(scheduler, name, None)
        if not isinstance(value, str):
            raise RuntimeError(f'AGENT_ENGINE_INCOMPATIBLE: {name}; update the plugin adapter')
        helpers[name] = value
    return SimpleNamespace(**helpers)


def _error(exc):
    # Never stringify arbitrary exception messages, URLs, credentials or payloads.
    allowed = {'RuntimeError', 'TimeoutError', 'InterruptedError', 'OSError',
               'PermissionError', 'FileNotFoundError', 'ConnectionError',
               'ConnectionRefusedError', 'ValueError', 'TypeError', 'WorkerPersistenceError'}
    details: dict[str, object] = {'code': 'AGENT_ENGINE_FAILED',
               'exception_class': type(exc).__name__ if type(exc).__name__ in allowed else 'OtherException'}
    for key in ('errno', 'winerror'):
        value = getattr(exc, key, None)
        if type(value) is int and -1000000 <= value <= 1000000:
            details[key] = value
    if isinstance(exc, RuntimeError) and exc.args and isinstance(exc.args[0], str):
        message = exc.args[0]
        if message.startswith('AGENT_ENGINE_INCOMPATIBLE:'):
            details['code'] = 'AGENT_ENGINE_INCOMPATIBLE'
            # Only adapter-generated compatibility text is exposed by the caller.
        elif message == 'worker_cron_registration_required':
            details['code'] = 'AGENT_ENGINE_WORKER_OWNER_REQUIRED'
    return json.dumps(details, sort_keys=True)


def run(job, *, extra_prompt=None, execution_id=None, cancel_event=None, initialize_mcp=False):
    """Return (success, output, final_response, error), without scheduler dispatch.

    Native pre-Agent refusals may return early. Empty output remains empty (not
    invented success text). Retry/quota scheduling is intentionally not emulated.
    Already-prepared workers leave initialize_mcp=False to avoid rediscovery.
    """
    agent = db = scope = native = None
    worker_state = {}
    session_id = 'cron_plugin_' + uuid.uuid4().hex
    job_id = job_name = ''
    try:
        native = _native()
        job = deepcopy(job)
        job_id = job['id']
        job_name = str(job.get('name') or job_id)
        if execution_id is not None:
            job['execution_id'] = execution_id
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('cancelled')
        early, prompt = native._prepare_job_prompt(job, job_id, job_name, extra_prompt, cancel_event)
        if early is not None:
            return early
        from run_agent import AIAgent
        scope = native._CronRunScope(job, job_id, execution_id)
        scope.enter()
        native._reload_dotenv_and_publish_delivery_target(job)
        config = native._load_cron_job_config(job, job_id, job_name)
        native._guard_job_credential_exfil(job)
        preflight = native._cron_preflight_enabled(config.cfg)
        reason = native._preflight_job_config(job, config.cfg) if preflight else None
        if initialize_mcp and not reason:
            native._init_cron_mcp_tools(job_id)
        if preflight:
            reason = reason or native._empty_requested_mcp_toolsets(job, config.cfg)
            if reason:
                # Outer scheduler owns alert/hold state; do not mutate its stored wrapper.
                error = 'AGENT_ENGINE_BLOCKED_CONFIG: native preflight refused this run; check hermes cron doctor'
                return False, error, '', error
        from hermes_cli.config import resolve_turn_limit
        runtime, model = native._resolve_job_runtime(job, job_id, config)
        turns = (config.cfg.get('agent') or {}).get('max_turns')
        setup = SimpleNamespace(model=model, runtime=runtime,
            max_iterations=resolve_turn_limit(config.cfg.get('max_turns') if turns is None else turns),
            reasoning_config=native._resolve_job_reasoning_config(job, config.cfg, str(model)),
            prefill_messages=native._load_prefill_messages(config.cfg, job_id),
            fallback_model=native._job_fallback_chain(job, config.cfg),
            credential_pool=native._load_credential_pool(runtime, job_id),
            fallback_notice=runtime.pop('_fallback_notice', None))
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('cancelled')
        db = native._open_cron_session_db(job)
        if db is None:
            raise RuntimeError('durable session store unavailable')
        agent = native._construct_cron_agent(AIAgent, job, config.cfg, setup,
            workdir=scope.workdir, session_id=session_id, session_db=db)
        result = native._run_agent_with_watchdog(agent, prompt, job, job_id, job_name,
            scope.task_id, cancel_event, worker_state=worker_state)
        response = native._final_response_from_result(result, job_id, job_name, AIAgent)
        if (setup.fallback_notice and response.strip() and not native._is_cron_silence_response(response)
                and native._cron_failure_marker_error(response) is None):
            response = f'{setup.fallback_notice}\n\n{response}'
        framed_prompt = native._normalize_newlines(prompt)
        logged = native._normalize_newlines(response or '(No response generated)')
        output = native._run_doc_header(job, job_name, job_id, framed_prompt,
            prompt_stamp=f'{native._PROMPT_FRAME}{len(framed_prompt)}\n')
        output += f'{native._RESPONSE_FRAME}{len(logged)}\n{native._RESPONSE_HEADING}{logged}{native._RESPONSE_TERMINATOR}'
        return True, output, response, None
    except Exception as exc:
        error = _error(exc)
        if json.loads(error)['code'] == 'AGENT_ENGINE_INCOMPATIBLE':
            error += '; update agent_engine.py adapter for installed Hermes helper signatures'
        return False, error, '', error
    finally:
        if scope is not None and native is not None:
            try:
                deferred = native.defer(worker_state.get('future'), db, agent, job_id,
                    job_name, session_id, workdir=scope.workdir)
            finally:
                scope.exit()
            if not deferred:
                try:
                    if db is not None:
                        native._finalize_cron_session(db, agent, job_id, job_name, session_id,
                            workdir=scope.workdir)
                finally:
                    native._teardown_cron_agent(agent, job_id)
