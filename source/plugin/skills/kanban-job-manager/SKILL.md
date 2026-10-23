---
name: kanban-job-manager
description: Use when explicitly registering or operating native jobs on Kanban.
---
# Kanban Job Manager

Installation registers no jobs and starts no work. Never register all jobs implicitly.
Inspect the selected existing native cron job and confirm registration authority first.

- `hermes kanban-jobs list` / `status`: registered local adapter state, not prompts.
- `hermes kanban-jobs register --job JOB_ID --board BOARD_SLUG`: preserve schedule,
  prompt, skills, model, tools, workdir and delivery. Only script/no_agent/interpreter
  become the local native adapter. Resolve existing native jobs with `hermes cron list`.
- Optional `--output-root ABSOLUTE_DIRECTORY` limits explicit attachments. Optional
  `--stages-json LOCAL_JSON_FILE` supplies a list of `{name, children}` stage objects.
  Stage reports are presentation, not additional work or completion requirements.
- Start only the current `[실행 대기]` Scheduled card by moving it to Ready.
  The Scheduled card shows the next cron time for display only; it has no timer itself and cron keeps scheduling.
- `hermes kanban-jobs dispatch --board BOARD_SLUG` reconciles registered jobs on that
  board; it can start every explicitly Ready registration there. Do not use it as
  a read-only status command. Normally the native dispatcher calls the hook.
- Move Running to Blocked or delete it to request cancellation. Await process exit
  and one next Scheduled card. Cancellation does not roll back completed writes;
  interrupted external writes may require reconciliation. Preserve history.
- Results use native cron output and native Kanban history/attachments. No file is
  required for a legitimate successful no-artifact execution. Only explicitly
  reported current-run files within the configured root are attached.
- `hermes kanban-jobs unregister --job JOB_ID`: only while idle. Restore original
  adapter fields; retain subsequent prompt/schedule edits and history. Unregister
  before disabling/uninstalling the plugin; do not delete its local state first.

Requires Windows native process containment in this release. Monitor-mode jobs
require a separate native connector. No custom scheduler, UI or auth policy.
Agent originals call the sibling plugin-local `agent_engine.run` in the owned
worker, passing the existing gate token as local `execution_id` and optional
reporting guidance. Its success/output/final-response/error tuple retains the
worker's empty-response/failure-marker checks and existing artifact/claim fences.
This engine requires neither a Core patch nor Gateway startup and never sends
the registered script wrapper back through canonical cron Agent admission.
Original `no_agent` scripts still use native `run_job`; the outer native fire
still owns delivery/bookkeeping. Native scheduling/dispatch runtime requirements
are separate from Agent engine execution; preserve existing approval settings.
Check runtime-helper compatibility after future Hermes Updates. Core-free does
not mean guaranteed future or whole-product compatibility; offline routing tests
do not prove fresh live execution. Do not start a Gateway as an Agent-engine fix.
A separately installed PLAUD engine/connector remains separately managed; this
package contains no importer job, account data or import engine.
Never infer actual Agent child stages from output prose or elapsed time.
