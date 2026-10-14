# Fork PR CI repair and notification release boundary

PR #10 targets `compat/notification-recovery-c70332f888`, not the divergent
fork `main`. Attribution uses the event's immutable PR base and head SHAs.
Non-PR calls retain the `origin/main` fallback; Git failures remain blocking.
Existing mapping checks remain intact. The mapping for
`openclaw-agent@users.noreply.github.com` was verified against GitHub's commit
API for `74336e508ff15be50be8572f4e5cb0e1a3c53828`: login `openclaw-agent`,
account ID `258722376`. No imported upstream author mappings were invented.

The public fork uses standard hosted runners for required Python, JS, Rust,
Nix and native Windows x64/ARM64 unit coverage. These runners are free for
public repositories according to GitHub's hosted-runner reference. Both Python
slices and all test selections remain; workers are bounded to available CPUs,
JS checks serialize, and time budgets increase rather than dropping tests.
The first standard-runner receipt exposed Windows files killed at the default
300-second file deadline (not a passing run despite its zero-failed summary).
Native Windows uses two file workers and a finite 900-second file deadline to
leave CPU headroom for nested PowerShell trees; all timeout/nonzero exits still
fail CI. This is runner scheduling, not permission to suppress desktop tests.
Workflow edits conservatively activate all classifier lanes. Docker publication
retains its existing upstream-only boundary; slow E2E lanes remain under the
existing release/dispatch policy, not silently enabled on PRs. This repair is
not permission to invoke paid release runners or purchase runner capacity.

Regression command:

    HERMES_PYTHON=<test-interpreter> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 3 tests/ci/test_fork_pr_gates.py tests/ci/test_workflow_job_graph.py tests/ci/test_classify_changes.py

Validation must include the canonical Kanban test family and all applicable CI
on the exact final PR head. Independent review must cover the CI repair and
notification candidate before merge. Do not bypass a failed or queued check.

This CI repair does not deploy notifications, fix the old installed startup,
change runtime state, or resume the blocked notification root. Existing source
recovery and its coordinator retain those responsibilities. Before root resume,
the old startup failure (`state.reconcile_logical_turns` on `None`) must be
resolved in a reviewed release, not by editing installed source. Activation is
through the existing deployment/IaC graph and supported external promoter at a
safe inactive boundary; preserve running work, subscriptions and approvals.
Acceptance still requires single-gateway health and an actual Telegram canary.
