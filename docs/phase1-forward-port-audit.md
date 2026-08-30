# Phase 1 v0.20.6 delta forward-port audit

Selected base: `89b38ed734ab2d5c3d263bc24fbdb8c74e931af4`

Live production reference: `89b38ed734ab2d5c3d263bc24fbdb8c74e931af4`

Phase 1 semantic reference: `489cd766f3f32720c5e4cf01ce4a9740c5493fe4`

Phase 1 reference base: `10e93c6ab958c7ec61cfc4416f4d4459e72ca8a7`

## Base selection

The selected base is the exact current production Git revision and declares
version `0.20.6`.  It is 185 commits ahead of the verified Phase 1 reference
base; the histories diverge by one squash commit on the Phase 1 side.  This
branch therefore carries the verified behavior forward onto the exact current
runtime rather than replacing or downgrading that runtime.

## v0.20.6 delta audit

The Phase 1 reference changes 116 paths. Only 17 of those paths were also
changed by the 185-commit current-runtime delta. The important overlaps
were `hermes_state.py`, gateway core, API, cron, CLI/TUI, and Telegram. The port
was applied in producer-owned slices and each overlap was tested before the
next slice.

The 185-commit executor audit found no new direct persistent reasoning producer.
`/bg` is the renamed independent persistent background producer and retains its
durable parent/child admission; `/btw` is an isolated background-review helper
and `/plan` rewrites an already-admitted current turn. Cron changes parsing and
request-override propagation without adding an execution boundary. Delegation
request overrides remain within the existing delegated execution path. Bounded
gateway compression remains current-turn helper work. Telegram request-pool
recovery is transport lifecycle only. MCP OAuth, credential rotation, fallback
resolution, and revisioned todo snapshots add no reasoning producer. New and
changed transports still normalize to `MessageEvent` and enter
`BasePlatformAdapter.handle_message`, inheriting common gateway admission.

Upstream improvements retained by the port include SessionDB read-connection
and repair hardening, cron's late SessionDB open after wake/prompt gates,
terminal cron completion verification, gateway shutdown/liveness hardening,
and current transport/plugin behavior. None of these replaces the logical-turn
ledger or scoped recovery contract; current upstream still had no
`admit_session_event`, ready-turn query, persisted occurrence field, or durable
approval continuation before this port.

## Production overlay reconciliation

The live tracked overlay's binary diff hash changed because its base advanced,
but its semantic additions did not: the archived and current patches contain
the same 70 added source lines, with no line unique to either patch. Those lines
bind durable approvals to the initiating Telegram user and route signed `pa:`
callbacks. Both behaviors are present in the candidate through
`gateway.signed_telegram_approval`, the `gateway.telegram_approval`
compatibility API, gateway approval metadata, and the current Telegram adapter.
The unchanged untracked files remain historical backups/tests and are not part
of this port.

## Invariant audit

| Phase 1 invariant | Current-line status | Evidence / gap |
| --- | --- | --- |
| Durable logical-turn admission | PARTIAL (state primitive ported) | The forward-port branch now has a current-line `logical_turns` ledger, `admit_session_event`, attempt lifecycle, scoped ready queries and execution/delivery terminal state. Production producer wiring and startup dispatch remain. |
| Durable lease authority | PRESENT in state layer | Cross-process session-turn leases exist and the forward-port removes upstream's dead-PID early reclaim. Claims use the current compression-lineage conversation lease and allocate a distinct attempt ID. Producer integration remains. |
| Truthful task/goal identity | PARTIAL | Current model entry points accept task context, but there is no shared admitted-event record enforcing authoritative/null correlation across producers. |
| Occurrence identity | MISSING | Current `MessageEvent` has transport message/update fields but no acceptance-time persisted occurrence identity shared by all persistent producers. |
| Bounded recovery filtering | PARTIAL | Delivery and delegation stores have scoped recovery; no common logical-turn dispatcher exists for accepted model work. |
| Execution vs delivery | ALREADY PRESENT (transport layer) | `gateway.delivery_ledger.DeliveryLedger` persists delivery obligations independently of model execution. It is not correlated to a durable logical turn because that primitive is absent. |
| Approval durability | MISSING / overlay-only | Clean upstream uses process-local approval callbacks. The durable continuation store/worker from the old line is absent. |
| Signed Telegram approval compatibility | MISSING / overlay-only | The required `gateway.telegram_approval` compatibility API and signed `pa:` implementation exist in the preserved production overlay/old branch, not clean upstream. |
| Startup recovery | PARTIAL | Delivery and async-delegation recovery exist; accepted ordinary/model turns have no durable startup dispatcher. |
| Persistent background work | PARTIAL | Current gateway/API background agents use persisted sessions and leases, but parent acceptance and child execution lack a common durable logical-turn identity. |
| Terminal immutability | PRESENT in state layer | Completed/unrecoverable/cancelled logical turns cannot be claimed or reopened by reconciliation; producer integration remains. |

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

### v0.20.6-only producer delta

The delta audit found one ownership-equivalent introduced after the old Phase 1
base: Kanban creator-session wakes now use `gateway.wake.deliver_wake` for both
push adapters and API self-posts. The upstream path rendered a durable Kanban
event into text but discarded the event cursor, so a retry of the same claimed
event received a fresh API identity while distinct same-text events had no
explicit producer identity at the push boundary.

The current-line port keys each Kanban wake from the persisted board,
subscription destination and claimed event cursor. Rewind/retry of the same
claim therefore resolves to one source event; the next cursor is independent
even if it renders byte-identical text. API self-post retries carry one
`Idempotency-Key`. Process-completion API wakes likewise forward an existing
producer `event_id`, or allocate one Hermes occurrence on the queued event and
retain it for subsequent delivery attempts. Rendered notification content is
never part of these identities.

