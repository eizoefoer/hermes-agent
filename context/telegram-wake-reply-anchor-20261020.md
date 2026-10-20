# Telegram native wake reply-anchor correction — 2026-10-20

## Scope and ownership

Existing repair `always-on-harness/t_b6b87d0e`, deployment `t_58627570`; no new task graph. All three scoped boards were read through supported CLI and had zero running cards. Named native worktree `fix/telegram-wake-reply-anchor-20261020` is the sole bounded source writer for this slice. Frozen native baseline is `123fc8c6dc233ab316721d12ff1f1dc3de69ee44`, verified in GitHub eizoefoer/hermes-agent before publication. Shared inherited routing origin is NOT the native publication destination. Source correction is `e2f8c31053bb4af520604ca8b2ff5837e73f865a`.

## Causal failure and minimal correction

Previously observed real event6233 woke the repair controller and executed its next action, but Telegram response delivery failed converting its synthetic kanban-wake ID to int. Source trace: `snapshot_automation` deliberately persists immutable producer identity as message_id; native restore retains it; `_reply_anchor_for_event` returned it as a Telegram reply anchor; actual Telegram.send then called int on the JSON identity. Do not change durable admission identity or fingerprints to cure a transport problem.

Six added lines in the existing reply helper omit the reply anchor only for internal Telegram events with non-ASCII-decimal message IDs. Chat, topic and adapter selection stay unchanged. Numeric real message replies (including internal events), explicit redirects and non-Telegram behavior remain. No framework, new dispatch path, provider repair, runtime config or production plugin change. This native source repair addresses the separate observed Gate3 delivery defect; it does NOT establish a native-upgrade prerequisite for the original two plugin fixes.

## Executed evidence

Raw logs under `context/telegram-wake-reply-anchor/`:

- `red.log`: actual adapter.send regression, 2 failed / 5 passed; both DM cases fail with the exact synthetic-ID int conversion. Forum routing already passed.
- `green.log`: initial seven-file regression after minimal correction, 75 passed / 0 failed.
- `native-composition.log` and `native-composition-green.log`: retained failed composition-test attempts. The test incorrectly assumed terminal execution meant transport had finished. The native path commits terminal BEFORE deliver_settled; changing stub reply text did not fix that race. These are fixture failures, not new product RED or acceptance.
- `native-composition-final.log`: corrected bounded state wait observes send count AND pending-delivery exhaustion; actual native authority/admission/restore/deliver_settled plus real Telegram.send fake bot, 8 passed / 0 failed. Inference and network remain fake. Replaying the same immutable event retains one admission and one wake send.
- `final.log`: exact nine existing/new native files, 94 passed / 0 failed, file retries0. Includes Telegram topics, explicit redirects, event dedupe, wake scope/admission, background completion, notifier ordering and notifier behavior.

Canonical runner used this checkout's `scripts/run_tests.sh --file-timeout 120` with absolute targets, `HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python3 HERMES_TEST_FILE_RETRIES=0`. Actual runner interpreter reports Python3.11.15. No bare pytest, guard bypass, full-suite or hosted-CI claim.

Independent spec then safety/quality review `deleg_9f296229`, gpt-6-luna/openai-codex, read exact e2f8c310 source and accepted BOUNDED SOURCE ONLY. Reviewer independently ran the new regression and existing topic file: 28 passed / 0 failed; diff-check clean. No actionable findings. Real network delivery/inference, deployment and live lifecycle not exercised.

## Runtime and remaining gates

Final `runtime.json`: native gateway PID514483/start457093884 unchanged; ExecStart native rc-123fc8c6dc23. Active/plugin pointers remain routing rc-b79039d833a9. Reviewed corrected plugin rc-87ba742de1d7 remains staged-only. No production restart/stop/signal/drain/reload, pointer/config/unit/acceptance/approval mutation, business action or spend.

Gate3 independent real recovery, correct-origin delivery and project isolation remain OPEN. Source composition is not the actual event-wake delivery receipt. Keep cron execution fallback. Gate4 business implementation has NOT resumed; requested existing eight cards retain their observed blocked/todo states. No new user input needed for safe source work.

Next existing integration lane must consume reviewed e2f8c310 for origin-delivery repair without coupling native activation unnecessarily to plugin-only publication. The missing concrete reviewed external plugin-only activation/rollback at a settled boundary remains an engineering capability gap, not a request to reapprove original fixes. No hosting-process restart, delayed/detached workaround or broad installer shortcut. After authorized deployment, observe a genuine event wake AND successful original-destination reply, independently verify recovery/isolation, then resume eligible existing business implementation tasks.

## Cleanup

No deletions. Retain this clean pushed but unmerged native source worktree as an integration handoff; preserve runtime releases, dirty shared work, secrets, state, backups and evidence. No disposable units created. Exact process/path scan and Git cleanliness/ref publication recorded with final task comment; inaccessible /proc entries limit host-wide inactivity proof. Source and evidence remain recoverable on the remote branch.

Models: parent gpt-6.1-sol/openai-codex; independent reviewer gpt-6-luna/openai-codex.
