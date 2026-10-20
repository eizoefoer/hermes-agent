# Telegram internal-wake reply anchor — 2026-10-20

## Scoped action and ownership

Cron execution owner consumed completed run355; always-on-harness, auto-dropshiper and heisentick-forward have no running cards. Existing notification card t_1ea0ac00 received source-ownership comment; no duplicate graph, reclaim or status transition. Requested gate cards read exactly: t_6ca511b2/t_e8be0705/t_b3e284b4 blocked; t_f5549b03/t_6ccc8045/t_c8210a94/t_dea8d4e3/t_e8e714a8 todo.

## Root cause and source

Intended correct-origin event-driven reporting -> actual event6233 woke execution but response failed Telegram numeric conversion -> admit_route_wake creates JSON ledger ID on MessageEvent.message_id; _reply_anchor_for_event forwarded it as Telegram reply target -> smallest source repair omits nonnumeric internal Telegram IDs from reply anchors, preserving ledger identity and ordinary numeric anchors. No dispatch/framework change, no provider changes, no upstream/version bump.

Native baseline123fc8c6dc233ab316721d12ff1f1dc3de69ee44 verified through eizoefoer/hermes-agent GitHub API (not inherited routing origin). Source: gateway/platforms/base.py, tests/gateway/test_telegram_internal_wake_anchor.py. Named branch fix/telegram-internal-wake-anchor-20261020.

Canonical RED: new6cases,2failed4passed; synthetic ordinary-DM and DM-topic IDs wrongly returned as reply anchors. Raw context/evidence/telegram-internal-wake/red.log retained. Canonical GREEN:5files66passed0failed, no retries,11.1s; includes new anchor tests, existing Telegram thread fallback, actual native Kanban event admission/deduplication, background notifications and redirect-anchor suites. Raw green.log retained. Runner: selected native scripts/run_tests.sh with HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python3, HERMES_TEST_FILE_RETRIES=0, --file-timeout90 and five explicit absolute test files. git diff --check passed.

Independent spec/safety review deleg_3ad732bd (gpt-6-luna) accepted bounded diff; reran canonical6passed0failed. Transcript /home/ubuntu/.hermes/cache/delegation/live/deleg_3ad732bd/task-0.log. No actionable findings. This is source/helper-level qualification with adjacent native admission tests, NOT actual Telegram transport, resident patch, hosted CI, main integration or live notification acceptance.

## Runtime and next action

Gateway716234/start446756489 unchanged; active-release.json unknown_unproven; dirty routing plugin release preserved. No production drain/stop/signal/restart/reload, link/config/unit/approval/acceptance metadata writes. Gates3/4 remain open; business implementation has not resumed. Event execution fallback remains required until correct-origin real transport receipt succeeds.

Do NOT expand original two-plugin-fix deployment into native upgrade: this notification repair is a separate existing notification-scope artifact, not a plugin startup prerequisite. Next eligible original deployment action remains smallest reviewed plugin-only publication/rollback handoff preserving native123fc8, using existing external separate-shell lifecycle only at safe settlement boundary. No hosting-turn restart or guard bypass; original approval need not be repeated. Notification branch separately awaits normal source integration and supported activation before live receipt. No live eBay/trading actions or spend.

## Cleanup and continuation

Evidence/raw logs are committed with source, not disposable scratch. New worktree may be retired only after clean status, exact-path process inspection, durable branch and remote SHA verification; canonical task comment records actual result. Dirty project/runtime/evidence/backups and other unassessed worktrees retained. No broad cleanup or host-wide inactivity claim.

Models: parent gpt-6.1-sol/openai-codex; independent review gpt-6-luna.
