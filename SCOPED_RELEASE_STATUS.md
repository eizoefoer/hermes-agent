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

## Native recovery composition qualification — 2026-10-16 14:48 UTC

Recovered the prior interrupted run's uncommitted native recovery port in this
same worktree; no duplicate worker/task graph was created. Added the retained
`dispatch_once(recovery_policy=...)` API, source-bound retry receipts and terminal
obligation retirement without replacing the modern dispatcher or its admission
gates. Fixed the reproduced integration defects:

- Dry-run reclaim no longer creates recovery tables/events. Existing native
  dry-run/reclaim behavior otherwise remains unchanged.
- PID plus start-identity live workers are preserved despite stale heartbeats.
- Callback signature inspection cannot invoke an admitted callback twice when
  that callback raises `TypeError`/`ValueError`.
- Recovery obligation retirement is in the same transaction as completion or
  archival. Injected interruption after deletion reproduced both rollback gaps
  RED (2 failed), then both passed after moving deletion into the transaction.
- Moved spawn helpers and hard-delete relations into topical siblings to meet
  existing file/complexity caps; no ratchet waivers added. The real CLI `--version`
  test moved to its own discovered file and uses an isolated config with update
  checks disabled. Its 30-second deadline and real subprocess assertion remain;
  no production startup/update behavior was changed.

Canonical validation of the final integration: **133 Kanban-related files,
949 passed, 0 failed, 4 skipped**, 279.4 seconds. This includes 81 native-family
files, 540 passed, 4 skipped (overlapping, not additive). After the final redundant
import cleanup, launcher/native DB tests passed again. All 12 staged source checks
passed, zero blocking/advisory findings. Independent final staged review found no
new actionable regressions and reported 12 focused passes. Initial review's
same-tick-backoff concern was not reproduced: observation occurs during reclaim,
before candidate admission. Source-capture failures intentionally remain bounded
fail-closed native failures; a changed source head does not lift native blocks.

A fresh real user-systemd canary against this native source plus routing candidate
`0096709ab866726d79b2adc33459522bfe550448` passed: **4 component restarts,
1 execution, 1 accepted local HTTP delivery**. Durable runs record crashed then
completed. Production gateway PID 3430553 and supervisor PID 3325081 retained
identical PID/start fingerprints before/after; worker and supervisor were in
separate cgroups. All three exact canary units were read back inactive,
MainPID=0, Result=success. Evidence:
`artifacts/scoped-approval-20261015/controller-native-canary-1436/` in the scoped
feature worktree, including its `receipt.json`, DBs and component restart log.
This is isolated real-component qualification, **NOT Telegram exactly-once,
production deployment or the full independent acceptance gate**.

Hosted CI for the preceding exact source `9629da76e27` completed: both Linux
Python shards, both macOS lanes, JS, Rust, attribution, Docker and Nix succeeded;
the all-platform aggregate failed because Windows x64/ARM64 failed. Exact logs
are retained. The x64 failure is the Windows desktop updater handoff-log
PermissionError; no unrelated Windows repair is included in this slice. Fresh
CI is required for the next pushed source; previous-head passes are not transferable.
Fresh PR12 comments/reviews/inline threads were all empty before this checkpoint.

All eight canonical task IDs were read from the actual always-on-harness board;
none has a live worker/claim. Statuses remain blocked/todo, not accepted. Native
turn-lease inspection found this cron controller as sole active owner. No live
board mutation, gateway restart, runtime activation or eBay/trading resumption
was performed. No worktrees were created/deleted; evidence, runtime, backups and
nonterminal work remain retained. Exactly-once remote send/wake crash-window
findings above remain release blockers. Follow the existing external safe-boundary
promotion mechanism only after reviewed source/CI and transport/admission proofs.

## Scoped hosted acceptance entry point — 2026-10-16

`release/SCOPED-RELEASE.md` and `release/scoped-acceptance.json` now define the
frozen Linux/macOS contract. `.github/workflows/scoped-release.yml` preserves the
broad workflow and uses five free standard-runner lanes: two full canonical Linux
default-discovery slices, explicit shared contracts on Linux/macOS, and the native
macOS marked suite. Actual candidate/tree SHA, baseline ancestry, selected command,
log hash and nonempty summary are retained and downloaded by a fail-closed aggregate.
Slow E2E exclusions of the existing default runner are explicit, not full E2E proof.

