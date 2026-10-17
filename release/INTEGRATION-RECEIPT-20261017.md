# Four-gate source integration receipt — 2026-10-17

Source-linked release evidence, not a parallel task ledger. Canonical tasks remain the existing eight cards on `always-on-harness`.

## Gate 1 — supported-source acceptance and canonical integration completed

Accepted candidate: `123fc8c6dc233ab316721d12ff1f1dc3de69ee44`.
Accepted complete source tree: `ba9af54f5f9d7323e78d8e34eeeb0992222696d6`.
Hosted run: https://github.com/eizoefoer/hermes-agent/actions/runs/39000952128 .
All five standard Linux/macOS lanes and the exact-source artifact verifier completed success. Parent readback and independent review checked source SHA/tree/run/manifest binding, nonempty actual summaries and log hashes. No failed or empty lane was waived. Lane selections overlap; their counts must not be added as unique tests.

| Native lane | Files | Passed | Failed | Skipped | Artifact |
| --- | ---: | ---: | ---: | ---: | --- |
| Linux default slice 1 | 3123 | 30633 | 0 | 457 | 12090996302 |
| Linux default slice 2 | 3122 | 32403 | 0 | 424 | 12090281399 |
| Linux shared contracts | 27 | 262 | 0 | 1 | 12088679229 |
| macOS shared contracts | 27 | 261 | 0 | 2 | 12088279515 |
| macOS native markers | 312 | 1182 | 0 | 370 | 12089545830 |

Artifacts retained at `/home/ubuntu/.hermes/cache/scoped-39000952128/`. The two Linux slices cover canonical default discovery, not all slow integration/E2E. macOS shared/native scope is explicit in the existing manifest. Broad upstream/Windows failures remain visible separately and are NOT described as green or release prerequisites.

Independent source/receipt review: `deleg_c9b990d8/task-0`; implementation/component review: `deleg_c9b990d8/task-1`. Integration review `deleg_2107508a/task-0` approved the full 22-commit stack and verified no branch-protection obstacle. An earlier review `deleg_3c439c12` incorrectly reapplied broad Windows CI as a prerequisite; its rejection is superseded by the corrected scope-aware review, not silently ignored. Review transcripts remain under `/home/ubuntu/.hermes/cache/delegation/live/`.

PR11/PR12 issue comments, reviews and inline comments were read freshly and were empty. PR12 was marked ready through the supported GitHub operation, then normal merge commits were used with exact expected-head fencing; no admin/protection bypass, squash, rebase or upstream update:

- PR12 merged into PR11: `2406c026277a34d2105c92f4ed15b2a8dc4a9aa1`.
- PR11 merged into maintained `compat/scoped-approval-db44c46208`: `cba881b0c497f5bbaba7fa85309176cb476739e2`.
- Exact GitHub commit readback verifies BOTH merge trees equal the accepted tree above. Both PRs read back merged, and the canonical compatibility ref reads back the latter merge SHA.
- Fork default main remains `592ae879fc557729192e238e2ac385492ae2c470`, deliberately divergent and untouched. This is compatibility-release integration, NOT main acceptance.

This handoff document is committed on a separate release-evidence branch so it does not silently alter the accepted canonical source tree or pretend an untested documentation commit is the hosted candidate.

## Real-component probe and delivery semantics

Independent canonical execution at accepted candidate used the existing PM test interpreter:

`HERMES_PYTHON=/home/ubuntu/.hermes/installs/4633b8792bccbc37/test-environment/gen-64919a58e5e34ea3b6cf37077bb2e57b/venv/bin/python scripts/run_tests.sh tests/gateway/test_kanban_event_wake_idempotency.py tests/gateway/test_kanban_notification_crash_window.py`

Result: 2 files, 3 passed, zero failures. The event test exercises native board, authority and SQLite admission: immutable per-event replay despite growing batch/mutable task data; bounded/redacted content; payload-conflict rejection; terminal receipt reuse; no volatile keyed fallback; healthy human turn untouched. The notification probe actually exits a child after loopback HTTP acceptance before local ping checkpoint, then retries in another process and observes TWO accepted messages. This is truthful failure-window characterization, not exactly-once external-delivery proof. Remote transport remains at-least-once attempts, possible duplicates/unknown outcomes, with no eventual-delivery guarantee.

