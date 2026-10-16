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
