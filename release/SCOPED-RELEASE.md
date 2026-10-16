# Scoped release acceptance contract

Source-linked supplement to `SCOPED_RELEASE_STATUS.md`, not another task ledger.
Canonical cards remain on the existing `always-on-harness` board.

## Frozen owner decision

Accept the complete PR11 + PR12 candidate on standard free Linux/macOS runners.
No upstream pull/merge/rebase, dependency/version bump, unrelated Windows repair,
approval bypass, production restart, paid runner or business action is authorized
by this acceptance workflow. Shared security/product regressions remain blocking;
the broad CI aggregate stays visible and unchanged, never relabelled green.

The machine-readable contract is `release/scoped-acceptance.json`. Its source
feature head is an immutable ancestor; the candidate SHA and tree SHA are derived
at execution time (a committed manifest cannot contain its own future commit SHA).
The frozen baseline is `db44c46208f1b421b4ecb7e56bf9d68b0423906f`.

## Release lineage, not default-main acceptance

PR12 is based on PR11; PR11 targets `compat/scoped-approval-db44c46208`.
That compatibility ref is the maintained scoped release target. Fork default
main snapshot `592ae879fc557729192e238e2ac385492ae2c470` diverges and must not be
reset or silently treated as containing this release. Any accepted deployment must
name the exact reviewed compatibility-tree SHA, integration commit and prior
runtime revision. PR12-only incremental diff acceptance is insufficient.

## Executed coverage and fail-closed evidence

- Two Linux slices run complete **canonical default discovery**, including shared
  security/routing/recovery/notifications. Existing canonical exclusions of slow
  integration/E2E/installer lanes remain; this is not a claim of full E2E coverage.
- Linux and macOS separately run the explicit required shared contract paths,
  including human-approved BOM/CRLF preservation, preimage/atomicity, adapter
  authorization, profile isolation, native DB fencing and terminal retirement.
- macOS native lane uses the existing OS-marker enumerator and canonical runner
  with `platforms and not integration`; no fake platform flags or Bash4 features.
- All lanes disable file retries, retain real output/selection/source receipts,
  require a complete nonempty passing summary plus exit zero and clean tracked
  source before/after. Aggregate downloads each artifact and verifies SHA/tree,
  manifest/log hashes and all five nonempty results. Missing/failed lane blocks.

Existing broad Linux run `38922912950` at feature head `34ad3512d9a` failed two
`test_gateway_connection_reconnect.py` transient-resume tests. The unchanged file
passed locally (23 tests), so neither a fix nor baseline attribution is asserted.
The new explicit shared lane includes that file; hosted failures remain blockers.
The old run also reported an LSP process-cleanup flake. No existing tests were
changed to hide these results.

## Current scoped failure diagnosis (2026-10-16)

Run `38939833522` tested full candidate `799016be6960ee9ec4145c4fac763a08436b2f14`
on standard `ubuntu-latest` and `macos-latest` runners; macOS shared passed,
Linux shared failed the failed-redial recovery test, and macOS native failed
`test_cancelled_exec_command_kills_shell_and_child`. Neither aggregate is accepted.
Linux slices were still executing when the targeted recovery fix was prepared.

The Linux failure is now reproduced deterministically: a failed dial publishes
its error/readiness before its thread necessarily exits; an immediate next call
sees that still-alive thread and rethrows the stale error instead of redialing.
The repair waits within the existing caller deadline for that already-failed
thread before deciding to redial. It does not reconnect healthy work or replay
admitted requests. A held-thread-exit regression failed before the repair;
canonical reconnect/backoff/recovery suites then passed 35 tests with no file
retries. Independent review `deleg_e22d4836` approved the focused diff. These are
local source proofs, not hosted or live acceptance. Evidence retained outside
source: `/home/ubuntu/.hermes/plans/scoped-reconnect-red-20261016.log` and
`scoped-reconnect-green-20261016.log`.

macOS cancellation cleanup remains a supported-platform blocker, not a Windows
exclusion. Exact job `117104389795` passed the timeout-tree test but timed out
waiting for both shell and child after cancellation. Read-only review
`deleg_82b6482d` suggested suspend/kill ordering as a hypothesis; this is not a
verified root cause or an authorized reason to weaken process identity fencing.
The relevant production and test files are unchanged from the frozen compatibility
baseline. Next investigation must distinguish a cleanup defect from native-host
PID observation/race behavior with real macOS process-state evidence, retaining
both assertions. Logs: `/home/ubuntu/.hermes/plans/scoped-799016-macos-native.log`
and `scoped-799016-linux-shared.log`. No new broad workflow dispatch was requested.

## Exact-source follow-up: Python 3.14 callback completion (2026-10-16)

Run `38943203945` at `9113b721d5f99a262a1bb820580e4ab0c0ff2f1c`
completed **failure**. Linux shared and both macOS lanes passed, including the
previously failing macOS cancellation test; this does not erase its earlier
failure or establish a product repair. Linux-1 failed session-read diff RPC,
worktree self-heal fixture Git commit exit128, and Telegram admission cancellation.
Linux-2 failed the inherited models.dev credential-alias expectation. None are
waived, excluded, or retried into acceptance; all five lanes remain mandatory.
Artifacts and exact-source receipts are retained under
`/home/ubuntu/.hermes/cache/four-gate-38943203945/`.

The Telegram failure reproduced with the frozen PM-built Python 3.14.7 and PTB
22.8: one selected test failed at the unchanged no-inflight-claims assertion.
PTB removes finished tasks from its tracked set in its own done callback; its
`stop()` may return before admission's subsequently queued cleanup callback runs.
The test now observes an event registered after that cleanup callback before
asserting settlement. It still requires no effects, no inflight or seen claim,
a closed callback coroutine, and successful single delivery on retry, and now
also requires the scheduled task to be cancelled. No sleep, product-code change,
forced claim clearing, or timeout relaxation was used. A missing cleanup still
fails the original state assertions after the event.

Canonical no-retry execution of the full admission file on that environment:
94 passed, zero failed. Independent read-only review `deleg_b1f59418` approved
this synchronization correction and independently replayed the 94 tests. The two
other Linux-1 files replayed on the same interpreter: nine passed, zero failed.
Their hosted failures remain unresolved: Git stderr was not exposed, and the
RPC deadline failure has no proven cause. These targeted local results do not
constitute full-tree hosted acceptance or clear Linux-2's catalog mismatch.

The existing board graph remains blocked/todo, with no running worker on the
selected board. A controller progress-comment command hit a pending security
approval on its combined readback pipeline and was not retried through another
path. No board mutation is claimed. The controller remains enabled, and source
integration, deployment, notification crash-window proof, and business resumption
are still gated. No upstream refresh, runtime activation, or cleanup deletion.

## Still outstanding

Hosted exact-source receipts and independent review of results; reviewed source
integration; rollback-capable tracked safe-boundary deployment; independent live
crash/recovery/origin-notification/healthy-worker/context/routing/approval proof;
reconciliation and eligible existing business-card resumption. The outstanding
send/wake crash-window findings in `SCOPED_RELEASE_STATUS.md` are not waived.

CLI board comment during this controller was refused with
`delegate_task child contexts cannot mutate Kanban tasks via the CLI`.
No direct DB write, ownership override, respawn or duplicate graph was attempted.
No gateway restart or production source activation occurred. Existing dirty,
runtime, evidence and nonterminal worktrees remain retained; no deletions.
