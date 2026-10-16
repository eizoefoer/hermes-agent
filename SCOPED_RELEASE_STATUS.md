# Scoped approval and compatible-runtime release status

This is a source-linked handoff, not a second task ledger. Canonical task truth is
Hermes Kanban board `always-on-harness`; reuse its existing cards.

## Owner decision — 2026-10-16

Freeze the known behavior baseline `db44c46208f1b421b4ecb7e56bf9d68b0423906f`.
Do not pull, merge, rebase or bump versions merely to follow upstream currency.
Integrating the existing scoped-approval feature branch into the signed callback
compatibility branch is integration of the approved scope, not an upstream update.

Release targets are the deployed Linux VM and applicable macOS compatibility.
Do not spend additional repair cycles on unrelated Windows desktop/updater defects
as VM prerequisites. Keep Windows results visible; a Linux skip is not Windows
acceptance. Do not label an unproven failure pre-existing: that requires a baseline
comparison. Preserve shared-code, security, encoding, approval-denial, concurrency,
profile isolation and routing tests, along with relevant hosted Linux/macOS checks.
The current all-platform CI aggregate remains unchanged and must not be reported
green unless it is actually green. A platform-scoped release exception must be
explicitly reviewed and backed by exact-head supported-platform CI receipts;
this document does not manufacture such a receipt or bypass branch protection.

## Source checkpoint

- PR11: `https://github.com/eizoefoer/hermes-agent/pull/11`, feature head
  `18110d2a17112ec11fb7ec1c07cc8ae54bc07fc1`; base
  `compat/scoped-approval-db44c46208`, **not default main**.
- PR12: `https://github.com/eizoefoer/hermes-agent/pull/12`, signed approval
  compatibility stacked on PR11. Its previous head
  `9efa81c98ad9620f79493b63b77188bcd5a6dc47` failed Linux Python shard 2 because
  the workflow selection replay rejected diagnostic `-o faulthandler_timeout=90`.
- Integrate the already-tested feature fix `b22c22f1198` and feature head above;
  preserve the narrow option/value qualification and negative selection tests.
  Do not weaken the replay parser to accept arbitrary `-o` configuration.
- Both PRs require fresh exact-head CI and feedback review before source acceptance.
  Neither a compatibility-branch merge nor local test passes establish default-main
  completion. Preserve divergent fork main; do not overwrite it.

## Manual CI replay — 2026-10-16

PR12's PR-event run `38896999189` failed before jobs with a GitHub internal
error; supported rerun returned HTTP 403 (`This workflow run cannot be retried`).
Manual run `38897089154` checked exact head `843c227afce2`, but its attribution
job `116960111862` had no PR event base and compared against divergent fork main.
Those historical contributor failures are not evidence of newly unmapped PR12
authors. The optional `comparison_base` input now binds manual replay attribution
to an explicitly supplied base commit; PR-event base/head remain authoritative,
and an omitted input preserves the existing `origin/main` comparison. All test
lanes and the aggregate gate remain unchanged. Supply the current PR base SHA
from GitHub, not a guessed branch or a same-head base that excludes new authors.

Real Git-history fixture regression: baseline 1 failed / 2 passed, then 3 passed;
canonical `tests/ci` suite: 37 files, 672 passed, 0 failed, 1 skipped. All 12
`scripts/check --base HEAD` checks passed. Independent read-only diff review found
no actionable findings. This is source qualification, not hosted-CI or deployment
acceptance. Read back the new exact-head hosted run before advancing any gate.

## Notification integration and real-component probe — 2026-10-16

Integrated only the existing PR10 functional notification change
`74336e508ff15be50be8572f4e5cb0e1a3c53828` and locale completion
`2d0b8f2a12106398598d10a512f462700856633b`; no PR10 CI rewrites or new upstream
revision were imported. This source checkpoint combines scoped writes, signed
approval callbacks and notification claim leases. It is NOT release acceptance.

Canonical Kanban-family validation: 107 files, 722 passed, 1 failed, 4 skipped.
The failure is `test_resolve_hermes_argv_module_actually_runs`: real CLI
`--version` exceeds its unchanged 30-second subprocess deadline. An isolated
single-test replay also failed. A separate runpy diagnostic exited zero but its
8-second stack dump was inside `source_releases._head_containing` through
`source_check.check_for_updates`; this does not make the canonical failure green.
Canonical scoped/encoding/signed-ingress/notification focused validation: 8 files,
90 passed, 0 failed (overlaps the family run; do not add as unique tests).
`scripts/check --staged`: all 12 checks passed, zero blocking/advisory findings.

The real isolated systemd recovery canary against routing source
`0096709ab866726d79b2adc33459522bfe550448` first refused missing
`claim_metadata`; notification integration supplies that API. The next probe
persisted the actual remaining refusal:
`dispatch_once() got an unexpected keyword argument 'recovery_policy'`.
Two fresh supervisor processes recorded retry/backoff, and the third respected
the persisted cooldown. No recovery worker was spawned. These are useful failure
receipts, NOT successful crash-recovery or exactly-once delivery evidence.
Retained diagnostic state is under the scoped-feature worktree's
`artifacts/scoped-approval-20261015/notification-integration-canary-state/`.
Production gateway and supervisors were not restarted or repointed.

Next integration must port the existing native recovery contracts from retained
`fix/native-recovery-gaps-t_b6b87d0e` (`cdede5423a3`), including terminal-obligation
retirement, onto the current refactored dispatcher. Do not replace that dispatcher
with the old file, silently discard its newer admission gates, remove
`recovery_policy` from the routing caller, or treat a failed canary as healthy.
The native `Store` lives in the routing service, not the Hermes native package;
a delegated claim that it was the missing native API was incorrect.

Independent staged review also retains two release blockers: ownership may expire
between a pre-send lease check and remote acceptance, and between the check and
internal wake admission. Fenced settlement protects SQLite ownership but cannot
undo an already-issued external effect. Do not claim exactly-once origin delivery
or duplicate-free wake admission from these leases. A lease renewal alone cannot
close the remote-acceptance/persistence crash window; deterministic admission
idempotency and explicit transport semantics require independent proof. These
findings remain unresolved, not waived, and prohibit release acceptance.

## Remaining four gates

1. Reviewed exact-head supported source CI, explicit review of any platform-scope
   exception, feedback reconciliation and accepted source merge (`t_6ca511b2`).
2. Tracked compatible deployment for scoped approvals, origin notifications and
   external recovery (`t_f5549b03`, `t_e8be0705`, `t_b3e284b4`, `t_c8210a94`).
3. Independent live crash recovery, exactly-once origin notification, live-worker
   preservation and project/context/approval isolation (`t_6ccc8045`, `t_dea8d4e3`).
4. Reconcile existing boards and resume eligible authorized business cards without
   duplicate graphs (`t_e8e714a8`). No live trading/orders or new spend.

Use existing external deployment safe-boundary machinery. No gateway restart
inside an active agent turn, no incompatible-client admission probe that could
replace the running gateway, no heartbeat-age-only reclaim. Scoped owner policy
must use the normal protected-file approval path; conversational consent is not
proof that protected tooling accepted the operation. Do not bypass CLI task
mutation refusals or write the live task database directly.

Existing runtime, evidence, backups and dirty worktrees remain retained. No cleanup
is authorized merely because CI is pending; remove only proven inactive clean
worktrees whose commits remain on durable refs.