## Updated acceptance gaps BEFORE any further repair

| Intended feature | Concrete remaining assertion/probe | Causal source/dependency | Smallest next action |
| --- | --- | --- | --- |
| Crash-durable keyed internal wake admission | Existing test loses receipt by in-process exception; it does not physically exit/reopen the authority process | Native admission is durable, but process-boundary persistence/recovery is not independently proved by that modeled failure | Isolated child commits keyed wake and exits before receipt; fresh child reopens same disposable DB, replays same identity and verifies one admission; changed payload must conflict. No production transport/model call. |
| Compatible rollback-capable production deployment | Approval-worker ExecStartPre currently fails installed-unit equality; gateway still runs prior native source | Installed always-on-harness unit differs from rc-c5cccd9ecdc5 canonical unit; reviewed installer `_restart` unconditionally restarts gateway | Prepare/review an external inactive-boundary plan using existing release installer/native store, preserving sandbox/resource drop-ins and backup/rollback; bind native canonical merge SHA and compatible routing source. Do not invoke restart-capable promote within this controller. |
| Independent live recovery/isolation | Deployed exact-source crash resume, correct-origin notification, healthy PID/start preservation, profile/project/routing/approval isolation and no terminal retry obligations lack current live receipts | Production has not activated accepted source | After safe-boundary deployment, independent isolated/live canaries with exact runtime/source/registry receipts. Do not call bounded source tests live deployment proof. |
| Existing authorized business continuation | Existing eight cards not reconciled/resumed in owner context | Native CLI refuses this execution context with `delegate_task child contexts cannot mutate Kanban tasks or boards` | Owner-context native board reconciliation after preceding gates; no env override, DB write, duplicate graph or blind worker spawn. |

Fresh system readback at 05:37:42 UTC: `hermes-approval-worker.service` failed, `Result=exit-code`; journal reports `deployment-integrity: FAIL: RuntimeError: installed unit differs from canonical source: /home/ubuntu/.config/systemd/user/always-on-harness.service`. Read-only inspection of installer lines225-229 confirms direct promote is not a safe in-turn activation path. No guard was bypassed; a combined terminal read of that restart-capable source was itself blocked, and no such script was executed.

Gates 2–4 remain OPEN. Production gateway PID3430553/start410927755 and supervisor PID3325081/start402731328 were not interrupted or repointed. No workflow redispatch, dependency/version change, Windows repair, purchase/live trade, protected-file approval weakening or production mutation occurred.

Cleanup: no deletion. Dirty routing checkout, PR11 artifacts, source/release-evidence worktrees, runtime releases, backups and research/evidence are retained. This named handoff worktree remains nonterminal for deployment continuation; its commit must remain on a pushed durable branch. Controller remains enabled because all four gates are not complete.

## Controller continuation — 2026-10-17 09:08 UTC

Live discovery, not stale continuity: PR11/12 read back MERGED; canonical remote compatibility ref is `cba881b0c497f5bbaba7fa85309176cb476739e2`. Its Git tree equals candidate `123fc8c6dc233ab316721d12ff1f1dc3de69ee44` (`ba9af54f5f9d7323e78d8e34eeeb0992222696d6`). Run `39000952128` read back success for five supported-host lanes plus artifact verifier; actual runner labels are only `ubuntu-latest` and `macos-latest`. Both PRs' fresh issue comments, reviews and inline comments were empty. This reconfirms scoped compatibility integration, not default-main acceptance or broad upstream aggregate green.

### Physical process-loss acceptance increment

The highest remaining isolated-feature probe from the preceding gap list is now exercised. Script `/home/ubuntu/.hermes/cache/four-gate-evidence/process-wake-20261017/physical_probe.py` uses actual GatewayRunner, DiscordAdapter, SessionAuthority, native route restoration and SQLite admission against disposable `HERMES_HOME`/runtime/database paths. Human inference is a blocked stand-in; adapter send is stubbed. The child commits keyed E1 behind the human turn, prints its admission evidence and physically exits 73 without producer checkpoint. A fresh child reopens the same disposable state and replays E1. It reads the SAME single queued admission and rejects changed text under the same identity with `admission_conflict`. The crashed human correctly becomes `unknown_execution`, pausing followers rather than guessing successful execution.