Initial independent review rejected self-referential SHA, wrong baseline,
macOS Bash4 selection and malformed result parsing; those were corrected before
commit. Final independent review approved the design except a claimed missing
`prune-python-cache` action input. Readback of setup-pm/action.yml lines45-47
proves that input exists, so that finding is inapplicable; no action-schema change.
Canonical explicit shared suite: 26 files, 255 passed, 0 failed, 1 foreign-Windows
skip. Helper behavior suite: 12 passed. These are local qualification only.

Broad run38922912950 at preceding feature source34ad3512d9a has two failed
transient-resume reconnect tests, plus an LSP cleanup flake; reconnect source and
tests are unchanged from frozen baseline, but baseline-failure attribution has
not been established. Local unchanged reconnect tests pass23/23. Both failures
remain visible, and reconnect is explicitly in the new shared acceptance selection.

An attempted progress comment on t_6ca511b2 was refused by the native CLI:
`delegate_task child contexts cannot mutate Kanban tasks via the CLI`.
The owner-context blocker is retained; no DB bypass, respawn or graph duplication.
No production service change, gateway restart, business mutation or worktree
deletion occurred. Exact hosted receipts, source merge, deployment and independent
live acceptance remain required; the remote-send/wake crash windows remain open.

## Scoped hosted repair — 2026-10-16 17:00 UTC

At candidate `aecc914b44ba80f845fb80546a35033334f31c80`, scoped run
`38932407158` passed Linux shared and macOS native lanes. macOS shared failed
`test_infrastructure_spawn_refusal_never_charges_the_card`: this Linux/systemd
contract assumes a missing user bus raises, whereas the native macOS child helper
correctly returns an in-process command before Linux supervisor probing. Independent
read-only review confirmed the host mismatch. Marked ONLY that test native Linux;
it remains mandatory and executes on Linux, with no fake OS or production change.
The canonical Linux DB/helper replay passed 65 tests across two files, zero failures,
one unrelated Windows skip. macOS must be re-executed on the new exact head;
these local passes are not hosted acceptance. Full Linux slices of the prior run
were still executing at readback, not accepted or cancelled.

The same review identified missing receipt-lane validation; the aggregate now rejects
a receipt from another lane as well as enforcing the existing source/tree/hash/result
checks. The macOS shared selection is explicitly the release manifest's contracts,
not a claim of full macOS discovery; full canonical default discovery remains Linux.
Reviewed integration into the maintained compatibility ref and exact deployment
provenance remain separate mandatory gates, never implied by a candidate receipt.
PR11/12 comments, reviews and inline comments were empty at fresh remote readback.

Native board comment again refused: `delegate_task child contexts cannot mutate
Kanban tasks via the CLI`. This is an owner-context blocker; no direct database
write or context/guard bypass attempted. Existing task states were read, not changed.
No workers reclaimed/spawned, broad workflow dispatched, gateway restarted, business
mutation performed, or worktree deleted. All evidence/runtime/dirty/nonterminal
work remains retained. Remote-send/wake crash-window findings remain blocking.

## Exact receipt review and inherited provider blocker — 2026-10-16 17:25 UTC

Hosted run `38936098738` at `20e1fa5ccae5b95609c9647493441c8b0c9b9809`
has verified downloaded artifacts: Linux shared `12061566329` (255 passed, 1
skip), macOS shared `12061332626` (254 passed, 2 skips), macOS native
`12060554238` (1182 passed, 370 skips). These overlap; do not add them as
unique tests. SHA/tree/run/manifest binding, actual canonical summaries and log
hashes were independently read back. Both complete Linux slices were still
executing at readback; the five-lane aggregate is NOT accepted.

