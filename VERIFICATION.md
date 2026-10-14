# Verification handoff

This is a source distribution, not a deployment into the working live profile.

## Executed

- Full final parent offline suite: **32 tests, 187.352 seconds, OK**, exit 0.
  Command: `KANBAN_TEST_WORK="SHORT_PROJECT_SOURCE_TEST_WORK" python source/run_tests.py`.
  The original 30-test baseline log is retained privately in
  `source/verification-work/full-suite.log` (Git-excluded); final checks are also
  recorded in the maintenance session's actual tool output.
- Native real script success and nonzero failure used
  `tools.cronjob_tools.execute_job_for_event` through generated native wrappers,
  the contained worker, and the original native script engine. Readback checked
  native completed/failed execution ledger, cleared fire claims, native output,
  preserved future schedule/next occurrence, terminal card and next Scheduled card.
- Generic Agent boundary fixtures verified unchanged original policies, respected
  subsequent prompt/model/tool edits, no injected guidance by default and no
  required artifact. These runners were injected offline fixtures, **not LLM calls**.
- Real Windows owned-process fixtures checked Blocked/deletion cancellation,
  child termination, controller crash containment, recovery without replay,
  native dependency/history fences, attachment-read cancellation and one-next-wait.
- Fresh-home native CLI enable/discovery verified the operating Skill resolver,
  `kanban-jobs list/status` returning `{}`, and no jobs/card database creation.
- Native `hermes plugins validate source/plugin --json`: `ok=true`, security safe,
  no core override, no warnings. Native `hermes plugins doctor source/plugin --ci`:
  PASS, **1 tool / 1 hook**. Both commands used an isolated verification home.
- `python source/verify_install.py --home NEW_SHORT_DISPOSABLE_HOME` ran the actual
  native local-payload validation/doctor path successfully, exit 0.
- RED→GREEN observed for missing adapter, explicit unregister, native manifest,
  real native wrapper execution, metadata re-registration, native fire fence /
  rollback, post-registration policy edits, absolute-root and parent-only-plan
  validation, and the Windows verifier shell-argument guard.
- Independent review reproduced empty-response false success. Empty and whitespace
  Agent responses now fail before card completion; genuine nonempty silence and
  script results still succeed. RED→GREEN and real contained supervision regressions
  passed. No fabricated success response is substituted.
- Independent review also identified inner native retry/quota hints missing across
  the inherited script boundary. Allowlisted hints are retained in private results
  and summaries, and flagged failures explicitly disclose the unavailable outer
  scheduler passthrough. Tests prove no invented retry or scheduling mutation.

Tested host: Windows, Python 3.14.7, Hermes v0.21.7,
source revision `71d34bad1045610252eda02ee79549436896e784`.
No dependency environment was modified with pip or a custom installer.

## Resolved verification issues

Deep Windows disposable-home paths caused native PM setup to fail before the
plugin executed. A short, explicitly owned project `source/test-work` path and
native launcher preparation resolved this. The native execution record retains
`fire_claim: null` after clearing; assertions check null, not absence of the key.
Workspace-wide validation initially traversed disposable dependency trees;
validation now targets the exact installable `source/plugin` payload. Tests and
fixtures are outside that installation payload.

## Publication checks and boundaries

- No live-profile installation, business run or account/import verification is
  part of source publication. Only the explicit README allowlist is published.
- Verify the published immutable Git ref in a disposable home using:
  `python source/verify_install.py --home NEW_SHORT_DISPOSABLE_HOME --repository Gary-Y-Lee/Hermes_Plugin_00.Kanban_Job_Manager/source/plugin --ref EXACT_40_CHARACTER_COMMIT_SHA`.
  This exercises native private-Git installation, enablement, CLI discovery,
  zero-job status, validation and Doctor. Same-host isolation is not a fresh OS.
- Execution is Windows-only. The inherited controller ceiling is 3500 seconds;
  the native outer script deadline can be shorter. No global timeout is widened.
- Monitor-mode jobs require a separate connector. PLAUD-specific imports require
  their separately installed engine/connector; neither their job nor engine is shipped.
- Native private transaction/process helpers are version-sensitive. Future-slot
  schedule preservation was exercised, not every crossed-slot/overrun combination.
- Native Agent unreachable-model retry and quota-hold parity is not available
  across the outer native script result contract. This also exists in the prior
  operating adapter. Hints are diagnostics only; next normal schedule/manual retry
  applies. No Core patch, parallel scheduler or hand-edited native store is added.
- Registration spans native stores and filesystem, not a distributed transaction;
  a crash during initial registration may require inspecting the retained local
  original snapshot. Completed repeat registration and caught native-update
  failure rollback are verified; every crash boundary is not claimed.
- Actual Agent stage-report compliance, fresh OS installation, live auth/business
  execution, Desktop drag/download/preview/folder-click behavior are unverified.
- Disposable test homes and private logs remain outside the publication allowlist;
  do not publish them.