Parent execution: crash PID2464639, reopen PID2464772, admission `2646badaf52a45d49936d6c8bf4fee2b`. Readable evidence: `/home/ubuntu/.hermes/cache/four-gate-evidence/process-wake-20261017/physical-native-mi9xy7y0/{receipt.json,execution.log}`. Independent review and real rerun `deleg_ce07fdea/task-0` passed with distinct child PIDs2465719/2465819 and admission `310ef4ad713543a59a0a3c10f01d9f3a`; evidence directory `physical-native-lb22s3ot` under the same cache parent. The earlier incomplete `probe.py` and failed logs remain retained, not represented as success. No product fix was made and no accepted source tree was changed.

Limits: proves physical process-boundary keyed admission durability/replay/conflict behavior in isolated real components. It does NOT prove production crash resume, full watcher/board cursor loss, model execution, external notification exactly once, cross-profile matrix, or terminal retirement. External notification attempts still permit duplicates/unknown outcomes. Gate 3 remains open pending independent deployed-live receipts.

### Acceptance-gap record before any further repair

| Intended feature | Concrete failing assertion/live probe | Causal source/necessary dependency | Smallest corrective action |
| --- | --- | --- | --- |
| Restart-safe durable approval recovery | Approval worker `ExecStartPre` failed again at 09:07:51 UTC: installed always-on unit differs from canonical source | `deployment_integrity.py::check_canonical_units` compares installed main units with rc-c5cccd9ecdc5; installed source/unit drift is a real startup dependency | On a named routing worktree, capture exact canonical-versus-installed unit delta and existing drop-ins; reconcile in tracked compatible deployment candidate, preserving safeguards. Validate real unit/config readback and rollback before activation; do not weaken equality guard. |
| Noninterrupting rollback-capable activation | Existing promote/rollback paths unconditionally restart gateway; no inactive-boundary API found by this audit | `release_installer.py::_restart` lines225–229 and `_restore_and_restart` lines231–243; accepted native source is not yet pinned by this installer | Add/review the smallest supported external safe-boundary transaction to existing installer/native ownership machinery, with exact native+routing source manifests, PID/start and admission fencing, preserved drop-ins, and rollback tests. No ad-hoc timer/restart, competing controller, direct private-method activation or approval bypass. |
| Independent live feature acceptance | Exact-source production crash/origin/profile/approval/routing/healthy-worker/terminal receipts absent | Accepted compatibility source not activated; old gateway/supervisor remain | After reviewed boundary deployment, independent live canaries; do not transfer this isolated probe into live acceptance. |
| Existing authorized continuation | Native CLI again refused even board list: `delegate_task child contexts cannot mutate Kanban tasks or boards` (also reported launcher exposure failure) | Native owner-context guard; this cron cannot perform native board mutation | Eligible owner-context native reconciliation only after preceding gates; no environment override, DB write or duplicate graph. |

Independent read-only deployment audit `deleg_80d1401f/task-0` agrees installer promotion is unsafe in-turn. Parent verified its causal restart lines and current systemd error directly. Exact read-only board DB inspection found the eight named cards: three blocked (`t_6ca511b2`, `t_e8be0705`, `t_b3e284b4`), five todo (`t_f5549b03`, `t_6ccc8045`, `t_c8210a94`, `t_dea8d4e3`, `t_e8e714a8`); none has a worker PID. No card was mutated. Gateway PID3430553/start410927755 and supervisor PID3325081/start402731328 remain owned by existing services, not reclaimed by heartbeat age.

No repair without the causal record above. Gates 2–4 remain open. No upstream refresh, broad workflow redispatch, Windows/catalog/checkout repair, production restart/repoint, business action, spend or deletion. Dirty/active/nonterminal worktrees, runtime, evidence, failed prototype and backups are retained. This evidence branch is separate from the accepted canonical tree; its documentation commit is not new hosted source acceptance.
