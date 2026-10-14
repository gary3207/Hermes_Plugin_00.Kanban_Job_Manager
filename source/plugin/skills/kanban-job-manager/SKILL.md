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
  Scheduled has no date/timer and stays inert; existing cron scheduling continues.
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
A separately installed PLAUD engine/connector remains separately managed; this
package contains no importer job, account data or import engine.
Never infer actual Agent child stages from output prose or elapsed time.
