# Hermes Kanban Job Manager

## 새 환경에서 Agent에게 요청하기

> 비공개 저장소 `Gary-Y-Lee/Hermes_Plugin_00.Kanban_Job_Manager`의 README에 따라
> 현재 Profile에 Kanban Job Manager를 설치해줘. 현재 Job 정의는 이 저장소에 없다.
> 기존 Job·카드·인증 설정은 보존하고, 설치만으로 Job을 등록하거나 실행하지 마.
> 설치 후에는 연결 가능한 기존 Job을 보여주고, 내가 지정한 Job만 연결해줘.

비공개 저장소 접근을 위한 GitHub 인증과 Hermes 설치가 필요합니다. 아래
`EXACT_40_CHARACTER_COMMIT_SHA`는 저장소의 실제 커밋으로 대체합니다. 설치 후
카드 관리는 기존 Kanban 화면을 사용합니다. PLAUD 업무 엔진·전용 연결은
별도 설치 대상이며, 이 저장소는 현재 PLAUD Job을 복원하지 않습니다.

A **job-free operating plugin**: native Scheduled waiting cards, manual Ready
starts, existing cron scheduling, native run history/results/attachments, and
contained cancellation. Reuses the maintained scheduled adapter/controller;
no replacement scheduler, UI, importer, business policy or dependency installer.

The installable directory is **`source/plugin`**. The rest of `source/` contains
only synthetic offline tests and verification tools, not installation payload.
No current job definitions, prompts, schedules, cards, history, credentials,
account data or machine-specific output paths are included.

## Install after publication

Requires an existing Hermes installation with native cron/Kanban and plugin
CLI/Skill registration. This release executes jobs only on **Windows**; other
platforms may load its guidance but registration fails closed. The tested runtime
is the local October 2026 Hermes source installation. Native private APIs used
for attachment transactions and process containment require compatibility checks
on future Hermes versions; no broad version support is claimed.

```bash
hermes plugins install Gary-Y-Lee/Hermes_Plugin_00.Kanban_Job_Manager/source/plugin --ref EXACT_40_CHARACTER_COMMIT_SHA --no-enable --yes-deps
hermes plugins enable hermes-kanban-job-manager --no-allow-tool-override
hermes kanban-jobs status
```

Installation/enablement creates **zero jobs, cards, registrations or timers**,
even if that home already contains unrelated jobs. No current job is restored.
Use native `hermes cron` to create your own job first, or select an existing one.

## Explicitly connect one existing job

```bash
hermes cron list
hermes kanban-jobs register --job JOB_ID --board default
hermes kanban-jobs list
hermes kanban-jobs status
```

Only the selected job's `script`, `no_agent` and `interpreter` are adapted.
The complete original descriptor stays in **local private plugin-data**; its
original engine runs in a contained child. The original script/interpreter are
used by native cron, and scripts must remain inside this home's native
`scripts/` directory. Prompt/model/provider/toolsets/skills/workdir/delivery and
schedule are preserved. Later native edits to non-adapter fields are respected.
Registration does not pause jobs or alter their next occurrence. Completed
repeat registration is idempotent; changed board/output/stage metadata is refused
rather than silently applied. Unregister/re-register explicitly to change it.

Move the current **`[실행 대기]` Scheduled card → Ready** to request a manual fire.
The existing cron scheduler still supplies automatic fires. Both use the same
job controller lock and native firing/bookkeeping. Scheduled itself has no timer.
The normal native dispatcher invokes the hook. `hermes kanban-jobs dispatch
--board default` is a mutating reconciliation/start command, not a status query:
it may start every registered Ready job on that board.

Running → Blocked or deleting the active card requests cancellation. The owned
process tree must terminate before one replacement Scheduled card is created.
History cards are not runnable; only the next waiting card represents a new run.
Cancellation preserves completed outputs/external writes, and an interrupted
external write can have an unknown outcome. Hard deletion loses native card
history; an independent local run summary remains.

```bash
hermes kanban-jobs unregister --job JOB_ID
```

