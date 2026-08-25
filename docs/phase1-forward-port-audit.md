# Phase 1 current-line forward-port audit

Audit base: `1bbb6e5bce56e721ab685af4cd87df21bbff4d35`

Production reference: `5400fb88e5bd235598b8681447ed70576480b79e`

Old behavioral reference: `7bc5e0604e2ceb8fe2cee70edcaa08b8724463f1`

## Base selection

Current NousResearch `main` is 79 commits ahead of the production revision;
the production revision is its direct ancestor. Both trees declare version
`0.20.5`. No `v0.20.5` tag points at the production revision. The canonical
current `main` revision is therefore the selected forward-port base.

## Invariant audit

| Phase 1 invariant | Current-line status | Evidence / gap |
| --- | --- | --- |
| Durable logical-turn admission | MISSING | `SessionDB` has no logical-turn/event/attempt ledger or `admit_session_event` API. |
| Durable lease authority | PARTIAL | Cross-process session-turn leases exist, but upstream reclaimed an unexpired lease from PID liveness. The first forward-port slice removes that early reclaim. |
| Truthful task/goal identity | PARTIAL | Current model entry points accept task context, but there is no shared admitted-event record enforcing authoritative/null correlation across producers. |
| Occurrence identity | MISSING | Current `MessageEvent` has transport message/update fields but no acceptance-time persisted occurrence identity shared by all persistent producers. |
| Bounded recovery filtering | PARTIAL | Delivery and delegation stores have scoped recovery; no common logical-turn dispatcher exists for accepted model work. |
| Execution vs delivery | ALREADY PRESENT (transport layer) | `gateway.delivery_ledger.DeliveryLedger` persists delivery obligations independently of model execution. It is not correlated to a durable logical turn because that primitive is absent. |
| Approval durability | MISSING / overlay-only | Clean upstream uses process-local approval callbacks. The durable continuation store/worker from the old line is absent. |
| Signed Telegram approval compatibility | MISSING / overlay-only | The required `gateway.telegram_approval` compatibility API and signed `pa:` implementation exist in the preserved production overlay/old branch, not clean upstream. |
| Startup recovery | PARTIAL | Delivery and async-delegation recovery exist; accepted ordinary/model turns have no durable startup dispatcher. |
| Persistent background work | PARTIAL | Current gateway/API background agents use persisted sessions and leases, but parent acceptance and child execution lack a common durable logical-turn identity. |
| Terminal immutability | MISSING for model work | Delivery rows have terminal handling, but model work has no durable terminal logical-turn state. |

## Current producer inventory (initial)

