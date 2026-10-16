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