Unregister only while idle. It restores only original adapter fields, archives
the waiting card and retains native history/local summaries and newer native
prompt/schedule edits. Unregister **before disabling/uninstalling** this plugin;
otherwise a scheduled local wrapper would have no implementation to execute.
Conflicting wrapper or adapter edits fail closed; inspect them rather than force
an overwrite. Registration spans filesystem/native stores, not a distributed
transaction: a process crash during initial registration may require inspection
of the retained local original snapshot; do not delete it or blindly overwrite.

## Results and optional presentation

### Native Agent retry boundary

The original model/tool/approval configuration is preserved, but this is a
script-isolation adapter, **not a transparent replacement for every native Agent
scheduler feature**. The original worker can identify a never-reached-model
failure or provider quota hold; the outer native script result contract does not
carry those transient flags into its Agent retry/hold bookkeeping. This boundary
also exists in the current operating adapter. Such failures remain failed cards;
their retry hints are retained as diagnostics, not silently translated into new
schedules. The next normal scheduled occurrence or an explicit manual retry still
applies. Native retry/hold parity is not claimed, and this package does not patch
Hermes Core or add a second scheduler to approximate it.

Native cron still saves/delivers its result and records its execution ledger.
The card Description contains timing, a bounded redacted native result and log
location; Result and run Summary are short, nonduplicated handoffs. Full native
output is kept in the private run directory. A legitimate no-file success stays
successful. Reported artifacts receive root/current-run/reparse/integrity checks
and native attachments under one ownership fence; unreported files are not
invented or automatically attached. Empty/stale/out-of-root files are refused.

`register --output-root ABSOLUTE_DIRECTORY --stages-json LOCAL_JSON_FILE` can
provide a list of `{ "name": "Stage", "children": ["Operation"] } objects.
By default the measured wrapper stages are Preparation / Original job /
Publication. Explicit custom Agent stages are optional reporting guidance, not
extra business work or completion gates. `kanban_job_progress` or the native
`kanban-jobs report` CLI accepts real numbered phase/subphase transitions and
current-run artifact paths. Missing child reports display unreported, never
inferred work/timing. Actual Agent compliance is **not** proven by offline tests.

Monitor-mode native jobs are deliberately refused (their outer native mode is
incompatible with this isolation wrapper); add a separately tested connector
when needed. A separately installed **PLAUD engine and PLAUD Kanban connector**
remain external prerequisites for PLAUD-specific manual imports. This package
neither includes that importer/job nor changes its registration/state.

## Offline verification

```bash
python source/run_tests.py
python source/verify_install.py --home NEW_SHORT_DISPOSABLE_HOME
```

Use an already installed Hermes launcher on PATH; the runner discovers its
runtime/dependency facts instead of installing dependencies. On Windows choose
`KANBAN_TEST_WORK` as an explicitly owned **short project source/test-work path**
if the checkout is deep. Native fresh-home bootstrap can otherwise exceed the
Windows path ceiling before the plugin runs. Tests and local verifier never
copy live auth/config; fresh native PM setup uses only its supported launcher.
These are same-host fixtures, not fresh-OS, account, live import or Desktop-click
verification. Tests leave disposable homes for inspection; remove only your
verification-owned homes after processes/telemetry have ended.

After parent publication, exercise the immutable private Git path with:

```bash
python source/verify_install.py --home NEW_SHORT_DISPOSABLE_HOME --repository Gary-Y-Lee/Hermes_Plugin_00.Kanban_Job_Manager/source/plugin --ref EXACT_40_CHARACTER_COMMIT_SHA
```

The verifier uses native install/enable/validate/doctor and command-scoped
`gh auth git-credential`, without printing tokens or modifying Git config.
The remote path is intentionally not executed before publication authorization.

## Publication allowlist

Publish only `.gitignore`, `.gitattributes`, `README.md`, `VERIFICATION.md`,
`source/plugin/__init__.py`, `source/plugin/job_manager.py`,
`source/plugin/controller_utils.py`, `source/plugin/plugin.yaml`,
`source/plugin/skills/kanban-job-manager/SKILL.md`, `source/run_tests.py`,
`source/test_job_manager.py`, `source/test_controller.py`, `source/fixture.py`,
and `source/verify_install.py`. Never add disposable homes, caches, output,
local registration snapshots, auth/config, or nested worktrees. Install only
`source/plugin`, not the workspace or test tree.
