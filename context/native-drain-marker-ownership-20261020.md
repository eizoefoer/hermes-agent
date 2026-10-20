# Cooperative native drain-marker ownership — source qualification only

Parent gpt-6.1-sol continued from reviewed native6ae5daae9abe666ff8883575765b344b46aae4a8 in named fix/native-drain-marker-ownership-20261020. Native and routing repositories are distinct despite inherited shared Git origin. Child deleg_706cd82f created the clean tree but returned no implementation; parent took over, no continuing implementation worker. Existing native cards were inspected with supported list/show; no workers running on always-on-harness, auto-dropshiper or heisentick-forward and no transitions/reclaims/duplicate graph.

## Concrete causal boundary

Routing's transaction-local admission lock does not serialize native write_drain_request/clear_drain_request. A completed observation can therefore be invalidated before handoff. Actual prechange native multiprocess RED invokes both supported native mutations and observes mutated rather than refused. This is a missing ownership dependency, not a production incident or authority blocker.

Additive gateway/drain_ownership.py introduces nonblocking POSIX flock scope drain_marker_ownership(home=...). Native write/clear cooperate; explicit ownership capability allows its owning process to mutate the selected home without recursively acquiring. Scope validates PID, home, descriptor lifetime and persistent lock inode. Contending normal write/clear raises BlockingIOError, never waits indefinitely. Exiting/error/owner death never deletes the marker, preserving unknown effects; persistent lock file is deliberately retained. Without a hold, existing POSIX mutation behavior remains; unsupported platforms preserve unheld behavior and refuse optional ownership. Direct filesystem writers/lock deletion and OLD deployed native writers are NOT fenced. This is cooperative serialization, NOT authorization or an activation API. Expiry and epoch semantics remain unchanged and can still end activeness; a lifecycle must remain bounded/recheck them and reconcile unknown owner loss.

Concrete consumer lives in routing tree /home/ubuntu/worktrees/external-gateway-activation-20261020, NativeGatewayDrain.owned_request(plan). It validates existing external plan before any native side effect, holds native capability across request and observations, preserves marker/intent on exit, and refuses missing new native API rather than downgrading. Actual watcher/actual status writer with isolated admitted coroutine qualifies work settlement and native cancellation exclusion while held. Systemd/Git admission and serving/inventory remain fixtures. This is NOT resident evidence or end-to-end activation/rollback.

## Execution/review

Evidence retained in routing context/evidence/drain-marker-ownership/:
- native-red.log:5fail, including real competing native clear/write mutating under prechange behavior.
- native-green-initial.log: new ownership5pass; existing external-drain15pass1fail at old60ms watcher-entry assertion.
- external-repeat.log: explicit existing-file repeat16pass. Initial failure retained as timing-sensitive, NOT erased/waived.
- watcher-red.log: unowned1pass/owned1fail missing concrete owned_request.
- composition-green.log:3files12pass0fail.
- final.log: canonical9explicit files58pass0fail, retries0. Includes native ownership, work-refresh, external drain, reload receipt/late rewire and routing watcher/adapter/planner/malformed-marker tests. Actual count58, estimated53 is not authoritative.
- Independent read-only review deleg_3c214c65 (gpt-6-luna) accepted bounded SOURCE and independently ran2files7pass0fail; transcript /home/ubuntu/.hermes/cache/delegation/live/deleg_3c214c65/task-0.log. Review predates two additional adapter-only assertions; production source unchanged after review. No main/full-suite/hosted-CI/deployed/live claim.

Canonical runner: this native checkout scripts/run_tests.sh with HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python3 HERMES_TEST_FILE_RETRIES=0 --file-timeout120 (separate argv) and explicit absolute files. Diff whitespace checks pass.

## Next exact integration dependency

Do NOT activate from cron or bypass self-restart guards. Existing resident native123fc8 does not cooperate with this new lock, so source-held ownership is NOT a completed old-owner migration fence. Single routing integration writer must finish existing external lifecycle with continuous qualified native drain ownership and old-writer exclusion through settlement, exact-source successful readback and coordinated qualified rollback. No generic callback API. Preserve original plugin fallback/reload fixes and their compatible rollback sources. Unknown settlement cannot authorize writes/restoration. Then independent exact-source transaction review, existing authorized external safe-boundary activation, resident plugin/original-feature canaries and Gate3 real recovery/correct-origin/isolation. Gate4 existing eBay/trading implementation remains not resumed.

## Runtime/cleanup

Gateway716234 active; plugin pointer rc-4faf458c56da; production drain marker absent. No production drain/restart/reload/config/unit/link/acceptance writes, no business mutations/spend. active-release remains unknown_unproven. Test subprocesses exited/waited; no systemd units created. Exact cwd scan returned no accessible owners in native/routing integration trees, but unreadable proc entries limit host-wide claims. Retain both trees for unfinished integration and preserve dirty primary, runtime, evidence/backups/secrets. No checkouts deleted. Event-driven continuation still unverified; keep execution fallback. No operator input needed for remaining source integration.
