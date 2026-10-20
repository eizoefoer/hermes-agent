# Native same-key reload correction — 2026-10-20 05:10 UTC

## Acceptance assertion and causal fix

Intended feature: activate/rollback the already-reviewed fallback/resume plugin without losing gateway handler bindings. Concrete failing public assertion: force reload of an already-loaded same-key plugin must call that profile's adapter rewire and report successful callbacks, not adapter inventory. Frozen native123fc8c6dc233ab316721d12ff1f1dc3de69ee44 emits loaded events only for new keys and its public verb merely counted configured adapters. The minimal correction explicitly calls existing profile-scoped _rewire_plugin_handlers on the gateway loop and returns its success count; existing loaded listeners remain intact, native idempotence handles a possible extra new-key pass. No loader redesign or guard changes.

Native source identity is distinct from routing plugin candidate despite shared Git object store. Branch fix/native-same-key-rewire-20261020 started exactly at native123fc8. The shared origin remote points to the ROUTING repository and does not contain123fc8 (GitHub API422); native fork eizoefoer/hermes-agent DOES contain123fc8 (API exact SHA). Initial SSH push refused publickey and HTTPS routing attempts timed out without creating a ref; do not repeat wrong-repository pushes. Explicit HTTPS native-fork push succeeded; remote native branch readback matches executable/source commit34e54fb2f1bc5df7dd568663a1e6eefb2c796935. Final context-only commit adds this provenance correction. Plugin candidate remains routing d61d71e32f3e in /home/ubuntu/worktrees/plugin-compatible-lifecycle-20261020; compatible rollback511d634ae694. Do NOT replace native PYTHONPATH with routing source.

## Execution, ownership and independent review

Canonical all-board running lists for always-on-harness, auto-dropshiper, heisentick-forward empty. Eight named cards and existing correction t_b6b87d0e inspected with correct --board; no transitions/reclaim/duplicate graph. Proc scan found no repair subprocess owner. Cron parent gpt-6.1-sol is sole bounded writer; no persistent worker claimed.

Child deleg_22827ba2 gpt-6-luna returned incomplete copied fixture with command-registry activation/home-IO failures, not meaningful behavioral RED. Parent took over rather than declaring external blocker. Rejected files/log retained as inert .txt under context/rejected-copied-fixture; no runnable rejected tests remain. No production state written by these fixtures.

Parent new minimal actual public-verb test with real discovery/command registry reproduced 2 RED stale-adapter assertions (primary/secondary) before source change. GREEN7 over new receipt and native late-rewire tests. Final canonical regression25 passed0failed across5files, no retries. Exact output in same-key-red.log, same-key-green.log, same-key-regression.log. Command:

HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python3 HERMES_TEST_FILE_RETRIES=0 ./scripts/run_tests.sh --file-timeout 120 tests/gateway/test_reload_plugin_rewire_receipt.py tests/gateway/test_late_plugin_rewire.py tests/gateway/test_platform_plugin_handlers.py tests/plugins/test_multiplex_plugin_cache_scope.py tests/tui_gateway/test_plugins_manage_late_activation.py

Tests prove already-loaded and repeat fresh registration reaches selected disposable adapter; secondary scope excludes primary; failed callback reports0; unserved-home request refused. Independent spec/safety review deleg_9effde1a gpt-6-luna accepted and independently reran7/7. Parent afterwards changed docstring only to remove misleading active-now claim and reran25/25. Not full native suite/hosted CI/real Telegram acceptance.

## Runtime and narrow next step

Gateway716234/start446756489 unchanged; native rc123fc8 and plugin/active pointers rc4faf unchanged. active-release.json unknown_unproven, loaded-plugin-proof false. No socket reload, native/plugin pointer, config/unit, approval DB, restart or promotion writes. Source correction CANNOT hot-replace the already-resident native reload implementation. Same-key count is fixed in source only, NOT in resident gateway.

Exact remaining activation capability: an already-reviewed authorized EXTERNAL safe-boundary operation that settles current gateway turns, activates the qualified native source plus reviewed plugin candidate with rollback, and starts gateway from that exact native root. Existing plugin reload cannot load its own native correction. Routing ReleaseInstaller full promote restarts harness+gateway and renders routing configuration; it is not plugin-only nor safe-boundary native activation. Pending supervisor-only promote_supervisor is not gateway activation. Do not invoke either as a shortcut, create delayed restart/timer, or bypass self-restart guard. Need review existing external restart path against these exact requirements before choosing it; if none is supported, hold activation and report missing operation/authority, not a generic maintenance request or new framework.

Next concrete eligible action: use this tiny reviewed source correction to select minimal controlled external activation versus plugin-only transaction, keeping both original fixes reviewed together and compatible rollback511. No more diagnostics growth in place of activation. Actual gateway-owned handler/original-feature recovery, correct-origin notification and project isolation proof remain required for Gate3. Gate4 requires existing eligible eBay/trading implementation owner actually resumed after Hermes gates; not resumed here. Protected provenance approval remains separate; no retry/bypass.

## Cleanup

Copied failed fixture quarantined, not deleted; logs preserved. Named source worktree retained because integration is unfinished. No checkout deletion or production cleanup. Runtime/releases/backups/secrets/shared dirty primary preserved. Exact process visibility is limited by inaccessible foreign /proc entries, so do not claim host-wide inactivity. Child finished; no ongoing repair worker after this bounded cycle. Commit/push and remote readback recorded by cron parent; no main/CI/deploy completion claim.