Previous scoped run `38932407158` Linux-2 finished with 30978 passed, 1 failed,
434 skipped. Exact failure: `test_commandcode_profile.py::TestCommandCodeFetchModelsPickerContract::test_resolve_provider_full` expects
`COMMANDCODE_API_KEY`, while live models.dev returns only `CMD_API_KEY`.
An isolated checkout of frozen baseline `db44c46208f1b421b4ecb7e56bf9d68b0423906f`
reproduced the identical assertion (9 passed, 1 failed); the current candidate
reproduced it too. Provider resolver, CommandCode plugin and this test have zero
diff from baseline. This establishes inherited behavior under the current external
catalog, not a new scoped-feature regression and not a waived product defect.
No unrelated provider repair, test weakening, catalog spoof or upstream update was
made. Keep the failed lane visible and diagnose only release-relevant causes;
full accepted-source CI remains blocked until this contract is honestly resolved.
Downloaded failed logs: `/home/ubuntu/.hermes/cache/four-gate-38932407158-linux2.log`.
Downloaded successful evidence: `/home/ubuntu/.hermes/cache/four-gate-38936098738/`.

Independent review identified a valid evidence weakness: identical self-reported
trees across lanes were not compared to an independently checked-out tree. The
aggregate now checks out exact `github.sha` and validates receipt tree, run ID and
manifest against that checkout, plus host identity and parsed log summary. Four
new binding regressions failed RED; canonical helper suite then passed 16/16.
YAML and embedded verifier compile passed. Independent current-diff review found
no actionable defect. Earlier review claims that macOS shared passed an invalid
lane or required paths were absent were disproved by the actual matrix and all
26 paths; corrected review explicitly confirmed both. Candidate acceptance does
not replace later exact integration/ref/deployment review.

No board ownership bypass, source activation, gateway restart, worker reclaim,
new graph or business action occurred. All existing runtime/evidence/dirty and
nonterminal work remains retained. The temporary baseline comparison checkout is
eligible for removal only after exact inactivity, clean status and durable-ref
checks; its failure output is retained independently in the session evidence.

## Supported-platform failure follow-up — 2026-10-16 22:20 UTC

Scoped run `38964228125` failed at exact source
`9756865cc32898f5dc3c62341e61c31cbd2d3868`; its aggregate remains failed.
Both shared lanes and native macOS passed. Linux1 failed on the session `/diff`
RPC deadline and a fixture `copytree` racing `.git/objects/maintenance.lock`;
Linux2 failed on the previously reproduced CommandCode catalog environment-key
mismatch. None is waived, and the session timeout is not proved inherited.

The Git fixture now runs each of its two commits with command-local
`gc.autoDetach=false` and `maintenance.autoDetach=false`: commit maintenance
finishes before the later snapshot, without disabling maintenance or persisting
configuration. An initial persistent-config attempt correctly failed the existing
stock-maintenance assertion and was replaced, not hidden. The final canonical
Git-lock file passed **22 tests, zero failures, no retries**; all 12 source checks
passed. Independent final review found no actionable issue in this narrow diff.
All real holder/reclaim and stock-config assertions remain unchanged.

The session read test passed locally twice, including alongside Git-lock tests;
this does not explain or waive its hosted timeout. Read-only investigation traced
working-diff collection but obtained no CI phase timing proving the slow point.
Keep that failure unresolved; no RPC timeout increase, test exclusion, unrelated
provider repair, upstream update, merge or live activation is authorized by these
local results. Exact revised-source hosted acceptance is still required before
Gate1, followed by the existing deployment and independent live gates.

## Original-feature crash-window acceptance — 2026-10-17

Current causal gaps, independently reviewed transport semantics and real-component
probe receipts are tracked in `release/ACCEPTANCE-GAPS.md`. A real child-process
crash after loopback HTTP acceptance produces two identical accepted sends on
lease recovery. A separate native FIFO probe preserves the healthy held turn but
creates two wake admissions for the same source event after notifier interruption;
the desired-one regression is deliberately RED. Fresh UUID-per-attempt wake identity
is causal. Mutable claimed-batch hashing alone is not a robust repair. These findings
block release; no four-gate acceptance, production activation or CI success is claimed.
Do not resume provider/checkout baseline maintenance before resolving this original
feature defect. Preserve normal approval gates and the existing task graph.

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