| Surface / producer | Current location | Class | Current ownership/recovery | Phase 1 gap |
| --- | --- | --- | --- | --- |
| Gateway ordinary messages (Telegram, Discord, Slack, Signal, Weixin/WeCom, Yuanbao, Matrix and other adapters) | `gateway/platforms/base.py:BasePlatformAdapter.handle_message` -> `gateway/run.py:GatewayRunner._handle_message` | S | Process-local active-session/running-agent guards plus SessionDB turn lease; pending input is process-local | Accepted event is not durably admitted before busy waiting/queueing; no startup turn recovery or persisted occurrence. |
| Queue/retry/resume and steer | `gateway/platforms/base.py`, `gateway/slash_commands.py`, `gateway/run.py` | S | Process-local FIFO/active-agent injection and normal turn lease | Future work is not represented by a durable logical turn. |
| Approval continuation | `tools/approval.py` and platform callbacks | S | Process-local callback registry | Decision/continuation is not a production-wired durable turn on clean upstream. |
| Goal continuation | `gateway/run.py` post-turn goal paths | S | Recursive/post-turn dispatch in the gateway process | Deferred continuation does not survive process replacement as accepted logical work. |
| Gateway `/background` and API run workers | `gateway/run.py` direct `run_conversation` paths | I | Independent persisted session plus normal session lease | No durable parent occurrence/child logical turn/attempt or startup execution recovery. |
| Process completion/watch | `gateway/run.py` synthetic `MessageEvent` injection | S | Process registry and gateway task memory | Generated follow-up is not accepted into a durable turn ledger before contention. |
| API chat/response/run | `gateway/platforms/api_server.py` | S/I | API-local agents and SessionDB leases; response store/delivery mechanisms | Direct model execution bypasses common durable admission and common recovery. |
| ACP prompt | `acp_adapter` server/session manager | S | Persisted ACP session and process-local execution state | No durable logical turn or startup dispatcher. |
| Feishu comments | `gateway/platforms/feishu_comment.py` | I | Dedicated persisted comment processing state | Direct reasoning lacks common logical-turn admission/recovery. |
| Cron/scheduler | cron scheduler and gateway cron dispatch | I | Cron execution database and process-local runner | Scheduler occurrence is durable, but model execution is not correlated to a SessionDB logical turn/attempt. |
| CLI interactive/resume/quiet/oneshot | `cli.py`, `hermes_cli`, `run_agent.py` | S | Persisted session and SessionDB lease | No accepted-event ledger, attempt state, or recovery dispatcher. |
| CLI background | `cli.py` | I | Process-local worker around a persisted child session | Parent/child admission and recovery are not atomic/durable. |
| TUI prompt/goal/process completion | `tui_gateway` | S | `session["running"]`, local workers, and downstream session lease | Local running state remains an admission authority; no durable event queue/rehydration. |
| Async delegation child fan-out/fan-in | `tools/delegate_tool.py`, `async_delegations` table | H for synchronous child; I/S notification for delayed result | Child helper execution returns to parent; async result persistence exists | Delayed result that needs another parent turn still lacks common durable turn admission. |
| Batch/eval runners | `batch_runner.py`, `evals`, scripts | E/H unless configured to reuse a persisted user session | Disposable runner-local execution | Explicitly exempt while no resumable session contract exists; reclassify any mode that reuses a persisted session. |
| Compression | agent compression modules | H | Dedicated compression lock and session lineage | Not user-session future work; exempt from logical-turn admission. |
| Curator/background reviewer | `agent/curator.py`, `agent/background_review.py` | H | Isolated evaluator/reviewer sessions | Exempt unless a mode is found that continues a user session. |
| MS Graph webhook and generic webhook | platform adapters -> common gateway message path | S | Transport auth/idempotency then process-local gateway dispatch | No persisted acceptance occurrence/logical turn when the source lacks a replay ID. |

This inventory is deliberately a current-line starting point. Each row must be
confirmed against the implementation slice that migrates it, and newer plugin
model executors must be added before the final repository-wide gate.

## Old-to-current semantic map

| Old Phase 1 area | Current equivalent | Port strategy |
| --- | --- | --- |
| `hermes_state.py` logical-turn schema/methods | `hermes_state_common.py` schema plus current `SessionDB` transaction/lineage lease code | Reimplement the ledger on current schema helpers; encode attempt ownership in the current conversation lease rather than replacing the modern lease table. |
| `gateway/platforms/base.py` event metadata/admission | Modern `MessageEvent`, batching and active-session machinery in the same module | Add acceptance metadata and route all new-turn paths through a common current-line facade; retain modern batching/command behavior. |
| `gateway/run.py` startup drain/background/continuations | Modern `GatewayRunner`, delivery ledger, session-state aggregate and expanded platform/plugin wiring | Integrate logical-turn recovery with existing delivery and shutdown systems, not the old runner layout. |
| Old approval store/continuation worker | Current approval callbacks plus modern gateway lifecycle | Port the durable store/worker as a thin adapter into common admission; preserve current approval UX and outcome APIs. |
| Old signed Telegram modules and `gateway.telegram_approval` API | Current Telegram adapter plus preserved production overlay | Reconcile behavior and harness API, retaining current callback authorization and current adapter architecture. |
| Old CLI/TUI admission patches | Current split CLI/TUI modules and current session lease APIs | Re-audit and migrate current call sites; do not transplant old monolithic functions. |

## Production database snapshot findings

The archive manifest pointer is intact: `ARCHIVE_MANIFEST.sha256` records the
supplied SHA-256 `b2fe6ced...` for `SHA256SUMS`.

Read-only inspection of the preserved source snapshots confirms:

* `state.db.raw` has the pre-existing malformed `messages_fts_trigram` index.
* backup-only `state.db` passes SQLite integrity checking.
* both contain 1,114 sessions, 29,725 messages and one session-turn lease.
* approvals, Kanban and cron snapshots pass SQLite integrity checks and expose
  their expected tables.

Current `SessionDB` contains a one-shot FTS-only rebuild/fail-open mechanism;
the malformed index does not prevent opening the database or reading primary
records. The FTS defect remains separate operational debt, not a Phase 1 schema
migration. Final compatibility testing will use fresh writable copies and will
compare primary counts before and after every schema slice.