## Implemented forward-port status

The forward-port keeps the current v0.20.6 runtime architecture and now closes
the gaps identified above.  The common `SessionDB.admit_session_event` ledger
records the accepted occurrence before persistent reasoning starts; claims
allocate an attempt and acquire the existing conversation-lineage turn lease.
Execution and delivery are terminalled independently, and recovery queries are
producer-scoped in SQLite before their deterministic bounded limit.

| Current producer | Class | Admission / ownership | Recovery and delivery |
| --- | --- | --- | --- |
| Gateway ordinary messages and platform adapters | S | `MessageEvent` occurrence -> common gateway admission -> attempt -> SessionDB lease | Gateway startup drain rehydrates queued/retry gateway events; terminal execution is not reopened for delivery retry. |
| Queue, retry, steer, goal and process-result turns | S | Common gateway admission; safe live-turn injection remains distinct from creation of a new logical turn | Same gateway dispatcher; busy-session events stay durable instead of synchronously self-waiting. |
| Approval continuation | S | Durable approval and continuation records feed common gateway admission | Continuation worker is a thin dispatcher; completion/terminal failure is durable and duplicate claims are idempotent. |
| Signed Telegram approval callbacks | S | Current Telegram callback path plus signed `pa:` compatibility and user/request/repository/action binding | Malformed, expired, wrong-user and tampered callbacks fail closed; accepted continuation uses the normal durable path. |
| Gateway `/background` | I | Durable parent occurrence establishes the persisted child session/task and child logical turn before parent success | Startup recovery dispatches eligible child turns; parent replay resolves to the same child. |
| API persistent chat/run | S/I | API source occurrence and real session/task metadata enter common SessionDB admission | API-scoped bounded recovery dispatcher; execution and client delivery remain separate. |
| ACP prompts | S | ACP event and target session are admitted before reasoning | ACP-scoped bounded recovery filtered in SQLite before `LIMIT`. |
| Feishu comment reasoning | I | Comment occurrence and persisted comment session use the common ledger and lease | Feishu-scoped recovery; ordinary comment conversation carries no fabricated task/goal ID. |
| Cron agent-backed jobs | I | Real cron job ID is task ID; each scheduled occurrence has a logical turn/attempt and lease | Bounded `cron-execution` recovery; ambiguous executing work remains blocked for reconciliation rather than blindly replayed. |
| CLI interactive/resume/quiet | S | Accepted CLI occurrence -> logical turn/attempt/lease; ordinary chat has null task/goal IDs | Supported CLI restoration reconciles unfinished accepted work; no-owner resume cannot be rejected by a local cache alone. |
| CLI oneshot | S | Persisted oneshot session uses the same ledger and lease | Interrupted work is durably visible/reconcilable; no daemon-style public auto-resume is claimed. |
| CLI `/background` | I | Parent command occurrence durably correlates one child identity/turn before success | Bounded supported child recovery; distinct commands remain distinct while replay is idempotent. |
| TUI prompt, goal and delayed completion | S | Persistent TUI work uses the common ledger/lease; `session["running"]` is local UI state only | TUI rehydration drains accepted work; explicit ephemeral mode exists only for test doubles. |
| Process completion/watch and no-ID MS Graph/Home Assistant notifications | S | Authoritative transport ID when present; otherwise a fresh acceptance-time occurrence persisted once | Occurrence and reply anchor survive rehydration; rendered content is not an occurrence identity. |
| Synchronous delegates, compression and curator/reviewer helpers | H | Isolated helper execution within an owning turn or evaluator context | Exempt: they do not create resumable user-session work. |
| Batch and explicit preview/restart probes | E | Disposable, non-resumable execution | Exempt and documented as ephemeral. |

Process-local running-agent/session registries remain useful for live object
references, UI status and shutdown drain, but cannot establish cross-process
ownership.  An unexpired SessionDB lease is authoritative even if its PID is
not locally visible; reclaim occurs only through expiry and canonical
reconciliation.  Completed, cancelled and unrecoverable turns are immutable.

Ordinary conversation has real `session_id` and null `task_id`/`goal_id`.
Task-backed background, API-run, cron and Kanban work propagate only their real
task identity; goal identity is populated only when an authoritative durable
goal exists.  Session/chat keys are never convenience task or goal IDs.

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

SQLite backup semantics were used to create fresh writable copies of the live
`state.db`, signed-approval, Kanban and cron execution stores. No live database
was opened for writing. Before candidate initialization the copies contained
1,130 sessions, 29,756 messages, 180 pre-existing logical turns, 49 approval
requests, 39 approval tasks, 37 callback events, 26 resume events, two grants,
and 1,000 retained cron executions.

Candidate initialization preserved every primary count. The state copy opened
through the ported `SessionDB`, retained the 180 logical turns, converted the
legacy lease table to the current compression-lineage key shape, and exposed
the complete logical-turn/delivery columns. Signed Telegram `ApprovalStore`
opened the copied production records with the existing filesystem-only key;
the temporary key copy was deleted immediately after the check. Current Kanban
migrations and cron schema initialization also completed without record loss.
All four post-initialization copies report `PRAGMA quick_check = ok`.

The older archived raw snapshot still records the known
`messages_fts_trigram` defect. The fresh live copy used here passed both an FTS
row read and the FTS5 integrity command under the candidate runtime, while its
primary tables remained unchanged. FTS shadow data remains rebuildable search
state rather than a reason to rewrite or discard primary session data.
